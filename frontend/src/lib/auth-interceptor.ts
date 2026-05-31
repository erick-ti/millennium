import { client } from "@/lib/api";

/**
 * An error carrying the HTTP status + request URL, so the global QueryClient
 * cache handler can branch on the "no session" status without re-reading a
 * `Response` (which `throwOnError` swallows — see {@link installAuthInterceptor}).
 *
 * In this DRF stack the unauthenticated status is **403**, not 401
 * (`SessionAuthentication.authenticate_header` is `None`, so DRF downgrades a
 * 401 to 403), and every endpoint is `IsAuthenticated` — so a 403 on a read
 * means "sign in".
 */
export class ApiError extends Error {
  readonly status?: number;
  readonly url?: string;
  /** The raw thrown body (e.g. DRF's `{ detail }`), preserved for callers. */
  readonly body: unknown;

  constructor(status: number | undefined, url: string | undefined, body: unknown) {
    super(extractDetail(body) ?? `Request failed${status ? ` (HTTP ${status})` : ""}.`);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
    this.body = body;
  }
}

function extractDetail(body: unknown): string | null {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

/**
 * The interceptor's gate, extracted as a pure function so the blast-radius
 * contract can be unit-tested directly.
 *
 * ONLY the throwing callers get wrapped. The generated `*Options` query
 * factories set `throwOnError: true`, so their errors surface through the global
 * `QueryCache` handler that needs the status. Bare SDK write calls (login /
 * logout / upload / review) use the default, non-throwing style and read the raw
 * `{ error, response }` body directly — so their error is returned UNTOUCHED
 * (same reference), preserving DRF field-error parsing (e.g. `import-upload`'s
 * `fieldError` and the import-review 409 read).
 */
export function toApiError(
  error: unknown,
  response: { status: number } | undefined,
  request: { url: string } | undefined,
  throwOnError: boolean | undefined,
): unknown {
  if (!throwOnError) return error;
  return new ApiError(response?.status, request?.url, error);
}

let registered = false;

/** Idempotently register the hey-api error interceptor (see {@link toApiError}). */
export function installAuthInterceptor(): void {
  if (registered) return;
  registered = true;
  client.interceptors.error.use((error, response, request, options) =>
    toApiError(error, response, request, options.throwOnError),
  );
}
