from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Sequence

from runner.execution_promotion import (
    effect_authority_key_from_environment,
    execution_authority_key_from_environment,
    load_json_document,
    review_key_from_environment,
)
from runner.github_backend import GitHubRestTransport
from runner.models import ProjectSchedulingState
from runner.portfolio_advancement import load_advancement_wave
from runner.portfolio_wave_scheduler import WaveExecutionBudget
from runner.registry import (
    load_dependency_snapshot,
    load_project_snapshot,
    load_worker_snapshot,
)

from .coordinator import plan_portal_wave
from .ecosystem_runtime import (
    PortalEcosystemStore,
    run_ecosystem_proposal_generations,
)
from .diagnostics import build_host_diagnostics
from .discovery import (
    GitHubRepositoryCatalog,
    build_live_project_registry,
    discover_live_project_registry,
    write_project_registry,
)
from .frontier_currentness import (
    PortalFrontierObservation,
    PortalHostFrontierCurrentness,
    PortalHostFrontierStore,
    frontier_policy_sha256,
)
from .live_portfolio import refresh_live_public_portfolio
from .host_bridge import (
    PortalHostBridgeStore,
    PortalHostExecutionAdapter,
    PortalHostNodeCurrentness,
)
from .host_driver_registry import load_host_command_drivers
from .host_pump import PortalHostPump
from .node_registry import load_execution_nodes
from .process_adapter import (
    PortalProposalProcessAdapter,
    build_process_proposal_execution_adapter,
)
from .worker_registry import load_worker_backends
from .runtime import (
    PortalRunStore,
    run_portal_until_idle,
)
from .route_resolver import PortalRouteAdvertisement
from .session import PortalCommandSession
from .task_currentness import LocalProjectRunnerTaskCurrentness
from .wave_runtime import (
    PortalWaveStore,
    execute_portal_source_proposal,
    prepare_portal_wave,
    promote_portal_source_proposal,
    promote_portal_wave_packet,
    reconcile_portal_source_proposal,
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
        "--static-projects",
        action="store_true",
        help=(
            "use --projects exactly as supplied instead of refreshing live "
            "membership for the safe host-session path"
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
    run.add_argument("--nodes", type=Path)
    run.add_argument(
        "--resume",
        action="store_true",
        help="restart from the durable non-secret configuration of an existing session",
    )
    run.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    run.add_argument("--run-id", default="portal-default")
    run.add_argument("--holder")
    run.add_argument("--lease-ttl", type=float, default=300.0)
    run.add_argument("--max-parallel", type=int, default=6)
    run.add_argument("--session-id")
    run.add_argument(
        "--wave",
        type=Path,
        default=ROOT / "portfolio" / "advancement_wave.public.json",
    )
    run.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "portfolio" / "corpus.public.json",
    )
    run.add_argument("--max-per-identity", type=int, default=2)
    run.add_argument("--max-per-family", type=int, default=2)
    run.add_argument("--max-per-lane", type=int, default=2)
    run.add_argument("--verifier", default="vera")
    run.add_argument(
        "--host-bridge",
        action="store_true",
        help="queue admitted work for an external ChatGPT/plugin host",
    )
    run.add_argument("--worker-backends", type=Path)
    run.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(".portal/workers"),
    )
    run.add_argument("--worker-holder-prefix", default="portal")
    run.add_argument("--delivery-lease-ttl", type=float, default=300.0)
    run.add_argument(
        "--occupied-node",
        action="append",
        default=[],
        dest="occupied_nodes",
        metavar="NODE=COUNT",
    )
    run.add_argument(
        "--project-runner-tasks",
        action="append",
        default=[],
        dest="project_runner_tasks",
        metavar="NODE=PATH",
        help=(
            "derive live occupied slots from a Project Runner task root; "
            "dead/PID-reused registrations reconcile to UNKNOWN_EXIT"
        ),
    )
    run.add_argument(
        "--host-node-occupancy",
        action="store_true",
        help=(
            "require fresh host-published occupancy for enabled nodes "
            "without another occupancy source"
        ),
    )
    run.add_argument(
        "--host-frontier-currentness",
        action="store_true",
        help=(
            "require fresh exact-head host currentness evidence for "
            "newly admitted repository frontiers"
        ),
    )
    run.add_argument("--max-cycles", type=int)
    run.add_argument("--max-idle-cycles", type=int)
    run.add_argument("--poll-seconds", type=float)
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
    status.add_argument("--session-id")
    status.add_argument(
        "--host-details",
        action="store_true",
        help=(
            "include queued-route diagnostics, unresolved attempts, "
            "current host routes and node occupancy"
        ),
    )
    status.add_argument(
        "--project-runner-tasks",
        action="append",
        default=[],
        dest="project_runner_tasks",
        metavar="NODE=PATH",
        help=(
            "reconcile and report Project Runner task currentness for a node; "
            "dead/PID-reused registrations become UNKNOWN_EXIT"
        ),
    )

    continue_cmd = subcommands.add_parser(
        "continue",
        help="refill the next safe generation of an existing command session",
    )
    continue_cmd.add_argument("--session-id", default="portal-default")
    continue_cmd.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    continue_cmd.add_argument("--holder")
    continue_cmd.add_argument(
        "--wave",
        type=Path,
        default=ROOT / "portfolio" / "advancement_wave.public.json",
    )
    continue_cmd.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "portfolio" / "corpus.public.json",
    )
    continue_cmd.add_argument(
        "--projects",
        type=Path,
        default=ROOT / "registry" / "projects.yaml",
    )
    continue_cmd.add_argument("--discover-owner")
    continue_cmd.add_argument("--write-live-registry", type=Path)
    continue_cmd.add_argument(
        "--static-projects",
        action="store_true",
        help=(
            "use --projects exactly as supplied instead of refreshing live "
            "membership for the safe host-session path"
        ),
    )
    continue_cmd.add_argument(
        "--resume",
        action="store_true",
        help="reuse the durable non-secret configuration from the prior run",
    )
    continue_cmd.add_argument("--nodes", type=Path)
    continue_cmd.add_argument("--lease-ttl", type=float, default=300.0)
    continue_cmd.add_argument("--max-parallel", type=int, default=6)
    continue_cmd.add_argument("--max-per-identity", type=int, default=2)
    continue_cmd.add_argument("--max-per-family", type=int, default=2)
    continue_cmd.add_argument("--max-per-lane", type=int, default=2)
    continue_cmd.add_argument("--verifier", default="vera")
    continue_cmd.add_argument(
        "--host-bridge",
        action="store_true",
        help="queue admitted work for an external ChatGPT/plugin host",
    )
    continue_cmd.add_argument("--worker-backends", type=Path)
    continue_cmd.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(".portal/workers"),
    )
    continue_cmd.add_argument("--worker-holder-prefix", default="portal")
    continue_cmd.add_argument("--delivery-lease-ttl", type=float, default=300.0)
    continue_cmd.add_argument(
        "--occupied-node",
        action="append",
        default=[],
        dest="occupied_nodes",
        metavar="NODE=COUNT",
    )
    continue_cmd.add_argument(
        "--project-runner-tasks",
        action="append",
        default=[],
        dest="project_runner_tasks",
        metavar="NODE=PATH",
        help="take a fresh Project Runner task-currentness occupancy snapshot",
    )
    continue_cmd.add_argument(
        "--host-node-occupancy",
        action="store_true",
        help=(
            "require fresh host-published occupancy for enabled nodes "
            "without another occupancy source"
        ),
    )
    continue_cmd.add_argument(
        "--host-frontier-currentness",
        action="store_true",
        help=(
            "require fresh exact-head host currentness evidence for "
            "newly admitted repository frontiers"
        ),
    )

    hold = subcommands.add_parser(
        "hold",
        help="durably prevent future refill for a subject without cancelling active effects",
    )
    hold.add_argument("--session-id", default="portal-default")
    hold.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    hold.add_argument("--holder", default="vera")
    hold.add_argument("--subject-kind", default="repository")
    hold.add_argument("--subject-id", required=True)

    complete = subcommands.add_parser(
        "complete",
        help="verify and close one active command-session subject",
    )
    complete.add_argument("--session-id", default="portal-default")
    complete.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    complete.add_argument("--holder", default="vera")
    complete.add_argument("--subject-kind", default="repository")
    complete.add_argument("--subject-id", required=True)
    complete.add_argument("--verifier", default="vera")
    complete.add_argument(
        "--host-bridge",
        action="store_true",
        help="verify completion through the subject's bound external host route",
    )

    stop = subcommands.add_parser(
        "stop",
        help="stop new admission/refill while preserving active and unresolved work",
    )
    stop.add_argument("--session-id", default="portal-default")
    stop.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    stop.add_argument("--holder", default="vera")

    host = subcommands.add_parser(
        "host",
        help="manage durable external host routes and dispatch evidence",
    )
    host_subcommands = host.add_subparsers(
        dest="host_command",
        required=True,
    )

    host_advertise = host_subcommands.add_parser(
        "advertise",
        help="advertise one current target-bound host execution route",
    )
    host_advertise.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_advertise.add_argument("--adapter-id", required=True)
    host_advertise.add_argument("--route-id", required=True)
    host_advertise.add_argument("--node-id", required=True)
    host_advertise.add_argument("--target-kind", default="repository")
    host_advertise.add_argument("--target-id", required=True)
    host_advertise.add_argument(
        "--capability",
        action="append",
        required=True,
        dest="capabilities",
    )
    host_advertise.add_argument(
        "--effect-capability",
        action="append",
        required=True,
        dest="effect_capabilities",
    )
    host_advertise.add_argument(
        "--authorized-effect",
        action="append",
        required=True,
        dest="authorized_effects",
    )
    host_advertise.add_argument("--preference", type=int, default=0)
    host_advertise.add_argument("--ttl-seconds", type=float, default=300.0)
    host_advertise.add_argument("--unavailable", action="store_true")
    host_advertise.add_argument("--detached", action="store_true")
    host_advertise.add_argument("--stale", action="store_true")

    host_routes = host_subcommands.add_parser(
        "routes",
        help="show non-expired host route advertisements",
    )
    host_routes.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )

    host_occupancy = host_subcommands.add_parser(
        "occupancy",
        help="publish one expiring host-observed node occupancy snapshot",
    )
    host_occupancy.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_occupancy.add_argument("--node-id", required=True)
    host_occupancy.add_argument("--occupied-slots", type=int, required=True)
    host_occupancy.add_argument("--ttl-seconds", type=float, default=300.0)

    host_occupancy_status = host_subcommands.add_parser(
        "occupancy-status",
        help="show non-expired host node occupancy snapshots",
    )
    host_occupancy_status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )

    host_frontier_advertise = host_subcommands.add_parser(
        "frontier-advertise",
        help="attest one bounded frontier against an exact repository head",
    )
    host_frontier_advertise.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_frontier_advertise.add_argument(
        "--wave",
        type=Path,
        default=ROOT / "portfolio" / "advancement_wave.public.json",
    )
    host_frontier_advertise.add_argument("--subject-id", required=True)
    host_frontier_advertise.add_argument("--ref", required=True)
    host_frontier_advertise.add_argument("--exact-head", required=True)
    host_frontier_advertise.add_argument(
        "--disposition",
        choices=("CURRENT", "HELD"),
        default="CURRENT",
    )
    host_frontier_advertise.add_argument(
        "--ttl-seconds",
        type=float,
        default=300.0,
    )

    host_frontier_status = host_subcommands.add_parser(
        "frontier-status",
        help="show non-expired host semantic frontier attestations",
    )
    host_frontier_status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_frontier_status.add_argument(
        "--wave",
        type=Path,
        help="also classify the supplied wave against live frontier evidence",
    )

    host_pending = host_subcommands.add_parser(
        "pending",
        help="show exact host dispatches not yet marked attempted",
    )
    host_pending.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_pending.add_argument("--session-id")

    host_take = host_subcommands.add_parser(
        "take",
        help=(
            "atomically take the next queued dispatch for one of the "
            "host's available adapters and cross the attempt boundary"
        ),
    )
    host_take.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_take.add_argument("--session-id")
    host_take.add_argument(
        "--adapter-id",
        action="append",
        required=True,
        dest="adapter_ids",
    )
    host_take.add_argument("--attempt-id", required=True)
    host_take.add_argument("--evidence-id", required=True)

    host_unresolved = host_subcommands.add_parser(
        "unresolved",
        help="show attempted host effects that still require reconciliation",
    )
    host_unresolved.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_unresolved.add_argument("--session-id")

    host_attempt = host_subcommands.add_parser(
        "attempt",
        help="durably cross the effect boundary before invoking a host route",
    )
    host_attempt.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_attempt.add_argument("--dispatch-id", required=True)
    host_attempt.add_argument("--attempt-id", required=True)
    host_attempt.add_argument("--evidence-id", required=True)

    host_reconcile = host_subcommands.add_parser(
        "reconcile",
        help="record host-owned execution reconciliation evidence",
    )
    host_reconcile.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_reconcile.add_argument("--dispatch-id", required=True)
    host_reconcile.add_argument(
        "--state",
        choices=(
            "IN_PROGRESS",
            "OUTCOME_UNKNOWN",
            "VERIFIED_COMPLETE",
            "VERIFIED_HELD",
        ),
        required=True,
    )
    host_reconcile.add_argument("--evidence-id", required=True)

    host_pump = host_subcommands.add_parser(
        "pump",
        help="execute queued host dispatches through configured bounded drivers",
    )
    host_pump.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    host_pump.add_argument("--drivers", type=Path, required=True)
    host_pump.add_argument("--session-id")
    host_pump.add_argument("--max-dispatches", type=int)

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

    wave_promote = wave_subcommands.add_parser(
        "promote",
        help=(
            "promote one exact Portal packet through Project Runner review "
            "and execution-authority gates"
        ),
    )
    wave_promote.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_promote.add_argument("--run-id", default="portal-wave-default")
    wave_promote.add_argument("--subject-id", required=True)
    wave_promote.add_argument("--review", type=Path, required=True)
    wave_promote.add_argument("--execution-grant", type=Path, required=True)
    wave_promote.add_argument("--effect-grant", type=Path)

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

    wave_proposal_promote = wave_subcommands.add_parser(
        "proposal-promote",
        help="promote one exact persisted source-tree proposal",
    )
    wave_proposal_promote.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_proposal_promote.add_argument("--run-id", default="portal-wave-default")
    wave_proposal_promote.add_argument("--subject-id", required=True)
    wave_proposal_promote.add_argument("--review", type=Path, required=True)
    wave_proposal_promote.add_argument(
        "--execution-grant",
        type=Path,
        required=True,
    )
    wave_proposal_promote.add_argument(
        "--effect-grant",
        type=Path,
        required=True,
    )

    wave_proposal_execute = wave_subcommands.add_parser(
        "proposal-execute",
        help="execute one previously promoted source-tree proposal",
    )
    wave_proposal_execute.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_proposal_execute.add_argument("--run-id", default="portal-wave-default")
    wave_proposal_execute.add_argument("--subject-id", required=True)

    wave_proposal_reconcile = wave_subcommands.add_parser(
        "proposal-reconcile",
        help="reconcile one ambiguous source-tree proposal without replay",
    )
    wave_proposal_reconcile.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    wave_proposal_reconcile.add_argument("--run-id", default="portal-wave-default")
    wave_proposal_reconcile.add_argument("--subject-id", required=True)
    wave_proposal_reconcile.add_argument("--reconciler", required=True)

    ecosystem = subcommands.add_parser(
        "ecosystem",
        help="run and inspect the durable whole-repository coordinator session",
    )
    ecosystem_subcommands = ecosystem.add_subparsers(
        dest="ecosystem_command",
        required=True,
    )

    ecosystem_propose = ecosystem_subcommands.add_parser(
        "propose",
        help=(
            "continuously refill parallel repository lanes and generate "
            "bounded source-tree proposals"
        ),
    )
    ecosystem_propose.add_argument(
        "--wave",
        type=Path,
        default=ROOT / "portfolio" / "advancement_wave.public.json",
    )
    ecosystem_propose.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "portfolio" / "corpus.public.json",
    )
    ecosystem_propose.add_argument(
        "--projects",
        type=Path,
        default=ROOT / "registry" / "projects.yaml",
    )
    ecosystem_propose.add_argument("--discover-owner")
    ecosystem_propose.add_argument("--write-live-registry", type=Path)
    ecosystem_propose.add_argument("--nodes", type=Path, required=True)
    ecosystem_propose.add_argument("--worker-backends", type=Path, required=True)
    ecosystem_propose.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    ecosystem_propose.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(".portal/workers"),
    )
    ecosystem_propose.add_argument("--session-id", default="portal-ecosystem")
    ecosystem_propose.add_argument("--holder", default="vera")
    ecosystem_propose.add_argument("--holder-prefix", default="portal")
    ecosystem_propose.add_argument("--lease-ttl", type=float, default=1800.0)
    ecosystem_propose.add_argument(
        "--delivery-lease-ttl",
        type=float,
        default=900.0,
    )
    ecosystem_propose.add_argument("--max-generations", type=int, default=100)
    ecosystem_propose.add_argument("--max-parallel", type=int, default=6)
    ecosystem_propose.add_argument("--max-per-identity", type=int, default=2)
    ecosystem_propose.add_argument("--max-per-family", type=int, default=2)
    ecosystem_propose.add_argument("--max-per-lane", type=int, default=2)

    ecosystem_status = ecosystem_subcommands.add_parser(
        "status",
        help="show the durable whole-repository coordinator session",
    )
    ecosystem_status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".portal/portal.sqlite3"),
    )
    ecosystem_status.add_argument("--session-id", default="portal-ecosystem")
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


def _occupied_node_slots(values: Sequence[str]) -> dict[str, int]:
    occupied: dict[str, int] = {}
    for raw in values:
        node_id, separator, count_text = raw.partition("=")
        node_id = node_id.strip()
        if not separator or not node_id:
            raise ValueError("occupied node must use NODE=COUNT")
        try:
            count = int(count_text)
        except ValueError as exc:
            raise ValueError("occupied node count must be an integer") from exc
        if count < 0:
            raise ValueError("occupied node count must be non-negative")
        if node_id in occupied:
            raise ValueError(f"duplicate occupied node: {node_id}")
        occupied[node_id] = count
    return occupied


def _project_runner_task_roots(values: Sequence[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for raw in values:
        node_id, separator, path_text = raw.partition("=")
        node_id = node_id.strip()
        path_text = path_text.strip()
        if not separator or not node_id or not path_text:
            raise ValueError("project runner tasks must use NODE=PATH")
        if node_id in roots:
            raise ValueError(f"duplicate Project Runner task source: {node_id}")
        roots[node_id] = Path(path_text)
    return roots


def _project_runner_status_payload(
    values: Sequence[str],
) -> dict[str, object]:
    roots = _project_runner_task_roots(values)
    nodes: dict[str, object] = {}
    total_occupied_slots = 0
    total_reconciled_unknown_exit = 0

    for node_id, tasks_root in sorted(roots.items()):
        snapshot = LocalProjectRunnerTaskCurrentness(
            node_id=node_id,
            tasks_root=tasks_root,
        ).snapshot()
        nodes[node_id] = {
            "tasks_root": str(snapshot.tasks_root),
            "occupied_slots": snapshot.occupied_slots,
            "reconciled_unknown_exit": snapshot.reconciled_unknown_exit,
            "state_counts": dict(sorted(snapshot.state_counts.items())),
        }
        total_occupied_slots += snapshot.occupied_slots
        total_reconciled_unknown_exit += snapshot.reconciled_unknown_exit

    return {
        "total_occupied_slots": total_occupied_slots,
        "total_reconciled_unknown_exit": total_reconciled_unknown_exit,
        "nodes": nodes,
    }


def _session_occupancy(
    args: argparse.Namespace,
    nodes,
) -> tuple[dict[str, int] | None, object | None]:
    static = _occupied_node_slots(args.occupied_nodes)
    task_roots = _project_runner_task_roots(args.project_runner_tasks)
    overlap = sorted(set(static).intersection(task_roots))
    if overlap:
        raise ValueError(
            "multiple occupancy sources for node: " + ", ".join(overlap)
        )

    probes = tuple(
        LocalProjectRunnerTaskCurrentness(
            node_id=node_id,
            tasks_root=task_root,
        )
        for node_id, task_root in sorted(task_roots.items())
    )
    explicit_nodes = set(static).union(task_roots)
    required_host_nodes: tuple[str, ...] = ()
    if args.host_node_occupancy:
        required_host_nodes = tuple(
            sorted(
                node.node_id
                for node in nodes
                if node.enabled and node.node_id not in explicit_nodes
            )
        )

    if not probes and not required_host_nodes:
        return static, None

    def provider() -> dict[str, int]:
        occupied = dict(static)
        for probe in probes:
            snapshot = probe()
            for node_id, count in snapshot.items():
                if node_id in occupied:
                    raise ValueError(
                        f"multiple occupancy sources for node: {node_id}"
                    )
                occupied[node_id] = count

        if required_host_nodes:
            store = PortalHostBridgeStore(Path(args.state_db))
            try:
                host_snapshot = PortalHostNodeCurrentness(
                    store=store,
                    required_node_ids=required_host_nodes,
                )()
            finally:
                store.close()
            for node_id, count in host_snapshot.items():
                if node_id in occupied:
                    raise ValueError(
                        f"multiple occupancy sources for node: {node_id}"
                    )
                occupied[node_id] = count
        return occupied

    return None, provider


def _inferred_wave_owner(wave_path: Path) -> str:
    wave = load_advancement_wave(Path(wave_path))
    owners: set[str] = set()
    canonical: dict[str, str] = {}
    for item in wave.items:
        if item.subject_kind != "repository":
            continue
        for repository in item.repositories:
            owner, separator, _name = repository.partition("/")
            if not separator or not owner:
                raise ValueError("wave repository must use owner/name")
            key = owner.casefold()
            owners.add(key)
            canonical.setdefault(key, owner)
    if len(owners) != 1:
        raise ValueError(
            "cannot infer one portfolio owner from wave; "
            "use --discover-owner or --static-projects"
        )
    key = next(iter(owners))
    return canonical[key]


def _session_portfolio_paths(
    args: argparse.Namespace,
) -> tuple[Path, Path, Path]:
    projects_path = Path(args.projects)
    wave_path = Path(args.wave)
    corpus_path = Path(args.corpus)
    discover_owner = getattr(args, "discover_owner", None)
    if getattr(args, "static_projects", False):
        if discover_owner:
            raise ValueError(
                "--static-projects cannot be combined with --discover-owner"
            )
        return wave_path, corpus_path, projects_path

    safe_host_live = bool(
        getattr(args, "host_bridge", False)
        and getattr(args, "host_frontier_currentness", False)
    )
    if not discover_owner and not safe_host_live:
        return wave_path, corpus_path, projects_path

    owner = (
        str(discover_owner).strip()
        if discover_owner
        else _inferred_wave_owner(wave_path)
    )
    if not owner:
        raise ValueError("portfolio owner is required")

    repositories = GitHubRepositoryCatalog(
        token=_github_token(),
    ).list_owned_repositories(owner)
    curated = load_project_snapshot(projects_path)
    registry = build_live_project_registry(
        owner=owner,
        repositories=repositories,
        curated_projects=curated.projects,
    )
    live_registry_path = (
        Path(args.write_live_registry)
        if getattr(args, "write_live_registry", None) is not None
        else Path(args.state_db).parent / "projects.live.yaml"
    )
    write_project_registry(live_registry_path, registry)

    live = refresh_live_public_portfolio(
        baseline_corpus_path=corpus_path,
        baseline_wave_path=wave_path,
        repositories=repositories,
        observed_at=datetime.now(timezone.utc).isoformat(),
        output_dir=Path(args.state_db).parent / "live-portfolio",
    )
    return live.wave_path, live.corpus_path, live_registry_path


def _session_projects_path(args: argparse.Namespace) -> Path:
    return _session_portfolio_paths(args)[2]

def _session_frontier_currentness(args: argparse.Namespace):
    if not args.host_frontier_currentness:
        return None

    transport = GitHubRestTransport(token=_github_token())

    def provider(wave_path: Path) -> tuple[tuple[str, str], ...]:
        store = PortalHostFrontierStore(Path(args.state_db))
        try:
            snapshot = PortalHostFrontierCurrentness(
                store=store,
                head_reader=transport.read_ref,
            ).classify(load_advancement_wave(Path(wave_path)))
        finally:
            store.close()
        return snapshot.excluded_subjects

    return provider


def _session_budget(args: argparse.Namespace) -> WaveExecutionBudget:
    return WaveExecutionBudget(
        max_parallel=args.max_parallel,
        max_per_identity=args.max_per_identity,
        max_per_family=args.max_per_family,
        max_per_lane=args.max_per_lane,
    )


def _session_execution_adapter(
    args: argparse.Namespace,
    nodes,
):
    if args.host_bridge and args.worker_backends is not None:
        raise ValueError("choose host bridge or worker backends, not both")
    if args.host_bridge:
        return PortalHostExecutionAdapter(
            store=PortalHostBridgeStore(Path(args.state_db)),
        )
    if args.worker_backends is None:
        return None

    driver = PortalProposalProcessAdapter(
        state_db=Path(args.state_db),
        nodes=tuple(nodes),
        backends=load_worker_backends(Path(args.worker_backends)),
        workspace_root=Path(args.workspace_root),
        holder_prefix=args.worker_holder_prefix,
        delivery_lease_ttl=args.delivery_lease_ttl,
        token=_github_token(),
    )
    return build_process_proposal_execution_adapter(driver)


def _session_result_payload(
    *,
    mode: str,
    result,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "mode": mode,
        "session_id": result.session_id,
        "control_state": result.control_state,
        "summary": result.summary,
        "protected_effects_authorized": False,
    }
    if hasattr(result, "generation"):
        payload["generation"] = result.generation
        payload["wave_run_id"] = result.wave_run_id
        payload["admitted"] = len(result.packets)
    if hasattr(result, "cycles"):
        payload["generations"] = len(result.cycles)
        payload["stop_reason"] = result.stop_reason
        payload["idle_cycles"] = result.idle_cycles
    return payload


def _session_resume_spec(
    args: argparse.Namespace,
) -> dict[str, object]:
    return {
        "schema": "PORTAL_COMMAND_SESSION_RESUME_V1",
        "holder": args.holder,
        "wave": str(Path(args.wave)),
        "corpus": str(Path(args.corpus)),
        "projects": str(Path(args.projects)),
        "discover_owner": args.discover_owner,
        "write_live_registry": (
            str(Path(args.write_live_registry))
            if args.write_live_registry is not None
            else None
        ),
        "static_projects": bool(args.static_projects),
        "nodes": str(Path(args.nodes)),
        "lease_ttl": float(args.lease_ttl),
        "max_parallel": int(args.max_parallel),
        "max_per_identity": int(args.max_per_identity),
        "max_per_family": int(args.max_per_family),
        "max_per_lane": int(args.max_per_lane),
        "verifier": args.verifier,
        "host_bridge": bool(args.host_bridge),
        "worker_backends": (
            str(Path(args.worker_backends))
            if args.worker_backends is not None
            else None
        ),
        "workspace_root": str(Path(args.workspace_root)),
        "worker_holder_prefix": args.worker_holder_prefix,
        "delivery_lease_ttl": float(args.delivery_lease_ttl),
        "occupied_nodes": list(args.occupied_nodes),
        "project_runner_tasks": list(args.project_runner_tasks),
        "host_node_occupancy": bool(args.host_node_occupancy),
        "host_frontier_currentness": bool(
            args.host_frontier_currentness
        ),
    }


def _apply_session_resume_spec(
    args: argparse.Namespace,
    spec: dict[str, object],
) -> None:
    if spec.get("schema") != "PORTAL_COMMAND_SESSION_RESUME_V1":
        raise ValueError("unsupported Portal session resume spec")

    required = {
        "holder",
        "wave",
        "corpus",
        "projects",
        "discover_owner",
        "write_live_registry",
        "static_projects",
        "nodes",
        "lease_ttl",
        "max_parallel",
        "max_per_identity",
        "max_per_family",
        "max_per_lane",
        "verifier",
        "host_bridge",
        "worker_backends",
        "workspace_root",
        "worker_holder_prefix",
        "delivery_lease_ttl",
        "occupied_nodes",
        "project_runner_tasks",
        "host_node_occupancy",
        "host_frontier_currentness",
    }
    missing = sorted(required.difference(spec))
    if missing:
        raise ValueError(
            "Portal session resume spec is missing: " + ", ".join(missing)
        )

    def required_text(key: str) -> str:
        value = spec[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"resume spec {key} must be a non-empty string")
        return value.strip()

    def optional_text(key: str) -> str | None:
        value = spec[key]
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"resume spec {key} must be null or a non-empty string"
            )
        return value.strip()

    def integer(key: str) -> int:
        value = spec[key]
        if type(value) is not int or value < 1:
            raise ValueError(f"resume spec {key} must be a positive integer")
        return value

    def number(key: str) -> float:
        value = spec[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"resume spec {key} must be numeric")
        value = float(value)
        if value <= 0:
            raise ValueError(f"resume spec {key} must be positive")
        return value

    def boolean(key: str) -> bool:
        value = spec[key]
        if type(value) is not bool:
            raise ValueError(f"resume spec {key} must be a boolean")
        return value

    def string_list(key: str) -> list[str]:
        value = spec[key]
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip()
            for item in value
        ):
            raise ValueError(
                f"resume spec {key} must be a list of non-empty strings"
            )
        return [item.strip() for item in value]

    stored_holder = required_text("holder")
    if args.holder is not None and args.holder.strip() != stored_holder:
        raise ValueError("resume holder differs from stored session holder")

    args.holder = stored_holder
    args.wave = Path(required_text("wave"))
    args.corpus = Path(required_text("corpus"))
    args.projects = Path(required_text("projects"))
    args.discover_owner = optional_text("discover_owner")
    write_live_registry = optional_text("write_live_registry")
    args.write_live_registry = (
        Path(write_live_registry)
        if write_live_registry is not None
        else None
    )
    args.static_projects = boolean("static_projects")
    args.nodes = Path(required_text("nodes"))
    args.lease_ttl = number("lease_ttl")
    args.max_parallel = integer("max_parallel")
    args.max_per_identity = integer("max_per_identity")
    args.max_per_family = integer("max_per_family")
    args.max_per_lane = integer("max_per_lane")
    args.verifier = required_text("verifier")
    args.host_bridge = boolean("host_bridge")
    worker_backends = optional_text("worker_backends")
    args.worker_backends = (
        Path(worker_backends)
        if worker_backends is not None
        else None
    )
    args.workspace_root = Path(required_text("workspace_root"))
    args.worker_holder_prefix = required_text("worker_holder_prefix")
    args.delivery_lease_ttl = number("delivery_lease_ttl")
    args.occupied_nodes = string_list("occupied_nodes")
    args.project_runner_tasks = string_list("project_runner_tasks")
    args.host_node_occupancy = boolean("host_node_occupancy")
    args.host_frontier_currentness = boolean(
        "host_frontier_currentness"
    )


def _session_run_payload(args: argparse.Namespace) -> dict[str, object]:
    controller = PortalCommandSession(Path(args.state_db))
    execution_adapter = None
    try:
        if args.resume:
            spec = controller.load_resume_spec(
                session_id=args.session_id,
                holder=args.holder,
            )
            _apply_session_resume_spec(args, spec)
        else:
            if args.holder is None:
                args.holder = "vera"
            if args.nodes is None:
                raise ValueError("--nodes is required unless --resume")

        wave_path, corpus_path, projects_path = _session_portfolio_paths(args)
        nodes = load_execution_nodes(Path(args.nodes))
        execution_adapter = _session_execution_adapter(args, nodes)
        occupied_node_slots, node_occupancy_provider = _session_occupancy(
            args,
            nodes,
        )
        frontier_currentness_provider = _session_frontier_currentness(args)
        common = dict(
            session_id=args.session_id,
            holder=args.holder,
            wave_path=wave_path,
            corpus_path=corpus_path,
            projects_path=projects_path,
            nodes=nodes,
            budget=_session_budget(args),
            lease_ttl=args.lease_ttl,
            token=_github_token(),
            occupied_node_slots=occupied_node_slots,
            frontier_currentness_provider=frontier_currentness_provider,
        )
        if args.once:
            once_common = dict(common)
            if node_occupancy_provider is not None:
                once_common["occupied_node_slots"] = node_occupancy_provider()
            result = controller.run(
                **once_common,
                execution_adapter=execution_adapter,
            )
        else:
            result = controller.run_until_idle(
                **common,
                verifier=args.verifier,
                max_cycles=(
                    0 if args.max_cycles is None else args.max_cycles
                ),
                max_idle_cycles=(
                    0
                    if args.max_idle_cycles is None
                    else args.max_idle_cycles
                ),
                poll_seconds=(
                    5.0 if args.poll_seconds is None else args.poll_seconds
                ),
                node_occupancy_provider=node_occupancy_provider,
                execution_adapter=execution_adapter,
                resume_spec=_session_resume_spec(args),
            )
        controller.save_resume_spec(
            session_id=args.session_id,
            holder=args.holder,
            spec=_session_resume_spec(args),
        )
    finally:
        controller.close()
        if isinstance(execution_adapter, PortalHostExecutionAdapter):
            execution_adapter.store.close()
    return _session_result_payload(
        mode="PORTAL_COMMAND_SESSION_RUN_V1",
        result=result,
    )


def _continue_payload(args: argparse.Namespace) -> dict[str, object]:
    controller = PortalCommandSession(Path(args.state_db))
    execution_adapter = None
    try:
        if args.resume:
            spec = controller.load_resume_spec(
                session_id=args.session_id,
                holder=args.holder,
            )
            _apply_session_resume_spec(args, spec)
        else:
            if args.holder is None:
                args.holder = "vera"
            if args.nodes is None:
                raise ValueError("--nodes is required unless --resume")

        wave_path, corpus_path, projects_path = _session_portfolio_paths(args)
        nodes = load_execution_nodes(Path(args.nodes))
        execution_adapter = _session_execution_adapter(args, nodes)
        occupied_node_slots, node_occupancy_provider = _session_occupancy(
            args,
            nodes,
        )
        frontier_currentness_provider = _session_frontier_currentness(args)
        if node_occupancy_provider is not None:
            occupied_node_slots = node_occupancy_provider()

        result = controller.continue_run(
            session_id=args.session_id,
            holder=args.holder,
            wave_path=wave_path,
            corpus_path=corpus_path,
            projects_path=projects_path,
            nodes=nodes,
            budget=_session_budget(args),
            lease_ttl=args.lease_ttl,
            token=_github_token(),
            verifier=args.verifier,
            occupied_node_slots=occupied_node_slots,
            frontier_currentness_provider=frontier_currentness_provider,
            execution_adapter=execution_adapter,
        )
        controller.save_resume_spec(
            session_id=args.session_id,
            holder=args.holder,
            spec=_session_resume_spec(args),
        )
    finally:
        controller.close()
        if isinstance(execution_adapter, PortalHostExecutionAdapter):
            execution_adapter.store.close()
    return _session_result_payload(
        mode="PORTAL_COMMAND_SESSION_CONTINUE_V1",
        result=result,
    )


def _hold_payload(args: argparse.Namespace) -> dict[str, object]:
    controller = PortalCommandSession(Path(args.state_db))
    try:
        status = controller.hold(
            session_id=args.session_id,
            holder=args.holder,
            subject_kind=args.subject_kind,
            subject_id=args.subject_id,
        )
    finally:
        controller.close()
    return {
        "mode": "PORTAL_COMMAND_SESSION_HOLD_V1",
        **status,
        "protected_effects_authorized": False,
    }


def _complete_payload(args: argparse.Namespace) -> dict[str, object]:
    execution_adapter = (
        PortalHostExecutionAdapter(
            store=PortalHostBridgeStore(Path(args.state_db)),
        )
        if args.host_bridge
        else None
    )
    controller = PortalCommandSession(Path(args.state_db))
    try:
        status = controller.complete(
            session_id=args.session_id,
            holder=args.holder,
            subject_kind=args.subject_kind,
            subject_id=args.subject_id,
            verifier=args.verifier,
            token=_github_token(),
            execution_adapter=execution_adapter,
        )
    finally:
        controller.close()
        if isinstance(execution_adapter, PortalHostExecutionAdapter):
            execution_adapter.store.close()
    return {
        "mode": "PORTAL_COMMAND_SESSION_COMPLETE_V1",
        **status,
        "protected_effects_authorized": False,
    }


def _stop_payload(args: argparse.Namespace) -> dict[str, object]:
    controller = PortalCommandSession(Path(args.state_db))
    try:
        status = controller.stop(
            session_id=args.session_id,
            holder=args.holder,
        )
    finally:
        controller.close()
    return {
        "mode": "PORTAL_COMMAND_SESSION_STOP_V1",
        **status,
        "protected_effects_authorized": False,
    }


def _run_payload(args: argparse.Namespace) -> dict[str, object]:
    if args.session_id:
        return _session_run_payload(args)
    if args.resume:
        raise ValueError("--resume requires --session-id")
    if args.worker_backends is not None:
        raise ValueError("--worker-backends requires --session-id")
    if args.nodes is None:
        raise ValueError("--nodes is required")
    if args.holder is None:
        args.holder = "vera"

    project_snapshot, portfolio_source = _run_project_snapshot(args)
    dependency_snapshot = load_dependency_snapshot(Path(args.dependencies))
    worker_snapshot = load_worker_snapshot(Path(args.workers))
    nodes = load_execution_nodes(Path(args.nodes))

    max_cycles = (
        1
        if args.once
        else (100 if args.max_cycles is None else args.max_cycles)
    )
    max_idle_cycles = (
        1
        if args.once
        else (
            1
            if args.max_idle_cycles is None
            else args.max_idle_cycles
        )
    )
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
        poll_seconds=(
            0.0
            if args.once or args.poll_seconds is None
            else args.poll_seconds
        ),
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
    if args.session_id:
        controller = PortalCommandSession(Path(args.state_db))
        try:
            status = controller.status(args.session_id)
        finally:
            controller.close()

        payload: dict[str, object] = {
            "mode": "PORTAL_COMMAND_SESSION_STATUS_V1",
            **status,
        }
        if args.host_details:
            host_store = PortalHostBridgeStore(Path(args.state_db))
            try:
                payload["host"] = build_host_diagnostics(
                    host_store,
                    session_id=args.session_id,
                )
            finally:
                host_store.close()
        if args.project_runner_tasks:
            payload["project_runner"] = _project_runner_status_payload(
                args.project_runner_tasks,
            )
        return payload

    if args.host_details:
        raise ValueError("--host-details requires --session-id")

    store = PortalRunStore(Path(args.state_db))
    try:
        summary = store.summary(args.run_id)
    finally:
        store.close()
    payload = {
        "mode": "PORTAL_RUN_STATUS_V1",
        "run": summary,
    }
    if args.project_runner_tasks:
        payload["project_runner"] = _project_runner_status_payload(
            args.project_runner_tasks,
        )
    return payload


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


def _wave_promote_payload(args: argparse.Namespace) -> dict[str, object]:
    review_document = load_json_document(Path(args.review))
    execution_document = load_json_document(Path(args.execution_grant))
    effect_document = (
        load_json_document(Path(args.effect_grant))
        if args.effect_grant is not None
        else None
    )
    receipt = promote_portal_wave_packet(
        state_db=Path(args.state_db),
        run_id=args.run_id,
        subject_id=args.subject_id,
        review_document=review_document,
        execution_grant_document=execution_document,
        effect_grant_document=effect_document,
        review_key=review_key_from_environment(),
        execution_authority_key=execution_authority_key_from_environment(),
        effect_authority_key=effect_authority_key_from_environment(),
        token=_github_token(),
    )
    protected = receipt.effect_class != "NO_PROTECTED_EFFECT"
    return {
        "mode": "PORTAL_WAVE_PROMOTE_V1",
        "promoted": True,
        "effect_class": receipt.effect_class,
        "promotion_sha256": receipt.promotion_sha256,
        "protected_effects_authorized": protected,
        "source_mutation_authorized": receipt.effect_class == "SOURCE_WRITE",
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



def _wave_proposal_promote_payload(args: argparse.Namespace) -> dict[str, object]:
    receipt = promote_portal_source_proposal(
        state_db=Path(args.state_db),
        run_id=args.run_id,
        subject_id=args.subject_id,
        review_document=load_json_document(Path(args.review)),
        execution_grant_document=load_json_document(Path(args.execution_grant)),
        effect_grant_document=load_json_document(Path(args.effect_grant)),
        review_key=review_key_from_environment(),
        execution_authority_key=execution_authority_key_from_environment(),
        effect_authority_key=effect_authority_key_from_environment(),
        token=_github_token(),
    )
    return {
        "mode": "PORTAL_SOURCE_PROPOSAL_PROMOTE_V1",
        "promoted": True,
        "effect_class": receipt.effect_class,
        "promotion_sha256": receipt.promotion_sha256,
        "protected_effects_authorized": True,
        "source_mutation_authorized": receipt.effect_class == "SOURCE_WRITE",
    }


def _wave_proposal_execute_payload(args: argparse.Namespace) -> dict[str, object]:
    result = execute_portal_source_proposal(
        state_db=Path(args.state_db),
        run_id=args.run_id,
        subject_id=args.subject_id,
        token=_github_token(),
    )
    return {
        "mode": "PORTAL_SOURCE_PROPOSAL_EXECUTE_V1",
        "state": result.state,
        "classification": result.classification,
        "backend_executed": result.backend_executed,
        "result_head": result.result_head,
        "reason": result.reason,
    }


def _wave_proposal_reconcile_payload(args: argparse.Namespace) -> dict[str, object]:
    result = reconcile_portal_source_proposal(
        state_db=Path(args.state_db),
        run_id=args.run_id,
        subject_id=args.subject_id,
        reconciler=args.reconciler,
        token=_github_token(),
    )
    return {
        "mode": "PORTAL_SOURCE_PROPOSAL_RECONCILE_V1",
        "state": result.state,
        "classification": result.classification,
        "backend_executed": result.backend_executed,
        "result_head": result.result_head,
        "reason": result.reason,
    }


def _ecosystem_projects_path(args: argparse.Namespace) -> Path:
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


def _ecosystem_propose_payload(args: argparse.Namespace) -> dict[str, object]:
    projects_path = _ecosystem_projects_path(args)
    nodes = load_execution_nodes(Path(args.nodes))
    backends = load_worker_backends(Path(args.worker_backends))
    budget = WaveExecutionBudget(
        max_parallel=args.max_parallel,
        max_per_identity=args.max_per_identity,
        max_per_family=args.max_per_family,
        max_per_lane=args.max_per_lane,
    )
    result = run_ecosystem_proposal_generations(
        wave_path=Path(args.wave),
        corpus_path=Path(args.corpus),
        projects_path=projects_path,
        state_db=Path(args.state_db),
        nodes=nodes,
        budget=budget,
        backends=backends,
        workspace_root=Path(args.workspace_root),
        session_id=args.session_id,
        holder=args.holder,
        lease_ttl=args.lease_ttl,
        delivery_lease_ttl=args.delivery_lease_ttl,
        holder_prefix=args.holder_prefix,
        max_generations=args.max_generations,
        token=_github_token(),
    )
    return {
        "mode": "PORTAL_ECOSYSTEM_PROPOSE_V1",
        "session_id": result.session_id,
        "generations": result.generations,
        "admitted": result.admitted,
        "awaiting_promotion": result.awaiting_promotion,
        "active": result.active,
        "terminal": result.terminal,
        "duplicate_admissions": result.duplicate_admissions,
        "workstreams_remaining": result.workstreams_remaining,
        "saturated": result.saturated,
        "protected_effects_authorized": False,
        "source_mutation_authorized": False,
    }


def _host_advertise_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        route = PortalRouteAdvertisement(
            adapter_id=args.adapter_id,
            route_id=args.route_id,
            node_id=args.node_id,
            target_kind=args.target_kind,
            target_id=args.target_id,
            capabilities=tuple(args.capabilities),
            effect_capabilities=tuple(args.effect_capabilities),
            authorized_effects=tuple(args.authorized_effects),
            available=not args.unavailable,
            attached=not args.detached,
            current=not args.stale,
            preference=args.preference,
        )
        store.advertise_route(
            route,
            ttl_seconds=args.ttl_seconds,
        )
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_ROUTE_ADVERTISE_V1",
        "route": asdict(route),
    }


def _host_routes_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        routes = store.active_routes()
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_ROUTES_V1",
        "routes": [asdict(route) for route in routes],
    }


def _host_occupancy_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        occupancy = store.advertise_node_occupancy(
            node_id=args.node_id,
            occupied_slots=args.occupied_slots,
            ttl_seconds=args.ttl_seconds,
        )
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_OCCUPANCY_ADVERTISE_V1",
        "occupancy": occupancy,
    }


def _host_occupancy_status_payload(
    args: argparse.Namespace,
) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        occupied_node_slots = store.active_node_occupancy()
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_OCCUPANCY_STATUS_V1",
        "occupied_node_slots": occupied_node_slots,
    }


def _host_frontier_advertise_payload(
    args: argparse.Namespace,
) -> dict[str, object]:
    wave = load_advancement_wave(Path(args.wave))
    matches = tuple(
        item
        for item in wave.items
        if item.subject_kind == "repository"
        and item.subject_id == args.subject_id
    )
    if len(matches) != 1:
        raise ValueError("frontier subject must match exactly one repository item")
    item = matches[0]
    if len(item.repositories) != 1:
        raise ValueError("repository frontier must bind exactly one repository")

    observation = PortalFrontierObservation(
        subject_kind=item.subject_kind,
        subject_id=item.subject_id,
        repository=item.repositories[0],
        ref=args.ref,
        exact_head=args.exact_head,
        frontier_sha256=frontier_policy_sha256(item),
        disposition=args.disposition,
    )
    store = PortalHostFrontierStore(Path(args.state_db))
    try:
        store.advertise(
            observation,
            ttl_seconds=args.ttl_seconds,
        )
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_FRONTIER_ADVERTISE_V1",
        "observation": asdict(observation),
    }


def _host_frontier_status_payload(
    args: argparse.Namespace,
) -> dict[str, object]:
    store = PortalHostFrontierStore(Path(args.state_db))
    try:
        observations = [
            asdict(observation)
            for _key, observation in sorted(store.active().items())
        ]
        payload: dict[str, object] = {
            "mode": "PORTAL_HOST_FRONTIER_STATUS_V1",
            "observations": observations,
        }
        if args.wave is not None:
            transport = GitHubRestTransport(token=_github_token())
            snapshot = PortalHostFrontierCurrentness(
                store=store,
                head_reader=transport.read_ref,
            ).classify(load_advancement_wave(Path(args.wave)))
            payload["classification"] = {
                "current_subjects": [
                    {
                        "subject_kind": subject_kind,
                        "subject_id": subject_id,
                    }
                    for subject_kind, subject_id
                    in snapshot.current_subjects
                ],
                "excluded_subjects": [
                    {
                        "subject_kind": subject_kind,
                        "subject_id": subject_id,
                        "reason": snapshot.reasons[
                            (subject_kind, subject_id)
                        ],
                    }
                    for subject_kind, subject_id
                    in snapshot.excluded_subjects
                ],
            }
        return payload
    finally:
        store.close()


def _host_pending_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        dispatches = store.pending_dispatches(session_id=args.session_id)
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_PENDING_V1",
        "dispatches": list(dispatches),
    }


def _host_take_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        dispatch = store.take_pending_dispatch(
            session_id=args.session_id,
            adapter_ids=tuple(args.adapter_ids),
            attempt_id=args.attempt_id,
            evidence_id=args.evidence_id,
        )
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_TAKE_V1",
        "dispatch": dispatch,
    }


def _host_unresolved_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        dispatches = store.unresolved_dispatches(session_id=args.session_id)
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_UNRESOLVED_V1",
        "dispatches": list(dispatches),
    }


def _host_attempt_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        dispatch = store.mark_attempted(
            dispatch_id=args.dispatch_id,
            attempt_id=args.attempt_id,
            evidence_id=args.evidence_id,
        )
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_ATTEMPT_V1",
        "dispatch": dispatch,
    }


def _host_reconcile_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        dispatch = store.record_reconciliation(
            dispatch_id=args.dispatch_id,
            state=args.state,
            evidence_id=args.evidence_id,
        )
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_RECONCILE_V1",
        "dispatch": dispatch,
    }


def _plain_result_item(item: object) -> dict[str, object]:
    if is_dataclass(item):
        return asdict(item)
    if isinstance(item, dict):
        return dict(item)
    values = getattr(item, "__dict__", None)
    if isinstance(values, dict):
        return dict(values)
    raise TypeError("host pump result item is not serializable")


def _host_pump_payload(args: argparse.Namespace) -> dict[str, object]:
    drivers = load_host_command_drivers(Path(args.drivers))
    store = PortalHostBridgeStore(Path(args.state_db))
    try:
        result = PortalHostPump(
            store=store,
            drivers=drivers,
        ).run_once(
            session_id=args.session_id,
            max_dispatches=args.max_dispatches,
        )
    finally:
        store.close()
    return {
        "mode": "PORTAL_HOST_PUMP_V1",
        "items": [_plain_result_item(item) for item in result.items],
        "attempted": result.attempted,
        "verified_complete": result.verified_complete,
        "verified_held": result.verified_held,
        "in_progress": result.in_progress,
        "outcome_unknown": result.outcome_unknown,
        "no_driver": result.no_driver,
        "race_lost": result.race_lost,
        "route_unqualified": result.route_unqualified,
    }


def _ecosystem_status_payload(args: argparse.Namespace) -> dict[str, object]:
    store = PortalEcosystemStore(Path(args.state_db))
    try:
        summary = store.summary(args.session_id)
    finally:
        store.close()
    return {
        "mode": "PORTAL_ECOSYSTEM_STATUS_V1",
        "ecosystem": summary,
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
        elif args.command == "continue":
            payload = _continue_payload(args)
        elif args.command == "hold":
            payload = _hold_payload(args)
        elif args.command == "complete":
            payload = _complete_payload(args)
        elif args.command == "stop":
            payload = _stop_payload(args)
        elif args.command == "host":
            if args.host_command == "advertise":
                payload = _host_advertise_payload(args)
            elif args.host_command == "routes":
                payload = _host_routes_payload(args)
            elif args.host_command == "occupancy":
                payload = _host_occupancy_payload(args)
            elif args.host_command == "occupancy-status":
                payload = _host_occupancy_status_payload(args)
            elif args.host_command == "frontier-advertise":
                payload = _host_frontier_advertise_payload(args)
            elif args.host_command == "frontier-status":
                payload = _host_frontier_status_payload(args)
            elif args.host_command == "pending":
                payload = _host_pending_payload(args)
            elif args.host_command == "take":
                payload = _host_take_payload(args)
            elif args.host_command == "unresolved":
                payload = _host_unresolved_payload(args)
            elif args.host_command == "attempt":
                payload = _host_attempt_payload(args)
            elif args.host_command == "reconcile":
                payload = _host_reconcile_payload(args)
            elif args.host_command == "pump":
                payload = _host_pump_payload(args)
            else:
                parser.error("unsupported host command")
                return 2
        elif args.command == "wave":
            if args.wave_command == "prepare":
                payload = _wave_prepare_payload(args)
            elif args.wave_command == "promote":
                payload = _wave_promote_payload(args)
            elif args.wave_command == "claim":
                payload = _wave_claim_payload(args)
            elif args.wave_command == "receipt":
                payload = _wave_receipt_payload(args)
            elif args.wave_command == "verify":
                payload = _wave_verify_payload(args)
            elif args.wave_command == "status":
                payload = _wave_status_payload(args)
            elif args.wave_command == "proposal-promote":
                payload = _wave_proposal_promote_payload(args)
            elif args.wave_command == "proposal-execute":
                payload = _wave_proposal_execute_payload(args)
            elif args.wave_command == "proposal-reconcile":
                payload = _wave_proposal_reconcile_payload(args)
            else:
                parser.error("unsupported wave command")
                return 2
        elif args.command == "ecosystem":
            if args.ecosystem_command == "propose":
                payload = _ecosystem_propose_payload(args)
            elif args.ecosystem_command == "status":
                payload = _ecosystem_status_payload(args)
            else:
                parser.error("unsupported ecosystem command")
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
