from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .budgets import BudgetEnvelope
from .leases import Lease


_SCHEMA = """
CREATE TABLE IF NOT EXISTS lineage_budgets (
    lineage_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    max_depth INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    remaining_children INTEGER NOT NULL,
    remaining_active INTEGER NOT NULL,
    remaining_retries INTEGER NOT NULL,
    remaining_backend_jobs INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    PRIMARY KEY (lineage_id, scope_id)
);

CREATE TABLE IF NOT EXISTS leases (
    work_fingerprint TEXT PRIMARY KEY,
    holder TEXT,
    fencing_token INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0
);
"""


class _SqliteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(_SCHEMA)
        _migrate_budget_scope_schema(self.connection)

    @contextmanager
    def _write_transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def _migrate_budget_scope_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(lineage_budgets)")
    }
    if "scope_id" in columns:
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "ALTER TABLE lineage_budgets RENAME TO lineage_budgets_legacy_scope"
        )
        connection.execute(
            """
            CREATE TABLE lineage_budgets (
                lineage_id TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                max_depth INTEGER NOT NULL,
                depth INTEGER NOT NULL,
                remaining_children INTEGER NOT NULL,
                remaining_active INTEGER NOT NULL,
                remaining_retries INTEGER NOT NULL,
                remaining_backend_jobs INTEGER NOT NULL,
                generation INTEGER NOT NULL,
                PRIMARY KEY (lineage_id, scope_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO lineage_budgets (
                lineage_id, scope_id, max_depth, depth, remaining_children,
                remaining_active, remaining_retries, remaining_backend_jobs,
                generation
            )
            SELECT
                lineage_id, 'root', max_depth, depth, remaining_children,
                remaining_active, remaining_retries, remaining_backend_jobs,
                generation
            FROM lineage_budgets_legacy_scope
            """
        )
        connection.execute("DROP TABLE lineage_budgets_legacy_scope")
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


class SqliteBudgetStore(_SqliteStore):
    def put_initial(self, budget: BudgetEnvelope) -> int:
        if budget.scope_id != "root":
            raise ValueError(
                "child budget scopes require atomic recursive admission"
            )
        with self._write_transaction():
            try:
                self.connection.execute(
                    """
                    INSERT INTO lineage_budgets (
                        lineage_id, scope_id, max_depth, depth, remaining_children,
                        remaining_active, remaining_retries,
                        remaining_backend_jobs, generation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        budget.lineage_id,
                        budget.scope_id,
                        budget.max_depth,
                        budget.depth,
                        budget.remaining_children,
                        budget.remaining_active,
                        budget.remaining_retries,
                        budget.remaining_backend_jobs,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("budget scope already exists") from exc
        return 1

    def get(
        self,
        lineage_id: str,
        scope_id: str = "root",
    ) -> tuple[BudgetEnvelope, int]:
        row = self.connection.execute(
            """
            SELECT max_depth, depth, remaining_children, remaining_active,
                   remaining_retries, remaining_backend_jobs, generation
            FROM lineage_budgets
            WHERE lineage_id = ? AND scope_id = ?
            """,
            (lineage_id, scope_id),
        ).fetchone()
        if row is None:
            raise KeyError((lineage_id, scope_id))
        budget = BudgetEnvelope(
            lineage_id=lineage_id,
            max_depth=int(row[0]),
            depth=int(row[1]),
            remaining_children=int(row[2]),
            remaining_active=int(row[3]),
            remaining_retries=int(row[4]),
            remaining_backend_jobs=int(row[5]),
            scope_id=scope_id,
        )
        return budget, int(row[6])

    def compare_and_swap(self, budget: BudgetEnvelope, *, expected_generation: int) -> int:
        with self._write_transaction():
            cursor = self.connection.execute(
                """
                UPDATE lineage_budgets
                SET max_depth = ?, depth = ?, remaining_children = ?,
                    remaining_active = ?, remaining_retries = ?,
                    remaining_backend_jobs = ?, generation = generation + 1
                WHERE lineage_id = ? AND scope_id = ? AND generation = ?
                """,
                (
                    budget.max_depth,
                    budget.depth,
                    budget.remaining_children,
                    budget.remaining_active,
                    budget.remaining_retries,
                    budget.remaining_backend_jobs,
                    budget.lineage_id,
                    budget.scope_id,
                    expected_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("budget generation mismatch")
        return expected_generation + 1


class SqliteLeaseStore(_SqliteStore):
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

        with self._write_transaction():
            row = self.connection.execute(
                """
                SELECT holder, fencing_token, expires_at, completed
                FROM leases WHERE work_fingerprint = ?
                """,
                (work_fingerprint,),
            ).fetchone()

            if row is None:
                token = 1
                expires_at = now + ttl
                self.connection.execute(
                    """
                    INSERT INTO leases (
                        work_fingerprint, holder, fencing_token, expires_at, completed
                    ) VALUES (?, ?, ?, ?, 0)
                    """,
                    (work_fingerprint, holder, token, expires_at),
                )
                return Lease(work_fingerprint, holder, token, expires_at)

            current_holder, current_token, current_expires, completed = row
            if bool(completed):
                return None
            if current_holder is not None and now < float(current_expires):
                return None

            token = int(current_token) + 1
            expires_at = now + ttl
            self.connection.execute(
                """
                UPDATE leases
                SET holder = ?, fencing_token = ?, expires_at = ?, completed = 0
                WHERE work_fingerprint = ?
                """,
                (holder, token, expires_at, work_fingerprint),
            )
            return Lease(work_fingerprint, holder, token, expires_at)

    def heartbeat(
        self,
        lease: Lease,
        *,
        now: float,
        ttl: float,
    ) -> Lease | None:
        if ttl <= 0:
            raise ValueError("lease ttl must be positive")
        with self._write_transaction():
            row = self.connection.execute(
                """
                SELECT holder, fencing_token, expires_at, completed
                FROM leases WHERE work_fingerprint = ?
                """,
                (lease.work_fingerprint,),
            ).fetchone()
            if not _row_matches(row, lease) or bool(row[3]) or now >= float(row[2]):
                return None
            expires_at = now + ttl
            self.connection.execute(
                "UPDATE leases SET expires_at = ? WHERE work_fingerprint = ?",
                (expires_at, lease.work_fingerprint),
            )
            return Lease(
                work_fingerprint=lease.work_fingerprint,
                holder=lease.holder,
                fencing_token=lease.fencing_token,
                expires_at=expires_at,
            )

    def release(self, lease: Lease, *, now: float) -> bool:
        with self._write_transaction():
            row = self.connection.execute(
                """
                SELECT holder, fencing_token, expires_at, completed
                FROM leases WHERE work_fingerprint = ?
                """,
                (lease.work_fingerprint,),
            ).fetchone()
            if not _row_matches(row, lease) or bool(row[3]) or now >= float(row[2]):
                return False
            self.connection.execute(
                """
                UPDATE leases SET holder = NULL, expires_at = 0
                WHERE work_fingerprint = ?
                """,
                (lease.work_fingerprint,),
            )
            return True

    def complete(self, lease: Lease, *, now: float) -> bool:
        with self._write_transaction():
            row = self.connection.execute(
                """
                SELECT holder, fencing_token, expires_at, completed
                FROM leases WHERE work_fingerprint = ?
                """,
                (lease.work_fingerprint,),
            ).fetchone()
            if not _row_matches(row, lease) or bool(row[3]) or now >= float(row[2]):
                return False
            self.connection.execute(
                """
                UPDATE leases SET completed = 1
                WHERE work_fingerprint = ?
                """,
                (lease.work_fingerprint,),
            )
            return True


def _row_matches(row, lease: Lease) -> bool:
    if row is None:
        return False
    holder, fencing_token, _expires_at, _completed = row
    return holder == lease.holder and int(fencing_token) == lease.fencing_token
