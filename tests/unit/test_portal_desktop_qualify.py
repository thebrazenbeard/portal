import json
import threading

import pytest

import portal.desktop_qualify as qualification
pre_active = pytest.importorskip("pre_active")
pytest.importorskip("pre_active.volition_bridge")


class FakeStore:
    autonomous_event = "source-autonomous"
    volition_event = "source-volition"
    def __init__(self, _):
        pass
    def request_autonomous_turn(self, **_):
        return self.autonomous_event
    def list_events(self, **_):
        return [{"id": event, "status": "DONE"} for event in (self.autonomous_event, self.volition_event)]
    def get_volition_signal_receipt(self, _):
        return {"cognition_event_id": self.volition_event, "state_revision": 1,
                "goal_id": "g1", "choice_class": "ADOPT", "effect_authority": False}
    def close(self):
        pass


class FakeBridge:
    def __init__(self, _):
        pass
    def enqueue_signal(self, **_):
        return "volition-signal"


class FakeClient:
    def __init__(self, *, delay=False, rejected=False, unrelated=False, bad_portfolio=False):
        self.human_id = None
        self.activity_polls = 0
        self.delay = delay
        self.rejected = rejected
        self.unrelated = unrelated
        self.bad_portfolio = bad_portfolio
        self.commands = []
    @staticmethod
    def receipt(request_id, *, accepted=True):
        return {"status": "ACCEPTED_HOST_OBSERVATION" if accepted else "REJECTED",
                "request_id": request_id, "runtime_id": "runtime-1",
                "route_id": "ollama:local", "canonical_memory_write": False,
                "protected_effect_authority": False}
    def record(self, request_id, *, source_event_id=None, state="COMPLETED", accepted=True):
        return {"request_id": request_id, "source_event_id": source_event_id,
                "source": "PRE_ACTIVE_AUTONOMOUS_TURN" if source_event_id else "HUMAN",
                "state": state, "route_id": "ollama:local", "evidence_id": "evidence:" + request_id,
                "protected_effect_authority": False,
                "acceptance": self.receipt(request_id, accepted=accepted)}
    def request(self, command, **payload):
        self.commands.append((command, payload))
        if command == "desktop_status":
            return {"state": "ACTIVE", "runtime_id": "runtime-1", "source_manifest": {"sources": []},
                    "components": {name: {"loaded": True} for name in ("vera_mono", "portal", "pre_active", "volition")},
                    "protected_effect_authority": False}
        if command == "desktop_discover_routes":
            return {"routes": [{"route_id": "ollama:local", "local": True, "available": True,
                                "current": True, "incremental_paid_compute": False, "auto_admissible": True,
                                "effect_authority_ceiling": "COGNITION_ONLY_NO_PROTECTED_EFFECT"}]}
        if command == "desktop_cognize":
            self.human_id = payload["request_id"]
            return {**self.record(self.human_id), "response_text": "sentinel: PORTAL_DESKTOP_HUMAN_OK"}
        if command == "desktop_recent_activity":
            self.activity_polls += 1
            state = "RUNNING" if self.delay and self.activity_polls < 3 else "COMPLETED"
            records = [self.record(self.human_id),
                       self.record("pre-active:step-1", source_event_id="other-event" if self.unrelated else FakeStore.autonomous_event, state=state, accepted=not self.rejected),
                       self.record("pre-active:step-2", source_event_id=FakeStore.volition_event)]
            return {"items": records}
        if command == "desktop_portfolio":
            return {"schema": "WRONG" if self.bad_portfolio else "PORTAL_DESKTOP_PORTFOLIO_V1",
                    "configured": False, "session": None, "sessions": [],
                    "dispatch_mode": "ADMISSION_ONLY", "protected_effect_authority": False}
        raise AssertionError(command)


def setup_fake(monkeypatch, client):
    monkeypatch.setattr(qualification, "FileBridgeClient", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(pre_active, "Store", FakeStore)
    monkeypatch.setattr(pre_active.volition_bridge, "VolitionBridge", FakeBridge)


def test_qualification_correlates_engine_step_and_waits_for_completion(monkeypatch, tmp_path):
    client = FakeClient(delay=True)
    setup_fake(monkeypatch, client)
    result = qualification.qualify_runtime(tmp_path, timeout_seconds=1)
    assert result["qualified"] is True
    assert result["autonomous_cognition"]["event_id"] == FakeStore.autonomous_event
    assert result["autonomous_cognition"]["request_id"] == "pre-active:step-1"
    assert result["volition_chain"]["request_id"] == "pre-active:step-2"
    assert client.activity_polls >= 4
    assert result["portfolio_ipc"]["status_only"] is True
    portfolio_calls = [payload for command, payload in client.commands if command == "desktop_portfolio"]
    assert len(portfolio_calls) == 1 and portfolio_calls[0]["action"] == "status"
    assert json.loads((tmp_path / "QUALIFICATION.json").read_text())["qualified"] is True


def test_admission_without_matching_cognition_does_not_qualify(monkeypatch, tmp_path):
    setup_fake(monkeypatch, FakeClient(unrelated=True))
    with pytest.raises(TimeoutError, match="autonomous cognition"):
        qualification.qualify_runtime(tmp_path, timeout_seconds=.02)
    assert not (tmp_path / "QUALIFICATION.json").exists()


def test_rejected_intake_does_not_qualify(monkeypatch, tmp_path):
    setup_fake(monkeypatch, FakeClient(rejected=True))
    with pytest.raises(RuntimeError, match="acceptance"):
        qualification.qualify_runtime(tmp_path, timeout_seconds=.02)
    assert not (tmp_path / "QUALIFICATION.json").exists()


def test_invalid_portfolio_status_does_not_qualify(monkeypatch, tmp_path):
    setup_fake(monkeypatch, FakeClient(bad_portfolio=True))
    with pytest.raises(RuntimeError, match="portfolio"):
        qualification.qualify_runtime(tmp_path, timeout_seconds=.1)
    assert not (tmp_path / "QUALIFICATION.json").exists()


def test_runtime_replacement_during_probe_does_not_qualify(monkeypatch, tmp_path):
    class ReplacedClient(FakeClient):
        def request(self, command, **payload):
            result = super().request(command, **payload)
            if command == "desktop_status" and self.human_id is not None:
                result["runtime_id"] = "replacement-runtime"
            return result
    setup_fake(monkeypatch, ReplacedClient())
    with pytest.raises(RuntimeError, match="changed|replacement"):
        qualification.qualify_runtime(tmp_path, timeout_seconds=.1)
    assert not (tmp_path / "QUALIFICATION.json").exists()


def test_acceptance_for_another_request_cannot_qualify(monkeypatch, tmp_path):
    class ForgedClient(FakeClient):
        def record(self, request_id, **payload):
            record = super().record(request_id, **payload)
            if payload.get("source_event_id"):
                record["acceptance"]["request_id"] = "another-request"
            return record
    setup_fake(monkeypatch, ForgedClient())
    with pytest.raises(RuntimeError, match="exact qualified Vera host acceptance"):
        qualification.qualify_runtime(tmp_path, timeout_seconds=.1)
    assert not (tmp_path / "QUALIFICATION.json").exists()


def test_qualification_real_engine_and_file_ipc_complete_every_chain(tmp_path):
    pytest.importorskip("vera_core")
    pytest.importorskip("volition")
    from portal.desktop_cognition import CognitionRoute
    from portal.desktop_host import ResidentHost, serve
    local = CognitionRoute("ollama:fake", "ollama", "fake", True, True, True,
                           ("text",), False, True, "COGNITION_ONLY_NO_PROTECTED_EFFECT")
    manifest = {"schema": "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1", "install_id": "qualification-test",
                "identity": {"project_id": "portal-test", "identity_id": "vera"},
                "cognition_policy": {"allow_local_no_paid_compute": True}, "sources": []}
    def invoke(_route, task):
        if "PORTAL_DESKTOP_HUMAN_OK" in task:
            return "PORTAL_DESKTOP_HUMAN_OK"
        if "PORTAL_DESKTOP_AUTONOMOUS_OK" in task:
            return "PORTAL_DESKTOP_AUTONOMOUS_OK"
        return "bounded Volition result"
    def factory(root, installed_manifest):
        return ResidentHost(root, installed_manifest, discover=lambda: (local,), invoke=invoke)
    stop = threading.Event()
    server = threading.Thread(target=serve, args=(tmp_path, manifest),
                              kwargs={"stop": stop, "host_factory": factory})
    server.start()
    try:
        result = qualification.qualify_runtime(tmp_path, timeout_seconds=8)
        assert result["qualified"] is True
        assert result["autonomous_cognition"]["request_id"].startswith("pre-active:")
        assert result["autonomous_cognition"]["request_id"] != result["autonomous_cognition"]["event_id"]
        assert result["volition_chain"]["acceptance_status"] == "ACCEPTED_HOST_OBSERVATION"
        assert result["portfolio_ipc"]["worker_execution_verified"] is False
        assert result["protected_effect_authority"] is False
    finally:
        stop.set()
        server.join(timeout=5)
    assert not server.is_alive()


@pytest.mark.parametrize(
    ("telemetry", "expected_mode"),
    [
        (
            {
                "schema": "PORTAL_DESKTOP_PORTFOLIO_V1",
                "sessions": [],
                "configured": False,
                "dispatch_mode": "ADMISSION_ONLY",
                "protected_effect_authority": False,
            },
            "ADMISSION_ONLY",
        ),
        (
            {
                "schema": "PORTAL_DESKTOP_PORTFOLIO_V2",
                "sessions": [],
                "configured": False,
                "dispatch_mode": "ADMISSION_ONLY",
                "worker_state": "UNAVAILABLE",
                "protected_effect_authority": False,
            },
            "ADMISSION_ONLY",
        ),
        (
            {
                "schema": "PORTAL_DESKTOP_PORTFOLIO_V2",
                "sessions": [],
                "configured": True,
                "profile": {"worker_backends": "state/worker-backends.live.yaml"},
                "dispatch_mode": "PROCESS_PROPOSAL",
                "worker_state": "CONFIGURED",
                "protected_effect_authority": False,
            },
            "PROCESS_PROPOSAL",
        ),
    ],
)
def test_qualification_accepts_governed_portfolio_versions(
    telemetry, expected_mode,
):
    assert qualification._qualified_portfolio_telemetry(telemetry) == expected_mode


@pytest.mark.parametrize("changes", [
    {"schema": "UNRECOGNIZED"},
    {"protected_effect_authority": True},
    {"sessions": "not a list"},
    {"dispatch_mode": "PRODUCTION_WRITE"},
    {"worker_state": "CONFIGURED", "dispatch_mode": "ADMISSION_ONLY"},
    {"worker_state": "UNAVAILABLE", "dispatch_mode": "PROCESS_PROPOSAL"},
    {"configured": False, "dispatch_mode": "PROCESS_PROPOSAL"},
])
def test_v2_portfolio_qualification_rejects_false_telemetry(changes):
    good = {
        "schema": "PORTAL_DESKTOP_PORTFOLIO_V2",
        "sessions": [],
        "configured": True,
        "profile": {"worker_backends": "state/worker-backends.live.yaml"},
        "dispatch_mode": "PROCESS_PROPOSAL",
        "worker_state": "CONFIGURED",
        "protected_effect_authority": False,
    }
    with pytest.raises(RuntimeError, match="portfolio status IPC"):
        qualification._qualified_portfolio_telemetry({**good, **changes})
