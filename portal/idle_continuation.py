"""Durable, authority-neutral idle admission for host-observed conversation events.

This gate does not receive native ChatGPT events itself, invoke a model, or
authorize portfolio effects. An authenticated host supplies event identities.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
import sqlite3

_EVENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@dataclass(frozen=True)
class IdleTicket:
    ticket_id: str
    reply_id: str
    input_id: str
    input_epoch: int
    claimed_by: str


class PortalIdleGate:
    """One locally durable gate; SQLite serializes competing host claimants."""

    def __init__(self, state_db: Path, *, idle_seconds: float = 60.0):
        if not math.isfinite(idle_seconds) or idle_seconds <= 0:
            raise ValueError("idle_seconds must be finite and positive")
        self.path = Path(state_db)
        self.idle_seconds = float(idle_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS portal_idle_state(
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    input_epoch INTEGER NOT NULL,
                    input_id TEXT,
                    reply_id TEXT,
                    due_at REAL,
                    state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portal_idle_inputs(
                    input_id TEXT PRIMARY KEY,
                    input_epoch INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portal_idle_claims(
                    ticket_id TEXT PRIMARY KEY,
                    reply_id TEXT NOT NULL,
                    input_id TEXT NOT NULL,
                    input_epoch INTEGER NOT NULL,
                    claimed_by TEXT NOT NULL,
                    claimed_at REAL NOT NULL
                );
                INSERT OR IGNORE INTO portal_idle_state
                VALUES (1,0,NULL,NULL,NULL,'AWAITING_INPUT');
            """)
            conn.commit()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _identifier(value: str) -> str:
        if not isinstance(value, str) or _EVENT_ID.fullmatch(value) is None:
            raise ValueError("event and claimant identifiers must be bounded")
        return value

    @staticmethod
    def _when(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("timestamp must be finite and nonnegative")
        if not math.isfinite(value) or value < 0:
            raise ValueError("timestamp must be finite and nonnegative")
        return float(value)

    def record_input(self, *, input_id: str) -> int:
        """Deduplicate retries; a genuinely new user input disarms the timer."""
        input_id = self._identifier(input_id)
        with closing(self._connection()) as con:
            con.execute("BEGIN IMMEDIATE")
            known = con.execute(
                "SELECT input_epoch FROM portal_idle_inputs WHERE input_id=?",
                (input_id,),
            ).fetchone()
            if known is not None:
                con.commit()
                return int(known["input_epoch"])
            epoch = int(con.execute(
                "SELECT input_epoch FROM portal_idle_state WHERE id=1"
            ).fetchone()[0]) + 1
            con.execute("INSERT INTO portal_idle_inputs VALUES (?,?)", (input_id, epoch))
            con.execute(
                "UPDATE portal_idle_state SET input_epoch=?,input_id=?,"
                "reply_id=NULL,due_at=NULL,state='AWAITING_REPLY' WHERE id=1",
                (epoch, input_id),
            )
            con.commit()
            return epoch

    def record_reply_complete(
        self, *, input_id: str, reply_id: str, completed_at: float
    ) -> bool:
        """Arm only the current input's final response; never rearm a claim."""
        input_id = self._identifier(input_id)
        reply_id = self._identifier(reply_id)
        completed_at = self._when(completed_at)
        with closing(self._connection()) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM portal_idle_state WHERE id=1").fetchone()
            if row["input_id"] != input_id:
                con.commit()
                return False
            if row["state"] == "ARMED" and row["reply_id"] == reply_id:
                con.commit()
                return True
            if row["state"] != "AWAITING_REPLY":
                con.commit()
                return False
            con.execute(
                "UPDATE portal_idle_state SET reply_id=?,due_at=?,"
                "state='ARMED' WHERE id=1",
                (reply_id, completed_at + self.idle_seconds),
            )
            con.commit()
            return True

    def claim_if_idle(
        self, *, now: float, claimed_by: str
    ) -> IdleTicket | None:
        """A ticket is a scheduling signal, never effect or spending authority."""
        now = self._when(now)
        claimed_by = self._identifier(claimed_by)
        with closing(self._connection()) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM portal_idle_state WHERE id=1").fetchone()
            if row["state"] != "ARMED" or now < row["due_at"]:
                con.commit()
                return None
            payload = f"{row['input_epoch']}:{row['input_id']}:{row['reply_id']}"
            ticket_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            con.execute(
                "INSERT INTO portal_idle_claims VALUES (?,?,?,?,?,?)",
                (ticket_id, row["reply_id"], row["input_id"],
                 row["input_epoch"], claimed_by, now),
            )
            con.execute("UPDATE portal_idle_state SET state='CLAIMED' WHERE id=1")
            con.commit()
            return IdleTicket(ticket_id, row["reply_id"], row["input_id"],
                              int(row["input_epoch"]), claimed_by)

    def status(self) -> dict:
        with closing(self._connection()) as con:
            row = con.execute("SELECT * FROM portal_idle_state WHERE id=1").fetchone()
            return dict(row)
