from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def test_setuptools_explicitly_packages_only_runner():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["setuptools"]["packages"] == ["runner"]
