import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("tool", ["guest-agent", "git-lfs"])
@pytest.mark.parametrize(
    "failure", ["", "checksum", "version", "signature", "linked", "path"]
)
def test_agent_verification_rejects_changed_bytes_identity_and_linked_paths(
    tmp_path, failure, tool
):
    root = Path(__file__).resolve().parents[1]
    cellar = tmp_path / "cellar"
    program = "tart-guest-agent" if tool == "guest-agent" else "git-lfs"
    version = "0.15.0+melten.example" if tool == "guest-agent" else "3.8.1"
    description = (
        "tart-guest-agent version 0.15.0+melten.example-patched"
        if tool == "guest-agent"
        else "git-lfs/3.8.1 (Melten; darwin arm64; go 1.27.3; git patched.example)"
    )
    binary = cellar / program / version.split("+")[0] / "bin" / program
    binary.parent.mkdir(parents=True)
    binary.write_text(
        "#!/bin/sh\necho '" + ("wrong" if failure == "version" else description) + "'\n"
    )
    binary.chmod(0o755)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if failure == "linked":
        original = binary.with_name("original")
        binary.rename(original)
        binary.symlink_to(original)
    evidence = tmp_path / ".runner-image"
    evidence.mkdir()
    (evidence / f"{tool}-build.json").write_text(
        json.dumps(
            {
                "binary": str(binary)
                if failure != "path"
                else str(tmp_path / "outside"),
                "binary_sha256": "0" * 64 if failure == "checksum" else digest,
                "version": version,
                "description": description,
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
    source = (root / f"macos/image/refresh-{tool}.sh").read_text()
    source = source.split("refresh_" + tool.replace("-", "_") + "() {", 1)[0]
    source = source.replace("/opt/homebrew/Cellar", str(cellar)).replace(
        "/usr/bin/codesign", "codesign"
    )
    result = subprocess.run(
        ["/bin/bash", "-s"],
        input=source + "\nverify_" + tool.replace("-", "_") + "\n",
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
    assert ("_sha256=" in result.stdout) == (not failure)


@pytest.mark.parametrize("tool", ["guest-agent", "git-lfs"])
@pytest.mark.parametrize("status", [0, 27])
def test_module_cache_cleanup_handles_read_only_directories_and_keeps_exit_status(
    tmp_path, status, tool
):
    root = Path(__file__).resolve().parents[1]
    source = (root / f"macos/image/refresh-{tool}.sh").read_text()
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
            "set -eu; " + tool.replace("-", "_") + "_work=$1; " + trap + '; exit "$2"',
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
