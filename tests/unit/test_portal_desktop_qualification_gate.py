"""Adversarial staged-host qualification, with no model/provider effects."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
from types import ModuleType

import pytest

from portal import desktop_qualify
from portal.desktop_install import InstallSpec, RuntimeSource


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("human_echo", "human cognition response"),
        ("human_remote", "human cognition route"),
        ("human_mismatched_receipt", "human cognition route/evidence"),
        ("autonomous_echo", "autonomous cognition response"),
        ("autonomous_remote", "autonomous cognition route"),
        ("volition_remote", "volition cognition route"),
        ("stale_event", "autonomous qualification event"),
        ("audio_only", "local text"),
        ("remote_optin", None),
        ("valid", None),
    ],
)
def test_staged_qualification_rejects_echo_stale_or_nonlocal_cognition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, reason: str | None,
) -> None:
    spec = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id="qualified-gate",
        sources=tuple(RuntimeSource(c, f"thebrazenbeard/{c}", "main", char * 40)
                      for c, char in zip(("vera-mono", "portal", "pre-active", "volition"), "abcd")),
    )
    spec.write(tmp_path / "RUNTIME_INSTALL_SPEC.json")
    state: dict[str, object] = {"human_id": "human", "keys": []}
    accepted = {
        "status": "ACCEPTED_HOST_OBSERVATION",
        "canonical_memory_write": False,
        "protected_effect_authority": False,
    }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, command: str, **kwargs):
            if command == "desktop_status":
                return {
                    "state": "ACTIVE", "runtime_id": "expected-runtime", "pid": 100,
                    "started_at": 1000.0, "source_manifest": spec.to_mapping(),
                    "components": {n: {"loaded": True}
                                   for n in ("vera_mono", "portal", "pre_active", "volition")},
                    "protected_effect_authority": False,
                }
            if command == "desktop_discover_routes":
                return {"routes": [{
                    "route_id": "local-text", "local": True, "available": True,
                    "current": True, "auto_admissible": True,
                    "incremental_paid_compute": False,
                    "capabilities": ["audio"] if mode == "audio_only" else ["text"],
                }]}
            if command == "desktop_cognize":
                state["human_id"] = kwargs["request_id"]
                return {
                    "state": "COMPLETED",
                    "response_text": (
                        "Reply only with PORTAL_DESKTOP_HUMAN_OK"
                        if mode == "human_echo" else "PORTAL_DESKTOP_HUMAN_OK"
                    ),
                    "route_id": ("paid-remote" if mode in ("human_remote", "remote_optin")
                                 else "local-text"),
                    "evidence_id": "human-evidence",
                    "protected_effect_authority": False,
                }
            if command == "desktop_recent_activity":
                auto_answer = (
                    "Reply only with PORTAL_DESKTOP_AUTONOMOUS_OK"
                    if mode == "autonomous_echo" else "PORTAL_DESKTOP_AUTONOMOUS_OK"
                )
                return {"items": [
                    {
                        "request_id": request_id, "state": "COMPLETED",
                        "route_id": ("paid-remote" if
                                     (mode in ("human_remote", "remote_optin")
                                      and request_id == state["human_id"])
                                     or (mode in ("autonomous_remote", "remote_optin")
                                         and request_id == "old-auto")
                                     or (mode == "volition_remote" and request_id == "volition-auto")
                                     else "local-text"),
                        "evidence_id": (
                            "different-human-evidence" if mode == "human_mismatched_receipt"
                            and request_id == state["human_id"] else "human-evidence"
                        ),
                        "protected_effect_authority": False,
                        "acceptance": {
                            **accepted,
                            "response_sha256": hashlib.sha256((
                                "PORTAL_DESKTOP_HUMAN_OK" if request_id == state["human_id"]
                                else auto_answer
                            ).encode("utf-8")).hexdigest(),
                        },
                    }
                    for request_id in (state["human_id"], "old-auto", "volition-auto")
                ]}
            raise AssertionError(command)

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def request_autonomous_turn(self, **kwargs):
            state["keys"].append(kwargs["dedup_key"])
            return "old-auto"

        def list_events(self, *, kind):
            assert kind == "autonomous.turn"
            return [{
                "id": "old-auto", "status": "DONE",
                "dedup_key": (
                    "historical-qualification" if mode == "stale_event"
                    else state["keys"][-1]
                ),
                "payload": {
                    "task": "Reply only with PORTAL_DESKTOP_AUTONOMOUS_OK",
                    "capabilities": ["text"], "source": "ENDOGENOUS",
                    "reason": "staged resident autonomous cognition qualification",
                },
            },
                    {"id": "volition-auto", "status": "DONE"}]

        def get_volition_signal_receipt(self, signal_id):
            return {"cognition_event_id": "volition-auto", "state_revision": 1}

        def close(self):
            pass

    class FakeVolitionBridge:
        def __init__(self, store):
            pass

        def enqueue_signal(self, **kwargs):
            return "volition-signal"

    fake_pre_active = ModuleType("pre_active")
    fake_pre_active.__path__ = []
    fake_pre_active.Store = FakeStore
    fake_bridge = ModuleType("pre_active.volition_bridge")
    fake_bridge.VolitionBridge = FakeVolitionBridge
    fake_pre_active.volition_bridge = fake_bridge
    monkeypatch.setitem(sys.modules, "pre_active", fake_pre_active)
    monkeypatch.setitem(sys.modules, "pre_active.volition_bridge", fake_bridge)
    monkeypatch.setattr(desktop_qualify, "FileBridgeClient", FakeClient)

    if reason is None:
        kwargs = {"timeout_seconds": 1.0, "require_local_route": mode != "remote_optin"}
        assert desktop_qualify.qualify_runtime(tmp_path, **kwargs)["qualified"] is True
        assert desktop_qualify.qualify_runtime(tmp_path, **kwargs)["qualified"] is True
        assert len(state["keys"]) == 2
        assert len(set(state["keys"])) == 2
        assert all(re.fullmatch(r"portal-desktop-staged-qualification-v2-[0-9a-f]{32}", key)
                   for key in state["keys"])
    else:
        with pytest.raises(RuntimeError, match=reason):
            desktop_qualify.qualify_runtime(tmp_path, timeout_seconds=1.0)
        assert not (tmp_path / "QUALIFICATION.json").exists()
