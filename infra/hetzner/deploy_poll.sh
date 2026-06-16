#!/usr/bin/env bash
# Pull-based continuous deployment for the Millennium Hetzner self-host. The
# millennium-deploy.timer (~2 min) runs this; when origin/main has moved (or a prior
# deploy failed) it self-heals the dedicated deploy checkout to EXACTLY origin/main
# and invokes the existing, health-gated deploy.sh. A no-op re-probes the public
# route and is otherwise near-instant.
#
# WHY PULL, NOT PUSH (design panel, 2026-06-16): the `millennium` user is in the
# docker group (≈ host-root) and the box is SHARED with a co-tenant. A push
# deployer would have to store a credential in GitHub that lands here with
# host-root power over BOTH tenants. This puller holds NO inbound credential — it
# only fetches a public repo and decides locally. Worst case if compromised:
# "read a public repo." The trust arrow points outward, like backup_db.sh's rclone.
#
# CI ASSUMPTION (do not remove): this trusts origin/main because branch protection
# (the protect-main ruleset — six required checks) blocks any commit from landing
# on main without green CI. Every commit on main is therefore green-by-construction,
# so we deploy origin/main with no token. *** IF BRANCH PROTECTION IS EVER WEAKENED
# OR REMOVED, THIS DEPLOYER NO LONGER GUARANTEES CI-GREEN RELEASES *** — restore it,
# or add a `gh api` check-run gate between the fetch and the deploy below.
#
# DEPLOYED-STATE MARKER: the source of truth for "what is live" is
# $STATE_DIR/last_deployed_sha (written ONLY after deploy.sh AND the public probe
# succeed) — NOT the git checkout HEAD. The self-healing reset advances HEAD to
# origin/main before deploy.sh runs, so a HEAD-based check would make a FAILED deploy
# look done (no retry, false green). Comparing origin/main to the marker means a
# failed deploy is retried every tick until it succeeds. A MISSING/lost marker means
# the deployed state is UNKNOWN → force a (re)deploy of origin/main (idempotent +
# health-gated); NEVER seed the marker from HEAD (Codex review).
#
# DEDICATED-CHECKOUT POLICY: /home/millennium/millennium is CD-owned. Do NOT
# hand-edit tracked files there — a deploy `git reset --hard` silently reverts them.
# The live env files (.env / backup.env / deploy.env) must stay UNTRACKED + gitignored;
# the deploy fails closed BOTH before reset (if origin/main TRACKS one — reset --hard
# overwrites the live secret with the committed content) AND after reset (if one
# exists but is no longer gitignored — `git clean -fd` would delete it). NEVER run
# `git clean -fdx` here (-fdx deletes gitignored files). Backups (~/millennium-backups)
# and rclone config (~/.config/rclone) live OUTSIDE the checkout. Lock, marker, and
# run-logs live under ~/.local/state/millennium-deploy (millennium-owned, persistent —
# NOT ~/.cache, which a reaper could clear), never world-writable /tmp (shared box).
#
# ROLLBACK: a manual rollback detaches HEAD (deploy.sh's `git checkout <sha>` path);
# this poller refuses to act on a detached HEAD, so a rollback is honored until you
# re-attach to main. The durable rollback is a `git revert` pushed to origin/main.
#
# Operational discipline copied from backup_db.sh (Healthchecks /start–success–/fail
# dead-man pings, EXIT trap, secrets not logged) plus flock single-instance and
# per-step `timeout` from the push_stats.sh sibling.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/millennium}"
DEPLOY_HEALTHCHECK_URL="${DEPLOY_HEALTHCHECK_URL:-}"
PUBLIC_HOST="${DEPLOY_PUBLIC_HOST:-millennium.erickti.com}"
# Lock, marker, and run-logs in a millennium-OWNED, PERSISTENT dir at a FIXED path
# (one lock across the systemd run and a manual run), never world-writable /tmp and
# never ~/.cache (a cache-reaper could delete the deployed-state marker).
STATE_DIR="${DEPLOY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/millennium-deploy}"
LOCKFILE="${DEPLOY_LOCKFILE:-$STATE_DIR/deploy.lock}"
MARKER="$STATE_DIR/last_deployed_sha"
# Per-step timeouts (seconds). The build can run minutes on a 4 GB box; deploy.sh
# also self-bounds its `up --wait` at 180s.
FETCH_TIMEOUT="${DEPLOY_FETCH_TIMEOUT:-60}"
DEPLOY_TIMEOUT="${DEPLOY_RUN_TIMEOUT:-1200}"
PROBE_TIMEOUT="${DEPLOY_PROBE_TIMEOUT:-15}"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

# Single-instance guard: a slow deploy must never overlap the next timer tick. The
# lock lives in the millennium-owned STATE_DIR, so there is no co-tenant symlink /
# pre-creation / truncation vector (unlike a fixed name in world-writable /tmp).
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    echo "deploy_poll: another run holds $LOCKFILE — skipping this tick" >&2
    exit 0
fi

STEP="init"
OLD_SHA="unknown"
NEW_SHA="unknown"
LOG=""

log() {
    local m="deploy_poll: $*"
    echo "$m" >&2
    if [[ -n "$LOG" ]]; then printf '%s\n' "$m" >>"$LOG"; fi
}

# Healthchecks ping. GET <URL><suffix>; for /fail also POST a diagnostic body so the
# alert is actionable. The URL is a credential: it is fed to curl via a config piped
# on STDIN (printf is a shell builtin, so it never appears in any process's argv /
# /proc/<pid>/cmdline) and the body via a 0600 file in the private STATE_DIR — never
# argv, which is world-readable to the co-tenant on this shared box. curl failures
# are swallowed so a notifier outage can't fail an otherwise-good run.
_hc_ping() {
    local suffix="${1:-}" body="${2:-}" bodyfile=""
    if [[ -z "$DEPLOY_HEALTHCHECK_URL" ]]; then return 0; fi
    if [[ -n "$body" ]]; then
        bodyfile="$(mktemp "$STATE_DIR/ping.XXXXXX.body" 2>/dev/null)" || bodyfile=""
        if [[ -n "$bodyfile" ]]; then printf '%s' "$body" >"$bodyfile" 2>/dev/null || true; fi
    fi
    {
        printf 'url = "%s"\n' "${DEPLOY_HEALTHCHECK_URL}${suffix}"
        printf 'max-time = 10\nretry = 3\nretry-connrefused\nsilent\nshow-error\nfail\n'
        if [[ -n "$bodyfile" ]]; then printf 'data = "@%s"\n' "$bodyfile"; fi
    } | curl -K - >/dev/null 2>&1 || true
    if [[ -n "$bodyfile" ]]; then rm -f "$bodyfile"; fi
    return 0
}

# On any non-zero exit, ping /fail with the failed step, SHA transition, exit code,
# and the tail of this run's log. Keep the log on failure for post-mortem; remove on
# success.
_on_exit() {
    local rc=$?
    if (( rc != 0 )); then
        local tail_out=""
        if [[ -n "$LOG" && -f "$LOG" ]]; then
            tail_out="$(tail -n 25 "$LOG" 2>/dev/null || true)"
        fi
        _hc_ping /fail "$(printf 'Millennium deploy FAILED\nstep=%s rc=%s\n%s -> %s\n--- last log lines ---\n%s\n' \
            "$STEP" "$rc" "$OLD_SHA" "$NEW_SHA" "$tail_out")"
        log "FAILED at step '$STEP' (rc=$rc); pinged /fail; full log kept at ${LOG:-<none>}"
    elif [[ -n "$LOG" ]]; then
        rm -f "$LOG"
    fi
    exit "$rc"
}
trap _on_exit EXIT

LOG="$(mktemp "$STATE_DIR/run.XXXXXX.log")"

# Probe the PUBLIC route through the edge. --resolve pins to the local edge
# (127.0.0.1:443) regardless of external DNS / NAT-hairpin behavior; both the
# frontend shell and the proxied backend health endpoint must return 200. A small
# retry absorbs a transient edge blip (e.g. the Caddy reload deploy.sh does just
# before this runs). Returns non-zero if either route is not OK.
probe_public() {
    local r=0
    timeout "$PROBE_TIMEOUT" curl -fsS --retry 2 --retry-delay 1 -o /dev/null \
        --resolve "${PUBLIC_HOST}:443:127.0.0.1" "https://${PUBLIC_HOST}/" >>"$LOG" 2>&1 || r=1
    timeout "$PROBE_TIMEOUT" curl -fsS --retry 2 --retry-delay 1 -o /dev/null \
        --resolve "${PUBLIC_HOST}:443:127.0.0.1" "https://${PUBLIC_HOST}/api/health/" >>"$LOG" 2>&1 || r=1
    return "$r"
}

cd "$REPO_DIR"

# Honor a manual rollback: deploy.sh's rollback path detaches HEAD (git checkout
# <sha>). If we're detached, a rollback is in progress — do NOT fetch/reset/redeploy
# over it (mirrors deploy.sh's own detached-HEAD guard). Re-attach to main to resume.
STEP="rollback-guard"
if ! git symbolic-ref -q HEAD >/dev/null; then
    log "detached HEAD — rollback in progress; skipping (re-attach to main to resume CD)"
    exit 0
fi

# Read the deployed-state marker (source of truth; see header) BEFORE the fetch so a
# fetch failure still reports it. A MISSING marker = unknown deployed state: leave
# DEPLOYED empty so the no-op branch is skipped and origin/main is (re)deployed — do
# NOT infer the deployed SHA from HEAD (it advances before deploy.sh succeeds).
DEPLOYED="$(cat "$MARKER" 2>/dev/null || true)"
OLD_SHA="(none)"
if [[ -n "$DEPLOYED" ]]; then OLD_SHA="${DEPLOYED:0:9}"; fi

STEP="fetch"
# Explicit destination refspec so the ref we read is EXACTLY the ref we fetched.
# `git fetch origin main` (source-only) writes FETCH_HEAD and only OPPORTUNISTICALLY
# updates refs/remotes/origin/main — not guaranteed across git versions / remote
# configs. If origin/main stayed stale, the marker could match it and we'd take the
# no-op path (false green) while main had actually advanced (Codex review).
timeout "$FETCH_TIMEOUT" git fetch --quiet origin '+refs/heads/main:refs/remotes/origin/main'
NEW="$(git rev-parse --verify refs/remotes/origin/main)"
NEW_SHA="${NEW:0:9}"

if [[ -n "$DEPLOYED" && "$DEPLOYED" == "$NEW" ]]; then
    # Last SUCCESSFUL deploy already == origin/main. Gate the dead-man success ping
    # on the PUBLIC route still being 200 — NOT merely on "main hasn't moved" — so
    # (a) a prior /fail stays sticky until the site is genuinely healthy again, and
    # (b) the site breaking for ANY reason alerts. A probe failure -> set -e -> /fail.
    STEP="probe-noop"
    probe_public
    _hc_ping
    exit 0
fi

# Deploy origin/main. Covers "main moved", "the previous deploy failed" (the marker
# only advances on full success), and "marker missing/unknown" — all redeploy here,
# so there is never a false green or an abandoned failure.
log "deploying $OLD_SHA -> $NEW_SHA"
_hc_ping /start

# Fail closed BEFORE reset if origin/main TRACKS any checkout env file: `git reset
# --hard` overwrites an untracked file with the committed content when the target
# tracks it (verified), so a commit that accidentally tracks .env/backup.env/
# deploy.env would DESTROY the live secret before the post-reset guard runs. The env
# files must never be tracked.
STEP="track-guard"
for _envf in infra/hetzner/.env infra/hetzner/backup.env infra/hetzner/deploy.env; do
    if git cat-file -e "$NEW:$_envf" 2>/dev/null; then
        log "REFUSING to deploy: origin/main ($NEW_SHA) TRACKS $_envf — reset --hard would overwrite the live secret. Remove it from the commit."
        exit 1
    fi
done

# Self-healing checkout: become EXACTLY origin/main, dropping any drift. (reset
# --hard only rewrites TRACKED files; the untracked, now-confirmed-untracked env
# files survive until `clean`.)
STEP="reset"
git reset --hard --quiet "$NEW"

# Fail closed if `git clean -fd` would delete a checkout env file (it exists but is
# not gitignored). AFTER the reset, so it checks the .gitignore that `clean` will
# actually use — a NEW commit that drops an env file from .gitignore is caught HERE,
# before clean deletes the live secret (which reset left in place because it's
# untracked). Verifies the cleanup-safety invariant, never just asserts it.
STEP="clean-guard"
for _envf in infra/hetzner/.env infra/hetzner/backup.env infra/hetzner/deploy.env; do
    if [[ -f "$_envf" ]] && ! git check-ignore -q "$_envf"; then
        log "REFUSING to clean: $_envf exists but is not gitignored — git clean -fd would delete it. Add it to .gitignore."
        exit 1
    fi
done

STEP="clean"
git clean -fd >>"$LOG" 2>&1

# The actual deploy, of EXACTLY the guarded SHA: DEPLOY_SKIP_PULL=1 tells deploy.sh
# NOT to run its own `git pull` (which could fast-forward past $NEW if origin/main
# advanced after our fetch + guards — bypassing the env-file track-guard and drifting
# the marker). The existing script is health-gated + rollback-aware.
STEP="deploy.sh"
DEPLOY_SKIP_PULL=1 timeout "$DEPLOY_TIMEOUT" "$REPO_DIR/infra/hetzner/deploy.sh" >>"$LOG" 2>&1

# Defense-in-depth: confirm nothing moved HEAD off the guarded SHA during the deploy
# before we trust it (probe + record the marker). If it did, abort without recording.
STEP="verify-head"
HEAD_AFTER="$(git rev-parse HEAD)"
if [[ "$HEAD_AFTER" != "$NEW" ]]; then
    log "ABORT: HEAD moved to ${HEAD_AFTER:0:9} during deploy (expected $NEW_SHA) — not recording marker."
    exit 1
fi

# Gate success on the PUBLIC route through the edge (req 3). A non-200 -> /fail.
STEP="probe"
probe_public

# Record the new deployed SHA ONLY after deploy.sh AND the public probe both pass.
# Any earlier failure leaves the marker unchanged → retried next tick, never green.
printf '%s\n' "$NEW" >"$MARKER"

STEP="done"
log "deployed $OLD_SHA -> $NEW_SHA; public / and /api/health/ both 200"
_hc_ping
