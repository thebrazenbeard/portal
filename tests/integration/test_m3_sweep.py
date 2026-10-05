import json
from pathlib import Path

from runner.cli import main


ROOT = Path(__file__).resolve().parents[2]


def test_frontier_report_keeps_ready_and_authority_blocked_work_visible(capsys):
    rc = main([
        "frontier-report",
        "--before", str(ROOT / "tests" / "fixtures" / "observations-before.yaml"),
        "--after", str(ROOT / "tests" / "fixtures" / "observations-after.yaml"),
        "--dependencies", str(ROOT / "tests" / "fixtures" / "m3-dependencies.yaml"),
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    statuses = {item["status"] for item in payload["frontiers"]}
    assert statuses == {"READY", "WAITING_AUTHORITY"}
    assert payload["ranked"][0]["status"] == "READY"
    assert payload["ranked"][1]["status"] == "WAITING_AUTHORITY"
    assert payload["collision_groups"]
    assert all(item["reasons"] for item in payload["ranked"])
