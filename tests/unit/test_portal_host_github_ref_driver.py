from __future__ import annotations

import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from portal import host_github_ref_driver as driver


def dispatch():
    return {
        "dispatch_id": "dispatch-1",
        "adapter_id": "lappy-local-readonly-gh",
        "route_id": "local-gh:portal:main",
        "subject_kind": "repository",
        "subject_id": "portal",
        "repository": "thebrazenbeard/portal",
        "ref": "main",
        "exact_head": "a" * 40,
        "effect_ceiling": "SOURCE_ONLY",
        "action": "VERIFY_EXACT_HEAD",
    }


def verify(row, monkeypatch, live="a" * 40):
    monkeypatch.setattr(driver, "read_live_head", lambda repo, ref: live)
    return driver.verify_dispatch(
        attempt_id="attempt-1", dispatch=row,
        allowed_repositories=frozenset({"thebrazenbeard/portal"}),
        required_adapter_id="lappy-local-readonly-gh",
    )


def test_live_exact_head_verification_echoes_original_binding(monkeypatch):
    r = verify(dispatch(), monkeypatch)
    assert r["state"] == "VERIFIED_COMPLETE"
    assert r["evidence_id"] == "github-readonly-ref:thebrazenbeard/portal:" + "a" * 40
    for field in driver._ECHO:
        assert r[field] == dispatch()[field]
    assert r["attempt_id"] == "attempt-1"


def test_head_movement_is_explicitly_held(monkeypatch):
    r = verify(dispatch(), monkeypatch, "b" * 40)
    assert r["state"] == "VERIFIED_HELD"
    assert r["evidence_id"].endswith("b" * 40)


@pytest.mark.parametrize("field,value", [
    ("repository", "thebrazenbeard/other"),
    ("repository", "thebrazenbeard/portal/../../../secrets"),
    ("subject_id", "other"),
    ("subject_kind", "process"),
    ("adapter_id", "someone-else"),
    ("route_id", "other:route"),
    ("ref", "refs/heads/develop"),
    ("exact_head", "not-a-hash"),
    ("effect_ceiling", "SOURCE_WRITE"),
    ("action", "ADVANCE"),
])
def test_unqualified_target_and_effect_fail_closed(monkeypatch, field, value):
    row = dispatch()
    row[field] = value
    monkeypatch.setattr(driver, "read_live_head", lambda *_: pytest.fail("must not execute"))
    with pytest.raises(driver.ReadOnlyDriverError):
        verify(row, monkeypatch)


@pytest.mark.parametrize("data", [
    b"",
    b"{}",
    b'{"schema":"PORTAL_HOST_DRIVER_REQUEST_V1","attempt_id":"x","dispatch":{}}',
    b'{"schema":"PORTAL_HOST_DRIVER_REQUEST_V1","attempt_id":"x","attempt_id":"y","dispatch":{}}',
    b'{"schema":"PORTAL_HOST_DRIVER_REQUEST_V1","attempt_id":"x","dispatch":{"subject_id":"a","subject_id":"b"}}',
])
def test_invalid_and_ambiguous_requests_are_rejected(data):
    with pytest.raises(driver.ReadOnlyDriverError):
        driver.decode_request(data)


def test_oversize_request_is_rejected():
    with pytest.raises(driver.ReadOnlyDriverError, match="size"):
        driver.decode_request(b"x" * (driver._MAX_INPUT_BYTES + 1))

def test_decoder_rejects_duplicate_keys_and_preserves_exact_echo():
    req = {"schema": "PORTAL_HOST_DRIVER_REQUEST_V1",
           "attempt_id": "attempt-1", "dispatch": dispatch()}
    attempt, obj = driver.decode_request(json.dumps(req).encode())
    assert attempt == "attempt-1"
    assert obj == dispatch()


def test_github_command_is_argv_only_and_read_only(monkeypatch):
    observed = {}

    def gh(args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ref":"refs/heads/main","object":{"type":"commit","sha":"c"*40}}))
    monkeypatch.setattr(subprocess, "run", gh)
    assert driver.read_live_head("thebrazenbeard/portal", "main") == "c"*40
    assert observed["args"] == [
        "gh", "api", "repos/thebrazenbeard/portal/git/ref/heads/main"
    ]
    assert observed["kwargs"]["shell"] is False


def test_transport_error_is_not_a_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=1, stdout=""))
    with pytest.raises(driver.ReadOnlyDriverError, match="lookup failed"):
        driver.read_live_head("thebrazenbeard/portal", "main")


def test_process_cli_returns_strict_result(monkeypatch, capsys):
    req = {"schema":"PORTAL_HOST_DRIVER_REQUEST_V1",
           "attempt_id":"attempt-1","dispatch":dispatch()}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(req).encode())))
    monkeypatch.setattr(driver, "read_live_head", lambda *_: "a"*40)
    assert driver.main(["--allowed-repository","thebrazenbeard/portal"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["state"] == "VERIFIED_COMPLETE"


def test_process_cli_does_not_emit_success_on_wrong_target(monkeypatch, capsys):
    row = dispatch()
    row["repository"] = "thebrazenbeard/other"
    req = {"schema":"PORTAL_HOST_DRIVER_REQUEST_V1",
           "attempt_id":"attempt-1","dispatch":row}
    monkeypatch.setattr(sys,"stdin",io.TextIOWrapper(io.BytesIO(json.dumps(req).encode())))
    assert driver.main(["--allowed-repository","thebrazenbeard/portal"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "HELD" in captured.err
@pytest.mark.parametrize("ref,kind", [
    ("refs/heads/other", "commit"),
    ("refs/heads/main", "tag"),
    ("refs/tags/main", "commit"),
])
def test_github_ref_response_rejects_wrong_target_or_object_type(monkeypatch, ref, kind):
    def gh(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "ref": ref,
                "object": {"type": kind, "sha": "c" * 40},
            }),
        )

    monkeypatch.setattr(subprocess, "run", gh)
    with pytest.raises(driver.ReadOnlyDriverError):
        driver.read_live_head("thebrazenbeard/portal", "main")