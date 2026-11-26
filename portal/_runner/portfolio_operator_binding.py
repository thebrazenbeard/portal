from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .models import ProjectDefinition, ProjectSchedulingState
from .portfolio_advancement import AdvancementWave, validate_wave_against_corpus
from .portfolio_corpus import PortfolioCorpusSnapshot
from .registry import ProjectRegistrySnapshot


@dataclass(frozen=True)
class OperatorBindingDecision:
    subject_kind: str
    subject_id: str
    repository: str | None
    state: str
    reason: str
    operator_project_id: str | None


@dataclass(frozen=True)
class OperatorBindingReport:
    corpus_sha256: str
    operator_registry_sha256: str
    decisions: tuple[OperatorBindingDecision, ...]

    def summary(self) -> dict[str, object]:
        states = Counter(item.state for item in self.decisions)
        reasons = Counter(item.reason for item in self.decisions)
        return {
            "subjects": len(self.decisions),
            "bound": states.get("BOUND", 0),
            "held": states.get("HELD", 0),
            "states": dict(sorted(states.items())),
            "reasons": dict(sorted(reasons.items())),
            "corpus_sha256": self.corpus_sha256,
            "operator_registry_sha256": self.operator_registry_sha256,
        }

    def bound_subjects(self) -> tuple[OperatorBindingDecision, ...]:
        return tuple(item for item in self.decisions if item.state == "BOUND")


def _repository_index(
    projects: tuple[ProjectDefinition, ...],
) -> dict[str, tuple[ProjectDefinition, ...]]:
    by_repository: dict[str, list[ProjectDefinition]] = {}
    for project in projects:
        for repository in project.repositories:
            by_repository.setdefault(repository, []).append(project)
    return {
        repository: tuple(sorted(items, key=lambda item: item.id))
        for repository, items in by_repository.items()
    }


def bind_wave_to_operator_registry(
    wave: AdvancementWave,
    corpus: PortfolioCorpusSnapshot,
    operator_registry: ProjectRegistrySnapshot,
    *,
    public_safe: bool,
) -> OperatorBindingReport:
    """Cross-bind corpus subjects to Operator projects without inference.

    A repository subject is BOUND only when exactly one Operator project owns
    the exact repository and that project's id, visibility, and schedulability
    agree with the corpus record. Workstreams remain HELD until an explicit
    multi-project binding contract exists.
    """
    validate_wave_against_corpus(wave, corpus, public_safe=public_safe)

    records = {record.id: record for record in corpus.records}
    repository_index = _repository_index(operator_registry.projects)
    decisions: list[OperatorBindingDecision] = []

    for item in wave.items:
        if item.subject_kind != "repository":
            decisions.append(
                OperatorBindingDecision(
                    subject_kind=item.subject_kind,
                    subject_id=item.subject_id,
                    repository=None,
                    state="HELD",
                    reason="WORKSTREAM_REQUIRES_EXPLICIT_OPERATOR_BINDING",
                    operator_project_id=None,
                )
            )
            continue

        record = records[item.subject_id]
        repository = record.repository
        matches = repository_index.get(repository, ())
        if not matches:
            decisions.append(
                OperatorBindingDecision(
                    subject_kind="repository",
                    subject_id=item.subject_id,
                    repository=repository,
                    state="HELD",
                    reason="OPERATOR_PROJECT_NOT_REGISTERED",
                    operator_project_id=None,
                )
            )
            continue
        if len(matches) != 1:
            decisions.append(
                OperatorBindingDecision(
                    subject_kind="repository",
                    subject_id=item.subject_id,
                    repository=repository,
                    state="HELD",
                    reason="OPERATOR_REPOSITORY_BINDING_AMBIGUOUS",
                    operator_project_id=None,
                )
            )
            continue

        project = matches[0]
        if project.id != record.id:
            decisions.append(
                OperatorBindingDecision(
                    subject_kind="repository",
                    subject_id=item.subject_id,
                    repository=repository,
                    state="HELD",
                    reason="OPERATOR_PROJECT_ID_DIVERGENCE",
                    operator_project_id=project.id,
                )
            )
            continue
        if project.visibility != record.visibility:
            decisions.append(
                OperatorBindingDecision(
                    subject_kind="repository",
                    subject_id=item.subject_id,
                    repository=repository,
                    state="HELD",
                    reason="OPERATOR_VISIBILITY_DIVERGENCE",
                    operator_project_id=project.id,
                )
            )
            continue
        if project.scheduling_state is not ProjectSchedulingState.SCHEDULABLE:
            decisions.append(
                OperatorBindingDecision(
                    subject_kind="repository",
                    subject_id=item.subject_id,
                    repository=repository,
                    state="HELD",
                    reason="OPERATOR_PROJECT_NOT_SCHEDULABLE",
                    operator_project_id=project.id,
                )
            )
            continue

        decisions.append(
            OperatorBindingDecision(
                subject_kind="repository",
                subject_id=item.subject_id,
                repository=repository,
                state="BOUND",
                reason="EXACT_REPOSITORY_PROJECT_BINDING",
                operator_project_id=project.id,
            )
        )

    return OperatorBindingReport(
        corpus_sha256=corpus.sha256,
        operator_registry_sha256=operator_registry.sha256,
        decisions=tuple(decisions),
    )
