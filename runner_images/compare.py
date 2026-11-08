"""Fail closed on incomplete inventories and unreviewed capability omissions."""

import json
import re
from pathlib import Path
from typing import cast

from runner_images.inventory import Inventory


def validate_record(record: object) -> str:
    if not isinstance(record, str) or not re.fullmatch(
        r"(?:(?:cmd|cache) [^/\s\x00-\x1f]+|env [A-Za-z_][A-Za-z0-9_]*)", record
    ):
        raise ValueError("invalid capability record")
    return record


def load(path: Path) -> Inventory:
    data: object = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("inventory must be an object")
    data = cast(dict[str, object], data)
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        raise ValueError("unsupported inventory schema")
    for field in ("os", "arch", "image_os", "image_version"):
        if not isinstance(data.get(field), str):
            raise ValueError(f"invalid inventory {field}")
    if data["os"] not in ("linux", "darwin") or data["arch"] not in ("arm64", "x86_64"):
        raise ValueError("unsupported inventory platform")
    records = data.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("inventory has no capability records")
    checked = [validate_record(record) for record in cast(list[object], records)]
    if not any(record.startswith("cmd ") for record in checked):
        raise ValueError("inventory has no executable commands")
    if checked != sorted(set(checked)):
        raise ValueError("inventory records must be sorted and unique")
    return Inventory(
        schema_version=1,
        os=cast(str, data["os"]),
        arch=cast(str, data["arch"]),
        image_os=cast(str, data["image_os"]),
        image_version=cast(str, data["image_version"]),
        records=checked,
    )


def compare(
    reference: Inventory, candidate: Inventory, allowlist: Path | None = None
) -> dict[str, object]:
    if (reference["os"], reference["arch"]) != (candidate["os"], candidate["arch"]):
        raise ValueError("reference and candidate OS/architecture must match")
    accepted: set[str] = set()
    if allowlist is not None:
        accepted = {
            validate_record(line) for line in allowlist.read_text().splitlines() if line
        }
    missing = set(reference["records"]) - set(candidate["records"])
    unexpected = sorted(missing - accepted)
    stale = sorted(accepted - missing)
    return {
        "compatible": not unexpected and not stale,
        "missing": sorted(missing),
        "unexpected_missing": unexpected,
        "stale_allowlist": stale,
        "extra": sorted(set(candidate["records"]) - set(reference["records"])),
    }
