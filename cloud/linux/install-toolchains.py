"""Install current official Linux x64 toolchains with verified download receipts."""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path


def resolve_npm(tag: str = "latest") -> dict[str, str]:
    package = json.loads(read(f"https://registry.npmjs.org/npm/{tag}"))
    version = package["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("npm must be a stable release")
    if tag == "next-11" and not version.startswith("11."):
        raise ValueError("Node 20 requires the compatible npm 11 release line")
    integrity = package["dist"]["integrity"]
    if not integrity.startswith("sha512-"):
        raise ValueError("npm archive requires official SHA-512 integrity")
    checksum = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
    if len(checksum) != 64:
        raise ValueError("invalid npm SHA-512 integrity")
    return {
        "version": version,
        "url": package["dist"]["tarball"],
        "sha512": checksum.hex(),
    }


def resolve_github(repository: str, pattern: str) -> dict[str, str]:
    release = json.loads(
        read(f"https://api.github.com/repos/{repository}/releases/latest")
    )
    asset = next(a for a in release["assets"] if re.fullmatch(pattern, a["name"]))
    return {
        "version": release["tag_name"],
        "url": asset["browser_download_url"],
        "sha256": asset["digest"].removeprefix("sha256:"),
    }


def download(url: str, destination: Path) -> None:
    subprocess.run(
        [
            "curl",
            "-fsSL",
            "--retry",
            "5",
            "--retry-all-errors",
            "--retry-delay",
            "2",
            url,
            "-o",
            str(destination),
        ],
        check=True,
    )


def read(url: str) -> bytes:
    # Hosted CI reuses its normal GitHub CLI token; no token is passed to guests.
    if url.startswith("https://api.github.com/") and os.environ.get("GH_TOKEN"):
        return subprocess.run(
            ["gh", "api", url], check=True, capture_output=True
        ).stdout
    # A real file lets curl discard a partial response before retrying.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "response"
        download(url, path)
        return path.read_bytes()


def resolve() -> dict[str, dict[str, str]]:
    node = next(
        item
        for item in json.loads(read("https://nodejs.org/dist/index.json"))
        if item["lts"] and re.fullmatch(r"v\d+\.\d+\.\d+", item["version"])
    )
    filename = f"node-{node['version']}-linux-x64.tar.xz"
    node_root = f"https://nodejs.org/dist/{node['version']}/"
    checksum = next(
        line.split()[0]
        for line in read(node_root + "SHASUMS256.txt").decode().splitlines()
        if line.split()[-1] == filename
    )
    go = next(
        item
        for item in json.loads(read("https://go.dev/dl/?mode=json"))
        if item["stable"]
    )
    go_file = next(
        item
        for item in go["files"]
        if item["os"] == "linux"
        and item["arch"] == "amd64"
        and item["kind"] == "archive"
    )
    rust = tomllib.loads(
        read("https://static.rust-lang.org/dist/channel-rust-stable.toml").decode()
    )
    result = {
        "node": {
            "version": node["version"],
            "url": node_root + filename,
            "sha256": checksum,
        },
        "go": {
            "version": go["version"],
            "url": "https://go.dev/dl/" + go_file["filename"],
            "sha256": go_file["sha256"],
        },
        "git-lfs": resolve_github(
            "git-lfs/git-lfs", r"git-lfs-linux-amd64-.*\.tar\.gz"
        ),
        "npm": resolve_npm(),
        "buildx": resolve_github("docker/buildx", r"buildx-.*\.linux-amd64"),
        "actions-runner": resolve_github(
            "actions/runner", r"actions-runner-linux-x64-.*\.tar\.gz"
        ),
        "syft": resolve_github("anchore/syft", r"syft_.*_linux_amd64\.tar\.gz"),
        "npm-node20": resolve_npm("next-11"),
    }
    result["npm-node24"] = result["npm"].copy()
    for component in ("rustc", "rust-std", "cargo"):
        package = rust["pkg"][component]
        target = package["target"]["x86_64-unknown-linux-gnu"]
        result[component] = {
            "version": package["version"].split()[0],
            "url": target["xz_url"],
            "sha256": target["xz_hash"],
        }
    return result


def verified_download(spec: dict[str, str], archive: Path) -> None:
    algorithm = "sha512" if "sha512" in spec else "sha256"
    if not re.fullmatch(
        r"[a-f0-9]{128}" if algorithm == "sha512" else r"[a-f0-9]{64}",
        spec[algorithm],
    ):
        raise ValueError("missing or invalid official archive checksum")
    download(spec["url"], archive)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, algorithm).hexdigest()
    if actual != spec[algorithm]:
        raise ValueError("official toolchain archive checksum mismatch")


def unpack(spec: dict[str, str], directory: Path) -> Path:
    archive = directory / "download.tar"
    verified_download(spec, archive)
    extracted = directory / "extracted"
    extracted.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(extracted, filter="data")
    return extracted


def install_buildx(prefix: Path, spec: dict[str, str], directory: Path) -> None:
    binary = directory / "docker-buildx"
    verified_download(spec, binary)
    destination = prefix / "lib/docker/cli-plugins/docker-buildx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, destination)
    destination.chmod(0o755)


def install_npm(prefix: Path, spec: dict[str, str], extracted: Path) -> None:
    package = extracted / "package"
    if json.loads((package / "package.json").read_text())["version"] != spec["version"]:
        raise ValueError("npm archive version differs from registry receipt")
    destination = prefix / "lib/node_modules/npm"
    owner = (destination if destination.exists() else prefix).stat()
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(package, destination, symlinks=True)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    for command in ("npm", "npx"):
        link = prefix / "bin" / command
        link.unlink(missing_ok=True)
        link.symlink_to(f"../lib/node_modules/npm/bin/{command}-cli.js")
    for path in [
        destination,
        *destination.rglob("*"),
        prefix / "bin/npm",
        prefix / "bin/npx",
    ]:
        stat = path.lstat()
        if (stat.st_uid, stat.st_gid) != (owner.st_uid, owner.st_gid):
            os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)


def refresh_runner_npm(runner: Path, receipt: Path) -> None:
    # Preserve the runner's actual Node binaries and its action routing contract.
    # npm 12 supports Node 24; npm 11 is the maintained compatible line for Node 20.
    specifications = json.loads(receipt.read_text())
    for runtime in ("node20", "node24"):
        prefix = runner / "externals" / runtime
        if not (prefix / "bin/node").is_file():
            raise ValueError(f"missing official runner runtime: {runtime}")
        spec = specifications[f"npm-{runtime}"]
        with tempfile.TemporaryDirectory() as directory:
            extracted = unpack(spec, Path(directory))
            install_npm(prefix, spec, extracted)
        specifications[f"npm-{runtime}"] = spec
        print(f"Installed runner {runtime} npm {spec['version']}", flush=True)
    receipt.write_text(json.dumps(specifications, indent=2) + "\n")


def install(
    prefix: Path, receipt: Path, specifications: dict[str, dict[str, str]] | None = None
) -> None:
    if specifications is None:
        specifications = resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / "bin").mkdir(exist_ok=True)
    for name in (
        "node",
        "go",
        "git-lfs",
        "npm",
        "buildx",
        "rustc",
        "rust-std",
        "cargo",
    ):
        spec = specifications[name]
        with tempfile.TemporaryDirectory() as directory:
            if name == "buildx":
                install_buildx(prefix, spec, Path(directory))
                print(f"Installed {name} {spec['version']}", flush=True)
                continue
            extracted = unpack(spec, Path(directory))
            if name == "node":
                shutil.copytree(
                    next(extracted.glob("node-*")),
                    prefix,
                    dirs_exist_ok=True,
                    symlinks=True,
                )
            elif name == "go":
                if (prefix / "go").exists():
                    raise ValueError("refusing to merge an existing Go installation")
                shutil.copytree(extracted / "go", prefix / "go", symlinks=True)
            elif name == "git-lfs":
                candidates = [p for p in extracted.rglob("git-lfs") if p.is_file()]
                if len(candidates) != 1:
                    raise ValueError("Git LFS archive must contain one executable")
                shutil.copy2(candidates[0], prefix / "bin/git-lfs")
            elif name == "npm":
                install_npm(prefix, spec, extracted)
            else:
                installer = next(extracted.glob("*/install.sh"))
                subprocess.run(
                    [
                        "bash",
                        str(installer),
                        f"--prefix={prefix}",
                        "--disable-ldconfig",
                    ],
                    check=True,
                )
        print(f"Installed {name} {spec['version']}", flush=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(specifications, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path, nargs="?")
    parser.add_argument("receipt", type=Path, nargs="?")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--runner-npm", action="store_true")
    modes.add_argument("--resolve", action="store_true")
    parser.add_argument("--specifications", type=Path)
    args = parser.parse_args()
    if args.resolve:
        print(json.dumps(resolve(), sort_keys=True))
    elif args.prefix is None or args.receipt is None:
        parser.error("prefix and receipt are required for installation")
    elif args.runner_npm:
        refresh_runner_npm(args.prefix.resolve(), args.receipt.resolve())
    else:
        specifications = (
            json.loads(args.specifications.read_text()) if args.specifications else None
        )
        install(args.prefix.resolve(), args.receipt.resolve(), specifications)
