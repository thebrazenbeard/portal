from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterable, Mapping

from runner.github_backend import GitHubTransport
from runner.portfolio_wave_scheduler import WaveExecutionBudget

from .adapters import (
    PortalDispatchRecord,
    PortalReconciliationRecord,
    PortalRouteBinding,
)
from .models import ExecutionNode
from .wave_runtime import (
    PortalWavePacket,
    PortalWavePreparationResult,
    PortalWaveVerificationResult,
    PortalWaveStore,
    prepare_portal_wave,
    verify_portal_wave_delivery,
)


_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_command_sessions (
    session_id TEXT PRIMARY KEY,
    control_state TEXT NOT NULL CHECK (control_state IN ('RUNNING', 'STOPPED')),
    holder TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_command_subjects (
    session_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'HELD', 'TERMINAL')),
    wave_run_id TEXT,
    verification_state TEXT,
    hold_requested INTEGER NOT NULL DEFAULT 0 CHECK (hold_requested IN (0, 1)),
    adapter_id TEXT,
    route_id TEXT,
    dispatch_state TEXT,
    dispatch_evidence_id TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (session_id, subject_kind, subject_id),
    FOREIGN KEY (session_id)
        REFERENCES portal_command_sessions(session_id)
        ON DELETE CASCADE
);
"""


@dataclass(frozen=True)
class PortalSessionResult:
    session_id: str
    control_state: str
    generation: int
    wave_run_id: str
    packets: tuple[PortalWavePacket, ...]
    summary: dict[str, int]


@dataclass(frozen=True)
class PortalRefillResult:
    session_id: str
    control_state: str
    cycles: tuple[PortalSessionResult, ...]
    stop_reason: str
    idle_cycles: int
    summary: dict[str, int]


class PortalCommandSession:
    """Durable operator-intent projection above Project Runner wave execution."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(_SESSION_SCHEMA)
        columns = {
            str(row[1])
            for row in self.connection.execute(
                "PRAGMA table_info(portal_command_subjects)"
            ).fetchall()
        }
        if "hold_requested" not in columns:
            self.connection.execute(
                "ALTER TABLE portal_command_subjects "
                "ADD COLUMN hold_requested INTEGER NOT NULL DEFAULT 0 "
                "CHECK (hold_requested IN (0, 1))"
            )
        for column in (
            "adapter_id",
            "route_id",
            "dispatch_state",
            "dispatch_evidence_id",
        ):
            if column not in columns:
                self.connection.execute(
                    f"ALTER TABLE portal_command_subjects ADD COLUMN {column} TEXT"
                )

    def close(self) -> None:
        self.connection.close()

    def _ensure_session(
        self,
        *,
        session_id: str,
        holder: str,
        now: float,
    ) -> tuple[str, int]:
        session_id = session_id.strip()
        holder = holder.strip()
        if not session_id:
            raise ValueError("session_id is required")
        if not holder:
            raise ValueError("session holder is required")

        row = self.connection.execute(
            """
            SELECT control_state, generation, holder
            FROM portal_command_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            self.connection.execute(
                """
                INSERT INTO portal_command_sessions(
                    session_id, control_state, holder, generation,
                    created_at, updated_at
                ) VALUES (?, 'RUNNING', ?, 0, ?, ?)
                """,
                (session_id, holder, now, now),
            )
            return "RUNNING", 0

        if str(row[2]) != holder:
            raise ValueError("session holder changed for existing session")
        return str(row[0]), int(row[1])

    def _subjects(
        self,
        session_id: str,
        state: str,
    ) -> tuple[tuple[str, str], ...]:
        rows = self.connection.execute(
            """
            SELECT subject_kind, subject_id
            FROM portal_command_subjects
            WHERE session_id = ? AND state = ?
            ORDER BY subject_kind, subject_id
            """,
            (session_id, state),
        ).fetchall()
        return tuple((str(kind), str(subject_id)) for kind, subject_id in rows)

    def _excluded_subjects(self, session_id: str) -> tuple[tuple[str, str], ...]:
        rows = self.connection.execute(
            """
            SELECT subject_kind, subject_id
            FROM portal_command_subjects
            WHERE session_id = ? AND state IN ('HELD', 'TERMINAL')
            ORDER BY subject_kind, subject_id
            """,
            (session_id,),
        ).fetchall()
        return tuple((str(kind), str(subject_id)) for kind, subject_id in rows)

    def _summary(self, session_id: str) -> dict[str, int]:
        counts = {"active": 0, "held": 0, "terminal": 0}
        for state, count in self.connection.execute(
            """
            SELECT state, COUNT(*)
            FROM portal_command_subjects
            WHERE session_id = ?
            GROUP BY state
            """,
            (session_id,),
        ).fetchall():
            counts[str(state).lower()] = int(count)
        return counts

    def _advance(
        self,
        *,
        session_id: str,
        holder: str,
        wave_path: Path,
        corpus_path: Path,
        projects_path: Path,
        nodes: Iterable[ExecutionNode],
        budget: WaveExecutionBudget,
        lease_ttl: float,
        token: str | None,
        occupied_node_slots: Mapping[str, int] | None,
        transport: GitHubTransport | None,
        clock: Callable[[], float],
        allow_restart: bool,
    ) -> PortalSessionResult:
        now = float(clock())
        control_state, generation = self._ensure_session(
            session_id=session_id,
            holder=holder,
            now=now,
        )
        if control_state == "STOPPED" and not allow_restart:
            raise ValueError("Portal session is stopped; use run to restart it")

        active_subjects = self._subjects(session_id, "ACTIVE")
        excluded_subjects = self._excluded_subjects(session_id)
        next_generation = generation + 1
        wave_run_id = f"{session_id}::g{next_generation}"

        prepared: PortalWavePreparationResult = prepare_portal_wave(
            wave_path=Path(wave_path),
            corpus_path=Path(corpus_path),
            projects_path=Path(projects_path),
            state_db=self.path,
            nodes=tuple(nodes),
            budget=budget,
            run_id=wave_run_id,
            holder=holder,
            lease_ttl=lease_ttl,
            token=token,
            excluded_subjects=excluded_subjects,
            active_subjects=active_subjects,
            occupied_node_slots=occupied_node_slots,
            transport=transport,
            clock=clock,
        )

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for packet in prepared.packets:
                self.connection.execute(
                    """
                    INSERT INTO portal_command_subjects(
                        session_id, subject_kind, subject_id, state,
                        wave_run_id, verification_state, hold_requested, updated_at
                    ) VALUES (?, 'repository', ?, 'ACTIVE', ?, NULL, 0, ?)
                    ON CONFLICT(session_id, subject_kind, subject_id)
                    DO UPDATE SET
                        state = 'ACTIVE',
                        wave_run_id = excluded.wave_run_id,
                        verification_state = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (session_id, packet.subject_id, wave_run_id, float(clock())),
                )
            self.connection.execute(
                """
                UPDATE portal_command_sessions
                SET control_state = 'RUNNING',
                    generation = ?,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (next_generation, float(clock()), session_id),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

        return PortalSessionResult(
            session_id=session_id,
            control_state="RUNNING",
            generation=next_generation,
            wave_run_id=wave_run_id,
            packets=prepared.packets,
            summary=self._summary(session_id),
        )

    def run(
        self,
        *,
        session_id: str,
        holder: str,
        wave_path: Path,
        corpus_path: Path,
        projects_path: Path,
        nodes: Iterable[ExecutionNode],
        budget: WaveExecutionBudget,
        lease_ttl: float,
        token: str | None,
        occupied_node_slots: Mapping[str, int] | None = None,
        transport: GitHubTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> PortalSessionResult:
        return self._advance(
            session_id=session_id,
            holder=holder,
            wave_path=wave_path,
            corpus_path=corpus_path,
            projects_path=projects_path,
            nodes=nodes,
            budget=budget,
            lease_ttl=lease_ttl,
            token=token,
            occupied_node_slots=occupied_node_slots,
            transport=transport,
            clock=clock,
            allow_restart=True,
        )

    def continue_run(
        self,
        *,
        session_id: str,
        holder: str,
        wave_path: Path,
        corpus_path: Path,
        projects_path: Path,
        nodes: Iterable[ExecutionNode],
        budget: WaveExecutionBudget,
        lease_ttl: float,
        token: str | None,
        occupied_node_slots: Mapping[str, int] | None = None,
        transport: GitHubTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> PortalSessionResult:
        return self._advance(
            session_id=session_id,
            holder=holder,
            wave_path=wave_path,
            corpus_path=corpus_path,
            projects_path=projects_path,
            nodes=nodes,
            budget=budget,
            lease_ttl=lease_ttl,
            token=token,
            occupied_node_slots=occupied_node_slots,
            transport=transport,
            clock=clock,
            allow_restart=False,
        )

    def _require_session(
        self,
        *,
        session_id: str,
        holder: str | None = None,
    ) -> tuple[str, int, str]:
        row = self.connection.execute(
            """
            SELECT control_state, generation, holder
            FROM portal_command_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Portal session does not exist")
        observed_holder = str(row[2])
        if holder is not None and observed_holder != holder.strip():
            raise ValueError("session holder changed for existing session")
        return str(row[0]), int(row[1]), observed_holder

    def status(self, session_id: str) -> dict[str, object]:
        control_state, generation, holder = self._require_session(
            session_id=session_id,
        )
        rows = self.connection.execute(
            """
            SELECT
                subject_kind, subject_id, state, wave_run_id,
                verification_state, hold_requested,
                adapter_id, route_id, dispatch_state, dispatch_evidence_id
            FROM portal_command_subjects
            WHERE session_id = ?
            ORDER BY subject_kind, subject_id
            """,
            (session_id,),
        ).fetchall()
        return {
            "session_id": session_id,
            "control_state": control_state,
            "generation": generation,
            "holder": holder,
            "summary": self._summary(session_id),
            "subjects": [
                {
                    "subject_kind": str(row[0]),
                    "subject_id": str(row[1]),
                    "state": str(row[2]),
                    "wave_run_id": (
                        str(row[3]) if row[3] is not None else None
                    ),
                    "verification_state": (
                        str(row[4]) if row[4] is not None else None
                    ),
                    "hold_requested": bool(row[5]),
                    "adapter_id": (
                        str(row[6]) if row[6] is not None else None
                    ),
                    "route_id": (
                        str(row[7]) if row[7] is not None else None
                    ),
                    "dispatch_state": (
                        str(row[8]) if row[8] is not None else None
                    ),
                    "dispatch_evidence_id": (
                        str(row[9]) if row[9] is not None else None
                    ),
                }
                for row in rows
            ],
        }

    def hold(
        self,
        *,
        session_id: str,
        holder: str,
        subject_kind: str,
        subject_id: str,
        clock: Callable[[], float] = time.time,
    ) -> dict[str, object]:
        self._require_session(session_id=session_id, holder=holder)
        subject_kind = subject_kind.strip()
        subject_id = subject_id.strip()
        if not subject_kind or not subject_id:
            raise ValueError("subject_kind and subject_id are required")
        now = float(clock())

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT state
                FROM portal_command_subjects
                WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                """,
                (session_id, subject_kind, subject_id),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO portal_command_subjects(
                        session_id, subject_kind, subject_id, state,
                        wave_run_id, verification_state, hold_requested, updated_at
                    ) VALUES (?, ?, ?, 'HELD', NULL, NULL, 1, ?)
                    """,
                    (session_id, subject_kind, subject_id, now),
                )
            elif str(row[0]) == "TERMINAL":
                raise ValueError("terminal subject cannot be held")
            else:
                # Active work retains its scheduling occupancy until the owning
                # substrate proves it terminal/held. This is durable no-refill
                # intent, not cancellation authority.
                self.connection.execute(
                    """
                    UPDATE portal_command_subjects
                    SET hold_requested = 1, updated_at = ?
                    WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                    """,
                    (now, session_id, subject_kind, subject_id),
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return self.status(session_id)

    def stop(
        self,
        *,
        session_id: str,
        holder: str,
        clock: Callable[[], float] = time.time,
    ) -> dict[str, object]:
        self._require_session(session_id=session_id, holder=holder)
        self.connection.execute(
            """
            UPDATE portal_command_sessions
            SET control_state = 'STOPPED', updated_at = ?
            WHERE session_id = ?
            """,
            (float(clock()), session_id),
        )
        return self.status(session_id)

    def complete(
        self,
        *,
        session_id: str,
        holder: str,
        subject_kind: str,
        subject_id: str,
        verifier: str,
        token: str | None,
        transport: GitHubTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> dict[str, object]:
        self._require_session(session_id=session_id, holder=holder)
        row = self.connection.execute(
            """
            SELECT state, wave_run_id
            FROM portal_command_subjects
            WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
            """,
            (session_id, subject_kind, subject_id),
        ).fetchone()
        if row is None:
            raise ValueError("Portal session subject does not exist")
        if str(row[0]) == "TERMINAL":
            return self.status(session_id)
        if str(row[0]) != "ACTIVE" or row[1] is None:
            raise ValueError("Portal session subject is not active")

        result: PortalWaveVerificationResult = verify_portal_wave_delivery(
            state_db=self.path,
            run_id=str(row[1]),
            subject_id=subject_id,
            verifier=verifier,
            token=token,
            transport=transport,
            clock=clock,
        )

        next_state = "ACTIVE"
        if result.state == "VERIFIED_COMPLETE":
            next_state = "TERMINAL"
        elif result.state == "VERIFIED_HELD":
            next_state = "HELD"

        self.connection.execute(
            """
            UPDATE portal_command_subjects
            SET state = ?, verification_state = ?, updated_at = ?
            WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
            """,
            (
                next_state,
                result.state,
                float(clock()),
                session_id,
                subject_kind,
                subject_id,
            ),
        )
        if result.state != "VERIFIED_COMPLETE":
            raise ValueError(
                f"subject is not verified complete: {result.state}"
            )
        return self.status(session_id)

    def bind_routes(
        self,
        *,
        session_id: str,
        holder: str,
        wave_run_id: str,
        bindings: Iterable[PortalRouteBinding],
        clock: Callable[[], float] = time.time,
    ) -> dict[str, object]:
        self._require_session(session_id=session_id, holder=holder)
        wave_run_id = wave_run_id.strip()
        if not wave_run_id:
            raise ValueError("wave_run_id is required")

        binding_tuple = tuple(bindings)
        seen: set[tuple[str, str]] = set()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for binding in binding_tuple:
                key = (binding.subject_kind, binding.subject_id)
                if key in seen:
                    raise ValueError("duplicate route binding subject")
                seen.add(key)
                row = self.connection.execute(
                    """
                    SELECT state, wave_run_id, adapter_id, route_id
                    FROM portal_command_subjects
                    WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                    """,
                    (session_id, binding.subject_kind, binding.subject_id),
                ).fetchone()
                if row is None:
                    raise ValueError("route binding subject does not exist")
                if str(row[0]) != "ACTIVE":
                    raise ValueError("route binding requires active subject")
                if row[1] is None or str(row[1]) != wave_run_id:
                    raise ValueError("route binding wave does not match subject")

                existing_adapter = str(row[2]) if row[2] is not None else None
                existing_route = str(row[3]) if row[3] is not None else None
                if existing_adapter is not None or existing_route is not None:
                    if (
                        existing_adapter == binding.adapter_id
                        and existing_route == binding.route_id
                    ):
                        continue
                    raise ValueError(
                        "route already bound; reconciliation required before substitution"
                    )

                self.connection.execute(
                    """
                    UPDATE portal_command_subjects
                    SET adapter_id = ?,
                        route_id = ?,
                        dispatch_state = 'BOUND',
                        dispatch_evidence_id = NULL,
                        updated_at = ?
                    WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                    """,
                    (
                        binding.adapter_id,
                        binding.route_id,
                        float(clock()),
                        session_id,
                        binding.subject_kind,
                        binding.subject_id,
                    ),
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return self.status(session_id)

    def record_dispatches(
        self,
        *,
        session_id: str,
        holder: str,
        records: Iterable[PortalDispatchRecord],
        clock: Callable[[], float] = time.time,
    ) -> dict[str, object]:
        self._require_session(session_id=session_id, holder=holder)
        record_tuple = tuple(records)
        seen: set[tuple[str, str]] = set()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for record in record_tuple:
                key = (record.subject_kind, record.subject_id)
                if key in seen:
                    raise ValueError("duplicate dispatch record subject")
                seen.add(key)
                row = self.connection.execute(
                    """
                    SELECT state, adapter_id, route_id
                    FROM portal_command_subjects
                    WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                    """,
                    (session_id, record.subject_kind, record.subject_id),
                ).fetchone()
                if row is None:
                    raise ValueError("dispatch subject does not exist")
                if str(row[0]) != "ACTIVE":
                    raise ValueError("dispatch requires active subject")
                if row[1] is None or row[2] is None:
                    raise ValueError("dispatch requires durable route binding")
                if (
                    str(row[1]) != record.adapter_id
                    or str(row[2]) != record.route_id
                ):
                    raise ValueError(
                        "dispatch route differs from durable route binding"
                    )

                self.connection.execute(
                    """
                    UPDATE portal_command_subjects
                    SET dispatch_state = ?,
                        dispatch_evidence_id = ?,
                        updated_at = ?
                    WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                    """,
                    (
                        record.state,
                        record.evidence_id,
                        float(clock()),
                        session_id,
                        record.subject_kind,
                        record.subject_id,
                    ),
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return self.status(session_id)

    def record_reconciliations(
        self,
        *,
        session_id: str,
        holder: str,
        records: Iterable[PortalReconciliationRecord],
        clock: Callable[[], float] = time.time,
    ) -> dict[str, object]:
        self._require_session(session_id=session_id, holder=holder)
        record_tuple = tuple(records)
        seen: set[tuple[str, str]] = set()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for record in record_tuple:
                key = (record.subject_kind, record.subject_id)
                if key in seen:
                    raise ValueError("duplicate reconciliation record subject")
                seen.add(key)

                row = self.connection.execute(
                    """
                    SELECT state, adapter_id, route_id
                    FROM portal_command_subjects
                    WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                    """,
                    (session_id, record.subject_kind, record.subject_id),
                ).fetchone()
                if row is None:
                    raise ValueError("reconciliation subject does not exist")
                if str(row[0]) != "ACTIVE":
                    raise ValueError("reconciliation requires active subject")
                if row[1] is None or row[2] is None:
                    raise ValueError("reconciliation requires durable route binding")
                if (
                    str(row[1]) != record.adapter_id
                    or str(row[2]) != record.route_id
                ):
                    raise ValueError(
                        "reconciliation route differs from durable route binding"
                    )

                next_state = "ACTIVE"
                if record.state == "VERIFIED_COMPLETE":
                    next_state = "TERMINAL"
                elif record.state == "VERIFIED_HELD":
                    next_state = "HELD"

                self.connection.execute(
                    """
                    UPDATE portal_command_subjects
                    SET state = ?,
                        verification_state = ?,
                        dispatch_evidence_id = ?,
                        updated_at = ?
                    WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                    """,
                    (
                        next_state,
                        record.state,
                        record.evidence_id,
                        float(clock()),
                        session_id,
                        record.subject_kind,
                        record.subject_id,
                    ),
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return self.status(session_id)

    def _dispatch_generation(
        self,
        *,
        session_id: str,
        holder: str,
        result: PortalSessionResult,
        execution_adapter: object,
        clock: Callable[[], float],
    ) -> None:
        if not result.packets:
            return

        bindings = tuple(execution_adapter.select_routes(result))
        expected = {
            ("repository", packet.subject_id)
            for packet in result.packets
        }
        binding_keys = {
            (binding.subject_kind, binding.subject_id)
            for binding in bindings
        }
        if len(binding_keys) != len(bindings):
            raise ValueError("execution adapter returned duplicate route bindings")
        if binding_keys != expected:
            raise ValueError(
                "execution adapter must route every newly admitted packet"
            )

        self.bind_routes(
            session_id=session_id,
            holder=holder,
            wave_run_id=result.wave_run_id,
            bindings=bindings,
            clock=clock,
        )
        records = tuple(execution_adapter.dispatch(result, bindings))
        record_keys = {
            (record.subject_kind, record.subject_id)
            for record in records
        }
        if len(record_keys) != len(records):
            raise ValueError("execution adapter returned duplicate dispatch records")
        if record_keys != binding_keys:
            raise ValueError(
                "execution adapter must return one dispatch record per bound route"
            )
        self.record_dispatches(
            session_id=session_id,
            holder=holder,
            records=records,
            clock=clock,
        )

    def reconcile_active(
        self,
        *,
        session_id: str,
        holder: str,
        verifier: str,
        token: str | None,
        transport: GitHubTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> dict[str, int]:
        self._require_session(session_id=session_id, holder=holder)
        rows = self.connection.execute(
            """
            SELECT subject_kind, subject_id, wave_run_id
            FROM portal_command_subjects
            WHERE session_id = ? AND state = 'ACTIVE'
            ORDER BY subject_kind, subject_id
            """,
            (session_id,),
        ).fetchall()

        changed = 0
        unresolved = 0
        verified_complete = 0
        verified_held = 0

        store = PortalWaveStore(self.path)
        try:
            for subject_kind, subject_id, wave_run_id in rows:
                if wave_run_id is None:
                    unresolved += 1
                    self.connection.execute(
                        """
                        UPDATE portal_command_subjects
                        SET verification_state = 'DELIVERY_UNAVAILABLE',
                            updated_at = ?
                        WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                        """,
                        (
                            float(clock()),
                            session_id,
                            str(subject_kind),
                            str(subject_id),
                        ),
                    )
                    continue

                try:
                    delivery = store.load_delivery(
                        run_id=str(wave_run_id),
                        subject_id=str(subject_id),
                    )
                    observed_state = str(delivery["state"])
                except (KeyError, ValueError):
                    observed_state = "DELIVERY_UNAVAILABLE"

                if observed_state == "RECEIPT_RECORDED":
                    result = verify_portal_wave_delivery(
                        state_db=self.path,
                        run_id=str(wave_run_id),
                        subject_id=str(subject_id),
                        verifier=verifier,
                        token=token,
                        transport=transport,
                        clock=clock,
                    )
                    observed_state = result.state

                next_state = "ACTIVE"
                if observed_state == "VERIFIED_COMPLETE":
                    next_state = "TERMINAL"
                    verified_complete += 1
                elif observed_state == "VERIFIED_HELD":
                    next_state = "HELD"
                    verified_held += 1
                else:
                    unresolved += 1

                if next_state != "ACTIVE":
                    changed += 1

                self.connection.execute(
                    """
                    UPDATE portal_command_subjects
                    SET state = ?, verification_state = ?, updated_at = ?
                    WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                    """,
                    (
                        next_state,
                        observed_state,
                        float(clock()),
                        session_id,
                        str(subject_kind),
                        str(subject_id),
                    ),
                )
        finally:
            store.close()

        return {
            "changed": changed,
            "unresolved": unresolved,
            "verified_complete": verified_complete,
            "verified_held": verified_held,
        }

    def run_until_idle(
        self,
        *,
        session_id: str,
        holder: str,
        wave_path: Path,
        corpus_path: Path,
        projects_path: Path,
        nodes: Iterable[ExecutionNode],
        budget: WaveExecutionBudget,
        lease_ttl: float,
        token: str | None,
        verifier: str,
        max_cycles: int = 100,
        max_idle_cycles: int = 1,
        poll_seconds: float = 0.0,
        occupied_node_slots: Mapping[str, int] | None = None,
        node_occupancy_provider: Callable[[], Mapping[str, int]] | None = None,
        execution_adapter: object | None = None,
        transport: GitHubTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> PortalRefillResult:
        if type(max_cycles) is not int or max_cycles < 1:
            raise ValueError("max_cycles must be a positive integer")
        if type(max_idle_cycles) is not int or max_idle_cycles < 1:
            raise ValueError("max_idle_cycles must be a positive integer")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must be non-negative")
        if occupied_node_slots is not None and node_occupancy_provider is not None:
            raise ValueError(
                "use occupied_node_slots or node_occupancy_provider, not both"
            )

        node_tuple = tuple(nodes)
        base_common = dict(
            session_id=session_id,
            holder=holder,
            wave_path=Path(wave_path),
            corpus_path=Path(corpus_path),
            projects_path=Path(projects_path),
            nodes=node_tuple,
            budget=budget,
            lease_ttl=lease_ttl,
            token=token,
            transport=transport,
            clock=clock,
        )

        def generation_common() -> dict[str, object]:
            if node_occupancy_provider is not None:
                snapshot = dict(node_occupancy_provider())
            else:
                snapshot = dict(occupied_node_slots or {})
            return {
                **base_common,
                "occupied_node_slots": snapshot,
            }

        first = self.run(**generation_common())
        cycles: list[PortalSessionResult] = [first]
        if execution_adapter is not None:
            self._dispatch_generation(
                session_id=session_id,
                holder=holder,
                result=first,
                execution_adapter=execution_adapter,
                clock=clock,
            )
        idle_cycles = 0
        stop_reason = "MAX_CYCLES"

        initial = self.status(session_id)
        if not cycles[0].packets and initial["summary"]["active"] == 0:
            return PortalRefillResult(
                session_id=session_id,
                control_state=str(initial["control_state"]),
                cycles=tuple(cycles),
                stop_reason="IDLE",
                idle_cycles=0,
                summary=dict(initial["summary"]),
            )

        for _ in range(1, max_cycles):
            current = self.status(session_id)
            if current["control_state"] == "STOPPED":
                stop_reason = "STOPPED"
                break

            reconciled = self.reconcile_active(
                session_id=session_id,
                holder=holder,
                verifier=verifier,
                token=token,
                transport=transport,
                clock=clock,
            )

            current = self.status(session_id)
            if current["control_state"] == "STOPPED":
                stop_reason = "STOPPED"
                break

            advanced = self.continue_run(**generation_common())
            cycles.append(advanced)
            if execution_adapter is not None:
                self._dispatch_generation(
                    session_id=session_id,
                    holder=holder,
                    result=advanced,
                    execution_adapter=execution_adapter,
                    clock=clock,
                )
            current = self.status(session_id)

            if not advanced.packets and current["summary"]["active"] == 0:
                stop_reason = "IDLE"
                idle_cycles = 0
                break

            progress = bool(advanced.packets) or reconciled["changed"] > 0
            if progress:
                idle_cycles = 0
            else:
                idle_cycles += 1
                if idle_cycles >= max_idle_cycles:
                    stop_reason = "WAITING_ACTIVE"
                    break

            if poll_seconds:
                sleep(poll_seconds)

        final = self.status(session_id)
        return PortalRefillResult(
            session_id=session_id,
            control_state=str(final["control_state"]),
            cycles=tuple(cycles),
            stop_reason=stop_reason,
            idle_cycles=idle_cycles,
            summary=dict(final["summary"]),
        )
