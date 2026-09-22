"""Tests for the CI check-run gate (infra/hetzner/ci_gate.py) behind deploy_poll.sh.

The gate lives under infra/ (outside the backend package, like the host-metrics
collector) and runs on the box's system python3, so it is loaded by path here. The
known-positive input is the real check-runs payload GitHub returned, unauthenticated,
for merge commit 63c480e on 2026-09-21 (tests/fixtures/check_runs_63c480e.json);
every other verdict is a mutation of it. The HTTP path runs against a loopback stub
(tests/github_stub.py), so no test can reach GitHub, and the stub records that the
gate asked and sent no credential.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.github_stub import Response, StubGitHub

GATE_PATH = Path(__file__).resolve().parents[2] / "infra" / "hetzner" / "ci_gate.py"
POLLER_PATH = GATE_PATH.with_name("deploy_poll.sh")
WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "check_runs_63c480e.json"
REPO = "erick-ti/millennium"
SHA = "63c480eddf63b6de41b8b24e1a88f8095fd3f18c"
REQUIRED = [
    "scan for secrets",
    "pytest (postgres)",
    "lint + build",
    "e2e (smoke)",
    "backend image",
    "frontend image",
]


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_gate", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()
CAPTURED: dict[str, Any] = json.loads(FIXTURE_PATH.read_text())


def _runs() -> list[dict[str, Any]]:
    return copy.deepcopy(CAPTURED["check_runs"])


def _find(runs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(run for run in runs if run["name"] == name)


def _copy_of(runs: list[dict[str, Any]], name: str, id_delta: int, **fields: Any) -> dict[str, Any]:
    run = copy.deepcopy(_find(runs, name))
    run["id"] += id_delta
    run.update(fields)
    return run


# ── evaluate(): the pure verdict ─────────────────────────────────────────────


def test_captured_merge_commit_is_ready() -> None:
    assert gate.evaluate(_runs(), REQUIRED, SHA) == ("ready", "6/6 success")


def test_captured_payload_carries_same_name_duplicates_and_extra_runs() -> None:
    # The fixture must keep exercising the "extra runs are ignored" path: CodeQL and
    # Dependabot runs (three of the latter, across three check suites) sit beside the
    # six required ones.
    names = [run["name"] for run in CAPTURED["check_runs"]]
    assert names.count("Dependabot") == 3
    assert len(CAPTURED["check_runs"]) == 12
    assert CAPTURED["total_count"] == 12


def test_missing_required_check_is_pending() -> None:
    runs = [run for run in _runs() if run["name"] != "e2e (smoke)"]
    assert gate.evaluate(runs, REQUIRED, SHA) == ("pending", "5/6 missing=[e2e (smoke)]")


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_non_completed_required_check_is_pending(status: str) -> None:
    runs = _runs()
    _find(runs, "frontend image").update(status=status, conclusion=None)
    assert gate.evaluate(runs, REQUIRED, SHA) == ("pending", "5/6 running=[frontend image]")


def test_newer_queued_rerun_blocks_despite_older_success() -> None:
    runs = _runs()
    runs.append(_copy_of(runs, "pytest (postgres)", +1, status="queued", conclusion=None))
    assert gate.evaluate(runs, REQUIRED, SHA) == ("pending", "5/6 running=[pytest (postgres)]")


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "skipped", "neutral", "cancelled", "timed_out", "action_required", "stale", None],
)
def test_any_non_success_conclusion_fails(conclusion: str | None) -> None:
    runs = _runs()
    _find(runs, "pytest (postgres)")["conclusion"] = conclusion
    assert gate.evaluate(runs, REQUIRED, SHA) == (
        "failed",
        f"5/6 failed=[pytest (postgres)={conclusion}]",
    )


def test_newest_completed_run_by_id_decides() -> None:
    older_failure = _runs()
    older_failure.append(_copy_of(older_failure, "lint + build", -1, conclusion="failure"))
    assert gate.evaluate(older_failure, REQUIRED, SHA) == ("ready", "6/6 success")

    newer_failure = _runs()
    newer_failure.append(_copy_of(newer_failure, "lint + build", +1, conclusion="failure"))
    assert gate.evaluate(newer_failure, REQUIRED, SHA) == (
        "failed",
        "5/6 failed=[lint + build=failure]",
    )


def test_failed_outranks_pending() -> None:
    runs = [run for run in _runs() if run["name"] != "e2e (smoke)"]
    _find(runs, "backend image")["conclusion"] = "failure"
    verdict, detail = gate.evaluate(runs, REQUIRED, SHA)
    assert verdict == "failed"
    assert detail == "4/6 failed=[backend image=failure]"


def test_runs_from_other_apps_or_other_commits_do_not_count() -> None:
    other_app = _runs()
    _find(other_app, "scan for secrets")["app"]["slug"] = "some-other-app"
    assert gate.evaluate(other_app, REQUIRED, SHA) == ("pending", "5/6 missing=[scan for secrets]")

    other_sha = _runs()
    _find(other_sha, "scan for secrets")["head_sha"] = "0" * 40
    assert gate.evaluate(other_sha, REQUIRED, SHA) == ("pending", "5/6 missing=[scan for secrets]")

    # Neither can a foreign run FAIL a name it does not own.
    foreign_failure = _runs()
    foreign_failure.append(
        _copy_of(foreign_failure, "scan for secrets", +1, conclusion="failure", head_sha="1" * 40)
    )
    assert gate.evaluate(foreign_failure, REQUIRED, SHA) == ("ready", "6/6 success")


def test_extra_check_runs_are_ignored_even_when_red() -> None:
    runs = _runs()
    runs.append(_copy_of(runs, "Analyze (python)", +1, conclusion="failure"))
    assert gate.evaluate(runs, REQUIRED, SHA) == ("ready", "6/6 success")


def test_empty_or_blank_required_list_is_refused() -> None:
    with pytest.raises(ValueError):
        gate.evaluate(_runs(), [], SHA)
    with pytest.raises(ValueError):
        gate.evaluate(_runs(), ["scan for secrets", "  "], SHA)


def test_duplicate_required_names_count_once() -> None:
    assert gate.evaluate(_runs(), [*REQUIRED, "scan for secrets"], SHA) == ("ready", "6/6 success")


def test_source_parses_under_the_python_38_grammar() -> None:
    # The box runs the Ubuntu system python3, not the project's 3.13 venv. Only the
    # grammar is provable here (execution is on the venv interpreter).
    ast.parse(GATE_PATH.read_text(), feature_version=(3, 8))


# ── the HTTP path, against the loopback stub ─────────────────────────────────


@pytest.fixture
def stub() -> Iterator[StubGitHub]:
    server = StubGitHub().start()
    try:
        yield server
    finally:
        server.close()


def _cli(
    stub: StubGitHub, *extra: str, sha: str = SHA, required: list[str] = REQUIRED
) -> list[str]:
    args = ["--repo", REPO, "--sha", sha, "--api-url", stub.url, "--timeout", "5", *extra]
    for name in required:
        args += ["--require", name]
    return args


def _payload(runs: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {"total_count": len(runs) if total is None else total, "check_runs": runs}


def _path(stub: StubGitHub) -> str:
    return stub.check_runs_path(REPO, SHA)


def test_cli_ready_asks_once_anonymously(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    stub.enqueue(_path(stub), stub.json_response(CAPTURED))
    assert gate.main(_cli(stub)) == 0
    assert capsys.readouterr().out == "ready 6/6 success\n"
    assert len(stub.requests) == 1
    request = stub.requests[0]
    assert request.path == _path(stub) + "?per_page=100&filter=latest"
    assert "authorization" not in request.headers
    assert request.headers["user-agent"] == "millennium-deploy-poll"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"


def test_cli_pending_and_failed_exit_codes(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    pending = _payload([run for run in _runs() if run["name"] != "e2e (smoke)"])
    stub.enqueue(_path(stub), stub.json_response(pending))
    assert gate.main(_cli(stub)) == 2
    assert capsys.readouterr().out == "pending 5/6 missing=[e2e (smoke)]\n"

    failed_runs = _runs()
    _find(failed_runs, "backend image")["conclusion"] = "failure"
    stub.enqueue(_path(stub), stub.json_response(_payload(failed_runs)))
    assert gate.main(_cli(stub)) == 3
    assert capsys.readouterr().out == "failed 5/6 failed=[backend image=failure]\n"


def test_cli_http_error_is_unknown(stub: StubGitHub, capsys: pytest.CaptureFixture[str]) -> None:
    stub.enqueue(_path(stub), Response(500, b"boom"))
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown HTTP 500\n"


def test_cli_rate_limit_reports_retry_at(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    before = int(time.time())
    stub.enqueue(_path(stub), Response(403, b"", {"Retry-After": "30"}))
    assert gate.main(_cli(stub)) == 4
    out = capsys.readouterr().out
    assert out.startswith("unknown HTTP 403 rate limited retry_at=")
    retry_at = int(out.rsplit("=", 1)[1])
    assert before + 30 <= retry_at <= int(time.time()) + 30

    reset = before + 1234
    stub.enqueue(
        _path(stub),
        Response(429, b"", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}),
    )
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == f"unknown HTTP 429 rate limited retry_at={reset}\n"

    # A 403 without rate-limit headers (e.g. the repo went private) still backs off a
    # minute, but is not called a rate limit.
    stub.enqueue(_path(stub), Response(403, b""))
    assert gate.main(_cli(stub)) == 4
    out = capsys.readouterr().out
    assert out.startswith("unknown HTTP 403 retry_at=")
    assert int(out.rsplit("=", 1)[1]) >= before + 60


def test_cli_malformed_answers_are_unknown(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    stub.enqueue(_path(stub), Response(200, b"{not json"))
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out.startswith("unknown malformed JSON on page 1")

    stub.enqueue(_path(stub), stub.json_response({"check_runs": "nope"}))
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown unexpected response shape on page 1\n"

    bad_run = _runs()
    bad_run[0]["id"] = "not-an-int"
    stub.enqueue(_path(stub), stub.json_response(_payload(bad_run)))
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown malformed check run on page 1\n"


def test_cli_follows_one_next_page_but_not_two(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _runs()
    first, second = runs[:6], runs[6:]
    next_link = f'<{stub.url}{_path(stub)}?per_page=100&filter=latest&page=2>; rel="next"'
    stub.enqueue(
        _path(stub),
        stub.json_response(_payload(first, total=12), headers={"Link": next_link}),
        stub.json_response(_payload(second, total=12)),
    )
    assert gate.main(_cli(stub)) == 0
    assert capsys.readouterr().out == "ready 6/6 success\n"
    assert [r.path for r in stub.requests] == [
        _path(stub) + "?per_page=100&filter=latest",
        _path(stub) + "?per_page=100&filter=latest&page=2",
    ]

    stub.requests.clear()
    stub.responses.clear()
    stub.enqueue(
        _path(stub),
        stub.json_response(_payload(first, total=12), headers={"Link": next_link}),
        stub.json_response(_payload(second, total=12), headers={"Link": next_link}),
    )
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown more than 2 pages of check runs\n"
    assert len(stub.requests) == 2


def test_cli_partial_answers_are_unknown(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _runs()
    next_link = f'<{stub.url}{_path(stub)}?per_page=100&filter=latest&page=2>; rel="next"'
    stub.enqueue(
        _path(stub),
        stub.json_response(_payload(runs[:6], total=12), headers={"Link": next_link}),
        Response(502, b"bad gateway"),
    )
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown HTTP 502\n"

    stub.responses.clear()
    stub.enqueue(_path(stub), stub.json_response(_payload(runs[:6], total=12)))
    assert gate.main(_cli(stub)) == 4
    assert (
        capsys.readouterr().out
        == "unknown inconsistent response: 6 check runs collected, total_count 12\n"
    )

    stub.responses.clear()
    off_base = '<http://127.0.0.1:9/elsewhere?page=2>; rel="next"'
    stub.enqueue(
        _path(stub), stub.json_response(_payload(runs[:6], total=12), headers={"Link": off_base})
    )
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown next-page link points outside the API base\n"


def test_cli_unreachable_api_is_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    server = StubGitHub().start()
    url = server.url
    server.close()  # the port is now closed: connection refused, never a verdict
    args = ["--repo", REPO, "--sha", SHA, "--api-url", url, "--timeout", "2"]
    for name in REQUIRED:
        args += ["--require", name]
    assert gate.main(args) == 4
    assert capsys.readouterr().out.startswith("unknown request failed:")


def test_cli_config_errors_exit_5_and_never_ask(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gate.main(_cli(stub, required=[])) == 5
    assert "config-error" in capsys.readouterr().out
    assert gate.main(_cli(stub, required=["scan for secrets", "   "])) == 5
    assert "config-error" in capsys.readouterr().out
    assert gate.main(_cli(stub, sha="63c480e")) == 5
    assert "config-error" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        gate.main(["--bogus"])
    assert exc.value.code == 5
    assert capsys.readouterr().out.startswith("config-error")
    assert stub.requests == []


def test_cli_honors_github_api_url_env(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_API_URL", stub.url)
    stub.enqueue(_path(stub), stub.json_response(CAPTURED))
    args = ["--repo", REPO, "--sha", SHA, "--timeout", "5"]
    for name in REQUIRED:
        args += ["--require", name]
    assert gate.main(args) == 0
    assert capsys.readouterr().out == "ready 6/6 success\n"
    assert len(stub.requests) == 1


def test_script_entrypoint_runs_as_a_subprocess(stub: StubGitHub) -> None:
    # deploy_poll.sh invokes the file with the box's python3; the shebang/main wiring
    # must produce the same line + exit code as the in-process call.
    stub.enqueue(_path(stub), stub.json_response(CAPTURED))
    result = subprocess.run(
        [sys.executable, str(GATE_PATH), *_cli(stub)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "GITHUB_API_URL": "http://127.0.0.1:9"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ready 6/6 success\n"
    assert result.stderr == ""


# -- the required list is a contract shared with the poller and the workflows -----


def test_default_required_checks_match_the_poller_and_the_workflow_job_names() -> None:
    # The six names live in the poller's default, this file, and the ruleset. A renamed
    # job would otherwise surface as a 30-minute production alert after the merge;
    # catch it here, at PR time. Job-level ``name:`` lines sit at four spaces.
    match = re.search(
        r'^REQUIRED_CHECKS="\$\{DEPLOY_REQUIRED_CHECKS-(.*?)\}"$', POLLER_PATH.read_text(), re.M
    )
    assert match is not None, "the poller's default REQUIRED_CHECKS line was not found"
    assert match.group(1).split(",") == REQUIRED

    job_names: set[str] = set()
    for workflow in WORKFLOWS_DIR.glob("*.yml"):
        job_names.update(re.findall(r"^    name: (.+?)\s*$", workflow.read_text(), re.M))
    missing = [name for name in REQUIRED if name not in job_names]
    assert not missing, f"required checks without a matching workflow job name: {missing}"


# -- completeness: an inconsistent answer is never ready ---------------------------


def test_cli_inconsistent_counts_are_unknown(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _runs()
    for bad_total in (None, "13", True):
        stub.enqueue(
            _path(stub), stub.json_response({"total_count": bad_total, "check_runs": runs})
        )
        assert gate.main(_cli(stub)) == 4
        assert capsys.readouterr().out == "unknown missing or non-integer total_count on page 1\n"

    stub.enqueue(_path(stub), stub.json_response({"check_runs": runs}))
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown missing or non-integer total_count on page 1\n"

    # Fewer runs than claimed (already covered) and MORE runs than claimed are both
    # inconsistent answers.
    stub.enqueue(_path(stub), stub.json_response(_payload(runs, total=6)))
    assert gate.main(_cli(stub)) == 4
    assert (
        capsys.readouterr().out
        == "unknown inconsistent response: 12 check runs collected, total_count 6\n"
    )


def test_cli_repeated_or_shifting_pages_are_unknown(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _runs()
    six = [run for run in runs if run["name"] in REQUIRED]
    next_link = f'<{stub.url}{_path(stub)}?per_page=100&filter=latest&page=2>; rel="next"'
    # Page 2 repeats page 1: the six unseen runs could hide a queued re-run.
    stub.enqueue(
        _path(stub),
        stub.json_response(_payload(six, total=12), headers={"Link": next_link}),
        stub.json_response(_payload(six, total=12)),
    )
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown duplicate check run ids across pages\n"

    stub.responses.clear()
    others = [run for run in runs if run["name"] not in REQUIRED]
    stub.enqueue(
        _path(stub),
        stub.json_response(_payload(six, total=12), headers={"Link": next_link}),
        stub.json_response(_payload(others, total=13)),
    )
    assert gate.main(_cli(stub)) == 4
    assert capsys.readouterr().out == "unknown total_count changed between pages (12 then 13)\n"


def test_cli_backoff_is_capped_at_an_hour(
    stub: StubGitHub, capsys: pytest.CaptureFixture[str]
) -> None:
    before = int(time.time())
    stub.enqueue(_path(stub), Response(429, b"", {"Retry-After": "999999"}))
    assert gate.main(_cli(stub)) == 4
    retry_at = int(capsys.readouterr().out.rsplit("=", 1)[1])
    assert before + 3600 <= retry_at <= int(time.time()) + 3600

    stub.enqueue(
        _path(stub),
        Response(
            403, b"", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(before + 86400)}
        ),
    )
    assert gate.main(_cli(stub)) == 4
    retry_at = int(capsys.readouterr().out.rsplit("=", 1)[1])
    assert retry_at <= int(time.time()) + 3600


def test_proxy_environment_blackholes_every_other_host() -> None:
    # Positive control for the poller test's isolation: with the proxy variables set
    # the way test_deploy_poll.py sets them, a request to any non-loopback host goes to
    # the closed port and is refused, never resolved. A subprocess, because urllib
    # builds its default opener (and reads the proxy variables) once per process.
    env = {
        "PATH": "/usr/bin:/bin",
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env[name] = "http://127.0.0.1:9"
    args = ["--repo", REPO, "--sha", SHA, "--api-url", "http://example.invalid", "--timeout", "2"]
    for name in REQUIRED:
        args += ["--require", name]
    result = subprocess.run(
        [sys.executable, str(GATE_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )
    assert result.returncode == 4, result.stderr
    assert result.stdout.startswith("unknown request failed:")
    assert "refused" in result.stdout.lower()
