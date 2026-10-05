import pytest

from runner.github_backend import (
    GitHubFileState,
    GitHubOutcomeUnknown,
    GitHubPreconditionFailed,
    GitHubRestTransport,
)


class ScriptedExactTransport(GitHubRestTransport):
    def __init__(self):
        super().__init__(
            token="test-token",
            api_base="https://api.github.test",
            graphql_url="https://api.github.test/graphql",
        )
        self.repository = "owner/repo"
        self.branch = "main"
        self.expected_head = "a" * 40
        self.refs = {(self.repository, self.branch): self.expected_head}
        self.old_blob = "b" * 40
        self.old_content = "before\n"
        self.new_blob = "c" * 40
        self.new_commit = "d" * 40
        self.new_content = None
        self.graphql_fail = False
        self.graphql_transport_error = False
        self.graphql_generic_error = False
        self.fail_readback = False
        self.requests = []

    def read_ref(self, repository, ref):
        if (
            self.fail_readback
            and self.refs[(repository, ref)] == self.new_commit
        ):
            raise RuntimeError("simulated readback uncertainty")
        return self.refs[(repository, ref)]

    def read_file(self, repository, path, ref):
        if ref == self.branch and self.refs[(repository, self.branch)] == self.new_commit:
            return GitHubFileState(
                sha=self.new_blob,
                content=self.new_content,
            )
        if ref in {self.branch, self.expected_head}:
            return GitHubFileState(
                sha=self.old_blob,
                content=self.old_content,
            )
        return None

    def _request(self, method, url, body=None):
        self.requests.append((method, url, body))
        if method == "GET" and url.endswith("/repos/owner/repo"):
            return {"node_id": "R_test"}
        if method == "GET" and "/git/commits/" in url:
            return {"tree": {"sha": "tree-root"}}
        if method == "GET" and url.endswith("/git/trees/tree-root"):
            return {
                "tree": [
                    {
                        "path": "docs",
                        "type": "tree",
                        "mode": "040000",
                        "sha": "tree-docs",
                    }
                ]
            }
        if method == "GET" and url.endswith("/git/trees/tree-docs"):
            return {
                "tree": [
                    {
                        "path": "file.txt",
                        "type": "blob",
                        "mode": "100644",
                        "sha": self.old_blob,
                    }
                ]
            }
        if method == "POST" and url.endswith("/git/blobs"):
            self.new_content = body["content"]
            return {"sha": self.new_blob}
        if method == "POST" and url.endswith("/git/trees"):
            assert body["base_tree"] == "tree-root"
            assert body["tree"] == [
                {
                    "path": "docs/file.txt",
                    "mode": "100644",
                    "type": "blob",
                    "sha": self.new_blob,
                }
            ]
            return {"sha": "tree-new"}
        if method == "POST" and url.endswith("/git/commits"):
            assert body["tree"] == "tree-new"
            assert body["parents"] == [self.expected_head]
            return {"sha": self.new_commit}
        if method == "POST" and url == self.graphql_url:
            if self.graphql_transport_error:
                raise RuntimeError("simulated transport uncertainty")
            update = body["variables"]["input"]["refUpdates"][0]
            assert update == {
                "name": "refs/heads/main",
                "beforeOid": self.expected_head,
                "afterOid": self.new_commit,
                "force": False,
            }
            if self.graphql_fail:
                return {
                    "data": {"updateRefs": None},
                    "errors": [{"message": "beforeOid mismatch"}],
                }
            if self.graphql_generic_error:
                return {
                    "data": {"updateRefs": None},
                    "errors": [{"message": "internal mutation error"}],
                }
            self.refs[(self.repository, self.branch)] = self.new_commit
            return {
                "data": {
                    "updateRefs": {
                        "clientMutationId": None,
                    }
                }
            }
        raise AssertionError((method, url, body))


def test_exact_source_write_uses_before_oid_and_reads_back():
    transport = ScriptedExactTransport()

    commit_sha, blob_sha = transport.put_file_exact_head(
        transport.repository,
        "docs/file.txt",
        transport.branch,
        "after\n",
        "Exact write",
        expected_head=transport.expected_head,
        expected_blob_sha=transport.old_blob,
    )

    assert commit_sha == transport.new_commit
    assert blob_sha == transport.new_blob
    assert transport.refs[
        (transport.repository, transport.branch)
    ] == transport.new_commit
    graphql = [
        body
        for method, url, body in transport.requests
        if method == "POST" and url == transport.graphql_url
    ]
    assert len(graphql) == 1


def test_exact_source_write_refuses_graphql_before_oid_mismatch():
    transport = ScriptedExactTransport()
    transport.graphql_fail = True

    with pytest.raises(
        GitHubPreconditionFailed,
        match="exact ref compare-and-swap rejected",
    ):
        transport.put_file_exact_head(
            transport.repository,
            "docs/file.txt",
            transport.branch,
            "after\n",
            "Exact write",
            expected_head=transport.expected_head,
            expected_blob_sha=transport.old_blob,
        )

    assert transport.refs[
        (transport.repository, transport.branch)
    ] == transport.expected_head



def test_exact_source_write_unclassified_graphql_error_is_outcome_unknown():
    transport = ScriptedExactTransport()
    transport.graphql_generic_error = True

    with pytest.raises(
        GitHubOutcomeUnknown,
        match="unclassified error",
    ):
        transport.put_file_exact_head(
            transport.repository,
            "docs/file.txt",
            transport.branch,
            "after\n",
            "Exact write",
            expected_head=transport.expected_head,
            expected_blob_sha=transport.old_blob,
        )


def test_exact_source_write_uncertain_ref_update_is_outcome_unknown():
    transport = ScriptedExactTransport()
    transport.graphql_transport_error = True

    with pytest.raises(
        GitHubOutcomeUnknown,
        match="outcome is unknown",
    ):
        transport.put_file_exact_head(
            transport.repository,
            "docs/file.txt",
            transport.branch,
            "after\n",
            "Exact write",
            expected_head=transport.expected_head,
            expected_blob_sha=transport.old_blob,
        )


def test_exact_source_write_readback_failure_after_publication_is_unknown():
    transport = ScriptedExactTransport()

    original = transport._request

    def request_and_arm_readback(method, url, body=None):
        result = original(method, url, body)
        if method == "POST" and url == transport.graphql_url:
            transport.fail_readback = True
        return result

    transport._request = request_and_arm_readback

    with pytest.raises(
        GitHubOutcomeUnknown,
        match="readback outcome is unknown",
    ):
        transport.put_file_exact_head(
            transport.repository,
            "docs/file.txt",
            transport.branch,
            "after\n",
            "Exact write",
            expected_head=transport.expected_head,
            expected_blob_sha=transport.old_blob,
        )

def test_exact_source_write_rejects_blob_mismatch_before_object_creation():
    transport = ScriptedExactTransport()

    with pytest.raises(
        GitHubPreconditionFailed,
        match="exact blob precondition failed",
    ):
        transport.put_file_exact_head(
            transport.repository,
            "docs/file.txt",
            transport.branch,
            "after\n",
            "Exact write",
            expected_head=transport.expected_head,
            expected_blob_sha="e" * 40,
        )

    assert not any(
        method == "POST" and url.endswith("/git/blobs")
        for method, url, _body in transport.requests
    )


def test_exact_source_write_rejects_nonregular_existing_file():
    transport = ScriptedExactTransport()
    original = transport._request

    def request_with_executable(method, url, body=None):
        if method == "GET" and url.endswith("/git/trees/tree-docs"):
            transport.requests.append((method, url, body))
            return {
                "tree": [
                    {
                        "path": "file.txt",
                        "type": "blob",
                        "mode": "100755",
                        "sha": transport.old_blob,
                    }
                ]
            }
        return original(method, url, body)

    transport._request = request_with_executable

    with pytest.raises(
        GitHubPreconditionFailed,
        match="regular files only",
    ):
        transport.put_file_exact_head(
            transport.repository,
            "docs/file.txt",
            transport.branch,
            "after\n",
            "Exact write",
            expected_head=transport.expected_head,
            expected_blob_sha=transport.old_blob,
        )
