"""Host-owned continuation cannot bypass source-only profile and durable claims."""
import hashlib
import sqlite3
from pathlib import Path
import sys

import pytest
import yaml

from portal.portfolio_autopilot import PortfolioAutopilot, command_vector_sha256


class FakeBridge:
    def __init__(self, snapshot, *, fail=False):
        self.snapshot = snapshot
        self.calls = []
        self.fail = fail

    def request(self, command, **kwargs):
        assert command == "desktop_portfolio"
        self.calls.append(kwargs)
        if kwargs["action"] == "inspect":
            return self.snapshot
        assert kwargs["action"] == "continue"
        if self.fail:
            raise RuntimeError("claimed request outcome unknown")
        assert kwargs["expected_generation"] == self.snapshot["session"]["generation"]
        assert kwargs["expected_holder"] == self.snapshot["session"]["holder"]
        self.snapshot["session"] = dict(
            self.snapshot["session"],
            generation=self.snapshot["session"]["generation"] + 1,
        )
        return self.snapshot


def fixture(tmp_path, *, dispatch="PROCESS_PROPOSAL", backend="PROCESS_JSON_V1"):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "local_ollama_worker.py"
    source.write_text("# placeholder local worker\n", encoding="utf-8")
    interpreter = root / "python.exe"
    interpreter.write_bytes(b"qualified fake Python interpreter; never executed")
    checkout_index = root / "checkouts.json"
    checkout_index.write_text('{"schema":"PORTAL_EXISTING_CHECKOUT_INDEX_V1","repositories":{}}',
                              encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    nodes = root / "nodes.yaml"
    nodes.write_text(yaml.safe_dump({
        "schema": "PORTAL_EXECUTION_NODES_V1",
        "nodes": [{"id": "desktop-local", "enabled": True, "max_parallel": 1,
                   "allowed_lanes": []}],
    }), encoding="utf-8")
    backends = root / "backends.yaml"
    backends.write_text(yaml.safe_dump({
        "schema": "PORTAL_WORKER_BACKENDS_V1",
        "workers": [{"node_id": "desktop-local", "kind": backend,
                     "command": [str(interpreter), str(source), "--checkout-index", str(checkout_index)] if backend == "PROCESS_JSON_V1" else None,
                     "pass_env": [], "timeout_seconds": 90}],
    }), encoding="utf-8")
    profile = {
        "mode": "LIVE_AUTO_V1", "max_parallel": 1, "holder": "sole-pr14",
        "discovered_effect_ceiling": "SOURCE_ONLY", "nodes": str(nodes),
        "worker_backends": str(backends),
    }
    snapshot = {"session": {"control_state": "RUNNING", "generation": 2,
                             "holder": "sole-pr14"},
                "dispatch_mode": dispatch,
                "worker_state": "CONFIGURED" if dispatch == "PROCESS_PROPOSAL" else "UNAVAILABLE",
                "profile": profile}
    return root, digest, snapshot


def _trusted_pins(root: Path) -> dict:
    interpreter = root / "python.exe"
    source = root / "local_ollama_worker.py"
    index = root / "checkouts.json"
    return {
        "expected_command_sha256": command_vector_sha256(
            (str(interpreter), str(source), "--checkout-index", str(index))
        ),
        "expected_interpreter_sha256": hashlib.sha256(interpreter.read_bytes()).hexdigest(),
    }


def test_one_authenticated_generation_is_durable(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot)
    state = tmp_path / "pilot.sqlite3"
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=state,
        expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
    )
    result = pilot.run_once()
    assert result == {"state": "VERIFIED_CONTINUATION", "admitted": True, "generation": 3}
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT state,result_generation FROM autopilot_attempts").fetchone() == ("VERIFIED", 3)
    assert pilot.run_once()["generation"] == 4
    continuations = [x for x in bridge.calls if x["action"] == "continue"]
    assert len(continuations) == 2
    for call in continuations:
        assert call["expected_worker_sha256"] == sha
        assert call["expected_command_sha256"] == _trusted_pins(root)["expected_command_sha256"]
        assert call["expected_interpreter_sha256"] == _trusted_pins(root)["expected_interpreter_sha256"]


def test_no_worker_never_dispatches(tmp_path):
    root, sha, snapshot = fixture(tmp_path, dispatch="ADMISSION_ONLY")
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge, **_trusted_pins(root))
    assert pilot.run_once()["state"] == "HOLD_NO_QUALIFIED_WORKER"
    assert all(call["action"] == "inspect" for call in bridge.calls)


def test_stopped_session_is_explicit_hold_without_replay(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    snapshot["session"]["control_state"] = "STOPPED"
    snapshot["session"]["summary"] = {"held": 1}
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "attempts.sqlite3",
        expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
    )
    assert pilot.run_once() == {"state": "HOLD_STOPPED_SESSION", "admitted": False}
    assert [call["action"] for call in bridge.calls] == ["inspect"]
    with sqlite3.connect(tmp_path / "attempts.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM autopilot_attempts").fetchone()[0] == 0


def test_unknown_outcome_blocks_replay(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot, fail=True)
    state = tmp_path / "state.sqlite3"
    pilot = PortfolioAutopilot(runtime_root=root, journal=state,
                               expected_worker_sha256=sha, client=bridge, **_trusted_pins(root))
    with pytest.raises(RuntimeError, match="unknown"):
        pilot.run_once()
    bridge.fail = False
    assert pilot.run_once()["state"] == "HOLD_UNRESOLVED_ATTEMPT"
    assert len([x for x in bridge.calls if x["action"] == "continue"]) == 1
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT state FROM autopilot_attempts").fetchone()[0] == "UNKNOWN"


def test_modified_worker_is_rejected_before_dispatch(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    (root / "local_ollama_worker.py").write_text("changed", encoding="utf-8")
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge, **_trusted_pins(root))
    with pytest.raises(ValueError, match="changed"):
        pilot.run_once()
    assert not any(x["action"] == "continue" for x in bridge.calls)


def test_paid_worker_is_rejected_before_dispatch(tmp_path):
    root, sha, snapshot = fixture(tmp_path, backend="CODEX_GH_PROPOSAL_V1")
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge, **_trusted_pins(root))
    with pytest.raises(ValueError, match="paid or unsupported"):
        pilot.run_once()
    assert not any(x["action"] == "continue" for x in bridge.calls)


def test_stopped_or_holder_changed_cannot_admit(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    snapshot["session"]["control_state"] = "STOPPED"
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge, **_trusted_pins(root))
    assert pilot.run_once()["admitted"] is False
    snapshot["session"]["control_state"] = "RUNNING"
    snapshot["session"]["holder"] = "another-owner"
    with pytest.raises(ValueError, match="holder"):
        pilot.run_once()


def test_hourly_admission_budget_blocks_unbounded_refill(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
                               max_hourly_cycles=1)
    assert pilot.run_once()["state"] == "VERIFIED_CONTINUATION"
    assert pilot.run_once()["state"] == "HOLD_HOURLY_BUDGET"
    assert len([call for call in bridge.calls if call["action"] == "continue"]) == 1


def test_invalid_cycle_budget_fails_before_any_io(tmp_path):
    with pytest.raises(ValueError, match="cycle budget"):
        PortfolioAutopilot(runtime_root=tmp_path, journal=tmp_path / "state.sqlite3",
                           expected_worker_sha256="a" * 64,
                           expected_command_sha256="a" * 64,
                           expected_interpreter_sha256="a" * 64,
                           max_hourly_cycles=0)



def _change_command(root: Path, transform) -> None:
    path = root / "backends.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    transform(payload["workers"][0]["command"])
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_extra_arguments_cannot_pass_admission(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    _change_command(root, lambda argv: argv.append("--unqualified-extra"))
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "attempts.sqlite3",
        expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
    )
    with pytest.raises(ValueError, match="four-argument"):
        pilot.run_once()
    assert not any(c["action"] == "continue" for c in bridge.calls)


def test_unqualified_executable_cannot_reuse_worker_digest(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    malicious = root / "unqualified.exe"
    malicious.write_bytes(b"not an approved interpreter")
    _change_command(root, lambda argv: argv.__setitem__(0, str(malicious)))
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "attempts.sqlite3",
        expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
    )
    with pytest.raises(ValueError, match="command vector changed"):
        pilot.run_once()
    assert not any(c["action"] == "continue" for c in bridge.calls)



def test_interpreter_content_hash_is_pinned(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "attempts.sqlite3",
        expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
    )
    (root / "python.exe").write_bytes(b"modified unqualified interpreter")
    with pytest.raises(ValueError, match="interpreter changed"):
        pilot.run_once()
    assert not any(c["action"] == "continue" for c in bridge.calls)


def test_checkout_index_argument_is_pinned(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    different = root / "alternate-checkouts.json"
    different.write_text("{}", encoding="utf-8")
    _change_command(root, lambda argv: argv.__setitem__(3, str(different)))
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "attempts.sqlite3",
        expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
    )
    with pytest.raises(ValueError, match="command vector changed"):
        pilot.run_once()
    assert not any(c["action"] == "continue" for c in bridge.calls)


def test_missing_index_fails_before_dispatch(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "attempts.sqlite3",
        expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
    )
    (root / "checkouts.json").unlink()
    with pytest.raises(ValueError, match="input is unavailable"):
        pilot.run_once()
    assert not any(c["action"] == "continue" for c in bridge.calls)


def test_operator_pinned_thirteen_slot_autopilot_can_continue_source_only(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    node_path = root / "nodes.yaml"
    raw = yaml.safe_load(node_path.read_text(encoding="utf-8"))
    raw["nodes"][0]["max_parallel"] = 13
    node_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    snapshot["profile"]["max_parallel"] = 13
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "attempts.sqlite3",
        expected_worker_sha256=sha, client=bridge, max_parallel_slots=13,
        **_trusted_pins(root),
    )
    result = pilot.run_once()
    assert result["state"] == "VERIFIED_CONTINUATION"
    forwarded = [call for call in bridge.calls if call["action"] == "continue"]
    assert len(forwarded) == 1
    assert forwarded[0]["expected_parallel_slots"] == 13
    assert forwarded[0]["expected_worker_sha256"] == sha


def test_autopilot_does_not_infer_multislot_authority_from_profile(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    snapshot["profile"]["max_parallel"] = 13
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "attempts.sqlite3",
        expected_worker_sha256=sha, client=bridge, **_trusted_pins(root),
    )
    with pytest.raises(ValueError, match="exact source-only slot profile"):
        pilot.run_once()
    assert [call["action"] for call in bridge.calls] == ["inspect"]


@pytest.mark.parametrize("invalid", [0, 14, True, 1.5])
def test_unqualified_autopilot_slot_cap_rejected_before_io(tmp_path, invalid):
    with pytest.raises(ValueError, match="slot cap"):
        PortfolioAutopilot(
            runtime_root=tmp_path, journal=tmp_path / "attempts.sqlite3",
            expected_worker_sha256="a" * 64,
            expected_command_sha256="b" * 64,
            expected_interpreter_sha256="c" * 64,
            max_parallel_slots=invalid,
        )


def test_distinct_explicit_session_id_is_used_for_all_ipc_calls(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=tmp_path / "new-session.sqlite3",
        expected_worker_sha256=sha, client=bridge,
        session_id="bt2-wave4-clean", **_trusted_pins(root),
    )
    assert pilot.run_once()["state"] == "VERIFIED_CONTINUATION"
    assert [call["session_id"] for call in bridge.calls] == [
        "bt2-wave4-clean", "bt2-wave4-clean", "bt2-wave4-clean",
    ]


def test_journal_binding_cannot_be_reused_for_another_session(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot)
    journal = tmp_path / "bound.sqlite3"
    first = PortfolioAutopilot(
        runtime_root=root, journal=journal,
        expected_worker_sha256=sha, client=bridge,
        session_id="bt2-clean", **_trusted_pins(root),
    )
    assert first.run_once()["state"] == "VERIFIED_CONTINUATION"
    before = len(bridge.calls)
    with pytest.raises(ValueError, match="another portfolio session"):
        PortfolioAutopilot(
            runtime_root=root, journal=journal,
            expected_worker_sha256=sha, client=bridge,
            session_id="portfolio", **_trusted_pins(root),
        )
    assert len(bridge.calls) == before
    with sqlite3.connect(journal) as db:
        assert db.execute(
            "SELECT session_id FROM autopilot_session_binding"
        ).fetchone() == ("bt2-clean",)
        assert db.execute("SELECT COUNT(*) FROM autopilot_attempts").fetchone()[0] == 1


def test_existing_unbound_legacy_journal_is_for_default_session_only(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    journal = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(journal) as db:
        db.execute(
            "CREATE TABLE autopilot_attempts("
            "generation INTEGER PRIMARY KEY, holder TEXT NOT NULL,"
            "request_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL,"
            "result_generation INTEGER, created_at REAL NOT NULL)"
        )
        db.execute(
            "INSERT INTO autopilot_attempts VALUES"
            "(2, 'old-holder', 'legacy-attempt', 'UNKNOWN', NULL, 0.0)"
        )
    with pytest.raises(ValueError, match="legacy journal"):
        PortfolioAutopilot(
            runtime_root=root, journal=journal,
            expected_worker_sha256=sha, session_id="new-session",
            **_trusted_pins(root),
        )
    with sqlite3.connect(journal) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM autopilot_attempts"
        ).fetchone()[0] == 1
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=journal,
        expected_worker_sha256=sha, session_id="portfolio",
        **_trusted_pins(root),
    )
    with sqlite3.connect(journal) as db:
        assert db.execute(
            "SELECT session_id FROM autopilot_session_binding"
        ).fetchone() == ("portfolio",)
    assert pilot.journal == journal


@pytest.mark.parametrize("session_id", ["", "  ", " spaced", "tail ", "bad\\nline", "a" * 129])
def test_invalid_session_id_rejected_before_journal_creation(tmp_path, session_id):
    root, sha, _snapshot = fixture(tmp_path)
    journal = tmp_path / "never.sqlite3"
    with pytest.raises(ValueError, match="session_id"):
        PortfolioAutopilot(
            runtime_root=root, journal=journal,
            expected_worker_sha256=sha,
            session_id=session_id, **_trusted_pins(root),
        )
    assert not journal.exists()


@pytest.mark.parametrize("budget", [True, 1.5, "3", None])
def test_non_integral_hourly_budget_rejected_before_journal_creation(tmp_path, budget):
    root, sha, _snapshot = fixture(tmp_path)
    journal = tmp_path / "never.sqlite3"
    with pytest.raises(ValueError, match="cycle budget"):
        PortfolioAutopilot(
            runtime_root=root, journal=journal,
            expected_worker_sha256=sha,
            max_hourly_cycles=budget, **_trusted_pins(root),
        )
    assert not journal.exists()
