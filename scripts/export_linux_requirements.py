"""Print the Linux CPython 3.11 runtime requirement closure of this project.

The closure is derived from the metadata of the packages installed in the
current environment, with environment markers evaluated for x86-64 Linux, so
Windows-only distributions such as pywin32 stay out of the Linux bundle.

    py -3.11 scripts/export_linux_requirements.py > dist/linux-requirements.txt
"""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Iterator

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

LINUX_ENVIRONMENT = {
    "implementation_name": "cpython",
    "platform_machine": "x86_64",
    "platform_system": "Linux",
    "platform_python_implementation": "CPython",
    "python_full_version": "3.11.8",
    "python_version": "3.11",
    "sys_platform": "linux",
    "os_name": "posix",
}


def closure(roots: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    pending: list[tuple[Requirement, frozenset[str]]] = [
        (Requirement(value), frozenset()) for value in roots
    ]
    while pending:
        requirement, extras = pending.pop(0)
        extras = extras | frozenset(requirement.extras)
        name = canonicalize_name(requirement.name)
        if name in resolved:
            continue
        try:
            metadata = distribution(name)
        except PackageNotFoundError:
            raise SystemExit(
                f"{requirement} is not installed in this environment; "
                "install requirements.txt before exporting the Linux closure"
            ) from None
        resolved[name] = f"{name}=={metadata.version}"
        for raw in metadata.requires or []:
            dependency = Requirement(raw)
            marker_extras = sorted(extras) or [""]
            if dependency.marker is not None and not any(
                dependency.marker.evaluate({**LINUX_ENVIRONMENT, "extra": extra})
                for extra in marker_extras
            ):
                continue
            pending.append((dependency, frozenset()))
    return resolved


def requirements_lines(path: Path) -> Iterator[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--requirements",
    type=Path,
    default=Path(__file__).resolve().parents[1] / "requirements.txt",
)
args = parser.parse_args()

pinned_packages = closure(list(requirements_lines(args.requirements)))
for pinned in sorted(pinned_packages.values()):
    print(pinned)
print(f"# {len(pinned_packages)} packages", file=sys.stderr)
