#!/usr/bin/env bash
# Nightly Postgres backup for the Millennium Hetzner self-host. Dumps the
# `postgres` compose service to a timestamped, compressed pg_dump archive on the
# host, prunes old archives, then (opt-in) mirrors the new archive to an off-box
# rclone remote and prunes the remote by age. Driven by millennium-backup.timer.
#
# Adapted from a sibling project's hardened backup script (atomic temp-rename, KEEP
# guard, off-box gating, dead-man's-switch), Millennium naming. pg_dump runs
# INSIDE the container (client version matches server) using the container's own
# POSTGRES_* env, so it needs no password on the host. The archive is custom
# format (-Fc → restore with pg_restore; see the deploy runbook
# "Backups & restore test").
#
# Config via env (millennium-backup.service EnvironmentFile=backup.env):
#   REPO_DIR                infra/hetzner dir with the compose file  (default: this script's dir)
#   BACKUP_DIR              where archives are written               (default: $HOME/millennium-backups)
#   KEEP                    how many most-recent local archives      (default: 7)
#   BACKUP_REMOTE           rclone remote to mirror to               (unset = local-only)
#   OFFSITE_KEEP_DAYS       off-box retention in days                (default: 30)
#   BACKUP_HEALTHCHECK_URL  healthchecks.io-style URL to ping        (unset = no pings)
set -euo pipefail
umask 077   # dumps are owner-only (600); a fresh BACKUP_DIR is 700 — DB data isn't world-readable

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/millennium-backups}"
KEEP="${KEEP:-7}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
OFFSITE_KEEP_DAYS="${OFFSITE_KEEP_DAYS:-30}"
BACKUP_HEALTHCHECK_URL="${BACKUP_HEALTHCHECK_URL:-}"

# Passive dead-man's-switch: ping <URL>/start after pre-flight, <URL> on success,
# <URL>/fail on any non-zero exit (EXIT trap). Default-off (no-op when unset).
# The URL is a credential — never echoed. curl errors are swallowed so a notifier
# outage never fails an otherwise-good backup.
_healthcheck_ping() {
    local suffix="${1:-}"
    if [[ -z "$BACKUP_HEALTHCHECK_URL" ]]; then
        return 0
    fi
    if curl --silent --show-error --max-time 10 --retry 3 --retry-connrefused --fail \
            "${BACKUP_HEALTHCHECK_URL}${suffix}" >/dev/null 2>&1; then
        echo "backup_db: healthcheck ${suffix:-success} ping ok"
    else
        echo "backup_db: healthcheck ${suffix:-success} ping failed (non-fatal)" >&2
    fi
}
_on_exit() {
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        _healthcheck_ping /fail
    fi
    exit "$rc"
}
trap _on_exit EXIT

# Fail closed on a bad retention count BEFORE dumping or pruning. The prune below
# keeps the newest $KEEP by array slice; a 0 / negative / non-numeric KEEP would
# make `_dump_count - KEEP` select EVERY archive (or error under arithmetic) — a
# config typo becoming total backup loss. Require a positive integer.
if ! [[ "$KEEP" =~ ^[1-9][0-9]*$ ]]; then
    echo "backup_db: KEEP must be a positive integer (got '$KEEP')" >&2
    exit 2
fi

# Fail closed if no off-box mirror is configured. A backup that lives only on this
# VPS dies with the VPS (host/disk loss takes the DB and every retained dump
# together), so a local-only run must NOT silently report success (Codex review
# 2026-06-14). Set ALLOW_LOCAL_ONLY_BACKUP=1 to deliberately accept local-only
# (e.g. before R2 is wired up) — then "success" means a local dump only. Pre-flight
# (before /start), so a miss pings /fail via the EXIT trap.
if [[ -z "$BACKUP_REMOTE" && "${ALLOW_LOCAL_ONLY_BACKUP:-}" != "1" ]]; then
    echo "backup_db: BACKUP_REMOTE is unset and ALLOW_LOCAL_ONLY_BACKUP != 1 — refusing to report a VPS-only backup as success. Configure an off-box rclone remote (runbook §3.6), or set ALLOW_LOCAL_ONLY_BACKUP=1 to accept local-only." >&2
    exit 2
fi

_healthcheck_ping /start

cd "$REPO_DIR"
mkdir -p "$BACKUP_DIR"
out="$BACKUP_DIR/millennium-$(date +%Y%m%d-%H%M%S).dump"
tmp="$out.partial"

# Dump with the container's own credentials. -T keeps the binary -Fc stream
# uncorrupted (no TTY). Write to a temp file and atomically rename on success so a
# reader (restore / off-box copy) never sees a partial archive. `name: millennium`
# in the compose file scopes this to the millennium-postgres container.
if ! docker compose -f docker-compose.yml exec -T postgres \
        sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' \
        > "$tmp"; then
    echo "backup_db: pg_dump failed; removing partial archive" >&2
    rm -f "$tmp"
    exit 1
fi

# A valid -Fc archive is never empty; guard a clean exit that wrote nothing.
if [[ ! -s "$tmp" ]]; then
    echo "backup_db: archive is empty; removing" >&2
    rm -f "$tmp"
    exit 1
fi

mv "$tmp" "$out"
echo "backup_db: wrote $out ($(du -h "$out" | cut -f1))"

# Retention: keep the $KEEP most-recent archives, delete older ones. The dump
# filenames embed a zero-padded UTC timestamp, so the glob's LEXICAL order is
# chronological (oldest first) — no `ls -t` needed, and robust against an mtime
# touched by a copy. `nullglob` makes an empty match an empty array (no error;
# stays bash-3.2-portable, no `mapfile`). FAIL CLOSED on a real `rm -f` error
# (read-only / mis-owned BACKUP_DIR): the old `| while rm | … || true` masked
# every failure, so a prune that couldn't delete would still upload and send the
# SUCCESS ping while dumps piled up to disk-full (Codex review 2026-06-14).
# `rm -f` already exits 0 for an already-gone file, so a non-zero here is a real
# failure worth aborting on — better to alarm than to silently rot the backups.
shopt -s nullglob
_dumps=("$BACKUP_DIR"/millennium-*.dump)
shopt -u nullglob
_dump_count=${#_dumps[@]}
if (( _dump_count > KEEP )); then
    for (( _i = 0; _i < _dump_count - KEEP; _i++ )); do
        echo "backup_db: pruning ${_dumps[_i]}"
        if ! rm -f "${_dumps[_i]}"; then
            echo "backup_db: prune failed for ${_dumps[_i]} — aborting before the success ping" >&2
            exit 1
        fi
    done
fi

# Off-box mirror (opt-in, gated on $BACKUP_REMOTE). All off-box validation lives
# HERE, not as a pre-flight, so a misconfigured optional add-on can never disable
# the core local backup (already durable + pruned above). We `copy` (not `sync`)
# so off-box retention (OFFSITE_KEEP_DAYS) can outlive local KEEP; the remote prune
# is filtered to our naming pattern so a mis-pointed remote can't delete anything
# outside the Millennium backup set.
if [[ -n "$BACKUP_REMOTE" ]]; then
    if ! [[ "$OFFSITE_KEEP_DAYS" =~ ^[1-9][0-9]*$ ]]; then
        echo "backup_db: OFFSITE_KEEP_DAYS must be a positive integer (got '$OFFSITE_KEEP_DAYS') — off-box mirror skipped, local dump retained at $out" >&2
        exit 2
    fi
    if ! command -v rclone >/dev/null 2>&1; then
        echo "backup_db: BACKUP_REMOTE is set but rclone is not installed — off-box mirror skipped, local dump retained at $out" >&2
        exit 2
    fi
    echo "backup_db: uploading to $BACKUP_REMOTE"
    if ! rclone copy --no-traverse "$out" "$BACKUP_REMOTE"; then
        echo "backup_db: rclone copy to $BACKUP_REMOTE failed — local dump retained at $out" >&2
        exit 1
    fi
    echo "backup_db: pruning remote archives older than ${OFFSITE_KEEP_DAYS}d on $BACKUP_REMOTE"
    if ! rclone delete --min-age "${OFFSITE_KEEP_DAYS}d" --include "millennium-*.dump" "$BACKUP_REMOTE"; then
        echo "backup_db: rclone remote prune on $BACKUP_REMOTE failed (upload succeeded)" >&2
        exit 1
    fi
fi

# Final success ping. Reaching here means: local dump landed + pruned, and (if
# BACKUP_REMOTE set) off-box mirror succeeded. A failed success ping is non-fatal.
_healthcheck_ping
