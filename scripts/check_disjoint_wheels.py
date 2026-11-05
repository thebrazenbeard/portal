"""Verify that proposed Portal/ProjectRunner/VeraMono wheels own disjoint paths.

This is a source/package ownership check, not dependency, UI, or host admission.
Run after building all three wheels, before installing them in either order.
"""

from __future__ import annotations

import argparse
import configparser
import json
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile


EXPECTED = {
    "portal": ("portal-coordinator", "portal/__init__.py", "portal/_runner/__init__.py"),
    "runner": ("project-runner", "runner/__init__.py"),
    "mono": ("vera-mono", "vera_core/__init__.py"),
}


def _read_wheel(path: Path) -> tuple[set[str], str, dict[str, str]]:
    with ZipFile(path) as archive:
        names = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError(f"{path}: expected one wheel METADATA, found {len(metadata)}")
        distribution = Parser().parsestr(archive.read(metadata[0]).decode())["Name"]
        entry_file = metadata[0].removesuffix("METADATA") + "entry_points.txt"
        entrypoints: dict[str, str] = {}
        if entry_file in names:
            parser = configparser.ConfigParser()
            parser.read_string(archive.read(entry_file).decode())
            entrypoints = dict(parser.items("console_scripts")) if parser.has_section("console_scripts") else {}
        return names, distribution, entrypoints


def assess_wheels(wheels: dict[str, Path]) -> dict[str, object]:
    packages = {}
    problems: list[str] = []
    for role, (expected_name, *required_files) in EXPECTED.items():
        names, distribution, scripts = _read_wheel(wheels[role])
        packages[role] = (names, scripts)
        if distribution != expected_name:
            problems.append(f"{role}: distribution {distribution!r}, expected {expected_name!r}")
        for required in required_files:
            if required not in names:
                problems.append(f"{role}: missing owned path {required}")

    overlaps: dict[str, list[str]] = {}
    for left, right in (("portal", "runner"), ("portal", "mono"), ("runner", "mono")):
        common = sorted(packages[left][0] & packages[right][0])
        if common:
            overlaps[f"{left}/{right}"] = common
            problems.append(f"{left}/{right}: {len(common)} owned archive path collisions")

    commands: dict[str, str] = {}
    for role, (_, scripts) in packages.items():
        for name, target in scripts.items():
            if name in commands:
                problems.append(f"CLI command {name!r} claimed by multiple distributions")
            commands[name] = f"{role}:{target}"
    expected_commands = {"portal", "portal-desktop", "project-runner", "vera-mono"}
    for missing in sorted(expected_commands - commands.keys()):
        problems.append(f"missing CLI command {missing!r}")

    return {
        "schema": "PORTAL_DISJOINT_WHEELS_REVIEW_V1",
        "qualified_source_ownership": not problems,
        "path_counts": {role: len(paths) for role, (paths, _) in packages.items()},
        "archive_collisions": overlaps,
        "commands": commands,
        "problems": problems,
        "limits": "Does not prove dependencies, runtime behavior, GUI, install order, or host admission",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for role in EXPECTED:
        parser.add_argument(f"--{role}", required=True, type=Path, help=f"{role} wheel file")
    args = parser.parse_args()
    result = assess_wheels({role: getattr(args, role) for role in EXPECTED})
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["qualified_source_ownership"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
