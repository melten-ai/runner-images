import base64
import hashlib
import io
import json
import os
import runpy
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: -1)() != 0,
    reason="requires root to reproduce runner ownership",
)
def test_npm_replacement_allows_runner_to_remove_its_old_tree(installer):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prefix = root / "runner"
        (prefix / "bin").mkdir(parents=True)
        old = prefix / "lib/node_modules/npm"
        old.mkdir(parents=True)
        (old / "old.js").write_text("obsolete npm")
        for path in [root, prefix, *prefix.rglob("*")]:
            os.chown(path, 65534, 65534)
        package = root / "extracted/package"
        (package / "bin").mkdir(parents=True)
        (package / "package.json").write_text('{"version":"11.21.2"}')
        for name in ("npm", "npx"):
            (package / f"bin/{name}-cli.js").write_text("updated npm")
        installer["install_npm"](prefix, {"version": "11.21.2"}, package.parent)
        assert (prefix / "lib/node_modules/npm").stat().st_uid == 65534
        assert (prefix / "bin/npm").lstat().st_uid == 65534
        subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys;shutil.rmtree(sys.argv[1])",
                str(prefix),
            ],
            user=65534,
            group=65534,
            extra_groups=[],
            cwd="/tmp",
            check=True,
        )
        assert not prefix.exists()


@pytest.fixture
def installer():
    return runpy.run_path("cloud/linux/install-toolchains.py")


def test_resolve_chooses_official_stable_linux_archives(installer, monkeypatch):
    documents = {
        "https://nodejs.org/dist/index.json": json.dumps(
            [
                {"version": "v99.0.0-rc.1", "lts": "Example"},
                {"version": "v26.1.0", "lts": False},
                {"version": "v24.1.0", "lts": "Example"},
            ]
        ),
        "https://nodejs.org/dist/v24.1.0/SHASUMS256.txt": "a" * 64
        + "  node-v24.1.0-linux-x64.tar.xz\n",
        "https://go.dev/dl/?mode=json": json.dumps(
            [
                {"version": "go9.0rc1", "stable": False},
                {
                    "version": "go1.25.0",
                    "stable": True,
                    "files": [
                        {"os": "linux", "arch": "arm64", "kind": "archive"},
                        {
                            "os": "linux",
                            "arch": "amd64",
                            "kind": "archive",
                            "filename": "go1.25.0.linux-amd64.tar.gz",
                            "sha256": "b" * 64,
                        },
                    ],
                },
            ]
        ),
        "https://static.rust-lang.org/dist/channel-rust-stable.toml": "\n".join(
            f'[pkg.{name}]\nversion = "1.90.0 (example)"\n'
            f"[pkg.{name}.target.x86_64-unknown-linux-gnu]\n"
            f'xz_url = "https://static.rust-lang.org/dist/{name}.tar.xz"\n'
            f'xz_hash = "{"c" * 64}"\n'
            for name in ("rustc", "rust-std", "cargo")
        ),
        "https://api.github.com/repos/git-lfs/git-lfs/releases/latest": json.dumps(
            {
                "tag_name": "v3.0.0",
                "assets": [
                    {
                        "name": "git-lfs-linux-amd64-v3.0.0.tar.gz",
                        "browser_download_url": "https://github.com/git-lfs/example.tar.gz",
                        "digest": "sha256:" + "d" * 64,
                    }
                ],
            }
        ),
        "https://registry.npmjs.org/npm/latest": json.dumps(
            {
                "version": "12.0.0",
                "dist": {
                    "tarball": "https://registry.npmjs.org/npm/-/npm-12.0.0.tgz",
                    "integrity": "sha512-" + base64.b64encode(b"n" * 64).decode(),
                },
            }
        ),
        "https://registry.npmjs.org/npm/next-11": json.dumps(
            {
                "version": "11.0.0",
                "dist": {
                    "tarball": "https://registry.npmjs.org/npm/-/npm-11.0.0.tgz",
                    "integrity": "sha512-" + base64.b64encode(b"m" * 64).decode(),
                },
            }
        ),
        **{
            f"https://api.github.com/repos/{repository}/releases/latest": json.dumps(
                {
                    "tag_name": "v1.0.0",
                    "assets": [
                        {
                            "name": filename,
                            "browser_download_url": "https://github.com/example/tool.tar.gz",
                            "digest": "sha256:" + "f" * 64,
                        }
                    ],
                }
            )
            for repository, filename in [
                ("actions/runner", "actions-runner-linux-x64-1.0.0.tar.gz"),
                ("anchore/syft", "syft_1.0.0_linux_amd64.tar.gz"),
            ]
        },
        "https://api.github.com/repos/docker/buildx/releases/latest": json.dumps(
            {
                "tag_name": "v0.38.2",
                "assets": [
                    {
                        "name": "buildx-v0.38.2.linux-amd64",
                        "browser_download_url": "https://github.com/docker/buildx/example",
                        "digest": "sha256:" + "e" * 64,
                    },
                    {"name": "buildx-v0.38.2.linux-arm64"},
                ],
            }
        ),
    }
    monkeypatch.setitem(
        installer["resolve"].__globals__, "read", lambda url: documents[url].encode()
    )
    result = installer["resolve"]()
    assert result["node"]["version"] == "v24.1.0"
    assert result["go"]["url"].endswith("linux-amd64.tar.gz")
    assert result["git-lfs"]["sha256"] == "d" * 64
    assert result["npm"]["sha512"] == (b"n" * 64).hex()
    assert result["buildx"]["sha256"] == "e" * 64
    assert set(result) == {
        "node",
        "npm",
        "buildx",
        "go",
        "git-lfs",
        "rustc",
        "rust-std",
        "cargo",
        "actions-runner",
        "syft",
        "npm-node20",
        "npm-node24",
    }
    assert result["npm-node24"] == result["npm"]
    assert result["npm-node20"]["version"] == "11.0.0"


def test_unpack_rejects_tampering_before_extraction(installer, tmp_path, monkeypatch):
    def download(command, **kwargs):
        Path(command[-1]).write_bytes(b"tampered archive")

    monkeypatch.setattr(installer["subprocess"], "run", download)
    with pytest.raises(ValueError, match="checksum mismatch"):
        installer["unpack"](
            {"url": "https://example.com/tool.tar.gz", "sha256": "a" * 64}, tmp_path
        )
    assert not (tmp_path / "extracted").exists()


def test_unpack_keeps_relative_executable_symlinks(installer, tmp_path, monkeypatch):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        entry = tarfile.TarInfo("node/lib/npm.js")
        entry.size = 4
        archive.addfile(entry, io.BytesIO(b"test"))
        link = tarfile.TarInfo("node/bin/npm")
        link.type = tarfile.SYMTYPE
        link.linkname = "../lib/npm.js"
        archive.addfile(link)
    content = data.getvalue()

    def download(command, **kwargs):
        Path(command[-1]).write_bytes(content)

    monkeypatch.setattr(installer["subprocess"], "run", download)
    root = installer["unpack"](
        {
            "url": "https://example.com/tool.tar.gz",
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        tmp_path,
    )
    assert (root / "node/bin/npm").is_symlink()
    assert (root / "node/bin/npm").read_bytes() == b"test"


def test_unpack_recovers_from_a_real_interrupted_download(installer, tmp_path):
    import http.server
    import threading

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        entry = tarfile.TarInfo("tool/version")
        entry.size = 3
        archive.addfile(entry, io.BytesIO(b"1.0"))
    content = data.getvalue()

    class Handler(http.server.BaseHTTPRequestHandler):
        attempts = 0

        def do_GET(self):
            Handler.attempts += 1
            partial = Handler.attempts == 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(content[:8] if partial else content)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = installer["unpack"](
            {
                "url": f"http://127.0.0.1:{server.server_port}/tool.tar.gz",
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            tmp_path,
        )
        assert (root / "tool/version").read_bytes() == b"1.0"
        assert Handler.attempts == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_ci_github_metadata_uses_existing_gh_auth_without_token_arguments(
    installer, monkeypatch
):
    monkeypatch.setenv("GH_TOKEN", "example-ci-token")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, b'{"tag_name":"v1.0.0"}')

    monkeypatch.setattr(installer["subprocess"], "run", run)
    body = installer["read"](
        "https://api.github.com/repos/example/tool/releases/latest"
    )
    assert json.loads(body)["tag_name"] == "v1.0.0"
    assert calls == [
        ["gh", "api", "https://api.github.com/repos/example/tool/releases/latest"]
    ]
    assert "example-ci-token" not in str(calls)


def test_github_auth_is_not_sent_to_other_metadata_hosts(installer, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "example-ci-token")
    calls = []

    def download(url, path):
        calls.append(url)
        path.write_bytes(b'{"version":"1.0.0"}')

    monkeypatch.setitem(installer["read"].__globals__, "download", download)
    assert (
        json.loads(installer["read"]("https://example.com/metadata"))["version"]
        == "1.0.0"
    )
    assert calls == ["https://example.com/metadata"]


@pytest.mark.parametrize("algorithm", ["sha256", "sha512"])
def test_unpack_rejects_both_checksum_algorithms_before_extracting(
    installer, tmp_path, monkeypatch, algorithm
):
    monkeypatch.setitem(
        installer["unpack"].__globals__,
        "download",
        lambda url, path: path.write_bytes(b"wrong bytes"),
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        installer["unpack"](
            {
                "url": "https://example.com/npm.tgz",
                algorithm: hashlib.new(algorithm, b"expected").hexdigest(),
            },
            tmp_path,
        )
    assert not (tmp_path / "extracted").exists()


def test_npm_refresh_removes_old_dependencies_and_keeps_runtime(installer, tmp_path):
    prefix = tmp_path / "node20"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/node").write_bytes(b"original official Node binary")
    stale = prefix / "lib/node_modules/npm/node_modules/old-vulnerable-package"
    stale.mkdir(parents=True)
    (stale / "package.json").write_text("{}")
    package = tmp_path / "extracted/package"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text('{"version":"11.21.2"}')
    for name in ["npm", "npx"]:
        (package / f"bin/{name}-cli.js").write_text("updated CLI")
    installer["install_npm"](prefix, {"version": "11.21.2"}, package.parent)
    assert not stale.exists()
    assert (prefix / "bin/npm").read_text() == "updated CLI"
    assert (prefix / "bin/npx").read_text() == "updated CLI"
    assert (prefix / "bin/node").read_bytes() == b"original official Node binary"


def test_npm_archive_identity_checked_before_replacing_installation(
    installer, tmp_path
):
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text('{"version":"1.0.0"}')
    with pytest.raises(ValueError, match="version differs"):
        installer["install_npm"](tmp_path / "prefix", {"version": "2.0.0"}, tmp_path)
    assert not (tmp_path / "prefix").exists()


@pytest.mark.parametrize(
    "version,integrity,message",
    [
        ("11.0.0-rc.1", "sha512-", "stable release"),
        ("12.0.0", "sha512-", "compatible npm 11"),
        ("11.0.0", "sha1-example", "SHA-512 integrity"),
        (
            "11.0.0",
            "sha512-" + base64.b64encode(b"short").decode(),
            "invalid npm SHA-512",
        ),
    ],
)
def test_legacy_npm_resolution_rejects_incompatible_or_unverified_release(
    installer, monkeypatch, version, integrity, message
):
    monkeypatch.setitem(
        installer["resolve_npm"].__globals__,
        "read",
        lambda url: json.dumps(
            {"version": version, "dist": {"integrity": integrity}}
        ).encode(),
    )
    with pytest.raises(ValueError, match=message):
        installer["resolve_npm"]("next-11")


@pytest.mark.parametrize("tampered", [False, True])
def test_buildx_binary_is_verified_before_install_and_executable(
    installer, tmp_path, monkeypatch, tampered
):
    content = b"example official Buildx binary"
    monkeypatch.setitem(
        installer["verified_download"].__globals__,
        "download",
        lambda url, path: path.write_bytes(b"tampered" if tampered else content),
    )
    prefix = tmp_path / "prefix"
    spec = {
        "url": "https://example.com/buildx",
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    destination = prefix / "lib/docker/cli-plugins/docker-buildx"
    if tampered:
        with pytest.raises(ValueError, match="checksum mismatch"):
            installer["install_buildx"](prefix, spec, tmp_path)
        assert not destination.exists()
    else:
        installer["install_buildx"](prefix, spec, tmp_path)
        assert destination.read_bytes() == content
        assert destination.stat().st_mode & 0o777 == 0o755


def test_runner_npm_installs_the_plan_without_resolving_again(
    installer, tmp_path, monkeypatch
):
    specifications = {
        "npm-node20": {"version": "11.21.2", "url": "https://example.com/npm11"},
        "npm-node24": {"version": "12.2.2", "url": "https://example.com/npm12"},
    }
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(specifications))
    runner = tmp_path / "runner"
    for runtime in ["node20", "node24"]:
        (runner / "externals" / runtime / "bin").mkdir(parents=True)
        (runner / "externals" / runtime / "bin/node").write_text("official node")

    def no_resolve(*args):
        raise AssertionError("must use the planned version")

    def unpack(spec, directory):
        package = directory / "package"
        (package / "bin").mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"version": spec["version"]}))
        for name in ["npm", "npx"]:
            (package / f"bin/{name}-cli.js").write_text("CLI")
        return directory

    monkeypatch.setitem(
        installer["refresh_runner_npm"].__globals__, "resolve_npm", no_resolve
    )
    monkeypatch.setitem(installer["refresh_runner_npm"].__globals__, "unpack", unpack)
    installer["refresh_runner_npm"](runner, receipt)
    assert json.loads(receipt.read_text()) == specifications
    for runtime in ["node20", "node24"]:
        prefix = runner / "externals" / runtime
        assert (prefix / "bin/npm").read_text() == "CLI"
        assert (prefix / "bin/node").read_text() == "official node"
