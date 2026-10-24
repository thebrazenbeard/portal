"""Bounded rule commitments proposed by a local model (Goal 1 research).

The model provides symbolic candidate programs, not a table containing answers
for withheld cases. These programs can be applied to previously unseen inputs,
with no eval/exec or host tool authority. This is not unrestricted rule
invention, verified model identity, independently held truth, or self-learning.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

_CASE = re.compile(r"[a-z][a-z0-9_-]{1,63}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ITEM = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_ALLOWED = frozenset((
    "parity_ones",
    "at_least_ones:2",
    "bit_is_one:0",
    "bit_is_one:1",
    "bit_is_one:2",
    "exact_ones:1",
    "exact_ones:2",
))
MAX_RESPONSE_BYTES = 900


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate model JSON key")
        result[k] = v
    return result


@dataclass(frozen=True, slots=True)
class RuleCommitment:
    case_id: str
    model_digest: str
    rules: tuple[str, ...]
    response_sha256: str
    commitment_sha256: str
    source_kind: str = "UNVERIFIED_LOCAL_MODEL_OUTPUT"


def _commitment_digest(case_id: str, model_digest: str, rules: tuple[str, ...], response_sha256: str) -> str:
    payload = json.dumps({
        "schema": "GOAL1_BOUNDED_RULE_COMMITMENT_V1",
        "case_id": case_id,
        "model_digest_claim": model_digest,
        "rules": list(rules),
        "response_sha256": response_sha256,
    }, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(payload)


def commit_rules(*, case_id: str, model_digest: str, response: str) -> RuleCommitment:
    """Lock bounded model output before later held-out examples are supplied.

    Callers are responsible for proving response really came from the named
    selected process and for keeping withheld labels inaccessible.
    """
    if type(case_id) is not str or _CASE.fullmatch(case_id) is None:
        raise ValueError("invalid case_id")
    if type(model_digest) is not str or _DIGEST.fullmatch(model_digest) is None:
        raise ValueError("invalid claimed model digest")
    if type(response) is not str:
        raise ValueError("model response must be UTF-8 text")
    payload = response.encode("utf-8")
    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("invalid bounded model response size")
    try:
        data = json.loads(response, object_pairs_hook=_unique_pairs)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid model proposal JSON") from exc
    if type(data) is not dict or set(data) != {"candidates"}:
        raise ValueError("model proposal contains unexpected fields")
    choices = data["candidates"]
    if (type(choices) is not list or not 2 <= len(choices) <= 6
            or any(type(x) is not str or x not in _ALLOWED for x in choices)
            or len(set(choices)) != len(choices)):
        raise ValueError("proposals must be distinct, whitelisted, finite rules")
    rules = tuple(choices)
    response_sha256 = _sha256(payload)
    commitment_sha256 = _commitment_digest(case_id, model_digest, rules, response_sha256)
    return RuleCommitment(
        case_id=case_id,
        model_digest=model_digest,
        rules=rules,
        response_sha256=response_sha256,
        commitment_sha256=commitment_sha256,
    )


def _rule_output(rule: str, bits: str) -> str:
    count = bits.count("1")
    if rule == "parity_ones":
        return str(count % 2)
    if rule == "at_least_ones:2":
        return str(int(count >= 2))
    if rule.startswith("bit_is_one:"):
        return bits[int(rule[-1])]
    if rule.startswith("exact_ones:"):
        return str(int(count == int(rule[-1])))
    raise ValueError("unrecognized symbolic rule")


def predict_rules(commitment: RuleCommitment, features: dict[str, str],
                  *, expected_commitment: str) -> dict[str, dict[str, str]]:
    """Apply a precommitted rule to unseen inputs. No answer keys accepted."""
    if type(commitment) is not RuleCommitment:
        raise ValueError("expected an exact rule commitment")
    recomputed = _commitment_digest(
        commitment.case_id, commitment.model_digest,
        commitment.rules, commitment.response_sha256,
    )
    if (type(expected_commitment) is not str
            or expected_commitment != recomputed
            or recomputed != commitment.commitment_sha256):
        raise ValueError("rule commitment does not match previous commitment")
    if (type(features) is not dict or not 1 <= len(features) <= 64
            or any(type(k) is not str or _ITEM.fullmatch(k) is None
                   or type(v) is not str or re.fullmatch(r"[01]{3}", v) is None
                   for k, v in features.items())):
        raise ValueError("invalid unseen feature set")
    return {
        rule: {item: _rule_output(rule, bits) for item, bits in sorted(features.items())}
        for rule in commitment.rules
    }
