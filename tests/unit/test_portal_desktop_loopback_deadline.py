"""Real socket regressions for the resident local-model HTTP boundary."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from portal import desktop_cognition
from portal.desktop_runtime import _post_loopback_json


_MODEL = "vera-local:latest"
_BODY = json.dumps({"model": _MODEL, "done": True, "response": "hello"}).encode()


@contextmanager
def _server(*, mode: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            # Emptying the request body avoids artificial connection resets on
            # Windows that obscure the HTTP response lifetime under test.
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                if mode == "slow-headers":
                    header = (
                        f"HTTP/1.0 200 OK\r\nContent-Length: {len(_BODY)}\r\n\r\n"
                    ).encode()
                    for byte in header:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.025)
                    self.wfile.write(_BODY)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(_BODY)))
                    self.end_headers()
                    for byte in _BODY:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        if mode == "slow-body":
                            time.sleep(0.025)
            except (BrokenPipeError, ConnectionResetError):
                # The client is expected to close a timed-out request.
                pass

        def do_GET(self):
            self.do_POST()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join(timeout=2)


def _request(port: int, timeout: float) -> dict[str, object]:
    return _post_loopback_json(
        port=port, path="/api/generate", model=_MODEL, task="hello",
        timeout_seconds=timeout,
        payload={"model": _MODEL, "prompt": "hello", "stream": False},
    )


@pytest.mark.parametrize("mode", ["slow-body", "slow-headers"])
def test_loopback_timeout_is_one_total_deadline_not_each_socket_read(mode):
    with _server(mode=mode) as port:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            _request(port, 0.16)
        assert time.monotonic() - start < 0.9


def test_http10_connection_close_retains_response_body_through_read():
    with _server(mode="fast") as port:
        assert _request(port, 2.0)["response"] == "hello"


@pytest.mark.parametrize("mode", ["slow-body", "slow-headers"])
def test_route_discovery_also_has_absolute_deadline(monkeypatch, mode):
    # Discovery happens before model selection, so it cannot stall even when
    # the actual inference request obeys its own total deadline.
    monkeypatch.setattr(desktop_cognition, "_DISCOVERY_TOTAL_TIMEOUT_SECONDS", 0.16,
                        raising=False)
    with _server(mode=mode) as port:
        start = time.monotonic()
        assert desktop_cognition._local_discovery_json(port, "/api/tags") is None
        assert time.monotonic() - start < 0.9
