import type { Metadata } from "next";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import AppProviders from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "VoiceScope Agent｜智能嗓音问诊",
  description: "面向嗓音疾病筛查与分诊的多模态医疗智能体",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>
          <AppProviders>{children}</AppProviders>
        </AntdRegistry>
      </body>
    </html>
  );
}
