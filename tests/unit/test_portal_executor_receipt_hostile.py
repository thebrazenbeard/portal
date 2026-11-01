"""Hostile synthetic edge tests: discovery evidence must never become authority.

All records are fabricated; these tests do not contact a host or provider.
"""

import json
import unittest

from portal.executor_discovery_receipt import parse_executor_discovery_receipt
from portal.capability_admission import (
    CapabilityDefinition,
    CapabilityInvocation,
    RuntimeAdvertisement,
    admit_capability,
)


def _valid(**changes):
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
    return record


class HostileExecutorDiscoveryReceiptTests(unittest.TestCase):
    def test_valid_synthetic_receipt_remains_observation_only(self):
        evidence = parse_executor_discovery_receipt(json.dumps(_valid()).encode("utf-8"))
        self.assertIs(evidence.observation_only, True)
        self.assertIs(evidence.execution_authorized, False)
        self.assertEqual(evidence.available_tools, ("list_devices",))
        self.assertEqual(evidence.connected_device_count, 0)

    def test_observation_cannot_substitute_for_an_admission_runtime(self):
        evidence = parse_executor_discovery_receipt(json.dumps(_valid(connected_device_count=7)))
        self.assertNotIsInstance(evidence, RuntimeAdvertisement)
        definition = CapabilityDefinition(
            capability_id="synthetic.readonly", version="1", operation="list",
            effect_class="READ_ONLY", input_schema_id="synthetic/in",
            output_schema_id="synthetic/out", source_repository="synthetic/repo",
            source_commit="a" * 40, privacy_class="PUBLIC", context_policy="GENERAL_READ",
        )
        request = CapabilityInvocation(
            request_id="req", capability_id="synthetic.readonly", capability_version="1",
            operation="list", required_effect="READ_ONLY", caller_model_id="model-A",
            participant_ref="anonymous", context_ref="no-context", adapter_id="none",
            host_id="unverified", idempotency_key="only-once",
        )
        admission = admit_capability(
            request, definition=definition, advertisement=evidence, grant=None,
            verify_grant=lambda *_: True, verify_context=None, now=20.0,
        )
        self.assertIs(admission.allowed, False)
        self.assertEqual(admission.reason, "NO_CURRENT_RUNTIME")

    def test_duplicate_json_keys_cannot_redefine_authority(self):
        # Python json.loads accepts duplicate object keys by default. A
        # signature/receipt verifier must not silently choose the last value.
        payload = json.dumps(_valid()).replace(
            '"execution_authorized": false',
            '"execution_authorized": false, "execution_authorized": true',
        )
        with self.assertRaises(ValueError):
            parse_executor_discovery_receipt(payload)
        payload = json.dumps(_valid()).replace(
            '"schema": "PROJECT_RUNNER_EXECUTOR_MCP_DISCOVERY_V1"',
            '"schema": "UNTRUSTED", "schema": "PROJECT_RUNNER_EXECUTOR_MCP_DISCOVERY_V1"',
        )
        with self.assertRaises(ValueError):
            parse_executor_discovery_receipt(payload)

    def test_nonfinite_numbers_and_bool_counts_fail_closed(self):
        for count in (True, -1, 1.0, "0", 1_000_000):
            with self.subTest(count=count), self.assertRaises(ValueError):
                parse_executor_discovery_receipt(json.dumps(_valid(connected_device_count=count)))
        for marker in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                parse_executor_discovery_receipt(json.dumps(_valid()).replace('"connected_device_count": 0',
                                                                           f'"connected_device_count": {marker}'))

    def test_unsigned_authority_claims_cannot_be_smuggled_as_extensions(self):
        for field, value in (
            ("grant_ref", "forged-grant"),
            ("authorized_effects", ["run_shell"]),
            ("host_qualified", True),
            ("trusted_attestation", True),
            ("devices", [{"deviceId": "sensitive-host-name"}]),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_executor_discovery_receipt(json.dumps(_valid(**{field: value})))

    def test_misleading_command_listing_is_still_only_observation(self):
        evidence = parse_executor_discovery_receipt(json.dumps(
            _valid(available_tools=["list_devices", "execute_command"])
        ))
        self.assertIs(evidence.execution_authorized, False)
        self.assertIs(evidence.observation_only, True)

    def test_duplicate_or_missing_local_list_devices_fails_closed(self):
        for tools in (["list_devices", "list_devices"], ["execute_command"], [], ["list_devices", 7]):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                parse_executor_discovery_receipt(json.dumps(_valid(available_tools=tools)))

    def test_oversized_or_invalid_utf8_fails_without_echoing_input(self):
        sentinel = "PRIVATE_TEST_SENTINEL_DO_NOT_ECHO"
        for payload in (json.dumps(_valid()) + (" " * 100_000) + sentinel,
                        b"\xff\xfe" + sentinel.encode("ascii")):
            with self.subTest(kind=type(payload).__name__):
                with self.assertRaises(ValueError) as raised:
                    parse_executor_discovery_receipt(payload)
                self.assertNotIn(sentinel, str(raised.exception))

    def test_bounded_bytes_with_excessive_nesting_fail_as_rejected_input(self):
        # 60 KiB is below the size cap but exceeds Python's JSON recursion
        # budget. The public parse API must reject rather than leak a
        # RecursionError into the containing admission process.
        deeply_nested = "[" * 30_000 + "0" + "]" * 30_000
        with self.assertRaises(ValueError):
            parse_executor_discovery_receipt(deeply_nested)

    def test_exact_utf8_byte_limit_is_inclusive_for_text_and_bytes(self):
        payload = json.dumps(_valid())
        boundary = payload + " " * (65_536 - len(payload.encode("utf-8")))
        for at_limit in (boundary, boundary.encode("utf-8")):
            with self.subTest(kind=type(at_limit).__name__):
                evidence = parse_executor_discovery_receipt(at_limit)
                self.assertIs(evidence.execution_authorized, False)
                self.assertIs(evidence.host_authenticated, False)
        for over_limit in (boundary + " ", boundary.encode("utf-8") + b" "):
            with self.subTest(kind=type(over_limit).__name__), self.assertRaises(ValueError):
                parse_executor_discovery_receipt(over_limit)

    def test_decoded_duplicate_keys_reject_both_authority_orders(self):
        for first, second in (("true", "false"), ("false", "true")):
            payload = json.dumps(_valid()).replace(
                '"execution_authorized": false',
                '"execution_authorized": ' + first
                + ', "execution_\\u0061uthorized": ' + second,
            )
            with self.subTest(first=first), self.assertRaises(ValueError):
                parse_executor_discovery_receipt(payload)

    def test_every_exact_schema_field_rejects_conflicting_json_types(self):
        candidates = (None, {}, [], True, False, 0, 1, 1.0, "", ["list_devices"])
        valid = _valid()
        for field in valid:
            for value in candidates:
                # Keep intentionally valid booleans, integer counts, and tool lists.
                if field == "observation_only" and value is True:
                    continue
                if field == "execution_authorized" and value is False:
                    continue
                if field == "connected_device_count" and type(value) is int:
                    continue
                if field == "available_tools" and value == ["list_devices"]:
                    continue
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    parse_executor_discovery_receipt(json.dumps(_valid(**{field: value})))

    def test_malformed_unicode_and_overflow_numbers_never_produce_evidence(self):
        payloads = (
            json.dumps(_valid()).replace('"connected_device_count": 0',
                                         '"connected_device_count": 1e9999'),
            json.dumps(_valid()).replace('"connected_device_count": 0',
                                         '"connected_device_count": -1e9999'),
            json.dumps(_valid()).replace('"list_devices"', '"list_devices\\ud800"'),
            json.dumps(_valid()) + "\ud800",
            json.dumps(_valid()).encode("utf-8") + b"\xed\xa0\x80",
        )
        for payload in payloads:
            with self.subTest(kind=type(payload).__name__), self.assertRaises(ValueError):
                parse_executor_discovery_receipt(payload)

    def test_discovery_cannot_substitute_for_grant_or_reach_verifiers(self):
        evidence = parse_executor_discovery_receipt(json.dumps(_valid(connected_device_count=1)))
        definition = CapabilityDefinition(
            capability_id="synthetic.readonly", version="1", operation="list",
            effect_class="READ_ONLY", input_schema_id="synthetic/in",
            output_schema_id="synthetic/out", source_repository="synthetic/repo",
            source_commit="a" * 40, privacy_class="PUBLIC", context_policy="GENERAL_READ",
        )
        request = CapabilityInvocation(
            request_id="req", capability_id="synthetic.readonly", capability_version="1",
            operation="list", required_effect="READ_ONLY", caller_model_id="model-A",
            participant_ref="anonymous", context_ref="no-context", adapter_id="adapter",
            host_id="synthetic-host", idempotency_key="only-once",
        )
        runtime = RuntimeAdvertisement(
            capability_id="synthetic.readonly", version="1", source_repository="synthetic/repo",
            source_commit="a" * 40, adapter_id="adapter", adapter_digest="sha256:" + "b" * 64,
            host_id="synthetic-host", observed_at=10.0, expires_at=30.0,
            available=True, attached=True, evidence_ref="synthetic:runtime",
        )
        calls = []

        def verifier(*args):
            calls.append(args)
            return True

        for advertisement, expected_reason in ((runtime, "NO_GRANT"), (evidence, "NO_CURRENT_RUNTIME")):
            with self.subTest(expected_reason=expected_reason):
                admission = admit_capability(
                    request, definition=definition, advertisement=advertisement, grant=evidence,
                    verify_grant=verifier, verify_context=verifier, now=20.0,
                )
                self.assertIs(admission.allowed, False)
                self.assertEqual(admission.reason, expected_reason)
                self.assertEqual(admission.receipt.authorization, "DENIED")
                self.assertEqual(admission.receipt.effect_status, "NOT_EXECUTED")
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
