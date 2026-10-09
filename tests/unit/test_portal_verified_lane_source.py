"""Authenticated owned-census/ref resolution must precede any candidate."""
from pathlib import Path
import pytest
import yaml
from portal.discovery import RepositoryInventoryItem
from portal.registered_checkout_intake import RegisteredCheckout
import portal.verified_lane_source as subject

class Catalog:
    def __init__(self, *, token="read-only-token", wrong_ref=False):
        self.token = token
        self.api_base = "https://api.github.com"
        self.urls = []
        self.wrong_ref = wrong_ref
    def list_owned_repositories(self, owner):
        assert owner == "thebrazenbeard"
        return (RepositoryInventoryItem(
            name="vera-mono", full_name="thebrazenbeard/vera-mono",
            private=False, archived=False, default_branch="main"),)
    def _get_json(self, url):
        self.urls.append(url)
        return {"ref": "refs/heads/other" if self.wrong_ref else "refs/heads/main",
                "object":{"type":"commit", "sha":"a"*40}}

def nodes(tmp_path, slots=1):
    path=tmp_path/"nodes.yaml"
    path.write_text(yaml.safe_dump({"schema":"PORTAL_EXECUTION_NODES_V1",
        "nodes":[{"id":"local","enabled":True,"max_parallel":slots,
                   "allowed_lanes":[]}]}),encoding="utf-8")
    return path

def scanner(monkeypatch):
    records=(RegisteredCheckout(
        repository="thebrazenbeard/vera-mono",subject_id="vera-mono",
        checkout=Path("C:/checkout/vera-mono"),head="a"*40,
        source_ref="main",already_observed=False),)
    monkeypatch.setattr(subject,"inspect_registered_checkouts",
                        lambda *_args,**_kwargs:records)
def test_verified_git_ref_plus_nodes_yields_one_candidate(tmp_path,monkeypatch):
    scanner(monkeypatch)
    catalog=Catalog()
    rows=subject.plan_authenticated_source_lanes(
        index_path=tmp_path/"index.json", nodes_path=nodes(tmp_path),
        owner="thebrazenbeard", catalog=catalog, qualified_slot_limit=1,
        observed_subject_ids=set(), delegated_subject_ids=set())
    assert [(x.subject_id,x.disposition) for x in rows] == [
        ("vera-mono","CANDIDATE_FOR_SCHEDULER")]
    assert catalog.urls == [
        "https://api.github.com/repos/thebrazenbeard/vera-mono/git/ref/heads/main"]

def test_missing_token_fails_before_git_api_or_scanning(tmp_path,monkeypatch):
    scanner(monkeypatch)
    catalog=Catalog(token="")
    with pytest.raises(ValueError,match="authenticated"):
        subject.plan_authenticated_source_lanes(
            index_path=tmp_path/"index.json",nodes_path=nodes(tmp_path),
            owner="thebrazenbeard", catalog=catalog,qualified_slot_limit=1,
            observed_subject_ids=set(),delegated_subject_ids=set())
    assert not catalog.urls

def test_wrong_ref_cannot_become_candidate(tmp_path,monkeypatch):
    scanner(monkeypatch)
    with pytest.raises(ValueError,match="GitHub ref"):
        subject.plan_authenticated_source_lanes(
            index_path=tmp_path/"index.json",nodes_path=nodes(tmp_path),
            owner="thebrazenbeard",catalog=Catalog(wrong_ref=True),
            qualified_slot_limit=1,observed_subject_ids=set(),
            delegated_subject_ids=set())
def test_capacity_above_node_limit_fails_closed(tmp_path,monkeypatch):
    scanner(monkeypatch)
    with pytest.raises(ValueError,match="capacity"):
        subject.plan_authenticated_source_lanes(
            index_path=tmp_path/"index.json",nodes_path=nodes(tmp_path),
            owner="thebrazenbeard",catalog=Catalog(),qualified_slot_limit=13,
            observed_subject_ids=set(),delegated_subject_ids=set())

def test_prior_hold_survives_authenticated_refresh(tmp_path,monkeypatch):
    scanner(monkeypatch)
    rows=subject.plan_authenticated_source_lanes(
        index_path=tmp_path/"index.json",nodes_path=nodes(tmp_path),
        owner="thebrazenbeard",catalog=Catalog(),qualified_slot_limit=1,
        observed_subject_ids={"vera-mono"},delegated_subject_ids=set())
    assert rows[0].disposition=="HOLD_PREVIOUSLY_OBSERVED"
