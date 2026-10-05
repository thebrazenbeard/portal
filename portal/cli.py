from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from typing import Sequence

from runner.models import ProjectSchedulingState
from runner.portfolio_advancement import load_advancement_wave
from runner.portfolio_wave_scheduler import WaveExecutionBudget
from runner.registry import (
    load_dependency_snapshot,
    load_project_snapshot,
    load_worker_snapshot,
)

from .coordinator import plan_portal_wave
from .discovery import (
    discover_live_project_registry,
    write_project_registry,
)
from .node_registry import load_execution_nodes
from .runtime import (
    PortalRunStore,
    run_portal_until_idle,
)
from .wave_runtime import (
    PortalWaveStore,
    prepare_portal_wave,
    verify_portal_wave_delivery,
)


ROOT = Path(__file__).resolve().parents[1]


def _github_token() -> str | None:
    return (
        os.environ.get("PORTAL_GITHUB_TOKEN")
        or os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portal",
        description=(
            "P.O.R.T.A.L. — Portfolio Orchestration & Repository Tracking "
            "Access Layer"
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    discover = subcommands.add_parser(
        "discover",
        help=(
            "discover the live owned GitHub estate and write a local "
            "Project Runner-compatible registry"
        ),
    )
    discover.add_argument("--owner", required=True)
    discover.add_argument(
        "--curated-projects",
        type=Path,
        default=ROOT / "registry" / "projects.yaml",
    )
    discover.add_argument("--output", type=Path, required=True)

    plan = subcommands.add_parser(
        "plan",
        help="plan a collision-safe portfolio wave onto declared nodes",
    )
    plan.add_argument("--wave", required=True)
    plan.add_argument("--nodes", required=True)
    plan.add_argument("--max-parallel", type=int, default=6)
    plan.add_argument("--max-per-lane", type=int, default=2)
    plan.add_argument("--max-per-identity", type=int, default=2)
    plan.add_argument("--max-per-family", type=int, default=2)
    plan.add_argument(
        "--occupied-collision-key",
        action="append",
        default=[],
        dest="occupied_collision_keys",
    )

    run = subcommands.add_parser(
        "run",
        help=(
            "refresh durable portfolio state and execute/refill "
            "qualified Project Runner queue lanes"
        ),
    )
    run.add_argument(
        "--projects",
        type=Path,
        default=ROOT / "registry" / "projects.yaml",
    )
    run.add_argument(
        "--discover-owner",
        help=(
            "replace static membership with a live owned GitHub inventory, "
            "using --projects only as curated metadata"
        ),
    )
    run.add_argument(
        "--write-live-registry",
        type=Path,
        help=(
            "optional operator-local path for the live registry; "
            "may contain private repository membership"
        ),
    )
    run.add_argument(
        "--workers",
        type=Path,
        default=ROOT / "registry" / "workers.yaml",
    )
    run.add_argument(
        "--dependencies",
        type=Path,
        default=ROOT / "topology" / "dependencies.yaml",
    )
    run.add_argument("--nodes", type=Path, required=True)
    run.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    run.add_argument("--run-id", default="portal-default")
    run.add_argument("--holder", default="vera")
    run.add_argument("--lease-ttl", type=float, default=300.0)
    run.add_argument("--max-parallel", type=int, default=6)
    run.add_argument("--max-cycles", type=int, default=100)
    run.add_argument("--max-idle-cycles", type=int, default=1)
    run.add_argument("--poll-seconds", type=float, default=0.0)
    run.add_argument(
        "--once",
        action="store_true",
        help="execute exactly one bounded durable Portal cycle",
    )

    status = subcommands.add_parser(
        "status",
        help="show durable P.O.R.T.A.L. run state",
    )
    status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    status.add_argument("--run-id", default="portal-default")

    wave = subcommands.add_parser(
        "wave",
        help="prepare and inspect durable advancement-wave work packets",
    )
    wave_subcommands = wave.add_subparsers(
        dest="wave_command",
        required=True,
    )

    wave_prepare = wave_subcommands.add_parser(
        "prepare",
        help=(
            "bind the advancement wave to live projects, assign nodes, "
            "and acquire exact-head Project Runner claims"
        ),
    )
    wave_prepare.add_argument(
        "--wave",
        type=Path,
        default=ROOT / "portfolio" / "advancement_wave.public.json",
    )
    wave_prepare.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "portfolio" / "corpus.public.json",
    )
    wave_prepare.add_argument(
        "--projects",
        type=Path,
        default=ROOT / "registry" / "projects.yaml",
    )
    wave_prepare.add_argument("--discover-owner")
    wave_prepare.add_argument("--write-live-registry", type=Path)
    wave_prepare.add_argument("--nodes", type=Path, required=True)
    wave_prepare.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_prepare.add_argument("--run-id", default="portal-wave-default")
    wave_prepare.add_argument("--holder", default="vera")
    wave_prepare.add_argument("--lease-ttl", type=float, default=300.0)
    wave_prepare.add_argument("--max-parallel", type=int, default=6)
    wave_prepare.add_argument("--max-per-identity", type=int, default=2)
    wave_prepare.add_argument("--max-per-family", type=int, default=2)
    wave_prepare.add_argument("--max-per-lane", type=int, default=2)
    wave_prepare.add_argument(
        "--occupied-collision-key",
        action="append",
        default=[],
        dest="occupied_collision_keys",
    )

    wave_claim = wave_subcommands.add_parser(
        "claim",
        help="lease one node-assigned wave packet to a worker",
    )
    wave_claim.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_claim.add_argument("--run-id", default="portal-wave-default")
    wave_claim.add_argument("--node", required=True)
    wave_claim.add_argument("--holder", required=True)
    wave_claim.add_argument("--lease-ttl", type=float, default=300.0)
    wave_claim.add_argument("--payload-out", type=Path, required=True)

    wave_receipt = wave_subcommands.add_parser(
        "receipt",
        help="record one exact fenced worker receipt",
    )
    wave_receipt.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_receipt.add_argument("--run-id", default="portal-wave-default")
    wave_receipt.add_argument("--subject-id", required=True)
    wave_receipt.add_argument("--node", required=True)
    wave_receipt.add_argument("--holder", required=True)
    wave_receipt.add_argument("--fencing-token", type=int, required=True)
    wave_receipt.add_argument(
        "--receipt-class",
        choices=(
            "SUCCEEDED_SOURCE_CHANGE",
            "SUCCEEDED_NO_EFFECT",
            "HELD",
            "FAILED_RETRYABLE",
            "FAILED_DETERMINISTIC",
            "OUTCOME_UNKNOWN",
        ),
        required=True,
    )
    wave_receipt.add_argument("--result-repository")
    wave_receipt.add_argument("--result-ref")
    wave_receipt.add_argument("--result-head")
    wave_receipt.add_argument("--evidence-sha256", required=True)
    wave_receipt.add_argument("--reason", required=True)

    wave_verify = wave_subcommands.add_parser(
        "verify",
        help="independently verify a recorded worker receipt",
    )
    wave_verify.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_verify.add_argument("--run-id", default="portal-wave-default")
    wave_verify.add_argument("--subject-id", required=True)
    wave_verify.add_argument("--verifier", required=True)

    wave_status = wave_subcommands.add_parser(
        "status",
        help="show durable advancement-wave outbox state",
    )
    wave_status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_status.add_argument("--run-id", default="portal-wave-default")
    return parser


def _discovery_payload(args: argparse.Namespace) -> dict[str, object]:
    curated = load_project_snapshot(Path(args.curated_projects))
    snapshot = discover_live_project_registry(
        owner=args.owner,
        curated_projects=curated.projects,
        token=_github_token(),
    )
    write_project_registry(Path(args.output), snapshot)
    public = sum(project.visibility == "public" for project in snapshot.projects)
    private = sum(project.visibility == "private" for project in snapshot.projects)
    archived = sum(
        project.scheduling_state is ProjectSchedulingState.ARCHIVED
        for project in snapshot.projects
    )
    return {
        "mode": "PORTAL_DISCOVERY_V1",
        "owner": args.owner,
        "repositories": len(snapshot.projects),
        "public": public,
        "private": private,
        "archived": archived,
        "registry_sha256": snapshot.sha256,
        "output": str(Path(args.output)),
        "repository_names_emitted": False,
    }


def _plan_payload(args: argparse.Namespace) -> dict[str, object]:
    wave = load_advancement_wave(Path(args.wave))
    nodes = load_execution_nodes(Path(args.nodes))
    budget = WaveExecutionBudget(
        max_parallel=args.max_parallel,
        max_per_lane=args.max_per_lane,
        max_per_identity=args.max_per_identity,
        max_per_family=args.max_per_family,
    )
    plan = plan_portal_wave(
        wave,
        budget=budget,
        nodes=nodes,
        occupied_collision_keys=args.occupied_collision_keys,
    )

    assignments = sorted(
        (asdict(item) for item in plan.assignments),
        key=lambda item: (str(item["subject_id"]), str(item["node_id"])),
    )
    node_deferrals = sorted(
        (asdict(item) for item in plan.node_deferrals),
        key=lambda item: (str(item["subject_id"]), str(item["reason"])),
    )
    runner_deferrals = sorted(
        (asdict(item) for item in plan.runner_plan.deferred),
        key=lambda item: (str(item["subject_id"]), str(item["reason"])),
    )
    return {
        "schema": "PORTAL_PLAN_V1",
        "wave_id": wave.wave_id,
        "summary": plan.summary(),
        "assignments": assignments,
        "node_deferrals": node_deferrals,
        "runner_deferrals": runner_deferrals,
    }


def _run_project_snapshot(args: argparse.Namespace):
    curated = load_project_snapshot(Path(args.projects))
    if not args.discover_owner:
        return curated, "static-registry"

    snapshot = discover_live_project_registry(
        owner=args.discover_owner,
        curated_projects=curated.projects,
        token=_github_token(),
    )
    if args.write_live_registry is not None:
        write_project_registry(Path(args.write_live_registry), snapshot)
    return snapshot, "live-discovery"


def _run_payload(args: argparse.Namespace) -> dict[str, object]:
    project_snapshot, portfolio_source = _run_project_snapshot(args)
    dependency_snapshot = load_dependency_snapshot(Path(args.dependencies))
    worker_snapshot = load_worker_snapshot(Path(args.workers))
    nodes = load_execution_nodes(Path(args.nodes))

    max_cycles = 1 if args.once else args.max_cycles
    max_idle_cycles = 1 if args.once else args.max_idle_cycles
    result = run_portal_until_idle(
        projects=project_snapshot.projects,
        dependencies=dependency_snapshot.dependencies,
        workers=worker_snapshot.workers,
        registry_digest=project_snapshot.sha256,
        dependency_digest=dependency_snapshot.sha256,
        worker_registry_digest=worker_snapshot.sha256,
        state_db=Path(args.state_db),
        nodes=nodes,
        run_id=args.run_id,
        holder=args.holder,
        lease_ttl=args.lease_ttl,
        max_parallel=args.max_parallel,
        max_cycles=max_cycles,
        max_idle_cycles=max_idle_cycles,
        poll_seconds=0.0 if args.once else args.poll_seconds,
        token=_github_token(),
    )

    cycles = []
    for cycle in result.cycles:
        cycles.append(
            {
                "cycle_number": cycle.cycle_number,
                "snapshot_id": cycle.cycle.snapshot_id,
                "snapshot_digest": cycle.cycle.snapshot_digest,
                "baseline": cycle.cycle.baseline,
                "ready": cycle.cycle.ready_count,
                "blocked": cycle.cycle.blocked_count,
                "progress_made": cycle.progress_made,
                "queue_summary": cycle.queue_summary,
                "lanes": [
                    {
                        "node_id": lane.node_id,
                        "slot": lane.slot,
                        "claimed": lane.claimed,
                        "queue_state": lane.queue_state,
                        "snapshot_id": lane.snapshot_id,
                        "fencing_token": lane.fencing_token,
                        "route_id": lane.route_id,
                        "operator_status": lane.operator_status,
                        "reason": lane.reason,
                    }
                    for lane in cycle.lanes
                ],
            }
        )
    return {
        "mode": "PORTAL_RUN_V1",
        "run_id": result.run_id,
        "portfolio_source": portfolio_source,
        "portfolio_repository_count": len(project_snapshot.projects),
        "portfolio_registry_sha256": project_snapshot.sha256,
        "stop_reason": result.stop_reason,
        "idle_cycles": result.idle_cycles,
        "cycles": cycles,
        "protected_effects_authorized": False,
    }


def _status_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalRunStore(Path(args.state_db))
    try:
        summary = store.summary(args.run_id)
    finally:
        store.close()
    return {
        "mode": "PORTAL_RUN_STATUS_V1",
        "run": summary,
    }


def _wave_projects_path(args: argparse.Namespace) -> Path:
    projects_path = Path(args.projects)
    if not args.discover_owner:
        return projects_path

    curated = load_project_snapshot(projects_path)
    snapshot = discover_live_project_registry(
        owner=args.discover_owner,
        curated_projects=curated.projects,
        token=_github_token(),
    )
    output = (
        Path(args.write_live_registry)
        if args.write_live_registry is not None
        else Path(args.state_db).parent / "projects.live.yaml"
    )
    write_project_registry(output, snapshot)
    return output


def _wave_prepare_payload(args: argparse.Namespace) -> dict[str, object]:
    projects_path = _wave_projects_path(args)
    nodes = load_execution_nodes(Path(args.nodes))
    budget = WaveExecutionBudget(
        max_parallel=args.max_parallel,
        max_per_identity=args.max_per_identity,
        max_per_family=args.max_per_family,
        max_per_lane=args.max_per_lane,
    )
    result = prepare_portal_wave(
        wave_path=Path(args.wave),
        corpus_path=Path(args.corpus),
        projects_path=projects_path,
        state_db=Path(args.state_db),
        nodes=nodes,
        budget=budget,
        run_id=args.run_id,
        holder=args.holder,
        lease_ttl=args.lease_ttl,
        token=_github_token(),
        occupied_collision_keys=args.occupied_collision_keys,
    )
    return {
        "mode": "PORTAL_WAVE_PREPARE_V1",
        "run_id": result.run_id,
        "plan_sha256": result.plan_sha256,
        "plan_path": str(result.plan_path),
        "assigned": result.assigned,
        "claimed": result.claimed,
        "held": result.held,
        "protected_effects_authorized": False,
        "packet_details_emitted": False,
    }


def _wave_claim_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalWaveStore(Path(args.state_db))
    try:
        claim = store.claim_delivery(
            run_id=args.run_id,
            node_id=args.node,
            holder=args.holder,
            now=time.time(),
            ttl=args.lease_ttl,
        )
    finally:
        store.close()

    if claim is None:
        return {
            "mode": "PORTAL_WAVE_CLAIM_V1",
            "claimed": False,
            "payload_written": False,
        }

    output = Path(args.payload_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(claim.payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "mode": "PORTAL_WAVE_CLAIM_V1",
        "claimed": True,
        "payload_written": True,
        "delivery_fencing_token": claim.fencing_token,
        "lease_expires_at": claim.lease_expires_at,
    }


def _wave_receipt_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalWaveStore(Path(args.state_db))
    try:
        receipt = store.record_delivery_receipt(
            run_id=args.run_id,
            subject_id=args.subject_id,
            node_id=args.node,
            holder=args.holder,
            expected_fencing_token=args.fencing_token,
            receipt_class=args.receipt_class,
            result_repository=args.result_repository,
            result_ref=args.result_ref,
            result_head=args.result_head,
            evidence_sha256=args.evidence_sha256,
            reason=args.reason,
            now=time.time(),
        )
    finally:
        store.close()
    return {
        "mode": "PORTAL_WAVE_RECEIPT_V1",
        "state": receipt.state,
        "receipt_class": receipt.receipt_class,
        "receipt_sha256": receipt.receipt_sha256,
    }


def _wave_verify_payload(args: argparse.Namespace) -> dict[str, object]:
    result = verify_portal_wave_delivery(
        state_db=Path(args.state_db),
        run_id=args.run_id,
        subject_id=args.subject_id,
        verifier=args.verifier,
        token=_github_token(),
    )
    return {
        "mode": "PORTAL_WAVE_VERIFY_V1",
        "state": result.state,
        "reason": result.reason,
    }


def _wave_status_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalWaveStore(Path(args.state_db))
    try:
        summary = store.summary(args.run_id)
    finally:
        store.close()
    return {
        "mode": "PORTAL_WAVE_STATUS_V1",
        "wave": summary,
    }


def entrypoint(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            payload = _discovery_payload(args)
        elif args.command == "plan":
            payload = _plan_payload(args)
        elif args.command == "run":
            payload = _run_payload(args)
        elif args.command == "status":
            payload = _status_payload(args)
        elif args.command == "wave":
            if args.wave_command == "prepare":
                payload = _wave_prepare_payload(args)
            elif args.wave_command == "claim":
                payload = _wave_claim_payload(args)
            elif args.wave_command == "receipt":
                payload = _wave_receipt_payload(args)
            elif args.wave_command == "verify":
                payload = _wave_verify_payload(args)
            elif args.wave_command == "status":
                payload = _wave_status_payload(args)
            else:
                parser.error("unsupported wave command")
                return 2
        else:
            parser.error("unsupported command")
            return 2
    except (OSError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        print(f"portal: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
