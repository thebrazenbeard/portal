from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from portal.host_command_driver import PortalCommandHostDriver


def _dispatch() -> dict[str, object]:
    return {
        "dispatch_id": "dispatch-123",
        "session_id": "portfolio",
        "run_id": "portfolio::g1",
        "subject_kind": "repository",
        "subject_id": "portal",
        "adapter_id": "workbridge",
        "route_id": "WorkLaptop:bridge",
        "node_id": "worklaptop",
        "repository": "thebrazenbeard/portal",
        "ref": "work/portal-coordinator-v1",
        "exact_head": "a" * 40,
        "effect_ceiling": "SOURCE_ONLY",
        "action": "ADVANCE",
        "frontier": "advance the bounded source frontier",
    }


def _response(
    dispatch: dict[str, object],
    *,
    attempt_id: str = "attempt-1",
    state: str = "VERIFIED_COMPLETE",
    evidence_id: str = "workbridge:receipt-1",
) -> dict[str, object]:
    return {
        "schema": "PORTAL_HOST_DRIVER_RESULT_V1",
        "attempt_id": attempt_id,
        "dispatch_id": dispatch["dispatch_id"],
        "adapter_id": dispatch["adapter_id"],
        "route_id": dispatch["route_id"],
        "subject_kind": dispatch["subject_kind"],
        "subject_id": dispatch["subject_id"],
        "state": state,
        "evidence_id": evidence_id,
    }


def test_command_driver_sends_exact_bound_request_and_accepts_verified_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dispatch = _dispatch()
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_response(dispatch)),
            stderr="",
        )

    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        fake_run,
    )

    driver = PortalCommandHostDriver(
        command=("adapter-wrapper", "--stdio"),
        timeout_seconds=12.0,
        cwd=tmp_path,
    )
    result = driver.execute(dispatch, attempt_id="attempt-1")

    assert result.state == "VERIFIED_COMPLETE"
    assert result.evidence_id == "workbridge:receipt-1"
    assert captured["command"] == ("adapter-wrapper", "--stdio")
    assert captured["shell"] is False
    assert captured["timeout"] == 12.0
    assert captured["cwd"] == tmp_path

    request = json.loads(str(captured["input"]))
    assert request == {
        "schema": "PORTAL_HOST_DRIVER_REQUEST_V1",
        "attempt_id": "attempt-1",
        "dispatch": dispatch,
    }


def test_command_driver_does_not_promote_process_exit_to_completion(
    monkeypatch,
) -> None:
    dispatch = _dispatch()

    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="{}",
            stderr="",
        ),
    )

    driver = PortalCommandHostDriver(command=("adapter-wrapper",))
    with pytest.raises(ValueError, match="driver result schema"):
        driver.execute(dispatch, attempt_id="attempt-1")


def test_command_driver_rejects_mismatched_attempt_or_route(
    monkeypatch,
) -> None:
    dispatch = _dispatch()
    bad = _response(dispatch, attempt_id="other-attempt")
    bad["route_id"] = "other-route"

    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(bad),
            stderr="",
        ),
    )

    driver = PortalCommandHostDriver(command=("adapter-wrapper",))
    with pytest.raises(ValueError, match="attempt_id"):
        driver.execute(dispatch, attempt_id="attempt-1")


def test_command_driver_rejects_cross_subject_evidence(
    monkeypatch,
) -> None:
    dispatch = _dispatch()
    bad = _response(dispatch)
    bad["subject_id"] = "tattler"

    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(bad),
            stderr="",
        ),
    )

    driver = PortalCommandHostDriver(command=("adapter-wrapper",))
    with pytest.raises(ValueError, match="subject_id"):
        driver.execute(dispatch, attempt_id="attempt-1")


def test_command_driver_nonzero_exit_is_not_treated_as_result(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=17,
            stdout="",
            stderr="adapter failed",
        ),
    )

    driver = PortalCommandHostDriver(command=("adapter-wrapper",))
    with pytest.raises(RuntimeError, match="exit code 17"):
        driver.execute(_dispatch(), attempt_id="attempt-1")


def test_command_driver_invalid_json_is_rejected(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="not-json",
            stderr="",
        ),
    )

    driver = PortalCommandHostDriver(command=("adapter-wrapper",))
    with pytest.raises(ValueError, match="valid JSON"):
        driver.execute(_dispatch(), attempt_id="attempt-1")


def test_command_driver_timeout_propagates_for_host_pump_to_mark_unknown(
    monkeypatch,
) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=3.0)

    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        timeout,
    )

    driver = PortalCommandHostDriver(
        command=("adapter-wrapper",),
        timeout_seconds=3.0,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        driver.execute(_dispatch(), attempt_id="attempt-1")


@pytest.mark.parametrize(
    "command",
    [
        (),
        ("",),
        ("ok", ""),
    ],
)
def test_command_driver_requires_nonempty_argv(command) -> None:
    with pytest.raises(ValueError, match="command"):
        PortalCommandHostDriver(command=command)


def test_command_driver_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        PortalCommandHostDriver(
            command=("adapter-wrapper",),
            timeout_seconds=0,
        )
