from dataclasses import replace
import pytest
from portal.desktop_cognition import CognitionRoute, discover_cognition_routes
from portal.desktop_install import InstallSpec, RuntimeSource
from portal.desktop_host import _runtime_id
from portal.desktop_runtime import CognitionLedger, CognitionRequestEnvelope, ResidentCognitionEngine
from portal.desktop_vera_acceptance import VeraRuntimeAcceptance

DIGEST = "a" * 64
MODEL = "gemma3:1b"
SOURCES = tuple(RuntimeSource(c, "thebrazenbeard/" + c, "main", ch * 40)
                for c, ch in zip(("vera-mono", "portal", "pre-active", "volition"), "abcd"))

def admission():
    from portal.desktop_cognition import RestrictedOllamaAdmission
    return RestrictedOllamaAdmission("ollama", MODEL, DIGEST, "http://127.0.0.1:11434")

def spec():
    return InstallSpec("VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V3", "restricted", SOURCES, admission())

def test_restricted_spec_roundtrip_and_legacy_no_downgrade(tmp_path):
    value = spec()
    path = tmp_path / "spec.json"
    value.write(path)
    assert InstallSpec.read(path) == value
    assert _runtime_id(value.to_mapping()) != _runtime_id(replace(value, model_admission=replace(admission(), registry_digest="b"*64)).to_mapping())
    with pytest.raises(ValueError):
        replace(value, schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1").validate()
    with pytest.raises(ValueError):
        replace(value, model_admission=None).validate()
    with pytest.raises(ValueError):
        replace(value, model_admission=replace(admission(), endpoint="http://192.0.2.1:11434")).validate()

def test_restricted_discovery_never_falls_back_and_checks_digest(tmp_path):
    from portal.desktop_cognition import restrict_cognition_routes, discover_restricted_ollama_routes
    routes = discover_cognition_routes(command_probe=lambda *_: None,
        ollama_tags=lambda: {"models":[{"name":MODEL,"digest":DIGEST},{"name":"vera-local:latest","digest":"b"*64}]},
        pre_active_models=lambda: None)
    allowed = restrict_cognition_routes(routes, admission())
    assert len(allowed) == 1 and allowed[0].model_or_agent == MODEL
    assert discover_restricted_ollama_routes(admission(), ollama_tags=lambda: {
        "models":[{"name":MODEL,"digest":DIGEST},{"name":"vera-local:latest","digest":"b"*64}]
    }) == allowed
    assert discover_restricted_ollama_routes(admission(), ollama_tags=lambda: {
        "models":[{"name":MODEL,"digest":DIGEST}]*2
    }) == ()
    assert discover_restricted_ollama_routes(admission(), ollama_tags=lambda: {
        "models":[{"name":MODEL,"digest":"c"*64},{"name":"vera-local:latest","digest":"b"*64}]
    }) == ()
    ledger = CognitionLedger(tmp_path/"ledger.db")
    calls=[]
    current=list(routes)
    try:
        engine=ResidentCognitionEngine(ledger=ledger, discover_routes=lambda:restrict_cognition_routes(tuple(current),admission()),
             invoke_route=lambda route,task:calls.append(route.model_or_agent) or "ok")
        def process(i):
            return engine.process(CognitionRequestEnvelope(str(i),"HUMAN","fixture","test",100))
        assert process(1).state == "COMPLETED"
        current[:] = [replace(x,observed_registry_digest="c"*64) for x in routes]
        assert process(2).state == "UNRESOLVED"
        current[:] = [x for x in routes if x.model_or_agent != MODEL]
        assert process(3).state == "UNRESOLVED"
        assert calls == [MODEL]
    finally:
        ledger.close()

def test_acceptance_binds_admission_and_public_receipt(tmp_path):
    import json
    from portal.desktop_ipc import _public_acceptance
    from portal.desktop_cognition import restrict_cognition_routes
    routes=discover_cognition_routes(command_probe=lambda *_:None,
        ollama_tags=lambda:{"models":[{"name":MODEL,"digest":DIGEST}]}, pre_active_models=lambda:None)
    class Fixture:
        def resume_context(self):
            return {"synthetic":True}
    accept=VeraRuntimeAcceptance(Fixture(),runtime_id="fixture",model_admission=admission())
    req=CognitionRequestEnvelope("one","HUMAN","test","neutral",100)
    receipt=accept(req,routes[0],"ok")
    assert receipt["model_admission_digest"]==admission().binding_digest()
    projected=_public_acceptance(json.dumps(receipt))
    assert projected["model_admission_digest"]==receipt["model_admission_digest"]
    assert projected["protected_effect_authority"] is False
    assert "task" not in projected
    with pytest.raises(RuntimeError,match="restricted admission"):
        accept(req,replace(routes[0],observed_registry_digest="b"*64),"ok")

def test_restricted_qualifier_refuses_optional_route_override(tmp_path, monkeypatch):
    from portal import desktop_qualify
    value=spec()
    value.write(tmp_path/"RUNTIME_INSTALL_SPEC.json")
    class Fixture:
        def __init__(self,*args,**kwargs):
            pass
        def request(self,command,**kwargs):
            assert command=="desktop_status"
            return {"state":"ACTIVE","source_manifest":value.to_mapping()}
    monkeypatch.setattr(desktop_qualify,"FileBridgeClient",Fixture)
    with pytest.raises(RuntimeError,match="cannot disable"):
        desktop_qualify.qualify_runtime(tmp_path,require_local_route=False,timeout_seconds=1)

def test_legacy_spec_cannot_downgrade_attested_restricted_stage(tmp_path, monkeypatch):
    import json
    from portal import desktop_host
    from portal.runtime_source_attestation import FILENAME
    value=spec()
    (tmp_path/FILENAME).write_text(json.dumps({"model_admission":value.model_admission.to_mapping()}))
    legacy=replace(value,schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",model_admission=None)
    # Reject before any route discovery, host state open, or model invocation.
    with pytest.raises(RuntimeError,match="restricted"):
        desktop_host._verify_host_model_admission(tmp_path,legacy)

def test_legacy_attestation_compatibility_and_link_refusal(tmp_path):
    import json
    from portal import desktop_host
    from portal.runtime_source_attestation import FILENAME
    legacy=replace(spec(),schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",model_admission=None)
    desktop_host._verify_host_model_admission(tmp_path,legacy)
    path=tmp_path/FILENAME
    path.write_text(json.dumps({"schema":"PORTAL_RUNTIME_SOURCE_ATTESTATION_V1"}))
    desktop_host._verify_host_model_admission(tmp_path,legacy)
    path.unlink()
    other=tmp_path/"other.json"
    other.write_text("{}")
    try:
        path.symlink_to(other)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(RuntimeError,match="redirected"):
        desktop_host._verify_host_model_admission(tmp_path,legacy)
