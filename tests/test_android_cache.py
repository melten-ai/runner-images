import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MACOS_BASH = Path("/bin/bash")


def _zip_executable_entry(name: str, body: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = 0o755 << 16
    return info


def _write_cli_tools_zip(path: Path, version: str) -> None:
    sdkmanager = (
        f"#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >>"$SDKMANAGER_LOG"\n'
        f'case "$1" in --version) echo {version};; *) echo {version};; esac\n'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("cmdline-tools/source.properties", f"Pkg.Revision={version}\n")
        archive.writestr(
            _zip_executable_entry("cmdline-tools/bin/sdkmanager", sdkmanager),
            sdkmanager,
        )
    path.write_bytes(buffer.getvalue())


def _installer_payload() -> str:
    return (
        (ROOT / "macos/image/android-sdk-state.sh").read_text()
        + "\n"
        + (ROOT / "macos/image/install-android.sh").read_text()
    )


@pytest.mark.parametrize("failure", ["", "runtime", "alias"])
@pytest.mark.parametrize("layout", ["versioned", "legacy"])
def test_android_retirement_requires_working_replacement_and_preserves_other_data(
    tmp_path, failure, layout
):
    sdk = tmp_path / "android-sdk"
    tools = sdk / "cmdline-tools"
    for version in ("16.0", "20.0", "21.0", "15.0"):
        target = tools / version
        (target / "bin").mkdir(parents=True)
        (target / "source.properties").write_text(
            "Pkg.Revision=" + ("unexpected" if version == "15.0" else version) + "\n"
        )
        (target / "bin/sdkmanager").write_text(
            "#!/bin/sh\necho "
            + ("wrong" if failure == "runtime" and version == "20.0" else version)
            + "\n"
        )
        (target / "bin/sdkmanager").chmod(0o755)
    if layout == "legacy":
        import shutil

        shutil.copytree(
            tools / ("16.0" if failure == "alias" else "20.0"), tools / "latest"
        )
    else:
        (tools / "latest").symlink_to("16.0" if failure == "alias" else "20.0")
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep").touch()
    (tools / "14.0").symlink_to(external, target_is_directory=True)
    source = (
        (Path(__file__).resolve().parents[1] / "macos/image/install-android.sh")
        .read_text()
        .split("JAVA_FEATURE_VERSION=", 1)[0]
    )
    result = subprocess.run(
        ["/bin/bash", "-s", "--", str(sdk), "20.0"],
        input=source + '\nretire_old_android_tools "$1" "$2"\necho completed\n',
        text=True,
        capture_output=True,
        env=os.environ,
        timeout=10,
    )
    assert (result.returncode == 0) == (not failure), result.stdout + result.stderr
    assert (tools / "16.0").exists() == bool(failure)
    assert all(
        (tools / v / "source.properties").exists() for v in ("20.0", "21.0", "15.0")
    )
    assert (tools / "latest").is_symlink() == (layout == "versioned")
    assert (tools / "14.0").is_symlink() and (external / "keep").exists()
    if failure:
        assert "completed" not in result.stdout


@pytest.mark.parametrize("old_version", ["16.0", "20.0", "21.0"])
@pytest.mark.parametrize("fail_link", [False, True])
def test_legacy_latest_is_promoted_only_after_validation_and_restored_on_failure(
    tmp_path, old_version, fail_link
):
    sdk = tmp_path / "sdk"
    tools = sdk / "cmdline-tools"
    work = tmp_path / "work"
    work.mkdir()
    for name, version in (("20.0", "20.0"), ("latest", old_version)):
        entry = tools / name
        (entry / "bin").mkdir(parents=True)
        (entry / "source.properties").write_text("Pkg.Revision=" + version + "\n")
        (entry / "bin/sdkmanager").write_text("#!/bin/sh\necho " + version + "\n")
        (entry / "bin/sdkmanager").chmod(0o755)
    (tools / "latest/keep").touch()
    fake = tmp_path / "bin"
    fake.mkdir()
    if fail_link:
        (fake / "ln").write_text("#!/bin/sh\nexit 17\n")
        (fake / "ln").chmod(0o755)
    source = (
        (Path(__file__).resolve().parents[1] / "macos/image/install-android.sh")
        .read_text()
        .split("JAVA_FEATURE_VERSION=", 1)[0]
    )
    r = subprocess.run(
        ["/bin/bash", "-s", "--", str(sdk), "20.0", str(work)],
        input=source + '\nactivate_android_tools "$1" "$2" "$3"\n',
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"]},
        timeout=10,
    )
    assert (r.returncode == 0) == (not fail_link), r.stdout + r.stderr
    if fail_link:
        assert not (tools / "latest").is_symlink() and (tools / "latest/keep").exists()
    else:
        assert (tools / "latest").is_symlink() and (
            tools / "latest"
        ).resolve() == tools / "20.0"
        old = (
            work / "previous-command-line-tools"
            if old_version == "20.0"
            else tools / old_version
        )
        assert (old / "keep").exists()


@pytest.mark.parametrize("failure", ["", "metadata", "binary", "escape"])
def test_package_verification_checks_installed_metadata_and_material_files(
    tmp_path, failure
):
    sdk = tmp_path / "sdk"
    package = "platform-tools"
    target = sdk / package
    target.mkdir(parents=True)
    (target / "package.xml").write_text(
        '<repository><localPackage path="'
        + ("wrong" if failure == "metadata" else package)
        + '"/></repository>'
    )
    (target / "adb").write_text("#!/bin/sh\nexit 0\n")
    (target / "adb").chmod(0o644 if failure == "binary" else 0o755)
    if failure == "escape":
        package = "../outside"
    source = (
        (Path(__file__).resolve().parents[1] / "macos/image/install-android.sh")
        .read_text()
        .split("JAVA_FEATURE_VERSION=", 1)[0]
    )
    result = subprocess.run(
        ["/bin/bash", "-s", "--", str(sdk), package],
        input=source + '\nverify_android_package "$1" "$2"\n',
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert (result.returncode == 0) == (not failure), result.stdout + result.stderr


def test_cli_only_installer_macos_bash_fixture_refresh(tmp_path):
    if not MACOS_BASH.is_file():
        pytest.skip("macOS /bin/bash required")
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    for version, label in (("16.0", "16.0"),):
        entry = tools / version
        (entry / "bin").mkdir(parents=True)
        (entry / "source.properties").write_text(f"Pkg.Revision={label}\n")
        sdkmanager = entry / "bin/sdkmanager"
        sdkmanager.write_text(
            f"#!/bin/sh\n"
            f'case "$1" in --version) echo {label};; *) echo {label};; esac\n'
        )
        sdkmanager.chmod(0o755)
    (tools / "latest").symlink_to("16.0")
    jhome = home / "toolset-java"
    (jhome / "bin").mkdir(parents=True)
    java = jhome / "bin/java"
    java.write_text("#!/bin/sh\nexit 0\n")
    java.chmod(0o755)
    (home / ".runner-image").mkdir(parents=True)
    (home / ".runner-image/github-macos-toolset.json").write_text(
        json.dumps({"java": {"arm64": {"default": "21"}}, "android": {}})
    )
    (home / ".runner-image/environment").write_text(f"JAVA_HOME={jhome}\n")
    env_before = (home / ".runner-image/environment").read_text()

    fixture_zip = tmp_path / "android-cli.zip"
    _write_cli_tools_zip(fixture_zip, "23.0")
    checksum = hashlib.sha256(fixture_zip.read_bytes()).hexdigest()
    manifest = json.dumps(
        {
            "version": "23.0",
            "url": "https://dl.google.com/android/repository/commandlinetools-mac-912345_latest.zip",
            "checksum_algorithm": "sha256",
            "checksum": checksum,
        }
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        """#!/bin/sh
case "$2" in
  https://dl.google.com/android/repository/commandlinetools-mac-912345_latest.zip) ;;
  *) echo unexpected-download-forbidden 1>&2; exit 22;;
esac
if [ "$1" = -fsSL ]; then
  shift
  out=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -o) out=$2; shift 2;;
      *) shift;;
    esac
  done
  cp "$CLI_TOOLS_FIXTURE" "$out"
  exit 0
fi
echo unexpected-download-forbidden 1>&2
exit 22
"""
    )
    sw_vers_body = (
        "#!/bin/sh\n"
        'case "$1" in -productVersion) echo 26.0;; -buildVersion) echo 1;; esac\n'
    )
    for name, body in {
        "sw_vers": sw_vers_body,
        "xcodebuild": '#!/bin/sh\necho "Xcode 16.0"\necho "Build version 1"\n',
    }.items():
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o755)
    curl.chmod(0o755)

    sdkmanager_log = tmp_path / "sdkmanager.log"
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "SDKMANAGER_LOG": str(sdkmanager_log),
        "CLI_TOOLS_FIXTURE": str(fixture_zip),
    }
    result = subprocess.run(
        [str(MACOS_BASH), "-s", "--", "21", manifest],
        input=_installer_payload(),
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    sdkmanager_calls = sdkmanager_log.read_text().splitlines()
    assert sdkmanager_calls and set(sdkmanager_calls) == {"--version"}
    assert not (home / "hostedtoolcache/Java_Temurin-Hotspot_jdk").exists()
    env_after = (home / ".runner-image/environment").read_text()
    assert env_after == env_before
    assert "ANDROID_HOME=" not in env_after
    assert (tools / "latest").is_symlink()
    assert (tools / "latest").resolve() == tools / "23.0"
    assert not (tools / "16.0").exists()
    assert (tools / "23.0/bin/sdkmanager").is_file()
    facts = (home / ".runner-image/android-facts").read_text()
    assert "android_maintenance=command-line-tools-only" in facts
    assert "android_packages=" not in facts
    assert "java_temurin=" not in facts


def test_cli_only_refresh_rejects_unsafe_sdk_before_download(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    tools.mkdir(parents=True)
    (tools / "latest").symlink_to("../../outside")
    (home / ".runner-image").mkdir()
    (home / ".runner-image/github-macos-toolset.json").write_text(
        json.dumps({"java": {"arm64": {"default": "21"}}})
    )
    (home / ".runner-image/environment").write_text("JAVA_HOME=/usr/bin\n")
    root = Path(__file__).resolve().parents[1]
    payload = (
        (root / "macos/image/android-sdk-state.sh").read_text()
        + "\n"
        + (root / "macos/image/install-android.sh")
        .read_text()
        .split('echo "==> Installing Android command-line tools', 1)[0]
        + "\nexit 0\n"
    )
    manifest = json.dumps(
        {
            "version": "23.0",
            "url": "https://dl.google.com/android/repository/commandlinetools-mac-123_latest.zip",
            "checksum_algorithm": "sha256",
            "checksum": "a" * 64,
        }
    )
    result = subprocess.run(
        ["bash", "-s", "--", "21", manifest],
        input=payload,
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(home)},
        timeout=10,
    )
    assert result.returncode != 0
    assert "unsafe" in (result.stdout + result.stderr).lower()


def test_inherited_android_install_requires_toolset_java(tmp_path):
    home = tmp_path / "home"
    tools = home / "android-sdk/cmdline-tools"
    (tools / "23.0/bin").mkdir(parents=True)
    (tools / "23.0/source.properties").write_text("Pkg.Revision=23.0\n")
    sdkmanager = tools / "23.0/bin/sdkmanager"
    sdkmanager.write_text("#!/bin/sh\necho 23.0\n")
    sdkmanager.chmod(0o755)
    (tools / "latest").symlink_to("23.0")
    (home / ".runner-image").mkdir(parents=True)
    (home / ".runner-image/github-macos-toolset.json").write_text(
        json.dumps({"java": {"arm64": {"default": "21"}}})
    )
    (home / ".runner-image/environment").write_text("JAVA_HOME=/missing/java\n")
    root = Path(__file__).resolve().parents[1]
    source = (
        (root / "macos/image/android-sdk-state.sh").read_text()
        + "\n"
        + (root / "macos/image/install-android.sh")
        .read_text()
        .split('echo "==> Installing Android command-line tools', 1)[0]
    )
    manifest = json.dumps(
        {
            "version": "23.0",
            "url": "https://dl.google.com/android/repository/commandlinetools-mac-123_latest.zip",
            "checksum_algorithm": "sha256",
            "checksum": "a" * 64,
        }
    )
    result = subprocess.run(
        ["/bin/bash", "-s", "--", "21", manifest],
        input=source + "\nexit 0\n",
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(home)},
        timeout=10,
    )
    assert result.returncode != 0
    assert "Toolset Java must be cached" in result.stderr


def test_sdk_revision_is_distinct_from_new_android_cli_runtime_version(tmp_path):
    tools = tmp_path / "tools"
    (tools / "bin").mkdir(parents=True)
    (tools / "source.properties").write_text("Pkg.Revision=23.0\n")
    (tools / "bin/android").write_text(
        '#!/bin/sh\ntest "$1" = --no-metrics || exit 7\necho Android CLI 0.9.0\n'
    )
    (tools / "bin/android").chmod(0o755)
    source = (
        (Path(__file__).resolve().parents[1] / "macos/image/install-android.sh")
        .read_text()
        .split("JAVA_FEATURE_VERSION=", 1)[0]
    )
    result = subprocess.run(
        ["/bin/bash", "-s", "--", str(tools)],
        input=source + '\nandroid_tools_runtime_ok "$1" 23.0\n',
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
