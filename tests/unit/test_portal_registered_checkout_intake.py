"""Offline work intake must not silently resume held repositories."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import pytest
from portal.registered_checkout_intake import inspect_registered_checkouts

def git(path: Path, *args: str) -> str:
    run = subprocess.run(["git", "-C", str(path), *args],
                         text=True, capture_output=True, check=True)
    return run.stdout.strip()

def checkout(root: Path, name: str, *, origin: str | None = None) -> Path:
    path = root / name
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Fixture")
    git(path, "config", "user.email", "fixture@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "remote", "add", "origin",
        origin or f"https://github.com/thebrazenbeard/{name}.git")
    (path / "README.md").write_text("Read-only fixture\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "fixture")
    return path

def index(root: Path, mapping: dict[str, Path]) -> Path:
    path = root / "checkouts.json"
    path.write_text(json.dumps({"schema": "PORTAL_EXISTING_CHECKOUT_INDEX_V1",
       "repositories": {name: str(path) for name, path in mapping.items()}}))
    return path

def test_new_checkout_is_a_candidate_but_held_subject_is_not_replayed(tmp_path: Path):
    old = checkout(tmp_path, "firesafe")
    new = checkout(tmp_path, "new-repo")
    path = index(tmp_path, {"thebrazenbeard/firesafe": old,
                             "thebrazenbeard/new-repo": new})
    original = path.read_bytes()
    result = inspect_registered_checkouts(path, owner="thebrazenbeard",
                                          observed_subject_ids={"firesafe"})
    assert [r.repository for r in result] == [
        "thebrazenbeard/firesafe", "thebrazenbeard/new-repo"]
    assert [r.already_observed for r in result] == [True, False]
    assert result[1].head == git(new, "rev-parse", "HEAD")
    assert result[1].source_ref == "main"
    assert result[1].subject_id == "new-repo"
    assert path.read_bytes() == original
    assert not git(new, "status", "--porcelain")

@pytest.mark.parametrize("wrong_origin", [True, False])
def test_checkout_must_be_clean_and_have_matching_github_origin(
    tmp_path: Path, wrong_origin: bool
):
    repo = checkout(tmp_path, "example",
        origin="https://github.com/other/example.git" if wrong_origin else None)
    if not wrong_origin:
        (repo / "README.md").write_text("changed\n", encoding="utf-8")
    path = index(tmp_path, {"thebrazenbeard/example": repo})
    with pytest.raises(ValueError, match="origin" if wrong_origin else "clean"):
        inspect_registered_checkouts(path, owner="thebrazenbeard",
                                     observed_subject_ids=set())
def test_untrusted_index_fails_closed_on_foreign_owner_or_case_duplicate(tmp_path: Path):
    repo = checkout(tmp_path, "example")
    foreign = index(tmp_path, {"someoneelse/example": repo})
    with pytest.raises(ValueError, match="owner"):
        inspect_registered_checkouts(foreign, owner="thebrazenbeard",
                                     observed_subject_ids=set())
    duplicate = index(tmp_path, {"thebrazenbeard/example": repo,
                                  "thebrazenbeard/EXAMPLE": repo})
    with pytest.raises(ValueError, match="duplicate"):
        inspect_registered_checkouts(duplicate, owner="thebrazenbeard",
                                     observed_subject_ids=set())


def test_registered_checkout_cannot_alias_subdirectory_as_repository_root(tmp_path: Path):
    repo = checkout(tmp_path, "example")
    nested = repo / "src"
    nested.mkdir()
    path = index(tmp_path, {"thebrazenbeard/example": nested})
    with pytest.raises(ValueError, match="Git worktree root"):
        inspect_registered_checkouts(path, owner="thebrazenbeard",
                                     observed_subject_ids=set())
