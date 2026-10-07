from __future__ import annotations

import json
from pathlib import Path

import portal.desktop_state as desktop_state


def test_desktop_state_store_persists_conversation_across_reopen(tmp_path: Path) -> None:
    store_cls = getattr(desktop_state, "DesktopStateStore", None)
    assert callable(store_cls)

    path = tmp_path / "desktop-ui.sqlite3"
    store = store_cls(path)
    try:
        store.append_message(
            role="human",
            content="hello",
            created_at=100.0,
            request_id="r1",
        )
        store.append_message(
            role="vera",
            content="hi Patrick",
            created_at=101.0,
            request_id="r1",
            state="COMPLETED",
            route_id="preactive-target:vera-base",
        )
    finally:
        store.close()

    reopened = store_cls(path)
    try:
        messages = reopened.list_messages(limit=10)
    finally:
        reopened.close()

    assert messages == (
        {
            "role": "human",
            "content": "hello",
            "created_at": 100.0,
            "request_id": "r1",
            "state": None,
            "route_id": None,
        },
        {
            "role": "vera",
            "content": "hi Patrick",
            "created_at": 101.0,
            "request_id": "r1",
            "state": "COMPLETED",
            "route_id": "preactive-target:vera-base",
        },
    )


def test_desktop_state_store_accepts_background_ui_worker_writes(
    tmp_path: Path,
) -> None:
    import threading

    store = desktop_state.DesktopStateStore(tmp_path / "desktop-ui.sqlite3")
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            store.append_message(
                role="human",
                content="from worker",
                created_at=100.0,
                request_id="threaded",
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=2)
    try:
        assert not thread.is_alive()
        assert errors == []
        assert store.list_messages(limit=1)[0]["content"] == "from worker"
    finally:
        store.close()


def test_load_portfolio_view_surfaces_active_frontiers_without_granting_authority(
    tmp_path: Path,
) -> None:
    loader = getattr(desktop_state, "load_portfolio_view", None)
    assert callable(loader)

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
            },
            {
                "id": "stable",
                "name": "Stable",
                "repository": "thebrazenbeard/stable",
                "priority": "P2",
                "activity_state": "STABLE",
                "status": "No active work.",
                "current_frontier": "Use when needed.",
            },
        ],
        "workstreams": [
            {
                "id": "nature-of-existence",
                "name": "The Nature of Existence",
                "priority": "P1",
                "activity_state": "ACTIVE",
                "status": "Research continues.",
                "current_frontier": "Keep evidence classes distinct.",
            }
        ],
    }
    path = tmp_path / "corpus.public.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")

    view = loader(path)

    assert view["observed_at"] == corpus["observed_at"]
    assert view["descriptive_only"] is True
    assert view["authority_granted"] is False
    assert [item["id"] for item in view["open_loops"]] == [
        "portal",
        "nature-of-existence",
    ]
    assert view["open_loops"][0]["frontier"] == "Complete the desktop shell."


def test_load_pending_authority_requests_preserves_exact_request_and_denies_grant(
    tmp_path: Path,
) -> None:
    loader = getattr(desktop_state, "load_pending_authority_requests", None)
    assert callable(loader)

    inbox = tmp_path / "authority-requests"
    inbox.mkdir()
    (inbox / "effect-1.json").write_text(
        json.dumps(
            {
                "schema": "PORTAL_DESKTOP_AUTHORITY_REQUEST_V1",
                "request_id": "effect-1",
                "subject_id": "portal",
                "repository": "thebrazenbeard/portal",
                "ref": "main",
                "exact_head": "a" * 40,
                "lineage_id": "lineage-1",
                "work_fingerprint": "b" * 64,
                "fencing_token": 3,
                "holder": "desktop-holder",
                "operation": "EXECUTE_FRONTIER",
                "effect_class": "SOURCE_WRITE",
                "execution_request": {"schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1"},
                "state_db": "state/portal/operator.sqlite3",
                "target": "thebrazenbeard/portal@main",
                "summary": "Write exact reviewed source change.",
                "source": "project-runner",
                "created_at": 100.0,
                "state": "PENDING",
            }
        ),
        encoding="utf-8",
    )

    requests = loader(inbox)

    assert requests == (
        {
            "request_id": "effect-1",
            "effect_class": "SOURCE_WRITE",
            "operation": "EXECUTE_FRONTIER",
            "target": "thebrazenbeard/portal@main",
            "summary": "Write exact reviewed source change.",
            "source": "project-runner",
            "created_at": 100.0,
            "exact_head": "a" * 40,
            "work_fingerprint": "b" * 64,
            "fencing_token": 3,
            "state": "PENDING",
            "request_is_authority": False,
            "approval_mints_execution_authority": True,
            "approval_mints_protected_effect_authority": True,
        },
    )
