"""Bounded GitHub source-head observation; observation is never execution authority."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from portal import github_head_probe
from portal.host_probe import PortalCommandHostProbe
from portal.host_authority import apply_host_authority
from portal.route_resolver import PortalRouteRequest, resolve_portal_route


SHA = "a" * 40
OTHER = "b" * 40


def _completed(payload: object, *, status: int = 0):
    return SimpleNamespace(returncode=status, stdout=json.dumps(payload), stderr="")


def test_live_exact_head_is_bounded_no_effect_observation(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return _completed({"sha": SHA})

    monkeypatch.setattr(github_head_probe.subprocess, "run", fake_run)
    result = github_head_probe.observe_head(
        "thebrazenbeard/portal", ref="main", expected_head=SHA,
    )
    assert result["schema"] == "PORTAL_HOST_PROBE_RESULT_V1"
    assert result["routes"][0]["current"] is True
    assert result["routes"][0]["effect_capabilities"] == []
    assert "authorized_effects" not in result["routes"][0]
    assert result["routes"][0]["target_id"] == "thebrazenbeard/portal"
    assert result["evidence_id"].endswith(SHA)
    assert seen[0][0] == [
        "gh", "api", "--method", "GET",
        "repos/thebrazenbeard/portal/commits/main",
    ]
    assert seen[0][1]["shell"] is False


def test_mismatch_reports_noncurrent_and_never_admits_execution(monkeypatch):
    monkeypatch.setattr(
        github_head_probe.subprocess, "run",
        lambda *a, **kw: _completed({"sha": OTHER}),
    )
    result = github_head_probe.observe_head(
        "thebrazenbeard/portal", ref="main", expected_head=SHA,
    )
    row = result["routes"][0]
    assert row["available"] is True
    assert row["attached"] is True
    assert row["current"] is False
    assert row["effect_capabilities"] == []
    from portal.route_resolver import PortalRouteAdvertisement
    record = PortalRouteAdvertisement(
        adapter_id=result["adapter_id"],
        authorized_effects=(), **row,
    )
    assert apply_host_authority((record,), {}) == (record,)
    request = PortalRouteRequest(
        subject_kind="repository", subject_id="portal", node_id=record.node_id,
        target_kind="repository", target_id=record.target_id,
        required_capabilities=("source_read",), required_effect="SOURCE_ONLY",
    )
    with pytest.raises(ValueError, match="no qualified"):
        resolve_portal_route(request, (record,))


@pytest.mark.parametrize("bad", [
    {"sha": "bad"},
    {"sha": 123},
    [],
    "wrong",
])
def test_unusable_gh_reply_fails_closed(monkeypatch, bad):
    monkeypatch.setattr(
        github_head_probe.subprocess, "run",
        lambda *a, **kw: _completed(bad),
    )
    with pytest.raises(ValueError):
        github_head_probe.observe_head(
            "thebrazenbeard/portal", ref="main", expected_head=SHA,
        )


def test_gh_failure_fails_without_a_route(monkeypatch):
    monkeypatch.setattr(
        github_head_probe.subprocess, "run",
        lambda *a, **kw: _completed({"sha": SHA}, status=1),
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        github_head_probe.observe_head(
            "thebrazenbeard/portal", ref="main", expected_head=SHA,
        )


@pytest.mark.parametrize("repository,ref", [
    ("x", "main"), ("thebrazenbeard/../../other", "main"),
    ("thebrazenbeard/portal", "../main"),
    ("thebrazenbeard/portal", "--request"),
    ("thebrazenbeard/portal", ""),
])
def test_invalid_locator_prevents_shell_dispatch(monkeypatch, repository, ref):
    def never(*args, **kw):
        raise AssertionError("No command should execute")
    monkeypatch.setattr(github_head_probe.subprocess, "run", never)
    with pytest.raises(ValueError):
        github_head_probe.observe_head(repository, ref=ref, expected_head=SHA)


def test_existing_host_probe_contract_parses_observation(tmp_path, monkeypatch):
    result = {
        "schema": "PORTAL_HOST_PROBE_RESULT_V1",
        "adapter_id": "github-head-observer",
        "evidence_id": "github-head:" + SHA,
        "routes": [{
            "route_id": "github-head:thebrazenbeard/portal:main",
            "node_id": "github-api",
            "target_kind": "repository",
            "target_id": "thebrazenbeard/portal",
            "capabilities": ["source_read"],
            "effect_capabilities": [],
            "available": True, "attached": True, "current": True,
            "preference": 0,
        }],
        "occupancy": [],
    }
    import portal.host_probe as probe_module
    monkeypatch.setattr(
        probe_module.subprocess, "run",
        lambda *a, **kw: _completed(result),
    )
    probe = PortalCommandHostProbe(
        adapter_id="github-head-observer", command=["python", "-m", "portal.github_head_probe"],
        cwd=tmp_path,
    )
    observation = probe.observe()
    assert observation.routes[0].authorized_effects == ()
    assert observation.routes[0].effect_capabilities == ()
