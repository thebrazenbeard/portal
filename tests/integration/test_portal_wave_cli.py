from __future__ import annotations

import json
from pathlib import Path

import portal.cli as portal_cli
from portal.wave_runtime import (
    PortalWaveDeliveryClaim,
    PortalWavePacket,
    PortalWavePreparationResult,
    PortalWaveReceipt,
    PortalWaveStore,
)


ROOT = Path(__file__).resolve().parents[2]


def _packet() -> PortalWavePacket:
    return PortalWavePacket(
        run_id="wave-cli",
        subject_id="project-runner",
        repository="thebrazenbeard/project-runner",
        ref="main",
        exact_head="a" * 40,
        node_id="lappy",
        lane_id="ONE",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id="lineage",
        work_fingerprint="c" * 64,
        action="EXECUTE_FRONTIER",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier="advance it",
        lead_identity="ONE",
        reviewer_identities=("REZON",),
    )


def test_portal_wave_prepare_cli_binds_budget_and_nodes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    captured = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="b" * 64,
            plan_path=tmp_path / "plan.json",
            assigned=1,
            claimed=1,
            held=0,
            packets=(_packet(),),
        )

    monkeypatch.setattr(portal_cli, "prepare_portal_wave", fake_prepare)

    code = portal_cli.entrypoint(
        [
            "wave",
            "prepare",
            "--nodes",
            str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
            "--state-db",
            str(tmp_path / "portal.sqlite3"),
            "--run-id",
            "wave-cli",
            "--max-parallel",
            "2",
            "--max-per-identity",
            "2",
            "--max-per-family",
            "2",
            "--max-per-lane",
            "2",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_WAVE_PREPARE_V1"
    assert payload["run_id"] == "wave-cli"
    assert payload["assigned"] == 1
    assert payload["claimed"] == 1
    assert payload["protected_effects_authorized"] is False
    assert [node.node_id for node in captured["nodes"]] == [
        "lappy",
        "worklaptop",
    ]
    assert captured["budget"].max_parallel == 2


def test_portal_wave_status_cli_reads_durable_outbox(
    tmp_path: Path,
    capsys,
) -> None:
    db = tmp_path / "portal.sqlite3"
    store = PortalWaveStore(db)
    try:
        store.ensure_run(
            run_id="wave-status",
            config_digest="a" * 64,
            wave_sha256="b" * 64,
            plan_sha256="c" * 64,
            holder="vera",
            now=1.0,
        )
    finally:
        store.close()

    code = portal_cli.entrypoint(
        [
            "wave",
            "status",
            "--state-db",
            str(db),
            "--run-id",
            "wave-status",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_WAVE_STATUS_V1"
    assert payload["wave"]["run_id"] == "wave-status"
    assert payload["wave"]["packets"] == 0



def test_portal_wave_claim_writes_worker_payload_and_hides_repo_from_stdout(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = tmp_path / "portal.sqlite3"
    store = PortalWaveStore(db)
    try:
        store.ensure_run(
            run_id="wave-cli",
            config_digest="a" * 64,
            wave_sha256="b" * 64,
            plan_sha256="c" * 64,
            holder="vera",
            now=1.0,
        )
        store.record_packet(
            _packet(),
            reason="test packet",
            now=1.0,
        )
    finally:
        store.close()

    worker_payload = {
        "schema": "PORTAL_WAVE_WORK_PACKET_V1",
        "repository": "thebrazenbeard/project-runner",
        "exact_head": "a" * 40,
        "execution_authorized": True,
        "execution_effect_class": "NO_PROTECTED_EFFECT",
        "protected_effects_authorized": False,
        "source_mutation_authorized": False,
        "target_ref_mutation_authorized": False,
    }

    def fake_claim_delivery(self, **kwargs):
        return PortalWaveDeliveryClaim(
            run_id=kwargs["run_id"],
            subject_id="project-runner",
            node_id=kwargs["node_id"],
            holder=kwargs["holder"],
            fencing_token=1,
            lease_expires_at=9999999999.0,
            payload=worker_payload,
        )

    monkeypatch.setattr(
        PortalWaveStore,
        "claim_delivery",
        fake_claim_delivery,
    )

    payload_out = tmp_path / "packet.json"
    code = portal_cli.entrypoint(
        [
            "wave",
            "claim",
            "--state-db",
            str(db),
            "--run-id",
            "wave-cli",
            "--node",
            "lappy",
            "--holder",
            "worker-lappy",
            "--lease-ttl",
            "30",
            "--payload-out",
            str(payload_out),
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["mode"] == "PORTAL_WAVE_CLAIM_V1"
    assert payload["claimed"] is True
    assert payload["payload_written"] is True
    assert "thebrazenbeard/project-runner" not in output

    worker_payload = json.loads(payload_out.read_text(encoding="utf-8"))
    assert worker_payload["repository"] == "thebrazenbeard/project-runner"
    assert worker_payload["exact_head"] == "a" * 40
    assert worker_payload["protected_effects_authorized"] is False


def test_portal_wave_receipt_records_exact_delivery_fence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    def fake_receipt(self, **kwargs):
        assert kwargs["expected_fencing_token"] == 7
        assert kwargs["receipt_class"] == "SUCCEEDED_NO_EFFECT"
        return PortalWaveReceipt(
            run_id=kwargs["run_id"],
            subject_id=kwargs["subject_id"],
            node_id=kwargs["node_id"],
            state="RECEIPT_RECORDED",
            receipt_class=kwargs["receipt_class"],
            receipt_sha256="f" * 64,
            result_repository=None,
            result_ref=None,
            result_head=None,
        )

    monkeypatch.setattr(
        PortalWaveStore,
        "record_delivery_receipt",
        fake_receipt,
    )
    monkeypatch.setattr(portal_cli.time, "time", lambda: 3.0)

    code = portal_cli.entrypoint(
        [
            "wave",
            "receipt",
            "--state-db",
            str(tmp_path / "portal.sqlite3"),
            "--run-id",
            "wave-cli",
            "--subject-id",
            "project-runner",
            "--node",
            "lappy",
            "--holder",
            "worker-lappy",
            "--fencing-token",
            "7",
            "--receipt-class",
            "SUCCEEDED_NO_EFFECT",
            "--evidence-sha256",
            "e" * 64,
            "--reason",
            "analysis complete",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_WAVE_RECEIPT_V1"
    assert payload["state"] == "RECEIPT_RECORDED"
    assert payload["receipt_class"] == "SUCCEEDED_NO_EFFECT"

def test_portal_wave_verify_cli_uses_independent_verifier(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from portal.wave_runtime import PortalWaveVerificationResult

    captured = {}

    def fake_verify(**kwargs):
        captured.update(kwargs)
        return PortalWaveVerificationResult(
            run_id=kwargs["run_id"],
            subject_id=kwargs["subject_id"],
            state="VERIFIED_COMPLETE",
            result_repository="thebrazenbeard/project-runner",
            result_ref="work/portal/project-runner",
            result_head="d" * 40,
            reason="verified",
        )

    monkeypatch.setattr(
        portal_cli,
        "verify_portal_wave_delivery",
        fake_verify,
    )

    code = portal_cli.entrypoint(
        [
            "wave",
            "verify",
            "--state-db",
            str(tmp_path / "portal.sqlite3"),
            "--run-id",
            "wave-cli",
            "--subject-id",
            "project-runner",
            "--verifier",
            "vera-review",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_WAVE_VERIFY_V1"
    assert payload["state"] == "VERIFIED_COMPLETE"
    assert captured["verifier"] == "vera-review"



def test_portal_wave_promote_resolves_durable_packet_without_manual_lineage(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from runner.execution_promotion import ExecutionPromotionReceipt

    review = tmp_path / "review.json"
    execution = tmp_path / "execution.json"
    review.write_text("{}", encoding="utf-8")
    execution.write_text("{}", encoding="utf-8")

    captured = {}

    def fake_promote(**kwargs):
        captured.update(kwargs)
        return ExecutionPromotionReceipt(
            lineage_id="lineage",
            work_fingerprint="c" * 64,
            fencing_token=1,
            holder="vera",
            repository="thebrazenbeard/project-runner",
            ref="main",
            exact_head="a" * 40,
            operation="EXECUTE_FRONTIER",
            effect_class="NO_PROTECTED_EFFECT",
            review_sha256="d" * 64,
            review_valid_until=999.0,
            execution_grant_sha256="e" * 64,
            execution_valid_until=999.0,
            execution_request_sha256=None,
            effect_grant_sha256=None,
            effect_valid_until=None,
            promoted_at=2.0,
            attempt_work_generation=2,
            promoted_work_generation=3,
            promotion_sha256="f" * 64,
        )

    monkeypatch.setattr(
        portal_cli,
        "promote_portal_wave_packet",
        fake_promote,
    )
    monkeypatch.setattr(
        portal_cli,
        "load_json_document",
        lambda path: {"path": str(path)},
    )
    monkeypatch.setattr(
        portal_cli,
        "review_key_from_environment",
        lambda: b"review",
    )
    monkeypatch.setattr(
        portal_cli,
        "execution_authority_key_from_environment",
        lambda: b"execution",
    )
    monkeypatch.setattr(
        portal_cli,
        "effect_authority_key_from_environment",
        lambda: None,
    )

    code = portal_cli.entrypoint(
        [
            "wave",
            "promote",
            "--state-db",
            str(tmp_path / "portal.sqlite3"),
            "--run-id",
            "wave-cli",
            "--subject-id",
            "project-runner",
            "--review",
            str(review),
            "--execution-grant",
            str(execution),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_WAVE_PROMOTE_V1"
    assert payload["promoted"] is True
    assert payload["effect_class"] == "NO_PROTECTED_EFFECT"
    assert payload["protected_effects_authorized"] is False
    assert captured["run_id"] == "wave-cli"
    assert captured["subject_id"] == "project-runner"
