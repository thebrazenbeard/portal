from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_FILES = {
    "project": _ROOT / "schemas" / "project.schema.json",
    "worker": _ROOT / "schemas" / "worker.schema.json",
    "observation": _ROOT / "schemas" / "observation.schema.json",
    "dependency": _ROOT / "schemas" / "dependency.schema.json",
    "frontier": _ROOT / "schemas" / "frontier.schema.json",
    "work-unit": _ROOT / "schemas" / "work-unit.schema.json",
    "portfolio-corpus": _ROOT / "schemas" / "portfolio-corpus.schema.json",
    "portfolio-advancement": _ROOT / "schemas" / "portfolio-advancement.schema.json",
    "p0-project-manifest": _ROOT / "schemas" / "p0-project-manifest.schema.json",
}


def validate_document(schema_name: str, payload: Any) -> None:
    try:
        schema_path = _SCHEMA_FILES[schema_name]
    except KeyError as exc:
        raise ValueError(f"unknown schema: {schema_name}") from exc
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)
