"""End-to-end tests for the CI check-run gate in infra/hetzner/deploy_poll.sh.

Runs the REAL poller (bash) against fixtures that can never reach the box or GitHub:

* ``REPO_DIR`` is a clone of a temporary bare origin whose committed
  ``infra/hetzner/deploy.sh`` is a stub that only records that it ran (and which
  HEAD it saw), so nothing is ever built or deployed;
* ``curl`` is a PATH shim that records the Healthchecks pings (parsed from the
  ``-K -`` stdin config the poller uses, including the ``/fail`` body) and returns
  a test-controlled probe result, so the alerts are asserted, not assumed;
* ``python3`` is a PATH shim that execs the real interpreter unless told to crash
  or hang, so the poller's handling of a broken gate is covered;
* ``GITHUB_API_URL`` points the gate at a loopback stub (tests/github_stub.py) that
  serves canned check-run payloads and records every request; both proxy variables
  black-hole any other HTTP(S) destination and no token is in the environment.

Linux-only: the poller needs GNU mktemp, flock and timeout (the box is Ubuntu, CI's
ubuntu job runs this; macOS skips it with a reason).
"""

from __future__ import annotations

import base64
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.github_stub import Response, StubGitHub

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="deploy_poll.sh needs Linux (GNU mktemp, flock, timeout)"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
POLLER = REPO_ROOT / "infra" / "hetzner" / "deploy_poll.sh"
FIXTURE_PATH = REPO_ROOT / "backend" / "tests" / "fixtures" / "check_runs_63c480e.json"
GITHUB_REPO = "erick-ti/millennium"
REQUIRED = [
    "scan for secrets",
    "pytest (postgres)",
    "lint + build",
    "e2e (smoke)",
    "backend image",
    "frontend image",
]
HC_URL = "https://hc.invalid/ping/00000000-0000-0000-0000-000000000000"
BLACKHOLE = "http://127.0.0.1:9"

STUB_DEPLOY = """#!/usr/bin/env bash
# Test double for deploy.sh: records the HEAD it was asked to deploy, never builds.
set -euo pipefail
printf '%s skip_pull=%s\\n' "$(git rev-parse HEAD)" "${DEPLOY_SKIP_PULL:-}" >>"$DEPLOY_STATE_DIR/deploy_calls"
if [[ -n "${STUB_ADVANCE_ORIGIN:-}" ]]; then
    # Simulate a merge landing on origin/main while this deploy is in flight.
    git -C "$STUB_ADVANCE_ORIGIN" commit -q --allow-empty -m "concurrent merge"
    git -C "$STUB_ADVANCE_ORIGIN" push -q origin HEAD:main
fi
if [[ -n "${STUB_MOVE_HEAD:-}" ]]; then
    git checkout -q --detach HEAD~1
fi
exit "${STUB_DEPLOY_RC:-0}"
"""

CURL_SHIM = """#!/usr/bin/env bash
# curl test double: never touches the network. Records Healthchecks pings (the
# poller feeds the URL via a `-K -` stdin config) and public-route probes.
set -u
log="$CURL_SHIM_LOG"
if [[ "${1:-}" == "-K" && "${2:-}" == "-" ]]; then
    cfg="$(cat)"
    url="$(printf '%s\\n' "$cfg" | sed -n 's/^url = "\\(.*\\)"$/\\1/p')"
    datafile="$(printf '%s\\n' "$cfg" | sed -n 's/^data = "@\\(.*\\)"$/\\1/p')"
    body=""
    if [[ -n "$datafile" && -f "$datafile" ]]; then body="$(base64 -w0 <"$datafile")"; fi
    printf 'PING %s %s\\n' "$url" "$body" >>"$log"
    exit 0
fi
printf 'PROBE %s\\n' "${@: -1}" >>"$log"
exit "${CURL_SHIM_PROBE_RC:-0}"
"""

PYTHON_SHIM = """#!/usr/bin/env bash
# python3 test double: the real interpreter, or a broken gate on demand.
case "${GATE_SHIM_MODE:-}" in
    crash) echo "Traceback (most recent call last): simulated crash" >&2; exit 1 ;;
    hang) exec sleep 30 ;;
    silent) exit 0 ;;
    garbage) echo "READY 6/6"; exit 0 ;;
    badcode) exit 2 ;;
esac
exec "$REAL_PYTHON" "$@"
"""


class Harness:
    def __init__(self, tmp_path: Path, stub: StubGitHub) -> None:
        # On Linux a missing tool is a broken runner image, not a reason to skip: the
        # required CI job must never go green with zero poller coverage.
        missing = [
            t for t in ("bash", "git", "flock", "timeout", "base64") if shutil.which(t) is None
        ]
        assert not missing, f"deploy_poll.sh needs {missing}; fix the runner image, do not skip"
        self.tmp_path = tmp_path
        self.stub = stub
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.git_env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        self.origin = tmp_path / "origin.git"
        self._git("init", "-q", "--bare", "-b", "main", str(self.origin), cwd=tmp_path)
        self.pusher = tmp_path / "pusher"
        self._git("init", "-q", "-b", "main", str(self.pusher), cwd=tmp_path)
        stub_deploy = self.pusher / "infra" / "hetzner" / "deploy.sh"
        stub_deploy.parent.mkdir(parents=True)
        stub_deploy.write_text(STUB_DEPLOY)
        stub_deploy.chmod(0o755)
        self._git("add", "-A", cwd=self.pusher)
        self._git("commit", "-q", "-m", "base", cwd=self.pusher)
        self._git("remote", "add", "origin", str(self.origin), cwd=self.pusher)
        self._git("push", "-q", "origin", "main", cwd=self.pusher)
        self.base = self.rev_parse("HEAD", cwd=self.pusher)
        self.checkout = tmp_path / "checkout"
        self._git(
            "clone", "-q", "--branch", "main", str(self.origin), str(self.checkout), cwd=tmp_path
        )
        self.write_marker(self.base)

        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in (("curl", CURL_SHIM), ("python3", PYTHON_SHIM)):
            shim = self.bin / name
            shim.write_text(body)
            shim.chmod(0o755)
        self.curl_log = self.state / "curl.log"

    def _git(self, *args: str, cwd: Path) -> str:
        result = subprocess.run(
            ["git", *args], cwd=cwd, env=self.git_env, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    def rev_parse(self, ref: str, cwd: Path | None = None) -> str:
        return self._git("rev-parse", ref, cwd=cwd or self.checkout)

    def push(self, message: str) -> str:
        """Land a new commit on origin/main (the fixture's "merge") and return its sha."""
        self._git("commit", "-q", "--allow-empty", "-m", message, cwd=self.pusher)
        self._git("push", "-q", "origin", "main", cwd=self.pusher)
        return self.rev_parse("HEAD", cwd=self.pusher)

    # ── state files ──
    @property
    def marker_path(self) -> Path:
        return self.state / "last_deployed_sha"

    def write_marker(self, sha: str) -> None:
        self.marker_path.write_text(sha + "\n")

    def marker(self) -> str | None:
        return self.marker_path.read_text().strip() if self.marker_path.exists() else None

    def ci_wait(self) -> str | None:
        path = self.state / "ci_wait"
        return path.read_text().strip() if path.exists() else None

    def gate_status(self) -> str:
        path = self.state / "ci_gate.status"
        return path.read_text().strip() if path.exists() else ""

    def deploy_calls(self) -> list[str]:
        path = self.state / "deploy_calls"
        return path.read_text().splitlines() if path.exists() else []

    def head(self) -> str:
        return self.rev_parse("HEAD")

    # ── curl shim records ──
    def pings(self) -> list[tuple[str, str]]:
        """(suffix, decoded body) per Healthchecks ping, in order; suffix '' = success."""
        out: list[tuple[str, str]] = []
        if not self.curl_log.exists():
            return out
        for line in self.curl_log.read_text().splitlines():
            if not line.startswith("PING "):
                continue
            parts = line.split(" ", 2)
            url = parts[1]
            body_b64 = parts[2].strip() if len(parts) > 2 else ""
            assert url.startswith(HC_URL), url
            decoded = base64.b64decode(body_b64).decode("utf-8") if body_b64 else ""
            out.append((url[len(HC_URL) :], decoded))
        return out

    def probes(self) -> list[str]:
        if not self.curl_log.exists():
            return []
        return [
            line[6:] for line in self.curl_log.read_text().splitlines() if line.startswith("PROBE ")
        ]

    # ── the stub GitHub ──
    def serve(self, sha: str, *responses: Response) -> None:
        self.stub.enqueue(self.stub.check_runs_path(GITHUB_REPO, sha), *responses)

    def payload(
        self,
        sha: str,
        overrides: dict[str, dict[str, Any]] | None = None,
        drop: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        template = json.loads(FIXTURE_PATH.read_text())
        runs = []
        for run in template["check_runs"]:
            if run["name"] in drop:
                continue
            run = copy.deepcopy(run)
            run["head_sha"] = sha
            if overrides and run["name"] in overrides:
                run.update(overrides[run["name"]])
            runs.append(run)
        return {"total_count": len(runs), "check_runs": runs}

    def api_requests_for(self, sha: str) -> list[str]:
        prefix = self.stub.check_runs_path(GITHUB_REPO, sha)
        return [r.path for r in self.stub.requests if r.path.startswith(prefix)]

    # ── running the poller ──
    def run(self, **extra_env: str) -> subprocess.CompletedProcess[str]:
        env = {
            **self.git_env,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "LC_ALL": "C",
            "REPO_DIR": str(self.checkout),
            "DEPLOY_STATE_DIR": str(self.state),
            "DEPLOY_LOCKFILE": str(self.state / "deploy.lock"),
            "DEPLOY_HEALTHCHECK_URL": HC_URL,
            "DEPLOY_PUBLIC_HOST": "millennium.test",
            "DEPLOY_GITHUB_REPO": GITHUB_REPO,
            "DEPLOY_GATE_TIMEOUT": "10",
            "DEPLOY_FETCH_TIMEOUT": "30",
            "DEPLOY_RUN_TIMEOUT": "60",
            "DEPLOY_PROBE_TIMEOUT": "5",
            "GITHUB_API_URL": self.stub.url,
            "CURL_SHIM_LOG": str(self.curl_log),
            "REAL_PYTHON": sys.executable,
            "http_proxy": BLACKHOLE,
            "https_proxy": BLACKHOLE,
            "HTTP_PROXY": BLACKHOLE,
            "HTTPS_PROXY": BLACKHOLE,
            "no_proxy": "127.0.0.1,localhost",
            "NO_PROXY": "127.0.0.1,localhost",
            **extra_env,
        }
        return subprocess.run(
            ["bash", str(POLLER)], env=env, capture_output=True, text=True, check=False, timeout=120
        )


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    stub = StubGitHub().start()
    try:
        yield Harness(tmp_path, stub)
    finally:
        stub.close()


def _assert_untouched(h: Harness) -> None:
    """A blocked candidate must leave the checkout, the marker, and deploy.sh alone."""
    assert h.deploy_calls() == []
    assert h.marker() == h.base
    assert h.head() == h.base


# ── deploys ──────────────────────────────────────────────────────────────────


def test_green_checks_deploy_the_gated_sha_and_record_the_marker(harness: Harness) -> None:
    new = harness.push("green change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run()

    assert result.returncode == 0, result.stderr
    assert harness.deploy_calls() == [f"{new} skip_pull=1"]
    assert harness.marker() == new
    assert harness.head() == new
    assert harness.gate_status().endswith("ready 6/6 success")
    assert harness.ci_wait() is None
    assert [suffix for suffix, _ in harness.pings()] == ["/start", ""]
    assert len(harness.probes()) == 2
    # Positive control: the gate really asked the stub, once, and sent no credential.
    assert harness.api_requests_for(new) == [
        harness.stub.check_runs_path(GITHUB_REPO, new) + "?per_page=100&filter=latest"
    ]
    assert all("authorization" not in r.headers for r in harness.stub.requests)


def test_pending_then_ready_deploys_on_the_later_tick_and_clears_the_wait(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(
        new,
        harness.stub.json_response(
            harness.payload(new, {"e2e (smoke)": {"status": "in_progress", "conclusion": None}})
        ),
        harness.stub.json_response(harness.payload(new)),
    )

    first = harness.run()
    assert first.returncode == 0, first.stderr
    _assert_untouched(harness)
    assert (harness.ci_wait() or "").startswith(new + " ")
    assert "running=[e2e (smoke)]" in harness.gate_status()

    second = harness.run()
    assert second.returncode == 0, second.stderr
    assert harness.deploy_calls() == [f"{new} skip_pull=1"]
    assert harness.marker() == new
    assert harness.ci_wait() is None
    assert [suffix for suffix, _ in harness.pings()] == ["", "/start", ""]


def test_origin_advancing_mid_deploy_does_not_change_the_deployed_sha(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run(STUB_ADVANCE_ORIGIN=str(harness.pusher))

    assert result.returncode == 0, result.stderr
    newer = harness.rev_parse("HEAD", cwd=harness.pusher)
    assert newer != new
    assert harness.deploy_calls() == [f"{new} skip_pull=1"]
    assert harness.marker() == new

    # The next tick gates the newer commit on its own merits (unknown here: the stub
    # has nothing for it), and the marker stays put.
    again = harness.run()
    assert again.returncode == 0, again.stderr
    assert harness.api_requests_for(newer)
    assert harness.marker() == new
    assert harness.deploy_calls() == [f"{new} skip_pull=1"]


# ── blocked: wait ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["running", "absent", "http500"])
def test_not_green_checks_wait_without_deploying(harness: Harness, kind: str) -> None:
    new = harness.push("change")
    if kind == "running":
        response = harness.stub.json_response(
            harness.payload(new, {"frontend image": {"status": "queued", "conclusion": None}})
        )
    elif kind == "absent":
        response = harness.stub.json_response({"total_count": 0, "check_runs": []})
    else:
        response = Response(500, b"boom")
    harness.serve(new, response)

    result = harness.run()

    assert result.returncode == 0, result.stderr
    _assert_untouched(harness)
    assert (harness.ci_wait() or "").startswith(new + " ")
    assert harness.pings() == [("", "")]  # one plain success ping: no /start, no /fail
    assert len(harness.probes()) == 2
    assert len(harness.api_requests_for(new)) == 1  # positive control: the gate asked
    assert "waiting for CI" in result.stderr


@pytest.mark.parametrize("mode", ["crash", "hang", "silent", "garbage", "badcode"])
def test_broken_gate_helper_waits_without_deploying(harness: Harness, mode: str) -> None:
    # crash = rc 1; hang = killed by DEPLOY_GATE_TIMEOUT (rc 124); silent = rc 0 with no
    # verdict; garbage = rc 0 with a wrong verdict word; badcode = rc 2 with no
    # verdict (python's own exit code when it cannot open the script).
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run(GATE_SHIM_MODE=mode, DEPLOY_GATE_TIMEOUT="1")

    assert result.returncode == 0, result.stderr
    _assert_untouched(harness)
    assert (harness.ci_wait() or "").startswith(new + " ")
    status = harness.gate_status()
    assert " unknown gate exit " in status
    if mode in ("silent", "garbage"):
        assert "without a ready verdict" in status
    if mode == "badcode":
        assert "without a pending verdict" in status
    assert harness.pings() == [("", "")]
    assert harness.api_requests_for(new) == []  # the shim never reached the gate


def test_helper_config_error_alerts_without_asking(harness: Harness) -> None:
    # bash passes the empty middle name through; the helper refuses with exit 5.
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run(DEPLOY_REQUIRED_CHECKS="scan for secrets,,lint + build")

    assert result.returncode == 1
    _assert_untouched(harness)
    assert harness.stub.requests == []
    pings = harness.pings()
    assert [suffix for suffix, _ in pings] == ["/fail"]
    assert "config-error" in pings[0][1]
    assert harness.gate_status().split(" ", 2)[2].startswith("config-error ")


def test_expired_backoff_asks_again(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))
    (harness.state / "ci_gate.backoff").write_text(f"{int(time.time()) - 10}\n")

    result = harness.run()

    assert result.returncode == 0, result.stderr
    assert len(harness.api_requests_for(new)) == 1
    assert harness.marker() == new
    assert not (harness.state / "ci_gate.backoff").exists()


def test_deploy_script_failure_alerts_and_keeps_the_marker(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run(STUB_DEPLOY_RC="1")

    assert result.returncode == 1
    assert harness.deploy_calls() == [f"{new} skip_pull=1"]
    assert harness.marker() == harness.base
    pings = harness.pings()
    assert [suffix for suffix, _ in pings] == ["/start", "/fail"]
    assert "step=deploy.sh rc=1" in pings[1][1]


def test_unlisted_required_check_blocks(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run(DEPLOY_REQUIRED_CHECKS=",".join([*REQUIRED, "never reported"]))

    assert result.returncode == 0, result.stderr
    _assert_untouched(harness)
    assert "missing=[never reported]" in harness.gate_status()


def test_candidate_change_restarts_the_wait_clock(harness: Harness) -> None:
    first = harness.push("first")
    harness.serve(first, harness.stub.json_response({"total_count": 0, "check_runs": []}))
    harness.run()
    assert (harness.ci_wait() or "").startswith(first + " ")
    stale_since = int((harness.ci_wait() or "0 0").split()[1]) - 4000
    (harness.state / "ci_wait").write_text(f"{first} {stale_since}\n")

    second = harness.push("second")
    harness.serve(second, harness.stub.json_response({"total_count": 0, "check_runs": []}))
    result = harness.run()

    assert result.returncode == 0, result.stderr  # a fresh candidate is not "stuck"
    _assert_untouched(harness)
    sha, since = (harness.ci_wait() or "  ").split()
    assert sha == second
    assert int(since) > stale_since + 3000


def test_rate_limit_backs_off_until_the_reported_reset(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, Response(429, b"", {"Retry-After": "3600"}))

    first = harness.run()
    assert first.returncode == 0, first.stderr
    _assert_untouched(harness)
    backoff = harness.state / "ci_gate.backoff"
    assert int(backoff.read_text()) > int(time.time()) + 3000
    assert "retry_at=" in harness.gate_status()
    assert len(harness.api_requests_for(new)) == 1

    second = harness.run()
    assert second.returncode == 0, second.stderr
    _assert_untouched(harness)
    assert len(harness.api_requests_for(new)) == 1  # no request while backing off
    assert "backoff" in harness.gate_status()
    assert harness.pings() == [("", ""), ("", "")]


def test_noop_tick_never_asks_github(harness: Harness) -> None:
    result = harness.run()

    assert result.returncode == 0, result.stderr
    assert harness.stub.requests == []
    assert harness.deploy_calls() == []
    assert harness.pings() == [("", "")]
    assert len(harness.probes()) == 2


# ── blocked: alert ───────────────────────────────────────────────────────────


def test_red_check_alerts_and_never_deploys(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(
        new,
        harness.stub.json_response(
            harness.payload(new, {"pytest (postgres)": {"conclusion": "failure"}})
        ),
    )

    result = harness.run()

    assert result.returncode == 1
    _assert_untouched(harness)
    assert "failed=[pytest (postgres)=failure]" in harness.gate_status()
    pings = harness.pings()
    assert [suffix for suffix, _ in pings] == ["/fail"]
    body = pings[0][1]
    assert "step=ci-gate rc=1" in body
    assert "failed=[pytest (postgres)=failure]" in body
    assert harness.ci_wait() is None


def test_blank_required_checks_refuses_before_asking(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run(DEPLOY_REQUIRED_CHECKS="")

    assert result.returncode == 1
    _assert_untouched(harness)
    assert harness.stub.requests == []
    pings = harness.pings()
    assert [suffix for suffix, _ in pings] == ["/fail"]
    assert "DEPLOY_REQUIRED_CHECKS is blank" in pings[0][1]


def test_wait_past_the_window_alerts_without_deploying(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response({"total_count": 0, "check_runs": []}))
    (harness.state / "ci_wait").write_text(f"{new} {int(time.time()) - 4000}\n")

    result = harness.run(DEPLOY_CI_WAIT_MAX="1800")

    assert result.returncode == 1
    _assert_untouched(harness)
    pings = harness.pings()
    assert [suffix for suffix, _ in pings] == ["/fail"]
    body = pings[0][1]
    assert "step=ci-gate rc=1" in body
    assert "still not green" in body
    assert "pending 0/6 missing=[" in body


def test_wait_with_a_failing_public_probe_alerts(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response({"total_count": 0, "check_runs": []}))

    result = harness.run(CURL_SHIM_PROBE_RC="22")

    assert result.returncode != 0
    _assert_untouched(harness)
    pings = harness.pings()
    assert [suffix for suffix, _ in pings] == ["/fail"]
    assert "step=probe-wait" in pings[0][1]


# ── the existing marker semantics survive the gate ───────────────────────────


def test_ready_but_failing_probe_leaves_the_marker_unwritten(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run(CURL_SHIM_PROBE_RC="22")

    assert result.returncode != 0
    assert harness.deploy_calls() == [f"{new} skip_pull=1"]
    assert harness.marker() == harness.base
    pings = harness.pings()
    assert [suffix for suffix, _ in pings] == ["/start", "/fail"]
    assert "step=probe rc=" in pings[1][1]


def test_deploy_that_moves_head_is_not_recorded(harness: Harness) -> None:
    new = harness.push("change")
    harness.serve(new, harness.stub.json_response(harness.payload(new)))

    result = harness.run(STUB_MOVE_HEAD="1")

    assert result.returncode == 1
    assert harness.deploy_calls() == [f"{new} skip_pull=1"]
    assert harness.marker() == harness.base
    pings = harness.pings()
    assert [suffix for suffix, _ in pings] == ["/start", "/fail"]
    assert "step=verify-head" in pings[1][1]
