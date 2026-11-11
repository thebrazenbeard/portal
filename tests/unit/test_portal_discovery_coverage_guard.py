import argparse
import pytest
from portal import cli
from portal.discovery import RepositoryInventoryItem, build_live_project_registry

def _args(tmp_path, required=False):
    return argparse.Namespace(owner="thebrazenbeard", curated_projects=cli.ROOT/"registry/projects.yaml",
                              output=tmp_path/"live.yaml", require_authenticated=required)

def test_explicit_complete_owner_scope_fails_before_discovery_without_token(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_github_token", lambda: None)
    monkeypatch.setattr(cli, "discover_live_project_registry",
                        lambda **kw: pytest.fail("must fail before fetching public-only scope"))
    with pytest.raises(ValueError, match="authenticated GitHub token required"):
        cli._discovery_payload(_args(tmp_path, True))
    assert not (tmp_path/"live.yaml").exists()

@pytest.mark.parametrize("token,scope", [(None,"PUBLIC_ONLY_OWNER"),("sample","AUTHENTICATED_OWNER_VISIBLE")])
def test_discovery_scope_is_explicit_not_claim_of_completeness(monkeypatch, tmp_path, token, scope):
    monkeypatch.setattr(cli, "_github_token", lambda: token)
    item=RepositoryInventoryItem(name="public-example",full_name="thebrazenbeard/public-example",
                                 private=False,archived=False,default_branch="main")
    snapshot=build_live_project_registry(owner="thebrazenbeard",repositories=(item,))
    monkeypatch.setattr(cli,"discover_live_project_registry",lambda **kw:snapshot)
    result=cli._discovery_payload(_args(tmp_path))
    assert result["discovery_scope"]==scope
    assert result["private_coverage_automatically_verified"] is False
    assert result["private"]==0
    assert (tmp_path/"live.yaml").is_file()

def test_full_run_and_continue_live_discovery_require_token_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_github_token", lambda: None)
    monkeypatch.setattr(cli, "GitHubRepositoryCatalog",
                        lambda **kw: pytest.fail("discovery should fail before network"))
    for command in ("run", "continue"):
        args=cli._parser().parse_args([
            command, "--session-id", "guard-test", "--discover-owner", "thebrazenbeard",
            "--require-authenticated", "--state-db", str(tmp_path/"state.sqlite3")])
        assert args.require_authenticated
        with pytest.raises(ValueError, match="authenticated GitHub token required"):
            cli._session_portfolio_paths(args)
    args=cli._parser().parse_args([
        "run", "--discover-owner", "thebrazenbeard", "--require-authenticated"])
    with pytest.raises(ValueError, match="authenticated GitHub token required"):
        cli._run_project_snapshot(args)
