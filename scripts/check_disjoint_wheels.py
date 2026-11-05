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


_SCHEME_DESTINATIONS = {
    "purelib": "site-packages",
    "platlib": "site-packages",
    "scripts": "scripts",
    "data": "prefix-data",
    "headers": "headers",
}

# pip/distlib's Windows entry-point wrapper uses name.exe. Reserve the other
# common shell wrapper spellings conservatively: this is an ownership gate,
# not an assertion about which files a given installer will generate.
_WINDOWS_COMMAND_SUFFIXES = (".exe", ".cmd", ".bat", ".ps1", "-script.py", "-script.pyw")


def _installed_destination(archive_path: str, data_stem: str) -> str:
    # Wheels are ZIP files with forward-slash paths. Reject ambiguous Windows
    # aliases, traversal, and backslashes rather than silently normalizing them.
    parts = archive_path.split("/")
    if (archive_path.startswith("/") or "\\" in archive_path or
            any(not part or part in {".", ".."} or part[-1] in {" ", "."}
                or any(c in part for c in ':*?"<>|\x00') for part in parts)):
        raise ValueError(f"unsafe archive path {archive_path!r}")

    if parts[0] == f"{data_stem}.data":
        if len(parts) < 3 or parts[1] not in _SCHEME_DESTINATIONS:
            raise ValueError(f"unsupported wheel .data relocation {archive_path!r}")
        return "/".join((_SCHEME_DESTINATIONS[parts[1]], *parts[2:])).casefold()
    return f"site-packages/{archive_path}".casefold()


def _read_wheel(path: Path) -> tuple[set[str], set[str], str, dict[str, str], list[str]]:
    with ZipFile(path) as archive:
        filenames = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        names = set(filenames)
        metadata = [name for name in filenames if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError(f"{path}: expected one wheel METADATA, found {len(metadata)}")
        distribution = Parser().parsestr(archive.read(metadata[0]).decode())["Name"]
        data_stem = metadata[0].removesuffix(".dist-info/METADATA")
        entry_file = metadata[0].removesuffix("METADATA") + "entry_points.txt"
        entrypoints: dict[str, str] = {}
        if entry_file in names:
            parser = configparser.ConfigParser()
            parser.optionxform = str  # preserve case so Windows aliases can be tested
            parser.read_string(archive.read(entry_file).decode())
            entrypoints = dict(parser.items("console_scripts")) if parser.has_section("console_scripts") else {}

        installed: set[str] = set()
        problems: list[str] = []
        for name in filenames:
            try:
                destination = _installed_destination(name, data_stem)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            if destination in installed:
                problems.append(f"duplicate installed destination {destination!r} from {name!r}")
            installed.add(destination)
        for command in entrypoints:
            if any(c in command for c in "/\\:*?\"<>|\x00") or command.endswith((" ", ".")):
                problems.append(f"unsafe generated CLI name {command!r}")
                continue
            for alias in ("", *_WINDOWS_COMMAND_SUFFIXES):
                destination = f"scripts/{command.casefold()}{alias}"
                if destination in installed:
                    problems.append(f"generated CLI conflicts with installed destination {destination!r}")
                installed.add(destination)
        return names, installed, distribution, entrypoints, problems


def assess_wheels(wheels: dict[str, Path]) -> dict[str, object]:
    packages = {}
    problems: list[str] = []
    for role, (expected_name, *required_files) in EXPECTED.items():
        names, installed, distribution, scripts, wheel_problems = _read_wheel(wheels[role])
        packages[role] = (names, installed, scripts)
        problems.extend(f"{role}: {problem}" for problem in wheel_problems)
        if distribution != expected_name:
            problems.append(f"{role}: distribution {distribution!r}, expected {expected_name!r}")
        for required in required_files:
            if required not in names:
                problems.append(f"{role}: missing owned path {required}")

    overlaps: dict[str, list[str]] = {}
    for left, right in (("portal", "runner"), ("portal", "mono"), ("runner", "mono")):
        common = sorted(packages[left][1] & packages[right][1])
        if common:
            overlaps[f"{left}/{right}"] = common
            problems.append(f"{left}/{right}: {len(common)} installed destination collisions")

    commands: dict[str, str] = {}
    for role, (_, _, scripts) in packages.items():
        for name, target in scripts.items():
            if name.casefold() in commands:
                problems.append(f"CLI command {name!r} claimed by multiple distributions")
            commands[name.casefold()] = f"{role}:{target}"
    expected_commands = {"portal", "portal-desktop", "project-runner", "vera-mono"}
    for missing in sorted(expected_commands - commands.keys()):
        problems.append(f"missing CLI command {missing!r}")

    return {
        "schema": "PORTAL_DISJOINT_WHEELS_REVIEW_V2",
        "qualified_source_ownership": not problems,
        "archive_path_counts": {role: len(paths) for role, (paths, _, _) in packages.items()},
        "installed_destination_counts": {role: len(paths) for role, (_, paths, _) in packages.items()},
        "installed_destination_collisions": overlaps,
        "commands": commands,
        "problems": problems,
        "limits": "Models wheel purelib/platlib/scripts/data/headers destinations and Windows case aliases; does not prove dependencies, runtime behavior, GUI, install order, or host admission",
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
