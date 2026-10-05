import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_reconstruction_repository_schema_pattern_matches_runtime_contract():
    schema = json.loads((ROOT / "schemas" / "worker.schema.json").read_text(encoding="utf-8"))
    pattern = schema["properties"]["workers"]["items"]["properties"]["reconstruction"]["properties"]["repository"]["pattern"]
    compiled = re.compile(pattern)
    assert compiled.fullmatch("thebrazenbeard/project-runner")
    assert compiled.fullmatch("sims/service")
    assert compiled.fullmatch("bad repo/name") is None
    assert compiled.fullmatch("owner/bad repo") is None
