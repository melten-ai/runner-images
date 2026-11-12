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
    result = {
        "version": release["tag_name"],
        "url": asset["browser_download_url"],
        "sha256": asset["digest"].removeprefix("sha256:"),
    }
    if asset.get("url"):
        result["api_url"] = asset["url"]
    return result


def resolve_go_module(module: str) -> str:
    version = json.loads(read(f"https://proxy.golang.org/{module}/@latest"))["Version"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Go module must be a stable release")
    return version


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
    result["git-lfs"]["net_version"] = resolve_go_module("golang.org/x/net")
    result["git-lfs"]["compress_version"] = resolve_go_module(
        "github.com/klauspost/compress"
    )
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
    if os.environ.get("GH_TOKEN") and spec.get("api_url", "").startswith(
        "https://api.github.com/repos/"
    ):
        # Authenticated build hosts can use the official asset API rather than
        # the browser-download endpoint. Guests keep using public URLs.
        with archive.open("wb") as stream:
            subprocess.run(
                [
                    "gh",
                    "api",
                    spec["api_url"],
                    "-H",
                    "Accept: application/octet-stream",
                ],
                stdout=stream,
                check=True,
            )
    else:
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


def install_host_tool(name: str, prefix: Path, receipt: Path) -> None:
    # Build-host dependencies share the guest tools' retry and integrity checks.
    # They are not part of the published VM image.
    if name == "oneup":
        spec = resolve_github(
            "circlesac/oneup", r"oneup-x86_64-unknown-linux-gnu\.tar\.gz"
        )
    elif name == "grype":
        spec = resolve_github("anchore/grype", r"grype_.*_linux_amd64\.tar\.gz")
    else:
        raise ValueError("unsupported build-host tool")
    with tempfile.TemporaryDirectory() as directory:
        extracted = unpack(spec, Path(directory))
        executable = extracted / name
        if not executable.is_file() or executable.is_symlink():
            raise ValueError("build-host archive must contain a regular executable")
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        shutil.copy2(executable, prefix / "bin" / name)
        (prefix / "bin" / name).chmod(0o755)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({name: spec}, indent=2) + "\n")
    print(f"Installed build-host {name} {spec['version']}", flush=True)


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


def probe_git_lfs(binary: Path, directory: Path, environment: dict[str, str]) -> None:
    environment = {**environment, "PATH": f"{binary.parent}:{environment['PATH']}"}
    directory.mkdir()

    def git(*args: str, cwd: Path = directory) -> None:
        subprocess.run(["git", *args], cwd=cwd, env=environment, check=True)

    git("init", "--bare", "-b", "main", "remote.git")
    git("init", "-b", "main", "repository")
    repository = directory / "repository"
    git("config", "user.name", "Example Builder", cwd=repository)
    git("config", "user.email", "builder@example.com", cwd=repository)
    git("lfs", "install", "--local", cwd=repository)
    git("lfs", "track", "*.payload", cwd=repository)
    expected = bytes(range(256)) * 4096
    (repository / "example.payload").write_bytes(expected)
    git("add", ".gitattributes", "example.payload", cwd=repository)
    git("commit", "-m", "Synthetic LFS fixture", cwd=repository)
    pointer = subprocess.check_output(
        ["git", "show", "HEAD:example.payload"], cwd=repository, env=environment
    )
    if not pointer.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        raise ValueError("Git did not store an LFS pointer")
    git("remote", "add", "origin", str(directory / "remote.git"), cwd=repository)
    git("push", "origin", "main", cwd=repository)
    git("clone", "--no-checkout", "remote.git", "consumer")
    consumer = directory / "consumer"
    git("lfs", "install", "--local", cwd=consumer)
    git("checkout", "main", cwd=consumer)
    if (consumer / "example.payload").read_bytes() != expected:
        raise ValueError("Git LFS clone did not materialize the original bytes")
    git("lfs", "fsck", cwd=consumer)


def build_git_lfs(
    prefix: Path, spec: dict[str, str], go_version: str, directory: Path
) -> None:
    for key in ("version", "net_version", "compress_version"):
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", spec[key]):
            raise ValueError("Git LFS inputs require exact stable module versions")
    go = str(prefix / "go/bin/go")
    environment = {
        **os.environ,
        "PATH": f"{prefix / 'go/bin'}:{prefix / 'bin'}:{os.environ['PATH']}",
        "GOTOOLCHAIN": "local",
        "GOPROXY": "https://proxy.golang.org",
        "GOSUMDB": "sum.golang.org",
        "GOPRIVATE": "",
        "GONOPROXY": "",
        "GONOSUMDB": "",
        "GOFLAGS": "",
        "GOOS": "linux",
        "GOARCH": "amd64",
        "CGO_ENABLED": "0",
        "GOMODCACHE": str(directory / "modules"),
        "GOCACHE": str(directory / "cache"),
        "GIT_CONFIG_GLOBAL": str(directory / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        environment.pop(key, None)
    (directory / "gitconfig").write_text("[init]\n defaultBranch = master\n")
    observed = subprocess.check_output([go, "version"], env=environment, text=True)
    if observed.strip() != f"go version {go_version} linux/amd64":
        raise ValueError("Git LFS compiler differs from the resolved Go version")
    module = "github.com/git-lfs/git-lfs/v3"
    source = json.loads(
        subprocess.check_output(
            [go, "mod", "download", "-json", f"{module}@{spec['version']}"],
            env=environment,
            text=True,
        )
    )
    origin = Path(source["Dir"]).resolve()
    if (
        source.get("Path") != module
        or source.get("Version") != spec["version"]
        or not source.get("Sum", "").startswith("h1:")
        or not origin.is_relative_to((directory / "modules").resolve())
    ):
        raise ValueError("Unexpected Git LFS source module")
    working = directory / "source"
    shutil.copytree(origin, working)
    for path in [working, *working.rglob("*")]:
        if path.is_symlink():
            raise ValueError("Git LFS source module contains a link")
        path.chmod(path.stat().st_mode | 0o200)
    subprocess.run(
        [
            go,
            "get",
            f"golang.org/x/net@{spec['net_version']}",
            f"github.com/klauspost/compress@{spec['compress_version']}",
        ],
        cwd=working,
        env=environment,
        check=True,
    )
    subprocess.run([go, "mod", "verify"], cwd=working, env=environment, check=True)
    subprocess.run([go, "test", "./..."], cwd=working, env=environment, check=True)
    identity = hashlib.sha256(
        json.dumps(
            {k: source[k] for k in ("Path", "Version", "Sum", "GoModSum")},
            sort_keys=True,
        ).encode()
        + (working / "go.mod").read_bytes()
        + (working / "go.sum").read_bytes()
        + go_version.encode()
    ).hexdigest()[:12]
    binary = directory / "git-lfs"
    subprocess.run(
        [
            go,
            "build",
            "-trimpath",
            "-ldflags",
            "-X github.com/git-lfs/git-lfs/v3/config.Vendor=Melten "
            f"-X github.com/git-lfs/git-lfs/v3/config.GitCommit=patched.{identity}",
            "-o",
            str(binary),
            ".",
        ],
        cwd=working,
        env=environment,
        check=True,
    )
    description = subprocess.check_output(
        [str(binary), "version"], env=environment, text=True
    ).strip()
    expected = (
        f"git-lfs/{spec['version'][1:]} (Melten; linux amd64; "
        f"go {go_version.removeprefix('go')}; git patched.{identity})"
    )
    if description != expected:
        raise ValueError("Built Git LFS identity differs from resolved inputs")
    probe_git_lfs(binary, directory / "smoke", environment)
    destination = prefix / "bin/git-lfs"
    shutil.copy2(binary, destination)
    report = {
        "source": {k: source[k] for k in ("Path", "Version", "Sum", "GoModSum")},
        "inputs": spec,
        "compiler": go_version,
        "description": description,
        "go_mod": (working / "go.mod").read_text(),
        "go_sum": (working / "go.sum").read_text(),
        "binary_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "upstream_tests": "passed",
        "local_transfer_smoke": "passed",
    }
    evidence = prefix / "share/runner-images/git-lfs-build.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(report, indent=2) + "\n")


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
                official = subprocess.check_output(
                    [str(candidates[0]), "version"], text=True
                )
                if not official.startswith(f"git-lfs/{spec['version'][1:]} "):
                    raise ValueError("Git LFS archive version differs from its plan")
                build_git_lfs(
                    prefix, spec, specifications["go"]["version"], Path(directory)
                )
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
    modes.add_argument("--host-tool", choices=("oneup", "grype"))
    parser.add_argument("--specifications", type=Path)
    args = parser.parse_args()
    if args.resolve:
        print(json.dumps(resolve(), sort_keys=True))
    elif args.prefix is None or args.receipt is None:
        parser.error("prefix and receipt are required for installation")
    elif args.runner_npm:
        refresh_runner_npm(args.prefix.resolve(), args.receipt.resolve())
    elif args.host_tool:
        install_host_tool(args.host_tool, args.prefix.resolve(), args.receipt.resolve())
    else:
        specifications = (
            json.loads(args.specifications.read_text()) if args.specifications else None
        )
        install(args.prefix.resolve(), args.receipt.resolve(), specifications)
