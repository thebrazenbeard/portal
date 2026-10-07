from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping

from runner.promoted_github_tree import GitHubSourceTreeFile


@dataclass(frozen=True)
class PortalSourceTreeProposal:
    repository: str
    source_ref: str
    expected_head: str
    message: str
    files: tuple[GitHubSourceTreeFile, ...]
    canonical_bytes: bytes
    sha256: str


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def load_source_tree_proposal(
    path: Path,
    *,
    packet: Mapping[str, object],
) -> PortalSourceTreeProposal:
    try:
        raw_bytes = Path(path).read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8", "strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("source-tree proposal is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("source-tree proposal must be an object")
    if payload.get("schema") != "PORTAL_SOURCE_TREE_PROPOSAL_V1":
        raise ValueError("unexpected source-tree proposal schema")

    repository = payload.get("repository")
    source_ref = payload.get("source_ref")
    expected_head = payload.get("expected_head")
    message = payload.get("message")
    if not isinstance(repository, str) or not repository:
        raise ValueError("source-tree proposal repository is required")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ValueError("source-tree proposal source_ref is required")
    if not isinstance(expected_head, str) or len(expected_head) != 40 or any(
        ch not in "0123456789abcdef" for ch in expected_head
    ):
        raise ValueError("source-tree proposal expected_head must be lowercase sha40")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("source-tree proposal message is required")

    if repository != packet.get("repository"):
        raise ValueError("source-tree proposal repository diverges from packet")
    if source_ref != packet.get("source_ref"):
        raise ValueError("source-tree proposal source_ref diverges from packet")
    if expected_head != packet.get("exact_head"):
        raise ValueError("source-tree proposal expected_head diverges from packet")
    if packet.get("effect_ceiling") != "SOURCE_ONLY":
        raise ValueError("source-tree proposal requires SOURCE_ONLY packet ceiling")
    if packet.get("source_mutation_authorized") is not False:
        raise ValueError("source-tree proposal packet must deny source mutation")

    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("source-tree proposal requires files")
    files: list[GitHubSourceTreeFile] = []
    seen: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise ValueError("source-tree proposal file must be an object")
        file = GitHubSourceTreeFile.from_mapping(raw)
        if file.path in seen:
            raise ValueError("source-tree proposal contains duplicate paths")
        seen.add(file.path)
        files.append(file)

    canonical_payload = {
        "schema": "PORTAL_SOURCE_TREE_PROPOSAL_V1",
        "repository": repository,
        "source_ref": source_ref,
        "expected_head": expected_head,
        "message": message.strip(),
        "files": [file.transport_payload() for file in files],
    }
    canonical = _canonical_bytes(canonical_payload)
    return PortalSourceTreeProposal(
        repository=repository,
        source_ref=source_ref,
        expected_head=expected_head,
        message=message.strip(),
        files=tuple(files),
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def source_tree_proposal_to_execution_request(
    proposal: PortalSourceTreeProposal,
) -> dict[str, object]:
    return {
        "schema": "PROJECT_RUNNER_GITHUB_SOURCE_TREE_WRITE_V1",
        "operation": "PUT_FILES",
        "repository": proposal.repository,
        "ref": proposal.source_ref,
        "expected_head": proposal.expected_head,
        "message": proposal.message,
        "files": [file.transport_payload() for file in proposal.files],
    }
