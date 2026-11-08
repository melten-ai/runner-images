"""Observe capabilities without exporting environment values or machine paths."""

import os
import platform
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict


class Inventory(TypedDict):
    schema_version: int
    os: str
    arch: str
    image_os: str
    image_version: str
    records: list[str]


def collect(environ: Mapping[str, str] | None = None) -> Inventory:
    env = os.environ if environ is None else environ
    records: set[str] = set()
    for directory in dict.fromkeys(env.get("PATH", "").split(os.pathsep)):
        if not directory:
            continue
        path = Path(directory)
        if not path.is_dir():
            continue
        for entry in path.iterdir():
            if os.access(entry, os.X_OK) and entry.is_file():
                records.add(f"cmd {entry.name}")

    cache = Path(env.get("RUNNER_TOOL_CACHE", "/opt/hostedtoolcache"))
    for marker in cache.glob("*/*/*.complete"):
        if marker.is_file():
            records.add(f"cache {marker.parent.parent.name}")

    for name in env:
        if not re.search(
            r"^(GITHUB_|RUNNER_|ACTIONS_|INPUT_|STATS_|SUDO_|XDG_|LC_|TERM|LANG|"
            r"CI$|HOME$|PATH$|PWD$|OLDPWD$|SHELL$|SHLVL$|USER$|LOGNAME$|HOSTNAME$|"
            r"MAIL$|__CF_USER_TEXT_ENCODING$|DEBIAN_FRONTEND$|INVENTORY_OUTPUT$|_$)",
            name,
        ):
            records.add(f"env {name}")

    machine = platform.machine().lower()
    return {
        "schema_version": 1,
        "os": platform.system().lower(),
        "arch": {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine),
        "image_os": env.get("ImageOS", ""),
        "image_version": env.get("ImageVersion", ""),
        "records": sorted(records),
    }
