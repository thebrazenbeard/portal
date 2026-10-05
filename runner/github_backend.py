from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from enum import Enum
import json
from typing import Iterable, Mapping, Protocol
from urllib import error, parse, request

from .backends import BackendResult
from .work_units import WorkUnit, work_unit_fingerprint


class GitHubPreconditionFailed(RuntimeError):
    """Exact Git state precondition failed before ref publication."""


class GitHubOutcomeUnknown(RuntimeError):
    """A ref publication may have occurred but cannot be proven by readback."""

    def __init__(
        self,
        message: str,
        *,
        candidate_commit_sha: str | None = None,
        candidate_blob_sha: str | None = None,
    ) -> None:
        super().__init__(message)
        self.candidate_commit_sha = candidate_commit_sha
        self.candidate_blob_sha = candidate_blob_sha


class GitHubOperation(str, Enum):
    READ_REF = "READ_REF"
    READ_FILE = "READ_FILE"
    CREATE_BRANCH = "CREATE_BRANCH"
    PUT_FILE = "PUT_FILE"


_ROUTE_CAPABILITY = {
    GitHubOperation.READ_REF: "github.read_ref",
    GitHubOperation.READ_FILE: "github.read_file",
    GitHubOperation.CREATE_BRANCH: "github.create_branch",
    GitHubOperation.PUT_FILE: "github.put_file",
}


@dataclass(frozen=True)
class GitHubFileState:
    sha: str
    content: str


@dataclass(frozen=True)
class GitHubRequest:
    operation: GitHubOperation
    repository: str
    ref: str
    expected_head: str | None = None
    source_ref: str | None = None
    path: str | None = None
    content: str | None = None
    message: str | None = None
    expected_blob_sha: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "GitHubRequest":
        operation = GitHubOperation(str(data["operation"]))
        repository = str(data["repository"]).strip()
        ref = str(data["ref"]).strip()
        if not repository or "/" not in repository:
            raise ValueError("github request requires owner/repository")
        if not ref:
            raise ValueError("github request requires ref")
        return cls(
            operation=operation,
            repository=repository,
            ref=ref,
            expected_head=str(data["expected_head"]) if data.get("expected_head") is not None else None,
            source_ref=str(data["source_ref"]) if data.get("source_ref") is not None else None,
            path=str(data["path"]) if data.get("path") is not None else None,
            content=str(data["content"]) if data.get("content") is not None else None,
            message=str(data["message"]) if data.get("message") is not None else None,
            expected_blob_sha=str(data["expected_blob_sha"]) if data.get("expected_blob_sha") is not None else None,
        )


@dataclass(frozen=True)
class TargetAuthorityGrant:
    repository: str
    operations: tuple[GitHubOperation, ...]
    ref_prefixes: tuple[str, ...]
    path_prefixes: tuple[str, ...] = ()

    def allows(self, req: GitHubRequest) -> bool:
        if req.repository != self.repository or req.operation not in self.operations:
            return False
        if not any(_scope_match(req.ref, item) for item in self.ref_prefixes):
            return False
        if req.operation in {GitHubOperation.READ_FILE, GitHubOperation.PUT_FILE}:
            if req.path is None or not self.path_prefixes:
                return False
            if not any(_path_scope_match(req.path, item) for item in self.path_prefixes):
                return False
        return True


def _scope_match(value: str, scope: str) -> bool:
    return value.startswith(scope) if scope.endswith("/") else value == scope


def _path_scope_match(value: str, scope: str) -> bool:
    normalized = scope.lstrip("/")
    return value == normalized or value.startswith(normalized.rstrip("/") + "/")


class GitHubTransport(Protocol):
    def read_ref(self, repository: str, ref: str) -> str: ...
    def read_file(self, repository: str, path: str, ref: str) -> GitHubFileState | None: ...
    def create_branch(self, repository: str, branch: str, sha: str) -> None: ...
    def put_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        expected_blob_sha: str | None = None,
    ) -> str: ...
    def put_file_exact_head(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        *,
        expected_head: str,
        expected_blob_sha: str | None = None,
    ) -> tuple[str, str]: ...


class GitHubRestTransport:
    """Small GitHub REST transport using only the Python standard library."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base: str = "https://api.github.com",
        graphql_url: str | None = None,
    ) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        if graphql_url is not None:
            self.graphql_url = graphql_url
        elif self.api_base == "https://api.github.com":
            self.graphql_url = "https://api.github.com/graphql"
        elif self.api_base.endswith("/api/v3"):
            self.graphql_url = self.api_base[:-7] + "/api/graphql"
        else:
            self.graphql_url = self.api_base + "/graphql"

    def _request(self, method: str, url: str, body: Mapping[str, object] | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "project-runner-m5",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
        except error.HTTPError as exc:
            if exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"github http {exc.code}: {detail}") from exc
        return json.loads(raw.decode("utf-8")) if raw else {}

    def read_ref(self, repository: str, ref: str) -> str:
        owner, repo = repository.split("/", 1)
        encoded_ref = parse.quote(f"heads/{ref}", safe="/")
        payload = self._request(
            "GET",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/git/ref/{encoded_ref}",
        )
        if payload is None:
            raise KeyError(f"missing ref {repository}@{ref}")
        return str(payload["object"]["sha"])

    def read_file(self, repository: str, path: str, ref: str) -> GitHubFileState | None:
        owner, repo = repository.split("/", 1)
        encoded_path = parse.quote(path.lstrip("/"), safe="/")
        encoded_ref = parse.quote(ref, safe="")
        payload = self._request(
            "GET",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/contents/{encoded_path}?ref={encoded_ref}",
        )
        if payload is None:
            return None
        if payload.get("type") != "file":
            raise RuntimeError("github contents target is not a file")
        encoding = payload.get("encoding")
        raw_content = str(payload.get("content", "")).replace("\n", "")
        if encoding != "base64":
            raise RuntimeError("unsupported github file encoding")
        content = base64.b64decode(raw_content).decode("utf-8")
        return GitHubFileState(sha=str(payload["sha"]), content=content)

    def read_commit_tree(self, repository: str, commit_sha: str) -> str:
        owner, repo = repository.split("/", 1)
        payload = self._request(
            "GET",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/git/commits/{parse.quote(commit_sha, safe='')}",
        )
        if payload is None:
            raise KeyError(commit_sha)
        tree = payload.get("tree")
        if not isinstance(tree, Mapping) or not tree.get("sha"):
            raise RuntimeError("github commit response missing tree")
        return str(tree["sha"])

    def inspect_update_refs_schema(self) -> Mapping[str, tuple[str, ...]]:
        query = """
        query ProjectRunnerUpdateRefsSchema {
          mutationType: __type(name: "Mutation") {
            fields { name }
          }
          updateRefsInput: __type(name: "UpdateRefsInput") {
            inputFields { name }
          }
          refUpdate: __type(name: "RefUpdate") {
            inputFields { name }
          }
        }
        """
        payload = self._request(
            "POST",
            self.graphql_url,
            {"query": query, "variables": {}},
        )
        if payload is None:
            raise RuntimeError("github graphql schema query returned no payload")
        errors = payload.get("errors")
        if errors:
            raise RuntimeError("github graphql schema query returned errors")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise RuntimeError("github graphql schema query missing data")

        def names(type_payload: object, field: str) -> tuple[str, ...]:
            if not isinstance(type_payload, Mapping):
                return ()
            values = type_payload.get(field)
            if not isinstance(values, list):
                return ()
            return tuple(
                sorted(
                    str(item.get("name"))
                    for item in values
                    if isinstance(item, Mapping) and item.get("name")
                )
            )

        return {
            "mutation_fields": names(data.get("mutationType"), "fields"),
            "update_refs_input_fields": names(
                data.get("updateRefsInput"),
                "inputFields",
            ),
            "ref_update_fields": names(
                data.get("refUpdate"),
                "inputFields",
            ),
        }

    def create_branch(self, repository: str, branch: str, sha: str) -> None:
        owner, repo = repository.split("/", 1)
        self._request(
            "POST",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def put_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        expected_blob_sha: str | None = None,
    ) -> str:
        owner, repo = repository.split("/", 1)
        encoded_path = parse.quote(path.lstrip("/"), safe="/")
        payload: dict[str, object] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if expected_blob_sha is not None:
            payload["sha"] = expected_blob_sha
        response = self._request(
            "PUT",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/contents/{encoded_path}",
            payload,
        )
        return str(response["commit"]["sha"])

    def _tree_entry(
        self,
        repository: str,
        tree_sha: str,
        path: str,
    ) -> Mapping[str, object] | None:
        owner, repo = repository.split("/", 1)
        parts = [part for part in path.lstrip("/").split("/") if part]
        if not parts:
            raise ValueError("github exact source write requires a file path")
        current_tree = tree_sha
        for index, part in enumerate(parts):
            payload = self._request(
                "GET",
                f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/git/trees/{parse.quote(current_tree, safe='')}",
            )
            if payload is None:
                raise KeyError(f"missing tree {current_tree}")
            entries = payload.get("tree")
            if not isinstance(entries, list):
                raise RuntimeError("github tree response missing entries")
            entry = next(
                (
                    item
                    for item in entries
                    if isinstance(item, Mapping)
                    and str(item.get("path", "")) == part
                ),
                None,
            )
            if entry is None:
                return None
            if index == len(parts) - 1:
                return entry
            if str(entry.get("type", "")) != "tree":
                return None
            current_tree = str(entry.get("sha", ""))
            if not current_tree:
                raise RuntimeError("github subtree response missing sha")
        return None

    def _update_ref_exact(
        self,
        *,
        repository: str,
        branch: str,
        expected_head: str,
        new_head: str,
    ) -> None:
        owner, repo = repository.split("/", 1)
        repository_payload = self._request(
            "GET",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}",
        )
        if repository_payload is None:
            raise KeyError(repository)
        repository_id = str(repository_payload.get("node_id", ""))
        if not repository_id:
            raise RuntimeError("github repository response missing node id")

        query = """
        mutation UpdateRefs($input: UpdateRefsInput!) {
          updateRefs(input: $input) {
            clientMutationId
          }
        }
        """
        body = {
            "query": query,
            "variables": {
                "input": {
                    "repositoryId": repository_id,
                    "refUpdates": [
                        {
                            "name": f"refs/heads/{branch}",
                            "beforeOid": expected_head,
                            "afterOid": new_head,
                            "force": False,
                        }
                    ],
                }
            },
        }
        try:
            payload = self._request("POST", self.graphql_url, body)
        except RuntimeError as exc:
            message = str(exc)
            if "github http 409:" in message or "github http 422:" in message:
                raise GitHubPreconditionFailed(
                    "github exact ref compare-and-swap rejected"
                ) from exc
            raise GitHubOutcomeUnknown(
                "github exact ref update outcome is unknown"
            ) from exc
        if payload is None:
            raise GitHubOutcomeUnknown(
                "github exact ref update returned no payload"
            )
        errors = payload.get("errors")
        if errors:
            messages = " ".join(
                str(item.get("message", ""))
                for item in errors
                if isinstance(item, Mapping)
            ).lower()
            if "beforeoid" in messages or "before oid" in messages:
                raise GitHubPreconditionFailed(
                    "github exact ref compare-and-swap rejected"
                )
            raise GitHubOutcomeUnknown(
                "github exact ref update returned an unclassified error"
            )
        data = payload.get("data")
        if (
            not isinstance(data, Mapping)
            or not isinstance(data.get("updateRefs"), Mapping)
        ):
            raise GitHubOutcomeUnknown(
                "github exact ref update response is incomplete"
            )

    def put_file_exact_head(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        *,
        expected_head: str,
        expected_blob_sha: str | None = None,
    ) -> tuple[str, str]:
        owner, repo = repository.split("/", 1)
        observed_head = self.read_ref(repository, branch)
        if observed_head != expected_head:
            raise GitHubPreconditionFailed("github exact head precondition failed")

        commit_payload = self._request(
            "GET",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/git/commits/{parse.quote(expected_head, safe='')}",
        )
        if commit_payload is None:
            raise KeyError(expected_head)
        tree = commit_payload.get("tree")
        if not isinstance(tree, Mapping) or not tree.get("sha"):
            raise RuntimeError("github commit response missing tree")
        base_tree_sha = str(tree["sha"])

        existing = self._tree_entry(repository, base_tree_sha, path)
        if existing is None:
            if expected_blob_sha is not None:
                raise GitHubPreconditionFailed(
                    "github expected blob is missing at exact head"
                )
            mode = "100644"
        else:
            if str(existing.get("type", "")) != "blob":
                raise GitHubPreconditionFailed(
                    "github exact source-write target is not a blob"
                )
            mode = str(existing.get("mode", ""))
            if mode != "100644":
                raise GitHubPreconditionFailed(
                    "github exact source-write supports regular files only"
                )
            observed_blob_sha = str(existing.get("sha", ""))
            if expected_blob_sha is None:
                raise GitHubPreconditionFailed(
                    "github existing file requires expected blob sha"
                )
            if observed_blob_sha != expected_blob_sha:
                raise GitHubPreconditionFailed(
                    "github exact blob precondition failed"
                )

        blob_payload = self._request(
            "POST",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/git/blobs",
            {"content": content, "encoding": "utf-8"},
        )
        if blob_payload is None or not blob_payload.get("sha"):
            raise RuntimeError("github blob creation missing sha")
        blob_sha = str(blob_payload["sha"])

        tree_payload = self._request(
            "POST",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/git/trees",
            {
                "base_tree": base_tree_sha,
                "tree": [
                    {
                        "path": path.lstrip("/"),
                        "mode": mode,
                        "type": "blob",
                        "sha": blob_sha,
                    }
                ],
            },
        )
        if tree_payload is None or not tree_payload.get("sha"):
            raise RuntimeError("github tree creation missing sha")
        new_tree_sha = str(tree_payload["sha"])

        new_commit_payload = self._request(
            "POST",
            f"{self.api_base}/repos/{parse.quote(owner)}/{parse.quote(repo)}/git/commits",
            {
                "message": message,
                "tree": new_tree_sha,
                "parents": [expected_head],
            },
        )
        if new_commit_payload is None or not new_commit_payload.get("sha"):
            raise RuntimeError("github commit creation missing sha")
        new_commit_sha = str(new_commit_payload["sha"])

        try:
            self._update_ref_exact(
                repository=repository,
                branch=branch,
                expected_head=expected_head,
                new_head=new_commit_sha,
            )
        except GitHubOutcomeUnknown as exc:
            raise GitHubOutcomeUnknown(
                str(exc),
                candidate_commit_sha=new_commit_sha,
                candidate_blob_sha=blob_sha,
            ) from exc

        try:
            readback_head = self.read_ref(repository, branch)
            readback_file = self.read_file(repository, path, branch)
        except (KeyError, RuntimeError, ValueError) as exc:
            raise GitHubOutcomeUnknown(
                "github exact source-write readback outcome is unknown",
                candidate_commit_sha=new_commit_sha,
                candidate_blob_sha=blob_sha,
            ) from exc
        if (
            readback_head != new_commit_sha
            or readback_file is None
            or readback_file.sha != blob_sha
            or readback_file.content != content
        ):
            raise GitHubOutcomeUnknown(
                "github exact source-write readback failed",
                candidate_commit_sha=new_commit_sha,
                candidate_blob_sha=blob_sha,
            )
        return new_commit_sha, blob_sha


class GitHubBackend:
    """Authority-separated GitHub execution backend.

    Technical route capability and target authorization are independent gates.
    """

    def __init__(
        self,
        *,
        transport: GitHubTransport,
        route_capabilities: Iterable[str],
        grants: Iterable[TargetAuthorityGrant],
    ) -> None:
        self.transport = transport
        self.route_capabilities = frozenset(route_capabilities)
        self.grants = tuple(grants)

    def execute(self, work: WorkUnit) -> BackendResult:
        fingerprint = work_unit_fingerprint(work)
        try:
            raw = work.payload.get("github")
            if not isinstance(raw, Mapping):
                raise ValueError("work payload missing github request")
            req = GitHubRequest.from_mapping(raw)
        except (KeyError, TypeError, ValueError) as exc:
            return _failure(fingerprint, "INVALID_REQUEST", str(exc))

        route_capability = _ROUTE_CAPABILITY[req.operation]
        if route_capability not in self.route_capabilities:
            return _failure(fingerprint, "ROUTE_UNAVAILABLE", route_capability)

        if not any(grant.allows(req) for grant in self.grants):
            return _failure(fingerprint, "AUTHORITY_DENIED", "target authority denied")

        try:
            if req.operation is GitHubOperation.READ_REF:
                return self._read_ref(fingerprint, req)
            if req.operation is GitHubOperation.READ_FILE:
                return self._read_file(fingerprint, req)
            if req.operation is GitHubOperation.CREATE_BRANCH:
                return self._create_branch(fingerprint, req)
            if req.operation is GitHubOperation.PUT_FILE:
                return self._put_file(fingerprint, req)
        except (KeyError, RuntimeError, ValueError):
            return _failure(fingerprint, "TRANSPORT_FAILED", "github transport failed")

        return _failure(fingerprint, "INVALID_REQUEST", "unsupported operation")

    def _read_ref(self, fingerprint: str, req: GitHubRequest) -> BackendResult:
        observed = self.transport.read_ref(req.repository, req.ref)
        if req.expected_head is not None and observed != req.expected_head:
            return _failure(fingerprint, "PRECONDITION_FAILED", "exact ref precondition failed")
        return BackendResult(
            work_fingerprint=fingerprint,
            succeeded=True,
            outputs=(observed,),
            evidence=("github:read-ref", "github:readback-verified"),
            classification="SUCCEEDED",
        )

    def _read_file(self, fingerprint: str, req: GitHubRequest) -> BackendResult:
        if req.path is None:
            return _failure(fingerprint, "INVALID_REQUEST", "read file requires path")
        read_ref = req.ref
        if req.expected_head is not None:
            observed_head = self.transport.read_ref(req.repository, req.ref)
            if observed_head != req.expected_head:
                return _failure(fingerprint, "PRECONDITION_FAILED", "exact ref precondition failed")
            read_ref = req.expected_head
        observed = self.transport.read_file(req.repository, req.path, read_ref)
        if observed is None:
            return _failure(fingerprint, "NOT_FOUND", "file not found")
        if req.expected_blob_sha is not None and observed.sha != req.expected_blob_sha:
            return _failure(fingerprint, "PRECONDITION_FAILED", "blob mismatch")
        content_digest = hashlib.sha256(observed.content.encode("utf-8")).hexdigest()
        return BackendResult(
            work_fingerprint=fingerprint,
            succeeded=True,
            outputs=(observed.sha, content_digest),
            evidence=("github:file-read", "github:readback-verified"),
            classification="SUCCEEDED",
        )

    def _create_branch(self, fingerprint: str, req: GitHubRequest) -> BackendResult:
        if req.source_ref is None or req.expected_head is None:
            return _failure(fingerprint, "INVALID_REQUEST", "create branch requires source_ref and expected_head")
        observed_source = self.transport.read_ref(req.repository, req.source_ref)
        if observed_source != req.expected_head:
            return _failure(fingerprint, "PRECONDITION_FAILED", "source ref precondition failed")
        self.transport.create_branch(req.repository, req.ref, req.expected_head)
        readback = self.transport.read_ref(req.repository, req.ref)
        if readback != req.expected_head:
            return _failure(fingerprint, "READBACK_FAILED", "branch readback failed")
        return BackendResult(
            work_fingerprint=fingerprint,
            succeeded=True,
            outputs=(readback,),
            evidence=(
                "github:create-branch",
                "github:readback-verified",
            ),
            classification="SUCCEEDED",
        )

    def _put_file(self, fingerprint: str, req: GitHubRequest) -> BackendResult:
        if req.path is None or req.content is None or req.message is None or req.expected_head is None:
            return _failure(fingerprint, "INVALID_REQUEST", "put file requires path/content/message/expected_head")
        observed_head = self.transport.read_ref(req.repository, req.ref)
        if observed_head != req.expected_head:
            return _failure(fingerprint, "PRECONDITION_FAILED", "target ref precondition failed")

        existing = self.transport.read_file(req.repository, req.path, req.ref)
        if existing is not None and req.expected_blob_sha is None:
            return _failure(fingerprint, "PRECONDITION_FAILED", "existing file requires expected_blob_sha")
        if req.expected_blob_sha is not None:
            if existing is None or existing.sha != req.expected_blob_sha:
                return _failure(fingerprint, "PRECONDITION_FAILED", "target blob precondition failed")

        commit_sha = self.transport.put_file(
            req.repository,
            req.path,
            req.ref,
            req.content,
            req.message,
            expected_blob_sha=req.expected_blob_sha,
        )
        readback_head = self.transport.read_ref(req.repository, req.ref)
        readback_file = self.transport.read_file(req.repository, req.path, req.ref)
        if readback_head != commit_sha or readback_file is None or readback_file.content != req.content:
            return _failure(fingerprint, "READBACK_FAILED", "file mutation readback failed")
        return BackendResult(
            work_fingerprint=fingerprint,
            succeeded=True,
            outputs=(commit_sha, readback_file.sha),
            evidence=(
                "github:put-file",
                "github:readback-verified",
            ),
            classification="SUCCEEDED",
        )


def _failure(fingerprint: str, classification: str, detail: str) -> BackendResult:
    return BackendResult(
        work_fingerprint=fingerprint,
        succeeded=False,
        outputs=(),
        evidence=(f"github:{classification.lower()}", detail),
        classification=classification,
    )
