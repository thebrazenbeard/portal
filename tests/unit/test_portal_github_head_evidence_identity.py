"""GitHub observation provenance must bind the request, not just reply SHA."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from portal import github_head_probe


SHA = "a" * 40
OTHER = "b" * 40


def _reply(*args, **kwargs):
    return SimpleNamespace(returncode=0, stdout=json.dumps({"sha": SHA}), stderr="")


def test_equal_head_different_repositories_are_distinct_evidence(monkeypatch):
    monkeypatch.setattr(github_head_probe.subprocess, "run", _reply)
    a = github_head_probe.observe_head(
        "thebrazenbeard/portal", ref="main", expected_head=SHA,
    )
    b = github_head_probe.observe_head(
        "thebrazenbeard/vera-mono", ref="main", expected_head=SHA,
    )
    assert a["evidence_id"] != b["evidence_id"]
    for receipt in (a, b):
        assert receipt["routes"][0]["effect_capabilities"] == []
        assert "authorized_effects" not in receipt["routes"][0]


def test_different_expected_head_cannot_share_evidence_id(monkeypatch):
    monkeypatch.setattr(github_head_probe.subprocess, "run", _reply)
    current = github_head_probe.observe_head(
        "thebrazenbeard/portal", ref="main", expected_head=SHA,
    )
    stale = github_head_probe.observe_head(
        "thebrazenbeard/portal", ref="main", expected_head=OTHER,
    )
    assert current["routes"][0]["current"] is True
    assert stale["routes"][0]["current"] is False
    assert current["evidence_id"] != stale["evidence_id"]


def test_different_refs_cannot_share_evidence_id(monkeypatch):
    monkeypatch.setattr(github_head_probe.subprocess, "run", _reply)
    main = github_head_probe.observe_head(
        "thebrazenbeard/portal", ref="main", expected_head=SHA,
    )
    branch = github_head_probe.observe_head(
        "thebrazenbeard/portal", ref="review/branch", expected_head=SHA,
    )
    assert main["evidence_id"] != branch["evidence_id"]


@pytest.mark.parametrize("invalid", [
    "feature..malformed", "refs/heads/branch.lock/child",
    "refs/heads/branch.", "feature/.hidden",
])
def test_git_ref_components_are_strictly_validated(monkeypatch, invalid):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid ref triggered a subprocess")
    monkeypatch.setattr(github_head_probe.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="invalid GitHub ref"):
        github_head_probe.observe_head(
            "thebrazenbeard/portal", ref=invalid, expected_head=SHA,
        )
