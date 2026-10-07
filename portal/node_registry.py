from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from .models import ExecutionNode


_SCHEMA = "PORTAL_EXECUTION_NODES_V1"
_TOP_LEVEL_KEYS = {"schema", "nodes"}
_NODE_KEYS = {"id", "max_parallel", "allowed_lanes", "enabled"}


def load_execution_nodes(path: Path) -> tuple[ExecutionNode, ...]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("execution node manifest must be a mapping")

    keys = set(payload)
    if keys != _TOP_LEVEL_KEYS:
        raise ValueError(
            "execution node manifest requires exactly schema and nodes"
        )
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported execution node manifest schema")

    raw_nodes = payload["nodes"]
    if not isinstance(raw_nodes, list):
        raise ValueError("execution node manifest nodes must be a list")

    nodes: list[ExecutionNode] = []
    seen: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, Mapping):
            raise ValueError("execution node entry must be a mapping")
        unknown = set(raw_node) - _NODE_KEYS
        if unknown:
            raise ValueError(
                "execution node entry contains unsupported fields: "
                + ", ".join(sorted(str(value) for value in unknown))
            )
        if "id" not in raw_node or "max_parallel" not in raw_node:
            raise ValueError(
                "execution node entry requires id and max_parallel"
            )
        node_id = raw_node["id"]
        if not isinstance(node_id, str):
            raise ValueError("execution node id must be a string")
        allowed_lanes = raw_node.get("allowed_lanes", [])
        if not isinstance(allowed_lanes, list):
            raise ValueError("execution node allowed_lanes must be a list")

        node = ExecutionNode(
            node_id=node_id,
            max_parallel=raw_node["max_parallel"],
            allowed_lanes=tuple(allowed_lanes),
            enabled=raw_node.get("enabled", True),
        )
        if node.node_id in seen:
            raise ValueError("duplicate execution node id")
        seen.add(node.node_id)
        nodes.append(node)

    return tuple(sorted(nodes, key=lambda node: node.node_id))
