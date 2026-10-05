from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Sequence

from runner.portfolio_advancement import load_advancement_wave
from runner.portfolio_wave_scheduler import WaveExecutionBudget

from .coordinator import plan_portal_wave
from .node_registry import load_execution_nodes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portal",
        description=(
            "P.O.R.T.A.L. — Portfolio Orchestration & Repository Tracking "
            "Access Layer"
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

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
    return parser


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


def entrypoint(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            payload = _plan_payload(args)
        else:
            parser.error("unsupported command")
            return 2
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"portal: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
