from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

from portal.worker_backend import (
    ProcessWorkerSpec,
    run_process_worker,
)


def _packet() -> dict[str, object]:
    return {
        "schema": "PORTAL_WAVE_WORK_PACKET_V1",
        "run_id": "run-1",
        "subject_id": "repo-a",
        "repository": "example/repo-a",
        "source_ref": "main",
        "exact_head": "a" * 40,
        "node_id": "node-a",
        "lane_id": "LANE_A",
        "action": "EXECUTE_FRONTIER",
        "frontier": "implement the bounded change",
        "effect_ceiling": "SOURCE_ONLY",
        "review_gate": "EXACT_HEAD_REVIEW",
        "lead_identity": "vera",
        "reviewer_identities": ["reviewer"],
        "plan_sha256": "b" * 64,
        "project_runner_claim": {
            "fencing_token": 1,
            "lineage_id": "lineage",
            "work_fingerprint": "c" * 64,
        },
        "delivery_fencing_token": 1,
        "execution_authorized": True,
        "execution_effect_class": "NO_PROTECTED_EFFECT",
        "execution_promotion": {
            "promotion_sha256": "d" * 64,
            "review_sha256": "e" * 64,
            "execution_grant_sha256": "f" * 64,
            "valid_until": 9999999999.0,
        },
        "protected_effects_authorized": False,
        "source_mutation_authorized": False,
        "target_ref_mutation_authorized": False,
        "completion_contract": {
            "worker_receipt_is_completion": False,
            "independent_verification_required": True,
        },
    }


def _write_worker(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_process_worker_returns_verified_local_artifact_receipt(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "worker.py"
    _write_worker(
        worker,
        """
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--portal-packet", required=True)
p.add_argument("--portal-receipt", required=True)
args = p.parse_args()

packet = json.loads(Path(args.portal_packet).read_text(encoding="utf-8"))
assert packet["source_mutation_authorized"] is False
artifact = Path(args.portal_receipt).parent / "proposal.json"
artifact.write_text(
    json.dumps({"proposal": "bounded change"}, sort_keys=True),
    encoding="utf-8",
)
digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
Path(args.portal_receipt).write_text(
    json.dumps(
        {
            "schema": "PORTAL_WORKER_RECEIPT_V1",
            "receipt_class": "SUCCEEDED_NO_EFFECT",
            "reason": "proposal artifact produced",
            "artifacts": [
                {
                    "kind": "SOURCE_PROPOSAL",
                    "relative_path": "proposal.json",
                    "sha256": digest,
                }
            ],
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
""",
    )

    result = run_process_worker(
        packet=_packet(),
        spec=ProcessWorkerSpec(
            command=(sys.executable, str(worker)),
            timeout_seconds=10.0,
            pass_env=(),
        ),
        workspace_root=tmp_path / "workspace",
    )

    assert result.receipt_class == "SUCCEEDED_NO_EFFECT"
    assert result.reason == "proposal artifact produced"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.kind == "SOURCE_PROPOSAL"
    assert artifact.path.read_text(encoding="utf-8")
    assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    assert result.evidence_sha256 == hashlib.sha256(
        result.receipt_path.read_bytes()
    ).hexdigest()


def test_process_worker_rejects_source_mutation_receipt(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "worker.py"
    _write_worker(
        worker,
        """
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--portal-packet", required=True)
p.add_argument("--portal-receipt", required=True)
args = p.parse_args()

Path(args.portal_receipt).write_text(
    json.dumps(
        {
            "schema": "PORTAL_WORKER_RECEIPT_V1",
            "receipt_class": "SUCCEEDED_SOURCE_CHANGE",
            "reason": "I wrote source directly",
            "artifacts": [],
        }
    ),
    encoding="utf-8",
)
""",
    )

    with pytest.raises(ValueError, match="source mutation receipts are forbidden"):
        run_process_worker(
            packet=_packet(),
            spec=ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            ),
            workspace_root=tmp_path / "workspace",
        )


def test_process_worker_rejects_artifact_path_escape(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "worker.py"
    _write_worker(
        worker,
        """
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--portal-packet", required=True)
p.add_argument("--portal-receipt", required=True)
args = p.parse_args()

Path(args.portal_receipt).write_text(
    json.dumps(
        {
            "schema": "PORTAL_WORKER_RECEIPT_V1",
            "receipt_class": "SUCCEEDED_NO_EFFECT",
            "reason": "bad artifact",
            "artifacts": [
                {
                    "kind": "SOURCE_PROPOSAL",
                    "relative_path": "../escape.txt",
                    "sha256": "e" * 64,
                }
            ],
        }
    ),
    encoding="utf-8",
)
""",
    )

    with pytest.raises(ValueError, match="artifact path escapes worker workspace"):
        run_process_worker(
            packet=_packet(),
            spec=ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            ),
            workspace_root=tmp_path / "workspace",
        )


def test_process_worker_timeout_is_outcome_unknown_not_replayed(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "worker.py"
    _write_worker(
        worker,
        """
import argparse
import time

p = argparse.ArgumentParser()
p.add_argument("--portal-packet", required=True)
p.add_argument("--portal-receipt", required=True)
p.parse_args()
time.sleep(5)
""",
    )

    result = run_process_worker(
        packet=_packet(),
        spec=ProcessWorkerSpec(
            command=(sys.executable, str(worker)),
            timeout_seconds=0.05,
            pass_env=(),
        ),
        workspace_root=tmp_path / "workspace",
    )

    assert result.receipt_class == "OUTCOME_UNKNOWN"
    assert "timed out" in result.reason
    assert result.artifacts == ()


def test_process_worker_requires_absolute_executable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute executable"):
        ProcessWorkerSpec(
            command=("python", "worker.py"),
            timeout_seconds=1.0,
            pass_env=(),
        )



def test_process_worker_rejects_unpromoted_packet(tmp_path: Path) -> None:
    worker = tmp_path / "worker.py"
    _write_worker(worker, "raise SystemExit(0)\n")
    packet = _packet()
    packet["execution_authorized"] = False

    with pytest.raises(ValueError, match="requires promoted execution"):
        run_process_worker(
            packet=packet,
            spec=ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=1.0,
                pass_env=(),
            ),
            workspace_root=tmp_path / "workspace",
        )
