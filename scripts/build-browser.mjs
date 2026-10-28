/** Build in a clean directory without copying local environment files or data. */
import { cp, mkdtemp, mkdir, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const temporary = await mkdtemp(join(tmpdir(), "voicescope-browser-"));
const output = join(root, "out");
const policy = "default-src 'self'; connect-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'";
try {
  for (const name of ["app", "components", "lib", "next.config.ts", "tsconfig.json", "package.json", "package-lock.json"])
    await cp(join(root, name), join(temporary, name), { recursive: true });
  // A separate entry keeps the server-edition clients out of the public bundle.
  await cp(join(root, "browser/page.tsx"), join(temporary, "app/page.tsx"));
  await symlink(join(root, "node_modules"), join(temporary, "node_modules"), "dir");
  const env = Object.fromEntries(["PATH", "HOME", "TMPDIR", "SYSTEMROOT"].filter((key) => process.env[key]).map((key) => [key, process.env[key]]));
  const result = spawnSync(process.execPath, [join(root, "node_modules/next/dist/bin/next"), "build", "--webpack"], {
    cwd: temporary,
    env: { ...env, NODE_ENV: "production", NEXT_TELEMETRY_DISABLED: "1", NEXT_PUBLIC_BROWSER_LOCAL: "true" },
    stdio: "inherit",
  });
  if (result.status !== 0) throw new Error("Browser build failed; existing output has not been replaced.");
  async function protect(directory) {
    for (const item of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, item.name);
      if (item.isDirectory()) await protect(path);
      else {
        if (/\.(?:wav|mp3|m4a|sqlite3?|db|py|pt|zip|docx|map)$|^\.env/i.test(item.name)) throw new Error("Private artifact in static output.");
        const source = await readFile(path, "utf8");
        if (source.includes("http://localhost:8000") || source.includes("/api/auth/me")) throw new Error("Server-edition client unexpectedly included in browser build.");
        if (item.name.endsWith(".html")) await writeFile(path, source.replace("<head>", `<head><meta http-equiv="Content-Security-Policy" content="${policy}">`));
      }
    }
  }
  await protect(join(temporary, "out"));
  await writeFile(join(temporary, "out/.nojekyll"), "");
  await rm(output, { recursive: true, force: true });
  await mkdir(output, { recursive: true });
  await cp(join(temporary, "out"), output, { recursive: true });
  console.log("Browser-only static site built in out/. No environment files, backend data or credentials were copied.");
} finally {
  await rm(temporary, { recursive: true, force: true });
}
