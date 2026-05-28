# syntax=docker/dockerfile:1.7

FROM node:24-alpine

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
