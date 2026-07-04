import { defineConfig, devices } from "@playwright/test";

// Playwright end-to-end smoke suite.
//
// Scope: testDir "./e2e" + testMatch "**/*.spec.ts" keep Playwright off Vitest's
// src/**/*.test.{ts,tsx}; Vitest in turn excludes **/e2e/** and **/*.spec.*, so
// neither runner can claim the other's files even if one lands in the wrong dir.
// e2e/ is excluded from tsconfig + eslint so the required `lint + build` job is
// unaffected; Playwright transpiles these specs (esbuild, no type-check) at run
// time, and `npm run test:e2e:typecheck` (tsconfig.e2e.json) is the static
// type/import check the CI e2e job runs.
//
// Servers: Playwright manages BOTH the Django backend (under config.settings.
// smoke, relaxed login throttle, LocMem cache, no Redis) and the Next frontend
// (which proxies /api/* to the backend, exercising the real CSRF/session path).
// The DB must be migrated + seeded BEFORE this runs (`make e2e` / the CI job do
// that as an explicit prestep, kept out of globalSetup so ordering is
// unambiguous). Ports are env-overridable because an unrelated local project
// may hold 3000/8000.
//
// Reuse is OPT-IN (E2E_REUSE): by default Playwright always starts its own
// smoke-configured servers, even locally. Otherwise it would silently reuse a
// running `make dev` compose stack on the same ports, which runs config.settings
// .dev (the strict 5/min login throttle + Redis cache), NOT smoke, defeating the
// relaxation and risking an intermittent 429 on rapid re-runs. With `make dev`
// up on the default ports, e2e now fails loudly (port in use) rather than testing
// the wrong settings; bring it down or set E2E_FRONTEND_PORT/E2E_BACKEND_PORT.

const isCI = !!process.env.CI;

const FRONTEND_PORT = process.env.E2E_FRONTEND_PORT ?? "3000";
const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? "8000";
// Derived purely from FRONTEND_PORT so the URL Playwright polls can't diverge
// from the port the managed Next server binds (PORT below). Use E2E_FRONTEND_PORT
// to move both together.
const BASE_URL = `http://localhost:${FRONTEND_PORT}`;
const BACKEND_URL = process.env.BACKEND_URL ?? `http://127.0.0.1:${BACKEND_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  // Pin to .spec so Playwright never claims a src/**/*.test.tsx Vitest file.
  testMatch: "**/*.spec.ts",
  // A small, DB-mutating smoke: run serially for determinism (no cross-spec
  // races on shared seeded rows) over speed; there are only two specs.
  fullyParallel: false,
  workers: 1,
  forbidOnly: isCI,
  retries: isCI ? 2 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: isCI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // The backend runs under the smoke settings module. The DB it connects to
      // is the one the migrate/seed prestep primed (same DATABASE_URL, inherited
      // from the process env).
      command: `uv run python manage.py runserver 127.0.0.1:${BACKEND_PORT} --noreload`,
      cwd: "../backend",
      url: `${BACKEND_URL}/api/health/`,
      reuseExistingServer: !isCI && !!process.env.E2E_REUSE,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        DJANGO_SETTINGS_MODULE: process.env.DJANGO_SETTINGS_MODULE ?? "config.settings.smoke",
        DATABASE_URL:
          process.env.DATABASE_URL ??
          "postgres://postgres:postgres@localhost:5432/millennium",
        // Must include the browser origin or every proxied POST 403s (invariant 10 in ARCHITECTURE.md).
        DJANGO_CSRF_TRUSTED_ORIGINS: process.env.DJANGO_CSRF_TRUSTED_ORIGINS ?? BASE_URL,
      },
    },
    {
      // Production server (build → start) in CI for deterministic navigations;
      // the dev server locally for fast iteration. BACKEND_URL must be set
      // before the build (rewrites bake into the routes manifest at build time).
      command: isCI ? "npm run build && npm run start" : "npm run dev",
      url: BASE_URL,
      reuseExistingServer: !isCI && !!process.env.E2E_REUSE,
      timeout: 180_000,
      env: {
        BACKEND_URL,
        PORT: FRONTEND_PORT,
      },
    },
  ],
});
