from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3

import pytest

from runner.cli import main
from runner.durable_dispatch import SqliteDispatchAdmissionStore
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_operator_bridge import (
    claim_bound_plan_subject,
    verify_bound_plan_subject,
)


ROOT = Path(__file__).resolve().parents[2]
WAVE = ROOT / "portfolio" / "advancement_wave.public.json"
CORPUS = ROOT / "portfolio" / "corpus.public.json"
PROJECTS = ROOT / "registry" / "projects.yaml"


class FakeReadOnlyTransport:
    def __init__(self, heads):
        self.heads = dict(heads)
        self.reads = []
        self.mutations = []

    def read_ref(self, repository, ref):
        self.reads.append((repository, ref))
        try:
            return self.heads[(repository, ref)]
        except KeyError as exc:
            raise KeyError((repository, ref)) from exc

    def read_file(self, repository, path, ref):
        return None

    def create_branch(self, repository, branch, sha):
        self.mutations.append(("create_branch", repository, branch, sha))
        raise AssertionError("claim bridge must not create branches")

    def put_file(
        self,
        repository,
        path,
        branch,
        content,
        message,
        expected_blob_sha=None,
    ):
        self.mutations.append(("put_file", repository, path, branch))
        raise AssertionError("claim bridge must not put files")


def _write_plan(tmp_path):
    stdout = StringIO()
    with redirect_stdout(stdout):
        code = main(
            [
                "portfolio-wave-plan",
                "--max-parallel",
                "50",
                "--max-per-identity",
                "50",
                "--max-per-family",
                "50",
            ]
        )
    assert code == 0
    payload = json.loads(stdout.getvalue())
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path, payload


def _selected_record(payload):
    subject_id = "project-runner"
    assert any(
        item["subject_id"] == subject_id
        for item in payload["selected"]
    )
    corpus = load_portfolio_corpus(CORPUS, public_safe=True)
    record = next(item for item in corpus.records if item.id == subject_id)
    return subject_id, record


def test_bound_plan_claim_rechecks_live_head_and_acquires_fresh_fence(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    live_head = "a" * 40
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): live_head}
    )

    claim = claim_bound_plan_subject(
        plan_path=plan_path,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id=subject_id,
        state_db=tmp_path / "operator.sqlite3",
        holder="operator-test",
        lease_ttl=60.0,
        allowed_repositories=(record.repository,),
        transport=transport,
        clock=lambda: 1000.0,
    )

    assert claim.subject_id == subject_id
    assert claim.repository == record.repository
    assert claim.ref == record.default_branch
    assert claim.exact_head == live_head
    assert claim.fencing_token == 1
    assert claim.lease_expires_at == 1060.0
    assert claim.budget_generation == 2
    assert claim.work_generation == 2
    assert transport.reads == [(record.repository, record.default_branch)]
    assert transport.mutations == []

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        state = db.execute(
            """
            SELECT work_json, status, generation
            FROM recursive_work_state
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (claim.lineage_id, claim.work_fingerprint),
        ).fetchone()
        lease = db.execute(
            """
            SELECT holder, fencing_token, expires_at, completed
            FROM leases WHERE work_fingerprint = ?
            """,
            (claim.work_fingerprint,),
        ).fetchone()
        attempts = db.execute(
            """
            SELECT result_json, result_sha256
            FROM execution_attempts
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (claim.lineage_id, claim.work_fingerprint),
        ).fetchall()

    assert state is not None
    work_payload = json.loads(state[0])
    assert state[1] == "CLAIMED"
    assert state[2] == 2
    assert work_payload["inputs"][0]["commit"] == live_head
    assert work_payload["payload"]["execution_authority"] is False
    assert work_payload["payload"]["protected_effects_authorized"] is False
    assert work_payload["payload"]["plan_sha256"] == claim.plan_sha256
    assert work_payload["payload"]["wave_sha256"] == claim.wave_sha256
    assert lease == ("operator-test", 1, 1060.0, 0)
    assert attempts == [(None, None)]


def test_tampered_plan_fails_before_currentness_or_claim(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    payload["selected"][0]["priority"] = "P4"
    plan_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): "b" * 40}
    )

    with pytest.raises(ValueError, match="plan digest mismatch"):
        claim_bound_plan_subject(
            plan_path=plan_path,
            wave_path=WAVE,
            corpus_path=CORPUS,
            projects_path=PROJECTS,
            subject_id=subject_id,
            state_db=tmp_path / "operator.sqlite3",
            holder="operator-test",
            lease_ttl=60.0,
            allowed_repositories=(record.repository,),
            transport=transport,
        )

    assert transport.reads == []
    assert not (tmp_path / "operator.sqlite3").exists()


def test_wrong_wave_fails_closed(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, _record = _selected_record(payload)
    altered = tmp_path / "wave.json"
    altered.write_bytes(WAVE.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="wave digest mismatch"):
        verify_bound_plan_subject(
            plan_path=plan_path,
            wave_path=altered,
            corpus_path=CORPUS,
            projects_path=PROJECTS,
            subject_id=subject_id,
        )


def test_wrong_corpus_blob_fails_closed(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, _record = _selected_record(payload)
    altered = tmp_path / "corpus.json"
    altered.write_bytes(CORPUS.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="corpus does not match"):
        verify_bound_plan_subject(
            plan_path=plan_path,
            wave_path=WAVE,
            corpus_path=altered,
            projects_path=PROJECTS,
            subject_id=subject_id,
        )



def test_operator_registry_visibility_divergence_blocks_claim(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id = "vera"
    assert any(item["subject_id"] == subject_id for item in payload["selected"])

    with pytest.raises(ValueError, match="not operator-bound"):
        verify_bound_plan_subject(
            plan_path=plan_path,
            wave_path=WAVE,
            corpus_path=CORPUS,
            projects_path=PROJECTS,
            subject_id=subject_id,
        )


def test_unauthorized_repository_fails_before_live_read(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): "c" * 40}
    )

    with pytest.raises(ValueError, match="not authorized"):
        claim_bound_plan_subject(
            plan_path=plan_path,
            wave_path=WAVE,
            corpus_path=CORPUS,
            projects_path=PROJECTS,
            subject_id=subject_id,
            state_db=tmp_path / "operator.sqlite3",
            holder="operator-test",
            lease_ttl=60.0,
            allowed_repositories=(),
            transport=transport,
        )

    assert transport.reads == []


def test_active_claim_replay_is_idempotent_for_same_holder(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): "d" * 40}
    )
    kwargs = dict(
        plan_path=plan_path,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id=subject_id,
        state_db=tmp_path / "operator.sqlite3",
        holder="operator-test",
        lease_ttl=60.0,
        allowed_repositories=(record.repository,),
        transport=transport,
        clock=lambda: 1000.0,
    )

    first = claim_bound_plan_subject(**kwargs)
    second = claim_bound_plan_subject(**kwargs)
    assert second == first

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        attempts = db.execute(
            "SELECT fencing_token FROM execution_attempts ORDER BY fencing_token"
        ).fetchall()
    assert attempts == [(1,)]


def test_active_claim_replay_rejects_different_holder(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): "e" * 40}
    )
    common = dict(
        plan_path=plan_path,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id=subject_id,
        state_db=tmp_path / "operator.sqlite3",
        lease_ttl=60.0,
        allowed_repositories=(record.repository,),
        transport=transport,
    )
    claim_bound_plan_subject(
        **common,
        holder="operator-a",
        clock=lambda: 1000.0,
    )
    with pytest.raises(ValueError, match="active lease"):
        claim_bound_plan_subject(
            **common,
            holder="operator-b",
            clock=lambda: 1001.0,
        )


def test_expired_claim_only_lease_reclaims_with_new_fence(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): "f" * 40}
    )
    common = dict(
        plan_path=plan_path,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id=subject_id,
        state_db=tmp_path / "operator.sqlite3",
        lease_ttl=10.0,
        allowed_repositories=(record.repository,),
        transport=transport,
    )
    first = claim_bound_plan_subject(
        **common,
        holder="operator-a",
        clock=lambda: 1000.0,
    )
    second = claim_bound_plan_subject(
        **common,
        holder="operator-b",
        clock=lambda: 1011.0,
    )

    assert first.fencing_token == 1
    assert second.fencing_token == 2
    assert second.holder == "operator-b"
    assert second.budget_generation == 2
    assert second.work_generation == 3
    assert second.lease_expires_at == 1021.0

    store = SqliteDispatchAdmissionStore(tmp_path / "operator.sqlite3")
    unresolved = store.unresolved_attempts()
    store.close()
    assert len(unresolved) == 1
    assert unresolved[0].fencing_token == 2
    assert unresolved[0].phase == "ADMITTED"

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        reconciled = db.execute(
            """
            SELECT outcome, reconciler
            FROM execution_reconciliations
            WHERE fencing_token = 1
            """
        ).fetchone()
    assert reconciled == ("NO_EFFECT_CONFIRMED", "portfolio-claim-recovery")


def test_crash_after_root_initialization_recovers_on_replay(tmp_path, monkeypatch):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): "1" * 40}
    )
    kwargs = dict(
        plan_path=plan_path,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id=subject_id,
        state_db=tmp_path / "operator.sqlite3",
        holder="operator-test",
        lease_ttl=60.0,
        allowed_repositories=(record.repository,),
        transport=transport,
        clock=lambda: 1000.0,
    )

    original_admit = SqliteDispatchAdmissionStore.admit

    def interrupted_admit(self, **_kwargs):
        raise RuntimeError("simulated interruption after root initialization")

    monkeypatch.setattr(SqliteDispatchAdmissionStore, "admit", interrupted_admit)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        claim_bound_plan_subject(**kwargs)

    monkeypatch.setattr(SqliteDispatchAdmissionStore, "admit", original_admit)
    recovered = claim_bound_plan_subject(**kwargs)
    assert recovered.fencing_token == 1
    assert recovered.budget_generation == 2
    assert recovered.work_generation == 2



def test_claim_recovery_rejects_lease_attempt_divergence(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): "4" * 40}
    )
    kwargs = dict(
        plan_path=plan_path,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id=subject_id,
        state_db=tmp_path / "operator.sqlite3",
        holder="operator-test",
        lease_ttl=60.0,
        allowed_repositories=(record.repository,),
        transport=transport,
        clock=lambda: 1000.0,
    )
    claim = claim_bound_plan_subject(**kwargs)

    with sqlite3.connect(tmp_path / "operator.sqlite3") as db:
        db.execute(
            """
            UPDATE leases
            SET holder = 'tampered-holder'
            WHERE work_fingerprint = ?
            """,
            (claim.work_fingerprint,),
        )

    with pytest.raises(ValueError, match="lease/attempt holder mismatch"):
        claim_bound_plan_subject(**kwargs)


def test_replay_after_live_head_moves_fails_closed(tmp_path):
    plan_path, payload = _write_plan(tmp_path)
    subject_id, record = _selected_record(payload)
    transport = FakeReadOnlyTransport(
        {(record.repository, record.default_branch): "2" * 40}
    )
    common = dict(
        plan_path=plan_path,
        wave_path=WAVE,
        corpus_path=CORPUS,
        projects_path=PROJECTS,
        subject_id=subject_id,
        state_db=tmp_path / "operator.sqlite3",
        holder="operator-test",
        lease_ttl=60.0,
        allowed_repositories=(record.repository,),
        transport=transport,
        clock=lambda: 1000.0,
    )
    claim_bound_plan_subject(**common)
    transport.heads[(record.repository, record.default_branch)] = "3" * 40

    with pytest.raises(ValueError, match="exact work state is not durable"):
        claim_bound_plan_subject(**common)
