"""Check staged files and complete publishable Git history without printing secrets."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath


PRIVATE_DIRS = {
    "data", "uploads", "recordings", "reports", "exports", "backups", "tmp", "temp",
    "node_modules", ".venv", "venv", ".next", ".idea",
}
PRIVATE_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".dump", ".bak", ".csv", ".tsv", ".jsonl", ".ndjson",
    ".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".mp4", ".mov", ".dcm", ".dicom",
    ".doc", ".docx", ".pdf", ".pptx", ".xls", ".xlsx", ".zip", ".tar", ".gz", ".7z",
    ".pt", ".pth", ".ckpt", ".safetensors", ".pem", ".key", ".log", ".tsbuildinfo",
}
SECRET_PATTERNS = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def private_path(name: str) -> bool:
    path = PurePosixPath(name)
    lower = path.name.lower()
    if any(part.lower() in PRIVATE_DIRS or part.startswith("参赛") for part in path.parts[:-1]):
        return True
    if lower.startswith(".env") and name != ".env.example":
        return True
    if name.startswith("backend/config/") and not lower.endswith(".example.json"):
        return True
    return (
        path.suffix.lower() in PRIVATE_SUFFIXES
        or any(marker in lower for marker in (".sqlite-", ".sqlite3-", ".db-"))
        or "credentials" in lower
        or lower == ".ds_store"
        or path.name.startswith("._")
    )


def check(refs: list[str]) -> list[str]:
    problems: set[str] = set()
    scanned: set[str] = set()

    def inspect(oid: str, name: str) -> None:
        if private_path(name):
            problems.add(f"Private data/artifact is tracked: {name}")
            return
        if oid in scanned:
            return
        scanned.add(oid)
        content = git("cat-file", "blob", oid)
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            problems.add(f"Possible credential in tracked content: {name} (value hidden)")

    if git("rev-parse", "--is-bare-repository").strip() == b"false":
        for entry in git("ls-files", "--stage", "-z").split(b"\0"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            mode, oid, stage = metadata.decode().split()
            if stage != "0":
                problems.add("Unresolved Git merge conflicts")
            elif mode == "120000":
                problems.add(f"Review symlink before publication: {name.decode()}")
            elif mode == "160000":
                problems.add(f"Review submodule before publication: {name.decode()}")
            else:
                inspect(oid, name.decode())

    commits = git("rev-list", *refs, "--").decode().splitlines()
    for commit in commits:
        for entry in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            if mode in {"120000", "160000"}:
                problems.add(f"Review symlink/submodule before publication: {name.decode()}")
            elif kind == "blob":
                inspect(oid, name.decode())
    print(f"Checked staged files and {len(commits)} historical commits; {len(scanned)} code/document blobs.")
    return sorted(problems)


def main() -> int:
    refs = sys.argv[1:] or ["HEAD"]
    if any(ref.startswith("-") for ref in refs):
        print("Pass branch names or commit IDs, not Git options.", file=sys.stderr)
        return 2
    problems = check(refs)
    if problems:
        print("Publication blocked:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print("Publication privacy checks passed (manual content review is still required).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
