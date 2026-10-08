"""Host-owned continuation cannot bypass source-only profile and durable claims."""
import hashlib
import sqlite3
from pathlib import Path
import sys

import pytest
import yaml

from portal.portfolio_autopilot import PortfolioAutopilot


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
                     "command": [sys.executable, str(source)] if backend == "PROCESS_JSON_V1" else None,
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


def test_one_authenticated_generation_is_durable(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot)
    state = tmp_path / "pilot.sqlite3"
    pilot = PortfolioAutopilot(
        runtime_root=root, journal=state,
        expected_worker_sha256=sha, client=bridge,
    )
    result = pilot.run_once()
    assert result == {"state": "VERIFIED_CONTINUATION", "admitted": True, "generation": 3}
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT state,result_generation FROM autopilot_attempts").fetchone() == ("VERIFIED", 3)
    assert pilot.run_once()["generation"] == 4
    assert len([x for x in bridge.calls if x["action"] == "continue"]) == 2


def test_no_worker_never_dispatches(tmp_path):
    root, sha, snapshot = fixture(tmp_path, dispatch="ADMISSION_ONLY")
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge)
    assert pilot.run_once()["state"] == "HOLD_NO_QUALIFIED_WORKER"
    assert all(call["action"] == "inspect" for call in bridge.calls)


def test_unknown_outcome_blocks_replay(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot, fail=True)
    state = tmp_path / "state.sqlite3"
    pilot = PortfolioAutopilot(runtime_root=root, journal=state,
                               expected_worker_sha256=sha, client=bridge)
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
                               expected_worker_sha256=sha, client=bridge)
    with pytest.raises(ValueError, match="changed"):
        pilot.run_once()
    assert not any(x["action"] == "continue" for x in bridge.calls)


def test_paid_worker_is_rejected_before_dispatch(tmp_path):
    root, sha, snapshot = fixture(tmp_path, backend="CODEX_GH_PROPOSAL_V1")
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge)
    with pytest.raises(ValueError, match="paid or unsupported"):
        pilot.run_once()
    assert not any(x["action"] == "continue" for x in bridge.calls)


def test_stopped_or_holder_changed_cannot_admit(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    snapshot["session"]["control_state"] = "STOPPED"
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge)
    assert pilot.run_once()["admitted"] is False
    snapshot["session"]["control_state"] = "RUNNING"
    snapshot["session"]["holder"] = "another-owner"
    with pytest.raises(ValueError, match="holder"):
        pilot.run_once()


def test_hourly_admission_budget_blocks_unbounded_refill(tmp_path):
    root, sha, snapshot = fixture(tmp_path)
    bridge = FakeBridge(snapshot)
    pilot = PortfolioAutopilot(runtime_root=root, journal=tmp_path / "state.sqlite3",
                               expected_worker_sha256=sha, client=bridge,
                               max_hourly_cycles=1)
    assert pilot.run_once()["state"] == "VERIFIED_CONTINUATION"
    assert pilot.run_once()["state"] == "HOLD_HOURLY_BUDGET"
    assert len([call for call in bridge.calls if call["action"] == "continue"]) == 1


def test_invalid_cycle_budget_fails_before_any_io(tmp_path):
    with pytest.raises(ValueError, match="cycle budget"):
        PortfolioAutopilot(runtime_root=tmp_path, journal=tmp_path / "state.sqlite3",
                           expected_worker_sha256="a" * 64, max_hourly_cycles=0)
