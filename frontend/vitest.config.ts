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
    // Printed after every `pnpm test`, like the backend's pytest-cov table. Text only, so no
    // files land in the checkout; `skipFull` hides fully covered files as the backend does.
    coverage: {
      enabled: true,
      provider: "v8",
      reporter: [["text", { skipFull: true }]],
      include: ["app/**", "components/**", "lib/**", "src/**", "proxy.ts"],
      exclude: ["**/*.d.ts"],
    },
  },
});
