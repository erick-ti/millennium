#!/usr/bin/env python3
"""Decide whether one commit's required CI checks are green, for deploy_poll.sh.

Runs on the HOST from the deploy checkout (stdlib only, nothing to install; Python
3.8+ syntax so any Ubuntu LTS system python3 can run it) and reads GitHub's public
check-runs API for ONE commit WITHOUT a token: the poller holds no inbound credential
by design and this keeps it that way. A public repository's check runs are readable
anonymously. The anonymous limit is 60 requests per hour per source IP (shared by
everything on the box), so the poller calls this at most once per tick and only
while a candidate commit is undeployed, and backs off until GitHub's reset when
limited.

One stdout line ``<verdict> <detail>`` and an exit code:

  0 ready         every required check's newest run is completed + success
  2 pending       a required check is missing, queued, or running
  3 failed        a required check's newest completed run did not succeed
  4 unknown       the API could not be read completely (rate limit, HTTP error,
                  malformed JSON, more than MAX_PAGES pages, a partial page set);
                  the caller waits, never deploys
  5 config-error  no usable required check names (an empty list would be trivially
                  green), or a malformed repo/sha/argument

Rules, all fail-closed:

* only runs whose ``head_sha`` is the requested commit AND whose app is GitHub
  Actions count (mirrors the protect-main ruleset's integration pin); a check run
  posted by any other app is ignored, so it can neither pass nor fail a name;
* a required name with ANY non-completed run is pending, whatever its older runs
  say: a queued re-run must block, and no timestamp arithmetic is involved;
* among the completed runs of a name the highest ``id`` (the newest; ids are
  monotonic where ``started_at`` is not) decides, and only ``conclusion ==
  "success"`` passes: skipped, neutral, cancelled, timed_out, action_required,
  stale, failure, and null all fail;
* failed outranks pending outranks ready;
* any GitHub Actions run of a required name counts, whichever event triggered it.

Usage::

    ci_gate.py --repo erick-ti/millennium --sha <40 hex> --require <name> [...]

``GITHUB_API_URL`` (default https://api.github.com) overrides the API base, the
same variable gh and Actions use; the tests point it at a loopback stub.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, NoReturn

EXIT_READY = 0
EXIT_PENDING = 2
EXIT_FAILED = 3
EXIT_UNKNOWN = 4
EXIT_CONFIG = 5

DEFAULT_API_URL = "https://api.github.com"
GITHUB_ACTIONS_SLUG = "github-actions"
PER_PAGE = 100
# One request covers a normal merge commit (about a dozen runs); a second page is
# tolerated, more is treated as an unreadable answer (unknown) to bound the per-tick
# request budget.
MAX_PAGES = 2
USER_AGENT = "millennium-deploy-poll"
# A rate-limit answer parks the gate until GitHub's reported reset; cap it so a bogus
# header can never park it longer than an hour (the operator recovery is to delete
# the poller's ci_gate.backoff file).
MAX_BACKOFF_SECONDS = 3600

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')


class GateError(Exception):
    """The API could not be read completely; the verdict is unknown."""


class RateLimited(GateError):
    """GitHub answered 403/429; ``retry_at`` is the epoch second to try again."""

    def __init__(self, detail: str, retry_at: int) -> None:
        super().__init__(detail)
        self.retry_at = retry_at


def evaluate(check_runs: list[dict[str, Any]], required: list[str], sha: str) -> tuple[str, str]:
    """Pure verdict over already-fetched check runs: (verdict, detail).

    ``verdict`` is ``ready``, ``pending``, or ``failed``. Raises ``ValueError`` on an
    empty or blank required list, so no caller can get a trivially green answer.
    """
    names = list(dict.fromkeys(required))  # keep order, drop duplicates
    if not names or any(not name.strip() for name in names):
        raise ValueError("required check names must be a non-empty list of non-blank names")

    runs_by_name: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    for run in check_runs:
        name = run.get("name")
        if name not in runs_by_name or run.get("head_sha") != sha:
            continue
        app = run.get("app")
        if not isinstance(app, dict) or app.get("slug") != GITHUB_ACTIONS_SLUG:
            continue
        runs_by_name[name].append(run)

    missing: list[str] = []
    waiting: list[str] = []
    failed: list[str] = []
    passed: list[str] = []
    for name in names:
        runs = runs_by_name[name]
        if not runs:
            missing.append(name)
            continue
        if any(run.get("status") != "completed" for run in runs):
            waiting.append(name)
            continue
        newest = max(runs, key=lambda run: int(run["id"]))
        if newest.get("conclusion") == "success":
            passed.append(name)
        else:
            failed.append(f"{name}={newest.get('conclusion')}")

    total = len(names)
    if failed:
        return "failed", f"{len(passed)}/{total} failed=[{', '.join(failed)}]"
    if missing or waiting:
        parts = [f"{len(passed)}/{total}"]
        if missing:
            parts.append(f"missing=[{', '.join(missing)}]")
        if waiting:
            parts.append(f"running=[{', '.join(waiting)}]")
        return "pending", " ".join(parts)
    return "ready", f"{len(passed)}/{total} success"


def fetch_check_runs(repo: str, sha: str, api_url: str, timeout: float) -> list[dict[str, Any]]:
    """Return the COMPLETE list of check runs for ``sha``; raise GateError on any gap."""
    base = api_url.rstrip("/")
    url = f"{base}/repos/{repo}/commits/{sha}/check-runs?per_page={PER_PAGE}&filter=latest"
    runs: list[dict[str, Any]] = []
    total: int | None = None
    pages = 0
    while True:
        body, headers = _get(url, timeout)
        pages += 1
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise GateError(f"malformed JSON on page {pages}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("check_runs"), list):
            raise GateError(f"unexpected response shape on page {pages}")
        page_total = data.get("total_count")
        if not isinstance(page_total, int) or isinstance(page_total, bool):
            raise GateError(f"missing or non-integer total_count on page {pages}")
        if total is not None and page_total != total:
            raise GateError(f"total_count changed between pages ({total} then {page_total})")
        total = page_total
        page_runs = data["check_runs"]
        _validate_runs(page_runs, pages)
        runs.extend(page_runs)
        next_url = _next_link(headers.get("Link") or "")
        if next_url is None:
            break
        if pages >= MAX_PAGES:
            raise GateError(f"more than {MAX_PAGES} pages of check runs")
        if not next_url.startswith(base + "/"):
            raise GateError("next-page link points outside the API base")
        url = next_url
    # Completeness is proven, never assumed: the set must be exactly what GitHub
    # claims (verified 2026-09-21 on a public commit with a re-run job that
    # total_count equals the runs returned under filter=latest, so a re-run cannot
    # make a green commit read as partial) and free of repeats across pages.
    ids = [run["id"] for run in runs]
    if len(set(ids)) != len(ids):
        raise GateError("duplicate check run ids across pages")
    if total != len(runs):
        raise GateError(
            f"inconsistent response: {len(runs)} check runs collected, total_count {total}"
        )
    return runs


def _validate_runs(page_runs: list[Any], page: int) -> None:
    for run in page_runs:
        ok = (
            isinstance(run, dict)
            and isinstance(run.get("id"), int)
            and not isinstance(run.get("id"), bool)
            and isinstance(run.get("name"), str)
            and isinstance(run.get("head_sha"), str)
            and isinstance(run.get("status"), str)
            and (run.get("conclusion") is None or isinstance(run.get("conclusion"), str))
            and (run.get("app") is None or isinstance(run.get("app"), dict))
        )
        if not ok:
            raise GateError(f"malformed check run on page {page}")


def _get(url: str, timeout: float) -> tuple[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8"), response.headers
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            retry_at, limited = _retry_at(exc.headers)
            detail = f"HTTP {exc.code} rate limited" if limited else f"HTTP {exc.code}"
            raise RateLimited(detail, retry_at) from exc
        raise GateError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        raise GateError(f"request failed: {exc}") from exc


def _retry_at(headers: Any, now: int | None = None) -> tuple[int, bool]:
    """(epoch to retry at, capped at MAX_BACKOFF_SECONDS; whether it was a rate limit)."""
    now = int(time.time()) if now is None else now
    cap = now + MAX_BACKOFF_SECONDS
    retry_after = headers.get("Retry-After")
    if retry_after is not None and retry_after.strip().isdigit():
        return min(now + int(retry_after), cap), True
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")
    if (
        remaining is not None
        and remaining.strip() == "0"
        and reset is not None
        and reset.strip().isdigit()
    ):
        return min(int(reset), cap), True
    return now + 60, False


def _next_link(link_header: str) -> str | None:
    for url, rel in _LINK_RE.findall(link_header):
        if rel == "next":
            return str(url)
    return None


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which would read as ``pending``; use 5."""

    def error(self, message: str) -> NoReturn:
        print(f"config-error {message}")
        sys.exit(EXIT_CONFIG)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="ci_gate.py",
        description="Report whether a commit's required GitHub check runs are green.",
    )
    parser.add_argument("--repo", required=True, help="repository as owner/name")
    parser.add_argument("--sha", required=True, help="full 40-hex commit id")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="NAME",
        help="required check-run name (repeat per check)",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="per-request seconds")
    parser.add_argument("--api-url", default=None, help="API base (default: $GITHUB_API_URL)")
    args = parser.parse_args(argv)

    required = list(dict.fromkeys(name.strip() for name in args.require))
    if not required or any(not name for name in required):
        print(
            "config-error no usable required check names (an empty list would be trivially green)"
        )
        return EXIT_CONFIG
    if not _SHA_RE.match(args.sha):
        print("config-error sha must be the full 40-hex commit id")
        return EXIT_CONFIG
    if not _REPO_RE.match(args.repo):
        print("config-error repo must be owner/name")
        return EXIT_CONFIG
    api_url = args.api_url or os.environ.get("GITHUB_API_URL") or DEFAULT_API_URL

    try:
        runs = fetch_check_runs(args.repo, args.sha, api_url, args.timeout)
        verdict, detail = evaluate(runs, required, args.sha)
    except RateLimited as exc:
        print(f"unknown {exc} retry_at={exc.retry_at}")
        return EXIT_UNKNOWN
    except GateError as exc:
        print(f"unknown {exc}")
        return EXIT_UNKNOWN
    except Exception as exc:  # the exit-code contract must hold even on a bug
        print(f"unknown internal error: {exc!r}")
        return EXIT_UNKNOWN

    print(f"{verdict} {detail}")
    return {"ready": EXIT_READY, "pending": EXIT_PENDING, "failed": EXIT_FAILED}[verdict]


if __name__ == "__main__":
    sys.exit(main())
