"""Bind a passing comparison to stopped image bytes and versioned inputs."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from runner_images.compare import compare, load


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("config.json", "disk.img", "nvram.bin"):
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError("image fingerprint requires complete regular VM files")
        result[name] = digest(path)
    return result


def read_object(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return cast(dict[str, object], value)


def checked_fingerprint(path: Path) -> dict[str, object]:
    value = read_object(path)
    if set(value) != {"config.json", "disk.img", "nvram.bin"} or not all(
        isinstance(v, str) and re.fullmatch("[a-f0-9]{64}", v) for v in value.values()
    ):
        raise ValueError("invalid image fingerprint")
    return value


def qualify(bundle: Path, image_directory: Path, metadata: dict[str, str]) -> None:
    if metadata.get("profile") not in ("xcode", "xcode-android"):
        raise ValueError("unknown image profile")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", metadata.get("image", "")):
        raise ValueError("invalid image name")
    for field in ("source_commit", "reference_commit"):
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", metadata.get(field, "")):
            raise ValueError("qualification requires exact Git revisions")
    result = compare(
        load(bundle / "reference.json"),
        load(bundle / "inventory.json"),
        bundle / "accepted-missing.txt",
    )
    (bundle / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    if not result["compatible"]:
        raise ValueError("image comparison failed")
    if not (bundle / "toolchain-check.txt").stat().st_size:
        raise ValueError("native toolchain verification evidence is empty")
    receipt = {
        "schema_version": 1,
        "status": "comparison-passed",
        "build": metadata,
        "base_files": checked_fingerprint(bundle / "base-fingerprint.json"),
        "image_files": fingerprint(image_directory),
        # Tart clone assigns a new MAC. Bind every other configuration field
        # so a consumer can verify the clone without requiring that MAC.
        "clone_config_sha256": hashlib.sha256(
            json.dumps(
                {
                    k: v
                    for k, v in read_object(image_directory / "config.json").items()
                    if k != "macAddress"
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "inventory_sha256": digest(bundle / "inventory.json"),
        "reference_sha256": digest(bundle / "reference.json"),
        "allowlist_sha256": digest(bundle / "accepted-missing.txt"),
        "toolset_sha256": digest(bundle / "toolset.json"),
        "toolchain_check_sha256": digest(bundle / "toolchain-check.txt"),
        "comparison": result,
    }
    if (bundle / "sbom.spdx.json").exists():
        sbom = read_object(bundle / "sbom.spdx.json")
        if sbom.get("spdxVersion") != "SPDX-2.3" or not sbom.get("packages"):
            raise ValueError("empty or invalid image SBOM")
        receipt["sbom_sha256"] = digest(bundle / "sbom.spdx.json")
    target = bundle / "comparison-passed.json"
    if target.exists():
        raise ValueError("qualification receipt already exists")
    target.write_text(json.dumps(receipt, indent=2) + "\n")


def finalize(bundle: Path, image_directory: Path) -> None:
    receipt = read_object(bundle / "comparison-passed.json")
    if receipt.get("status") != "comparison-passed" or receipt.get(
        "image_files"
    ) != fingerprint(image_directory):
        raise ValueError("published image no longer matches the verified bytes")
    target = bundle / "qualification.json"
    if target.exists():
        raise ValueError("qualification receipt already exists")
    receipt["status"] = "passed"
    receipt["qualified_at"] = datetime.now(UTC).isoformat()
    target.write_text(json.dumps(receipt, indent=2) + "\n")
    (bundle / "comparison-passed.json").unlink()


def record(bundle: Path, repository: Path, source_commit: str) -> Path:
    receipt = read_object(bundle / "qualification.json")
    build = receipt.get("build")
    if not isinstance(build, dict):
        raise ValueError("invalid build metadata")
    build = cast(dict[str, object], build)
    profile = build.get("profile")
    if profile not in ("xcode", "xcode-android"):
        raise ValueError("unknown image profile")
    if (
        type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "passed"
        or build.get("source_commit") != source_commit
        or build.get("reference_commit") != source_commit
    ):
        raise ValueError("qualification is not for the trusted source revision")
    image = build.get("image")
    run_url = build.get("run_url")
    match = (
        re.fullmatch(f"macos-26-{profile}-([0-9]+)-([0-9]+)", image)
        if isinstance(image, str)
        else None
    )
    local_build = (
        isinstance(image, str)
        and re.fullmatch(f"macos-26-{profile}-local-[a-f0-9]{{32}}", image)
        and run_url == ""
    )
    if not local_build and (
        not match
        or run_url
        != f"https://github.com/circlesac/runner-images/actions/runs/{match.group(1)}"
    ):
        raise ValueError("only this workflow's public image builds can be recorded")
    if build.get("base") != "ghcr.io/cirruslabs/macos-tahoe-xcode:latest":
        raise ValueError("only the public base recipe can be recorded here")
    runner_version = build.get("runner_version")
    qualified_at = receipt.get("qualified_at")
    if not isinstance(runner_version, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", runner_version
    ):
        raise ValueError("invalid runner version")
    if not isinstance(qualified_at, str):
        raise ValueError("qualification time is missing")
    when = datetime.fromisoformat(qualified_at)
    if when.tzinfo is None:
        raise ValueError("qualification time requires a timezone")
    when = when.astimezone(ZoneInfo("Asia/Seoul"))
    for name, field, trusted in (
        ("inventory.json", "inventory_sha256", None),
        (
            "reference.json",
            "reference_sha256",
            repository / "snapshots/github-hosted/macos-26.json",
        ),
        (
            "accepted-missing.txt",
            "allowlist_sha256",
            repository / f"macos/accepted-missing/{profile}.txt",
        ),
        ("toolset.json", "toolset_sha256", None),
        ("toolchain-check.txt", "toolchain_check_sha256", None),
    ):
        actual = digest(bundle / name)
        if actual != receipt.get(field) or (trusted and actual != digest(trusted)):
            raise ValueError("qualification inputs were changed or are untrusted")
    result = compare(
        load(bundle / "reference.json"),
        load(bundle / "inventory.json"),
        bundle / "accepted-missing.txt",
    )
    if not result["compatible"] or result != receipt.get("comparison"):
        raise ValueError("qualification comparison is not reproducible")
    for field in ("base_files", "image_files"):
        values = receipt.get(field)
        if not isinstance(values, dict):
            raise ValueError("invalid image identity")
        values = cast(dict[str, object], values)
        if set(values) != {
            "config.json",
            "disk.img",
            "nvram.bin",
        }:
            raise ValueError("incomplete image identity")
        if not all(
            isinstance(v, str) and re.fullmatch("[a-f0-9]{64}", v)
            for v in values.values()
        ):
            raise ValueError("invalid image identity")
    if (
        "sbom_sha256" in receipt
        and digest(bundle / "sbom.spdx.json") != receipt["sbom_sha256"]
    ):
        raise ValueError("image SBOM was changed")
    # A content-addressed directory preserves every verified build without overwriting.
    identity = digest(bundle / "qualification.json")
    destination = repository / f"qualifications/tart/{profile}/{identity}"
    files = (
        "qualification.json",
        "inventory.json",
        "toolset.json",
        "toolchain-check.txt",
    )
    if "sbom_sha256" in receipt:
        files += ("sbom.spdx.json",)
    for name in files:
        target = destination / name
        content = (bundle / name).read_bytes()
        if target.exists() and target.read_bytes() != content:
            raise ValueError("qualification record is immutable")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    build_link = f"[Actions run]({run_url})" if run_url else "local Apple Silicon Mac"
    summary = (
        f"# Verified Tart image\n\n"
        f"- Image: `{image}` ({profile})\n"
        f"- Qualified: {when:%Y-%m-%d %H:%M:%S} KST\n"
        "- Recipe: [source commit]"
        f"(https://github.com/circlesac/runner-images/tree/{source_commit})\n"
        f"- Reference revision: `{source_commit}`\n"
        f"- Build: {build_link}\n"
        f"- GitHub runner: {runner_version}\n\n"
        "Native checks and the reviewed capability comparison passed. "
        "The stopped image files matched their comparison-time SHA256 hashes. "
        "This is a local image on the builder; registry publication is separate.\n\n"
        "[Receipt and hashes](qualification.json) · [Inventory](inventory.json) "
        "· [Native checks](toolchain-check.txt) · [Toolset](toolset.json)\n"
    )
    (destination / "README.md").write_text(summary)
    return destination
