import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit/component tests only (C9.5). Deliberately minimal: jsdom, the same "@/" alias as tsconfig, and esbuild's
// automatic JSX runtime so no React plugin is needed. This does not affect `next build` or `next lint`.
export default defineConfig({
  esbuild: { jsx: "automatic" },
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
