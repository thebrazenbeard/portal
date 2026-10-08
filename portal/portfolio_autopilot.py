"""Opt-in host-owned portfolio continuation: no ChatGPT-native event claims.

A separate resident *process* may invoke this controller periodically.
No worker is installed or admitted by constructing this object.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import time
from typing import Callable

import yaml

from .desktop_ipc import FileBridgeClient
from .node_registry import load_execution_nodes
from .worker_registry import load_worker_backends


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=10)
    con.row_factory = sqlite3.Row
    return con


class PortfolioAutopilot:
    """Durable at-most-once continuation admission for a verified local worker."""

    def __init__(
        self, *, runtime_root: Path, journal: Path, expected_worker_sha256: str,
        client: object | None = None, max_hourly_cycles: int = 4,
    ):
        if isinstance(max_hourly_cycles, bool) or not 1 <= max_hourly_cycles <= 60:
            raise ValueError("hourly cycle budget must be 1 to 60")
        self.max_hourly_cycles = max_hourly_cycles
        if len(expected_worker_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in expected_worker_sha256
        ):
            raise ValueError("expected worker SHA-256 must be lowercase hex")
        self.root = Path(runtime_root)
        self.journal = Path(journal)
        self.worker_sha = expected_worker_sha256
        self.client = client or FileBridgeClient(self.root, timeout_seconds=180)
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with closing(_connect(self.journal)) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS autopilot_attempts("
                "generation INTEGER PRIMARY KEY, holder TEXT NOT NULL,"
                "request_id TEXT NOT NULL UNIQUE,"
                "state TEXT NOT NULL CHECK(state IN "
                "('CALLING','VERIFIED','UNKNOWN')),"
                "result_generation INTEGER, created_at REAL NOT NULL)"
            )
            con.commit()

    def _eligible(self, snapshot: dict) -> tuple[dict, dict] | None:
        session = snapshot.get("session") or {}
        if session.get("control_state") != "RUNNING":
            return None
        if snapshot.get("dispatch_mode") == "ADMISSION_ONLY":
            return None
        if snapshot.get("dispatch_mode") != "PROCESS_PROPOSAL":
            raise ValueError("unrecognized dispatch mode")
        if snapshot.get("worker_state") != "CONFIGURED":
            raise ValueError("worker reported inconsistent configuration")
        profile = snapshot.get("profile") or {}
        if (profile.get("mode") != "LIVE_AUTO_V1"
            or profile.get("discovered_effect_ceiling") != "SOURCE_ONLY"
            or profile.get("max_parallel") != 1):
            raise ValueError("autopilot requires single-slot source-only profile")
        node_config = Path(profile["nodes"])
        backend_config = Path(profile["worker_backends"])
        nodes = load_execution_nodes(node_config)
        enabled = [node for node in nodes if node.enabled]
        if len(enabled) != 1 or enabled[0].max_parallel != 1:
            raise ValueError("autopilot requires one enabled worker slot")
        manifest = yaml.safe_load(backend_config.read_text(encoding="utf-8"))
        workers = manifest["workers"]
        if len(workers) != 1 or workers[0].get("kind") != "PROCESS_JSON_V1":
            raise ValueError("paid or unsupported worker backend rejected")
        if workers[0].get("pass_env"):
            raise ValueError("autopilot refuses inherited credential environment")
        if workers[0].get("node_id") != enabled[0].node_id:
            raise ValueError("worker node mismatch")
        configured = load_worker_backends(backend_config)[enabled[0].node_id]
        command = configured.command
        if (len(command) < 2
                or Path(command[1]).name != "local_ollama_worker.py"
                or not Path(command[1]).is_file()):
            raise ValueError("autopilot requires local Ollama worker")
        digest = hashlib.sha256(Path(command[1]).read_bytes()).hexdigest()
        if digest != self.worker_sha:
            raise ValueError("worker changed since qualification")
        if session.get("holder") != profile["holder"]:
            raise ValueError("portfolio holder does not match profile")
        return session, profile


    def run_once(self) -> dict:
        """Run one host continuation or return a guarded no-dispatch status."""
        snapshot = self.client.request(
            "desktop_portfolio", action="inspect", session_id="portfolio"
        )
        ready = self._eligible(snapshot)
        if ready is None:
            return {"state": "HOLD_NO_QUALIFIED_WORKER", "admitted": False}
        session, profile = ready
        generation = session["generation"]
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise ValueError("invalid session generation")
        request_id = "portalautog" + str(generation) + "_"
        request_id += hashlib.sha256(profile["holder"].encode()).hexdigest()[:20]
        with closing(_connect(self.journal)) as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute(
                "SELECT state FROM autopilot_attempts ORDER BY generation DESC LIMIT 1"
            ).fetchone()
            if previous and previous["state"] != "VERIFIED":
                con.commit()
                return {"state": "HOLD_UNRESOLVED_ATTEMPT", "admitted": False}
            recent = con.execute(
                "SELECT COUNT(*) FROM autopilot_attempts WHERE created_at>=?",
                (time.time() - 3600,),
            ).fetchone()[0]
            if recent >= self.max_hourly_cycles:
                con.commit()
                return {"state": "HOLD_HOURLY_BUDGET", "admitted": False}
            try:
                con.execute(
                    "INSERT INTO autopilot_attempts VALUES (?,?,?,'CALLING',NULL,?)",
                    (generation, session["holder"], request_id, time.time()),
                )
            except sqlite3.IntegrityError:
                con.commit()
                return {"state": "HOLD_DUPLICATE_GENERATION", "admitted": False}
            con.commit()
        try:
            result = self.client.request(
                "desktop_portfolio", request_id=request_id,
                action="continue", session_id="portfolio",
                expected_generation=generation,
                expected_holder=session["holder"],
            )
            after = self.client.request(
                "desktop_portfolio", action="inspect", session_id="portfolio"
            )
            observed = (after.get("session") or {})
            if (result.get("session", {}).get("generation") != generation + 1
                    or observed.get("generation") != generation + 1
                    or observed.get("holder") != session["holder"]
                    or observed.get("control_state") != "RUNNING"):
                raise RuntimeError("ambiguous portfolio continuation outcome")
        except Exception:
            with closing(_connect(self.journal)) as con:
                con.execute(
                    "UPDATE autopilot_attempts SET state='UNKNOWN' "
                    "WHERE generation=? AND state='CALLING'", (generation,)
                )
                con.commit()
            raise
        with closing(_connect(self.journal)) as con:
            con.execute(
                "UPDATE autopilot_attempts SET state='VERIFIED',"
                "result_generation=? WHERE generation=? AND state='CALLING'",
                (generation + 1, generation),
            )
            con.commit()
        return {"state": "VERIFIED_CONTINUATION", "admitted": True,
                "generation": generation + 1}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--worker-sha256", required=True)
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument("--max-hourly-cycles", type=int, default=4)
    parser.add_argument("--forever", action="store_true")
    args = parser.parse_args(argv)
    if not 10 <= args.interval_seconds <= 86400:
        parser.error("interval must be between 10 seconds and one day")
    pilot = PortfolioAutopilot(
        runtime_root=args.runtime_root, journal=args.journal,
        expected_worker_sha256=args.worker_sha256,
        max_hourly_cycles=args.max_hourly_cycles,
    )
    try:
        while True:
            result = pilot.run_once()
            print(result, flush=True)
            if not args.forever:
                return 0
            if result["state"] == "HOLD_UNRESOLVED_ATTEMPT":
                return 2
            time.sleep(args.interval_seconds)
    except Exception as exc:
        print("AUTOPILOT_HALTED", type(exc).__name__, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
