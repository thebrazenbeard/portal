import json
from pathlib import Path

from runner.cli import main


ROOT = Path(__file__).resolve().parents[2]


def test_dispatch_report_runs_m2_m3_m4_mock_pipeline(capsys):
    rc = main([
        "dispatch-report",
        "--before", str(ROOT / "tests" / "fixtures" / "observations-before.yaml"),
        "--after", str(ROOT / "tests" / "fixtures" / "observations-after.yaml"),
        "--dependencies", str(ROOT / "tests" / "fixtures" / "m3-dependencies.yaml"),
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "M4_MOCK_NO_DOWNSTREAM_EFFECTS"
    assert len(payload["attempts"]) == 1
    attempt = payload["attempts"][0]
    assert attempt["project"] == "project-runner"
    assert attempt["backend_succeeded"] is True
    assert attempt["verification_status"] == "COMPLETE"
    assert attempt["fencing_token"] == 1
    assert payload["blocked"][0]["project"] == "unregistered-consumer"
    assert payload["blocked"][0]["status"] == "WAITING_AUTHORITY"
