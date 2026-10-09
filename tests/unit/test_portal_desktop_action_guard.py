"""Desktop controls must not start unsafe or pointless worker requests."""
import json
from pathlib import Path
import pytest
from portal.desktop_action_guard import load_operator_pins, prepare_portfolio_request

DIGESTS={"command_sha256":"a"*64,
 "interpreter_sha256":"b"*64,"worker_sha256":"c"*64}

def test_requires_explicit_operator_pin_file_and_maps_resident_keys(tmp_path:Path):
    f=tmp_path/"observed.json"
    f.write_text(json.dumps({"source":"operator-observed, not independent review",
                            "pins":DIGESTS}))
    result=load_operator_pins(f)
    assert result == {"expected_command_sha256":"a"*64,
        "expected_interpreter_sha256":"b"*64,"expected_worker_sha256":"c"*64}

@pytest.mark.parametrize("pins", [
    {}, {"command_sha256":"a"*64}, {**DIGESTS,"worker_sha256":"G"*64},
    {**DIGESTS,"unexpected":"a"*64}
])
def test_invalid_pin_manifests_rejected(tmp_path,pins):
    p=tmp_path/"pins.json";p.write_text(json.dumps({"pins":pins}))
    with pytest.raises(ValueError,match="pins"):
        load_operator_pins(p)

def status(*,control="STOPPED",subjects=(),worker="CONFIGURED"):
    return {"configured":True,"worker_state":worker,
       "session":{"control_state":control,"subjects":list(subjects)}}

def test_stopped_all_held_never_runs_even_with_pins():
    blocked=prepare_portfolio_request("run",status(subjects=(
        {"state":"HELD","subject_id":"firesafe"},)),pins={
        "expected_command_sha256":"a"*64,
        "expected_interpreter_sha256":"b"*64,
        "expected_worker_sha256":"c"*64})
    assert not blocked.allowed and "held" in blocked.explanation.lower()

def test_run_against_active_session_must_not_restart_it():
    result=prepare_portfolio_request("run",status(control="RUNNING",
        subjects=({"state":"ACTIVE","subject_id":"x"},)),pins=None)
    assert not result.allowed and "continue" in result.explanation.lower()

def test_continue_cannot_restart_stopped_session():
    result=prepare_portfolio_request("continue",status(),pins=None)
    assert not result.allowed and "stopped" in result.explanation.lower()
def test_run_requires_operator_pins_if_worker_is_configured():
    result=prepare_portfolio_request("run",status(subjects=()),pins=None)
    assert not result.allowed and "pin" in result.explanation.lower()

def test_run_includes_only_explicit_pins_for_eligible_new_session():
    pins={f"expected_{name}_sha256":d for name,d in [
        ("command","a"*64),("interpreter","b"*64),("worker","c"*64)]}
    result=prepare_portfolio_request("run",status(subjects=()),pins=pins)
    assert result.allowed
    assert result.payload==pins

def test_readonly_status_unaffected():
    result=prepare_portfolio_request("status",status(),pins=None)
    assert result.allowed and result.payload=={}
