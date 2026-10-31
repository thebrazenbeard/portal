"""Protocol tests with a synthetic signer; not a host anti-rollback proof."""

import base64
import json
import struct
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from portal.monotonic_anchor import (
    AnchorUnavailable,
    PinnedAnchorEndpoint,
    RemoteMonotonicAnchor,
    _canonical,
)


def raw_public(key):
    return key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class FakeSocket:
    def __init__(self, *, signer, change=None, malformed=None):
        self.signer = signer
        self.change = change
        self.malformed = malformed
        self.wire = b""
        self.location = None
        self.timeout = None

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def settimeout(self, value): self.timeout = value
    def connect(self, path): self.location = path

    def sendall(self, data):
        request = json.loads(data[4:].decode())
        response = dict(request, decision="ADVANCED", receipt_sequence=1)
        if self.change:
            response.update(self.change)
        response["signature"] = base64.b64encode(self.signer.sign(_canonical(response))).decode()
        payload = self.malformed if self.malformed is not None else _canonical(response)
        self.wire = struct.pack("!I", len(payload)) + payload

    def recv(self, n):
        data, self.wire = self.wire[:n], self.wire[n:]
        return data


class AnchorProtocolTests(unittest.TestCase):
    def setUp(self):
        self.signer = Ed25519PrivateKey.generate()
        self.endpoint = PinnedAnchorEndpoint(
            socket_path="/run/portal/anchor.sock",
            signer_public_key=raw_public(self.signer),
            authority_id="portal-authority", host_id="host-A",
            issuer_id="issuer-A", witness_id="witness-A",
        )
        self.client = RemoteMonotonicAnchor(self.endpoint)

    def advance(self, fake):
        with patch("portal.monotonic_anchor.socket.socket", return_value=fake):
            return self.client.advance(previous_epoch=7, request_digest="a" * 64)

    def test_signed_exact_advance(self):
        fake = FakeSocket(signer=self.signer)
        self.assertEqual(self.advance(fake), 8)
        self.assertEqual(fake.location, "/run/portal/anchor.sock")
        self.assertEqual(fake.timeout, 0.5)

    def test_altered_identity_denied_even_when_signed(self):
        for key in ("host_id", "issuer_id", "witness_id", "authority_id", "request_digest", "nonce"):
            with self.subTest(key=key):
                with self.assertRaises(AnchorUnavailable):
                    self.advance(FakeSocket(signer=self.signer, change={key: "different"}))

    def test_malformed_epoch_denied_even_when_signed(self):
        for value in (7, 9, True, "8"):
            with self.subTest(value=value):
                with self.assertRaises(AnchorUnavailable):
                    self.advance(FakeSocket(signer=self.signer, change={"next_epoch": value}))

    def test_forged_signer_denied(self):
        with self.assertRaises(AnchorUnavailable):
            self.advance(FakeSocket(signer=Ed25519PrivateKey.generate()))

    def test_denied_decision_fails_closed(self):
        with self.assertRaises(AnchorUnavailable):
            self.advance(FakeSocket(signer=self.signer, change={"decision": "DENIED"}))

    def test_oversize_denied(self):
        with self.assertRaises(AnchorUnavailable):
            self.advance(FakeSocket(signer=self.signer, malformed=b"x" * 5000))

    def test_invalid_json_denied(self):
        with self.assertRaises(AnchorUnavailable):
            self.advance(FakeSocket(signer=self.signer, malformed=b"{"))

    def test_missing_socket_fails_closed(self):
        class Missing(FakeSocket):
            def connect(self, path): raise FileNotFoundError(path)
        with self.assertRaises(AnchorUnavailable):
            self.advance(Missing(signer=self.signer))

    def test_request_rejected_if_epoch_or_digest_invalid(self):
        for epoch, digest in ((-1, "a"*64), (True, "a"*64), (0, "A"*64), (0, "x")):
            with self.subTest(epoch=epoch):
                with self.assertRaises(ValueError):
                    self.client.advance(previous_epoch=epoch, request_digest=digest)

    def test_unsafe_endpoint_shape_rejected(self):
        with self.assertRaises(ValueError):
            PinnedAnchorEndpoint(socket_path="relative", signer_public_key=raw_public(self.signer),
                                 authority_id="a", host_id="h", issuer_id="i", witness_id="w")
        with self.assertRaises(ValueError):
            PinnedAnchorEndpoint(socket_path="/tmp/x", signer_public_key=b"short",
                                 authority_id="a", host_id="h", issuer_id="i", witness_id="w")

    def test_missing_signature_denied(self):
        with self.assertRaises(AnchorUnavailable):
            self.advance(FakeSocket(signer=self.signer, malformed=b'{}'))


if __name__ == "__main__":
    unittest.main()
