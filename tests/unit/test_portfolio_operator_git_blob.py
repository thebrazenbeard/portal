import subprocess

from runner.portfolio_operator_bridge import _git_blob_sha_for_path


def _run(repo, *args):
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
    ).strip()


def test_git_blob_sha_for_path_honors_git_text_filters(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Project Runner Test"],
        cwd=repo,
        check=True,
    )
    (repo / ".gitattributes").write_text("*.json text eol=lf\n", encoding="utf-8")
    corpus = repo / "corpus.json"
    corpus.write_bytes(b'{\n  "value": 1\n}\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
    expected = _run(repo, "rev-parse", "HEAD:corpus.json")

    corpus.write_bytes(b'{\r\n  "value": 1\r\n}\r\n')

    assert _git_blob_sha_for_path(corpus) == expected
