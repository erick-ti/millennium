#!/usr/bin/env bash
# Pull-based continuous deployment for the Millennium Hetzner self-host. The
# millennium-deploy.timer (~2 min) runs this; when origin/main has moved (or a prior
# deploy failed) it self-heals the dedicated deploy checkout to EXACTLY origin/main
# and invokes the existing, health-gated deploy.sh. A no-op re-probes the public
# route and is otherwise near-instant.
#
# WHY PULL, NOT PUSH: the `millennium` user is in the
# docker group (≈ host-root) and the box is SHARED with a co-tenant. A push
# deployer would have to store a credential in GitHub that lands here with
# host-root power over BOTH tenants. This puller holds NO inbound credential: it
# only fetches a public repo and decides locally. Worst case if compromised:
# "read a public repo." The trust arrow points outward, like backup_db.sh's rclone.
#
# CI GATE: branch protection (the protect-main ruleset, six required checks) keeps
# a red PR head off main, but it does not make a MERGE COMMIT's own tree tested
# before it lands: the ruleset does not require branches to be up to date, so a PR
# head can be green against a stale base, and the merge commit's push-triggered run
# only starts AFTER the merge, i.e. after this poller may already have deployed it
# (on 2026-09-20 a merge commit received no check suite at all and deployed
# unnoticed). So a commit deploys only once ci_gate.py (beside this script, run from
# the CURRENTLY INSTALLED checkout before any reset, so a candidate cannot influence
# its own gating) reports every required check completed + successful on THAT sha,
# read from GitHub's public check-runs API without a token. Absent, queued, or
# running checks (or an unreadable API) mean WAIT, bounded by DEPLOY_CI_WAIT_MAX
# before the dead-man alerts; a red check alerts now and never deploys; a blank
# DEPLOY_REQUIRED_CHECKS refuses (an empty list would be trivially green). There is
# no off switch: the bypass is a manual pinned deploy (stop millennium-deploy.timer,
# then `git checkout <sha> && ./deploy.sh` under the poller's flock). The gate checks
# the checks, not the ruleset, so it still holds if branch protection is ever
# weakened.
#
# DEPLOYED-STATE MARKER: the source of truth for "what is live" is
# $STATE_DIR/last_deployed_sha (written ONLY after deploy.sh AND the public probe
# succeed), NOT the git checkout HEAD. The self-healing reset advances HEAD to
# origin/main before deploy.sh runs, so a HEAD-based check would make a FAILED deploy
# look done (no retry, false green). Comparing origin/main to the marker means a
# failed deploy is retried every tick until it succeeds. A MISSING/lost marker means
# the deployed state is UNKNOWN → force a (re)deploy of origin/main (idempotent +
# health-gated); NEVER seed the marker from HEAD.
#
# DEDICATED-CHECKOUT POLICY: /home/millennium/millennium is CD-owned. Do NOT
# hand-edit tracked files there: a deploy `git reset --hard` silently reverts them.
# The live env files (.env / backup.env / deploy.env) must stay UNTRACKED + gitignored;
# the deploy fails closed BOTH before reset (if origin/main TRACKS one, reset --hard
# overwrites the live secret with the committed content) AND after reset (if one
# exists but is no longer gitignored, `git clean -fd` would delete it). NEVER run
# `git clean -fdx` here (-fdx deletes gitignored files). Backups (~/millennium-backups)
# and rclone config (~/.config/rclone) live OUTSIDE the checkout. Lock, marker, and
# run-logs live under ~/.local/state/millennium-deploy (millennium-owned, persistent,
# NOT ~/.cache, which a reaper could clear), never world-writable /tmp (shared box).
#
# ROLLBACK: a manual rollback detaches HEAD (deploy.sh's `git checkout <sha>` path);
# this poller refuses to act on a detached HEAD, so a rollback is honored until you
# re-attach to main. The durable rollback is a `git revert` pushed to origin/main.
#
# Operational discipline copied from backup_db.sh (Healthchecks /start, success, /fail
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
# CI check-run gate (see header). The required names mirror the protect-main
# ruleset; override ONLY to track a renamed job (comma-separated, quoted in
# deploy.env). Unset = the default six; set-but-BLANK = refuse to deploy (an empty
# list would be trivially green, so it is never a way to switch the gate off).
GITHUB_REPO="${DEPLOY_GITHUB_REPO:-erick-ti/millennium}"
REQUIRED_CHECKS="${DEPLOY_REQUIRED_CHECKS-scan for secrets,pytest (postgres),lint + build,e2e (smoke),backend image,frontend image}"
# How long a candidate may stay absent/queued/running before the dead-man gets /fail
# (CI normally completes in ~3 min; the alert means "no run exists or CI is stuck:
# look at GitHub Actions"). GATE_TIMEOUT bounds one evaluation (one API request,
# two at most).
CI_WAIT_MAX="${DEPLOY_CI_WAIT_MAX:-1800}"
GATE_TIMEOUT="${DEPLOY_GATE_TIMEOUT:-30}"
# ci_gate.py ships beside this script and runs from the INSTALLED checkout (this
# script's own directory), never from the candidate commit.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CI_GATE="$SCRIPT_DIR/ci_gate.py"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true
# A stuck or red candidate keeps one small run log per tick (logs are kept on any
# non-zero exit for post-mortem); bound the pile.
find "$STATE_DIR" -maxdepth 1 -name 'run.*.log' -mtime +7 -delete 2>/dev/null || true

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
# /proc/<pid>/cmdline) and the body via a 0600 file in the private STATE_DIR, never
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
# <sha>). If we're detached, a rollback is in progress, do NOT fetch/reset/redeploy
# over it (mirrors deploy.sh's own detached-HEAD guard). Re-attach to main to resume.
STEP="rollback-guard"
if ! git symbolic-ref -q HEAD >/dev/null; then
    log "detached HEAD — rollback in progress; skipping (re-attach to main to resume CD)"
    exit 0
fi

# Read the deployed-state marker (source of truth; see header) BEFORE the fetch so a
# fetch failure still reports it. A MISSING marker = unknown deployed state: leave
# DEPLOYED empty so the no-op branch is skipped and origin/main is (re)deployed, do
# NOT infer the deployed SHA from HEAD (it advances before deploy.sh succeeds).
DEPLOYED="$(cat "$MARKER" 2>/dev/null || true)"
OLD_SHA="(none)"
if [[ -n "$DEPLOYED" ]]; then OLD_SHA="${DEPLOYED:0:9}"; fi

STEP="fetch"
# Explicit destination refspec so the ref we read is EXACTLY the ref we fetched.
# `git fetch origin main` (source-only) writes FETCH_HEAD and only OPPORTUNISTICALLY
# updates refs/remotes/origin/main, not guaranteed across git versions / remote
# configs. If origin/main stayed stale, the marker could match it and we'd take the
# no-op path (false green) while main had actually advanced.
timeout "$FETCH_TIMEOUT" git fetch --quiet origin '+refs/heads/main:refs/remotes/origin/main'
NEW="$(git rev-parse --verify refs/remotes/origin/main)"
NEW_SHA="${NEW:0:9}"

if [[ -n "$DEPLOYED" && "$DEPLOYED" == "$NEW" ]]; then
    # Last SUCCESSFUL deploy already == origin/main. Gate the dead-man success ping
    # on the PUBLIC route still being 200, NOT merely on "main hasn't moved", so
    # (a) a prior /fail stays sticky until the site is genuinely healthy again, and
    # (b) the site breaking for ANY reason alerts. A probe failure -> set -e -> /fail.
    STEP="probe-noop"
    probe_public
    _hc_ping
    exit 0
fi

# CI check-run gate. Deploy $NEW only once GitHub reports every required check
# green on THAT commit (see the header). Runs BEFORE /start, the env-file guards,
# and the reset, so a blocked candidate leaves the checkout, the marker, and the
# dead-man's state exactly as they were. Verdicts: ready = deploy; pending/unknown =
# wait (probe + success ping as on a no-op tick, alert only past DEPLOY_CI_WAIT_MAX);
# failed/config error = exit 1 (the EXIT trap pings /fail with the verdict; sticky
# each tick).
STEP="ci-gate"
NOW="$(date +%s)"
if [[ -z "${REQUIRED_CHECKS//[[:space:],]/}" ]]; then
    log "REFUSING to deploy: DEPLOY_REQUIRED_CHECKS is blank (an empty check list would be trivially green). Unset it for the default six or list the required job names."
    exit 1
fi
_req_args=()
IFS=',' read -r -a _req_names <<<"$REQUIRED_CHECKS"
for _n in "${_req_names[@]}"; do _req_args+=(--require "$_n"); done

VERDICT="unknown"
GATE_LINE=""
_backoff="$(cat "$STATE_DIR/ci_gate.backoff" 2>/dev/null || true)"
if [[ "$_backoff" =~ ^[0-9]+$ ]] && (( _backoff > NOW )); then
    # GitHub rate-limited an earlier tick: honor its reset time, no request until then.
    VERDICT="pending"
    GATE_LINE="pending rate-limit backoff until $_backoff ($(( _backoff - NOW ))s left)"
else
    rm -f "$STATE_DIR/ci_gate.backoff"
    _gate_rc=0
    GATE_LINE="$(timeout "$GATE_TIMEOUT" python3 "$CI_GATE" --repo "$GITHUB_REPO" --sha "$NEW" "${_req_args[@]}" 2>>"$LOG")" || _gate_rc=$?
    GATE_LINE="${GATE_LINE%%$'\n'*}"
    # Every verdict needs BOTH the exit code and the matching verdict word on stdout:
    # an empty or odd answer is never trusted (python itself exits 2 when it cannot
    # open the script, which must not read as "pending" with a blank reason). Any
    # other code (a crash 1, a timeout 124, a missing python3 127) is unreadable.
    case "$_gate_rc" in
        0) _expect="ready" ;;
        2) _expect="pending" ;;
        3) _expect="failed" ;;
        4) _expect="unknown" ;;
        5) _expect="config-error" ;;
        *) _expect="" ;;
    esac
    if [[ -n "$_expect" && "$GATE_LINE" == "$_expect "* ]]; then
        VERDICT="$_expect"
    else
        VERDICT="unknown"
        GATE_LINE="unknown gate exit $_gate_rc without a${_expect:+ $_expect} verdict: ${GATE_LINE:-<no output>}"
    fi
    if [[ "$GATE_LINE" =~ retry_at=([0-9]+) ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}" >"$STATE_DIR/ci_gate.backoff"
    fi
fi
# Record the verdict for the operator (ci_gate.status = the last one, ci_gate.log =
# recent history) and for a /fail body (via the run log).
_stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "ci-gate $NEW_SHA: $GATE_LINE"
printf '%s %s %s\n' "$_stamp" "$NEW_SHA" "$GATE_LINE" >>"$STATE_DIR/ci_gate.log"
printf '%s %s %s\n' "$_stamp" "$NEW_SHA" "$GATE_LINE" >"$STATE_DIR/ci_gate.status"
if (( $(wc -l <"$STATE_DIR/ci_gate.log") > 200 )); then
    tail -n 200 "$STATE_DIR/ci_gate.log" >"$STATE_DIR/ci_gate.log.tmp" \
        && mv "$STATE_DIR/ci_gate.log.tmp" "$STATE_DIR/ci_gate.log"
fi

case "$VERDICT" in
    ready)
        rm -f "$STATE_DIR/ci_wait" "$STATE_DIR/ci_gate.backoff"
        ;;
    failed|config-error)
        log "NOT deploying $NEW_SHA: $GATE_LINE (site stays on $OLD_SHA)"
        exit 1
        ;;
    *)
        # pending / unknown: wait, bounded. ci_wait = "<sha> <first-seen epoch>",
        # restarted whenever the candidate changes, so a fresh commit is never
        # "stuck" on its predecessor's clock.
        _since="$NOW"
        _wait="$(cat "$STATE_DIR/ci_wait" 2>/dev/null || true)"
        if [[ "$_wait" == "$NEW "* ]]; then
            _prev="${_wait#* }"
            if [[ "$_prev" =~ ^[0-9]+$ ]]; then _since="$_prev"; fi
        fi
        printf '%s %s\n' "$NEW" "$_since" >"$STATE_DIR/ci_wait"
        _waited=$(( NOW - _since ))
        if (( _waited > CI_WAIT_MAX )); then
            log "CI still not green on $NEW_SHA after ${_waited}s (limit ${CI_WAIT_MAX}s): $GATE_LINE. Check GitHub Actions; if no run exists, dispatch the workflows by hand."
            exit 1
        fi
        log "waiting for CI on $NEW_SHA (${_waited}s of ${CI_WAIT_MAX}s): $GATE_LINE"
        STEP="probe-wait"
        probe_public
        _hc_ping
        exit 0
        ;;
esac

# Deploy origin/main. Covers "main moved", "the previous deploy failed" (the marker
# only advances on full success), and "marker missing/unknown", all redeploy here,
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
# actually use: a NEW commit that drops an env file from .gitignore is caught HERE,
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
# advanced after our fetch + guards, bypassing the env-file track-guard and drifting
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

# Gate success on the PUBLIC route through the edge. A non-200 -> /fail.
STEP="probe"
probe_public

# Record the new deployed SHA ONLY after deploy.sh AND the public probe both pass.
# Any earlier failure leaves the marker unchanged → retried next tick, never green.
printf '%s\n' "$NEW" >"$MARKER"

STEP="done"
log "deployed $OLD_SHA -> $NEW_SHA; public / and /api/health/ both 200"
_hc_ping
