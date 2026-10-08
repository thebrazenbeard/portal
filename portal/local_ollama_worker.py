"""Local-only, source-only Ollama proposal worker for P.O.R.T.A.L. prototype."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request
from urllib.error import URLError

SHA40 = re.compile(r"^[a-f0-9]{40}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
OUTPUT_PATH = "docs/portal/local-review-evidence-v1.md"


def _git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True, text=True, errors="replace", check=True, timeout=20,
    )
    return result.stdout.strip()


def _validate(packet: dict, checkout: Path) -> str:
    if packet.get("schema") != "PORTAL_WAVE_WORK_PACKET_V1":
        raise ValueError("wrong packet schema")
    required = {
        "advisory_only": True,
        "execution_authorized": False,
        "protected_effects_authorized": False,
        "source_mutation_authorized": False,
        "target_ref_mutation_authorized": False,
    }
    if any(packet.get(k) is not v for k, v in required.items()):
        raise ValueError("packet does not deny protected or source effects")
    if packet.get("effect_ceiling") != "SOURCE_ONLY":
        raise ValueError("wrong source-only ceiling")
    if (packet.get("execution_promotion") is not None
            or packet.get("execution_effect_class") is not None):
        raise ValueError("unexpected execution promotion")
    if not isinstance(packet.get("repository"), str) or not REPO.fullmatch(packet["repository"]):
        raise ValueError("invalid repository")
    if not isinstance(packet.get("exact_head"), str) or not SHA40.fullmatch(packet["exact_head"]):
        raise ValueError("invalid head")
    if not isinstance(packet.get("source_ref"), str) or not packet["source_ref"]:
        raise ValueError("missing source ref")
    if _git(checkout, "rev-parse", "HEAD") != packet["exact_head"]:
        raise ValueError("stale checkout")
    if _git(checkout, "status", "--porcelain"):
        raise ValueError("unclean checkout")
    origin = _git(checkout, "remote", "get-url", "origin").lower().replace("\\", "/")
    repo = packet["repository"].lower()

    if not (origin.endswith("/" + repo + ".git") or origin.endswith("/" + repo)):
        raise ValueError("wrong checkout origin")
    if (checkout / OUTPUT_PATH).exists():
        raise ValueError("proposal path already exists")
    whitelist = ("README.md", ".github/workflows/manifest-provenance.yml",
                 "tests/test_manifest_validation.py")
    sections = []
    for relative in whitelist:
        item = checkout / relative
        if item.is_file() and not item.is_symlink():
            sections.append("FILE: " + relative + "\n" + item.read_text(
                encoding="utf-8", errors="replace"
            )[:8000])
    if not sections:
        raise ValueError("no allowed source evidence")
    return "\n\n".join(sections)


def _query_local_model(source: str, model: str) -> dict:
    prompt = (
        "Review these repository files ONLY. Return JSON with properties: "
        "observations (array of 1-3 short strings), suggested_check (one "
        "short string), uncertainty (one short string). Each observation "
        "must be grounded in the supplied file text. GitHub CI result is "
        "NOT supplied: do not claim its cause or status. Do not invent "
        "execution, permissions, test results, or verified effects. "
        "No shell commands, URLs, or credentials.\n\n" + source
    )
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                         "format": "json", "options": {"num_predict": 450,
                         "temperature": 0}}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        value = json.loads(response.read().decode("utf-8"))
    output = json.loads(value["response"])
    if not isinstance(output, dict):
        raise ValueError("model object missing")
    observations = output.get("observations")
    if not (isinstance(observations, list) and 1 <= len(observations) <= 3
            and all(isinstance(v, str) and 0 < len(v) <= 1500 for v in observations)):
        raise ValueError("model observation structure invalid")
    if not all(isinstance(output.get(k), str) and 0 < len(output[k]) <= 1500
               for k in ("suggested_check", "uncertainty")):
        raise ValueError("model summary structure invalid")
    return output


def _write_once(path: Path, data: dict) -> None:
    if path.exists():
        raise ValueError("durable output already exists")
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def run(packet_path: Path, receipt_path: Path, checkout: Path, model: str) -> None:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    source = _validate(packet, checkout)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    review = _query_local_model(source, model)
    observations = "\n".join("- " + x for x in review["observations"])
    content = (
        "# Local model source inspection (unverified)\n\n"
        "Repository: " + packet["repository"] + "\n"
        "Exact head: " + packet["exact_head"] + "\n"
        "Whitelisted evidence SHA-256: " + digest + "\n"
        "Local model: " + model + "\n\n"
        "## Observations (model hypotheses)\n\n" + observations + "\n\n"
        "## Suggested verification\n\n" + review["suggested_check"] + "\n\n"
        "## Limitations\n\n" + review["uncertainty"] + "\n\n"
        "Advisory proposal only. No independent review or external effect.\n"
    )
    proposal = {"schema": "PORTAL_SOURCE_TREE_PROPOSAL_V1",
                "repository": packet["repository"],
                "source_ref": packet["source_ref"],
                "expected_head": packet["exact_head"],
                "message": "Portal: no-paid local model review (advisory only)",
                "files": [{"path": OUTPUT_PATH, "content": content,
                           "expected_blob_sha": None}]}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path = receipt_path.parent / "proposal.json"
    _write_once(proposal_path, proposal)
    artifact_sha = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    _write_once(receipt_path, {"schema": "PORTAL_WORKER_RECEIPT_V1",
        "receipt_class": "PROPOSED_SOURCE_TREE",
        "reason": "source-only local model review; unverified",
        "artifacts": [{"kind": "SOURCE_TREE_PROPOSAL",
                       "relative_path": "proposal.json", "sha256": artifact_sha}]})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portal-packet", required=True)
    parser.add_argument("--portal-receipt", required=True)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--model", default="qwen3:4b-instruct")
    args = parser.parse_args()
    try:
        run(Path(args.portal_packet), Path(args.portal_receipt),
            Path(args.checkout), args.model)
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, URLError) as exc:
        receipt = Path(args.portal_receipt)
        if not receipt.exists():
            receipt.parent.mkdir(parents=True, exist_ok=True)
            _write_once(receipt, {
                "schema": "PORTAL_WORKER_RECEIPT_V1",
                "receipt_class": ("FAILED_RETRYABLE" if isinstance(
                    exc, (URLError, TimeoutError, ConnectionError)
                ) else "FAILED_DETERMINISTIC"),
                "reason": "local proposal unqualified: " + type(exc).__name__,
                "artifacts": [],
            })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
