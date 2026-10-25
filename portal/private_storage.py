"""Owner-only local runtime custody for persisted cognition and bridge data.

POSIX permission changes use opened, no-follow file descriptors so a symlink
cannot redirect chmod onto an unrelated target. Windows filesystem ACLs are
not qualified by chmod: installers must provide a private profile/root ACL.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat


def ensure_private_directory(path: Path) -> None:
    path = Path(path)
    if path.is_symlink() or path.is_junction():
        raise ValueError("runtime private directory must not be a symlink or junction")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        if not stat.S_ISDIR(current.st_mode) or current.st_uid != os.geteuid():
            raise PermissionError("runtime private directory must be owner-managed")
        os.fchmod(fd, 0o700)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise PermissionError("runtime private directory permissions not enforced")
    finally:
        os.close(fd)


def ensure_private_file(path: Path, *, create: bool = False) -> None:
    """Seal an owned regular file, optionally creating it exclusively at 0600.

    Call only after securing its parent directory. Does not follow symlinks,
    including when asked to inspect SQLite WAL/SHM sidecars.
    """
    path = Path(path)
    if os.name != "posix":
        if path.is_symlink() or path.is_junction():
            raise ValueError("runtime private file must not be a symlink or junction")
        if create:
            path.touch(exist_ok=True)
        return
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            fd = os.open(path, flags)
    else:
        fd = os.open(path, flags)
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode) or current.st_uid != os.geteuid():
            raise PermissionError("runtime private file must be owner-managed regular file")
        os.fchmod(fd, 0o600)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            raise PermissionError("runtime private file permissions not enforced")
    finally:
        os.close(fd)


def secure_sqlite_sidecars(database: Path) -> None:
    """Seal existing SQLite sidecars; private parent guards new sidecars."""
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            ensure_private_file(sidecar)


def secure_existing_bridge_json(directory: Path) -> None:
    """Tighten legacy request/response JSON after sealing its parent."""
    for path in Path(directory).iterdir():
        if path.name.endswith((".json", ".json.tmp")):
            ensure_private_file(path)
