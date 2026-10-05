from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_active_worker_registry_does_not_depend_on_chatgpt_share_urls():
    registry_path = ROOT / "registry" / "workers.yaml"
    text = registry_path.read_text(encoding="utf-8")
    assert "chatgpt.com/" not in text

    payload = yaml.safe_load(text)
    assert payload["workers"]
    for worker in payload["workers"]:
        locators = worker["locators"]
        assert "share_url" not in locators
        if worker["worker_type"] == "CHATGPT_CUSTOM_GPT":
            assert locators.get("gpt_id", "").startswith("g-")
            if worker["lifecycle"] == "REGISTERED":
                assert all(state == "UNVERIFIED" for state in worker["routes"].values())
