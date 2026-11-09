import os
import subprocess
from pathlib import Path

import pytest


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
