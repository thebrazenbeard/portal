from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest

from portal.desktop_readonly_receipts import (
    BridgeIntentConflict,
    ReadOnlyBridgeClient,
)


def _response(request_id: str, *, result: object = None) -> dict[str, object]:
    return {
        "schema": "VERA_UNIFIED_BRIDGE_RESPONSE_V1",
        "request_id": request_id,
        "runtime_id": "observed-runtime-1",
        "ok": True,
        "result": result if result is not None else {
            "state": "ACTIVE", "protected_effect_authority": False,
        },
    }


def _path(root: Path, directory: str, request_id: str) -> Path:
    path = root / "bridge" / directory / f"{request_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_readonly_status_survives_lost_client_and_retrieves_same_response(
    tmp_path: Path,
) -> None:
    first = ReadOnlyBridgeClient(tmp_path)
    first.submit_once("stable-1")
    request = _path(tmp_path, "requests", "stable-1")
    claimed = _path(tmp_path, "claimed", "stable-1")
    request.replace(claimed)
    response = _path(tmp_path, "responses", "stable-1")
    response.write_text(json.dumps(_response("stable-1")), encoding="utf-8")
    claimed.unlink()  # Existing host clears its claim after writing response.

    # Model a fresh client process with no in-memory state or extra request.
    restored = ReadOnlyBridgeClient(tmp_path)
    restored.submit_once("stable-1")
    assert not request.exists()
    observed = restored.observe("stable-1")
    assert observed.state == "OBSERVED_SUCCESS_UNVERIFIED_TRANSPORT"
    assert observed.result == {"state": "ACTIVE", "protected_effect_authority": False}
    assert observed.producer_authenticated is False
    assert observed.protected_effect_authority is False
    assert response.exists()  # An observation does not destroy its evidence.


def test_prepositioned_unclaimed_success_does_not_return_result(tmp_path: Path) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("forged-1")
    response = _path(tmp_path, "responses", "forged-1")
    response.write_text(json.dumps(_response("forged-1", result={"success": True})))
    observed = client.observe("forged-1")
    assert observed.state == "UNATTRIBUTED_RESPONSE"
    assert observed.result is None
    assert response.exists()
    assert _path(tmp_path, "requests", "forged-1").exists()


def test_wrong_id_response_is_preserved_and_refused(tmp_path: Path) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("wrong-1")
    _path(tmp_path, "requests", "wrong-1").replace(
        _path(tmp_path, "claimed", "wrong-1")
    )
    response = _path(tmp_path, "responses", "wrong-1")
    response.write_text(json.dumps(_response("someone-else")))
    assert client.observe("wrong-1").state == "INVALID_RESPONSE_PRESERVED"
    assert response.exists()


def test_malformed_response_is_preserved_without_escalation(tmp_path: Path) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("malformed-1")
    _path(tmp_path, "requests", "malformed-1").replace(
        _path(tmp_path, "claimed", "malformed-1")
    )
    response = _path(tmp_path, "responses", "malformed-1")
    response.write_text("not-json", encoding="utf-8")
    assert client.observe("malformed-1").state == "INVALID_RESPONSE_PRESERVED"
    assert response.read_text(encoding="utf-8") == "not-json"


def test_deeply_nested_json_response_is_invalid_without_escaping(
    tmp_path: Path,
) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("nested-1")
    _path(tmp_path, "requests", "nested-1").replace(
        _path(tmp_path, "claimed", "nested-1")
    )
    response = _path(tmp_path, "responses", "nested-1")
    malicious = "[" * 10000 + "0" + "]" * 10000
    assert len(malicious) < 1024 * 1024
    response.write_text(malicious, encoding="utf-8")
    observed = client.observe("nested-1")
    assert observed.state == "INVALID_RESPONSE_PRESERVED"
    assert observed.result is None
    assert response.read_text(encoding="utf-8") == malicious


def test_claimed_response_cannot_self_assert_protected_effect_authority(
    tmp_path: Path,
) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("false-authority-1")
    _path(tmp_path, "requests", "false-authority-1").replace(
        _path(tmp_path, "claimed", "false-authority-1")
    )
    response = _path(tmp_path, "responses", "false-authority-1")
    response.write_text(json.dumps(_response(
        "false-authority-1", result={"protected_effect_authority": True},
    )))
    observed = client.observe("false-authority-1")
    assert observed.state == "INVALID_RESPONSE_PRESERVED"
    assert observed.result is None
    assert observed.protected_effect_authority is False


def test_crash_after_claim_never_reenqueues_unknown_operation(tmp_path: Path) -> None:
    first = ReadOnlyBridgeClient(tmp_path)
    first.submit_once("claimed-1")
    request = _path(tmp_path, "requests", "claimed-1")
    claimed = _path(tmp_path, "claimed", "claimed-1")
    request.replace(claimed)
    restored = ReadOnlyBridgeClient(tmp_path)
    assert restored.observe("claimed-1").state == "OUTCOME_UNKNOWN"
    restored.submit_once("claimed-1")
    assert not request.exists()
    assert claimed.exists()


def test_missing_response_after_claim_cleanup_is_still_unknown(tmp_path: Path) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("lost-1")
    _path(tmp_path, "requests", "lost-1").unlink()
    assert client.observe("lost-1").state == "OUTCOME_UNKNOWN"
    client.submit_once("lost-1")
    assert not _path(tmp_path, "requests", "lost-1").exists()


def test_timeout_does_not_delete_or_replay_unclaimed_request(tmp_path: Path) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("slow-1")
    observed = client.wait("slow-1", timeout_seconds=0.01, poll_seconds=0.001)
    assert observed.state == "PENDING_UNCLAIMED"
    client.submit_once("slow-1")
    assert _path(tmp_path, "requests", "slow-1").exists()


def test_duplicate_intent_fails_closed_on_changed_or_partial_file(tmp_path: Path) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("bound-1")
    intent = _path(tmp_path, "readonly_intents", "bound-1")
    payload = json.loads(intent.read_text(encoding="utf-8"))
    payload["request_sha256"] = "bad"
    intent.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BridgeIntentConflict):
        ReadOnlyBridgeClient(tmp_path).submit_once("bound-1")
    intent.write_text("{", encoding="utf-8")
    with pytest.raises(BridgeIntentConflict):
        ReadOnlyBridgeClient(tmp_path).submit_once("bound-1")


def test_readonly_command_allowlist_and_path_validation(tmp_path: Path) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    for command in ("shutdown", "desktop_cognize", "portal_status"):
        with pytest.raises(PermissionError):
            client.submit_once("blocked", command=command)
    for request_id in ("../oops", "a/b", "", "*", "a" * 129):
        with pytest.raises(ValueError):
            client.submit_once(request_id)
    assert not (tmp_path / "bridge" / "requests").exists()


def test_wait_observes_late_host_result_without_discarding_it(tmp_path: Path) -> None:
    client = ReadOnlyBridgeClient(tmp_path)
    client.submit_once("late-1")

    def host() -> None:
        time.sleep(0.02)
        _path(tmp_path, "requests", "late-1").replace(
            _path(tmp_path, "claimed", "late-1")
        )
        _path(tmp_path, "responses", "late-1").write_text(
            json.dumps(_response("late-1")), encoding="utf-8"
        )
        _path(tmp_path, "claimed", "late-1").unlink()

    thread = threading.Thread(target=host)
    thread.start()
    try:
        observed = client.wait("late-1", timeout_seconds=1, poll_seconds=0.005)
    finally:
        thread.join(timeout=1)
    assert observed.state == "OBSERVED_SUCCESS_UNVERIFIED_TRANSPORT"
    assert _path(tmp_path, "responses", "late-1").exists()


def test_bridge_symlink_ancestor_cannot_redirect_an_intent_outside_root(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (runtime / "bridge").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable to this user/platform")
    with pytest.raises(BridgeIntentConflict, match="symlink/junction"):
        ReadOnlyBridgeClient(runtime)
    assert not list(outside.iterdir())


def test_runtime_parent_symlink_ancestor_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "runtime").mkdir(parents=True)
    try:
        (tmp_path / "linked_parent").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable to this user/platform")
    with pytest.raises(BridgeIntentConflict, match="symlink/junction"):
        ReadOnlyBridgeClient(tmp_path / "linked_parent" / "runtime")
    assert not (outside / "runtime" / "bridge").exists()


def test_intent_subdirectory_symlink_cannot_redirect_a_write(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    bridge = runtime / "bridge"
    bridge.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (bridge / "readonly_intents").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable to this user/platform")
    client = ReadOnlyBridgeClient(runtime)
    with pytest.raises(BridgeIntentConflict, match="symlink/junction"):
        client.submit_once("redirect-1")
    assert not list(outside.iterdir())


def test_response_subdirectory_symlink_cannot_redirect_a_read(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    client = ReadOnlyBridgeClient(runtime)
    client.submit_once("redirect-read-1")
    outside = tmp_path / "outside"
    outside.mkdir()
    response_dir = runtime / "bridge" / "responses"
    if response_dir.exists():
        response_dir.rmdir()
    try:
        response_dir.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable to this user/platform")
    with pytest.raises(BridgeIntentConflict, match="symlink/junction"):
        client.observe("redirect-read-1")
