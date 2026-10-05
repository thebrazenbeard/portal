from runner.models import WorkerDefinition, WorkerLifecycle


def test_worker_locator_defaults_to_registered_only():
    worker = WorkerDefinition.from_mapping({
        "id": "tkal-in-ket-quorum",
        "name": "T'Kal-in-ket Quorum",
        "worker_type": "CHATGPT_CUSTOM_GPT",
        "lifecycle": "REGISTERED",
        "locators": {"gpt_id": "g-68c3e43aabc4819180ef0a89a10c4eb6"},
        "routes": {},
        "roles": [],
    })
    assert worker.lifecycle is WorkerLifecycle.REGISTERED


def test_invalid_gpt_id_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="GPT id"):
        WorkerDefinition.from_mapping({
            "id": "bad",
            "name": "Bad",
            "worker_type": "CHATGPT_CUSTOM_GPT",
            "lifecycle": "REGISTERED",
            "locators": {"gpt_id": "not-a-gpt-id"},
            "routes": {},
            "roles": [],
        })


def test_executable_worker_requires_verified_route():
    import pytest
    with pytest.raises(ValueError, match="EXECUTABLE"):
        WorkerDefinition.from_mapping({
            "id": "tkal",
            "name": "T'Kal",
            "worker_type": "CHATGPT_CUSTOM_GPT",
            "lifecycle": "EXECUTABLE",
            "locators": {"gpt_id": "g-68c3e43aabc4819180ef0a89a10c4eb6"},
            "routes": {"RUNNER_ACTION_PULL": "UNVERIFIED"},
            "roles": ["hostile_review"],
        })


def test_profiled_worker_requires_durable_reconstruction():
    import pytest
    with pytest.raises(ValueError, match="durable reconstruction evidence"):
        WorkerDefinition.from_mapping({
            "id": "reviewer",
            "name": "Reviewer",
            "worker_type": "OPENAI_AGENT",
            "lifecycle": "PROFILED",
            "locators": {"model": "reviewer-model"},
            "routes": {"OPENAI_AGENT_API": "UNVERIFIED"},
            "roles": ["hostile_review"],
        })


def test_profiled_worker_accepts_exact_github_reconstruction():
    worker = WorkerDefinition.from_mapping({
        "id": "reviewer",
        "name": "Reviewer",
        "worker_type": "OPENAI_AGENT",
        "lifecycle": "PROFILED",
        "locators": {"model": "reviewer-model"},
        "routes": {"OPENAI_AGENT_API": "UNVERIFIED"},
        "roles": ["hostile_review"],
        "reconstruction": {
            "repository": "owner/repo",
            "path": "workers/reviewer.md",
            "commit": "a" * 40,
        },
    })
    assert worker.reconstruction == {
        "repository": "owner/repo",
        "path": "workers/reviewer.md",
        "commit": "a" * 40,
    }


def test_ui_locator_cannot_substitute_for_durable_reconstruction():
    import pytest
    with pytest.raises(ValueError, match="durable reconstruction evidence"):
        WorkerDefinition.from_mapping({
            "id": "custom",
            "name": "Custom",
            "worker_type": "CHATGPT_CUSTOM_GPT",
            "lifecycle": "PROFILED",
            "locators": {
                "gpt_id": "g-68c3e43aabc4819180ef0a89a10c4eb6",
                "share_url": "https://chatgpt.com/g/example",
            },
            "routes": {"CHATGPT_INVOCATION": "CONNECTED"},
            "roles": ["specialist"],
        })


def test_reconstruction_repository_rejects_whitespace():
    import pytest
    with pytest.raises(ValueError, match="owner/repo"):
        WorkerDefinition.from_mapping({
            "id": "reviewer",
            "name": "Reviewer",
            "worker_type": "OPENAI_AGENT",
            "lifecycle": "PROFILED",
            "locators": {"model": "reviewer-model"},
            "routes": {"OPENAI_AGENT_API": "UNVERIFIED"},
            "roles": ["hostile_review"],
            "reconstruction": {
                "repository": "bad repo/name",
                "path": "workers/reviewer.md",
                "commit": "a" * 40,
            },
        })


def test_reconstruction_repository_accepts_names_containing_s():
    worker = WorkerDefinition.from_mapping({
        "id": "reviewer-s",
        "name": "Reviewer S",
        "worker_type": "OPENAI_AGENT",
        "lifecycle": "PROFILED",
        "locators": {"model": "reviewer-model"},
        "routes": {"OPENAI_AGENT_API": "UNVERIFIED"},
        "roles": ["hostile_review"],
        "reconstruction": {
            "repository": "sims/service",
            "path": "workers/reviewer.md",
            "commit": "b" * 40,
        },
    })
    assert worker.reconstruction["repository"] == "sims/service"
