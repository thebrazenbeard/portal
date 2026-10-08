from pathlib import Path
import subprocess
import sys

from portal.codex_gh_worker import _checked


def test_checked_decodes_utf8_output_from_real_process(tmp_path: Path):
    script = tmp_path / "utf8_probe.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(bytes([0xE2, 0x80, 0x9D]))\n"
        "sys.stderr.buffer.write(bytes([0xE2, 0x80, 0x9D]))\n",
        encoding="utf-8",
    )
    completed = _checked(
        subprocess.run,
        (sys.executable, str(script)),
        timeout_seconds=10,
    )
    assert completed.stdout == "\u201d"
    assert completed.stderr == "\u201d"
