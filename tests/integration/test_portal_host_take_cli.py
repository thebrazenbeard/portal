from __future__ import annotations

import json
from pathlib import Path

import portal.cli as portal_cli
from portal.adapters import PortalRouteBinding
from portal.host_bridge import PortalHostBridgeStore
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket


def _queue(
    store: PortalHostBridgeStore,
    *,
    run_id: str,
    subject_id: str,
    adapter_id: str,
    created_at: float,
) -> str:
    packet = PortalWavePacket(
        run_id=run_id,
        subject_id=subject_id,
        repository=f"thebrazenbeard/{subject_id}",
        ref="main",
        exact_head="a" * 40,
        node_id="repo-native",
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=f"lineage:{subject_id}",
        work_fingerprint="c" * 64,
        action="ADVANCE",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=f"Advance {subject_id}.",
        lead_identity="vera",
        reviewer_identities=(),
    )
    result = PortalSessionResult(
        session_id="portfolio",
        control_state="RUNNING",
        generation=1,
        wave_run_id=run_id,
        packets=(packet,),
        summary={"active": 1, "held": 0, "terminal": 0},
    )
    binding = PortalRouteBinding(
        subject_kind="repository",
        subject_id=subject_id,
        adapter_id=adapter_id,
        route_id=f"{adapter_id}:{subject_id}",
    )
    store.advertise_route(
        PortalRouteAdvertisement(
            adapter_id=adapter_id,
            route_id=binding.route_id,
            node_id="repo-native",
            target_kind="repository",
            target_id=f"thebrazenbeard/{subject_id}",
            capabilities=("semantic_work",),
            effect_capabilities=("SOURCE_ONLY",),
            authorized_effects=("SOURCE_ONLY",),
            available=True,
            attached=True,
            current=True,
            preference=50,
        ),
        observed_at=0.0,
        ttl_seconds=1_000_000_000_000.0,
    )
    queued = store.queue_dispatch(
        result=result,
        packet=packet,
        binding=binding,
        queued_at=created_at,
    )
    return str(queued["dispatch_id"])


def test_take_pending_dispatch_filters_adapter_and_marks_attempt_atomically(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    workbridge = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="alpha",
        adapter_id="workbridge",
        created_at=100.0,
    )
    github = _queue(
        store,
        run_id="portfolio::g2",
        subject_id="beta",
        adapter_id="github",
        created_at=101.0,
    )

    taken = store.take_pending_dispatch(
        session_id="portfolio",
        adapter_ids=("github",),
        attempt_id="chatgpt-attempt-1",
        evidence_id="chatgpt:request:1",
        attempted_at=110.0,
    )

    assert taken is not None
    assert taken["dispatch_id"] == github
    assert taken["state"] == "ATTEMPTED"
    assert taken["attempt_id"] == "chatgpt-attempt-1"
    assert taken["dispatch_evidence_id"] == "chatgpt:request:1"
    assert store.load_dispatch(workbridge)["state"] == "QUEUED"
    assert store.take_pending_dispatch(
        session_id="portfolio",
        adapter_ids=("github",),
        attempt_id="chatgpt-attempt-2",
        evidence_id="chatgpt:request:2",
        attempted_at=111.0,
    ) is None
    store.close()


def test_take_pending_dispatch_rejects_empty_adapter_filter(tmp_path: Path) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    _queue(
        store,
        run_id="portfolio::g1",
        subject_id="alpha",
        adapter_id="github",
        created_at=100.0,
    )

    try:
        store.take_pending_dispatch(
            session_id="portfolio",
            adapter_ids=(),
            attempt_id="attempt",
            evidence_id="evidence",
        )
    except ValueError as exc:
        assert "adapter_ids" in str(exc)
    else:
        raise AssertionError("empty adapter filter must fail closed")
    store.close()


def test_host_take_cli_returns_exact_attempted_dispatch(
    tmp_path: Path,
    capsys,
) -> None:
    state_db = tmp_path / "portal.sqlite3"
    store = PortalHostBridgeStore(state_db)
    dispatch_id = _queue(
        store,
        run_id="portfolio::g1",
        subject_id="portal",
        adapter_id="github",
        created_at=100.0,
    )
    store.close()

    code = portal_cli.entrypoint([
        "host", "take",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
        "--adapter-id", "github",
        "--attempt-id", "chatgpt-attempt-1",
        "--evidence-id", "chatgpt:request:1",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_HOST_TAKE_V1"
    assert payload["dispatch"]["dispatch_id"] == dispatch_id
    assert payload["dispatch"]["state"] == "ATTEMPTED"
    assert payload["dispatch"]["adapter_id"] == "github"
