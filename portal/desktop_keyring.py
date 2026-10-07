"""Windows user-bound key custody for P.O.R.T.A.L. Desktop authority."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
from typing import Callable, Sequence


_KEY_KINDS = frozenset({"execution", "protected_effect"})
_KEY_BYTES = 32


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, object]:
    if not value:
        return _DataBlob(0, None), None
    buffer = ctypes.create_string_buffer(value)
    return (
        _DataBlob(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        ),
        buffer,
    )


def _windows_dpapi_protect(secret: bytes, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    data_blob, data_buffer = _blob_from_bytes(secret)
    entropy_blob, entropy_buffer = _blob_from_bytes(entropy)
    output = _DataBlob()
    _ = data_buffer, entropy_buffer

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(data_blob),
        "P.O.R.T.A.L. Desktop authority key",
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)


def _windows_dpapi_unprotect(blob: bytes, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    data_blob, data_buffer = _blob_from_bytes(blob)
    entropy_blob, entropy_buffer = _blob_from_bytes(entropy)
    output = _DataBlob()
    description = wintypes.LPWSTR()
    _ = data_buffer, entropy_buffer

    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(data_blob),
        ctypes.byref(description),
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if description:
            kernel32.LocalFree(description)
        if output.pbData:
            kernel32.LocalFree(output.pbData)


Protect = Callable[[bytes, bytes], bytes]
Unprotect = Callable[[bytes, bytes], bytes]
RandomBytes = Callable[[int], bytes]


class DesktopAuthorityKeyStore:
    """Store execution/effect keys as current-user DPAPI ciphertext.

    Review evidence is deliberately excluded so user approval and independent
    review do not collapse into the same local key-custody surface.
    """

    def __init__(
        self,
        root: Path,
        *,
        protect: Protect = _windows_dpapi_protect,
        unprotect: Unprotect = _windows_dpapi_unprotect,
        random_bytes: RandomBytes = secrets.token_bytes,
    ) -> None:
        self.root = Path(root)
        self._protect = protect
        self._unprotect = unprotect
        self._random_bytes = random_bytes

    @staticmethod
    def _validate_kind(kind: str) -> str:
        normalized = str(kind).strip()
        if normalized == "review":
            raise ValueError(
                "review authority key custody is intentionally excluded "
                "from P.O.R.T.A.L. Desktop"
            )
        if normalized not in _KEY_KINDS:
            raise ValueError(
                "authority key kind must be execution or protected_effect"
            )
        return normalized

    def _path(self, kind: str) -> Path:
        normalized = self._validate_kind(kind)
        return self.root / f"{normalized}.dpapi"

    @staticmethod
    def _entropy(kind: str) -> bytes:
        return (
            f"portal-desktop-authority-key:{kind}:v1"
        ).encode("utf-8")

    def provision(
        self,
        kind: str,
        *,
        rotate: bool = False,
    ) -> dict[str, object]:
        normalized = self._validate_kind(kind)
        path = self._path(normalized)
        if path.exists() and not rotate:
            raise FileExistsError(
                f"{normalized} authority key already exists"
            )

        secret = self._random_bytes(_KEY_BYTES)
        if not isinstance(secret, bytes) or len(secret) != _KEY_BYTES:
            raise ValueError(
                "authority key generator must return exactly 32 bytes"
            )
        encrypted = self._protect(
            secret,
            self._entropy(normalized),
        )
        if not isinstance(encrypted, bytes) or not encrypted:
            raise ValueError("authority key protection returned no ciphertext")
        if encrypted == secret:
            raise ValueError("authority key protection returned plaintext")

        self.root.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(encrypted)
        os.replace(temp, path)
        return {
            "kind": normalized,
            "path": str(path),
            "created": True,
            "rotated": bool(rotate),
            "bytes": _KEY_BYTES,
        }

    def load(self, kind: str) -> bytes | None:
        normalized = self._validate_kind(kind)
        path = self._path(normalized)
        if not path.is_file():
            return None
        encrypted = path.read_bytes()
        if not encrypted:
            raise ValueError(
                f"{normalized} authority key ciphertext is empty"
            )
        secret = self._unprotect(
            encrypted,
            self._entropy(normalized),
        )
        if not isinstance(secret, bytes) or len(secret) != _KEY_BYTES:
            raise ValueError(
                f"{normalized} authority key plaintext is invalid"
            )
        return secret

    def status(self) -> dict[str, bool]:
        return {
            kind: self._path(kind).is_file()
            for kind in ("execution", "protected_effect")
        }



StoreFactory = Callable[[Path], DesktopAuthorityKeyStore]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portal-desktop-authority-key",
        description=(
            "Provision or inspect current-user encrypted P.O.R.T.A.L. Desktop "
            "execution/protected-effect key custody."
        ),
    )
    parser.add_argument("--runtime-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "status",
        help="show whether execution/effect ciphertexts exist",
    )
    provision = commands.add_parser(
        "provision",
        help=(
            "create or rotate one DPAPI-protected authority credential; "
            "does not create review evidence custody"
        ),
    )
    provision.add_argument(
        "kind",
        choices=("execution", "protected_effect"),
    )
    provision.add_argument("--rotate", action="store_true")
    return parser


def entrypoint(
    argv: Sequence[str] | None = None,
    *,
    store_factory: StoreFactory = DesktopAuthorityKeyStore,
) -> int:
    args = _parser().parse_args(argv)
    root = (
        Path(args.runtime_root).resolve()
        / "state"
        / "portal"
        / "authority-keyring"
    )
    store = store_factory(root)

    if args.command == "status":
        payload: dict[str, object] = {
            "mode": "PORTAL_DESKTOP_AUTHORITY_KEY_STATUS_V1",
            "runtime_root": str(Path(args.runtime_root).resolve()),
            "custody": store.status(),
            "review_key_custody": False,
        }
    elif args.command == "provision":
        result = store.provision(
            args.kind,
            rotate=bool(args.rotate),
        )
        payload = {
            "mode": "PORTAL_DESKTOP_AUTHORITY_KEY_PROVISION_V1",
            "runtime_root": str(Path(args.runtime_root).resolve()),
            **result,
            "review_key_custody": False,
        }
    else:
        raise AssertionError(f"unsupported command: {args.command}")

    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


def main() -> None:
    raise SystemExit(entrypoint())
