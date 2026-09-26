"use client";

import { MotionConfig } from "framer-motion";
import type { ReactNode } from "react";
import { motion as motionTokens } from "@/src/theme/tokens";

export function MotionProvider({ children, reduced }: { children: ReactNode; reduced: boolean }) {
  return (
    <MotionConfig
      reducedMotion={reduced ? "always" : "never"}
      transition={{ duration: motionTokens.base, ease: motionTokens.ease }}
    >
      {children}
    </MotionConfig>
  );
}
