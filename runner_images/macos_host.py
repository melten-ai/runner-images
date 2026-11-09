"""Prepare a verified Azure CLI cask without copying host credentials to a VM."""

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import quote

from runner_images.rebuild import gh


def guest(vm: str, args: list[str]) -> str:
    return subprocess.check_output(
        ["tart", "exec", vm, *args], text=True, stdin=subprocess.DEVNULL, timeout=900
    ).strip()


def prepare_azure_cli(vm: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*-build-[0-9]+-[0-9]+", vm):
        raise ValueError("cask preparation requires an owned temporary build VM")
    guest(vm, ["/opt/homebrew/bin/brew", "update", "--quiet"])
    metadata = cast(
        dict[str, object],
        json.loads(
            guest(
                vm,
                ["/opt/homebrew/bin/brew", "info", "--json=v2", "--cask", "azure-cli"],
            )
        ),
    )
    casks = cast(list[dict[str, object]], metadata["casks"])
    if len(casks) != 1 or casks[0].get("token") != "azure-cli":
        raise ValueError("expected the official Azure CLI cask")
    cask = casks[0]
    version, url, checksum = (str(cask[key]) for key in ("version", "url", "sha256"))
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or not re.fullmatch(
        r"[a-f0-9]{64}", checksum
    ):
        raise ValueError("Azure CLI cask requires a stable version and SHA256")
    expected_url = (
        "https://github.com/Azure/azure-cli/releases/download/"
        f"azure-cli-{version}/azure-cli-{version}-macos-arm64.tar.gz"
    )
    if url != expected_url:
        raise ValueError("Azure CLI cask must use the official ARM64 release")
    release = cast(
        dict[str, object],
        gh(
            "GET",
            f"repos/Azure/azure-cli/releases/tags/{quote('azure-cli-' + version)}",
        ),
    )
    assets = cast(list[dict[str, object]], release.get("assets", []))
    asset = next((a for a in assets if a.get("browser_download_url") == url), None)
    if asset is None or asset.get("digest") != "sha256:" + checksum:
        raise ValueError("GitHub asset and cask SHA256 do not agree")
    api_url = str(asset.get("url", ""))
    if not re.fullmatch(
        r"https://api\.github\.com/repos/Azure/azure-cli/releases/assets/[0-9]+",
        api_url,
    ):
        raise ValueError("invalid Azure CLI asset API URL")
    cache = guest(vm, ["/opt/homebrew/bin/brew", "--cache", "--cask", "azure-cli"])
    if (
        not cache.startswith("/Users/admin/Library/Caches/Homebrew/")
        or ".." in PurePosixPath(cache).parts
        or "\n" in cache
    ):
        raise ValueError("refusing a cask cache path outside the build guest cache")
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "azure-cli.tar.gz"
        with archive.open("wb") as stream:
            subprocess.run(
                ["gh", "api", api_url, "-H", "Accept: application/octet-stream"],
                stdout=stream,
                stdin=subprocess.DEVNULL,
                check=True,
                timeout=900,
            )
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                raise ValueError("downloaded Azure CLI archive checksum mismatch")
        with archive.open("rb") as stream:
            subprocess.run(
                [
                    "tart",
                    "exec",
                    "-i",
                    vm,
                    "/bin/bash",
                    "-c",
                    'set -eu; mkdir -p "$(dirname "$1")"; cat >"$1"',
                    "--",
                    cache,
                ],
                stdin=stream,
                check=True,
                timeout=900,
            )
    actual = guest(vm, ["/usr/bin/shasum", "-a", "256", cache]).split()
    if not actual or actual[0] != checksum:
        raise ValueError("Azure CLI archive changed during guest transfer")
    print(f"Prepared verified Azure CLI {version} in the build guest cache", flush=True)
