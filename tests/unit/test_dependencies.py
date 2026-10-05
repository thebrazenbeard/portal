from pathlib import Path

import pytest

from runner.models import DependencyReaction
from runner.registry import load_dependencies


def test_load_dependencies_returns_typed_edge(tmp_path: Path):
    path = tmp_path / "dependencies.yaml"
    path.write_text("""dependencies:
- id: bus-routing-to-project-runner
  provider: chat-communication-bus
  consumer: project-runner
  kind: routing-contract
  selector:
    repository: thebrazenbeard/chat-communication-bus
    ref: main
    path_prefix: architecture/contracts/
  reaction: INSPECT
  evidence: exact-subject
""", encoding="utf-8")

    edges = load_dependencies(path)

    assert len(edges) == 1
    assert edges[0].reaction is DependencyReaction.INSPECT
    assert edges[0].selector.path_prefix == "architecture/contracts/"


def test_load_dependencies_rejects_duplicate_ids(tmp_path: Path):
    path = tmp_path / "dependencies.yaml"
    path.write_text("""dependencies:
- id: duplicate
  provider: a
  consumer: b
  kind: source
  selector: {repository: org/repo}
  reaction: RETEST
  evidence: exact-subject
- id: duplicate
  provider: a
  consumer: c
  kind: source
  selector: {repository: org/repo}
  reaction: REREVIEW
  evidence: exact-subject
""", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate dependency id"):
        load_dependencies(path)


def test_load_dependencies_rejects_unknown_reaction(tmp_path: Path):
    path = tmp_path / "dependencies.yaml"
    path.write_text("""dependencies:
- id: bad-reaction
  provider: a
  consumer: b
  kind: source
  selector: {repository: org/repo}
  reaction: EXPLODE
  evidence: exact-subject
""", encoding="utf-8")

    with pytest.raises(Exception):
        load_dependencies(path)
