import type { NextConfig } from "next";

// 允许本机及当前局域网设备访问开发服务器的热更新资源。
const allowedDevOrigins = [
  "localhost",
  "127.0.0.1",
  "172.16.0.89",
  process.env.NEXT_PUBLIC_DEV_ORIGIN?.replace(/^https?:\/\//, "").replace(/\/$/, ""),
].filter((origin): origin is string => Boolean(origin));

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins,
  ...(process.env.NEXT_PUBLIC_BROWSER_LOCAL === "true" ? {
    output: "export" as const,
    basePath: "/VoiceScope-Web",
    trailingSlash: true,
    images: { unoptimized: true },
  } : {}),
};

export default nextConfig;
