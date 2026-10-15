from __future__ import annotations

import json
from pathlib import Path

import pytest

from portal.desktop_wave_history import list_governed_wave_receipts


SHA = "a" * 40


def make_wave(root: Path, *, tamper: str = "", count: int = 3) -> Path:
    base = root / "state" / "portal" / "governed-waves" / "20261015-8x72-test"
    base.mkdir(parents=True)
    names = [f"thebrazenbeard/proj-{i}" for i in range(count)]
    manifest = {
        "selected_runner_sha": SHA,
        "exact_live_names": names,
        "execution_lane_limit": 2,
        "logic_queue_limit": 72,
    }
    summary = {
        "schema": "PORTAL_PROJECT_RUNNER_89_FINAL_72_LOGICAL_LANES_V1",
        "scope_count": count, "receipts": count,
        "max_concurrent_executions": 2,
        "max_logical_queue": 72,
        "distinct_logical_lanes": count,
        "runtime_source_sha": SHA,
        "independent_logic_agents": 0,
        "source_code_executed": 0,
        "repositories_modified": 0,
    }
    for name, value in (("summary.json", summary), ("manifest.json", manifest)):
        (base / name).write_text(json.dumps(value), encoding="utf8")
    for i, name in enumerate(names):
        folder = base / "subjects" / f"proj-{i}"
        folder.mkdir(parents=True)
        (folder / "attempt.json").write_text('{"state":"ATTEMPTED"}', encoding="utf8")
        (folder / "final.json").write_text(json.dumps({
            "repository": name, "lane": f"logical-{i+1:02d}",
            "state": "SOURCE_CURRENTNESS_VERIFIED",
            "source_code_executed": False,
            "github_repository_mutated": False,
        }), encoding="utf8")
    if tamper == "unaccounted":
        summary["receipts"] = count - 1
        (base / "summary.json").write_text(json.dumps(summary))
    elif tamper == "invented_72":
        summary["distinct_logical_lanes"] = 72
        (base / "summary.json").write_text(json.dumps(summary))
    elif tamper == "effect":
        f = base / "subjects" / "proj-0" / "final.json"
        record = json.loads(f.read_text())
        record["source_code_executed"] = True
        f.write_text(json.dumps(record))
    elif tamper == "wrong_source":
        manifest["selected_runner_sha"] = "b" * 40
        (base / "manifest.json").write_text(json.dumps(manifest))
    elif tamper == "incomplete":
        (base / "subjects" / "proj-0" / "attempt.json").unlink()
    return base


def test_consistent_wave_is_labeled_inspection_not_project_execution(tmp_path: Path):
    folder = make_wave(tmp_path)
    (only,) = list_governed_wave_receipts(tmp_path)
    assert only["status"] == "LOCAL_INSPECTION_COMPLETE"
    assert only["inspected"] == only["subjects"] == 3
    assert only["logical_lanes"] == 3
    assert only["logical_queue_limit"] == 72
    assert only["physical_workers_limit"] == 2
    assert only["projects_executed"] == 0
    assert only["repositories_modified"] == 0
    assert only["independently_attested"] is False
    assert only["evidence_directory"] == str(folder)


@pytest.mark.parametrize(
    "tamper", ("unaccounted", "invented_72", "effect",
               "wrong_source", "incomplete"),
)
def test_inconsistent_or_misleading_waves_fail_closed(tmp_path: Path, tamper: str):
    make_wave(tmp_path, tamper=tamper)
    assert list_governed_wave_receipts(tmp_path) == ()


def test_wave_history_limits_and_missing_state(tmp_path: Path):
    assert list_governed_wave_receipts(tmp_path) == ()
    for bad in (0, 21, True):
        with pytest.raises(ValueError):
            list_governed_wave_receipts(tmp_path, limit=bad)
