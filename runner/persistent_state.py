from __future__ import annotations

from contextlib import contextmanager
import hmac
import json
from pathlib import Path
import sqlite3
import unicodedata

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

CREATE TABLE IF NOT EXISTS collision_reservations (
    collision_key TEXT PRIMARY KEY,
    work_fingerprint TEXT,
    holder TEXT,
    fencing_token INTEGER,
    generation INTEGER NOT NULL
);
"""


def _validate_collision_keys(keys) -> tuple[str, ...]:
    if type(keys) not in (tuple, list):
        raise ValueError("collision keys must be an exact sequence")
    if any(type(key) is not str or not key or key != key.strip()
           or any(unicodedata.category(char) in {"Cc", "Cs"} for char in key)
           for key in keys):
        raise ValueError("collision keys require nonempty literal strings without controls or edge whitespace")
    if len(set(keys)) != len(keys):
        raise ValueError("collision keys must not contain duplicates")
    return tuple(keys)


def _table_exists(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _has_durable_attempt(connection, fingerprint):
    return _table_exists(connection, "execution_attempts") and connection.execute(
        "SELECT 1 FROM execution_attempts WHERE work_fingerprint=? LIMIT 1", (fingerprint,)
    ).fetchone() is not None


def _durable_attempt_needs_reconciliation(connection, fingerprint):
    if not _has_durable_attempt(connection, fingerprint):
        return False
    rows = connection.execute(
        """SELECT lineage_id,fencing_token,holder,admitted_at,budget_generation,work_generation,attempt_sha256
           FROM execution_attempts WHERE work_fingerprint=? ORDER BY fencing_token""", (fingerprint,)
    ).fetchall()
    # A newer NO_EFFECT only describes that attempt. It cannot launder an
    # older unresolved effect left by a historical retry policy.
    return any(_attempt_needs_reconciliation(connection, fingerprint, row) for row in rows)


def _attempt_needs_reconciliation(connection, fingerprint, row):
    from .durable_dispatch import _attempt_digest, _reconciliation_digest
    lineage, token, holder, admitted, budget_generation, work_generation, digest = row
    expected = _attempt_digest(lineage_id=lineage, work_fingerprint_value=fingerprint,
        fencing_token=token, holder=holder, admitted_at=float(admitted),
        budget_generation=budget_generation, work_generation=work_generation)
    if not hmac.compare_digest(str(digest), expected):
        raise ValueError("collision attempt journal digest mismatch")
    latest = connection.execute(
        """SELECT outcome,reason,evidence_json,reconciler,observed_at,reconciliation_sha256
           FROM execution_reconciliations WHERE lineage_id=? AND work_fingerprint=? AND fencing_token=?
           ORDER BY sequence DESC LIMIT 1""", (lineage, fingerprint, token)
    ).fetchone()
    if latest is None:
        return True
    outcome, reason, evidence_json, reconciler, observed_at, digest = latest
    try:
        evidence = json.loads(evidence_json)
        if type(evidence) is not list or any(type(item) is not str for item in evidence):
            raise ValueError("invalid evidence")
        expected = _reconciliation_digest(outcome=outcome, reason=reason, evidence=tuple(evidence),
            reconciler=reconciler, observed_at=float(observed_at))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("collision reconciliation evidence is invalid") from exc
    if not hmac.compare_digest(str(digest), expected):
        raise ValueError("collision reconciliation journal digest mismatch")
    # Integrity is not authentication. No independent no-effect verifier is
    # selected here, so even a hash-valid NO_EFFECT remains non-releasable.
    return True


def _collision_work_keys(connection, fingerprint) -> tuple[str, ...]:
    """Read keys from validated immutable work, never from an actor's assertion."""
    if not _table_exists(connection, "recursive_work_state"):
        return ()
    # Lazy import avoids coupling recursive_state's schema construction to ours.
    from .recursive_state import _immutable_digest, _work_from_payload
    from .work_units import WorkUnitStatus, work_unit_fingerprint
    keys = None
    for row in connection.execute(
        """SELECT lineage_id, work_json, budget_scope_id, parent_fingerprint,
                  ancestry_json, effective_capabilities_json, immutable_sha256, status
           FROM recursive_work_state WHERE work_fingerprint=?""", (fingerprint,)
    ).fetchall():
        lineage, work_json, scope, parent, ancestry, capabilities, digest, status = row
        expected = _immutable_digest(work_json=work_json, lineage_id=lineage,
            budget_scope_id=scope, parent_fingerprint=parent,
            ancestry_json=ancestry, effective_capabilities_json=capabilities)
        if not hmac.compare_digest(str(digest), expected):
            raise ValueError("collision work immutable digest mismatch")
        try:
            payload = json.loads(work_json)
            candidate = _validate_collision_keys(payload["collision_keys"])
            work = _work_from_payload(payload, status=WorkUnitStatus(status))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("collision work state is structurally invalid") from exc
        if work_unit_fingerprint(work) != fingerprint:
            raise ValueError("collision work fingerprint mismatch")
        if keys is not None and set(candidate) != set(keys):
            raise ValueError("collision work associations disagree")
        keys = candidate
    return keys or ()


def _validate_collision_ownership(connection, lease: Lease) -> None:
    keys = _collision_work_keys(connection, lease.work_fingerprint)
    for key in keys:
        if not _table_exists(connection, "collision_reservations"):
            raise ValueError("collision ownership is unavailable for legacy work")
        row = connection.execute(
            "SELECT work_fingerprint,holder,fencing_token,generation FROM collision_reservations WHERE collision_key=?", (key,)
        ).fetchone()
        if row is None or row[:3] != (lease.work_fingerprint, lease.holder, lease.fencing_token) or type(row[3]) is not int or row[3] <= 0:
            raise ValueError("collision ownership is missing or stale")
    if _table_exists(connection, "collision_reservations"):
        owned = {row[0] for row in connection.execute(
            "SELECT collision_key FROM collision_reservations WHERE work_fingerprint=?", (lease.work_fingerprint,)
        )}
        if owned != set(keys):
            raise ValueError("collision ownership key set mismatch")


def _reserve_collision_keys(connection, *, work_fingerprint, holder, fencing_token, keys, previous_lease=None):
    keys = _validate_collision_keys(keys)
    # Legacy active rows are evidence of unresolved ownership, not a migration
    # authorization. Do not let a newly created reservation table hide them.
    if keys and _table_exists(connection, "recursive_work_state"):
        fingerprints = connection.execute(
            """SELECT DISTINCT r.work_fingerprint,l.holder,r.status FROM recursive_work_state r
               LEFT JOIN leases l ON l.work_fingerprint=r.work_fingerprint
               WHERE r.work_fingerprint<>?""", (work_fingerprint,)
        ).fetchall()
        for other, other_holder, status in fingerprints:
            if not set(keys).intersection(_collision_work_keys(connection, other)):
                continue
            # A terminal label is not authenticated proof that an earlier
            # admitted attempt caused no effect. Historical roots may have
            # missing collision_reservations rows; their unresolved admitted
            # attempts must still fence an overlapping new claim.
            unresolved = _durable_attempt_needs_reconciliation(connection, other)
            active_lease = (
                status not in ('COMPLETE', 'FAILED_DETERMINISTIC', 'SUPERSEDED')
                and other_holder is not None
            )
            if unresolved or active_lease:
                raise ValueError("collision key has an unresolved lease owner")
    for key in sorted(keys):
        row = connection.execute(
            "SELECT work_fingerprint,holder,fencing_token,generation FROM collision_reservations WHERE collision_key=?", (key,)
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO collision_reservations (collision_key,work_fingerprint,holder,fencing_token,generation) VALUES (?,?,?,?,1)",
                (key, work_fingerprint, holder, fencing_token))
            continue
        owner, prior_holder, prior_token, generation = row
        if type(generation) is not int or generation <= 0:
            raise ValueError("collision reservation generation is invalid")
        if owner is not None and owner != work_fingerprint:
            raise ValueError("collision key already has an owner")
        if owner is not None and (previous_lease is None or row[:3] != (
                previous_lease.work_fingerprint, previous_lease.holder, previous_lease.fencing_token)):
            raise ValueError("collision reservation previous owner is missing or stale")
        if owner is None and (prior_holder is not None or prior_token is not None):
            raise ValueError("collision reservation tombstone is invalid")
        connection.execute(
            """UPDATE collision_reservations SET work_fingerprint=?,holder=?,fencing_token=?,generation=generation+1
               WHERE collision_key=? AND generation=?""", (work_fingerprint, holder, fencing_token, key, generation))


def _release_collision_keys(connection, lease: Lease):
    _validate_collision_ownership(connection, lease)
    # Terminal labels alone cannot resolve an admitted effect. A reservation
    # that never acquired an execution attempt can be locally cancelled under
    # the existing exact-fence terminal CAS; admitted work retains exclusion.
    if _durable_attempt_needs_reconciliation(connection, lease.work_fingerprint):
        return
    connection.execute(
        """UPDATE collision_reservations SET work_fingerprint=NULL,holder=NULL,fencing_token=NULL
           WHERE work_fingerprint=? AND holder=? AND fencing_token=?""",
        (lease.work_fingerprint, lease.holder, lease.fencing_token))


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
            keys = _collision_work_keys(self.connection, work_fingerprint)
            if _has_durable_attempt(self.connection, work_fingerprint):
                return None  # Durable attempts require their journal/recovery API.
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
                _reserve_collision_keys(self.connection, work_fingerprint=work_fingerprint,
                    holder=holder, fencing_token=token, keys=keys)
                return Lease(work_fingerprint, holder, token, expires_at)

            current_holder, current_token, current_expires, completed = row
            if bool(completed):
                return None
            if current_holder is not None and now < float(current_expires):
                return None
            if current_holder is not None:
                _validate_collision_ownership(self.connection, Lease(work_fingerprint,
                    str(current_holder), int(current_token), float(current_expires)))

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
            _reserve_collision_keys(self.connection, work_fingerprint=work_fingerprint,
                holder=holder, fencing_token=token, keys=keys,
                previous_lease=(Lease(work_fingerprint, str(current_holder), int(current_token), float(current_expires))
                    if current_holder is not None else None))
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
            try:
                _validate_collision_ownership(self.connection, lease)
            except ValueError:
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
            if _has_durable_attempt(self.connection, lease.work_fingerprint):
                return False
            try:
                _release_collision_keys(self.connection, lease)
            except ValueError:
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
            if _has_durable_attempt(self.connection, lease.work_fingerprint):
                return False
            try:
                _release_collision_keys(self.connection, lease)
            except ValueError:
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
