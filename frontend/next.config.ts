import type { NextConfig } from "next";

// The Django backend the /api/* rewrite proxy forwards to. Compose sets
// BACKEND_URL=http://backend:8000 (service name); host dev defaults to
// http://localhost:8000.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // With proxy.ts present on /api/*, Next 16 buffers each proxied request body in memory up
  // to `proxyClientMaxBodySize` (default 10MB) and — per the Next docs — SILENTLY forwards
  // only the truncated prefix on overflow (no error). The import upload's backend cap
  // (MAX_UPLOAD_BYTES = 10MB, apps/imports/views.py) equals that default, so a near-10MB CSV
  // plus multipart overhead could be truncated before Django sees it → a malformed/partial
  // import instead of a clean 400. Set the proxy buffer comfortably ABOVE the backend cap so
  // the backend's size check is authoritative: any file ≤10MB arrives intact (clean accept or
  // 400), and anything large enough to truncate here is still >10MB → backend 400. Keep this
  // > MAX_UPLOAD_BYTES + multipart overhead if either cap changes. (Codex review 2026-05-30.)
  experimental: {
    proxyClientMaxBodySize: "20mb",
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
