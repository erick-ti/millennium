import type { NextConfig } from "next";

// The Django backend the /api/* rewrite proxy forwards to. Compose sets
// BACKEND_URL=http://backend:8000 (service name); host dev defaults to
// http://localhost:8000.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
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
