"""Desktop admission guard: do not turn UI clicks into unsafe dispatch.

Pins must be explicitly supplied by an operator rather than derived from
the executable under test. This only checks consistency, not independent
attestation or trusted code origins.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping

_HEX = re.compile(r"^[a-f0-9]{64}$")
_KEYS = ("command_sha256","interpreter_sha256","worker_sha256")

@dataclass(frozen=True)
class DesktopActionDecision:
    allowed: bool
    explanation: str
    payload: dict[str,str]

def load_operator_pins(path: Path) -> dict[str,str]:
    """Read operator-selected pins; do not compute them from installed files."""
    if not isinstance(path, Path) or not path.is_file():
        raise ValueError("operator pins file is missing")
    try:
        record=json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("operator pins file cannot be read") from exc
    source=record.get("pins") if isinstance(record,dict) else None
    if (not isinstance(source,dict) or set(source)!=set(_KEYS)
        or any(not isinstance(source.get(key),str)
               or not _HEX.fullmatch(source[key]) for key in _KEYS)):
        raise ValueError("operator pins must contain three lowercase SHA-256 digests")
    return {"expected_"+key: source[key] for key in _KEYS}

def prepare_portfolio_request(
    action: str, snapshot: Mapping[str,object] | None,
    *, pins: dict[str,str] | None,
) -> DesktopActionDecision:
    """Guard UI run/continue against replay and unpinned resident workers."""
    if action not in {"run","continue"}:
        return DesktopActionDecision(True,"",{})
    data=snapshot or {}
    session=data.get("session")
    if isinstance(session,dict):
        current=str(session.get("control_state") or "")
        subjects=session.get("subjects",[])
        if action=="run" and current=="RUNNING":
            return DesktopActionDecision(False,
                "Portfolio already running. Refresh status or use Continue; do not restart its generation.",{})
        if action=="continue" and current!="RUNNING":
            return DesktopActionDecision(False,
                "Portfolio is stopped or unavailable. Continue cannot restart it; review its held work and profile.",{})
        if (isinstance(subjects,list) and subjects
            and all(isinstance(item,dict)
                    and item.get("state") in {"HELD","COMPLETED","CANCELLED","STOPPED"}
                    for item in subjects)):
            return DesktopActionDecision(False,
                "No runnable work: the current portfolio is held or complete. Admit a new, verified subject before running again.",{})
    if data.get("worker_state")=="CONFIGURED":
        if not isinstance(pins,dict) or any(
            not isinstance(pins.get("expected_"+key),str)
            or not _HEX.fullmatch(pins["expected_"+key]) for key in _KEYS
        ):
            return DesktopActionDecision(False,
                "Worker SHA-256 pins are missing. Select an operator-pinned manifest; the UI cannot invent trusted hashes.",{})
        return DesktopActionDecision(True,"Ready for pinned source-only proposal request.",
            {("expected_"+key):pins["expected_"+key] for key in _KEYS})
    return DesktopActionDecision(True,"Admission-only; no executable proposal worker is configured.",{})
