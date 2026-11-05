from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from portal.discovery import RepositoryInventoryItem
from portal.pr_intake import (
    CANDIDATE_SCHEMA,
    SCHEMA,
    GitHubPullRequestCatalog,
    collect_pr_candidates,
    pr_candidate,
    write_local_snapshot,
)


HEAD = "a" * 40
BASE = "b" * 40
NEXT = "c" * 40
REPOSITORY = "thebrazenbeard/portal"


def _repo(name: str = "portal", *, private: bool = False, archived: bool = False):
    return RepositoryInventoryItem(
        name=name, full_name=f"thebrazenbeard/{name}",
        private=private, archived=archived, default_branch="main",
    )


def _pull(
    number: int = 14, *, head: str = HEAD, base: str = BASE,
    name: str = REPOSITORY,
):
    return {
        "number": number,
        "state": "open",
        "draft": True,
        "head": {
            "sha": head,
            "ref": "feat/review",
            "repo": {"full_name": "elsewhere/fork"},
        },
        "base": {
            "sha": base,
            "ref": "main",
            "repo": {"full_name": name},
        },
    }


FILES = [
    {"filename": "portal/a.py", "status": "modified"},
    {"filename": "portal/new.py", "previous_filename": "portal/old.py", "status": "renamed"},
]


class FakePRCatalog:
    def __init__(self, pulls: tuple[object, ...], *, token: str | None = "test"):
        self.pulls = pulls
        self.token = token
        self.file_requests: list[tuple[str, int]] = []
        self.verified = {}
        self.files = {}

    def list_open_pull_requests(self, repository):
        return list(self.pulls)

    def list_pr_files(self, repository, number):
        self.file_requests.append((repository, number))
        return self.files.get(number, FILES)

    def get_pr(self, repository, number):
        return self.verified.get(number, _pull(number))


def test_candidate_contains_exact_revision_and_separate_collision_scopes():
    candidate = pr_candidate(_repo(), _pull(), FILES, verification=_pull())
    assert candidate["schema"] == CANDIDATE_SCHEMA
    assert candidate["subject_key"] == "github-pr:thebrazenbeard/portal#14"
    assert HEAD in candidate["revision_key"]
    assert candidate["base_sha"] == BASE
    assert candidate["head_repository"] == "elsewhere/fork"
    assert candidate["changed_files"] == [
        "portal/a.py", "portal/new.py", "portal/old.py",
    ]
    assert candidate["review_collision_keys"] == [
        "file:thebrazenbeard/portal/portal/a.py",
        "file:thebrazenbeard/portal/portal/new.py",
        "file:thebrazenbeard/portal/portal/old.py",
    ]
    assert candidate["mutation_collision_keys"] == [
        "repository:thebrazenbeard/portal"
    ]
    assert candidate["disposition"] == "UNREVIEWED"
    assert candidate["candidate_dispositions"] == [
        "KEEP", "CONSOLIDATE", "SUPERSEDE",
    ]
    assert candidate["execution_authority"] is False
    assert candidate["source_mutation_authorized"] is False
    assert candidate["protected_effects_authorized"] is False


def test_candidate_digest_is_stable_for_file_listing_order():
    first = pr_candidate(_repo(), _pull(), FILES, verification=_pull())
    second = pr_candidate(_repo(), _pull(), list(reversed(FILES)), verification=_pull())
    assert first["revision_key"] == second["revision_key"]


@pytest.mark.parametrize(
    ("changed", "match"),
    [
        ({"head": {"sha": NEXT}}, "PR head"),
        ({"base": {"sha": NEXT}}, "PR base"),
        ({"base": {"ref": "other"}}, "target ref"),
        ({"state": "closed"}, "no longer open"),
        ({"number": 99}, "identity changed"),
    ],
)
def test_mid_read_mutation_fails_closed(changed, match):
    original = _pull()
    verified = deepcopy(original)
    for field, value in changed.items():
        if isinstance(value, dict):
            verified[field].update(value)
        else:
            verified[field] = value
    with pytest.raises(ValueError, match=match):
        pr_candidate(_repo(), original, FILES, verification=verified)


@pytest.mark.parametrize(
    ("bad_files", "match"),
    [
        ([{"filename": "../outside.py"}], "safe relative path"),
        ([{"filename": "/etc/passwd"}], "safe relative path"),
        ([{"filename": "src\\oops.py"}], "safe relative path"),
        ([{"filename": "same.py"}, {"filename": "same.py"}], "duplicate"),
    ],
)
def test_changed_filename_validation(bad_files, match):
    with pytest.raises(ValueError, match=match):
        pr_candidate(_repo(), _pull(), bad_files, verification=_pull())


def test_public_only_snapshot_and_archived_exclusion():
    catalog = FakePRCatalog((_pull(),))
    snapshot = collect_pr_candidates(
        "thebrazenbeard",
        [_repo(private=True), _repo("archive", archived=True)],
        catalog,
        observed_at="2026-11-05T00:00:00+00:00",
    )
    assert snapshot["schema"] == SCHEMA
    assert snapshot["repository_inventory_count"] == 2
    assert snapshot["candidate_count"] == 0
    assert snapshot["candidates"] == []
    assert not catalog.file_requests


def test_private_snapshot_requires_explicit_token_and_scope():
    catalog = FakePRCatalog((_pull(),), token=None)
    with pytest.raises(ValueError, match="authenticated"):
        collect_pr_candidates(
            "thebrazenbeard", [_repo(private=True)], catalog,
            include_private=True,
        )
    catalog.token = "test"
    snapshot = collect_pr_candidates(
        "thebrazenbeard", [_repo(private=True)], catalog,
        include_private=True,
    )
    assert snapshot["candidate_count"] == 1
    assert snapshot["candidates"][0]["private"] is True


def test_changed_head_during_collection_prevents_any_candidate_snapshot():
    catalog = FakePRCatalog((_pull(),))
    catalog.verified[14] = _pull(head=NEXT)
    with pytest.raises(ValueError, match="changed during intake"):
        collect_pr_candidates("thebrazenbeard", [_repo()], catalog)


def test_duplicate_pr_entries_and_duplicate_repository_fail_closed():
    catalog = FakePRCatalog((_pull(), _pull()))
    with pytest.raises(ValueError, match="duplicate PR identity"):
        collect_pr_candidates("thebrazenbeard", [_repo()], catalog)

    catalog.pulls = (_pull(),)
    with pytest.raises(ValueError, match="duplicate repository"):
        collect_pr_candidates("thebrazenbeard", [_repo(), _repo()], catalog)


def test_owner_scope_mismatch_fails_closed():
    bad = RepositoryInventoryItem(
        name="else", full_name="other/else",
        private=False, archived=False, default_branch="main",
    )
    with pytest.raises(ValueError, match="crossed portfolio owner"):
        collect_pr_candidates("thebrazenbeard", [bad], FakePRCatalog(()))


def test_snapshot_write_create_only_and_no_source_tree(tmp_path: Path):
    snapshot = {"schema": SCHEMA, "private_scope_included": True}
    file = tmp_path / "private" / "candidates.json"
    write_local_snapshot(file, snapshot)
    assert '"private_scope_included": true' in file.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_local_snapshot(file, snapshot)

    portal_source = Path(__file__).resolve().parents[2] / "any-pr-snapshot.json"
    with pytest.raises(ValueError, match="source tree"):
        write_local_snapshot(portal_source, snapshot)


def test_paginated_read_returns_every_page_and_no_partial_silent_limit():
    class Stub(GitHubPullRequestCatalog):
        def __init__(self):
            super().__init__(token="test")
            self.urls = []

        def _get_json(self, url):
            self.urls.append(url)
            return list(range(100)) if "page=1" in url else [101]

    catalog = Stub()
    result = catalog.list_open_pull_requests(REPOSITORY)
    assert len(result) == 101
    assert len(catalog.urls) == 2
    assert all("state=open" in url for url in catalog.urls)


def test_nonlist_page_fails_closed():
    class Stub(GitHubPullRequestCatalog):
        def _get_json(self, url):
            return {"message": "partial API error"}
    with pytest.raises(ValueError, match="not a list"):
        Stub().list_open_pull_requests(REPOSITORY)
