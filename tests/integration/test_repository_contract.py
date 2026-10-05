from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_required_m1_repository_files_exist():
    required = [
        "README.md",
        "PROJECT_RUNNER.md",
        ".github/workflows/test.yml",
        "registry/projects.yaml",
        "registry/workers.yaml",
    ]
    assert [p for p in required if not (ROOT / p).exists()] == []
