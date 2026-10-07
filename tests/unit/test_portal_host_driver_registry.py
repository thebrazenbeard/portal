from __future__ import annotations

from pathlib import Path

import pytest

from portal.host_command_driver import PortalCommandHostDriver
from portal.host_driver_registry import load_host_command_drivers


def test_load_host_command_drivers_builds_strict_driver_map(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "drivers.yaml"
    manifest.write_text(
        """
schema: PORTAL_HOST_COMMAND_DRIVERS_V1
drivers:
  - adapter_id: workbridge
    command:
      - powershell.exe
      - -NoProfile
      - -File
      - C:\\ops\\workbridge-driver.ps1
    timeout_seconds: 45
    cwd: C:\\ops
  - adapter_id: github
    command:
      - python
      - C:\\ops\\github-driver.py
""".lstrip(),
        encoding="utf-8",
    )

    drivers = load_host_command_drivers(manifest)

    assert set(drivers) == {"github", "workbridge"}
    assert isinstance(drivers["workbridge"], PortalCommandHostDriver)
    assert drivers["workbridge"].command == (
        "powershell.exe",
        "-NoProfile",
        "-File",
        r"C:\ops\workbridge-driver.ps1",
    )
    assert drivers["workbridge"].timeout_seconds == 45.0
    assert drivers["workbridge"].cwd == Path(r"C:\ops")
    assert drivers["github"].cwd is None


@pytest.mark.parametrize(
    "text, match",
    [
        (
            "schema: WRONG\ndrivers: []\n",
            "unsupported host driver manifest schema",
        ),
        (
            "schema: PORTAL_HOST_COMMAND_DRIVERS_V1\ndrivers: {}\n",
            "drivers must be a list",
        ),
        (
            """
schema: PORTAL_HOST_COMMAND_DRIVERS_V1
drivers:
  - adapter_id: workbridge
    command: [python, one.py]
  - adapter_id: workbridge
    command: [python, two.py]
""",
            "duplicate host driver adapter_id",
        ),
        (
            """
schema: PORTAL_HOST_COMMAND_DRIVERS_V1
drivers:
  - adapter_id: workbridge
    command: powershell.exe -File driver.ps1
""",
            "command must be a list",
        ),
        (
            """
schema: PORTAL_HOST_COMMAND_DRIVERS_V1
drivers:
  - adapter_id: workbridge
    command: [python, driver.py]
    unexpected: true
""",
            "unsupported fields",
        ),
    ],
)
def test_load_host_command_drivers_fails_closed(
    tmp_path: Path,
    text: str,
    match: str,
) -> None:
    manifest = tmp_path / "drivers.yaml"
    manifest.write_text(text.lstrip(), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        load_host_command_drivers(manifest)
