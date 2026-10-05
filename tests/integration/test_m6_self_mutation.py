import hashlib
from pathlib import Path

from runner.dispatch import DispatchAttempt
from runner.github_backend import (
    GitHubBackend,
    GitHubFileState,
    GitHubOperation,
    TargetAuthorityGrant,
)
from runner.leases import Lease
from runner.m6_github import verify_github_mutation_attempt
from runner.models import CostClass, ExactSubject, Frontier, FrontierStatus
from runner.persistent_state import SqliteLeaseStore
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


REPOSITORY = "thebrazenbeard/project-runner"
SOURCE_HEAD = "a" * 40
PROOF_BRANCH = "proof/m6-self-mutation"
ORIGINAL = "# Project Runner\n"
MUTATED = "# Project Runner\n\nM6 self-mutation proof.\n"


def _blob(content: str) -> str:
    payload = content.encode()
    return hashlib.sha1(
        ("blob " + str(len(payload)) + "\0").encode() + payload
    ).hexdigest()


class MutationTransport:
    def __init__(self):
        self.refs = {(REPOSITORY, "main"): SOURCE_HEAD}
        self.files = {
            (REPOSITORY, "README.md", "main"): GitHubFileState(
                sha=_blob(ORIGINAL),
                content=ORIGINAL,
            )
        }

    def read_ref(self, repository, ref):
        return self.refs[(repository, ref)]

    def read_file(self, repository, path, ref):
        return self.files.get((repository, path, ref))

    def create_branch(self, repository, branch, sha):
        self.refs[(repository, branch)] = sha
        for (repo, path, ref), state in list(self.files.items()):
            if repo == repository and ref == "main":
                self.files[(repository, path, branch)] = state

    def put_file(
        self,
        repository,
        path,
        branch,
        content,
        message,
        expected_blob_sha=None,
    ):
        current = self.files.get((repository, path, branch))
        if expected_blob_sha is not None:
            assert current is not None
            assert current.sha == expected_blob_sha
        blob_sha = _blob(content)
        state = GitHubFileState(sha=blob_sha, content=content)
        self.files[(repository, path, branch)] = state
        commit_sha = hashlib.sha1(
            (
                self.refs[(repository, branch)]
                + path
                + content
                + message
            ).encode()
        ).hexdigest()
        self.files[(repository, path, commit_sha)] = state
        self.refs[(repository, branch)] = commit_sha
        return commit_sha


def _grant(operation, ref, *, paths=()):
    return TargetAuthorityGrant(
        repository=REPOSITORY,
        operations=(operation,),
        ref_prefixes=(ref,),
        path_prefixes=tuple(paths),
    )


def _execution_backend(transport):
    return GitHubBackend(
        transport=transport,
        route_capabilities={
            "github.create_branch",
            "github.put_file",
        },
        grants=(
            _grant(GitHubOperation.CREATE_BRANCH, PROOF_BRANCH),
            _grant(
                GitHubOperation.PUT_FILE,
                PROOF_BRANCH,
                paths=("README.md",),
            ),
        ),
    )


def _verification_backend(transport):
    return GitHubBackend(
        transport=transport,
        route_capabilities={
            "github.read_ref",
            "github.read_file",
        },
        grants=(
            _grant(GitHubOperation.READ_REF, PROOF_BRANCH),
            _grant(
                GitHubOperation.READ_FILE,
                PROOF_BRANCH,
                paths=("README.md",),
            ),
        ),
    )


def _frontier(subject):
    return Frontier(
        id="m6-self-mutation-proof",
        project="project-runner",
        subject=subject,
        work_type="IMPLEMENT",
        reason="bounded self-mutation proof",
        dependencies=(),
        required_capabilities=("write_branch",),
        collision_keys=("repo:project-runner",),
        cost_class=CostClass.TRIVIAL,
        priority_inputs={
            "criticality": 1,
            "dependency_depth": 0,
            "staleness": 0,
            "uncertainty": 0,
            "cost": 1,
        },
        status=FrontierStatus.READY,
    )


def _work(subject, request):
    return WorkUnit(
        id="m6-self-mutation",
        root_frontier_id="m6-self-mutation-proof",
        parent_work_id=None,
        inputs=(subject,),
        operation="GITHUB",
        required_capabilities=("write_branch",),
        collision_keys=("repo:project-runner",),
        recursion_depth=0,
        budget_allocation={
            "children": 0,
            "active": 1,
            "retries": 0,
            "backend_jobs": 1,
        },
        expected_outputs=("github-result",),
        completion_criteria=(
            "exact-precondition",
            "independent-postcondition-readback",
            "current-fencing-token",
        ),
        status=WorkUnitStatus.PENDING,
        payload={"github": request},
    )


def _attempt(work, result, lease):
    return DispatchAttempt(
        frontier=_frontier(work.inputs[0]),
        work=work,
        lease=lease,
        result=result,
    )


def test_m6_self_mutation_branch_and_file_write_are_independently_verified(
    tmp_path: Path,
):
    transport = MutationTransport()
    executor = _execution_backend(transport)
    verifier = _verification_backend(transport)
    lease_store = SqliteLeaseStore(tmp_path / "proof.sqlite3")

    source = ExactSubject(
        repository=REPOSITORY,
        ref="main",
        commit=SOURCE_HEAD,
    )
    branch_work = _work(
        source,
        {
            "operation": "CREATE_BRANCH",
            "repository": REPOSITORY,
            "ref": PROOF_BRANCH,
            "source_ref": "main",
            "expected_head": SOURCE_HEAD,
        },
    )
    branch_lease = lease_store.claim(
        work_unit_fingerprint(branch_work),
        holder="m6-self-proof-branch",
        now=0.0,
        ttl=60.0,
    )
    assert branch_lease is not None
    assert branch_lease.fencing_token == 1
    branch_result = executor.execute(branch_work)
    assert branch_result.succeeded
    branch_outcome = verify_github_mutation_attempt(
        _attempt(branch_work, branch_result, branch_lease),
        lease_store=lease_store,
        now=1.0,
        verification_backend=verifier,
    )
    assert branch_outcome.status is WorkUnitStatus.COMPLETE
    assert transport.refs[(REPOSITORY, PROOF_BRANCH)] == SOURCE_HEAD

    target_before = ExactSubject(
        repository=REPOSITORY,
        ref=PROOF_BRANCH,
        commit=SOURCE_HEAD,
    )
    put_work = _work(
        target_before,
        {
            "operation": "PUT_FILE",
            "repository": REPOSITORY,
            "ref": PROOF_BRANCH,
            "path": "README.md",
            "content": MUTATED,
            "message": "test: M6 self-mutation",
            "expected_head": SOURCE_HEAD,
            "expected_blob_sha": _blob(ORIGINAL),
        },
    )
    put_lease = lease_store.claim(
        work_unit_fingerprint(put_work),
        holder="m6-self-proof-put",
        now=2.0,
        ttl=60.0,
    )
    assert put_lease is not None
    assert put_lease.fencing_token == 1

    put_result = executor.execute(put_work)
    assert put_result.succeeded
    assert len(put_result.outputs) == 2
    new_commit, new_blob = put_result.outputs
    assert new_commit != SOURCE_HEAD
    assert new_blob == _blob(MUTATED)

    put_outcome = verify_github_mutation_attempt(
        _attempt(put_work, put_result, put_lease),
        lease_store=lease_store,
        now=3.0,
        verification_backend=verifier,
    )
    assert put_outcome.status is WorkUnitStatus.COMPLETE
    assert put_outcome.reason == "mutation postcondition independently verified"
    assert transport.refs[(REPOSITORY, PROOF_BRANCH)] == new_commit
    assert transport.files[(REPOSITORY, "README.md", PROOF_BRANCH)] == (
        GitHubFileState(sha=new_blob, content=MUTATED)
    )


class MoveAfterVerificationHeadReadTransport(MutationTransport):
    def __init__(self):
        super().__init__()
        self.move_after_next_proof_ref_read = False

    def read_ref(self, repository, ref):
        observed = super().read_ref(repository, ref)
        if self.move_after_next_proof_ref_read and ref == PROOF_BRANCH:
            self.move_after_next_proof_ref_read = False
            self.refs[(repository, ref)] = "f" * 40
        return observed


def test_m6_put_file_is_superseded_if_branch_moves_between_postcondition_reads(
    tmp_path: Path,
):
    transport = MoveAfterVerificationHeadReadTransport()
    executor = _execution_backend(transport)
    verifier = _verification_backend(transport)
    lease_store = SqliteLeaseStore(tmp_path / "proof.sqlite3")
    transport.create_branch(REPOSITORY, PROOF_BRANCH, SOURCE_HEAD)

    target_before = ExactSubject(
        repository=REPOSITORY,
        ref=PROOF_BRANCH,
        commit=SOURCE_HEAD,
    )
    work = _work(
        target_before,
        {
            "operation": "PUT_FILE",
            "repository": REPOSITORY,
            "ref": PROOF_BRANCH,
            "path": "README.md",
            "content": MUTATED,
            "message": "test: M6 self-mutation",
            "expected_head": SOURCE_HEAD,
            "expected_blob_sha": _blob(ORIGINAL),
        },
    )
    lease = lease_store.claim(
        work_unit_fingerprint(work),
        holder="m6-self-proof-postread-race",
        now=0.0,
        ttl=60.0,
    )
    assert lease is not None

    result = executor.execute(work)
    assert result.succeeded
    transport.move_after_next_proof_ref_read = True

    outcome = verify_github_mutation_attempt(
        _attempt(work, result, lease),
        lease_store=lease_store,
        now=1.0,
        verification_backend=verifier,
    )

    assert outcome.status is WorkUnitStatus.SUPERSEDED
    assert outcome.reason == "mutated branch moved during independent verification"


class FailFinalVerificationRefReadTransport(MutationTransport):
    def __init__(self):
        super().__init__()
        self.fail_verification_reads = False
        self.verification_ref_reads = 0

    def read_ref(self, repository, ref):
        if self.fail_verification_reads and ref == PROOF_BRANCH:
            self.verification_ref_reads += 1
            if self.verification_ref_reads == 3:
                raise RuntimeError("simulated final ref read failure")
        return super().read_ref(repository, ref)


def test_m6_put_file_final_ref_read_failure_stays_outcome_unknown(tmp_path: Path):
    transport = FailFinalVerificationRefReadTransport()
    executor = _execution_backend(transport)
    verifier = _verification_backend(transport)
    lease_store = SqliteLeaseStore(tmp_path / "proof.sqlite3")
    transport.create_branch(REPOSITORY, PROOF_BRANCH, SOURCE_HEAD)

    target_before = ExactSubject(
        repository=REPOSITORY,
        ref=PROOF_BRANCH,
        commit=SOURCE_HEAD,
    )
    work = _work(
        target_before,
        {
            "operation": "PUT_FILE",
            "repository": REPOSITORY,
            "ref": PROOF_BRANCH,
            "path": "README.md",
            "content": MUTATED,
            "message": "test: M6 self-mutation",
            "expected_head": SOURCE_HEAD,
            "expected_blob_sha": _blob(ORIGINAL),
        },
    )
    lease = lease_store.claim(
        work_unit_fingerprint(work),
        holder="m6-self-proof-final-ref-unavailable",
        now=0.0,
        ttl=60.0,
    )
    assert lease is not None

    result = executor.execute(work)
    assert result.succeeded
    transport.fail_verification_reads = True

    outcome = verify_github_mutation_attempt(
        _attempt(work, result, lease),
        lease_store=lease_store,
        now=1.0,
        verification_backend=verifier,
    )

    assert outcome.status is WorkUnitStatus.OUTCOME_UNKNOWN
    assert outcome.reason == "final independent branch readback is unavailable"


def test_m6_self_mutation_is_superseded_if_branch_moves_after_write(tmp_path: Path):
    transport = MutationTransport()
    executor = _execution_backend(transport)
    verifier = _verification_backend(transport)
    lease_store = SqliteLeaseStore(tmp_path / "proof.sqlite3")
    transport.create_branch(REPOSITORY, PROOF_BRANCH, SOURCE_HEAD)

    target_before = ExactSubject(
        repository=REPOSITORY,
        ref=PROOF_BRANCH,
        commit=SOURCE_HEAD,
    )
    work = _work(
        target_before,
        {
            "operation": "PUT_FILE",
            "repository": REPOSITORY,
            "ref": PROOF_BRANCH,
            "path": "README.md",
            "content": MUTATED,
            "message": "test: M6 self-mutation",
            "expected_head": SOURCE_HEAD,
            "expected_blob_sha": _blob(ORIGINAL),
        },
    )
    lease = lease_store.claim(
        work_unit_fingerprint(work),
        holder="m6-self-proof-stale",
        now=0.0,
        ttl=60.0,
    )
    assert lease is not None
    result = executor.execute(work)
    assert result.succeeded

    transport.refs[(REPOSITORY, PROOF_BRANCH)] = "f" * 40
    outcome = verify_github_mutation_attempt(
        _attempt(work, result, lease),
        lease_store=lease_store,
        now=1.0,
        verification_backend=verifier,
    )
    assert outcome.status is WorkUnitStatus.SUPERSEDED
    assert outcome.reason == "mutated branch moved after execution"


def test_mutation_transport_failure_stays_outcome_unknown(tmp_path: Path):
    transport = MutationTransport()
    lease_store = SqliteLeaseStore(tmp_path / "proof.sqlite3")
    transport.create_branch(REPOSITORY, PROOF_BRANCH, SOURCE_HEAD)
    subject = ExactSubject(
        repository=REPOSITORY,
        ref=PROOF_BRANCH,
        commit=SOURCE_HEAD,
    )
    work = _work(
        subject,
        {
            "operation": "PUT_FILE",
            "repository": REPOSITORY,
            "ref": PROOF_BRANCH,
            "path": "README.md",
            "content": MUTATED,
            "message": "test",
            "expected_head": SOURCE_HEAD,
            "expected_blob_sha": _blob(ORIGINAL),
        },
    )
    lease = lease_store.claim(
        work_unit_fingerprint(work),
        holder="m6-self-proof-unknown",
        now=0.0,
        ttl=60.0,
    )
    assert lease is not None
    from runner.backends import BackendResult

    result = BackendResult(
        work_fingerprint=work_unit_fingerprint(work),
        succeeded=False,
        outputs=(),
        evidence=("github:transport_failed",),
        classification="TRANSPORT_FAILED",
    )
    outcome = verify_github_mutation_attempt(
        _attempt(work, result, lease),
        lease_store=lease_store,
        now=1.0,
        verification_backend=_verification_backend(transport),
    )
    assert outcome.status is WorkUnitStatus.OUTCOME_UNKNOWN
    assert outcome.reason == "mutation outcome requires reconciliation"


def test_mutation_precondition_drift_is_superseded(tmp_path: Path):
    transport = MutationTransport()
    lease_store = SqliteLeaseStore(tmp_path / "proof.sqlite3")
    transport.create_branch(REPOSITORY, PROOF_BRANCH, SOURCE_HEAD)
    subject = ExactSubject(
        repository=REPOSITORY,
        ref=PROOF_BRANCH,
        commit=SOURCE_HEAD,
    )
    work = _work(
        subject,
        {
            "operation": "PUT_FILE",
            "repository": REPOSITORY,
            "ref": PROOF_BRANCH,
            "path": "README.md",
            "content": MUTATED,
            "message": "test",
            "expected_head": SOURCE_HEAD,
            "expected_blob_sha": _blob(ORIGINAL),
        },
    )
    lease = lease_store.claim(
        work_unit_fingerprint(work),
        holder="m6-self-proof-precondition",
        now=0.0,
        ttl=60.0,
    )
    assert lease is not None

    from runner.backends import BackendResult

    result = BackendResult(
        work_fingerprint=work_unit_fingerprint(work),
        succeeded=False,
        outputs=(),
        evidence=("github:precondition_failed",),
        classification="PRECONDITION_FAILED",
    )
    outcome = verify_github_mutation_attempt(
        _attempt(work, result, lease),
        lease_store=lease_store,
        now=1.0,
        verification_backend=_verification_backend(transport),
    )

    assert outcome.status is WorkUnitStatus.SUPERSEDED
    assert outcome.reason == "mutation precondition moved before execution"


class RecordVerificationFileRefTransport(MutationTransport):
    def __init__(self):
        super().__init__()
        self.record_verification_reads = False
        self.verification_file_refs = []

    def read_file(self, repository, path, ref):
        if self.record_verification_reads and path == "README.md":
            self.verification_file_refs.append(ref)
        return super().read_file(repository, path, ref)


def test_m6_put_file_independent_file_proof_reads_exact_returned_commit(tmp_path: Path):
    transport = RecordVerificationFileRefTransport()
    executor = _execution_backend(transport)
    verifier = _verification_backend(transport)
    lease_store = SqliteLeaseStore(tmp_path / "proof.sqlite3")
    transport.create_branch(REPOSITORY, PROOF_BRANCH, SOURCE_HEAD)
    target_before = ExactSubject(repository=REPOSITORY, ref=PROOF_BRANCH, commit=SOURCE_HEAD)
    work = _work(target_before, {
        "operation": "PUT_FILE", "repository": REPOSITORY, "ref": PROOF_BRANCH,
        "path": "README.md", "content": MUTATED, "message": "test: immutable proof",
        "expected_head": SOURCE_HEAD, "expected_blob_sha": _blob(ORIGINAL),
    })
    lease = lease_store.claim(work_unit_fingerprint(work), holder="m6-exact-commit-proof", now=0.0, ttl=60.0)
    assert lease is not None
    result = executor.execute(work)
    assert result.succeeded
    new_commit, _new_blob = result.outputs
    transport.record_verification_reads = True
    outcome = verify_github_mutation_attempt(
        _attempt(work, result, lease), lease_store=lease_store, now=1.0,
        verification_backend=verifier,
    )
    assert outcome.status is WorkUnitStatus.COMPLETE
    assert transport.verification_file_refs == [new_commit]
