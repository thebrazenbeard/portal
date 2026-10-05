import hashlib

from runner.github_backend import (
    GitHubBackend,
    GitHubFileState,
    GitHubOperation,
    TargetAuthorityGrant,
)
from runner.models import ExactSubject
from runner.work_units import WorkUnit, WorkUnitStatus


class FakeGitHubTransport:
    def __init__(self):
        self.refs = {("thebrazenbeard/project-runner", "main"): "a" * 40}
        self.files = {}
        self.mutations = []

    def read_ref(self, repository, ref):
        return self.refs[(repository, ref)]

    def read_file(self, repository, path, ref):
        return self.files.get((repository, path, ref))

    def create_branch(self, repository, branch, sha):
        self.mutations.append(("create_branch", repository, branch, sha))
        self.refs[(repository, branch)] = sha

    def put_file(self, repository, path, branch, content, message, expected_blob_sha=None):
        self.mutations.append(("put_file", repository, path, branch, expected_blob_sha))
        blob_sha = hashlib.sha1(("blob " + str(len(content.encode())) + "\0").encode() + content.encode()).hexdigest()
        self.files[(repository, path, branch)] = GitHubFileState(sha=blob_sha, content=content)
        commit_sha = hashlib.sha1((self.refs[(repository, branch)] + path + content + message).encode()).hexdigest()
        self.refs[(repository, branch)] = commit_sha
        return commit_sha


def _work(github_payload):
    return WorkUnit(
        id="work-1",
        root_frontier_id="frontier-1",
        parent_work_id=None,
        inputs=(ExactSubject(repository="thebrazenbeard/project-runner", ref="main", commit="a" * 40),),
        operation="GITHUB",
        required_capabilities=("write_branch",),
        collision_keys=("repo:project-runner",),
        recursion_depth=0,
        budget_allocation={"active": 1, "backend_jobs": 1},
        expected_outputs=("github-result",),
        completion_criteria=("readback",),
        status=WorkUnitStatus.PENDING,
        payload={"github": github_payload},
    )


def _grant(*ops, refs=("main", "m5/"), paths=()):
    return TargetAuthorityGrant(
        repository="thebrazenbeard/project-runner",
        operations=tuple(ops),
        ref_prefixes=tuple(refs),
        path_prefixes=tuple(paths),
    )


def test_route_capability_without_target_authority_cannot_mutate():
    transport = FakeGitHubTransport()
    backend = GitHubBackend(
        transport=transport,
        route_capabilities={"github.create_branch"},
        grants=(),
    )
    result = backend.execute(_work({
        "operation": "CREATE_BRANCH",
        "repository": "thebrazenbeard/project-runner",
        "ref": "m5/test",
        "source_ref": "main",
        "expected_head": "a" * 40,
    }))

    assert result.succeeded is False
    assert result.classification == "AUTHORITY_DENIED"
    assert transport.mutations == []


def test_target_grant_without_route_capability_cannot_mutate():
    transport = FakeGitHubTransport()
    backend = GitHubBackend(
        transport=transport,
        route_capabilities={"github.read_ref"},
        grants=(_grant(GitHubOperation.CREATE_BRANCH),),
    )
    result = backend.execute(_work({
        "operation": "CREATE_BRANCH",
        "repository": "thebrazenbeard/project-runner",
        "ref": "m5/test",
        "source_ref": "main",
        "expected_head": "a" * 40,
    }))

    assert result.succeeded is False
    assert result.classification == "ROUTE_UNAVAILABLE"
    assert transport.mutations == []


def test_wrong_path_is_denied_even_when_repo_and_operation_are_granted():
    transport = FakeGitHubTransport()
    backend = GitHubBackend(
        transport=transport,
        route_capabilities={"github.put_file"},
        grants=(_grant(GitHubOperation.PUT_FILE, refs=("main",), paths=("docs/allowed/",)),),
    )
    result = backend.execute(_work({
        "operation": "PUT_FILE",
        "repository": "thebrazenbeard/project-runner",
        "ref": "main",
        "path": "secrets/nope.txt",
        "content": "nope",
        "message": "test",
        "expected_head": "a" * 40,
    }))

    assert result.classification == "AUTHORITY_DENIED"
    assert transport.mutations == []


def test_expected_head_mismatch_blocks_branch_creation():
    transport = FakeGitHubTransport()
    backend = GitHubBackend(
        transport=transport,
        route_capabilities={"github.create_branch"},
        grants=(_grant(GitHubOperation.CREATE_BRANCH),),
    )
    result = backend.execute(_work({
        "operation": "CREATE_BRANCH",
        "repository": "thebrazenbeard/project-runner",
        "ref": "m5/test",
        "source_ref": "main",
        "expected_head": "b" * 40,
    }))

    assert result.classification == "PRECONDITION_FAILED"
    assert transport.mutations == []


def test_authorized_create_branch_requires_exact_readback():
    transport = FakeGitHubTransport()
    backend = GitHubBackend(
        transport=transport,
        route_capabilities={"github.create_branch"},
        grants=(_grant(GitHubOperation.CREATE_BRANCH),),
    )
    result = backend.execute(_work({
        "operation": "CREATE_BRANCH",
        "repository": "thebrazenbeard/project-runner",
        "ref": "m5/test",
        "source_ref": "main",
        "expected_head": "a" * 40,
    }))

    assert result.succeeded is True
    assert result.classification == "SUCCEEDED"
    assert transport.refs[("thebrazenbeard/project-runner", "m5/test")] == "a" * 40
    assert "github:readback-verified" in result.evidence


def test_authorized_put_file_reads_back_written_content():
    transport = FakeGitHubTransport()
    backend = GitHubBackend(
        transport=transport,
        route_capabilities={"github.put_file"},
        grants=(_grant(GitHubOperation.PUT_FILE, refs=("main",), paths=("docs/",)),),
    )
    result = backend.execute(_work({
        "operation": "PUT_FILE",
        "repository": "thebrazenbeard/project-runner",
        "ref": "main",
        "path": "docs/m5.txt",
        "content": "hello",
        "message": "m5",
        "expected_head": "a" * 40,
    }))

    assert result.succeeded is True
    assert result.classification == "SUCCEEDED"
    assert transport.files[("thebrazenbeard/project-runner", "docs/m5.txt", "main")].content == "hello"


def test_read_ref_requires_both_route_and_target_read_authority():
    transport = FakeGitHubTransport()
    work = _work({
        "operation": "READ_REF",
        "repository": "thebrazenbeard/project-runner",
        "ref": "main",
        "expected_head": "a" * 40,
    })
    backend = GitHubBackend(
        transport=transport,
        route_capabilities={"github.read_ref"},
        grants=(_grant(GitHubOperation.READ_REF, refs=("main",)),),
    )
    result = backend.execute(work)
    assert result.succeeded is True
    assert result.outputs == ("a" * 40,)


def test_backend_evidence_redacts_target_identity_on_success_and_denial():
    transport = FakeGitHubTransport()
    denied = GitHubBackend(
        transport=transport,
        route_capabilities={"github.create_branch"},
        grants=(),
    ).execute(_work({
        "operation": "CREATE_BRANCH",
        "repository": "thebrazenbeard/project-runner",
        "ref": "m5/test",
        "source_ref": "main",
        "expected_head": "a" * 40,
    }))
    denied_text = " ".join(denied.evidence)
    assert "thebrazenbeard" not in denied_text
    assert "project-runner" not in denied_text
    assert "m5/test" not in denied_text

    allowed = GitHubBackend(
        transport=transport,
        route_capabilities={"github.create_branch"},
        grants=(_grant(GitHubOperation.CREATE_BRANCH),),
    ).execute(_work({
        "operation": "CREATE_BRANCH",
        "repository": "thebrazenbeard/project-runner",
        "ref": "m5/test",
        "source_ref": "main",
        "expected_head": "a" * 40,
    }))
    allowed_text = " ".join(allowed.evidence)
    assert allowed.succeeded
    assert allowed.evidence == (
        "github:create-branch",
        "github:readback-verified",
    )
    assert "thebrazenbeard" not in allowed_text
    assert "project-runner" not in allowed_text
    assert "m5/test" not in allowed_text


def test_put_file_evidence_redacts_repository_path_ref_and_commit():
    transport = FakeGitHubTransport()
    backend = GitHubBackend(
        transport=transport,
        route_capabilities={"github.put_file"},
        grants=(
            _grant(
                GitHubOperation.PUT_FILE,
                refs=("main",),
                paths=("docs/",),
            ),
        ),
    )
    result = backend.execute(_work({
        "operation": "PUT_FILE",
        "repository": "thebrazenbeard/project-runner",
        "ref": "main",
        "path": "docs/private-looking.txt",
        "content": "hello",
        "message": "test",
        "expected_head": "a" * 40,
    }))

    assert result.succeeded
    assert result.evidence == (
        "github:put-file",
        "github:readback-verified",
    )
    evidence_text = " ".join(result.evidence)
    assert "thebrazenbeard" not in evidence_text
    assert "project-runner" not in evidence_text
    assert "private-looking" not in evidence_text
    assert result.outputs[0] not in evidence_text
    assert result.outputs[1] not in evidence_text
