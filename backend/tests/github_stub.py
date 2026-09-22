"""A loopback stand-in for GitHub's check-runs API, shared by the ci_gate and
deploy_poll tests.

Serves canned responses per path (a queue whose last entry stays sticky, so a test
can script "pending, then ready" across ticks) and records every request with its
headers, so a test can assert the gate actually asked (a positive control) and never
sent a credential. Binds 127.0.0.1 on an ephemeral port; nothing leaves the host.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class RecordedRequest:
    path: str
    headers: dict[str, str]


class StubGitHub:
    def __init__(self) -> None:
        self.responses: dict[str, list[Response]] = {}
        self.requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        self._served_sticky: set[str] = set()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                with stub._lock:
                    stub.requests.append(
                        RecordedRequest(self.path, {k.lower(): v for k, v in self.headers.items()})
                    )
                    queue = stub.responses.get(path)
                    if not queue:
                        response = None
                    elif len(queue) > 1:
                        response = queue.pop(0)
                    else:
                        response = queue[0]
                        stub._served_sticky.add(path)
                if response is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(response.status)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()
                self.wfile.write(response.body)

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> StubGitHub:
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        assert isinstance(host, str)
        return f"http://{host}:{port}"

    @staticmethod
    def check_runs_path(repo: str, sha: str) -> str:
        return f"/repos/{repo}/commits/{sha}/check-runs"

    def enqueue(self, path: str, *responses: Response) -> None:
        """Queue responses for a path; the last one stays sticky across requests. A
        sticky response that has already been served is replaced, not re-queued."""
        with self._lock:
            queue = self.responses.setdefault(path, [])
            if path in self._served_sticky:
                queue.clear()
                self._served_sticky.discard(path)
            queue.extend(responses)

    @staticmethod
    def json_response(
        payload: Any, status: int = 200, headers: dict[str, str] | None = None
    ) -> Response:
        return Response(status, json.dumps(payload).encode("utf-8"), dict(headers or {}))
