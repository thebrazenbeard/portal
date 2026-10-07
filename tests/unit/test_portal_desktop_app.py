from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from portal.desktop_app import DesktopViewModel
from portal.desktop_state import DesktopStateStore


@dataclass
class FakeAuthorityService:
    calls: list[tuple[str, str, dict[str, object]]]

    def approve(self, request_id: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("approve", request_id, dict(kwargs)))
        return {
            "decision": "APPROVED",
            "execution_grant": "execution-grant.json",
            "effect_grant": "effect-grant.json",
            "execution_performed": False,
        }

    def deny(self, request_id: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("deny", request_id, dict(kwargs)))
        return {
            "decision": "DENIED",
            "execution_performed": False,
        }


@dataclass
class FakeClient:
    calls: list[tuple[str, dict[str, object]]]

    def request(self, command: str, **payload: object) -> dict[str, object]:
        self.calls.append((command, payload))
        if command == "desktop_status":
            return {
                "state": "ACTIVE",
                "runtime_id": "r1",
                "components": {"vera_mono": {"loaded": True}},
            }
        if command == "desktop_discover_routes":
            return {
                "routes": [
                    {
                        "route_id": "codex:cli",
                        "local": False,
                        "available": True,
                        "current": True,
                        "incremental_paid_compute": None,
                        "auto_admissible": False,
                        "preference": 50,
                    },
                    {
                        "route_id": "ollama:vera-local:latest",
                        "local": True,
                        "available": True,
                        "current": True,
                        "incremental_paid_compute": False,
                        "auto_admissible": True,
                        "preference": 10,
                    },
                ]
            }
        if command == "desktop_recent_activity":
            return {
                "items": [
                    {
                        "request_id": "auto-1",
                        "source": "PRE_ACTIVE_AUTONOMOUS_TURN",
                        "reason": "open loop",
                        "state": "COMPLETED",
                        "route_id": "ollama:vera-local:latest",
                        "updated_at": 100.0,
                        "protected_effect_authority": False,
                    }
                ]
            }
        if command == "desktop_cognize":
            return {
                "request_id": payload["request_id"],
                "state": "COMPLETED",
                "route_id": "ollama:vera-local:latest",
                "response_text": "hello Patrick",
                "retryable": False,
                "evidence_id": "cognition:x",
                "protected_effect_authority": False,
            }
        raise AssertionError(command)


def test_resolve_runtime_root_uses_discovered_active_desktop_runtime(
    tmp_path: Path,
) -> None:
    import portal.desktop_app as desktop_app

    base = tmp_path / "VeraDesktopRuntime" / "active"
    base.mkdir(parents=True)
    (base / "RUNTIME_INSTALL_SPEC.json").write_text(
        json.dumps(
            {
                "schema": "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
                "activation": {
                    "requested": True,
                    "qualified": True,
                    "active": True,
                },
            }
        ),
        encoding="utf-8",
    )
    (base / "QUALIFICATION.json").write_text(
        json.dumps({"qualified": True}),
        encoding="utf-8",
    )
    (base / "INSTALLATION_RESULT.json").write_text(
        json.dumps({"installed_at": 100.0}),
        encoding="utf-8",
    )

    resolved = desktop_app.resolve_runtime_root(
        configured=None,
        local_app_data=tmp_path,
    )

    assert resolved == base.resolve()


def test_view_model_refresh_reports_runtime_route_and_last_autonomous_activity() -> None:
    client = FakeClient([])
    vm = DesktopViewModel(client)

    snapshot = vm.refresh()

    assert snapshot.runtime_state == "ACTIVE"
    assert snapshot.runtime_id == "r1"
    assert snapshot.current_route == "ollama:vera-local:latest"
    assert snapshot.last_autonomous_activity is not None
    assert snapshot.last_autonomous_activity["request_id"] == "auto-1"
    assert snapshot.pending_effects == ()


def test_view_model_send_message_uses_ipc_runtime_not_direct_model() -> None:
    client = FakeClient([])
    vm = DesktopViewModel(client)

    response = vm.send_message(
        "hello",
        request_id="human-fixed",
        created_at=123.0,
    )

    assert response["response_text"] == "hello Patrick"
    command, payload = client.calls[-1]
    assert command == "desktop_cognize"
    assert payload["request_id"] == "human-fixed"
    assert payload["source"] == "HUMAN"
    assert payload["reason"] == "desktop conversation message"
    assert payload["task"] == "hello"


def test_view_model_persists_conversation_and_surfaces_portfolio_open_loops(
    tmp_path: Path,
) -> None:
    state = DesktopStateStore(tmp_path / "desktop-ui.sqlite3")
    corpus = {
        "corpus_id": "PROJECT_RUNNER_PORTFOLIO_CORPUS_V1",
        "observed_at": "2026-10-07T10:00:00-04:00",
        "freshness_rule": "refresh before currentness claims",
        "records": [
            {
                "id": "portal",
                "name": "P.O.R.T.A.L.",
                "repository": "thebrazenbeard/portal",
                "priority": "P0",
                "activity_state": "ACTIVE",
                "status": "Desktop source in progress.",
                "current_frontier": "Complete the desktop shell.",
            }
        ],
        "workstreams": [],
    }
    portfolio_path = tmp_path / "corpus.public.json"
    portfolio_path.write_text(json.dumps(corpus), encoding="utf-8")
    authority_path = tmp_path / "authority-requests"
    authority_path.mkdir()
    (authority_path / "install-1.json").write_text(
        json.dumps(
            {
                "schema": "PORTAL_DESKTOP_AUTHORITY_REQUEST_V1",
                "request_id": "install-1",
                "subject_id": "portal",
                "repository": "thebrazenbeard/portal",
                "ref": "main",
                "exact_head": "a" * 40,
                "lineage_id": "lineage-1",
                "work_fingerprint": "b" * 64,
                "fencing_token": 1,
                "holder": "desktop-holder",
                "operation": "EXECUTE_FRONTIER",
                "effect_class": "SOURCE_WRITE",
                "execution_request": {
                    "schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1"
                },
                "state_db": "state/portal/operator.sqlite3",
                "target": "thebrazenbeard/portal@main",
                "summary": "Write exact reviewed source change.",
                "source": "project-runner",
                "created_at": 122.0,
                "state": "PENDING",
            }
        ),
        encoding="utf-8",
    )
    try:
        client = FakeClient([])
        vm = DesktopViewModel(
            client,
            state_store=state,
            portfolio_path=portfolio_path,
            authority_requests_path=authority_path,
        )

        vm.send_message(
            "hello",
            request_id="human-fixed",
            created_at=123.0,
        )
        snapshot = vm.refresh()

        assert [item["role"] for item in snapshot.conversation] == [
            "human",
            "vera",
        ]
        assert snapshot.conversation[0]["content"] == "hello"
        assert snapshot.conversation[1]["content"] == "hello Patrick"
        assert snapshot.open_loops[0]["id"] == "portal"
        assert snapshot.portfolio_descriptive_only is True
        assert snapshot.portfolio_authority_granted is False
        assert snapshot.pending_effects[0]["request_id"] == "install-1"
        assert snapshot.pending_effects[0]["effect_class"] == "SOURCE_WRITE"
        assert snapshot.pending_effects[0]["request_is_authority"] is False
        assert (
            snapshot.pending_effects[0][
                "approval_mints_protected_effect_authority"
            ]
            is True
        )
    finally:
        state.close()


def test_view_model_approve_and_deny_delegate_to_real_authority_surface() -> None:
    client = FakeClient([])
    authority = FakeAuthorityService([])
    vm = DesktopViewModel(client, authority_service=authority)

    approved = vm.approve_authority(
        "request-1",
        now=200.0,
        valid_for_seconds=120.0,
    )
    denied = vm.deny_authority("request-2", now=201.0)

    assert approved["decision"] == "APPROVED"
    assert approved["execution_performed"] is False
    assert denied["decision"] == "DENIED"
    assert authority.calls == [
        (
            "approve",
            "request-1",
            {
                "now": 200.0,
                "valid_for_seconds": 120.0,
            },
        ),
        (
            "deny",
            "request-2",
            {
                "now": 201.0,
            },
        ),
    ]


def test_view_model_rejects_empty_message_without_ipc_call() -> None:
    client = FakeClient([])
    vm = DesktopViewModel(client)

    try:
        vm.send_message("   ")
    except ValueError as exc:
        assert "message" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert client.calls == []
