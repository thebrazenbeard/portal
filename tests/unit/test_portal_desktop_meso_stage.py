"""Optional real-library proof for the V2 staged MESO ingress seam.

The default PORTAL dev environment does not install MESO/Pre-Active. A V2
installed runtime must have them, and desktop_qualify fails if it does not.
"""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from portal import desktop_qualify
from portal.desktop_install import InstallSpec, RuntimeSource


def _source_spec(schema: str) -> InstallSpec:
    components = ("vera-mono", "portal", "pre-active", "volition")
    if schema == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2":
        components += ("meso-crct",)
    return InstallSpec(
        schema=schema,
        install_id="frozen-runtime",
        sources=tuple(
            RuntimeSource(component, f"thebrazenbeard/{component}", "main", char * 40)
            for component, char in zip(components, "abcde")
        ),
    )


@pytest.mark.parametrize("downgrade", ["schema", "source-sha", "install-id"])
def test_v2_rejects_stale_or_forged_host_source_manifest(
    tmp_path: Path, downgrade: str,
) -> None:
    frozen = _source_spec("VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2")
    frozen.write(tmp_path / "RUNTIME_INSTALL_SPEC.json")
    host_manifest = frozen.to_mapping()
    if downgrade == "schema":
        host_manifest["schema"] = "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1"
    elif downgrade == "source-sha":
        host_manifest["sources"][-1]["sha"] = "f" * 40
    else:
        host_manifest["install_id"] = "another-runtime"

    with pytest.raises(RuntimeError, match="differs from staged install"):
        desktop_qualify._validated_staged_source_identity(tmp_path, host_manifest)


def test_v1_qualification_receipt_keeps_exact_historical_keys(tmp_path: Path) -> None:
    frozen = _source_spec("VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1")
    frozen.write(tmp_path / "RUNTIME_INSTALL_SPEC.json")
    identity = desktop_qualify._validated_staged_source_identity(
        tmp_path, frozen.to_mapping(),
    )
    assert identity["schema"] == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1"
    legacy = {"schema": "VERA_DESKTOP_RUNTIME_QUALIFICATION_V1", "qualified": True}
    before = dict(legacy)
    desktop_qualify._add_optional_meso_receipt(legacy, None)
    assert legacy == before
    assert "meso_ingress" not in legacy


def test_v2_meso_ingress_is_durable_and_effect_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("meso_crct")
    pytest.importorskip("volition")
    pytest.importorskip("pre_active")
    from pre_active.context import ContextAssembler
    from pre_active.engine import Engine, ModelResponse
    from pre_active.store import Store
    from pre_active.tools import ToolRegistry

    class Model:
        def respond(self, *, messages, tools):
            return ModelResponse(final_text="MESO_BRIDGE_QUALIFICATION_OK")

    state_path = tmp_path / "state" / "pre-active" / "runtime.sqlite3"
    state_path.parent.mkdir(parents=True)

    def process_then_read(predicate, *, timeout_seconds):
        worker = Store(state_path)
        try:
            engine = Engine(
                store=worker, model=Model(), tools=ToolRegistry(worker),
                context=ContextAssembler(worker), system_prompt="Run.",
                worker_id="meso-stage-test",
            )
            assert engine.run_once(now=time.time() + 0.01) is None
        finally:
            worker.close()
        return predicate()

    monkeypatch.setattr(desktop_qualify, "_wait_until", process_then_read)
    receipt = desktop_qualify.qualify_meso_ingress(tmp_path, timeout_seconds=1.0)
    assert receipt["signal_provenance"] == "model_generated"
    assert receipt["effect_authority"] is False
    assert receipt["claim_ceiling"] == "MESO_INTENT_INGRESS_ONLY_NOT_OUTCOME_OR_GOAL_CREDIT"

    reopened = Store(state_path)
    try:
        events = reopened.list_events(kind="volition.signal")
        assert len(events) == 1
        assert events[0]["id"] == receipt["signal_event_id"]
        assert events[0]["payload"]["meso_source_event_id"] == receipt["source_event_id"]
        assert events[0]["payload"]["effect_authority"] is False
        assert "capabilities" not in events[0]["payload"]
        assert reopened.get_volition_signal_receipt(receipt["signal_event_id"])
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "changed", ["source-sha", "resident-restart", "component-lost", "inactive", "unchanged"],
)
def test_v2_qualification_rejects_resident_change_during_meso_ingress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    """An earlier host read cannot qualify a host that changed during MESO."""
    import pre_active
    import pre_active.volition_bridge

    frozen = _source_spec("VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V2")
    frozen.write(tmp_path / "RUNTIME_INSTALL_SPEC.json")
    before = frozen.to_mapping()
    state = {"after_meso": False, "human_id": None}
    accepted = {
        "status": "ACCEPTED_HOST_OBSERVATION",
        "canonical_memory_write": False,
        "protected_effect_authority": False,
    }

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def request(self, command: str, **kwargs):
            if command == "desktop_status":
                manifest = before
                pid, started_at = 1001, 1000.0
                reported_state = "ACTIVE"
                components = {
                    name: {"loaded": True}
                    for name in ("vera_mono", "portal", "pre_active", "volition")
                }
                if state["after_meso"]:
                    if changed == "source-sha":
                        manifest = frozen.to_mapping()
                        manifest["sources"][-1]["sha"] = "f" * 40
                    elif changed == "resident-restart":
                        pid, started_at = 2002, 2000.0
                    elif changed == "component-lost":
                        components["volition"] = {"loaded": False}
                    elif changed == "inactive":
                        reported_state = "STOPPED"
                return {
                    "state": reported_state, "source_manifest": manifest,
                    "runtime_id": "runtime-v2", "pid": pid,
                    "started_at": started_at,
                    "components": components,
                    "protected_effect_authority": False,
                }
            if command == "desktop_discover_routes":
                return {"routes": [{
                    "local": True, "available": True, "current": True,
                    "incremental_paid_compute": False, "auto_admissible": True,
                }]}
            if command == "desktop_cognize":
                state["human_id"] = kwargs["request_id"]
                return {
                    "state": "COMPLETED",
                    "response_text": "PORTAL_DESKTOP_HUMAN_OK",
                    "protected_effect_authority": False,
                    "route_id": "local", "evidence_id": "human-evidence",
                }
            if command == "desktop_recent_activity":
                return {"items": [
                    {
                        "request_id": request_id, "state": "COMPLETED",
                        "route_id": "local", "evidence_id": "evidence",
                        "protected_effect_authority": False,
                        "acceptance": accepted,
                    }
                    for request_id in (state["human_id"], "auto-turn", "volition-turn")
                ]}
            raise AssertionError(f"unexpected request: {command}")

    class FakeStore:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def request_autonomous_turn(self, **kwargs) -> str:
            return "auto-turn"

        def list_events(self, *, kind: str):
            assert kind == "autonomous.turn"
            return [
                {"id": "auto-turn", "status": "DONE"},
                {"id": "volition-turn", "status": "DONE"},
            ]

        def get_volition_signal_receipt(self, event_id: str):
            assert event_id == "volition-signal"
            return {
                "state_revision": 1,
                "cognition_event_id": "volition-turn",
                "goal_id": "goal:1", "choice_class": "NONE",
            }

        def close(self) -> None:
            pass

    class FakeVolitionBridge:
        def __init__(self, store) -> None:
            pass

        def enqueue_signal(self, **kwargs) -> str:
            return "volition-signal"

    def changed_after_meso(*args, **kwargs):
        state["after_meso"] = True
        return {"claim_ceiling": "MESO_INTENT_INGRESS_ONLY_NOT_OUTCOME_OR_GOAL_CREDIT"}

    monkeypatch.setattr(desktop_qualify, "FileBridgeClient", FakeClient)
    monkeypatch.setattr(desktop_qualify, "qualify_meso_ingress", changed_after_meso)
    monkeypatch.setattr(pre_active, "Store", FakeStore)
    monkeypatch.setattr(pre_active.volition_bridge, "VolitionBridge", FakeVolitionBridge)

    if changed == "unchanged":
        result = desktop_qualify.qualify_runtime(tmp_path, timeout_seconds=1.0)
        assert result["schema"] == "VERA_DESKTOP_RUNTIME_QUALIFICATION_V2"
        assert result["qualified"] is True
        assert result["meso_ingress"]["claim_ceiling"].startswith("MESO_INTENT_INGRESS_")
        assert (tmp_path / "QUALIFICATION.json").is_file()
    else:
        with pytest.raises(RuntimeError, match="resident .* changed"):
            desktop_qualify.qualify_runtime(tmp_path, timeout_seconds=1.0)
        assert not (tmp_path / "QUALIFICATION.json").exists()
    assert state["after_meso"] is True
