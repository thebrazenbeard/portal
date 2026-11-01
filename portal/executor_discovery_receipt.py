"""Decode Project Runner's *observational* Executor discovery receipt.

This boundary is deliberately not an adapter, RuntimeAdvertisement, host
attestation, grant, or Portal capability admission. Project Runner's probe
talks to a caller-selected 127.0.0.1 MCP server; the server's self-declared
name and list of devices do not establish identity, consent, or authority.

Accept only the exact public, minimally scoped discovery JSON emitted by the
Runner probe. The decoder performs no I/O and never invokes a workstation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re


_SCHEMA = "PROJECT_RUNNER_EXECUTOR_MCP_DISCOVERY_V1"
_PROTOCOL = "2025-06-18"
_MAX_BYTES = 65_536
_MAX_TOOLS = 1_000
_MAX_DEVICES = 1_000
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
_FIELDS = frozenset({
    "schema",
    "observation_only",
    "execution_authorized",
    "server_name_claim",
    "protocol_version",
    "available_tools",
    "connected_device_count",
})


@dataclass(frozen=True)
class ExecutorDiscoveryEvidence:
    """Unattested source/wire evidence; cannot be used as a capability grant."""

    schema: str
    server_name_claim: str
    protocol_version: str
    available_tools: tuple[str, ...]
    connected_device_count: int
    observation_only: bool = True
    execution_authorized: bool = False
    host_authenticated: bool = False


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Executor discovery JSON key")
        result[key] = value
    return result


def _bad_constant(_value: str) -> object:
    raise ValueError("nonfinite Executor discovery JSON value")


def parse_executor_discovery_receipt(payload: str | bytes) -> ExecutorDiscoveryEvidence:
    """Parse the exact Runner stdout JSON, not a user/model-derived grant.

    Strictly rejecting extra authority claims and duplicate fields prevents
    their accidental acceptance by later callers. Even a valid result is a
    local observation with *no* authenticated host or execution capability.
    """

    if isinstance(payload, bytes):
        if len(payload) > _MAX_BYTES:
            raise ValueError("Executor discovery receipt exceeds limit")
        try:
            payload = payload.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Executor discovery receipt is not UTF-8") from exc
    elif isinstance(payload, str):
        try:
            if len(payload.encode("utf-8", "strict")) > _MAX_BYTES:
                raise ValueError("Executor discovery receipt exceeds limit")
        except UnicodeEncodeError as exc:
            raise ValueError("Executor discovery receipt has invalid Unicode") from exc
    else:
        raise ValueError("Executor discovery receipt must be UTF-8 JSON text")

    try:
        raw = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_bad_constant,
        )
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ValueError("malformed Executor discovery JSON") from exc

    if not isinstance(raw, dict) or frozenset(raw) != _FIELDS:
        raise ValueError("unexpected Executor discovery receipt fields")
    if (
        raw["schema"] != _SCHEMA
        or raw["server_name_claim"] != "executor"
        or raw["protocol_version"] != _PROTOCOL
    ):
        raise ValueError("Executor discovery source/protocol claim mismatch")
    if raw["observation_only"] is not True or raw["execution_authorized"] is not False:
        raise ValueError("Executor discovery attempted an authority claim")

    tools = raw["available_tools"]
    if (
        not isinstance(tools, list)
        or not 1 <= len(tools) <= _MAX_TOOLS
        or any(not isinstance(name, str) or _TOOL_NAME.fullmatch(name) is None for name in tools)
        or len(set(tools)) != len(tools)
        or "list_devices" not in tools
    ):
        raise ValueError("invalid Executor discovery tool list")

    count = raw["connected_device_count"]
    if type(count) is not int or not 0 <= count <= _MAX_DEVICES:
        raise ValueError("invalid Executor discovery device count")

    return ExecutorDiscoveryEvidence(
        schema=_SCHEMA,
        server_name_claim="executor",
        protocol_version=_PROTOCOL,
        available_tools=tuple(tools),
        connected_device_count=count,
    )
