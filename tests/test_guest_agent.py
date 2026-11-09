import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "failure", ["", "checksum", "version", "signature", "linked", "path"]
)
def test_agent_verification_rejects_changed_bytes_identity_and_linked_paths(
    tmp_path, failure
):
    root = Path(__file__).resolve().parents[1]
    cellar = tmp_path / "cellar"
    binary = cellar / "tart-guest-agent/0.15.0/bin/tart-guest-agent"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        "#!/bin/sh\necho 'tart-guest-agent version "
        + ("wrong" if failure == "version" else "0.15.0+melten.example-patched")
        + "'\n"
    )
    binary.chmod(0o755)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if failure == "linked":
        original = binary.with_name("original")
        binary.rename(original)
        binary.symlink_to(original)
    evidence = tmp_path / ".runner-image"
    evidence.mkdir()
    (evidence / "guest-agent-build.json").write_text(
        json.dumps(
            {
                "binary": str(binary)
                if failure != "path"
                else str(tmp_path / "outside"),
                "binary_sha256": "0" * 64 if failure == "checksum" else digest,
                "version": "0.15.0+melten.example",
            }
        )
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "codesign").write_text(
        "#!/bin/sh\nexit " + ("27" if failure == "signature" else "0") + "\n"
    )
    (tools / "codesign").chmod(0o755)
    # Map the external Homebrew root and codesign boundary to portable fixtures;
    # execute the original verification function, including byte/version checks.
    source = (root / "macos/image/refresh-guest-agent.sh").read_text()
    source = source.split("refresh_guest_agent() {", 1)[0]
    source = source.replace("/opt/homebrew/Cellar", str(cellar)).replace(
        "/usr/bin/codesign", "codesign"
    )
    result = subprocess.run(
        ["/bin/bash", "-s"],
        input=source + "\nverify_guest_agent\n",
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": str(tools) + ":" + os.environ["PATH"],
        },
        timeout=10,
    )
    assert (result.returncode == 0) == (not failure), result.stdout + result.stderr
    assert ("guest_agent_sha256=" in result.stdout) == (not failure)


@pytest.mark.parametrize("status", [0, 27])
def test_module_cache_cleanup_handles_read_only_directories_and_keeps_exit_status(
    tmp_path, status
):
    root = Path(__file__).resolve().parents[1]
    source = (root / "macos/image/refresh-guest-agent.sh").read_text()
    trap = next(
        line.strip() for line in source.splitlines() if line.strip().startswith("trap ")
    )
    work = tmp_path / "owned-work"
    module = work / "modules/example.org/module@v1.0.0"
    module.mkdir(parents=True)
    (module / "go.mod").write_text("module example.org/module\n")
    (module / "go.mod").chmod(0o444)
    module.chmod(0o555)
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            "set -eu; guest_agent_work=$1; " + trap + '; exit "$2"',
            "--",
            str(work),
            str(status),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == status, result.stdout + result.stderr
    assert not work.exists()
