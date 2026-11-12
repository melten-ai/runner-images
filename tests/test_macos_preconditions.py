"""Stock Bash must reject invalid inputs before tools or durable writes."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def entrypoint(tmp_path):
    root = tmp_path / "source"
    (root / "macos/image").mkdir(parents=True)
    for name in (
        "build-image.sh",
        "rebuild.sh",
        "network.sh",
        "image/install-runner.sh",
    ):
        shutil.copyfile(ROOT / "macos" / name, root / "macos" / name)
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "external-calls"
    scripts = {
        "git": (
            'case "$*" in *rev-parse*) '
            "echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;; esac"
        ),
        "uname": (
            'if [ "$1" = -s ]; then echo "${TEST_OS:-Darwin}"; '
            'else echo "${TEST_ARCH:-arm64}"; fi'
        ),
    }
    for name in ("tart", "curl", "gh", "brew"):
        scripts[name] = f'echo {name} >>"$TEST_EXTERNAL_LOG"; exit 91'
    for name, body in scripts.items():
        path = tools / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    reference = tmp_path / "reference.json"
    reference.write_text("{}")
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text("")
    env = {
        **os.environ,
        "PATH": str(tools) + ":" + os.environ["PATH"],
        "TEST_EXTERNAL_LOG": str(log),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
    }
    return root, reference, allowlist, env, log


@pytest.mark.parametrize(
    "failure",
    [
        "image",
        "same-base",
        "profile",
        "runner-dir",
        "traversal",
        "reference",
        "allowlist",
        "bundle",
        "os",
        "arch",
    ],
)
def test_build_preconditions_stop_before_provider_calls(entrypoint, failure):
    root, reference, allowlist, env, log = entrypoint
    image, base, profile, runner_dir = (
        "candidate",
        "base",
        "xcode",
        "/Users/example/actions-runner",
    )
    if failure == "image":
        image = "../outside"
    elif failure == "same-base":
        image = base
    elif failure == "profile":
        profile = "unknown"
    elif failure == "runner-dir":
        runner_dir = "/"
    elif failure == "traversal":
        runner_dir = "/Users/example/../outside"
    elif failure == "reference":
        reference.unlink()
    elif failure == "allowlist":
        allowlist.unlink()
    elif failure == "bundle":
        bundle = root / "artifacts/tart/candidate"
        bundle.mkdir(parents=True)
        (bundle / "evidence").write_text("preserve")
    else:
        env["TEST_OS" if failure == "os" else "TEST_ARCH"] = "unsupported"
    result = subprocess.run(
        [
            "/bin/bash",
            str(root / "macos/build-image.sh"),
            image,
            base,
            profile,
            runner_dir,
            str(reference),
            str(allowlist),
        ],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not log.exists(), result.stdout + result.stderr
    if failure == "bundle":
        assert (root / "artifacts/tart/candidate/evidence").read_text() == "preserve"
    else:
        assert not (root / "artifacts").exists()
    assert not (root / "outside").exists()


@pytest.mark.parametrize("failure", ["profile", "os", "arch"])
def test_bootstrap_preconditions_stop_before_auth_or_install(entrypoint, failure):
    root, _, _, env, log = entrypoint
    profile = "unknown" if failure == "profile" else "xcode"
    if failure != "profile":
        env["TEST_OS" if failure == "os" else "TEST_ARCH"] = "unsupported"
    result = subprocess.run(
        ["/bin/bash", str(root / "macos/rebuild.sh"), profile],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not log.exists(), result.stdout + result.stderr
    assert not Path(env["XDG_STATE_HOME"]).exists()


def test_bad_runner_checksum_preserves_existing_guest_files(entrypoint):
    root, _, _, env, log = entrypoint
    home = Path(env["HOME"])
    runner = home / "runner"
    runner.mkdir(parents=True)
    marker = runner / "existing-output"
    marker.write_text("preserve")
    result = subprocess.run(
        ["/bin/bash", "-s", "--", "2.338.2", str(runner), "invalid"],
        input=(root / "macos/image/install-runner.sh").read_bytes(),
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert marker.exists(), "Invalid checksum must not remove existing guest files"
    assert marker.read_text() == "preserve"
    assert not log.exists()
