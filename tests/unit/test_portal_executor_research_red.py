"""W40 research contract for the observational Runner -> Portal handoff.

All payloads are synthetic; no host credentials, connected devices or protected
effects are involved. A successful discovery is never an authorization grant.
"""

import json
import unittest


def _evidence(**overrides):
    payload = {
        "schema": "PROJECT_RUNNER_EXECUTOR_MCP_DISCOVERY_V1",
        "observation_only": True,
        "execution_authorized": False,
        "server_name_claim": "executor",
        "protocol_version": "2025-06-18",
        "available_tools": ["list_devices"],
        "connected_device_count": 0,
    }
    payload.update(overrides)
    return json.dumps(payload)


class PortalExecutorResearchTests(unittest.TestCase):
    def _parse(self, payload):
        from portal.executor_discovery_receipt import parse_executor_discovery_receipt

        return parse_executor_discovery_receipt(payload)

    def test_exact_observation_is_bounded_and_never_execution_authority(self):
        evidence = self._parse(_evidence())
        self.assertTrue(evidence.observation_only)
        self.assertFalse(evidence.execution_authorized)
        self.assertEqual(evidence.available_tools, ("list_devices",))
        self.assertEqual(evidence.connected_device_count, 0)

    def test_never_accepts_execution_or_grant_claims_in_discovery(self):
        for payload in (
            _evidence(execution_authorized=True),
            _evidence(authorized_effects=["SOURCE_WRITE"]),
            _evidence(grant_ref="fake-grant"),
            _evidence(runtime_identity_verified=True),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self._parse(payload)

    def test_schema_and_claimed_server_must_match_exact_contract(self):
        for payload in (
            _evidence(schema="PROJECT_RUNNER_EXECUTOR_MCP_DISCOVERY_V2"),
            _evidence(server_name_claim="malicious-executor"),
            _evidence(protocol_version="2024-11-05"),
            _evidence(observation_only=False),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self._parse(payload)

    def test_bool_count_duplicate_tools_and_nonobject_hold(self):
        for payload in (
            _evidence(connected_device_count=True),
            _evidence(connected_device_count=-1),
            _evidence(available_tools=["list_devices", "list_devices"]),
            _evidence(available_tools=[]),
            json.dumps(["not-an-object"]),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self._parse(payload)

    def test_runner_cli_bytes_are_accepted_without_any_authority(self):
        evidence = self._parse(_evidence().encode("utf-8"))
        self.assertEqual(evidence.server_name_claim, "executor")
        self.assertFalse(evidence.execution_authorized)


if __name__ == "__main__":
    unittest.main()
