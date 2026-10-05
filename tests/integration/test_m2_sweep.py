import json
from pathlib import Path

from runner.cli import main


ROOT = Path(__file__).resolve().parents[2]


def test_evaluate_change_reports_only_changed_matching_consumer(capsys):
    rc = main([
        "evaluate-change",
        "--before", str(ROOT / "tests" / "fixtures" / "observations-before.yaml"),
        "--after", str(ROOT / "tests" / "fixtures" / "observations-after.yaml"),
        "--dependencies", str(ROOT / "topology" / "dependencies.yaml"),
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["changed_subjects"]) == 1
    assert payload["changed_subjects"][0]["target"] == "chat-communication-bus"
    assert payload["invalidations"] == [{
        "consumer": "project-runner",
        "dependency_id": "bus-routing-to-project-runner",
        "provider": "chat-communication-bus",
        "reaction": "INSPECT",
        "subject": {
            "commit": "b" * 40,
            "digest": None,
            "path": "architecture/contracts/RADAR_TOPOLOGY_V1.json",
            "ref": "main",
            "repository": "thebrazenbeard/chat-communication-bus",
        },
    }]
