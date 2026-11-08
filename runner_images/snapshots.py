"""Persist a complete hosted observation set as deterministic versioned JSON."""

import json
from pathlib import Path

from runner_images.compare import load


def update(source: Path, destination: Path) -> list[str]:
    pending: dict[Path, str] = {}
    for label, operating_system, architecture in (
        ("ubuntu-24.04", "linux", "x86_64"),
        ("macos-26", "darwin", "arm64"),
    ):
        inventory = load(source / f"reference-{label}" / "inventory.json")
        if (inventory["os"], inventory["arch"]) != (operating_system, architecture):
            raise ValueError("reference observation does not match its label")
        if not inventory["image_os"] or not inventory["image_version"]:
            raise ValueError("hosted reference must identify its image")
        pending[destination / f"{label}.json"] = json.dumps(inventory, indent=2) + "\n"

    # Validate the whole matrix before replacing any previous observation.
    changed: list[str] = []
    for path, content in pending.items():
        if path.exists() and path.read_text() == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(content)
        temporary.replace(path)
        changed.append(path.name)
    return changed
