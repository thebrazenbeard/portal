"""Executable source contract for VN2 co-located, authenticated Rezon reads."""

from __future__ import annotations

import json
import os
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from portal.desktop_rezon import RezonReadConfig
from scripts.qualify_rezon_colocation import inspect_colocation, main


EPISODE = "collective-" + "a" * 32
PORT = 18765
TOKEN = "abcdef0123456789" * 4


def config():
    return RezonReadConfig(episode_id=EPISODE, port=PORT, token=TOKEN)


def complete_snapshot():
    digest = "0" * 64
    return {
        "episode_id": EPISODE,
        "complete": True,
        "missing_agents": [],
        "input_digest": digest,
        "syntheses": [{
            "is_current": True,
            "input_digest": digest,
            "status": "unverified_model_synthesis",
            "synthesis_id": "s1",
            "content": "unverified advisory",
        }],
    }


def test_missing_config_makes_no_observer_request():
    with patch("scripts.qualify_rezon_colocation.RezonReadConfig.from_environment", return_value=None):
        with patch("scripts.qualify_rezon_colocation.read_rezon_state", side_effect=AssertionError("HTTP attempted")):
            result = inspect_colocation(expected_episode=EPISODE, expected_port=PORT)
    assert result["status"] == "HOLD"
    assert result["reason"] == "observer_configuration_missing_or_invalid"


def test_port_and_episode_bound_before_any_observer_request():
    with patch("scripts.qualify_rezon_colocation.RezonReadConfig.from_environment", return_value=config()):
        with patch("scripts.qualify_rezon_colocation.read_rezon_state", side_effect=AssertionError("HTTP attempted")):
            assert inspect_colocation(expected_episode=EPISODE, expected_port=8765)["reason"] == "expected_endpoint_mismatch"
            assert inspect_colocation(expected_episode="collective-" + "b" * 32, expected_port=PORT)["reason"] == "expected_endpoint_mismatch"


def test_missing_explicit_admission_makes_no_observer_request():
    with patch("scripts.qualify_rezon_colocation.RezonReadConfig.from_environment", return_value=config()):
        with patch("scripts.qualify_rezon_colocation.authorized_rezon_route_ids", return_value=frozenset()):
            with patch("scripts.qualify_rezon_colocation.read_rezon_state", side_effect=AssertionError("HTTP attempted")):
                assert inspect_colocation(expected_episode=EPISODE, expected_port=PORT)["reason"] == "explicit_episode_admission_missing"


def test_qualified_read_never_claims_install_or_effect_and_hides_token():
    with patch("scripts.qualify_rezon_colocation.RezonReadConfig.from_environment", return_value=config()):
        with patch("scripts.qualify_rezon_colocation.authorized_rezon_route_ids", return_value=frozenset({f"rezon:{EPISODE}"})):
            with patch("scripts.qualify_rezon_colocation.read_rezon_state", return_value=(complete_snapshot(), "1" * 64)):
                buf = StringIO()
                with redirect_stdout(buf):
                    assert main(["--episode", EPISODE, "--port", str(PORT)]) == 0
    result = json.loads(buf.getvalue())
    assert result["status"] == "PASS_READ_ONLY"
    assert result["source_client_authenticated"] is True
    assert result["deployed_route_verified"] is False
    assert result["protected_effect_authority"] is False
    assert TOKEN not in buf.getvalue()


def test_transport_or_incomplete_episode_hold_without_secret_leakage():
    with patch("scripts.qualify_rezon_colocation.RezonReadConfig.from_environment", return_value=config()):
        with patch("scripts.qualify_rezon_colocation.authorized_rezon_route_ids", return_value=frozenset({f"rezon:{EPISODE}"})):
            with patch("scripts.qualify_rezon_colocation.read_rezon_state", side_effect=RuntimeError(TOKEN)):
                result = inspect_colocation(expected_episode=EPISODE, expected_port=PORT)
                assert result["status"] == "HOLD"
                assert TOKEN not in json.dumps(result)
            with patch("scripts.qualify_rezon_colocation.read_rezon_state", return_value=({}, "1" * 64)):
                assert inspect_colocation(expected_episode=EPISODE, expected_port=PORT)["reason"] == "no_complete_authenticated_episode"
            with patch("scripts.qualify_rezon_colocation.read_rezon_state", return_value=([], "1" * 64)):
                assert inspect_colocation(expected_episode=EPISODE, expected_port=PORT)["status"] == "HOLD"
            with patch("scripts.qualify_rezon_colocation.read_rezon_state", return_value=(complete_snapshot(), "not-a-hash")):
                assert inspect_colocation(expected_episode=EPISODE, expected_port=PORT)["status"] == "HOLD"


def test_exact_port_and_credential_survive_environment_mutation_after_admission():
    admitted = config()

    def mutate_environment_after_admission():
        os.environ["PORTAL_REZON_PORT"] = "17777"
        os.environ["REZON_COLLECTIVE_STATE_READ_TOKEN"] = "0" * 64
        return frozenset({f"rezon:{EPISODE}"})

    def read_only_original_target(sealed_config):
        assert sealed_config is admitted
        assert sealed_config.port == PORT
        assert sealed_config.token == TOKEN
        return complete_snapshot(), "1" * 64

    with patch.dict(os.environ, {"PORTAL_REZON_PORT": str(PORT),
                                 "REZON_COLLECTIVE_STATE_READ_TOKEN": TOKEN}):
        with patch("scripts.qualify_rezon_colocation.RezonReadConfig.from_environment", return_value=admitted):
            with patch("scripts.qualify_rezon_colocation.authorized_rezon_route_ids",
                       side_effect=mutate_environment_after_admission):
                with patch("scripts.qualify_rezon_colocation.read_rezon_state",
                           side_effect=read_only_original_target) as reader:
                    result = inspect_colocation(expected_episode=EPISODE, expected_port=PORT)
    assert result["status"] == "PASS_READ_ONLY"
    reader.assert_called_once_with(admitted)
