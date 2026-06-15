#!/usr/bin/env bash
# Idempotent Hetzner deploy for Millennium. Applies EVERY config-as-code surface
# so a repo change can't silently drift in production (Codex review 2026-06-14):
#   - the app stack (postgres/backend/frontend)
#   - the migrate + createcachetable one-shot
#   - the separate Caddy edge — WITH a config reload, because a change to the
#     bind-mounted Caddyfile is NOT picked up by `up -d` (the mount spec is
#     unchanged), so the running Caddy keeps its old config until reloaded
#   - the systemd units, which are COPIES in /etc/systemd/system untouched by
#     `git pull`
#
# Run as the `millennium` user (the systemd sync uses sudo). Safe to re-run.
#   ~/millennium/infra/hetzner/deploy.sh
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/millennium}"

cd "$REPO_DIR"
# Capture the currently-deployed revision up front so an unhealthy deploy can be
# rolled back (the in-place recreate below goes live before --wait confirms health).
PREV_SHA="$(git rev-parse --short HEAD)"
# Skip the pull on a detached HEAD — that's the rollback path (`git checkout <sha>
# && ./deploy.sh`), where `git pull` would fail (no upstream) during the exact
# failure it's meant to recover from (Codex review 2026-06-14).
if git symbolic-ref -q HEAD >/dev/null; then
    echo "deploy: git pull"
    git pull --ff-only
else
    echo "deploy: detached HEAD (rollback mode) — skipping git pull"
fi

cd "$REPO_DIR/infra/hetzner"

echo "deploy: build images"
docker compose build

echo "deploy: migrate + createcachetable"
docker compose run --rm migrate

echo "deploy: up app stack (wait for healthy)"
# --wait blocks until the backend + frontend healthchecks pass, failing loudly
# otherwise. Honest scope (Codex review 2026-06-14): on the FIRST deploy this gates
# pre-live (the edge isn't up yet); on a REDEPLOY `up -d` recreates the containers
# in place and the running edge routes to the new ones immediately, so an unhealthy
# redeploy is briefly live — --wait catches it and the rollback hint below points at
# recovery. No blue-green: this is a single-user manual deploy with CI-gated images
# + expand/contract migrations (old code tolerates the new schema, so a code
# rollback is safe). Name the services so the one-shot `migrate` doesn't block --wait.
if ! docker compose up -d --wait --wait-timeout 180 backend frontend; then
    echo "deploy: app did NOT become healthy — the new containers may be live but failing." >&2
    echo "deploy: roll back to the last-good revision with:" >&2
    echo "        git checkout ${PREV_SHA} && ./deploy.sh" >&2
    exit 1
fi

echo "deploy: validate + up edge + reload Caddy config"
# Validate the Caddyfile in a throwaway container FIRST — it does NOT publish
# 80/443 or touch the running edge. A syntactically bad Caddyfile must never reach
# the live edge: `caddy reload` already keeps the old config on a bad reload, but
# the restart fallback below would boot the invalid config and take :80/:443 down
# (Codex review 2026-06-14). `set -e` aborts here on an invalid config, leaving the
# running edge on its last-good config.
docker compose -f edge/docker-compose.yml run --rm --no-deps caddy \
    validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose -f edge/docker-compose.yml up -d
# Config is now known-valid → reload in place (graceful, zero-downtime). The
# restart fallback is SAFE because it can only ever boot a validated config (it
# covers the admin-not-ready-yet case right after a fresh `up -d`).
docker compose -f edge/docker-compose.yml exec -T caddy \
    caddy reload --config /etc/caddy/Caddyfile 2>/dev/null \
    || docker compose -f edge/docker-compose.yml restart caddy

# Needs root: the runbook grants `millennium` a NOPASSWD sudoers drop-in scoped to
# cp + systemctl (it's --disabled-password, so passworded sudo can't work; and the
# docker group already makes it root-equivalent, so this is no new boundary).
echo "deploy: sync systemd units (sudo)"
sudo cp systemd/millennium-* /etc/systemd/system/
sudo systemctl daemon-reload
_timers=(
    millennium-sync-ygoprodeck.timer
    millennium-sync-tcgcsv.timer
    millennium-value-portfolios.timer
    millennium-run-alerts.timer
    millennium-backup.timer
)
sudo systemctl enable "${_timers[@]}"    # persist across reboot (idempotent)
sudo systemctl restart "${_timers[@]}"   # re-arm with the current schedule

echo "deploy: done."
echo "  verify: docker compose ps && systemctl list-timers 'millennium-*'"
