import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

API_URL = "https://api.github.com/repos/anchore/syft/releases/latest"
SYFT_BODY = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
assert os.environ["SYFT_GOLANG_CAPTURE_SYMBOLS"] == "all"
with Path(os.environ["TEST_SCAN_CALLS"]).open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
for index, arg in enumerate(sys.argv[1:], 1):
    if arg == "-o":
        format, path = sys.argv[index + 1].split("=", 1)
        Path(path).write_text(json.dumps({"format": format}))
"""


def synthetic_syft_archive() -> tuple[bytes, str]:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        entry = tarfile.TarInfo("syft")
        payload = SYFT_BODY.encode()
        entry.size = len(payload)
        entry.mode = 0o755
        archive.addfile(entry, io.BytesIO(payload))
    content = data.getvalue()
    return content, hashlib.sha256(content).hexdigest()


def release_metadata(digest: str, download: str) -> str:
    return json.dumps(
        {
            "tag_name": "v1.99.0",
            "assets": [
                {
                    "name": "syft_1.99.0_darwin_arm64.tar.gz",
                    "browser_download_url": download,
                    "digest": digest,
                },
                {
                    "name": "syft_1.99.0_linux_amd64.tar.gz",
                    "browser_download_url": "https://example.invalid/linux.tar.gz",
                    "digest": "sha256:" + "b" * 64,
                },
            ],
        }
    )


def patched_script(root: Path, tmp_path: Path) -> Path:
    script = tmp_path / "collect-sbom.sh"
    text = (root / "macos/image/collect-sbom.sh").read_text()
    text = text.replace("/private/tmp", str(tmp_path))
    script.write_text(text)
    script.chmod(0o755)
    return script


def install_curl(
    tools: Path,
    archive_path: Path,
    metadata: str,
    download_url: str,
    download_log: Path | None = None,
) -> None:
    log_path = "None" if download_log is None else repr(str(download_log))
    (tools / "curl").write_text(
        f"""#!/usr/bin/env python3
import sys
from pathlib import Path
api = {API_URL!r}
download = {download_url!r}
archive = Path({str(archive_path)!r})
meta = {metadata!r}
log = {log_path}
args = sys.argv[1:]
if api in args:
    sys.stdout.write(meta)
    raise SystemExit(0)
if download in args:
    if log is not None:
        Path(log).write_text("download\\n")
    out = args[args.index("-o") + 1]
    Path(out).write_bytes(archive.read_bytes())
    raise SystemExit(0)
raise SystemExit("unexpected curl args: " + repr(args))
"""
    )
    (tools / "curl").chmod(0o755)


@pytest.fixture
def collector_layout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    archive, sha256 = synthetic_syft_archive()
    download = "https://github.example/anchore/syft/syft.tar.gz"
    digest = f"sha256:{sha256}"
    archive_path = tmp_path / "fixtures" / "syft.tar.gz"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(archive)
    metadata = release_metadata(digest, download)
    tools = tmp_path / "bin"
    tools.mkdir()
    brew_log = tmp_path / "brew.log"
    (tools / "brew").write_text(f"#!/bin/sh\necho brew-invoked >>{brew_log}\nexit 1\n")
    (tools / "brew").chmod(0o755)
    install_curl(tools, archive_path, metadata, download)
    untouched = tmp_path / "untouched.marker"
    untouched.write_text("leave-me-alone")
    evidence = tmp_path / "runner-image-sbom"
    calls = tmp_path / "calls.jsonl"
    return {
        "root": root,
        "tmp_path": tmp_path,
        "tools": tools,
        "evidence": evidence,
        "calls": calls,
        "untouched": untouched,
        "archive_path": archive_path,
        "digest": digest,
        "download": download,
        "metadata": metadata,
        "brew_log": brew_log,
    }


def test_verified_download_invokes_scanner_once_and_cleans_scratch(collector_layout):
    layout = collector_layout
    script = patched_script(layout["root"], layout["tmp_path"])
    subprocess.run(
        ["bash", str(script)],
        env={
            **os.environ,
            "PATH": str(layout["tools"]) + os.pathsep + os.environ["PATH"],
            "TEST_SCAN_CALLS": str(layout["calls"]),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert len(layout["calls"].read_text().splitlines()) == 1
    for format in ("syft", "spdx"):
        assert json.loads((layout["evidence"] / f"sbom.{format}.json").read_text()) == {
            "format": f"{format}-json"
        }
    scratch = list(layout["tmp_path"].glob("runner-image-sbom-scanner.*"))
    assert not scratch
    assert not layout["brew_log"].exists()
    assert layout["untouched"].read_text() == "leave-me-alone"


def test_checksum_mismatch_rejects_before_extract(collector_layout):
    layout = collector_layout
    script = patched_script(layout["root"], layout["tmp_path"])
    bad_meta = release_metadata("sha256:" + "c" * 64, layout["download"])
    install_curl(layout["tools"], layout["archive_path"], bad_meta, layout["download"])
    result = subprocess.run(
        ["bash", str(script)],
        env={
            **os.environ,
            "PATH": str(layout["tools"]) + os.pathsep + os.environ["PATH"],
            "TEST_SCAN_CALLS": str(layout["calls"]),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not layout["calls"].exists()
    assert not layout["evidence"].exists()
    scratch = list(layout["tmp_path"].glob("runner-image-sbom-scanner.*"))
    assert not scratch
    assert layout["untouched"].read_text() == "leave-me-alone"


@pytest.mark.parametrize(
    "digest",
    ["sha256:abc", "sha1:" + "d" * 64, "sha256:" + "e" * 63, None],
)
def test_invalid_release_digest_rejects_before_download(collector_layout, digest):
    layout = collector_layout
    script = patched_script(layout["root"], layout["tmp_path"])
    bad_meta = release_metadata(digest, layout["download"])
    if digest is None:
        value = json.loads(bad_meta)
        del value["assets"][0]["digest"]
        bad_meta = json.dumps(value)
    download_log = layout["tmp_path"] / "downloads.log"
    install_curl(
        layout["tools"],
        layout["archive_path"],
        bad_meta,
        layout["download"],
        download_log,
    )
    result = subprocess.run(
        ["bash", str(script)],
        env={
            **os.environ,
            "PATH": str(layout["tools"]) + os.pathsep + os.environ["PATH"],
            "TEST_SCAN_CALLS": str(layout["calls"]),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    if digest is not None:
        assert "valid sha256 digest" in result.stderr
    assert not layout["calls"].exists()
    assert not download_log.exists()
    scratch = list(layout["tmp_path"].glob("runner-image-sbom-scanner.*"))
    assert not scratch
    assert layout["untouched"].read_text() == "leave-me-alone"
