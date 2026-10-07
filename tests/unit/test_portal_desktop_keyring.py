from __future__ import annotations

from pathlib import Path

import pytest

import portal.desktop_keyring as desktop_keyring


def _protect(secret: bytes, entropy: bytes) -> bytes:
    return b"wrapped:" + entropy + b":" + secret[::-1]


def _unprotect(blob: bytes, entropy: bytes) -> bytes:
    prefix = b"wrapped:" + entropy + b":"
    assert blob.startswith(prefix)
    return blob[len(prefix):][::-1]


def test_key_store_provisions_encrypted_user_bound_key_without_plaintext(
    tmp_path: Path,
) -> None:
    store_cls = getattr(desktop_keyring, "DesktopAuthorityKeyStore", None)
    assert callable(store_cls)

    store = store_cls(
        tmp_path,
        protect=_protect,
        unprotect=_unprotect,
        random_bytes=lambda size: bytes(range(size)),
    )
    metadata = store.provision("execution")

    assert metadata["kind"] == "execution"
    assert metadata["created"] is True
    assert metadata["rotated"] is False
    assert metadata["bytes"] == 32
    path = Path(metadata["path"])
    assert path.is_file()
    plaintext = bytes(range(32))
    assert plaintext not in path.read_bytes()
    assert store.load("execution") == plaintext


def test_key_store_refuses_overwrite_without_rotate(tmp_path: Path) -> None:
    store = desktop_keyring.DesktopAuthorityKeyStore(
        tmp_path,
        protect=_protect,
        unprotect=_unprotect,
        random_bytes=lambda size: b"a" * size,
    )
    store.provision("execution")

    with pytest.raises(FileExistsError, match="already exists"):
        store.provision("execution")


def test_key_store_rotation_replaces_only_requested_key_kind(
    tmp_path: Path,
) -> None:
    values = iter([b"a" * 32, b"b" * 32, b"c" * 32])
    store = desktop_keyring.DesktopAuthorityKeyStore(
        tmp_path,
        protect=_protect,
        unprotect=_unprotect,
        random_bytes=lambda _size: next(values),
    )
    store.provision("execution")
    store.provision("protected_effect")
    rotated = store.provision("execution", rotate=True)

    assert rotated["rotated"] is True
    assert store.load("execution") == b"c" * 32
    assert store.load("protected_effect") == b"b" * 32


def test_key_store_missing_key_returns_none(tmp_path: Path) -> None:
    store = desktop_keyring.DesktopAuthorityKeyStore(
        tmp_path,
        protect=_protect,
        unprotect=_unprotect,
    )

    assert store.load("execution") is None
    assert store.load("protected_effect") is None


def test_key_store_rejects_review_key_custody(tmp_path: Path) -> None:
    store = desktop_keyring.DesktopAuthorityKeyStore(
        tmp_path,
        protect=_protect,
        unprotect=_unprotect,
    )

    with pytest.raises(ValueError, match="review"):
        store.provision("review")


def test_key_store_status_never_returns_key_material(tmp_path: Path) -> None:
    store = desktop_keyring.DesktopAuthorityKeyStore(
        tmp_path,
        protect=_protect,
        unprotect=_unprotect,
        random_bytes=lambda size: b"z" * size,
    )
    store.provision("execution")

    status = store.status()

    assert status == {
        "execution": True,
        "protected_effect": False,
    }
    assert b"z" * 32 not in repr(status).encode("utf-8")


def test_keyring_cli_status_and_provision_never_emit_secret_material(
    tmp_path: Path,
    capsys,
) -> None:
    calls: list[tuple[str, bool]] = []

    class FakeStore:
        def status(self) -> dict[str, bool]:
            return {"execution": False, "protected_effect": True}

        def provision(
            self,
            kind: str,
            *,
            rotate: bool = False,
        ) -> dict[str, object]:
            calls.append((kind, rotate))
            return {
                "kind": kind,
                "path": str(tmp_path / f"{kind}.dpapi"),
                "created": True,
                "rotated": rotate,
                "bytes": 32,
            }

    factory = lambda _root: FakeStore()

    assert desktop_keyring.entrypoint(
        ["--runtime-root", str(tmp_path), "status"],
        store_factory=factory,
    ) == 0
    status = capsys.readouterr().out
    assert '"execution": false' in status.lower()
    assert '"protected_effect": true' in status.lower()

    assert desktop_keyring.entrypoint(
        [
            "--runtime-root",
            str(tmp_path),
            "provision",
            "execution",
            "--rotate",
        ],
        store_factory=factory,
    ) == 0
    provision = capsys.readouterr().out
    assert calls == [("execution", True)]
    assert '"created": true' in provision.lower()
    assert "authority key" not in provision.lower()
