# syntax=docker/dockerfile:1.7

# Multi-stage (Railway deploy phase slice 3): `dev` is the compose target
# (infra/docker-compose.yml pins `target: dev` — without the pin compose would
# build the LAST stage and run the production server against the bind mount);
# an untargeted `docker build` produces the final `runtime` stage — the
# production image Railway deploys.

# --- dev: next dev against the compose bind mount ---------------------------
FROM node:24-alpine AS dev

WORKDIR /app

# Install deps as a separate layer so source edits don't bust the install
# cache. node_modules then lives at /app/node_modules; compose uses an
# anonymous volume there to shield it from the host bind mount (host modules
# are darwin-arm64; the container needs linux-x64-musl). This is the Node
# equivalent of the backend's /opt/venv-outside-the-bind-mount rule
# (Invariant 8).
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

# next dev binds to localhost by default; -H 0.0.0.0 makes it reachable through
# the compose port mapping. Compose binds the host port to 127.0.0.1 only
# (Invariant 5 echo), so this is loopback-only end to end.
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0"]

# --- build: produce the standalone production bundle ------------------------
# Deliberately NOT `FROM dev`: the build stage's libc must MATCH the runtime's
# (both node:24-slim/glibc). `npm ci` selects native-addon binaries by the
# INSTALL platform, but the addon loads by the RUNTIME libc — Next itself
# ships sharp as an optionalDependency, so an alpine (musl) build stage traces
# @img/sharp-linuxmusl-* into .next/standalone and the first next/image use
# would 500 on the glibc runtime, in production only (adversarial review
# 2026-06-12). The dev stage stays alpine for compose.
FROM node:24-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# BACKEND_URL is REQUIRED at build time and fails closed: next.config.ts's
# /api/* rewrite reads it during `next build`, and the whole config is
# serialized into the standalone server.js — an unset ARG would silently bake
# the localhost fallback and every /api/* request in production would proxy to
# nowhere. Changing the backend URL later requires a REBUILD, not a restart
# (deploy runbook: pin the backend's PORT and never rename the service).
ARG BACKEND_URL
RUN test -n "$BACKEND_URL" || { echo "BACKEND_URL build arg is required (it is baked into the standalone server)" >&2; exit 1; }
ENV BACKEND_URL=$BACKEND_URL \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# --- runtime: minimal production server --------------------------------------
# Debian-slim, NOT alpine: musl DNS resolution of *.railway.internal is the
# documented Alpine private-networking failure class on Railway; glibc
# sidesteps it entirely.
FROM node:24-slim AS runtime

WORKDIR /app

# The standalone server.js reads PORT (Railway injects it; default 3000 below
# matches compose/local) and HOSTNAME at startup. "::" binds dual-stack — the
# Railway edge and IPv4 clients connect via IPv4-mapped addresses, and any
# private IPv6 consumer works too (the backend image's [::] posture).
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME="::" \
    PORT=3000

# server.js does not serve public/ or .next/static unless they sit inside the
# standalone tree (per the bundled output.md doc) — copy them in.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next/static ./.next/static

USER node

EXPOSE 3000

CMD ["node", "server.js"]
