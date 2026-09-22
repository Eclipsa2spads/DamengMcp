from __future__ import annotations

import argparse
from collections import deque
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


def read_metadata(wheel: Path) -> dict[str, object]:
    with ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        message = BytesParser().parsebytes(archive.read(metadata_name))
    return {
        "name": canonicalize_name(message["Name"]),
        "version": Version(message["Version"]),
        "requires_python": message.get("Requires-Python"),
        "dependencies": [Requirement(value) for value in message.get_all("Requires-Dist", [])],
        "file": wheel.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("requirements", type=Path)
    args = parser.parse_args()

    packages: dict[str, dict[str, object]] = {}
    for wheel in args.wheelhouse.glob("*.whl"):
        if "win32" in wheel.name or "win_amd64" in wheel.name:
            raise SystemExit(f"Windows wheel found in Linux wheelhouse: {wheel.name}")
        metadata = read_metadata(wheel)
        name = str(metadata["name"])
        if name in packages:
            raise SystemExit(f"Duplicate wheel project: {name}")
        packages[name] = metadata

    environment = default_environment()
    environment.update(
        {
            "implementation_name": "cpython",
            "platform_machine": "x86_64",
            "platform_system": "Linux",
            "python_full_version": "3.11.8",
            "python_version": "3.11",
            "sys_platform": "linux",
        }
    )

    def active(requirement: Requirement, extras: frozenset[str]) -> bool:
        """Evaluate an environment marker the way pip does for the target platform."""
        if requirement.marker is None:
            return True
        return any(
            requirement.marker.evaluate({**environment, "extra": extra})
            for extra in (sorted(extras) or [""])
        )

    roots = [
        Requirement(line.strip())
        for line in args.requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    queue: deque[tuple[Requirement, str]] = deque(
        (item, "requirements.txt") for item in roots if active(item, frozenset())
    )
    visited: set[tuple[str, tuple[str, ...]]] = set()
    resolved: set[str] = set()

    while queue:
        requirement, parent = queue.popleft()
        extras = frozenset(requirement.extras)
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(extras)))
        if key in visited:
            continue
        visited.add(key)
        metadata = packages.get(name)
        if metadata is None:
            raise SystemExit(f"Missing Linux wheel for {requirement} (required by {parent})")
        version = metadata["version"]
        if requirement.specifier and version not in requirement.specifier:
            raise SystemExit(
                f"Wheel version {name}=={version} does not satisfy {requirement} (required by {parent})"
            )
        resolved.add(f"{name}=={version}")
        for dependency in metadata["dependencies"]:
            if active(dependency, extras):
                queue.append((dependency, f"{name}=={version}"))

    print(f"Linux CPython 3.11 dependency closure is complete: {len(resolved)} packages")
    for item in sorted(resolved):
        print(item)


if __name__ == "__main__":
    main()
