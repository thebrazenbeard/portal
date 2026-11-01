"""Contract checks for Runner's observational Executor MCP discovery output."""

import json
import unittest

from portal.executor_discovery_receipt import (
    ExecutorDiscoveryEvidence,
    parse_executor_discovery_receipt,
)


def valid_receipt(**changes):
    record = {
        "schema": "PROJECT_RUNNER_EXECUTOR_MCP_DISCOVERY_V1",
        "observation_only": True,
        "execution_authorized": False,
        "server_name_claim": "executor",
        "protocol_version": "2025-06-18",
        "available_tools": ["list_devices"],
        "connected_device_count": 0,
    }
    record.update(changes)
    return json.dumps(record)


class ExecutorDiscoveryReceiptTests(unittest.TestCase):
    def test_exact_runner_receipt_is_only_unattested_observation(self):
        parsed = parse_executor_discovery_receipt(valid_receipt().encode())
        self.assertIsInstance(parsed, ExecutorDiscoveryEvidence)
        self.assertTrue(parsed.observation_only)
        self.assertFalse(parsed.execution_authorized)
        self.assertFalse(parsed.host_authenticated)
        self.assertEqual(parsed.available_tools, ("list_devices",))
        self.assertEqual(parsed.connected_device_count, 0)

    def test_positive_device_count_still_never_authorizes(self):
        parsed = parse_executor_discovery_receipt(
            valid_receipt(available_tools=["list_devices", "execute_command"], connected_device_count=2)
        )
        self.assertEqual(parsed.connected_device_count, 2)
        self.assertFalse(parsed.execution_authorized)
        self.assertFalse(parsed.host_authenticated)

    def test_authority_claims_are_rejected_not_ignored(self):
        for change in (
            {"execution_authorized": True},
            {"observation_only": False},
            {"authorized_effects": ["SOURCE_WRITE"]},
            {"host_authenticated": True},
            {"authorization": "ALLOWED"},
            {"observation_only": 1},
            {"execution_authorized": 0},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    parse_executor_discovery_receipt(valid_receipt(**change))

    def test_identity_and_schema_are_not_trusted_when_mismatched(self):
        for change in (
            {"schema": "OTHER"},
            {"server_name_claim": "evil-executor"},
            {"protocol_version": "2025-03-26"},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    parse_executor_discovery_receipt(valid_receipt(**change))

    def test_malformed_tools_and_counts_rejected(self):
        for change in (
            {"connected_device_count": True},
            {"connected_device_count": -1},
            {"connected_device_count": 1001},
            {"connected_device_count": 1.1},
            {"available_tools": []},
            {"available_tools": ["list_devices", "list_devices"]},
            {"available_tools": ["execute_command"]},
            {"available_tools": ["list_devices", 17]},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    parse_executor_discovery_receipt(valid_receipt(**change))

    def test_duplicate_json_keys_and_non_object_rejected(self):
        for raw in (
            valid_receipt()[:-1] + ',"execution_authorized":true}',
            '[1, 2]',
            'null',
            '{"schema":NaN}',
        ):
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(ValueError):
                    parse_executor_discovery_receipt(raw)

    def test_invalid_or_oversized_transport_body_rejected(self):
        for raw in (b"\xff", "x" * 65537, None, {"schema": "x"}):
            with self.subTest(kind=type(raw).__name__):
                with self.assertRaises(ValueError):
                    parse_executor_discovery_receipt(raw)


if __name__ == "__main__":
    unittest.main()
