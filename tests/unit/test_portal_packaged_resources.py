"""Prevent source/installed-wheel drift in bundled, read-only inputs."""

from hashlib import sha256
from pathlib import Path

from portal._resources import resource_root


def test_bundle_is_byte_exact_copy_of_checkout_runtime_resources() -> None:
    checkout = Path(__file__).resolve().parents[2]
    bundle = checkout / "portal" / "_data"
    patterns = {
        "registry": "*.yaml",
        "policy": "*.yaml",
        "topology": "*.yaml",
        "portfolio": "*.json",
        "schemas": "*.json",
    }
    for subdir, pattern in patterns.items():
        source_files = sorted(path.name for path in (checkout / subdir).glob(pattern))
        bundled_files = sorted(path.name for path in (bundle / subdir).glob(pattern))
        assert source_files, subdir
        assert bundled_files == source_files, subdir
        for name in source_files:
            source = (checkout / subdir / name).read_bytes()
            copy = (bundle / subdir / name).read_bytes()
            assert sha256(copy).digest() == sha256(source).digest(), (
                subdir,
                name,
            )


def test_runtime_uses_bundled_data_even_in_checkout() -> None:
    checkout = Path(__file__).resolve().parents[2]
    assert resource_root() == checkout / "portal" / "_data"
