from __future__ import annotations

from dataclasses import replace

from runner.execution_promotion import (
    ExecutionPromotionReceipt,
    PromotedExecution,
    SOURCE_WRITE,
)
from runner.github_backend import GitHubOutcomeUnknown, GitHubPreconditionFailed
from runner.promoted_github_tree import (
    PromotedGitHubSourceTreeWriteBackend,
    source_tree_write_request_sha256,
)


def _promotion(request_sha: str) -> ExecutionPromotionReceipt:
    return ExecutionPromotionReceipt(
        lineage_id="lineage",
        work_fingerprint="a" * 64,
        fencing_token=1,
        holder="holder",
        repository="example/repo",
        ref="main",
        exact_head="b" * 40,
        operation="EXECUTE_FRONTIER",
        effect_class=SOURCE_WRITE,
        review_sha256="c" * 64,
        review_valid_until=1000.0,
        execution_grant_sha256="d" * 64,
        execution_valid_until=1000.0,
        execution_request_sha256=request_sha,
        effect_grant_sha256="e" * 64,
        effect_valid_until=1000.0,
        promoted_at=1.0,
        attempt_work_generation=1,
        promoted_work_generation=2,
        promotion_sha256="f" * 64,
    )


def _request():
    return {
        "schema": "PROJECT_RUNNER_GITHUB_SOURCE_TREE_WRITE_V1",
        "operation": "PUT_FILES",
        "repository": "example/repo",
        "ref": "main",
        "expected_head": "b" * 40,
        "message": "Apply bounded source proposal",
        "files": [
            {
                "path": "src/a.py",
                "content": "A = 1\n",
                "expected_blob_sha": None,
            },
            {
                "path": "tests/test_a.py",
                "content": "def test_a(): assert True\n",
                "expected_blob_sha": None,
            },
        ],
    }


class FakeTreeTransport:
    def __init__(self):
        self.calls = []
        self.fail = None

    def put_files_exact_head(
        self,
        repository,
        files,
        branch,
        message,
        *,
        expected_head,
    ):
        self.calls.append(
            (repository, tuple(files), branch, message, expected_head)
        )
        if self.fail == "precondition":
            raise GitHubPreconditionFailed("stale")
        if self.fail == "unknown":
            raise GitHubOutcomeUnknown(
                "unknown",
                candidate_commit_sha="1" * 40,
                candidate_blob_sha="2" * 40,
            )
        return "1" * 40, {
            "src/a.py": "2" * 40,
            "tests/test_a.py": "3" * 40,
        }


def _execution(request, transport):
    digest = source_tree_write_request_sha256(request)
    return PromotedExecution(
        work=None,  # backend does not inspect WorkUnit internals
        operation="EXECUTE_FRONTIER",
        effect_class=SOURCE_WRITE,
        execution_request=request,
        promotion=_promotion(digest),
    )


def test_promoted_source_tree_write_executes_one_atomic_multi_file_effect():
    request = _request()
    transport = FakeTreeTransport()
    backend = PromotedGitHubSourceTreeWriteBackend(transport=transport)

    result = backend.execute_promoted(_execution(request, transport))

    assert result.succeeded is True
    assert result.classification == "SUCCEEDED"
    assert result.outputs[0] == "1" * 40
    assert len(result.outputs) == 3
    assert transport.calls
    repository, files, branch, message, expected_head = transport.calls[0]
    assert repository == "example/repo"
    assert branch == "main"
    assert expected_head == "b" * 40
    assert [item["path"] for item in files] == [
        "src/a.py",
        "tests/test_a.py",
    ]


def test_source_tree_request_rejects_duplicate_paths_without_mutation():
    request = _request()
    request["files"].append(dict(request["files"][0]))
    transport = FakeTreeTransport()
    backend = PromotedGitHubSourceTreeWriteBackend(transport=transport)

    result = backend.execute_promoted(_execution(request, transport))

    assert result.succeeded is False
    assert result.classification == "INVALID_REQUEST"
    assert transport.calls == []


def test_source_tree_request_rejects_noncanonical_path_without_mutation():
    request = _request()
    request["files"][0]["path"] = "../escape.py"
    transport = FakeTreeTransport()
    backend = PromotedGitHubSourceTreeWriteBackend(transport=transport)

    result = backend.execute_promoted(_execution(request, transport))

    assert result.succeeded is False
    assert result.classification == "INVALID_REQUEST"
    assert transport.calls == []


def test_source_tree_write_preserves_precondition_failure_class():
    request = _request()
    transport = FakeTreeTransport()
    transport.fail = "precondition"
    backend = PromotedGitHubSourceTreeWriteBackend(transport=transport)

    result = backend.execute_promoted(_execution(request, transport))

    assert result.succeeded is False
    assert result.classification == "PRECONDITION_FAILED"


def test_source_tree_write_preserves_outcome_unknown_without_retry():
    request = _request()
    transport = FakeTreeTransport()
    transport.fail = "unknown"
    backend = PromotedGitHubSourceTreeWriteBackend(transport=transport)

    result = backend.execute_promoted(_execution(request, transport))

    assert result.succeeded is False
    assert result.classification == "OUTCOME_UNKNOWN"
    assert len(transport.calls) == 1
    assert result.outputs[0] == "1" * 40
