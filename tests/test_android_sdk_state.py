import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "macos/image/android-sdk-state.sh"


def run_detect(home: Path) -> str:
    result = subprocess.run(
        ["bash", str(STATE), "detect"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def tools_bin(tools: Path, version: str = "23.0") -> None:
    entry = tools / version
    (entry / "bin").mkdir(parents=True)
    (entry / "source.properties").write_text(f"Pkg.Revision={version}\n")
    script = entry / "bin/sdkmanager"
    script.write_text(f"#!/bin/sh\necho {version}\n")
    script.chmod(0o755)


def test_detect_canonical_latest_alias(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools_bin(tools)
    (tools / "latest").symlink_to("23.0")
    assert run_detect(home) == "present"


def test_detect_versioned_only_without_latest(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools_bin(tools, "22.0")
    assert run_detect(home) == "present"


def test_detect_absent_without_cmdline_tools(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_detect(home) == "absent"


def test_detect_unsafe_dangling_latest_without_versioned(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools.mkdir(parents=True)
    (tools / "latest").symlink_to("99.0")
    assert run_detect(home) == "unsafe"


def test_detect_unsafe_latest_escapes_sdk(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools.mkdir(parents=True)
    outside = home / "outside"
    outside.mkdir()
    (outside / "bin").mkdir()
    (outside / "bin/sdkmanager").write_text("#!/bin/sh\necho 1\n")
    (outside / "bin/sdkmanager").chmod(0o755)
    (tools / "latest").symlink_to("../../outside")
    assert run_detect(home) == "unsafe"


def test_detect_unsafe_sdk_symlink(tmp_path):
    home = tmp_path / "home"
    target = home / "real-sdk/cmdline-tools"
    tools_bin(target)
    (target / "latest").symlink_to("23.0")
    home.mkdir(parents=True, exist_ok=True)
    (home / "android-sdk").symlink_to(home / "real-sdk")
    assert run_detect(home) == "unsafe"


def test_detect_unsafe_extra_symlink_under_cmdline_tools(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools_bin(tools)
    (tools / "latest").symlink_to("23.0")
    external = tmp_path / "external"
    external.mkdir()
    (tools / "platforms").symlink_to(external, target_is_directory=True)
    assert run_detect(home) == "unsafe"


def test_assert_writable_rejects_unsafe_layout(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools.mkdir(parents=True)
    (tools / "latest").symlink_to("../../outside")
    result = subprocess.run(
        ["bash", str(STATE), "assert-writable"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "unsafe" in result.stderr.lower()


def test_detect_unsafe_dangling_latest_rejects_despite_versioned_tools(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools_bin(tools, "21.0")
    (tools / "latest").symlink_to("99.0")
    assert run_detect(home) == "unsafe"


def test_detect_unsafe_dangling_extra_symlink_with_versioned_tools(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools_bin(tools, "21.0")
    (tools / "stale-link").symlink_to("missing-entry")
    assert run_detect(home) == "unsafe"


def test_detect_unsafe_dangling_cmdline_tools_root(tmp_path):
    home = tmp_path / "home"
    (home / "android-sdk").mkdir(parents=True)
    (home / "android-sdk/cmdline-tools").symlink_to("nowhere")
    assert run_detect(home) == "unsafe"


def test_detect_unsafe_sdk_root_symlink_without_cmdline_tools(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    real = home / "real-sdk"
    real.mkdir()
    (home / "android-sdk").symlink_to(real)
    assert run_detect(home) == "unsafe"
