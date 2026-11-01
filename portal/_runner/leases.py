from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Lock
from typing import Protocol


@dataclass(frozen=True)
class Lease:
    work_fingerprint: str
    holder: str
    fencing_token: int
    expires_at: float


class LeaseStore(Protocol):
    def claim(
        self,
        work_fingerprint: str,
        *,
        holder: str,
        now: float,
        ttl: float,
    ) -> Lease | None: ...

    def heartbeat(
        self,
        lease: Lease,
        *,
        now: float,
        ttl: float,
    ) -> Lease | None: ...

    def release(self, lease: Lease, *, now: float) -> bool: ...

    def complete(self, lease: Lease, *, now: float) -> bool: ...


@dataclass
class _LeaseState:
    lease: Lease
    completed: bool = False


class InMemoryLeaseStore:
    """Thread-safe reference lease store for M4 tests and mock dispatch.

    A production/distributed backend must preserve the same atomic claim and
    fencing semantics using its own compare-and-swap/transaction primitive.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[str, _LeaseState] = {}
        self._last_token: dict[str, int] = {}

    def _next_token(self, fingerprint: str) -> int:
        token = self._last_token.get(fingerprint, 0) + 1
        self._last_token[fingerprint] = token
        return token

    def claim(
        self,
        work_fingerprint: str,
        *,
        holder: str,
        now: float,
        ttl: float,
    ) -> Lease | None:
        if ttl <= 0:
            raise ValueError("lease ttl must be positive")
        if not holder.strip():
            raise ValueError("lease holder is required")
        with self._lock:
            state = self._states.get(work_fingerprint)
            if state is not None:
                if state.completed:
                    return None
                if now < state.lease.expires_at:
                    return None

            lease = Lease(
                work_fingerprint=work_fingerprint,
                holder=holder,
                fencing_token=self._next_token(work_fingerprint),
                expires_at=now + ttl,
            )
            self._states[work_fingerprint] = _LeaseState(lease=lease)
            return lease

    def heartbeat(
        self,
        lease: Lease,
        *,
        now: float,
        ttl: float,
    ) -> Lease | None:
        if ttl <= 0:
            raise ValueError("lease ttl must be positive")
        with self._lock:
            state = self._states.get(lease.work_fingerprint)
            if not self._matches_current(state, lease):
                return None
            assert state is not None
            if state.completed or now >= state.lease.expires_at:
                return None
            updated = replace(state.lease, expires_at=now + ttl)
            state.lease = updated
            return updated

    def release(self, lease: Lease, *, now: float) -> bool:
        with self._lock:
            state = self._states.get(lease.work_fingerprint)
            if not self._matches_current(state, lease):
                return False
            assert state is not None
            if state.completed or now >= state.lease.expires_at:
                return False
            del self._states[lease.work_fingerprint]
            return True

    def complete(self, lease: Lease, *, now: float) -> bool:
        with self._lock:
            state = self._states.get(lease.work_fingerprint)
            if not self._matches_current(state, lease):
                return False
            assert state is not None
            if state.completed or now >= state.lease.expires_at:
                return False
            state.completed = True
            return True

    @staticmethod
    def _matches_current(state: _LeaseState | None, lease: Lease) -> bool:
        if state is None:
            return False
        current = state.lease
        return (
            current.work_fingerprint == lease.work_fingerprint
            and current.holder == lease.holder
            and current.fencing_token == lease.fencing_token
        )
