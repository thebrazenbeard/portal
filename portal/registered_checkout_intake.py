"""Read-only registered-checkout frontier discovery for local advisory planning."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_HEAD = re.compile(r"^[0-9a-f]{40}$")

@dataclass(frozen=True)
class RegisteredCheckout:
    repository: str
    subject_id: str
    checkout: Path
    head: str
    source_ref: str
    already_observed: bool

def _git(checkout: Path, *args: str) -> str:
    """Read locally without Git's optional index lock or fsmonitor hook."""
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
         "-C", str(checkout), *args],
        check=True, capture_output=True, text=True, errors="replace", timeout=20,
    )
    return result.stdout.strip()

def _matches_origin(actual: str, repository: str) -> bool:
    repo = repository.casefold()
    origin = actual.strip().rstrip("/").casefold()
    if origin.endswith(".git"):
        origin = origin[:-4]
    return origin in {
        f"https://github.com/{repo}",
        f"ssh://git@github.com/{repo}",
        f"git@github.com:{repo}",
    }

def inspect_registered_checkouts(
    index_path: Path, *, owner: str, observed_subject_ids: set[str]
) -> tuple[RegisteredCheckout, ...]:
    """Produce an offline candidate plan, never a dispatch or admission."""
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        raise ValueError("invalid portfolio owner")
    payload = json.loads(Path(index_path).read_text(encoding="utf-8-sig"))
    if (not isinstance(payload, dict)
        or set(payload) != {"schema", "repositories"}
        or payload["schema"] != "PORTAL_EXISTING_CHECKOUT_INDEX_V1"
        or not isinstance(payload["repositories"], dict)):
        raise ValueError("invalid checkout index schema")
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    observed = {x.casefold() for x in observed_subject_ids}
    rows: list[RegisteredCheckout] = []
    for repository, value in sorted(payload["repositories"].items(),
                                    key=lambda kv: str(kv[0]).casefold()):
        if (not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository)
            or any(x in {".", ".."} for x in repository.split("/"))):
            raise ValueError("invalid registered repository")
        repo_owner, subject_id = repository.split("/", 1)
        if repo_owner.casefold() != owner.casefold():
            raise ValueError("registered repository outside portfolio owner")
        if repository.casefold() in seen_names or subject_id.casefold() in seen_ids:
            raise ValueError("duplicate registered repository or subject")
        seen_names.add(repository.casefold())
        seen_ids.add(subject_id.casefold())
        if not isinstance(value, str) or not value.strip():
            raise ValueError("registered checkout path must be nonempty")
        checkout = Path(value)
        if not checkout.is_absolute() or checkout.is_symlink() or not checkout.is_dir():
            raise ValueError("registered checkout is not an absolute local directory")
        if not _matches_origin(_git(checkout, "remote", "get-url", "origin"), repository):
            raise ValueError("registered checkout origin mismatch")
        head = _git(checkout, "rev-parse", "HEAD")
        if not _HEAD.fullmatch(head):
            raise ValueError("invalid current checkout head")
        source_ref = _git(checkout, "rev-parse", "--abbrev-ref", "HEAD")
        if source_ref == "HEAD" or not source_ref:
            raise ValueError("detached checkout has no source ref")
        if _git(checkout, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("registered checkout must be clean")
        rows.append(RegisteredCheckout(
            repository=repository, subject_id=subject_id, checkout=checkout,
            head=head, source_ref=source_ref,
            already_observed=subject_id.casefold() in observed,
        ))
    return tuple(rows)
