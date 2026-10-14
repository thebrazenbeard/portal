from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "portal_resident_reentry", ROOT / "scripts" / "portal_resident_reentry.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_resident_stops_without_restarting_stopped_session(tmp_path):
    db = tmp_path / "portal.sqlite3"
    db.touch()
    calls = []

    def invoke(args):
        calls.append(tuple(args))
        return {"session_id": "estate", "control_state": "STOPPED"}

    assert module.reenter(session_id="estate", state_db=db, poll_seconds=1,
                          max_polls=0, invoke=invoke) == []
    assert len(calls) == 1
    assert calls[0][0] == "status"


def test_resident_refills_and_rereads_every_generation(tmp_path):
    db = tmp_path / "portal.sqlite3"
    db.touch()
    calls = []
    sleeps = []

    def invoke(args):
        calls.append(tuple(args))
        if args[0] == "status":
            return {"session_id": "estate", "control_state": "RUNNING"}
        assert args[:2] == ["continue", "--resume"]
        return {"session_id": "estate", "mode": "PORTAL_COMMAND_SESSION_CONTINUE_V1",
                "control_state": "RUNNING", "generation": len(calls) // 2, "summary": {}}

    result = module.reenter(session_id="estate", state_db=db,
                            poll_seconds=1, max_polls=3,
                            invoke=invoke, sleep=sleeps.append)
    assert len(result) == 3
    assert [c[0] for c in calls] == ["status", "continue"] * 3
    assert sleeps == [1.0, 1.0]
    assert all("--resume" in c for c in calls if c[0] == "continue")


def test_resident_never_dispatches_on_invalid_state(tmp_path):
    db = tmp_path / "portal.sqlite3"
    db.touch()
    calls = []

    def invoke(args):
        calls.append(tuple(args))
        return {"session_id": "other", "control_state": "RUNNING"}

    with pytest.raises(RuntimeError, match="ambiguous"):
        module.reenter(session_id="estate", state_db=db, poll_seconds=1,
                       max_polls=1, invoke=invoke)
    assert len(calls) == 1


def test_resident_fails_closed_on_unknown_outcome(tmp_path):
    db = tmp_path / "portal.sqlite3"
    db.touch()
    calls = []

    def invoke(args):
        calls.append(tuple(args))
        if args[0] == "status":
            return {"session_id": "estate", "control_state": "RUNNING"}
        return {"session_id": "estate", "mode": "WRONG", "control_state": "RUNNING"}

    with pytest.raises(RuntimeError, match="ambiguous"):
        module.reenter(session_id="estate", state_db=db, poll_seconds=1,
                       max_polls=1, invoke=invoke)
    assert len(calls) == 2


def test_resident_refuses_unseeded_state_and_busy_loop(tmp_path):
    with pytest.raises(FileNotFoundError):
        module.reenter(session_id="estate", state_db=tmp_path / "absent.db",
                       poll_seconds=1, max_polls=1)
    db = tmp_path / "portal.sqlite3"
    db.touch()
    with pytest.raises(ValueError, match="poll_seconds"):
        module.reenter(session_id="estate", state_db=db,
                       poll_seconds=0, max_polls=1)


def test_resident_does_not_replay_ambiguous_failure(tmp_path):
    db = tmp_path / "portal.sqlite3"
    db.touch()
    calls = []

    def invoke(args):
        calls.append(tuple(args))
        if args[0] == "status":
            return {"session_id": "estate", "control_state": "RUNNING"}
        raise RuntimeError("transport unavailable after possible effect")

    with pytest.raises(RuntimeError, match="transport unavailable"):
        module.reenter(session_id="estate", state_db=db,
                       poll_seconds=1, max_polls=3, invoke=invoke)
    assert [c[0] for c in calls] == ["status", "continue"]
