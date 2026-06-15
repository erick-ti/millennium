# Railway deploy runbook

> **SUPERSEDED (2026-06-14).** Millennium deploys to a self-hosted Hetzner VPS —
> see the local Hetzner deploy runbook. This Railway plan is retained as the
> evaluated managed-PaaS alternative; it predates the DatabaseCache change that
> dropped Redis, so its env matrix below still lists `REDIS_URL`/Redis as
> required.

How Millennium deploys to Railway (phase-1 deploy target). This is the authoritative
companion to the config-as-code files in `infra/railway/*.railway.json` — JSON carries
no comments, so **every "why" lives here**. The four topology decisions were settled at
the deploy kickoff (DECISIONS.md 2026-06-12): Railway **cron** services (no always-on
worker/beat), a **private-only** backend, **autodeploy** on push (Wait-for-CI off), and
the **generated** `*.up.railway.app` domain at launch.

Slices 1–5 are repo-side prep (merged). This runbook drives the **live** provisioning
sessions (slices 6–7) and the post-deploy hardening (slice 8). Nothing here has been
exercised against the live dashboard yet — items marked **[verify live]** are the first
deploy's open checks.

## Topology

Six logical Railway services in one project, all from this repo on `main`, plus two
managed plugins:

```
Internet ──HTTPS──> [frontend]  (PUBLIC, *.up.railway.app, Next standalone)
                        │  /api/* rewrite, baked at build time
                        ▼
                    http://backend.railway.internal:8000
                        │
                    [backend web]  (PRIVATE-only, gunicorn, no public domain)
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼                ▼
   ${Postgres}     ${Redis}         (private network)
        ▲               ▲
        └──────┬────────┘
   [cron: sync_ygoprodeck 02:00]  [cron: sync_tcgcsv 03:00]
   [cron: value_portfolios 04:00] [cron: run_alerts 05:00]   (all UTC; backend image)
```

- **frontend** — the only public service. Serves the Next standalone server; proxies
  `/api/*` to the backend over the private network. `BACKEND_URL` is baked into the
  image at **build** time (see Operational contracts).
- **backend web** — gunicorn, reachable only at `backend.railway.internal:8000`. Owns
  database migrations (pre-deploy step). No public domain (smaller attack surface; no
  egress cost; `/admin/` is reachable only via `railway run` tunneling).
- **4 cron services** — each builds the **backend** image and runs one management
  command on a schedule, then exits. They replace Celery worker+beat (kickoff fork 1).
- **Postgres** and **Redis** — managed Railway plugins. Redis is cache + the (idle but
  fail-closed-required) Celery broker/result backend; no worker consumes it under the
  cron topology.

## Per-service settings (the dashboard half)

Two things are **not** expressible in `railway.json` and must be set per-service in the
dashboard. Getting either wrong fails the deploy in a way the committed config can't show:

| Railway service name | Root Directory | Config File path |
|---|---|---|
| **`backend`** (exact — see below) | `/backend` | `/infra/railway/backend.railway.json` |
| `frontend` | `/frontend` | `/infra/railway/frontend.railway.json` |
| `sync-ygoprodeck` | `/backend` | `/infra/railway/cron-sync-ygoprodeck.railway.json` |
| `sync-tcgcsv` | `/backend` | `/infra/railway/cron-sync-tcgcsv.railway.json` |
| `value-portfolios` | `/backend` | `/infra/railway/cron-value-portfolios.railway.json` |
| `run-alerts` | `/backend` | `/infra/railway/cron-run-alerts.railway.json` |

- **The backend service MUST be named exactly `backend`.** Railway private DNS is
  service-name-based (`<slugified-name>.railway.internal`), and the frontend image bakes
  `BACKEND_URL=http://backend.railway.internal:8000` at build time while the backend's
  `ALLOWED_HOSTS` lists `backend.railway.internal`. Name the service "backend web" or
  accept a generated name and its DNS becomes e.g. `backend-web.railway.internal` — the
  baked upstream resolves to nothing and **every** `/api/*` request fails (and Django would
  reject the wrong Host). If you must use a different name, change `BACKEND_URL` (then
  **rebuild** the frontend) and `ALLOWED_HOSTS` to match. The other services' names are
  free — nothing resolves the crons (no inbound traffic), and the managed Postgres/Redis
  names only need to match the `${{ServiceName.*}}` reference variables.
- **Root Directory is mandatory.** The Dockerfiles use subdir-relative `COPY` (e.g.
  `COPY pyproject.toml uv.lock ./`, `COPY package.json package-lock.json ./`), so the
  build **context** must be scoped to `/backend` (or `/frontend`). With Root Directory
  set, Railway auto-detects the `Dockerfile` at that root — which is why the JSON omits
  `dockerfilePath`. If a service is created without Root Directory, the context is the
  repo root and every relative `COPY` fails. **[verify live]** that the dashboard's
  Config File field accepts the nested `/infra/railway/*.railway.json` path; if it
  rejects a non-root path, fall back to repo-root-level files (e.g. `/railway.backend.json`).
- **`watchPatterns` are repo-root-relative**, even with a Root Directory set — that's
  why the JSON uses `backend/**` / `frontend/**` (no leading slash). Do **not** "fix"
  them to `/backend/**`: that breaks the conditional-deploy behavior (a backend-only push
  would redeploy the frontend, or vice versa). Each service ALSO watches its OWN config
  file (e.g. `infra/railway/backend.railway.json`), so a config-only change (a cron
  schedule, `drainingSeconds`, a healthcheck) actually triggers a redeploy that reads it —
  without that, Railway only reads config-as-code when a deploy is created, and a
  config-only commit would leave production on stale settings while the repo looks fixed.

Dashboard-only toggles (no `railway.json` field):

- **Autodeploy** on `main`: **Enabled** (default). `protect-main` already gates `main`
  through **six required checks** — `scan for secrets`, `pytest (postgres)`,
  `lint + build`, `e2e (smoke)`, **`backend image`, and `frontend image`** (the last two
  promoted 2026-06-13). The two image checks build AND boot the exact production images,
  so a broken prod image can't reach `main` and therefore can't autodeploy — which is what
  makes the next bullet (Wait-for-CI off) safe.
- **Wait for CI**: **OFF** (kickoff fork 3). It waits on *all* check suites, so the one
  remaining **advisory** check (`CodeQL`/`Analyze`) going red would silently *skip*
  production deploys — fighting the advisory-by-design posture. `protect-main` (including
  the image-build checks above) is the deploy gate, not Wait-for-CI.
- **Public domain**: generate a `*.up.railway.app` domain for the **frontend only**. The
  backend gets **no** public domain (private-only).
- **PORT**: set `PORT=8000` as a **backend-web** service variable so the frontend's baked
  `BACKEND_URL=...:8000` stays valid. Cron services exit (no PORT, no healthcheck). The
  frontend leaves Railway's injected PORT alone (`server.js` reads it).

## Config-as-code field rationale

(Why each committed value is what it is — change a value only with this in view.)

- **`build.builder: "DOCKERFILE"`** — both images are Dockerfile-built (not Nixpacks).
- **backend/frontend omit `startCommand`** — so the image `CMD` is the single source of
  truth (gunicorn `exec ... --bind [::]:${PORT}` / `node server.js`). Do **not** set a
  dashboard Start Command; config-as-code overrides the dashboard, but omitting the key
  here means "use the image default" without relying on the unverified `null`-defers
  semantics. **[verify live]** the image CMD runs (no override needed).
- **backend `preDeployCommand: ["python manage.py migrate --noinput"]`** — runs in a
  one-off container before the new deploy goes live. **[verify live]** that a non-zero
  exit (a failing migration) HALTS the deploy rather than going live on a stale schema.
- **backend `healthcheckPath: /api/health/`** — the `AllowAny`, DB-free liveness probe.
  Railway probes plain HTTP with `Host: healthcheck.railway.app` (hence that host in
  `ALLOWED_HOSTS`, and `DJANGO_SECURE_SSL_REDIRECT=False` so the probe isn't 301'd).
- **frontend `healthcheckPath: /api/health/`** (NOT `/`) — deliberately exercises the
  baked `/api/*` rewrite to the private backend, so a wrong `BACKEND_URL` / backend
  service name / port / private-DNS failure makes the frontend deploy go **unhealthy at
  deploy time** instead of silently serving a shell where every login/read/import 500s
  (the misconfiguration class flagged in review). Slice-4 CI already proved the standalone
  image proxies `/api/health/` → backend → 200. This couples the frontend deploy to the
  backend being up — fine here: the provisioning order deploys the backend first, and a
  frontend without its backend is non-functional anyway. **[verify live]** that Railway's
  healthcheck is a deploy-time gate (not a continuous liveness probe that would couple
  ongoing frontend health to backend availability); and on a commit touching BOTH
  `backend/**` and `frontend/**`, the parallel frontend deploy may retry within the 120s
  `healthcheckTimeout` until the backend's deploy settles.
- **`healthcheckTimeout: 120`** — the deploy-time window Railway waits for `/api/health/`
  to return 200 before marking the deploy failed (Railway's default is 300s; 120 is
  generous for boot + migrate here). This is a *different* knob from gunicorn's
  `--timeout`/`--graceful-timeout` (per-request cap + shutdown grace, both 120 in the
  Dockerfile, sized to the synchronous import) — they coincide at 120 but need not move
  in lockstep.
- **web/frontend `restartPolicyType: ON_FAILURE` (maxRetries 3)** — long-running
  services restart on crash.
- **backend `drainingSeconds: 150`** — Railway's SIGTERM→SIGKILL drain window defaults
  to **0s**, which would SIGKILL the container the instant a deploy/restart starts —
  before gunicorn's `--graceful-timeout 120` can finish an in-flight request. A
  synchronous CSV import (`POST /api/imports/batches/`, `run_import` runs inline and is
  deliberately NOT batch-transactional) killed mid-flight leaves committed rows/lots under
  a still-`PROCESSING` batch. 150 > 120 gives gunicorn its full graceful window plus
  margin, so this is **enforced in config**, not a live check.
- **frontend `drainingSeconds: 150`** — the import path is TWO hops (browser → Next proxy →
  Django), so the frontend hop needs the same drain as the backend. With the R7
  `proxyTimeout: 125s`, the Next proxy now holds a long `/api/*` request in-flight for up
  to 125s; at the 0s default, a frontend deploy/restart would SIGKILL it mid-proxy, severing
  the browser↔Next connection while Django keeps committing — the same failed-but-committed
  import the proxy-timeout fix targets, via a different trigger. The Next standalone server
  DOES trap SIGTERM and drain gracefully in production (`start-server.js` `cleanup`:
  `server.close()` finishes pending requests; `closeAllConnections()` is dev-only), so a
  drain window > the 125s proxy timeout lets the in-flight import complete. 150 matches the
  backend. (Codex review 2026-06-13 — this corrected an earlier, now-stale claim that the
  frontend held no long in-flight work; true before the proxy-timeout change, false after.)
- **The cron services keep the 0s default — deliberately, because drain would be a no-op for
  them:** `drainingSeconds` only buys time for a process that *traps* SIGTERM to finish
  gracefully (gunicorn and the Next server do); a bare `python manage.py` command installs no
  SIGTERM handler, so SIGTERM terminates it immediately and the drain window is never used.
  The crons' interrupt-safety comes from their design, not drain — see the next bullet.
- **cron `restartPolicyType: NEVER` + deploy-during-run safety** — a clean exit-0 is normal
  completion, not a crash; NEVER stops a crash-loop on success, and the *schedule* still
  fires the next run regardless. Two interruption cases: (a) an **application failure** is
  caught → a FAILED `SyncRun`/`ValuationRun`/`AlertRun` is recorded, and for the
  **atomic** commands (`value_portfolios`, `run_alerts` — writes + run-record in one
  `transaction.atomic`) the pass rolls back cleanly; (b) a **hard kill** (a deploy/restart
  coinciding with the 02:00–05:00 window — rare) SIGKILLs the process with no audit row.
  The atomic commands roll back to nothing on (b) too. The two **syncs** (`sync_ygoprodeck`,
  `sync_tcgcsv`) write *incrementally* (per-card saves; per-group reconcile commits +
  append-only price rows), so a hard kill can leave a **partial-but-valid** catalog/price
  capture with no SyncRun — but this neither corrupts nor misleads downstream: the writes
  are idempotent + append-only (the next run completes them without duplication, alias-aware),
  and `value_portfolios`/`run_alerts` REFUSE unless a **same-day SUCCESS pricing SyncRun**
  exists, so a killed `sync_tcgcsv` (no SUCCESS) defers valuation/alerts a day rather than
  valuing a partial price table. This is the same tolerance a mid-run Celery-worker kill
  had on the prior topology — not a new risk. Deferred hardening (NOT slice 5): a
  SIGTERM-trapping base command + a drain window, or wrapping the syncs all-or-nothing —
  only if hard-kill-mid-sync proves common (it shouldn't; deploys rarely hit the cron window).
  Revisit `ON_FAILURE` only if transient upstream (YGOPRODeck/TCGCSV) blips prove common.
- **cron `cronSchedule`** — 02:00/03:00/04:00/05:00 UTC, matching the old Celery-beat
  schedule. Order is deliberate (metadata → pricing → valuation → alerts), but the real
  guard is each command's in-app same-day dependency check, not the 1h gaps.
- **cron services omit `healthcheckPath`/`preDeployCommand`/PORT** — a run-and-exit
  process answers no probe and owns no migrations (the web service does).

## Environment variable matrix

`${{Postgres.*}}` / `${{Redis.*}}` are Railway reference-variable syntax — rename if the
managed services aren't named `Postgres`/`Redis`. The seven `collectstatic` placeholders
in `backend/Dockerfile` are **build-only** and never reach Railway env — do not confuse
them with these.

| Variable | Backend web | Cron (all 4) | Frontend | Posture / value |
|---|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | required | required | — | `config.settings.prod` (Invariant 1 fails closed if unset) |
| `DJANGO_SECRET_KEY` | required | required | — | strong random; same value across all backend-image services |
| `DJANGO_ALLOWED_HOSTS` | required | required | — | `backend.railway.internal,healthcheck.railway.app` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | required | required | — | exact `https://<app>.up.railway.app` (set after the domain exists, then redeploy) |
| `DATABASE_URL` | required | required | — | `${{Postgres.DATABASE_URL}}` (prefer the private host — **[verify live]**) |
| `REDIS_URL` | required | required | — | `${{Redis.REDIS_URL}}` |
| `CELERY_BROKER_URL` | required | required | — | `${{Redis.REDIS_URL}}` — idle (no worker) but fail-closed-required (Invariant 2) |
| `CELERY_RESULT_BACKEND` | required | required | — | `${{Redis.REDIS_URL}}` |
| `DJANGO_SECURE_SSL_REDIRECT` | `False` | `False` | — | private-only: plain-HTTP healthcheck must not 301 |
| `PORT` | `8000` | — | injected | pin backend web; cron exits; frontend uses injected |
| `DJANGO_SESSION_COOKIE_SAMESITE` | default `Lax` | default | — | exact `Lax`/`Strict`/`None` only, or boot fails |
| `DJANGO_CSRF_COOKIE_SAMESITE` | default `Lax` | default | — | same |
| `DJANGO_NUM_PROXIES` | default `0` | default | — | leave `0`; slice 8 sets the verified value after the XFF spoof test |
| `DJANGO_SECURE_HSTS_SECONDS` | default `0` | default | — | leave `0` until HTTPS proven on the final host (Invariant 6); ramp in slice 8+ |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` / `_PRELOAD` | default `False` | default | — | leave default |
| `SYNC_GUARD_METADATA_TOLERANCE` | default `0.02` | (ygoprodeck) | — | float in [0,1) |
| `SYNC_GUARD_PRICING_TOLERANCE` | default `0.10` | (tcgcsv) | — | float |
| `SYNC_GUARD_ARCHETYPE_TOLERANCE` | default `0.05` | (ygoprodeck) | — | float |
| `BACKEND_URL` | — | — | **build-time** | `http://backend.railway.internal:8000` — a Docker build ARG baked into `server.js`; a change needs a frontend **rebuild**, not a restart |

`WEB_CONCURRENCY` (gunicorn workers, image default `2`) and the frontend's
`NODE_ENV`/`HOSTNAME`/`NEXT_TELEMETRY_DISABLED` are baked into the images — no dashboard
action needed.

## Provisioning order (slices 6–7)

1. Create the project; add the **Postgres** and **Redis** plugins. Note their service
   names (used in the `${{...}}` refs). **[verify live]** that the injected `DATABASE_URL`
   host ends in `.railway.internal` (private); prefer the explicit private URL if not.
2. Create the **backend web** service (repo `main`, Root Directory `/backend`, Config File
   `/infra/railway/backend.railway.json`). Set its env (matrix above; `PORT=8000`,
   `DJANGO_CSRF_TRUSTED_ORIGINS` is a placeholder for now). Deploy.
3. Verify: the `preDeployCommand` migrate ran; the healthcheck is green (it sends
   `Host: healthcheck.railway.app`); `/api/health/` returns 200 over the private network;
   `/api/schema/` returns 403 anonymously (Invariant 7 in prod). Create the superuser via
   `railway run python manage.py createsuperuser`.
4. Create the **frontend** service (Root Directory `/frontend`, Config File
   `/infra/railway/frontend.railway.json`). Set `BACKEND_URL=http://backend.railway.internal:8000`
   as a service variable so Railway forwards it to the build ARG. Deploy. Generate the
   public `*.up.railway.app` domain.
5. Set `DJANGO_CSRF_TRUSTED_ORIGINS=https://<app>.up.railway.app` (the exact generated
   origin) on the backend web service and **redeploy** the backend.
6. Browser-verify on the public domain: login → CSV import upload → approve → `/collection`
   shows the holding (exercises session auth, the CSRF/Origin gate, the upload body-size
   chain, and the import state machine end to end).
7. Create the 4 **cron** services (each Root Directory `/backend`, its own Config File).
   Next morning, confirm the chain via the `SyncRun`/`ValuationRun`/`AlertRun` records.

## Operational contracts (standing, no automated guard)

- **Migrations must be backward-compatible (expand/contract) — the cross-service skew
  rule.** Only the **backend web** service migrates (its `preDeployCommand`); the 4 cron
  services watch `backend/**` and so redeploy the new ORM code on the same
  migration-bearing commit, but they do **not** migrate. They must not — five concurrent
  migrators would race on the migration table / DDL locks; a single migrator (web) is
  correct. The consequence: there is a window where new cron code can run against the old
  schema — narrow in the normal case (a cron only executes at 02:00–05:00 UTC, by which
  time the web migrate, which runs in seconds, is long done), but real if the web migrate
  **fails** (its deploy halts, leaving web on old code+schema, while the cron images still
  advance to new code). The standing rule that makes this safe is **expand/contract**: a
  migration is additive/backward-compatible and the new code tolerates the previous schema
  for one deploy cycle (add columns/tables nullable-or-defaulted; never rename/drop in the
  same release as the code that stops using them). This is the repo's existing additive-
  migration style. **If a migration genuinely can't be backward-compatible**, do NOT rely
  on autodeploy: pause the cron services' autodeploy, deploy the backend web (migrate)
  first, confirm it's green, then resume/redeploy the crons. Also: a **failed** web migrate
  must be resolved before the next 02:00 cron window (hours away), since the crons will
  otherwise tick on new-code-vs-old-schema.
- **The synchronous-import timeout chain must stay ordered: Railway edge ≥ Next proxy ≥
  gunicorn ≥ the import.** The public path is browser → Next proxy → Django. The import
  (`POST /api/imports/batches/`, `MAX_UPLOAD_ROWS=10k` ≈ ≤100s on Railway) is bounded by
  gunicorn's `--timeout 120`; `frontend/next.config.ts` sets `experimental.proxyTimeout:
  125000` (> 120s) so the **backend** timeout is authoritative — without it, Next's 30s
  default would 500 a legitimate large import at the proxy while Django keeps committing
  rows (a "failed" import that partly succeeded). **[verify live]** Railway's **edge**
  request timeout sits in front of the Next proxy — confirm it also exceeds 120s (raise it
  or lower `MAX_UPLOAD_ROWS` if not), or large imports get cut at the edge regardless.
- **`BACKEND_URL` is frozen at frontend image build.** Renaming the backend service or
  changing its `PORT` silently breaks every `/api/*` proxy in production until the
  frontend is rebuilt. The slice-4 CI image smoke uses an aliased stub, so it cannot catch
  a real-service rename. Pin `PORT=8000`; never rename the backend service.
- **Deploy drain is enforced on BOTH import-path hops, not assumed.** `backend.railway.json`
  AND `frontend.railway.json` each set `drainingSeconds: 150` (> the 120s gunicorn window /
  125s Next proxy timeout) because Railway's default drain is 0s — see the field rationale
  above. The import traverses browser → Next proxy → Django, so a kill at *either* hop
  severs it; both drain windows must stay above their respective timeouts. Don't lower
  either below its service's request timeout.
- **Cron skip-on-overlap is silent.** A hung run (stuck lock, slow fetch) means Railway
  skips the next scheduled run with no alert (no error alerting exists — logs only,
  accepted for the MVP). The append-only `SyncRun`/`ValuationRun`/`AlertRun` records are
  the only detection; a multi-day skip leaves valuation/alerts stale.
- **`preDeployCommand` is single-slot** (string or 1-element array). A future second
  pre-deploy command must chain via `sh -c 'a && b'`, not a second array entry.
- **Config-as-code overrides the dashboard and does not write back.** A value in
  `railway.json` wins over a dashboard setting of the same field.

## Cross-references

- DECISIONS.md 2026-06-12 — the deploy kickoff (four forks) and the per-slice records.
- Invariant 6 — HSTS off by default; ramp only after HTTPS is proven on the final host.
- Invariant 10 — `CSRF_TRUSTED_ORIGINS` must be the exact frontend origin (scheme incl.).
- Slice 8 — the empirical XFF spoof test that sets `DJANGO_NUM_PROXIES`, and the HSTS ramp.
