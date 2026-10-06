from pathlib import Path


def public_data_root() -> Path:
    """Resolve shipped public defaults independently of cwd and private overrides."""
    package = Path(__file__).resolve().parent
    bundled = package / "data"
    return bundled if bundled.is_dir() else package.parent
