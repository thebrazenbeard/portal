from dataclasses import replace
from pathlib import Path

from runner.cli import main
from runner.portfolio_advancement import load_advancement_wave
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_operator_binding import bind_wave_to_operator_registry
from runner.registry import ProjectRegistrySnapshot, load_project_snapshot


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "portfolio" / "corpus.public.json"
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
REGISTRY = ROOT / "registry" / "projects.yaml"


def _decision_map(report):
    return {
        (item.subject_kind, item.subject_id): item
        for item in report.decisions
    }


def test_real_public_wave_binds_only_exact_operator_projects():
    corpus = load_portfolio_corpus(CORPUS, public_safe=True)
    wave = load_advancement_wave(WAVE)
    registry = load_project_snapshot(REGISTRY)

    report = bind_wave_to_operator_registry(
        wave,
        corpus,
        registry,
        public_safe=True,
    )
    decisions = _decision_map(report)

    assert decisions[("repository", "project-runner")].state == "BOUND"
    assert decisions[("repository", "discovery")].state == "BOUND"

    assert decisions[("repository", "vera")].state == "HELD"
    assert (
        decisions[("repository", "vera")].reason
        == "OPERATOR_VISIBILITY_DIVERGENCE"
    )
    assert decisions[("repository", "vera-control-plane")].state == "HELD"
    assert (
        decisions[("repository", "vera-control-plane")].reason
        == "OPERATOR_VISIBILITY_DIVERGENCE"
    )

    assert decisions[("repository", "project-achilles")].state == "HELD"
    assert (
        decisions[("repository", "project-achilles")].reason
        == "OPERATOR_PROJECT_NOT_REGISTERED"
    )

    assert decisions[("workstream", "yeshua-real-testament")].state == "HELD"
    assert (
        decisions[("workstream", "yeshua-real-testament")].reason
        == "WORKSTREAM_REQUIRES_EXPLICIT_OPERATOR_BINDING"
    )

    summary = report.summary()
    assert summary["bound"] > 0
    assert summary["held"] > 0
    assert report.corpus_sha256 == corpus.sha256
    assert report.operator_registry_sha256 == registry.sha256


def test_repository_binding_ambiguity_fails_closed():
    corpus = load_portfolio_corpus(CORPUS, public_safe=True)
    wave = load_advancement_wave(WAVE)
    registry = load_project_snapshot(REGISTRY)
    discovery = next(
        project for project in registry.projects if project.id == "discovery"
    )
    duplicate = replace(discovery, id="discovery-shadow")
    ambiguous = ProjectRegistrySnapshot(
        projects=registry.projects + (duplicate,),
        sha256="f" * 64,
        byte_length=registry.byte_length,
    )

    report = bind_wave_to_operator_registry(
        wave,
        corpus,
        ambiguous,
        public_safe=True,
    )
    decision = _decision_map(report)[("repository", "discovery")]
    assert decision.state == "HELD"
    assert decision.reason == "OPERATOR_REPOSITORY_BINDING_AMBIGUOUS"


def test_operator_binding_cli_reports_no_execution_authority(capsys):
    code = main(["portfolio-operator-bindings"])
    assert code == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTFOLIO_OPERATOR_BINDING_REPORT_V1"
    assert payload["execution_authority"] is False
    assert payload["summary"]["bound"] > 0
    assert payload["summary"]["held"] > 0
