"""Desktop-local durable UI state and descriptive portfolio views."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from typing import Mapping


_DESKTOP_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('human', 'vera', 'system')),
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    request_id TEXT,
    state TEXT,
    route_id TEXT
);
"""


class DesktopStateStore:
    """Desktop-local UI state. This is not Vera canonical memory."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
        )
        with self._lock:
            self.connection.executescript(_DESKTOP_STATE_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def append_message(
        self,
        *,
        role: str,
        content: str,
        created_at: float,
        request_id: str | None = None,
        state: str | None = None,
        route_id: str | None = None,
    ) -> None:
        if role not in {"human", "vera", "system"}:
            raise ValueError("unsupported desktop conversation role")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("conversation content is required")
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO conversation_messages(
                    role, content, created_at, request_id, state, route_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    role,
                    content,
                    float(created_at),
                    request_id,
                    state,
                    route_id,
                ),
            )
            self.connection.commit()

    def list_messages(self, *, limit: int = 200) -> tuple[dict[str, object], ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT role, content, created_at, request_id, state, route_id
                FROM (
                    SELECT sequence, role, content, created_at, request_id, state, route_id
                    FROM conversation_messages
                    ORDER BY sequence DESC
                    LIMIT ?
                )
                ORDER BY sequence ASC
                """,
                (int(limit),),
            ).fetchall()
        return tuple(
            {
                "role": str(row[0]),
                "content": str(row[1]),
                "created_at": float(row[2]),
                "request_id": str(row[3]) if row[3] is not None else None,
                "state": str(row[4]) if row[4] is not None else None,
                "route_id": str(row[5]) if row[5] is not None else None,
            }
            for row in rows
        )


def load_portfolio_view(path: Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("portfolio corpus must be an object")
    if payload.get("corpus_id") != "PROJECT_RUNNER_PORTFOLIO_CORPUS_V1":
        raise ValueError("unexpected portfolio corpus schema")

    open_loops: list[dict[str, object]] = []
    for collection_name, kind in (("records", "repository"), ("workstreams", "workstream")):
        raw_items = payload.get(collection_name, [])
        if not isinstance(raw_items, list):
            raise ValueError(f"portfolio corpus {collection_name} must be a list")
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            activity_state = raw.get("activity_state")
            if not isinstance(activity_state, str) or not activity_state.startswith("ACTIVE"):
                continue
            item_id = raw.get("id")
            name = raw.get("name")
            frontier = raw.get("current_frontier")
            status = raw.get("status")
            if not all(isinstance(value, str) and value.strip() for value in (item_id, name, frontier)):
                continue
            open_loops.append(
                {
                    "kind": kind,
                    "id": item_id.strip(),
                    "name": name.strip(),
                    "repository": (
                        str(raw["repository"]).strip()
                        if raw.get("repository") is not None
                        else None
                    ),
                    "priority": (
                        str(raw["priority"]).strip()
                        if raw.get("priority") is not None
                        else None
                    ),
                    "activity_state": activity_state.strip(),
                    "status": str(status).strip() if status is not None else "",
                    "frontier": frontier.strip(),
                }
            )

    return {
        "observed_at": payload.get("observed_at"),
        "freshness_rule": payload.get("freshness_rule"),
        "descriptive_only": True,
        "authority_granted": False,
        "open_loops": tuple(open_loops),
    }



def load_pending_authority_requests(
    directory: Path,
) -> tuple[dict[str, object], ...]:
    directory = Path(directory)
    if not directory.is_dir():
        return ()

    requests: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"authority request is not valid JSON: {path.name}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ValueError("authority request must be an object")
        if payload.get("schema") != "PORTAL_DESKTOP_AUTHORITY_REQUEST_V1":
            raise ValueError("unexpected authority request schema")
        if payload.get("state") != "PENDING":
            continue

        required: dict[str, str] = {}
        for key in (
            "request_id",
            "effect_class",
            "operation",
            "target",
            "summary",
            "source",
            "exact_head",
            "work_fingerprint",
        ):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"authority request {key} is required")
            required[key] = value.strip()
        if len(required["exact_head"]) != 40 or any(
            ch not in "0123456789abcdef" for ch in required["exact_head"]
        ):
            raise ValueError(
                "authority request exact_head must be lowercase 40-hex"
            )
        if len(required["work_fingerprint"]) != 64 or any(
            ch not in "0123456789abcdef"
            for ch in required["work_fingerprint"]
        ):
            raise ValueError(
                "authority request work_fingerprint must be lowercase sha256"
            )
        fencing_token = payload.get("fencing_token")
        if (
            isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or fencing_token <= 0
        ):
            raise ValueError(
                "authority request fencing_token must be a positive integer"
            )
        created_at = payload.get("created_at")
        if not isinstance(created_at, (int, float)) or isinstance(
            created_at,
            bool,
        ):
            raise ValueError("authority request created_at must be numeric")

        effect_class = required["effect_class"]
        requests.append(
            {
                "request_id": required["request_id"],
                "effect_class": effect_class,
                "operation": required["operation"],
                "target": required["target"],
                "summary": required["summary"],
                "source": required["source"],
                "created_at": float(created_at),
                "exact_head": required["exact_head"],
                "work_fingerprint": required["work_fingerprint"],
                "fencing_token": fencing_token,
                "state": "PENDING",
                "request_is_authority": False,
                "approval_mints_execution_authority": True,
                "approval_mints_protected_effect_authority": (
                    effect_class != "NO_PROTECTED_EFFECT"
                ),
            }
        )
    return tuple(requests)
