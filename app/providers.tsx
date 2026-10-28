"use client";

import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

export default function AppProviders({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#0d8d83",
          colorInfo: "#0d8d83",
          colorSuccess: "#18a66a",
          borderRadius: 12,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
        },
        components: {
          Button: {
            controlHeight: 40,
            borderRadius: 10,
            fontWeight: 600,
          },
          Progress: {
            defaultColor: "#0d8d83",
            remainingColor: "#e8efee",
          },
        },
      }}
    >
      {children}
    </ConfigProvider>
  );
}
