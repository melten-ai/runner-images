"""Full install-runner.sh checks with synthetic runner and npm archives."""

import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_RUNNER = ROOT / "macos/image/install-runner.sh"
RUNNER_VERSION = "2.338.2"
RUNNER_TARBALL_URL = (
    f"https://github.com/actions/runner/releases/download/v{RUNNER_VERSION}/"
    f"actions-runner-osx-arm64-{RUNNER_VERSION}.tar.gz"
)
NODE_MARK = b"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
package = Path(sys.argv[1]).parents[1] / "package.json"
print(json.loads(package.read_text())["version"])
"""


def npm_archive(version: str, *, stale: bool = False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:

        def add(name: str, body: bytes) -> None:
            info = tarfile.TarInfo(name=f"package/{name}")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))

        add("package.json", json.dumps({"version": version}).encode())
        add("bin/npm-cli.js", b"#!/usr/bin/env node\n")
        add("bin/npx-cli.js", b"#!/usr/bin/env node\n")
        if stale:
            add("node_modules/stale-runner-bundle/index.js", b"obsolete\n")
    return buffer.getvalue()


def npm_metadata(version: str, tarball: str, archive: bytes) -> dict:
    digest = hashlib.sha512(archive).digest()
    return {
        "version": version,
        "dist": {
            "integrity": "sha512-" + base64.b64encode(digest).decode(),
            "tarball": tarball,
        },
    }


def runner_archive(*, node_mark: bytes = NODE_MARK) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    run_sh = b"#!/bin/bash\necho official-run.sh\n"

    def add_file(name: str, body: bytes, mode: int = 0o644) -> None:
        info = tarfile.TarInfo(name=name)
        info.size = len(body)
        info.mode = mode
        archive.addfile(info, io.BytesIO(body))

    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        add_file("run.sh", run_sh, 0o755)
        for runtime in ("node20", "node24"):
            add_file(f"externals/{runtime}/bin/node", node_mark, 0o755)
            stale_root = f"externals/{runtime}/lib/node_modules/npm"
            add_file(
                f"{stale_root}/package.json",
                json.dumps({"version": "6.14.0"}).encode(),
            )
            add_file(f"{stale_root}/bin/npm-cli.js", b"stale\n")
            add_file(
                f"{stale_root}/node_modules/stale-runner-bundle/index.js",
                b"obsolete bundled dependency\n",
            )
        add_file("config.sh", b"# pre-existing runner file\n")
    payload = buffer.getvalue()
    return payload, hashlib.sha256(payload).hexdigest()


def patched_install_script(tmp_path: Path, archive_path: Path) -> Path:
    text = INSTALL_RUNNER.read_text().replace(
        "/tmp/actions-runner.tar.gz", str(archive_path)
    )
    script = tmp_path / "install-runner-test.sh"
    script.write_text(text)
    script.chmod(0o755)
    return script


def write_curl_stub(
    tools: Path,
    *,
    runner_archive: bytes,
    metadata: dict[str, dict],
    tarballs: dict[str, bytes],
) -> None:
    catalog = {
        "runner_url": RUNNER_TARBALL_URL,
        "runner_archive": base64.b64encode(runner_archive).decode(),
        "metadata": metadata,
        "tarballs": {
            url: base64.b64encode(payload).decode() for url, payload in tarballs.items()
        },
    }
    (tools / "catalog.json").write_text(json.dumps(catalog))
    (tools / "curl").write_text(
        """#!/usr/bin/env python3
import base64
import json
import sys
from pathlib import Path

root = Path(sys.argv[0]).parent
catalog = json.loads((root / "catalog.json").read_text())
args = sys.argv[1:]
out = None
for index, token in enumerate(args):
    if token in ("-o", "--output") and index + 1 < len(args):
        out = args[index + 1]
url = next((token for token in reversed(args) if not token.startswith("-")), None)

if url is None:
    sys.stderr.write("stub curl requires a URL\\n")
    raise SystemExit(22)

def emit(body: bytes) -> None:
    if out:
        Path(out).write_bytes(body)
    else:
        sys.stdout.buffer.write(body)

if url == catalog["runner_url"]:
    emit(base64.b64decode(catalog["runner_archive"]))
    raise SystemExit(0)

if url.startswith("https://registry.npmjs.org/npm/") and "/-/" not in url:
    tag = url.rsplit("/", 1)[-1]
    body = json.dumps(catalog["metadata"][tag]).encode()
    emit(body)
    raise SystemExit(0)

payload = catalog["tarballs"].get(url)
if payload is None:
    sys.stderr.write(f"unknown download URL: {url}\\n")
    raise SystemExit(22)
emit(base64.b64decode(payload))
"""
    )
    (tools / "curl").chmod(0o755)


def default_npm_catalog() -> tuple[dict[str, dict], dict[str, bytes]]:
    npm11 = npm_archive("11.55.1")
    npm12 = npm_archive("12.55.1")
    url11 = "https://registry.npmjs.org/npm/-/npm-11.55.1.tgz"
    url12 = "https://registry.npmjs.org/npm/-/npm-12.55.1.tgz"
    metadata = {
        "next-11": npm_metadata("11.55.1", url11, npm11),
        "latest": npm_metadata("12.55.1", url12, npm12),
    }
    return metadata, {url11: npm11, url12: npm12}


@pytest.fixture
def guest_layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home"
    runner = home / "actions-runner"
    (runner / "_work").mkdir(parents=True)
    (runner / "_work/job-output.txt").write_text("preserved")
    (runner / "old-top-level").write_text("remove-me")
    tools = tmp_path / "bin"
    tools.mkdir()
    archive = tmp_path / "actions-runner.tar.gz"
    return home, runner, tools, archive


def run_install(
    home: Path,
    runner: Path,
    tools: Path,
    script: Path,
    runner_sha: str,
    *,
    metadata: dict[str, dict],
    tarballs: dict[str, bytes],
    runner_bytes: bytes,
) -> subprocess.CompletedProcess[bytes]:
    write_curl_stub(
        tools, runner_archive=runner_bytes, metadata=metadata, tarballs=tarballs
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{tools}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(script), RUNNER_VERSION, str(runner), runner_sha],
        capture_output=True,
        env=env,
    )


def test_full_install_refreshes_npm_preserves_work_and_wraps_signals(
    guest_layout: tuple[Path, Path, Path, Path],
):
    home, runner, tools, archive_path = guest_layout
    runner_bytes, runner_sha = runner_archive()
    metadata, tarballs = default_npm_catalog()
    script = patched_install_script(home.parent, archive_path)

    result = run_install(
        home,
        runner,
        tools,
        script,
        runner_sha,
        metadata=metadata,
        tarballs=tarballs,
        runner_bytes=runner_bytes,
    )
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert (runner / "_work/job-output.txt").read_text() == "preserved"
    assert not (runner / "old-top-level").exists()
    assert (runner / "run-runner.sh").read_bytes().startswith(b"#!/bin/bash")
    assert "/usr/bin/perl" in (runner / "run.sh").read_text()
    assert os.access(runner / "run.sh", os.X_OK)
    assert os.access(runner / "run-runner.sh", os.X_OK)

    for runtime, expected in (("node20", "11.55.1"), ("node24", "12.55.1")):
        prefix = runner / "externals" / runtime
        assert (prefix / "bin/node").read_bytes() == NODE_MARK
        assert (
            json.loads((prefix / "lib/node_modules/npm/package.json").read_text())[
                "version"
            ]
            == expected
        )
        assert not (
            prefix / "lib/node_modules/npm/node_modules/stale-runner-bundle"
        ).exists()
        assert (prefix / "bin/npm").resolve() == (
            prefix / "lib/node_modules/npm/bin/npm-cli.js"
        ).resolve()
        assert (prefix / "bin/npx").resolve() == (
            prefix / "lib/node_modules/npm/bin/npx-cli.js"
        ).resolve()


@pytest.mark.parametrize(
    "scenario,expect_stderr,check_runtime",
    [
        (
            "checksum",
            b"checksum mismatch",
            lambda runner: (
                json.loads(
                    (
                        runner / "externals/node20/lib/node_modules/npm/package.json"
                    ).read_text()
                )["version"]
                == "6.14.0"
            ),
        ),
        ("metadata_major", b"compatible npm 11", None),
        ("metadata_major_latest", b"compatible npm 12", None),
        ("archive_version", b"differs from registry metadata", None),
    ],
)
def test_install_failures_leave_affected_npm_intact(
    guest_layout: tuple[Path, Path, Path, Path],
    scenario: str,
    expect_stderr: bytes,
    check_runtime: Callable[[Path], bool] | None,
):
    home, runner, tools, archive_path = guest_layout
    runner_bytes, runner_sha = runner_archive()
    metadata, tarballs = default_npm_catalog()
    script = patched_install_script(home.parent, archive_path)

    if scenario == "checksum":
        url = "https://registry.npmjs.org/npm/-/npm-11.55.1.tgz"
        tarballs[url] = npm_archive("11.55.1", stale=True)
    elif scenario == "metadata_major":
        bad = npm_archive("10.0.0")
        url = "https://registry.npmjs.org/npm/-/npm-bad.tgz"
        metadata["next-11"] = npm_metadata("10.0.0", url, bad)
        tarballs[url] = bad
    elif scenario == "metadata_major_latest":
        bad = npm_archive("13.0.0")
        url = "https://registry.npmjs.org/npm/-/npm-bad.tgz"
        metadata["latest"] = npm_metadata("13.0.0", url, bad)
        tarballs[url] = bad
    elif scenario == "archive_version":
        archive = npm_archive("11.55.1")
        url = "https://registry.npmjs.org/npm/-/npm-11.55.1.tgz"
        tarballs[url] = archive
        metadata["next-11"] = npm_metadata("11.55.9", url, archive)

    result = run_install(
        home,
        runner,
        tools,
        script,
        runner_sha,
        metadata=metadata,
        tarballs=tarballs,
        runner_bytes=runner_bytes,
    )
    assert result.returncode != 0
    assert expect_stderr in result.stderr
    if check_runtime is not None:
        assert check_runtime(runner)
    if scenario == "metadata_major_latest":
        assert (
            json.loads(
                (
                    runner / "externals/node24/lib/node_modules/npm/package.json"
                ).read_text()
            )["version"]
            == "6.14.0"
        )
        assert (
            json.loads(
                (
                    runner / "externals/node20/lib/node_modules/npm/package.json"
                ).read_text()
            )["version"]
            == "11.55.1"
        )
