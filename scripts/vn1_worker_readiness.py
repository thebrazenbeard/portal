"""Read-only PORTAL VN1 worker readiness witness. No dispatch or effect authority."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
BINDING = ROOT / "source-binding.json"
SOURCE = ROOT / "src-exact"
RECEIPT = ROOT / "status" / "latest.json"

def utc_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")

def main() -> int:
    now = datetime.now(timezone.utc)
    binding = json.loads(BINDING.read_text(encoding="utf-8-sig"))
    if binding.get("schema") != "BT2_PORTAL_VN1_SOURCE_SNAPSHOT_V1":
        raise RuntimeError("Unexpected source binding schema")
    expected = binding.get("commit")
    result = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=15,
    )
    observed = result.stdout.strip()
    tools = {key: bool(shutil.which(key)) for key in
             ("git", "node", "python", "go", "ffmpeg", "ffprobe", "sqlite3", "uv", "rg")}
    ready = observed == expected and all(tools.values())
    latest_test = ROOT / "results" / "pytest-exact-git-exitcode.txt"
    test_exit = None
    if latest_test.is_file():
        try:
            test_exit = int(latest_test.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            test_exit = None
    suite_qualified = False
    qual_path = ROOT / "results" / "qualification.json"
    suite_log = ROOT / "results" / "pytest-exact-git.txt"
    if qual_path.is_file() and suite_log.is_file():
        try:
            qualification = json.loads(qual_path.read_text(encoding="utf-8-sig"))
            log_hash = hashlib.sha256(suite_log.read_bytes()).hexdigest()
            suite_qualified = (
                qualification.get("schema") == "BT2_PORTAL_VN1_QUALIFIED_TEST_V1"
                and qualification.get("source_commit") == observed
                and qualification.get("exit_code") == 0
                and qualification.get("test_count") == 753
                and qualification.get("pytest_log_sha256") == log_hash
                and test_exit == 0
            )
        except (OSError, ValueError, TypeError):
            suite_qualified = False
    ready = ready and suite_qualified
    status = {
        "schema": "BT2_PORTAL_VN1_WORKER_READINESS_V1",
        "node_id": "vn1",
        "machine": os.environ.get("COMPUTERNAME", ""),
        "observed_at": utc_iso(now),
        "expires_at": utc_iso(now + timedelta(minutes=10)),
        "state": "READY" if ready else "DEGRADED",
        "source_repository": binding.get("repository"),
        "source_expected_commit": expected,
        "source_observed_commit": observed,
        "source_matches": observed == expected,
        "tools_present": tools,
        "last_full_test_exit": test_exit,
        "last_full_suite_qualified": suite_qualified,
        "nominal_max_parallel": 1,
        "occupancy": "NOT_OBSERVED",
        "route_attached": False,
        "authorized_effects": [],
        "qualified_for_protected_effects": False,
        "receipt_kind": "OBSERVATION_ONLY",
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECEIPT.with_name(RECEIPT.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        tmp.replace(RECEIPT)
    finally:
        tmp.unlink(missing_ok=True)
    print(json.dumps({"state": status["state"], "source_matches": observed == expected,
                      "receipt": str(RECEIPT), "last_full_test_exit": test_exit}))
    return 0 if ready else 2

if __name__ == "__main__":
    sys.exit(main())
