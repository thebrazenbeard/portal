"""PORTAL's opt-in Rezon observer contract (no privileged Rezon requests)."""

from __future__ import annotations

from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portal.desktop_cognition import CognitionRequest, discover_cognition_routes, select_cognition_route
from portal.desktop_ipc import DesktopRuntimeCommandHandler
from portal.desktop_rezon import (
    RezonReadConfig,
    authorized_rezon_route_ids,
    discover_rezon_observer,
    read_rezon_state,
)
from portal.desktop_runtime import (
    CognitionLedger, CognitionRequestEnvelope, ResidentCognitionEngine,
    invoke_local_text,
)


TOKEN = "0123456789abcdef" * 4
EPISODE = "collective-" + "a" * 32
PORT = 18765
PATH = f"/v1/episodes/{EPISODE}/state"
SNAPSHOT = {
    "episode_id": EPISODE,
    "input_digest": "b" * 64,
    "complete": True,
    "missing_agents": [],
    "syntheses": [{
        "synthesis_id": "s1", "integrator_id": "judge",
        "input_digest": "b" * 64, "content": "Competing claims remain unverified.",
        "status": "unverified_model_synthesis", "is_current": True,
    }],
}


class StubTransport:
    def __init__(self, state=None, *, corrupt_challenge=False, corrupt_response=False):
        self.calls = []
        self.state = state or SNAPSHOT
        self.corrupt_challenge = corrupt_challenge
        self.corrupt_response = corrupt_response
        self.client_nonce = None
        self.context = None

    def factory(self, host, port, *, timeout):
        assert host == "127.0.0.1" and port == PORT and 0 < timeout <= 5
        outer = self

        class Conn:
            def request(self, method, path, *, headers):
                outer.calls.append((method, path, headers))

            def getresponse(self):
                method, path, headers = outer.calls[-1]
                assert method == "GET"
                if path == "/v1/read-challenge":
                    assert headers["X-Rezon-State-Path"] == PATH
                    outer.client_nonce = headers["X-Rezon-Client-Nonce"]
                    nonce, epoch, ms = "c" * 64, "d" * 32, 12345
                    audience = f"127.0.0.1:{PORT}"
                    context = (b"GET\0" + PATH.encode() + b"\0" + audience.encode()
                               + b"\0" + bytes.fromhex(nonce)
                               + bytes.fromhex(outer.client_nonce)
                               + ms.to_bytes(8, "big") + bytes.fromhex(epoch))
                    outer.context = context
                    proof = hmac.new(bytes.fromhex(TOKEN),
                        b"rezon-state-read/preflight/v2\0" + context, sha256).hexdigest()
                    if outer.corrupt_challenge:
                        proof = "0" * 64
                    value = {"server_nonce": nonce, "client_nonce": outer.client_nonce,
                             "state_path": PATH, "audience": audience,
                             "issued_ms": ms, "epoch": epoch, "expires_in_ms": 10000,
                             "proof": proof}
                    response_proof = None
                else:
                    assert path == PATH
                    assert headers["Authorization"].startswith("Rezon-Read-HMAC ")
                    parts = headers["Authorization"].split(" ", 1)[1].split(".")
                    assert len(parts) == 5
                    expected = hmac.new(bytes.fromhex(TOKEN),
                        b"rezon-state-read/request/v2\0" + outer.context, sha256).hexdigest()
                    assert hmac.compare_digest(parts[4], expected)
                    value = outer.state
                    response_proof = hmac.new(bytes.fromhex(TOKEN),
                        b"rezon-state-read/response/v2\0" + outer.context
                        + sha256(json.dumps(value, separators=(",", ":")).encode()).digest(),
                        sha256).hexdigest()
                    if outer.corrupt_response:
                        response_proof = "0" * 64
                raw = json.dumps(value, separators=(",", ":")).encode()

                class Response:
                    status = 200

                    def read(self, limit):
                        return raw[:limit]

                    def getheader(self, name):
                        return response_proof if name == "X-Rezon-State-Proof" else None

                    def close(self):
                        pass

                return Response()

            def close(self):
                pass

        return Conn()


class RezonObserverTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "PORTAL_REZON_EPISODE_ID": EPISODE,
            "PORTAL_REZON_PORT": str(PORT),
            "REZON_COLLECTIVE_STATE_READ_TOKEN": TOKEN,
            "PORTAL_REZON_ADMIT_EPISODE_ID": EPISODE,
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_authenticated_read_is_two_gets_with_no_bearer_or_write(self):
        stub = StubTransport()
        with patch("portal.desktop_rezon._DeadlineHTTPConnection", stub.factory):
            state, fingerprint = read_rezon_state(RezonReadConfig.from_environment())
        self.assertEqual(state, SNAPSHOT)
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual([call[:2] for call in stub.calls], [
            ("GET", "/v1/read-challenge"), ("GET", PATH),
        ])
        self.assertNotIn("Authorization", stub.calls[0][2])
        self.assertNotIn(TOKEN, json.dumps(stub.calls))

    def test_challenge_and_response_authentication_fail_closed(self):
        for corruption, expected_calls in (({"corrupt_challenge": True}, 1),
                                           ({"corrupt_response": True}, 2)):
            stub = StubTransport(**corruption)
            with self.subTest(corruption=corruption), patch(
                    "portal.desktop_rezon._DeadlineHTTPConnection", stub.factory):
                with self.assertRaisesRegex(RuntimeError, "authentication"):
                    read_rezon_state(RezonReadConfig.from_environment())
                self.assertEqual(len(stub.calls), expected_calls)

    def test_explicit_selection_and_exact_state_binding(self):
        stub = StubTransport()
        with patch("portal.desktop_rezon._DeadlineHTTPConnection", stub.factory):
            routes = discover_cognition_routes(command_probe=lambda *_: None,
                ollama_tags=lambda: {"models": []}, pre_active_models=lambda: None,
                rezon_observer=discover_rezon_observer)
            self.assertEqual(len(routes), 1)
            route = routes[0]
            self.assertFalse(route.auto_admissible)
            self.assertEqual(route.capabilities, ("rezon_collective_advisory",))
            self.assertIsNone(select_cognition_route(CognitionRequest(
                required_capabilities=("rezon_collective_advisory",)), routes))
            admitted = authorized_rezon_route_ids()
            self.assertEqual(admitted, frozenset({route.route_id}))
            self.assertEqual(select_cognition_route(CognitionRequest(
                required_capabilities=("rezon_collective_advisory",)), routes,
                authorized_route_ids=admitted), route)
            self.assertIsNone(select_cognition_route(CognitionRequest(), routes,
                authorized_route_ids=admitted))
            self.assertIsNone(select_cognition_route(CognitionRequest(
                required_capabilities=()), routes, authorized_route_ids=admitted))
            output = json.loads(invoke_local_text(route, EPISODE))
            self.assertEqual(output["content"], "Competing claims remain unverified.")
            self.assertEqual(output["independent_hosts_verified"], False)
            self.assertEqual(output["protected_effect_authority"], False)
            with self.assertRaisesRegex(ValueError, "exact episode"):
                invoke_local_text(route, "new arbitrary model prompt")
            with patch.dict(os.environ, {"PORTAL_REZON_ADMIT_EPISODE_ID": ""}):
                with self.assertRaisesRegex(ValueError, "exact episode"):
                    invoke_local_text(route, EPISODE)
            stub.state = {**SNAPSHOT, "reviews": [{"content": "new review"}]}
            with self.assertRaisesRegex(RuntimeError, "changed"):
                invoke_local_text(route, EPISODE)

    def test_no_observer_http_before_request_source_capability_session_admission(self):
        # Exercise the *actual* default discovery function; a fake tuple of
        # routes would miss a premature private HTTP read during discovery.
        safe_discovery = lambda: discover_cognition_routes(command_probe=lambda *_: None,
            ollama_tags=lambda: None, pre_active_models=lambda: None)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CognitionLedger(Path(tmp) / "cognition.sqlite3")
            try:
                engine = ResidentCognitionEngine(ledger=ledger,
                    discover_routes=safe_discovery,
                    authorized_route_ids=authorized_rezon_route_ids())
                handler = DesktopRuntimeCommandHandler(engine=engine,
                    discover_routes=safe_discovery,
                    runtime_status=lambda: {"state": "ACTIVE"})
                requests = [
                    (CognitionRequestEnvelope("text", "HUMAN", "ordinary", "hello", 99), None),
                    (CognitionRequestEnvelope("auto", "PRE_ACTIVE_AUTONOMOUS_TURN",
                        "observer", EPISODE, 100,
                        required_capabilities=("rezon_collective_advisory",)), None),
                    (CognitionRequestEnvelope("chat", "HUMAN", "observer", EPISODE,
                        101, required_capabilities=("rezon_collective_advisory",)), "a" * 32),
                ]
                with patch("portal.desktop_rezon._get_json",
                           side_effect=AssertionError("private observer read before admission")):
                    self.assertEqual(handler.handle({"command": "desktop_discover_routes"})[
                        "routes"], [])
                    for request, session in requests:
                        self.assertEqual(engine.process(request,
                            conversation_session_id=session).state, "UNRESOLVED")
                with patch.dict(os.environ, {"PORTAL_REZON_ADMIT_EPISODE_ID": ""}):
                    with patch("portal.desktop_rezon._get_json",
                               side_effect=AssertionError("observer read after revocation")):
                        self.assertEqual(engine.process(CognitionRequestEnvelope(
                            "revoked", "HUMAN", "observer", EPISODE, 102,
                            required_capabilities=("rezon_collective_advisory",)
                        )).state, "UNRESOLVED")
            finally:
                ledger.close()

    def test_explicit_human_request_does_real_authenticated_observer_io(self):
        stub = StubTransport()
        safe_discovery = lambda: discover_cognition_routes(command_probe=lambda *_: None,
            ollama_tags=lambda: None, pre_active_models=lambda: None)
        with tempfile.TemporaryDirectory() as tmp, patch(
                "portal.desktop_rezon._DeadlineHTTPConnection", stub.factory):
            ledger = CognitionLedger(Path(tmp) / "cognition.sqlite3")
            try:
                engine = ResidentCognitionEngine(ledger=ledger,
                    discover_routes=safe_discovery,
                    authorized_route_ids=authorized_rezon_route_ids())
                response = engine.process(CognitionRequestEnvelope(
                    "authorized", "HUMAN", "observer", EPISODE, 103,
                    required_capabilities=("rezon_collective_advisory",)))
                self.assertEqual(response.state, "COMPLETED")
                self.assertEqual(json.loads(response.response_text)["content"],
                                 "Competing claims remain unverified.")
                self.assertEqual([call[0] for call in stub.calls], ["GET"] * 4)
            finally:
                ledger.close()

    def test_unconfigured_or_incomplete_collective_never_advertised(self):
        with patch.dict(os.environ, {"PORTAL_REZON_EPISODE_ID": "invalid"}):
            self.assertEqual(authorized_rezon_route_ids(), frozenset())
        with patch.dict(os.environ, {"PORTAL_REZON_ADMIT_EPISODE_ID": ""}):
            with patch("portal.desktop_rezon._DeadlineHTTPConnection",
                       side_effect=AssertionError("no unauthorised observer request")):
                self.assertFalse(discover_cognition_routes(command_probe=lambda *_: None,
                    ollama_tags=lambda: None, pre_active_models=lambda: None,
                    rezon_observer=discover_rezon_observer))
        incomplete = {**SNAPSHOT, "complete": False, "missing_agents": ["bob"]}
        stub = StubTransport(state=incomplete)
        with patch("portal.desktop_rezon._DeadlineHTTPConnection", stub.factory):
            self.assertFalse(discover_cognition_routes(command_probe=lambda *_: None,
                ollama_tags=lambda: None, pre_active_models=lambda: None,
                rezon_observer=discover_rezon_observer))


if __name__ == "__main__":
    unittest.main()
