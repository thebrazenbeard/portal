from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .budgets import BudgetEnvelope
from .leases import Lease


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def _managed_connection(path: Path):
    conn = _connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class StoredBudget:
    envelope: BudgetEnvelope
    generation: int


class SQLiteLineageBudgetStore:
    """Durable lineage-wide budget ledger with generation CAS."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _managed_connection(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lineage_budget (
                    lineage_id TEXT PRIMARY KEY,
                    max_depth INTEGER NOT NULL,
                    depth INTEGER NOT NULL,
                    remaining_children INTEGER NOT NULL,
                    remaining_active INTEGER NOT NULL,
                    remaining_retries INTEGER NOT NULL,
                    remaining_backend_jobs INTEGER NOT NULL,
                    generation INTEGER NOT NULL
                )
                """
            )

    def create(self, envelope: BudgetEnvelope) -> StoredBudget:
        if envelope.scope_id != "root":
            raise ValueError(
                "lineage-wide budget store accepts only the root budget scope"
            )
        with _managed_connection(self.path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO lineage_budget (
                        lineage_id, max_depth, depth, remaining_children,
                        remaining_active, remaining_retries,
                        remaining_backend_jobs, generation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        envelope.lineage_id,
                        envelope.max_depth,
                        envelope.depth,
                        envelope.remaining_children,
                        envelope.remaining_active,
                        envelope.remaining_retries,
                        envelope.remaining_backend_jobs,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("lineage budget already exists") from exc
        return StoredBudget(envelope=envelope, generation=0)

    def get(self, lineage_id: str) -> StoredBudget | None:
        with _managed_connection(self.path) as conn:
            row = conn.execute(
                """
                SELECT max_depth, depth, remaining_children, remaining_active,
                       remaining_retries, remaining_backend_jobs, generation
                FROM lineage_budget WHERE lineage_id = ?
                """,
                (lineage_id,),
            ).fetchone()
        if row is None:
            return None
        return _stored_budget(lineage_id, row)

    def reserve(
        self,
        lineage_id: str,
        *,
        expected_generation: int,
        depth: int,
        children: int,
        active: int,
        retries: int,
        backend_jobs: int,
    ) -> StoredBudget:
        requested = {
            "children": children,
            "active": active,
            "retries": retries,
            "backend jobs": backend_jobs,
        }
        if depth < 0 or any(value < 0 for value in requested.values()):
            raise ValueError("budget reservation values must be non-negative")

        conn = _connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT max_depth, depth, remaining_children, remaining_active,
                       remaining_retries, remaining_backend_jobs, generation
                FROM lineage_budget WHERE lineage_id = ?
                """,
                (lineage_id,),
            ).fetchone()
            if row is None:
                raise ValueError("lineage budget not found")
            stored = _stored_budget(lineage_id, row)
            if stored.generation != expected_generation:
                raise ValueError(
                    f"budget generation mismatch: expected {expected_generation}, got {stored.generation}"
                )
            if depth > stored.envelope.max_depth:
                raise ValueError("budget depth exhausted")

            available = {
                "children": stored.envelope.remaining_children,
                "active": stored.envelope.remaining_active,
                "retries": stored.envelope.remaining_retries,
                "backend jobs": stored.envelope.remaining_backend_jobs,
            }
            for name, value in requested.items():
                if value > available[name]:
                    raise ValueError(f"{name} budget exceeded")

            new_generation = stored.generation + 1
            updated = BudgetEnvelope(
                lineage_id=lineage_id,
                max_depth=stored.envelope.max_depth,
                depth=stored.envelope.depth,
                remaining_children=stored.envelope.remaining_children - children,
                remaining_active=stored.envelope.remaining_active - active,
                remaining_retries=stored.envelope.remaining_retries - retries,
                remaining_backend_jobs=stored.envelope.remaining_backend_jobs - backend_jobs,
            )
            cursor = conn.execute(
                """
                UPDATE lineage_budget
                SET remaining_children = ?, remaining_active = ?,
                    remaining_retries = ?, remaining_backend_jobs = ?,
                    generation = ?
                WHERE lineage_id = ? AND generation = ?
                """,
                (
                    updated.remaining_children,
                    updated.remaining_active,
                    updated.remaining_retries,
                    updated.remaining_backend_jobs,
                    new_generation,
                    lineage_id,
                    expected_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("budget generation changed during reservation")
            conn.execute("COMMIT")
            return StoredBudget(updated, new_generation)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            conn.close()


class SQLiteLeaseStore:
    """Durable atomic lease store with monotonic fencing tokens."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _managed_connection(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lease_counter (
                    work_fingerprint TEXT PRIMARY KEY,
                    last_token INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lease_state (
                    work_fingerprint TEXT PRIMARY KEY,
                    holder TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0
                )
                """
            )

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

        conn = _connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT holder, fencing_token, expires_at, completed
                FROM lease_state WHERE work_fingerprint = ?
                """,
                (work_fingerprint,),
            ).fetchone()
            if row is not None:
                _, _, expires_at, completed = row
                if completed:
                    conn.execute("COMMIT")
                    return None
                if now < float(expires_at):
                    conn.execute("COMMIT")
                    return None

            counter = conn.execute(
                "SELECT last_token FROM lease_counter WHERE work_fingerprint = ?",
                (work_fingerprint,),
            ).fetchone()
            token = (int(counter[0]) if counter else 0) + 1
            conn.execute(
                """
                INSERT INTO lease_counter(work_fingerprint, last_token)
                VALUES (?, ?)
                ON CONFLICT(work_fingerprint)
                DO UPDATE SET last_token = excluded.last_token
                """,
                (work_fingerprint, token),
            )
            expires_at = now + ttl
            conn.execute(
                """
                INSERT INTO lease_state(
                    work_fingerprint, holder, fencing_token, expires_at, completed
                ) VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(work_fingerprint) DO UPDATE SET
                    holder = excluded.holder,
                    fencing_token = excluded.fencing_token,
                    expires_at = excluded.expires_at,
                    completed = 0
                """,
                (work_fingerprint, holder, token, expires_at),
            )
            conn.execute("COMMIT")
            return Lease(work_fingerprint, holder, token, expires_at)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            conn.close()

    def heartbeat(self, lease: Lease, *, now: float, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("lease ttl must be positive")
        new_expiry = now + ttl
        with _managed_connection(self.path) as conn:
            cursor = conn.execute(
                """
                UPDATE lease_state
                SET expires_at = ?
                WHERE work_fingerprint = ?
                  AND holder = ?
                  AND fencing_token = ?
                  AND completed = 0
                  AND expires_at > ?
                """,
                (
                    new_expiry,
                    lease.work_fingerprint,
                    lease.holder,
                    lease.fencing_token,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return Lease(lease.work_fingerprint, lease.holder, lease.fencing_token, new_expiry)

    def release(self, lease: Lease, *, now: float) -> bool:
        with _managed_connection(self.path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM lease_state
                WHERE work_fingerprint = ?
                  AND holder = ?
                  AND fencing_token = ?
                  AND completed = 0
                  AND expires_at > ?
                """,
                (lease.work_fingerprint, lease.holder, lease.fencing_token, now),
            )
            return cursor.rowcount == 1

    def complete(self, lease: Lease, *, now: float) -> bool:
        with _managed_connection(self.path) as conn:
            cursor = conn.execute(
                """
                UPDATE lease_state
                SET completed = 1
                WHERE work_fingerprint = ?
                  AND holder = ?
                  AND fencing_token = ?
                  AND completed = 0
                  AND expires_at > ?
                """,
                (lease.work_fingerprint, lease.holder, lease.fencing_token, now),
            )
            return cursor.rowcount == 1


def _stored_budget(lineage_id: str, row: tuple) -> StoredBudget:
    max_depth, depth, children, active, retries, jobs, generation = row
    return StoredBudget(
        envelope=BudgetEnvelope(
            lineage_id=lineage_id,
            max_depth=int(max_depth),
            depth=int(depth),
            remaining_children=int(children),
            remaining_active=int(active),
            remaining_retries=int(retries),
            remaining_backend_jobs=int(jobs),
        ),
        generation=int(generation),
    )
