import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": new URL(".", import.meta.url).pathname,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    // Coverage instrumentation slows the heaviest page tests past the 5s default.
    testTimeout: 20000,
    // One dot per test; failures are still printed in full.
    reporters: ["dot"],
    // The antd deprecation notices repeat for every render and bury the result; the proxy tests
    // print the configuration errors they are asserting on. Neither is a test failure.
    onConsoleLog(log) {
      if (/\[antd:|Proxy is not configured/.test(log)) return false;
    },
    // Printed after every `pnpm test`: a four-line summary. Text only, so no files land in the
    // checkout. Set COVERAGE_DETAIL=1 for the per-file table (fully covered files hidden).
    coverage: {
      enabled: true,
      provider: "v8",
      reporter: process.env.COVERAGE_DETAIL ? [["text", { skipFull: true }]] : ["text-summary"],
      include: ["app/**", "components/**", "lib/**", "src/**", "proxy.ts"],
      exclude: ["**/*.d.ts"],
      // Measured 2026-10-09: 73.14 / 60.88 / 70.86 / 78.9. Only ever raise these numbers.
      thresholds: { statements: 72, branches: 59, functions: 69, lines: 77 },
    },
  },
});
