from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Mapping, Sequence

from .backends import BackendResult
from .execution_promotion import PromotedExecution, SOURCE_WRITE
from .github_backend import (
    GitHubOutcomeUnknown,
    GitHubPreconditionFailed,
    GitHubTransport,
)


_SOURCE_TREE_WRITE_SCHEMA = "PROJECT_RUNNER_GITHUB_SOURCE_TREE_WRITE_V1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class GitHubSourceTreeFile:
    path: str
    content: str
    expected_blob_sha: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "GitHubSourceTreeFile":
        path_raw = value.get("path")
        if not isinstance(path_raw, str) or not path_raw:
            raise ValueError("promoted source-tree file requires path")
        if "\\" in path_raw:
            raise ValueError("promoted source-tree path must use forward slashes")
        pure = PurePosixPath(path_raw)
        if (
            pure.is_absolute()
            or path_raw.endswith("/")
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValueError("promoted source-tree path must already be canonical")
        path = pure.as_posix()

        content = value.get("content")
        if not isinstance(content, str):
            raise ValueError("promoted source-tree file requires string content")

        expected = value.get("expected_blob_sha")
        expected_blob_sha: str | None
        if expected is None:
            expected_blob_sha = None
        else:
            expected_blob_sha = str(expected)
            if _SHA40.fullmatch(expected_blob_sha) is None:
                raise ValueError(
                    "promoted source-tree expected blob must be lowercase sha40"
                )

        return cls(
            path=path,
            content=content,
            expected_blob_sha=expected_blob_sha,
        )

    def transport_payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "content": self.content,
            "expected_blob_sha": self.expected_blob_sha,
        }


@dataclass(frozen=True)
class GitHubSourceTreeWriteRequest:
    repository: str
    ref: str
    expected_head: str
    message: str
    files: tuple[GitHubSourceTreeFile, ...]

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> "GitHubSourceTreeWriteRequest":
        if value.get("schema") != _SOURCE_TREE_WRITE_SCHEMA:
            raise ValueError("unexpected promoted GitHub source-tree schema")
        if value.get("operation") != "PUT_FILES":
            raise ValueError("promoted source-tree write must use PUT_FILES")

        repository = value.get("repository")
        ref = value.get("ref")
        expected_head = value.get("expected_head")
        message = value.get("message")
        if (
            not isinstance(repository, str)
            or "/" not in repository
            or any(ch.isspace() for ch in repository)
        ):
            raise ValueError("promoted source-tree write requires owner/repository")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("promoted source-tree write requires ref")
        if not isinstance(expected_head, str) or _SHA40.fullmatch(expected_head) is None:
            raise ValueError("promoted source-tree write requires exact head")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("promoted source-tree write requires commit message")

        raw_files = value.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError("promoted source-tree write requires files")
        files: list[GitHubSourceTreeFile] = []
        seen: set[str] = set()
        for raw in raw_files:
            if not isinstance(raw, Mapping):
                raise ValueError("promoted source-tree file must be an object")
            file = GitHubSourceTreeFile.from_mapping(raw)
            if file.path in seen:
                raise ValueError("promoted source-tree write contains duplicate paths")
            seen.add(file.path)
            files.append(file)

        return cls(
            repository=repository,
            ref=ref.strip(),
            expected_head=expected_head,
            message=message.strip(),
            files=tuple(files),
        )


def source_tree_write_request_sha256(value: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class PromotedGitHubSourceTreeWriteBackend:
    """Publish one exact-head multi-file tree as one atomic Git commit."""

    def __init__(self, *, transport: GitHubTransport) -> None:
        self.transport = transport

    @staticmethod
    def _failure(
        fingerprint: str,
        classification: str,
        detail: str,
        outputs: Sequence[str] = (),
    ) -> BackendResult:
        return BackendResult(
            work_fingerprint=fingerprint,
            succeeded=False,
            outputs=tuple(outputs),
            evidence=(
                f"github:{classification.lower()}",
                detail,
            ),
            classification=classification,
        )

    def execute_promoted(self, execution: PromotedExecution) -> BackendResult:
        fingerprint = execution.promotion.work_fingerprint
        if execution.effect_class != SOURCE_WRITE:
            return self._failure(
                fingerprint,
                "EFFECT_CLASS_DENIED",
                "promoted GitHub source-tree adapter requires SOURCE_WRITE",
            )
        if execution.execution_request is None:
            return self._failure(
                fingerprint,
                "REQUEST_MISSING",
                "promoted source-tree write has no durable execution request",
            )

        try:
            request = GitHubSourceTreeWriteRequest.from_mapping(
                execution.execution_request
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._failure(
                fingerprint,
                "INVALID_REQUEST",
                str(exc),
            )

        request_sha256 = source_tree_write_request_sha256(
            execution.execution_request
        )
        if execution.promotion.execution_request_sha256 != request_sha256:
            return self._failure(
                fingerprint,
                "REQUEST_BINDING_MISMATCH",
                "promoted source-tree request does not match durable promotion",
            )

        promotion = execution.promotion
        if (
            request.repository != promotion.repository
            or request.ref != promotion.ref
            or request.expected_head != promotion.exact_head
        ):
            return self._failure(
                fingerprint,
                "PROMOTION_BINDING_MISMATCH",
                "source-tree request target diverges from promotion",
            )

        try:
            commit_sha, blob_shas = self.transport.put_files_exact_head(
                request.repository,
                tuple(file.transport_payload() for file in request.files),
                request.ref,
                request.message,
                expected_head=request.expected_head,
            )
        except GitHubPreconditionFailed:
            return self._failure(
                fingerprint,
                "PRECONDITION_FAILED",
                "exact GitHub source-tree compare-and-swap failed",
            )
        except GitHubOutcomeUnknown as exc:
            outputs: tuple[str, ...] = ()
            if exc.candidate_commit_sha is not None:
                values = [exc.candidate_commit_sha]
                if exc.candidate_blob_shas is not None:
                    values.extend(
                        exc.candidate_blob_shas[file.path]
                        for file in request.files
                        if file.path in exc.candidate_blob_shas
                    )
                outputs = tuple(values)
            return self._failure(
                fingerprint,
                "OUTCOME_UNKNOWN",
                "exact GitHub source-tree publication outcome is unknown",
                outputs,
            )
        except (KeyError, RuntimeError, TypeError, ValueError):
            return self._failure(
                fingerprint,
                "TRANSPORT_FAILED",
                "GitHub source-tree transport failed before verified completion",
            )

        expected_paths = tuple(file.path for file in request.files)
        if set(blob_shas) != set(expected_paths):
            return self._failure(
                fingerprint,
                "READBACK_FAILED",
                "source-tree transport returned incomplete blob bindings",
            )

        return BackendResult(
            work_fingerprint=fingerprint,
            succeeded=True,
            outputs=(
                commit_sha,
                *(blob_shas[path] for path in expected_paths),
            ),
            evidence=(
                "github:promoted-source-tree-write",
                "github:exact-cas",
                "github:readback-verified",
            ),
            classification="SUCCEEDED",
        )
