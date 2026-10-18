"""Pre-mutation numeric-domain probes of actual disposable Runner writers."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("b_claim_helpers", HERE.parent / "diagnostics/runner_claim_concurrency_probe.py")
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
Store = helpers.SqliteDispatchAdmissionStore

INVALID = [
    ("now-bool", True, 10.0),
    ("now-nan", float("nan"), 10.0),
    ("now-inf", float("inf"), 10.0),
    ("now-overflow-int", 10 ** 400, 10.0),
    ("now-inexact-int", 2 ** 53 + 1, 10.0),
    ("ttl-bool", 111.0, True),
    ("ttl-nan", 111.0, float("nan")),
    ("ttl-inf", 111.0, float("inf")),
    ("ttl-overflow-int", 111.0, 10 ** 400),
    ("ttl-inexact-int", 111.0, 2 ** 53 + 1),
    ("ttl-zero", 111.0, 0),
    ("ttl-negative", 111.0, -1),
    ("expiry-overflow", 1e308, 1e308),
    ("expiry-no-progress", 1e20, 1),
]


def setup(db, mode):
    helpers.seed(db)
    if mode == "claimed-recover":
        store = Store(db)
        try:
            store.admit(lineage_id="lineage-a", work_fingerprint_value=helpers.work_unit_fingerprint(helpers.work()),
                        budget_scope_id="root", expected_budget_generation=1, expected_work_generation=1,
                        holder="fixture-original", now=100.0, ttl=10.0)
        finally:
            store.close()


def call(store, mode, now, ttl):
    if mode == "admit":
        return store.admit(lineage_id="lineage-a", work_fingerprint_value=helpers.work_unit_fingerprint(helpers.work()),
                           budget_scope_id="root", expected_budget_generation=1, expected_work_generation=1,
                           holder="fixture-original", now=now, ttl=ttl)
    return store.recover_claim_only_root(budget=helpers.budget(), work=helpers.work(), effective_capabilities=("read",),
                                        holder="fixture-original", now=now, ttl=ttl)


@pytest.mark.parametrize("mode", ["admit", "pending-recover", "claimed-recover"])
@pytest.mark.parametrize("label,now,ttl", INVALID, ids=[item[0] for item in INVALID])
def test_invalid_clock_domain_rejected_before_mutation(label, now, ttl, mode, tmp_path):
    db = tmp_path / "clock-domain.db"
    setup(db, mode)
    before = helpers.snapshot(db)
    store = Store(db)
    statements = []
    store.connection.set_trace_callback(statements.append)
    rejected = None
    returned = None
    try:
        try:
            result = call(store, mode, now, ttl)
            returned = {"fence": result.lease.fencing_token, "expires_at": repr(result.lease.expires_at)}
        except (ValueError, TypeError, OverflowError) as exc:
            rejected = {"type": type(exc).__name__, "reason": str(exc)}
    finally:
        store.close()
    after = helpers.snapshot(db)
    mutations = [statement.strip() for statement in statements
                 if statement.lstrip().upper().startswith(("BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE"))]
    evidence = {"label": label, "mode": mode, "now_type": type(now).__name__, "now": repr(now),
                "ttl_type": type(ttl).__name__, "ttl": repr(ttl), "rejected": rejected, "returned": returned,
                "mutation_statements": mutations, "rows_unchanged": after == before,
                "before": before, "after": after, "backend_dispatches": 0}
    (tmp_path / "clock-domain-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    assert rejected is not None, "invalid clock domain was admitted"
    assert not mutations, "clock rejection occurred only after transaction/mutation began"
    assert before == after, "invalid clock domain changed durable state"


@pytest.mark.parametrize("mode", ["admit", "pending-recover", "claimed-recover"])
@pytest.mark.parametrize("numeric_kind", ["integer", "float", "exact-large-integer"])
def test_valid_exact_numeric_clock_cold_replay(mode, numeric_kind, tmp_path):
    db = tmp_path / "clock-valid.db"
    setup(db, mode)
    if numeric_kind == "exact-large-integer":
        now, ttl = 2 ** 53, 4
    else:
        now = 111 if mode == "claimed-recover" else 100
        if numeric_kind == "float":
            now = float(now)
        ttl = 10
    store = Store(db)
    try:
        first = call(store, mode, now, ttl)
    finally:
        store.close()
    before = helpers.snapshot(db)
    reopened = Store(db)
    try:
        replay = reopened.recover_claim_only_root(budget=helpers.budget(), work=helpers.work(), effective_capabilities=("read",),
            holder="fixture-original", now=float(now) + 2, ttl=ttl)
    finally:
        reopened.close()
    assert helpers.snapshot(db) == before
    assert first.lease == replay.lease
    (tmp_path / "clock-domain-valid-evidence.json").write_text(json.dumps({"mode": mode, "kind": numeric_kind,
        "now": repr(now), "first": helpers.asdict(first.lease), "replay": helpers.asdict(replay.lease),
        "durable_rows_unchanged": True, "backend_dispatches": 0}, indent=2, sort_keys=True), encoding="utf-8")
