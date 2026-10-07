from __future__ import annotations

from pathlib import Path

import pytest

import portal.task_currentness as task_currentness


def test_local_task_currentness_reconciles_before_counting_active_capacity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_reconcile(tasks_root):
        calls.append("reconcile")
        assert Path(tasks_root) == tmp_path / "tasks"
        return [
            {"task_id": "stale-a", "state": "UNKNOWN_EXIT"},
            {"task_id": "stale-b", "state": "UNKNOWN_EXIT"},
        ]

    def fake_summarize(tasks_root):
        calls.append("summarize")
        assert Path(tasks_root) == tmp_path / "tasks"
        return {
            "tasks": [
                {"task_id": "live", "state": "RUNNING"},
                {"task_id": "uncertain", "state": "IDENTITY_UNVERIFIED"},
            ],
        }

    monkeypatch.setattr(
        task_currentness,
        "reconcile_orphaned_tasks",
        fake_reconcile,
    )
    monkeypatch.setattr(
        task_currentness,
        "summarize_tasks",
        fake_summarize,
    )

    provider = task_currentness.LocalProjectRunnerTaskCurrentness(
        node_id="lappy",
        tasks_root=tmp_path / "tasks",
    )
    snapshot = provider.snapshot()

    assert calls == ["reconcile", "summarize"]
    assert snapshot.node_id == "lappy"
    assert snapshot.occupied_slots == 2
    assert snapshot.reconciled_unknown_exit == 2
    assert snapshot.state_counts == {
        "IDENTITY_UNVERIFIED": 1,
        "RUNNING": 1,
    }


def test_local_task_currentness_counts_any_remaining_active_record_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        task_currentness,
        "reconcile_orphaned_tasks",
        lambda tasks_root: [],
    )
    monkeypatch.setattr(
        task_currentness,
        "summarize_tasks",
        lambda tasks_root: {
            "tasks": [
                {"task_id": "race", "state": "ORPHANED"},
                {"task_id": "live", "state": "RUNNING"},
            ],
        },
    )

    provider = task_currentness.LocalProjectRunnerTaskCurrentness(
        node_id="worklaptop",
        tasks_root=tmp_path / "tasks",
    )

    assert provider() == {"worklaptop": 2}


def test_local_task_currentness_rejects_empty_node_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="node_id is required"):
        task_currentness.LocalProjectRunnerTaskCurrentness(
            node_id="  ",
            tasks_root=tmp_path / "tasks",
        )
