"use client";

import { App, ConfigProvider } from "antd";
import esES from "antd/locale/es_ES";
import { useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";
import { AuthProvider } from "@/components/auth-provider";
import { MotionProvider } from "@/components/motion-provider";
import { antdTheme } from "@/src/theme/antdTheme";

export function AppProviders({ children }: { children: ReactNode }) {
  const shouldReduceMotion = Boolean(useReducedMotion());
  const theme = shouldReduceMotion
    ? { ...antdTheme, token: { ...antdTheme.token, motion: false } }
    : antdTheme;

  return (
    <ConfigProvider locale={esES} theme={theme}>
      <App>
        <MotionProvider reduced={shouldReduceMotion}>
          <AuthProvider>{children}</AuthProvider>
        </MotionProvider>
      </App>
    </ConfigProvider>
  );
}
