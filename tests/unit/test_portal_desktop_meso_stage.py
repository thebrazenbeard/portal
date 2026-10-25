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
