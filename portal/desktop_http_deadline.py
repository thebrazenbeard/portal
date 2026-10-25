"""Private numeric-loopback HTTP connection with a total monotonic deadline.

Both model discovery and inference use this transport; a slow-drip peer may
not extend the selected request timeout by sending intermittent bytes.
"""

from __future__ import annotations

import http.client
import io
import time


def _remaining_http_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("local model request exceeded its total deadline")
    return remaining


class _DeadlineBodyReader(io.RawIOBase):
    """Preserve socket-file ownership while enforcing one deadline per recv."""

    def __init__(self, sock, deadline: float) -> None:
        super().__init__()
        self._sock = sock
        self._deadline = deadline
        # This native file reference must survive an HTTP/1.0 close after
        # headers while http.client is still reading the response body.
        self._raw = sock.makefile("rb", buffering=0)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        self._sock.settimeout(_remaining_http_time(self._deadline))
        return self._raw.readinto(buffer)

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, deadline: float) -> None:
        self._sock = sock
        self._deadline = deadline

    def sendall(self, data) -> None:
        self._sock.settimeout(_remaining_http_time(self._deadline))
        self._sock.sendall(data)

    def makefile(
        self, mode: str = "r", buffering: int | None = None, *,
        encoding=None, errors=None, newline=None,
    ):
        # Match the socket.makefile call shape while remaining binary/read-only.
        # http.client currently calls makefile("rb"); explicit buffering also
        # must not cause an accidental TypeError or skip the shared deadline.
        if mode != "rb" or any(v is not None for v in (encoding, errors, newline)):
            raise ValueError("only HTTP response binary reads are supported")
        if buffering is not None and (type(buffering) is not int or buffering < -1):
            raise ValueError("invalid HTTP response buffer size")
        reader = _DeadlineBodyReader(self._sock, self._deadline)
        if buffering == 0:
            return reader
        if buffering in (None, -1):
            return io.BufferedReader(reader)
        return io.BufferedReader(reader, buffer_size=buffering)

    def close(self) -> None:
        self._sock.close()


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    """Bound connect, request, headers and body to one monotonic deadline."""

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._deadline = time.monotonic() + timeout

    def connect(self) -> None:
        self.timeout = _remaining_http_time(self._deadline)
        super().connect()
        assert self.sock is not None
        self.sock = _DeadlineSocket(self.sock, self._deadline)
