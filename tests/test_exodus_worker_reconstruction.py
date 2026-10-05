from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_runner_requires_no_permanent_chat_and_names_three_interfaces():
    text = (ROOT / "docs" / "PROJECT_RUNNER_WORKER_RECONSTRUCTION_V1.md").read_text(encoding="utf-8")
    assert "Project Runner requires no fourth permanent chat" in text
    assert "`Vera`" in text
    assert "`Vera Control Plane Coordinator`" in text
    assert "`BT2 Coordinator`" in text


def test_chat_urls_and_unverified_worker_routes_are_not_operational_state():
    text = (ROOT / "docs" / "PROJECT_RUNNER_WORKER_RECONSTRUCTION_V1.md").read_text(encoding="utf-8")
    assert "share URL or GPT ID is a locator" in text
    assert "Routes explicitly marked `UNVERIFIED` remain unusable" in text
    assert "must not be the only locator for current state" in text


def test_operating_contract_points_to_reconstruction_contract():
    text = (ROOT / "PROJECT_RUNNER.md").read_text(encoding="utf-8")
    assert "requires no permanent Project Runner chat" in text
    assert "docs/PROJECT_RUNNER_WORKER_RECONSTRUCTION_V1.md" in text
