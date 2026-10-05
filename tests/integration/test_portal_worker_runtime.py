from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from portal.models import ExecutionNode
from portal.wave_runtime import PortalWavePacket, PortalWaveStore
from portal.worker_backend import ProcessWorkerSpec
from portal.worker_runtime import run_wave_workers_once


class FakeReadOnlyTransport:
    def __init__(self, heads):
        self.heads = dict(heads)
        self.reads = []

    def read_ref(self, repository: str, ref: str) -> str:
        self.reads.append((repository, ref))
        return self.heads[(repository, ref)]

    def read_file(self, repository: str, path: str, ref: str):
        return None

    def create_branch(self, repository: str, branch: str, sha: str) -> None:
        raise AssertionError("automatic worker runtime must not mutate source")

    def put_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        expected_blob_sha: str | None = None,
    ):
        raise AssertionError("automatic worker runtime must not mutate source")


def _packet(subject: str, node: str) -> PortalWavePacket:
    return PortalWavePacket(
        run_id="auto-run",
        subject_id=subject,
        repository=f"example/{subject}",
        ref="main",
        exact_head="a" * 40,
        node_id=node,
        lane_id="LANE_A",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=f"lineage-{subject}",
        work_fingerprint="c" * 64,
        action="EXECUTE_FRONTIER",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=f"advance {subject}",
        lead_identity="vera",
        reviewer_identities=("reviewer",),
    )


def _seed(db: Path) -> None:
    store = PortalWaveStore(db)
    try:
        store.ensure_run(
            run_id="auto-run",
            config_digest="a" * 64,
            wave_sha256="b" * 64,
            plan_sha256="c" * 64,
            holder="vera",
            now=1.0,
        )
        store.record_packet(_packet("repo-a", "alpha"), reason="ready", now=1.0)
        store.record_packet(_packet("repo-b", "alpha"), reason="ready", now=1.0)
    finally:
        store.close()


def _worker(path: Path) -> None:
    path.write_text(
        """
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--portal-packet", required=True)
p.add_argument("--portal-receipt", required=True)
args = p.parse_args()
packet = json.loads(Path(args.portal_packet).read_text(encoding="utf-8"))
Path(args.portal_receipt).write_text(
    json.dumps(
        {
            "schema": "PORTAL_WORKER_RECEIPT_V1",
            "receipt_class": "SUCCEEDED_NO_EFFECT",
            "reason": "analysis complete for " + packet["subject_id"],
            "artifacts": [],
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )


def test_automatic_worker_pass_fills_node_capacity_and_verifies(
    tmp_path: Path,
) -> None:
    db = tmp_path / "portal.sqlite3"
    _seed(db)
    worker = tmp_path / "worker.py"
    _worker(worker)

    result = run_wave_workers_once(
        state_db=db,
        run_id="auto-run",
        nodes=(ExecutionNode(node_id="alpha", max_parallel=2),),
        backends={
            "alpha": ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            )
        },
        workspace_root=tmp_path / "workers",
        holder_prefix="portal",
        delivery_lease_ttl=30.0,
        verifier="vera-review",
        token=None,
        transport=FakeReadOnlyTransport(
            {
                ("example/repo-a", "main"): "a" * 40,
                ("example/repo-b", "main"): "a" * 40,
            }
        ),
    )

    assert result.claimed == 2
    assert result.verified_complete == 2
    assert result.no_work == 0
    assert {slot.subject_id for slot in result.slots} == {"repo-a", "repo-b"}
    assert {slot.state for slot in result.slots} == {"VERIFIED_COMPLETE"}

    store = PortalWaveStore(db)
    try:
        summary = store.summary("auto-run")
    finally:
        store.close()
    assert summary["delivery_states"] == {"VERIFIED_COMPLETE": 2}


def test_automatic_worker_pass_is_idle_after_packets_are_terminal(
    tmp_path: Path,
) -> None:
    db = tmp_path / "portal.sqlite3"
    _seed(db)
    worker = tmp_path / "worker.py"
    _worker(worker)
    kwargs = dict(
        state_db=db,
        run_id="auto-run",
        nodes=(ExecutionNode(node_id="alpha", max_parallel=2),),
        backends={
            "alpha": ProcessWorkerSpec(
                command=(sys.executable, str(worker)),
                timeout_seconds=10.0,
                pass_env=(),
            )
        },
        workspace_root=tmp_path / "workers",
        holder_prefix="portal",
        delivery_lease_ttl=30.0,
        verifier="vera-review",
        token=None,
        transport=FakeReadOnlyTransport(
            {
                ("example/repo-a", "main"): "a" * 40,
                ("example/repo-b", "main"): "a" * 40,
            }
        ),
    )

    first = run_wave_workers_once(**kwargs)
    second = run_wave_workers_once(**kwargs)

    assert first.verified_complete == 2
    assert second.claimed == 0
    assert second.no_work == 2


def test_automatic_worker_pass_requires_backend_for_every_enabled_node(
    tmp_path: Path,
) -> None:
    db = tmp_path / "portal.sqlite3"
    _seed(db)

    with pytest.raises(ValueError, match="missing worker backend for enabled node"):
        run_wave_workers_once(
            state_db=db,
            run_id="auto-run",
            nodes=(ExecutionNode(node_id="alpha", max_parallel=1),),
            backends={},
            workspace_root=tmp_path / "workers",
            holder_prefix="portal",
            delivery_lease_ttl=30.0,
            verifier="vera-review",
            token=None,
            transport=FakeReadOnlyTransport(
                {("example/repo-a", "main"): "a" * 40}
            ),
        )
