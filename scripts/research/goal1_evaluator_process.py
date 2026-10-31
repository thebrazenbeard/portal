"""Separate process, learner-facing JSONL adapter for synthetic Goal 1 scoring.

Reuses the exact BlindInquiryGate. A trusted controller launches this process,
holds the fixture path, and exposes ONLY its standard-output protocol to the
learner. A process boundary by itself is not a security isolation boundary:
an adversarial learner with filesystem/process access could read the fixture.

In particular, do NOT expose a hash of low-entropy hidden labels before
scoring; their possible values may be brute-forced.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from goal1_blind_inquiry_gate import BlindInquiryGate, ProbeDenied, TrialClosed

MAX_LINE = 4096
MAX_FIXTURE_BYTES = 32768


def _distinct(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate request key")
        result[key] = value
    return result


def _load_fixture(path: Path) -> BlindInquiryGate:
    if not path.is_file() or path.stat().st_size > MAX_FIXTURE_BYTES:
        raise ValueError("invalid evaluator fixture")
    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_distinct)
    allowed = {"case_id", "inputs", "answers", "training_ids", "holdout_ids", "probe_budget"}
    if type(payload) is not dict or set(payload) != allowed:
        raise ValueError("invalid fixture schema")
    if type(payload["training_ids"]) is not list or type(payload["holdout_ids"]) is not list:
        raise ValueError("invalid fixture IDs")
    return BlindInquiryGate(
        case_id=payload["case_id"], inputs=payload["inputs"], answers=payload["answers"],
        training_ids=tuple(payload["training_ids"]),
        holdout_ids=tuple(payload["holdout_ids"]),
        probe_budget=payload["probe_budget"],
    )


def _reply(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _dispatch(gate: BlindInquiryGate, request: object, started: bool) -> tuple[dict[str, object], bool]:
    if type(request) is not dict or type(request.get("action")) is not str:
        return {"error": "INVALID_REQUEST"}, started
    action = request["action"]
    if action == "start" and set(request) == {"action"} and not started:
        return {"status": "INQUIRY", "prompt": gate.public_prompt()}, True
    if not started:
        return {"error": "INVALID_REQUEST"}, started
    if action == "probe" and set(request) == {"action", "item"}:
        try:
            feature, label = gate.probe(request["item"])
        except (ProbeDenied, TrialClosed):
            return {"error": "PROBE_DENIED"}, started
        return {"status": "INQUIRY", "item": request["item"], "features": feature, "label": label}, started
    if action == "open_holdout" and set(request) == {"action"}:
        try:
            features = gate.open_holdout()
        except TrialClosed:
            return {"error": "TRIAL_CLOSED"}, started
        return {"status": "PREDICTION", "inputs": features}, started
    if action == "submit" and set(request) == {"action", "predictions"}:
        try:
            score = gate.commit_predictions(request["predictions"])
        except TrialClosed:
            return {"error": "TRIAL_CLOSED"}, started
        except (ValueError, TypeError):
            return {"error": "INVALID_ARGUMENT"}, started
        return {"status": "SCORED", "score": score}, started
    return {"error": "INVALID_REQUEST"}, started


def serve(gate: BlindInquiryGate) -> None:
    started = False
    while True:
        raw = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not raw:
            return
        if len(raw) > MAX_LINE or not raw.endswith(b"\n"):
            _reply({"error": "INVALID_REQUEST"})
            return
        try:
            text = raw.decode("utf-8", errors="strict")
            request = json.loads(text, object_pairs_hook=_distinct)
        except (UnicodeDecodeError, ValueError):
            _reply({"error": "INVALID_REQUEST"})
            continue
        response, started = _dispatch(gate, request, started)
        _reply(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="Finite no-effect research evaluator")
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args()
    try:
        gate = _load_fixture(Path(args.fixture))
    except (OSError, TypeError, ValueError, KeyError):
        print("invalid evaluator fixture", file=sys.stderr)
        return 2
    serve(gate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
