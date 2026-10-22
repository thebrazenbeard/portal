"""Read-only loopback model-service inventory, never a qualified Vera runtime probe.

Only GET /api/version and GET /api/tags are issued. No generation, provider
mutation, model activation, credential access, or state write is performed.
Server responses are self-reported and cannot establish runtime consumption.
"""
from __future__ import annotations

import argparse
import json
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

MAX_REPLY = 65536
CLAIM_LIMIT = "LOCAL_SERVICE_REPORTED_INVENTORY_ONLY"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect not allowed")


def _read_json(opener, url: str, timeout: float) -> dict:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("unexpected HTTP status")
        data = response.read(MAX_REPLY + 1)
    if len(data) > MAX_REPLY:
        raise ValueError("unbounded service response")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("unexpected JSON response")
    return value


def probe_local_inventory(*, port: int = 11434, timeout: float = 2.0,
                          opener=None) -> dict:
    """Gather only a localhost service inventory. No privileged proof is minted."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid local port")
    if type(timeout) not in (int, float) or not 0 < timeout <= 10:
        raise ValueError("invalid bounded timeout")
    result = {
        "service": "ollama_compatible_loopback",
        "claim_limit": CLAIM_LIMIT,
        "inventory_observed": False,
        "models_reported": [],
        "qvr_installed_verified": False,
        "qvr_selected_route_verified": False,
        "qvr_consumption_verified": False,
        "model_inference_executed": False,
        "independent_attestation": False,
    }
    base = f"http://127.0.0.1:{port}"
    client = opener if opener is not None else build_opener(ProxyHandler({}), NoRedirect())
    try:
        version = _read_json(client, base + "/api/version", timeout)
        inventory = _read_json(client, base + "/api/tags", timeout)
        if not isinstance(version.get("version"), str) or not version["version"]:
            raise ValueError("missing reported version")
        models = inventory.get("models")
        if not isinstance(models, list):
            raise ValueError("missing model list")
        names = []
        for model in models:
            if not isinstance(model, dict) or not isinstance(model.get("name"), str):
                raise ValueError("invalid model entry")
            name = model["name"]
            if not name or len(name) > 128:
                raise ValueError("invalid model identifier")
            names.append(name)
        result["inventory_observed"] = True
        result["reported_version"] = version["version"]
        result["models_reported"] = names
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        result["failure"] = type(exc).__name__
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=11434)
    args = parser.parse_args(argv)
    result = probe_local_inventory(port=args.port)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["inventory_observed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
