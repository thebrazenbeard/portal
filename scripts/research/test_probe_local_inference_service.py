"""No network required: assert bounded READ-only model inventory semantics."""
import io
import json
import unittest
from unittest.mock import patch

from probe_local_inference_service import (
    MAX_REPLY, NoRedirect, probe_local_inventory, main,
)


class Response(io.BytesIO):
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


class Reader:
    def __init__(self, version=None, models=None, fail=None):
        self.version = version if version is not None else {"version": "test"}
        self.models = models if models is not None else {"models": [{"name": "local:test"}]}
        self.fail = fail
        self.requests = []

    def open(self, req, timeout):
        self.requests.append((req.get_method(), req.full_url, timeout))
        if self.fail:
            raise OSError("service not available")
        body = self.version if req.full_url.endswith("/api/version") else self.models
        return Response(json.dumps(body).encode())


class ProbeTests(unittest.TestCase):
    def test_get_only_two_bounded_read_paths(self):
        reader = Reader()
        r = probe_local_inventory(opener=reader)
        self.assertTrue(r["inventory_observed"])
        self.assertEqual([m for m, _, _ in reader.requests], ["GET", "GET"])
        self.assertEqual([u.split(":11434")[-1] for _, u, _ in reader.requests],
                         ["/api/version", "/api/tags"])
        self.assertFalse(r["model_inference_executed"])
        self.assertFalse(r["qvr_consumption_verified"])

    def test_unavailable_service_is_hold(self):
        r = probe_local_inventory(opener=Reader(fail=True))
        self.assertFalse(r["inventory_observed"])
        self.assertEqual(r["failure"], "OSError")

    def test_forged_model_list_does_not_qualify_vera(self):
        reader = Reader(models={"models": [{"name": "vera-local:latest"}]})
        r = probe_local_inventory(opener=reader)
        self.assertTrue(r["inventory_observed"])
        for k in ("qvr_installed_verified", "qvr_selected_route_verified",
                  "qvr_consumption_verified", "independent_attestation"):
            self.assertFalse(r[k])

    def test_reject_non_number_or_bad_port(self):
        for port in (True, -1, 0, 65536, "11434"):
            with self.subTest(port=port):
                with self.assertRaises(ValueError):
                    probe_local_inventory(port=port)

    def test_reject_invalid_timeouts(self):
        for timeout in (True, 0, 11, -1):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    probe_local_inventory(timeout=timeout)

    def test_invalid_inventory_is_not_a_success(self):
        for malformed in ({"models": None}, {"models": [{"name": ""}]},
                          {"models": [{"name": 123}]}, {"models": "not a list"}):
            with self.subTest(malformed=malformed):
                r = probe_local_inventory(opener=Reader(models=malformed))
                self.assertFalse(r["inventory_observed"])

    def test_refuse_http_redirect(self):
        self.assertRaises(ValueError, NoRedirect().redirect_request,
                          None, None, 302, "redirect", {}, "https://example.com")

    def test_oversized_response_rejected(self):
        class Huge(Reader):
            def open(self, req, timeout):
                return Response(b"x" * (MAX_REPLY + 1))
        r = probe_local_inventory(opener=Huge())
        self.assertFalse(r["inventory_observed"])
        self.assertEqual(r["failure"], "ValueError")


if __name__ == "__main__":
    unittest.main()
