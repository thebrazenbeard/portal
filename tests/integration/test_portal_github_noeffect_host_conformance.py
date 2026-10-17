"""Exact GitHub source observation must not become effect authority."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from types import SimpleNamespace
import pytest
from portal import github_head_probe
from portal.host_authority import apply_host_authority
from portal.host_bridge import PortalHostBridgeStore, PortalHostExecutionAdapter
from portal.host_probe import PortalCommandHostProbe
from portal.route_resolver import PortalRouteRequest
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket
HEAD = "a" * 40
REPO = "thebrazenbeard/portal"
def _packet():
    return PortalWavePacket(
        run_id="noeffect::1",subject_id="portal",repository=REPO,
        ref="main",exact_head=HEAD,node_id="github-api",lane_id="vera",
        state="CLAIMED",plan_sha256="b"*64,fencing_token=7,
        lineage_id="fixture-lineage",work_fingerprint="c"*64,
        action="OBSERVE_SOURCE",effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",frontier="no dispatch without authority",
        lead_identity="vera",reviewer_identities=())

def _session():
    return PortalSessionResult(
        session_id="source-fixture",control_state="RUNNING",generation=1,
        wave_run_id="noeffect::1",packets=(_packet(),),
        summary={"active":1,"held":0,"terminal":0})

def _request(packet):
    return PortalRouteRequest(
        subject_kind="repository",subject_id=packet.subject_id,
        node_id=packet.node_id,target_kind="repository",
        target_id=packet.repository,required_capabilities=("source_read",),
        required_effect=packet.effect_ceiling)
def _verify_hold(tmp_path, observed):
    assert len(observed.routes)==1
    route=observed.routes[0]
    assert route.available and route.attached and route.current
    assert route.effect_capabilities==()
    assert route.authorized_effects==()
    key=(route.adapter_id,route.route_id,route.node_id,
         route.target_kind,route.target_id)
    with pytest.raises(ValueError,match="exceeds observed effect"):
        apply_host_authority((route,),{key:("SOURCE_ONLY",)})
    route=apply_host_authority((route,),{})[0]
    db=tmp_path/"host"/"bridge.sqlite3"
    store=PortalHostBridgeStore(db)
    try:
        store.advertise_route(route,ttl_seconds=60.,observed_at=100.)
        adapter=PortalHostExecutionAdapter(
            store=store,clock=lambda:120.,request_builder=_request)
        with pytest.raises(ValueError,match="no qualified execution route"):
            adapter.select_routes(_session())
        assert store.pending_dispatches(session_id="source-fixture")==()
    finally:
        store.close()
    reopened=PortalHostBridgeStore(db)
    try:
        assert reopened.pending_dispatches(session_id="source-fixture")==()
    finally:
        reopened.close()
def test_exact_current_head_still_holds_on_no_effect(tmp_path,monkeypatch):
    monkeypatch.setattr(github_head_probe.subprocess,"run",
        lambda *args,**kw: SimpleNamespace(
            returncode=0,stdout=json.dumps({"sha":HEAD}),stderr=""))
    result=github_head_probe.observe_head(REPO,ref="main",expected_head=HEAD)
    import portal.host_probe as probe_module
    monkeypatch.setattr(probe_module.subprocess,"run",
        lambda *args,**kw: SimpleNamespace(
            returncode=0,stdout=json.dumps(result),stderr=""))
    observed=PortalCommandHostProbe(
        adapter_id="github-head-observer",
        command=("python","-m","portal.github_head_probe")).observe()
    _verify_hold(tmp_path,observed)


@pytest.mark.skipif(
    os.environ.get("PORTAL_TEST_LIVE_GITHUB_NOEFFECT")!="1",
    reason="opt in to authenticated read-only GitHub CLI")
def test_live_exact_head_holds_without_host_dispatch(tmp_path):
    response=subprocess.run(
        ["gh","api","--method","GET",f"repos/{REPO}/commits/main",
         "--jq",".sha"],
        text=True,capture_output=True,timeout=20,check=True,shell=False)
    head=response.stdout.strip()
    assert len(head)==40
    observed=PortalCommandHostProbe(
        adapter_id="github-head-observer",
        command=(sys.executable,"-m","portal.github_head_probe",
            "--repository",REPO,"--ref","main","--expected-head",head),
    ).observe()
    _verify_hold(tmp_path,observed)
