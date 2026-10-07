from __future__ import annotations

import pytest

from runner.github_backend import (
    GitHubFileState,
    GitHubOutcomeUnknown,
    GitHubPreconditionFailed,
    GitHubRestTransport,
)


class ScriptedTreeTransport(GitHubRestTransport):
    def __init__(self):
        super().__init__(
            token="test-token",
            api_base="https://api.github.test",
            graphql_url="https://api.github.test/graphql",
        )
        self.repository = "owner/repo"
        self.branch = "main"
        self.expected_head = "a" * 40
        self.new_commit = "d" * 40
        self.old_blob = "b" * 40
        self.new_blobs = iter(("c" * 40, "e" * 40))
        self.refs = {(self.repository, self.branch): self.expected_head}
        self.contents = {}
        self.requests = []
        self.fail_before_oid = False
        self.fail_readback = False

    def read_ref(self, repository, ref):
        if self.fail_readback and self.refs[(repository, ref)] == self.new_commit:
            raise RuntimeError("readback uncertain")
        return self.refs[(repository, ref)]

    def read_file(self, repository, path, ref):
        if ref == self.new_commit:
            return self.contents.get(path)
        if path == "src/existing.py":
            return GitHubFileState(
                sha=self.old_blob,
                content="before\n",
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
                        "path": "src",
                        "type": "tree",
                        "mode": "040000",
                        "sha": "tree-src",
                    }
                ]
            }
        if method == "GET" and url.endswith("/git/trees/tree-src"):
            return {
                "tree": [
                    {
                        "path": "existing.py",
                        "type": "blob",
                        "mode": "100644",
                        "sha": self.old_blob,
                    }
                ]
            }
        if method == "POST" and url.endswith("/git/blobs"):
            sha = next(self.new_blobs)
            return {"sha": sha}
        if method == "POST" and url.endswith("/git/trees"):
            assert body["base_tree"] == "tree-root"
            assert [item["path"] for item in body["tree"]] == [
                "src/existing.py",
                "tests/test_new.py",
            ]
            self.contents["src/existing.py"] = GitHubFileState(
                sha="c" * 40,
                content="after\n",
            )
            self.contents["tests/test_new.py"] = GitHubFileState(
                sha="e" * 40,
                content="def test_new(): assert True\n",
            )
            return {"sha": "tree-new"}
        if method == "POST" and url.endswith("/git/commits"):
            assert body["tree"] == "tree-new"
            assert body["parents"] == [self.expected_head]
            return {"sha": self.new_commit}
        if method == "POST" and url == self.graphql_url:
            update = body["variables"]["input"]["refUpdates"][0]
            assert update["beforeOid"] == self.expected_head
            assert update["afterOid"] == self.new_commit
            assert update["force"] is False
            if self.fail_before_oid:
                return {
                    "data": {"updateRefs": None},
                    "errors": [{"message": "beforeOid mismatch"}],
                }
            self.refs[(self.repository, self.branch)] = self.new_commit
            return {"data": {"updateRefs": {"clientMutationId": None}}}
        raise AssertionError((method, url, body))


FILES = (
    {
        "path": "src/existing.py",
        "content": "after\n",
        "expected_blob_sha": "b" * 40,
    },
    {
        "path": "tests/test_new.py",
        "content": "def test_new(): assert True\n",
        "expected_blob_sha": None,
    },
)


def test_exact_source_tree_write_is_one_commit_with_full_readback():
    transport = ScriptedTreeTransport()

    commit, blobs = transport.put_files_exact_head(
        transport.repository,
        FILES,
        transport.branch,
        "Atomic multi-file write",
        expected_head=transport.expected_head,
    )

    assert commit == transport.new_commit
    assert blobs == {
        "src/existing.py": "c" * 40,
        "tests/test_new.py": "e" * 40,
    }
    tree_posts = [
        body
        for method, url, body in transport.requests
        if method == "POST" and url.endswith("/git/trees")
    ]
    commit_posts = [
        body
        for method, url, body in transport.requests
        if method == "POST" and url.endswith("/git/commits")
    ]
    assert len(tree_posts) == 1
    assert len(commit_posts) == 1


def test_exact_source_tree_write_rejects_stale_blob_before_object_creation():
    transport = ScriptedTreeTransport()
    bad = (
        {
            "path": "src/existing.py",
            "content": "after\n",
            "expected_blob_sha": "9" * 40,
        },
    )

    with pytest.raises(GitHubPreconditionFailed, match="blob precondition"):
        transport.put_files_exact_head(
            transport.repository,
            bad,
            transport.branch,
            "Should not write",
            expected_head=transport.expected_head,
        )

    assert not any(
        method == "POST" and url.endswith("/git/blobs")
        for method, url, _body in transport.requests
    )


def test_exact_source_tree_write_before_oid_failure_is_precondition():
    transport = ScriptedTreeTransport()
    transport.fail_before_oid = True

    with pytest.raises(GitHubPreconditionFailed):
        transport.put_files_exact_head(
            transport.repository,
            FILES,
            transport.branch,
            "Atomic multi-file write",
            expected_head=transport.expected_head,
        )


def test_exact_source_tree_write_uncertain_readback_is_outcome_unknown():
    transport = ScriptedTreeTransport()
    original = transport._request

    def request_and_arm(method, url, body=None):
        result = original(method, url, body)
        if method == "POST" and url == transport.graphql_url:
            transport.fail_readback = True
        return result

    transport._request = request_and_arm

    with pytest.raises(GitHubOutcomeUnknown, match="readback"):
        transport.put_files_exact_head(
            transport.repository,
            FILES,
            transport.branch,
            "Atomic multi-file write",
            expected_head=transport.expected_head,
        )
