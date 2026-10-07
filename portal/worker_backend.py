from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import subprocess
from typing import Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_RECEIPTS = {
    "SUCCEEDED_NO_EFFECT",
    "PROPOSED_SOURCE_TREE",
    "HELD",
    "FAILED_RETRYABLE",
    "FAILED_DETERMINISTIC",
    "OUTCOME_UNKNOWN",
}
_SUCCESS_RECEIPTS = {"SUCCEEDED_NO_EFFECT", "PROPOSED_SOURCE_TREE", "HELD"}


@dataclass(frozen=True)
class ProcessWorkerSpec:
    command: tuple[str, ...]
    timeout_seconds: float = 900.0
    pass_env: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("process worker command is required")
        if any(not isinstance(value, str) or not value for value in self.command):
            raise ValueError("process worker command entries must be non-empty strings")
        executable = Path(self.command[0])
        if not executable.is_absolute():
            raise ValueError("process worker requires an absolute executable path")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("process worker timeout_seconds must be positive")
        normalized_env: list[str] = []
        seen: set[str] = set()
        for name in self.pass_env:
            if not isinstance(name, str) or _ENV_NAME.fullmatch(name) is None:
                raise ValueError("process worker pass_env contains invalid name")
            if name not in seen:
                normalized_env.append(name)
                seen.add(name)
        object.__setattr__(self, "pass_env", tuple(sorted(normalized_env)))


@dataclass(frozen=True)
class WorkerArtifact:
    kind: str
    relative_path: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class ProcessWorkerResult:
    receipt_class: str
    reason: str
    evidence_sha256: str
    artifacts: tuple[WorkerArtifact, ...]
    receipt_path: Path
    packet_path: Path
    stdout_path: Path
    stderr_path: Path
    returncode: int | None
    timed_out: bool
    replayed: bool


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _delivery_identity(packet: Mapping[str, object]) -> str:
    required = (
        "run_id",
        "subject_id",
        "node_id",
        "delivery_fencing_token",
        "exact_head",
        "plan_sha256",
    )
    identity: dict[str, object] = {}
    for key in required:
        value = packet.get(key)
        if value is None or value == "":
            raise ValueError(f"worker packet missing {key}")
        identity[key] = value
    return hashlib.sha256(_canonical_bytes(identity)).hexdigest()


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(raw, encoding="utf-8")
    os.replace(temp, path)


def _minimal_environment(
    *,
    pass_env: tuple[str, ...],
    workspace: Path,
) -> dict[str, str]:
    base_names = (
        "HOME",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    )
    env = {
        name: os.environ[name]
        for name in base_names
        if name in os.environ
    }
    for name in pass_env:
        if name in os.environ:
            env[name] = os.environ[name]
    env["PORTAL_WORKSPACE"] = str(workspace)
    env["PORTAL_WORKER_PROTOCOL"] = "PORTAL_PROCESS_WORKER_V1"
    return env


def _synthetic_receipt(reason: str) -> dict[str, object]:
    return {
        "schema": "PORTAL_WORKER_RECEIPT_V1",
        "receipt_class": "OUTCOME_UNKNOWN",
        "reason": reason,
        "artifacts": [],
    }


def _load_receipt(
    *,
    receipt_path: Path,
    workspace: Path,
) -> tuple[str, str, tuple[WorkerArtifact, ...]]:
    try:
        raw = receipt_path.read_bytes()
    except OSError as exc:
        raise ValueError("worker receipt is unavailable") from exc
    try:
        payload = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("worker receipt is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("worker receipt must be a JSON object")
    if payload.get("schema") != "PORTAL_WORKER_RECEIPT_V1":
        raise ValueError("unexpected worker receipt schema")

    receipt_class = payload.get("receipt_class")
    if receipt_class == "SUCCEEDED_SOURCE_CHANGE":
        raise ValueError(
            "source mutation receipts are forbidden for process workers"
        )
    if receipt_class not in _ALLOWED_RECEIPTS:
        raise ValueError("unsupported process worker receipt class")

    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("worker receipt reason is required")

    artifacts_raw = payload.get("artifacts", [])
    if not isinstance(artifacts_raw, list):
        raise ValueError("worker receipt artifacts must be an array")

    root = workspace.resolve()
    artifacts: list[WorkerArtifact] = []
    seen_paths: set[str] = set()
    for item in artifacts_raw:
        if not isinstance(item, dict):
            raise ValueError("worker artifact entry must be an object")
        kind = item.get("kind")
        relative = item.get("relative_path")
        digest = item.get("sha256")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("worker artifact kind is required")
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
        ):
            raise ValueError("worker artifact relative_path is invalid")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError("artifact path escapes worker workspace")
        canonical_relative = pure.as_posix()
        if canonical_relative in seen_paths:
            raise ValueError("worker artifact path is duplicated")
        seen_paths.add(canonical_relative)
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError("worker artifact sha256 must be lowercase SHA-256")

        candidate = workspace.joinpath(*pure.parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError("worker artifact file is unavailable") from exc
        if resolved == root or root not in resolved.parents:
            raise ValueError("artifact path escapes worker workspace")
        if not resolved.is_file():
            raise ValueError("worker artifact must be a regular file")
        observed = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if observed != digest:
            raise ValueError("worker artifact digest mismatch")
        artifacts.append(
            WorkerArtifact(
                kind=kind.strip(),
                relative_path=canonical_relative,
                path=resolved,
                sha256=digest,
            )
        )

    return receipt_class, reason.strip(), tuple(artifacts)


def _result_from_receipt(
    *,
    receipt_path: Path,
    packet_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    workspace: Path,
    returncode: int | None,
    timed_out: bool,
    replayed: bool,
) -> ProcessWorkerResult:
    receipt_class, reason, artifacts = _load_receipt(
        receipt_path=receipt_path,
        workspace=workspace,
    )
    if returncode not in (None, 0) and receipt_class in _SUCCESS_RECEIPTS:
        replacement = _synthetic_receipt(
            "worker process exited non-zero after emitting a success receipt"
        )
        _write_json_atomic(receipt_path, replacement)
        receipt_class, reason, artifacts = _load_receipt(
            receipt_path=receipt_path,
            workspace=workspace,
        )
    evidence_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    return ProcessWorkerResult(
        receipt_class=receipt_class,
        reason=reason,
        evidence_sha256=evidence_sha256,
        artifacts=artifacts,
        receipt_path=receipt_path,
        packet_path=packet_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        returncode=returncode,
        timed_out=timed_out,
        replayed=replayed,
    )


def run_process_worker(
    *,
    packet: Mapping[str, object],
    spec: ProcessWorkerSpec,
    workspace_root: Path,
) -> ProcessWorkerResult:
    """Execute one exact delivery through an operator-configured process.

    The process is never invoked through a shell. The immutable packet and
    receipt paths are appended as explicit argv entries. A durable receipt is
    replayed instead of re-executing the same fenced delivery.
    """

    advisory_only = packet.get("advisory_only") is True
    if advisory_only:
        if packet.get("execution_authorized") is not False:
            raise ValueError(
                "advisory process worker must explicitly deny execution authority"
            )
        if packet.get("execution_effect_class") is not None:
            raise ValueError(
                "advisory process worker must not carry an execution effect class"
            )
        if packet.get("execution_promotion") is not None:
            raise ValueError(
                "advisory process worker must not carry execution promotion"
            )
    else:
        if packet.get("execution_authorized") is not True:
            raise ValueError("process worker requires promoted execution")
        if packet.get("execution_effect_class") != "NO_PROTECTED_EFFECT":
            raise ValueError(
                "process worker only accepts NO_PROTECTED_EFFECT execution"
            )
        promotion = packet.get("execution_promotion")
        if not isinstance(promotion, Mapping):
            raise ValueError("process worker requires execution promotion binding")
        for key in (
            "promotion_sha256",
            "review_sha256",
            "execution_grant_sha256",
        ):
            value = promotion.get(key)
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ValueError(
                    f"process worker promotion binding has invalid {key}"
                )

    if packet.get("protected_effects_authorized") is not False:
        raise ValueError("process worker packet must deny protected effects")
    if packet.get("source_mutation_authorized") is not False:
        raise ValueError("process worker packet must deny source mutation")
    if packet.get("target_ref_mutation_authorized") is not False:
        raise ValueError("process worker packet must deny target ref mutation")

    identity = _delivery_identity(packet)
    workspace = Path(workspace_root) / identity
    workspace.mkdir(parents=True, exist_ok=True)
    packet_path = workspace / "packet.json"
    receipt_path = workspace / "receipt.json"
    stdout_path = workspace / "stdout.log"
    stderr_path = workspace / "stderr.log"

    packet_record = dict(packet)

    if packet_path.exists():
        try:
            existing = json.loads(packet_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("existing worker packet is invalid") from exc
        if existing != packet_record:
            raise ValueError(
                "worker workspace already binds a different delivery packet"
            )
    else:
        _write_json_atomic(packet_path, packet_record)

    if receipt_path.exists():
        return _result_from_receipt(
            receipt_path=receipt_path,
            packet_path=packet_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            workspace=workspace,
            returncode=None,
            timed_out=False,
            replayed=True,
        )

    argv = (
        *spec.command,
        "--portal-packet",
        str(packet_path),
        "--portal-receipt",
        str(receipt_path),
    )
    env = _minimal_environment(
        pass_env=spec.pass_env,
        workspace=workspace,
    )

    returncode: int | None = None
    timed_out = False
    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        try:
            completed = subprocess.run(
                argv,
                cwd=workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                shell=False,
                timeout=float(spec.timeout_seconds),
                check=False,
            )
            returncode = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _write_json_atomic(
                receipt_path,
                _synthetic_receipt(
                    "worker process timed out; outcome is unknown and is not replayed"
                ),
            )
        except OSError as exc:
            _write_json_atomic(
                receipt_path,
                _synthetic_receipt(
                    f"worker process launch failed: {type(exc).__name__}"
                ),
            )

    if not receipt_path.exists():
        _write_json_atomic(
            receipt_path,
            _synthetic_receipt(
                "worker process exited without a durable receipt; outcome is unknown"
            ),
        )

    return _result_from_receipt(
        receipt_path=receipt_path,
        packet_path=packet_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        workspace=workspace,
        returncode=returncode,
        timed_out=timed_out,
        replayed=False,
    )
