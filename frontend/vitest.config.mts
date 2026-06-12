import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { configDefaults, defineConfig } from "vitest/config";

// Vitest + React Testing Library for component unit tests (Phase 4 slice 3).
// tsconfigPaths resolves the `@/*` alias; jsdom provides the DOM. Async Server
// Components aren't supported by Vitest — every component under test here is a
// Client Component ("use client"), which is fine.
export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Belt-and-suspenders against the Playwright e2e suite (Phase 5 slice 6):
    // a *.spec.ts (or anything under e2e/) imports @playwright/test and hard-
    // crashes Vitest if collected. The include above already scopes to src/, but
    // these explicit excludes mean even a misplaced spec can't break this
    // required job.
    exclude: [...configDefaults.exclude, "**/e2e/**", "**/*.spec.*"],
  },
});
