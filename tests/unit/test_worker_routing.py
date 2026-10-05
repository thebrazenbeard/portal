from pathlib import Path
import json

import pytest

from runner.models import (
    Frontier,
    Observation,
    ProjectDefinition,
    WorkerDefinition,
)
from runner.portfolio import SqlitePortfolioStore
from runner.worker_routing import (
    SqliteWorkerRouteStore,
    resolve_read_only_worker_route,
)


def _project(*, worker=True):
    target = {
        "work_type": "REREVIEW",
        "repository": "example/consumer",
        "ref": "main",
    }
    if worker:
        target.update(
            {
                "worker_id": "reviewer",
                "worker_route": "OPENAI_AGENT_API",
            }
        )
    return ProjectDefinition.from_mapping(
        {
            "id": "consumer",
            "name": "Consumer",
            "visibility": "public",
            "repositories": ["example/consumer"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "consumer",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [target],
        }
    )


def _worker(
    *,
    route_state: str = "VERIFIED",
    effect_class: str = "READ_ONLY",
    replay_policy: str = "SAFE",
):
    routes = {"OPENAI_AGENT_API": route_state}
    route_contracts = {
        "OPENAI_AGENT_API": {
            "effect_class": effect_class,
            "replay_policy": replay_policy,
        }
    }
    if route_state != "VERIFIED":
        routes["GITHUB_ACTION"] = "VERIFIED"
        route_contracts["GITHUB_ACTION"] = {
            "effect_class": "READ_ONLY",
            "replay_policy": "SAFE",
        }
    return WorkerDefinition.from_mapping(
        {
            "id": "reviewer",
            "name": "Reviewer",
            "worker_type": "OPENAI_AGENT",
            "lifecycle": "EXECUTABLE",
            "locators": {"model": "reviewer-model"},
            "roles": ["review"],
            "routes": routes,
            "route_contracts": route_contracts,
            "reconstruction": {
                "repository": "example/worker-definitions",
                "path": "workers/reviewer.md",
                "commit": "a" * 40,
            },
        }
    )


def _frontier():
    return Frontier.from_mapping(
        {
            "id": "frontier-review",
            "project": "consumer",
            "subject": {
                "repository": "example/provider",
                "ref": "main",
                "commit": "b" * 40,
            },
            "work_type": "REREVIEW",
            "reason": "provider changed",
            "dependencies": ["provider-consumer"],
            "required_capabilities": ["analyze"],
            "collision_keys": ["project:consumer"],
            "cost_class": "SMALL",
            "priority_inputs": {
                "fanout": 1,
                "blocked_downstream": 0,
                "staleness_risk": 1,
                "failure_severity": 0,
                "declared_priority": 0,
                "cost": 1,
                "authority_available": 1,
                "scheduling_eligible": 1,
                "executable_now": 1,
            },
            "status": "READY",
        }
    )



def _frontier_json() -> str:
    frontier = _frontier()
    return json.dumps(
        {
            "id": frontier.id,
            "project": frontier.project,
            "subject": {
                "repository": frontier.subject.repository,
                "ref": frontier.subject.ref,
                "commit": frontier.subject.commit,
                "path": frontier.subject.path,
                "digest": frontier.subject.digest,
            },
            "work_type": frontier.work_type,
            "reason": frontier.reason,
            "dependencies": list(frontier.dependencies),
            "required_capabilities": list(frontier.required_capabilities),
            "collision_keys": list(frontier.collision_keys),
            "cost_class": frontier.cost_class.value,
            "priority_inputs": dict(frontier.priority_inputs),
            "status": frontier.status.value,
        },
        sort_keys=True,
    )


def test_verified_read_only_worker_route_resolves():
    route = resolve_read_only_worker_route(
        project=_project(),
        frontier=_frontier(),
        workers=(_worker(),),
    )
    assert route is not None
    assert route.worker_id == "reviewer"
    assert route.invocation_route.value == "OPENAI_AGENT_API"
    assert route.replay_policy.value == "SAFE"


def test_unverified_worker_route_is_rejected():
    with pytest.raises(ValueError, match="not VERIFIED"):
        resolve_read_only_worker_route(
            project=_project(),
            frontier=_frontier(),
            workers=(_worker(route_state="CONNECTED"),),
        )


def test_mutating_worker_route_is_rejected():
    with pytest.raises(ValueError, match="not qualified as READ_ONLY"):
        resolve_read_only_worker_route(
            project=_project(),
            frontier=_frontier(),
            workers=(_worker(effect_class="MUTATING"),),
        )


def test_non_inspect_work_requires_explicit_worker_binding():
    with pytest.raises(ValueError, match="requires an explicit worker route"):
        resolve_read_only_worker_route(
            project=_project(worker=False),
            frontier=_frontier(),
            workers=(_worker(),),
        )


def test_worker_route_outbox_is_idempotent_for_exact_queue_fence(tmp_path: Path):
    db = tmp_path / "worker-route.sqlite3"
    store = SqliteWorkerRouteStore(db)
    route = resolve_read_only_worker_route(
        project=_project(),
        frontier=_frontier(),
        workers=(_worker(),),
    )
    assert route is not None
    frontier_json = """{"id":"frontier-review"}"""

    first = store.enqueue(
        snapshot_id=2,
        frontier_fingerprint="f" * 64,
        queue_fencing_token=3,
        worker_registry_digest="1" * 64,
        route=route,
        target_repository="example/consumer",
        target_ref="main",
        target_head="c" * 40,
        frontier_json=frontier_json,
        now=10.0,
    )
    second = store.enqueue(
        snapshot_id=2,
        frontier_fingerprint="f" * 64,
        queue_fencing_token=3,
        worker_registry_digest="1" * 64,
        route=route,
        target_repository="example/consumer",
        target_ref="main",
        target_head="c" * 40,
        frontier_json=frontier_json,
        now=11.0,
    )
    store.close()

    assert first.route_id == second.route_id
    assert second.state == "PENDING"



def _enqueued_store(tmp_path: Path, *, replay_policy: str = "SAFE"):
    db = tmp_path / "worker-delivery.sqlite3"
    portfolio = SqlitePortfolioStore(db)
    observation = Observation.from_mapping(
        {
            "target": "provider",
            "evidence_class": "AUTHORITATIVE",
            "subject": {
                "repository": "example/provider",
                "ref": "main",
                "commit": "b" * 40,
            },
            "observed_value": "b" * 40,
            "observed_at": "test",
            "observer": "test",
        }
    )
    snapshot_id = portfolio.commit_cycle(
        registry_digest="0" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="1" * 64,
        snapshot_digest="3" * 64,
        observed_at=1.0,
        baseline=True,
        observations=(observation,),
        changed_count=0,
        ranked_frontiers=(),
        expected_previous_snapshot_id=None,
        expected_previous_dependency_snapshot_id=None,
    )
    portfolio.close()

    store = SqliteWorkerRouteStore(db)
    route = resolve_read_only_worker_route(
        project=_project(),
        frontier=_frontier(),
        workers=(_worker(replay_policy=replay_policy),),
    )
    assert route is not None
    envelope = store.enqueue(
        snapshot_id=snapshot_id,
        frontier_fingerprint="f" * 64,
        queue_fencing_token=3,
        worker_registry_digest="1" * 64,
        route=route,
        target_repository="example/consumer",
        target_ref="main",
        target_head="c" * 40,
        frontier_json=_frontier_json(),
        now=10.0,
    )
    return store, envelope


def test_worker_delivery_claim_is_fenced_and_reclaim_increments_token(
    tmp_path: Path,
):
    store, envelope = _enqueued_store(tmp_path)
    try:
        first = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(_worker(),),
            holder="worker-one",
            now=11.0,
            ttl=10.0,
            worker_registry_digest="1" * 64,
        )
        assert first is not None
        assert first.route_id == envelope.route_id
        assert first.fencing_token == 1
        assert first.payload["target_head"] == "c" * 40

        blocked = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(_worker(),),
            holder="worker-two",
            now=12.0,
            ttl=10.0,
            worker_registry_digest="1" * 64,
        )
        assert blocked is None

        reclaimed = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(_worker(),),
            holder="worker-two",
            now=22.0,
            ttl=10.0,
            worker_registry_digest="1" * 64,
        )
        assert reclaimed is not None
        assert reclaimed.route_id == envelope.route_id
        assert reclaimed.fencing_token == 2
    finally:
        store.close()


def test_worker_delivery_requires_current_worker_registry_digest(tmp_path: Path):
    store, envelope = _enqueued_store(tmp_path)
    try:
        claim = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(_worker(),),
            holder="worker-one",
            now=11.0,
            ttl=10.0,
            worker_registry_digest="2" * 64,
        )
        assert claim is None
    finally:
        store.close()


def test_stale_delivery_fence_cannot_record_receipt(tmp_path: Path):
    store, envelope = _enqueued_store(tmp_path)
    try:
        first = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(_worker(),),
            holder="worker-one",
            now=11.0,
            ttl=5.0,
            worker_registry_digest="1" * 64,
        )
        assert first is not None
        second = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(_worker(),),
            holder="worker-two",
            now=17.0,
            ttl=10.0,
            worker_registry_digest="1" * 64,
        )
        assert second is not None

        with pytest.raises(ValueError, match="delivery fence"):
            store.record_receipt(
                route_id=envelope.route_id,
                worker_id="reviewer",
                invocation_route=envelope.invocation_route,
                holder="worker-one",
                expected_fencing_token=first.fencing_token,
                receipt_class="SUCCEEDED",
                receipt_sha256="a" * 64,
                now=18.0,
            )
    finally:
        store.close()


def test_worker_receipt_is_idempotent_only_for_exact_same_receipt(tmp_path: Path):
    store, envelope = _enqueued_store(tmp_path)
    try:
        claim = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(_worker(),),
            holder="worker-one",
            now=11.0,
            ttl=10.0,
            worker_registry_digest="1" * 64,
        )
        assert claim is not None

        first = store.record_receipt(
            route_id=envelope.route_id,
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            holder="worker-one",
            expected_fencing_token=claim.fencing_token,
            receipt_class="SUCCEEDED",
            receipt_sha256="a" * 64,
            now=12.0,
        )
        second = store.record_receipt(
            route_id=envelope.route_id,
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            holder="worker-one",
            expected_fencing_token=claim.fencing_token,
            receipt_class="SUCCEEDED",
            receipt_sha256="a" * 64,
            now=13.0,
        )
        assert first == second
        assert second.state == "RECEIPT_RECORDED"

        with pytest.raises(ValueError, match="different receipt"):
            store.record_receipt(
                route_id=envelope.route_id,
                worker_id="reviewer",
                invocation_route=envelope.invocation_route,
                holder="different-holder",
                expected_fencing_token=claim.fencing_token,
                receipt_class="SUCCEEDED",
                receipt_sha256="a" * 64,
                now=13.5,
            )

        with pytest.raises(ValueError, match="different receipt"):
            store.record_receipt(
                route_id=envelope.route_id,
                worker_id="reviewer",
                invocation_route=envelope.invocation_route,
                holder="worker-one",
                expected_fencing_token=claim.fencing_token,
                receipt_class="FAILED_RETRYABLE",
                receipt_sha256="b" * 64,
                now=14.0,
            )
    finally:
        store.close()



@pytest.mark.parametrize("replay_policy", ["RECONCILE_REQUIRED", "NEVER"])
def test_expired_non_safe_delivery_becomes_outcome_unknown(
    tmp_path: Path,
    replay_policy: str,
):
    store, envelope = _enqueued_store(
        tmp_path,
        replay_policy=replay_policy,
    )
    worker = _worker(replay_policy=replay_policy)
    try:
        first = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(worker,),
            holder="worker-one",
            now=11.0,
            ttl=5.0,
            worker_registry_digest="1" * 64,
        )
        assert first is not None

        second = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(worker,),
            holder="worker-two",
            now=17.0,
            ttl=10.0,
            worker_registry_digest="1" * 64,
        )
        assert second is None
        state = store.connection.execute(
            "SELECT state FROM worker_route_outbox WHERE route_id = ?",
            (envelope.route_id,),
        ).fetchone()
        assert state == ("OUTCOME_UNKNOWN",)
    finally:
        store.close()


def test_delivery_claim_rejects_worker_registry_route_downgrade(tmp_path: Path):
    store, envelope = _enqueued_store(tmp_path)
    try:
        with pytest.raises(ValueError, match="not VERIFIED"):
            store.claim_next(
                worker_id="reviewer",
                invocation_route=envelope.invocation_route,
                workers=(_worker(route_state="CONNECTED"),),
                holder="worker-one",
                now=11.0,
                ttl=10.0,
                worker_registry_digest="1" * 64,
            )
    finally:
        store.close()



def test_worker_delivery_supersedes_packet_when_provider_subject_moves(
    tmp_path: Path,
):
    store, envelope = _enqueued_store(tmp_path)
    db = store.path
    store.close()

    portfolio = SqlitePortfolioStore(db)
    second = portfolio.commit_cycle(
        registry_digest="0" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="1" * 64,
        snapshot_digest="4" * 64,
        observed_at=2.0,
        baseline=False,
        observations=(
            Observation.from_mapping(
                {
                    "target": "provider",
                    "evidence_class": "AUTHORITATIVE",
                    "subject": {
                        "repository": "example/provider",
                        "ref": "main",
                        "commit": "c" * 40,
                    },
                    "observed_value": "c" * 40,
                    "observed_at": "test-2",
                    "observer": "test",
                }
            ),
        ),
        changed_count=1,
        ranked_frontiers=(),
        expected_previous_snapshot_id=1,
        expected_previous_dependency_snapshot_id=1,
    )
    assert second == 2
    portfolio.close()

    store = SqliteWorkerRouteStore(db)
    try:
        claim = store.claim_next(
            worker_id="reviewer",
            invocation_route=envelope.invocation_route,
            workers=(_worker(),),
            holder="worker-one",
            now=11.0,
            ttl=10.0,
            worker_registry_digest="1" * 64,
        )
        assert claim is None
        state = store.connection.execute(
            "SELECT state FROM worker_route_outbox WHERE route_id = ?",
            (envelope.route_id,),
        ).fetchone()
        assert state == ("SUPERSEDED",)
    finally:
        store.close()
