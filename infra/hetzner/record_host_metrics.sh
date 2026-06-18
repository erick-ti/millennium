#!/usr/bin/env bash
# Host-side glue for the millennium-host-metrics timer. Reads the box's load on the HOST
# (the backend container is isolated from host /proc + the host disk), then pipes the
# JSON sample into a ONE-OFF backend container that validates + persists it.
#
# `run --rm` (NOT `exec` into the live backend) on purpose: the live backend is
# mem_limit:512m running gunicorn, so exec-ing a full Django process into THAT cgroup
# every 2 min risks tipping the web container into an OOM under load. A one-off container
# gets its own 512m cgroup (the sync timers' precedent), isolated from gunicorn. It also
# doesn't need the live backend to be up, so a tick during a deploy recreate just spins
# its own container instead of failing — no backend-running guard needed. `--no-deps`
# keeps a 2-min sampler from auto-starting the stack when it's intentionally down.
#
# cd first so compose resolves the `millennium` project from ./docker-compose.yml + .env.
# Fail-loud (`set -e` + pipefail) on a genuine collector or ingest error.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

python3 collect_host_metrics.py \
  | docker compose run --rm --no-deps -T backend python manage.py record_host_metrics
