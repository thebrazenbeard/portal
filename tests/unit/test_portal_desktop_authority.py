from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from portal.desktop_authority import DesktopAuthorityService
from runner.execution_promotion import (
    NO_PROTECTED_EFFECT,
    parse_effect_grant,
    parse_execution_grant,
)


EXECUTION_KEY = b"desktop-execution-key"
EFFECT_KEY = b"desktop-effect-key"


def _write_claim_db(
    path: Path,
    *,
    effect_ceiling: str = "SOURCE_ONLY",
    now: float = 100.0,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE recursive_work_state (
                lineage_id TEXT NOT NULL,
                work_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                generation INTEGER NOT NULL,
                work_json TEXT NOT NULL,
                PRIMARY KEY (lineage_id, work_fingerprint)
            );
            CREATE TABLE leases (
                work_fingerprint TEXT PRIMARY KEY,
                holder TEXT NOT NULL,
                fencing_token INTEGER NOT NULL,
                expires_at REAL NOT NULL,
                completed INTEGER NOT NULL
            );
            """
        )
        claim_payload = {
            "schema": "PROJECT_RUNNER_BOUND_PLAN_CLAIM_V1",
            "subject_id": "portal",
            "repository": "thebrazenbeard/portal",
            "ref": "main",
            "exact_head": "a" * 40,
            "plan_sha256": "b" * 64,
            "wave_sha256": "c" * 64,
            "execution_authority": False,
            "protected_effects_authorized": False,
            "selected": {
                "subject_id": "portal",
                "action": "EXECUTE_FRONTIER",
                "effect_ceiling": effect_ceiling,
                "review_gate": "EXACT_HEAD_REVIEW",
                "reviewer_identities": ["REZON"],
            },
        }
        work_json = {
            "id": "desktop-authority-test",
            "operation": "PORTFOLIO_BOUND_CLAIM",
            "payload": claim_payload,
        }
        connection.execute(
            """
            INSERT INTO recursive_work_state(
                lineage_id, work_fingerprint, status, generation, work_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "lineage-1",
                "d" * 64,
                "CLAIMED",
                2,
                json.dumps(work_json),
            ),
        )
        connection.execute(
            """
            INSERT INTO leases(
                work_fingerprint, holder, fencing_token, expires_at, completed
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("d" * 64, "desktop-holder", 7, now + 600.0, 0),
        )
        connection.commit()
    finally:
        connection.close()


def _write_request(
    runtime_root: Path,
    *,
    effect_class: str,
    execution_request: dict[str, object] | None,
) -> Path:
    inbox = runtime_root / "state" / "portal" / "authority-requests"
    inbox.mkdir(parents=True)
    path = inbox / "request-1.json"
    path.write_text(
        json.dumps(
            {
                "schema": "PORTAL_DESKTOP_AUTHORITY_REQUEST_V1",
                "request_id": "request-1",
                "subject_id": "portal",
                "repository": "thebrazenbeard/portal",
                "ref": "main",
                "exact_head": "a" * 40,
                "lineage_id": "lineage-1",
                "work_fingerprint": "d" * 64,
                "fencing_token": 7,
                "holder": "desktop-holder",
                "operation": "EXECUTE_FRONTIER",
                "effect_class": effect_class,
                "execution_request": execution_request,
                "state_db": "state/portal/operator.sqlite3",
                "target": "thebrazenbeard/portal@main",
                "summary": "Approve exact claimed work.",
                "source": "project-runner",
                "created_at": 100.0,
                "state": "PENDING",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_create_request_from_exact_claim_populates_desktop_inbox(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    db = runtime_root / "state" / "portal" / "operator.sqlite3"
    db.parent.mkdir(parents=True)
    _write_claim_db(db)
    service = DesktopAuthorityService(
        runtime_root,
        execution_key=EXECUTION_KEY,
        effect_key=EFFECT_KEY,
    )
    execution_request = {
        "schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1",
        "operation": "PUT_FILE",
        "repository": "thebrazenbeard/portal",
        "ref": "main",
        "expected_head": "a" * 40,
        "path": "docs/exact.txt",
        "content": "x\n",
        "message": "Exact write",
        "expected_blob_sha": None,
    }

    result = service.create_request(
        state_db=db,
        subject_id="portal",
        repository="thebrazenbeard/portal",
        ref="main",
        exact_head="a" * 40,
        lineage_id="lineage-1",
        work_fingerprint="d" * 64,
        fencing_token=7,
        holder="desktop-holder",
        operation="EXECUTE_FRONTIER",
        effect_class="SOURCE_WRITE",
        execution_request=execution_request,
        target="thebrazenbeard/portal@main",
        summary="Write exact reviewed source change.",
        source="project-runner",
        now=100.0,
    )

    request_path = Path(result["request_path"])
    assert request_path.is_file()
    payload = json.loads(request_path.read_text("utf-8"))
    assert payload["request_id"] == result["request_id"]
    assert payload["state_db"] == "state/portal/operator.sqlite3"
    assert payload["execution_request"] == execution_request
    assert payload["state"] == "PENDING"


def test_approve_creates_real_execution_and_effect_grants_and_resolves_request(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    db = runtime_root / "state" / "portal" / "operator.sqlite3"
    db.parent.mkdir(parents=True)
    _write_claim_db(db)
    execution_request = {
        "schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1",
        "operation": "PUT_FILE",
        "repository": "thebrazenbeard/portal",
        "ref": "main",
        "expected_head": "a" * 40,
        "path": "docs/desktop-authority.txt",
        "content": "approved\n",
        "message": "Desktop authority test",
        "expected_blob_sha": None,
    }
    _write_request(
        runtime_root,
        effect_class="SOURCE_WRITE",
        execution_request=execution_request,
    )
    service = DesktopAuthorityService(
        runtime_root,
        execution_key=EXECUTION_KEY,
        effect_key=EFFECT_KEY,
    )

    result = service.approve(
        "request-1",
        issuer="patrick-desktop",
        now=100.0,
        valid_for_seconds=300.0,
    )

    execution = json.loads(Path(result["execution_grant"]).read_text("utf-8"))
    effect = json.loads(Path(result["effect_grant"]).read_text("utf-8"))
    parsed_execution = parse_execution_grant(execution, key=EXECUTION_KEY)
    parsed_effect = parse_effect_grant(effect, key=EFFECT_KEY)

    assert parsed_execution.issuer == "patrick-desktop"
    assert parsed_execution.execution_request == execution_request
    assert parsed_execution.effect_class == "SOURCE_WRITE"
    assert parsed_execution.valid_until == 400.0
    assert parsed_effect.effect_class == "SOURCE_WRITE"
    assert (
        parsed_effect.execution_request_sha256
        == parsed_execution.execution_request_sha256
    )
    assert not (
        runtime_root
        / "state"
        / "portal"
        / "authority-requests"
        / "request-1.json"
    ).exists()
    assert (
        runtime_root
        / "state"
        / "portal"
        / "authority-requests"
        / "resolved"
        / "approved"
        / "request-1.json"
    ).is_file()


def test_approve_no_effect_requires_only_execution_key(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    db = runtime_root / "state" / "portal" / "operator.sqlite3"
    db.parent.mkdir(parents=True)
    _write_claim_db(db, effect_ceiling="NO_EFFECT")
    _write_request(
        runtime_root,
        effect_class=NO_PROTECTED_EFFECT,
        execution_request=None,
    )
    service = DesktopAuthorityService(
        runtime_root,
        execution_key=EXECUTION_KEY,
        effect_key=None,
    )

    result = service.approve("request-1", now=100.0)

    assert Path(result["execution_grant"]).is_file()
    assert result["effect_grant"] is None


def test_approval_uses_user_bound_key_store_when_environment_is_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    db = runtime_root / "state" / "portal" / "operator.sqlite3"
    db.parent.mkdir(parents=True)
    _write_claim_db(db)
    execution_request = {
        "schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1",
        "operation": "PUT_FILE",
    }
    _write_request(
        runtime_root,
        effect_class="SOURCE_WRITE",
        execution_request=execution_request,
    )

    class FakeKeyStore:
        def load(self, kind: str) -> bytes | None:
            return {
                "execution": EXECUTION_KEY,
                "protected_effect": EFFECT_KEY,
            }.get(kind)

    monkeypatch.delenv(
        "PROJECT_RUNNER_EXECUTION_AUTHORITY_KEY",
        raising=False,
    )
    monkeypatch.delenv(
        "PROJECT_RUNNER_PROTECTED_EFFECT_AUTHORITY_KEY",
        raising=False,
    )
    service = DesktopAuthorityService(
        runtime_root,
        key_store=FakeKeyStore(),
    )

    result = service.approve("request-1", now=100.0)

    execution = json.loads(Path(result["execution_grant"]).read_text("utf-8"))
    effect = json.loads(Path(result["effect_grant"]).read_text("utf-8"))
    assert parse_execution_grant(execution, key=EXECUTION_KEY).issuer
    assert parse_effect_grant(effect, key=EFFECT_KEY).issuer


def test_protected_approval_fails_closed_without_effect_key(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    db = runtime_root / "state" / "portal" / "operator.sqlite3"
    db.parent.mkdir(parents=True)
    _write_claim_db(db)
    _write_request(
        runtime_root,
        effect_class="SOURCE_WRITE",
        execution_request={"schema": "ANY"},
    )
    service = DesktopAuthorityService(
        runtime_root,
        execution_key=EXECUTION_KEY,
        effect_key=None,
    )

    with pytest.raises(ValueError, match="protected-effect authority key"):
        service.approve("request-1", now=100.0)


def test_approval_refuses_request_that_does_not_match_durable_claim(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    db = runtime_root / "state" / "portal" / "operator.sqlite3"
    db.parent.mkdir(parents=True)
    _write_claim_db(db)
    request_path = _write_request(
        runtime_root,
        effect_class="SOURCE_WRITE",
        execution_request={"schema": "ANY"},
    )
    payload = json.loads(request_path.read_text("utf-8"))
    payload["exact_head"] = "f" * 40
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    service = DesktopAuthorityService(
        runtime_root,
        execution_key=EXECUTION_KEY,
        effect_key=EFFECT_KEY,
    )

    with pytest.raises(ValueError, match="exact head"):
        service.approve("request-1", now=100.0)


def test_deny_resolves_request_without_minting_authority(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    db = runtime_root / "state" / "portal" / "operator.sqlite3"
    db.parent.mkdir(parents=True)
    _write_claim_db(db)
    _write_request(
        runtime_root,
        effect_class="SOURCE_WRITE",
        execution_request={"schema": "ANY"},
    )
    service = DesktopAuthorityService(
        runtime_root,
        execution_key=EXECUTION_KEY,
        effect_key=EFFECT_KEY,
    )

    result = service.deny("request-1", now=100.0)

    assert result["decision"] == "DENIED"
    assert not (runtime_root / "state" / "portal" / "authority-grants").exists()
    assert (
        runtime_root
        / "state"
        / "portal"
        / "authority-requests"
        / "resolved"
        / "denied"
        / "request-1.json"
    ).is_file()
