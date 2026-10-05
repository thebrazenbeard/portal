from pathlib import Path
from runner.registry import load_projects, load_workers

ROOT = Path(__file__).resolve().parents[2]


def test_seed_registry_contains_public_portfolio_without_private_expansion():
    projects = load_projects(ROOT / "registry/projects.yaml")
    workers = load_workers(ROOT / "registry/workers.yaml")

    assert {p.id for p in projects} == {
        "project-runner",
        "chat-communication-bus",
        "vera",
        "vera-control-plane",
        "hc-brain",
        "roots",
        "testament",
        "transcendence",
        "rezon",
        "world-zero",
        "on-theo",
        "wip",
        "mosaic",
        "driftguard",
        "discovery",
    }

    private_projects = {p.id for p in projects if p.visibility == "private"}
    assert private_projects == {
        "chat-communication-bus",
        "vera",
        "vera-control-plane",
    }

    assert len(workers) == 13
    custom_gpts = tuple(
        worker
        for worker in workers
        if worker.worker_type.value == "CHATGPT_CUSTOM_GPT"
    )
    assert len(custom_gpts) == 12
    assert all(worker.lifecycle.value == "REGISTERED" for worker in custom_gpts)
    assert all(worker.reconstruction is None for worker in custom_gpts)

    reference = next(
        worker
        for worker in workers
        if worker.id == "project-runner-reference-read-worker"
    )
    assert reference.lifecycle.value == "EXECUTABLE"
    assert reference.worker_type.value == "GITHUB_ACTION"
    assert reference.routes[
        next(route for route in reference.routes if route.value == "RUNNER_ACTION_PULL")
    ].value == "VERIFIED"
    assert reference.reconstruction is not None
