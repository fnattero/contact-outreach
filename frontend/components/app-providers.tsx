"use client";

import { App, ConfigProvider } from "antd";
import esES from "antd/locale/es_ES";
import type { ReactNode } from "react";
import { AuthProvider } from "@/components/auth-provider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider locale={esES} theme={{ token: { colorPrimary: "#155eef" } }}>
      <App>
        <AuthProvider>{children}</AuthProvider>
      </App>
    </ConfigProvider>
  );
}
