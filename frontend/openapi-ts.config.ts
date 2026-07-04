import { defineConfig } from "@hey-api/openapi-ts";

/**
 * Codegen config for `@hey-api/openapi-ts`, generates a typed TS client from
 * the committed `openapi.json` snapshot (refreshed via `make frontend-snapshot-schema`).
 *
 * The schema acquisition strategy is committed-snapshot + committed-client:
 * PR diffs show the API surface change, client-gen has no live-Django
 * dependency in CI, and schema drift is auditable.
 *
 * Plugins:
 *  - `@hey-api/client-fetch` emits a fetch-based runtime client INTO the output
 *    folder (the v0.73+ bundled-client pattern, no separate runtime npm pkg).
 *  - `@hey-api/typescript` emits the type declarations for every component.
 *  - `@hey-api/sdk` emits the SDK functions (one per operation).
 *  - `@tanstack/react-query` emits query/mutation/queryKey helpers wrapping each
 *    SDK function, ready to plug into the slice-1 TanStack Query provider.
 */
export default defineConfig({
  input: "./openapi.json",
  // No post-processor: this project doesn't use Prettier (the `format` config
  // requires a prettier binary on PATH). The generated code is consumed, not
  // hand-edited; ESLint ignores the generated tree.
  output: "./src/lib/api/generated",
  plugins: [
    "@hey-api/client-fetch",
    "@hey-api/typescript",
    "@hey-api/sdk",
    "@tanstack/react-query",
  ],
});
