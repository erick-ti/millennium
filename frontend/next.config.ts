import type { NextConfig } from "next";

// The Django backend the /api/* rewrite proxy forwards to. Compose sets
// BACKEND_URL=http://backend:8000 (service name); host dev defaults to
// http://localhost:8000.
//
// Validated + canonicalized to an ORIGIN before use:
// this value is serialized into the standalone server.js at build time, so a
// pathful value would silently bake wrong rewrite destinations (e.g.
// http://backend:8000/api → /api/api/...), and nothing at runtime can fix it:
// only a REBUILD changes the baked value. Fail the build instead. A bare
// trailing slash is tolerated (URL.origin canonicalizes it away).
function validatedBackendOrigin(raw: string): string {
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`BACKEND_URL ${JSON.stringify(raw)} is not a valid URL`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`BACKEND_URL ${JSON.stringify(raw)} must be http(s), got ${parsed.protocol}`);
  }
  if (parsed.pathname !== "/" || parsed.search || parsed.hash || parsed.username || parsed.password) {
    throw new Error(
      `BACKEND_URL ${JSON.stringify(raw)} must be an origin only — no path, query, hash, or ` +
        "credentials (the /api/* rewrite appends its own path)",
    );
  }
  return parsed.origin;
}

const BACKEND_URL = validatedBackendOrigin(process.env.BACKEND_URL ?? "http://localhost:8000");

const nextConfig: NextConfig = {
  // Produce .next/standalone (a self-contained server.js + traced node_modules
  // subset) for the production Docker image (Railway deploy phase slice 3).
  // Unconditional on purpose: `next dev` ignores it, and `next start` (the e2e
  // CI webServer) only WARNS, verified against the installed source
  // (dist/server/next.js: standalone warns, only output:"export" throws).
  // NOTE: next.config (including the BACKEND_URL rewrite below) is SERIALIZED
  // into the standalone server.js at build time: a backend URL change needs a
  // frontend image REBUILD, not a restart.
  output: "standalone",
  // With proxy.ts present on /api/*, Next 16 buffers each proxied request body in memory up
  // to `proxyClientMaxBodySize` (default 10MB) and, per the Next docs, SILENTLY forwards
  // only the truncated prefix on overflow (no error). The import upload's backend cap
  // (MAX_UPLOAD_BYTES = 10MB, apps/imports/views.py) equals that default, so a near-10MB CSV
  // plus multipart overhead could be truncated before Django sees it → a malformed/partial
  // import instead of a clean 400. Set the proxy buffer comfortably ABOVE the backend cap so
  // the backend's size check is authoritative: any file ≤10MB arrives intact (clean accept or
  // 400), and anything large enough to truncate here is still >10MB → backend 400. Keep this
  // > MAX_UPLOAD_BYTES + multipart overhead if either cap changes.
  experimental: {
    proxyClientMaxBodySize: "20mb",
    // The /api/* path is browser → THIS Next proxy → Django. Next 16's proxy
    // timeout defaults to 30_000ms when unset (verified: dist/server/lib/
    // router-utils/proxy-request.js: `proxyTimeout || 30000`, in ms), but the
    // synchronous CSV import (POST /api/imports/batches/) is sized to the
    // BACKEND's 120s gunicorn --timeout (MAX_UPLOAD_ROWS=10k ≈ ≤100s on Railway).
    // A 30s proxy cutoff would 500 a legitimate large import at the proxy while
    // Django keeps committing rows: a "failed" import that actually (partly)
    // succeeded, then a confusing retry. Set the proxy ABOVE the backend budget
    // so the backend's own --timeout is authoritative (a too-slow import returns
    // a real backend error, not a silent proxy cutoff). 125s > gunicorn's 120s.
    // NOTE: a Railway EDGE request timeout sits in
    // front of this proxy. The runbook flags confirming it also exceeds 120s.
    proxyTimeout: 125_000,
  },
  // Django's APPEND_SLASH=True (default) canonicalizes /api/foo → /api/foo/.
  // With Next's default trailingSlash:false the two would round-trip forever
  // (Next strips, Django 308 adds, browser refetches, Next strips, …). We
  // align Next with Django's convention; page routes get a trailing slash too,
  // which is a fine personal-app default. Both `source` and `destination` then
  // need explicit trailing slashes (per the Next rewrites doc).
  trailingSlash: true,
  async rewrites() {
    // Same-origin proxy: browser hits /api/* on Next, Next forwards to
    // Django. Keeps the Django session cookie + CSRF flow CORS-free.
    return [
      {
        source: "/api/:path*/",
        destination: `${BACKEND_URL}/api/:path*/`,
      },
    ];
  },
};

export default nextConfig;
