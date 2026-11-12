import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "refresh_aws_cli",
    Path(__file__).resolve().parents[1] / "macos/image/refresh-aws-cli.py",
)
assert spec and spec.loader
aws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aws)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    cellar = tmp_path / "awscli"
    site = cellar / "2.0.0/libexec/lib/python3.15/site-packages"
    for name, version in (("awscli", "2.0.0"), ("urllib3", "2.6.3")):
        metadata = site / f"{name}-{version}.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    package = site / "urllib3"
    package.mkdir()
    (package / "__init__.py").write_text("# original package\n")
    (package / "response.py").write_text("# original response\n")
    (site / "unrelated").write_text("preserve this dependency")
    receipt = tmp_path / "evidence/aws.json"
    plan = {
        "source": {},
        "patches": [],
        "modules": {"response.py": hashlib.sha256(b"# patched response\n").hexdigest()},
    }
    monkeypatch.setattr(aws, "source_inputs", lambda: plan)
    version = aws.build_version(plan)
    wheel = tmp_path / "example.whl"
    wheel.write_bytes(b"verified wheel")
    monkeypatch.setattr(aws, "build_wheel", lambda *args: wheel)

    def install(command, **kwargs):
        assert "--no-deps" in command and "--no-compile" in command
        assert "--isolated" in command and kwargs["check"]
        target = Path(command[command.index("--target") + 1])
        package = target / "urllib3"
        assert not package.exists()
        package.mkdir()
        (package / "__init__.py").write_text("# patched package\n")
        (package / "response.py").write_text("# patched response\n")
        metadata = target / f"urllib3-{version}.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(f"Name: urllib3\nVersion: {version}\n")

    monkeypatch.setattr(aws.subprocess, "run", install)
    return cellar, site, receipt, plan


@pytest.mark.parametrize("failure", [None, "staged", "activated", "receipt"])
def test_aws_refresh_preserves_dependencies_and_rolls_back_failed_activation(
    installed, monkeypatch, failure
):
    cellar, site, receipt, plan = installed
    calls = []
    receipt.parent.mkdir()
    receipt.write_text('{"prior":true}\n')
    prior = receipt.read_bytes()

    def probe(python, cli, candidate, aws_version, urllib_version, work):
        calls.append(candidate)
        assert aws_version == "2.0.0" and urllib_version == aws.build_version(plan)
        assert aws.package_version(candidate, "urllib3") == urllib_version
        assert (candidate / "unrelated").read_text() == "preserve this dependency"
        assert (candidate / "urllib3/response.py").read_text() == "# patched response\n"
        if (
            failure == "staged"
            and candidate != site
            or failure == "activated"
            and candidate == site
        ):
            raise subprocess.CalledProcessError(7, "AWS lifecycle probe")

    monkeypatch.setattr(aws, "probe", probe)
    if failure == "receipt":
        monkeypatch.setattr(
            aws,
            "save_receipt",
            lambda *args: (_ for _ in ()).throw(OSError("receipt failure")),
        )
    if failure:
        with pytest.raises((OSError, subprocess.CalledProcessError)):
            aws.refresh(cellar, receipt)
        assert (site / "urllib3/response.py").read_text() == "# original response\n"
        assert aws.package_version(site, "urllib3") == "2.6.3"
        assert receipt.read_bytes() == prior
    else:
        aws.refresh(cellar, receipt)
        report = json.loads(receipt.read_text())
        assert report["status"] == "backported"
        assert report["package_sha256"] == aws.package_hashes(site)
        assert report["source_inputs"] == plan
        # Replay verifies the existing identity rather than rebuilding it.
        monkeypatch.setattr(
            aws, "build_wheel", lambda *args: pytest.fail("rebuilt on replay")
        )
        aws.refresh(cellar, receipt)
        assert calls[-1] == site
    assert not list(site.parent.glob(".runner-image-aws-*"))


@pytest.mark.parametrize("change", ["source", "receipt", "version", "missing"])
def test_aws_verification_rejects_changed_source_receipt_and_missing_evidence(
    installed, monkeypatch, change
):
    cellar, site, receipt, plan = installed
    monkeypatch.setattr(aws, "probe", lambda *args: None)
    aws.refresh(cellar, receipt)
    if change == "source":
        (site / "urllib3/response.py").write_text("# tampered\n")
    elif change == "receipt":
        data = json.loads(receipt.read_text())
        data["source_inputs"]["patches"].append({"unexpected": True})
        receipt.write_text(json.dumps(data))
    elif change == "version":
        metadata = next(site.glob("urllib3-*.dist-info")) / "METADATA"
        metadata.write_text("Name: urllib3\nVersion: 2.8.0\n")
    else:
        receipt.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        aws.verify_installed(cellar, receipt)


def test_download_checksum_failure_never_accepts_changed_source(tmp_path, monkeypatch):
    path = tmp_path / "source.tar.gz"

    def download(command, **kwargs):
        assert command[:3] == ["curl", "-fsSL", "--retry"]
        path.write_bytes(b"changed source")

    monkeypatch.setattr(aws.subprocess, "run", download)
    with pytest.raises(ValueError, match="checksum"):
        aws.download("https://example.com/source", "0" * 64, path)


def test_cli_probe_environment_excludes_real_credentials(tmp_path, monkeypatch):
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "PYTHONSTARTUP",
        "PIP_EXTRA_INDEX_URL",
    ):
        monkeypatch.setenv(name, "synthetic-private-value")
    env = aws.clean_environment(tmp_path / "site", tmp_path)
    assert not any(
        name in env
        for name in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "AWS_SESSION_TOKEN",
            "AWS_PROFILE",
            "PYTHONSTARTUP",
            "PIP_EXTRA_INDEX_URL",
        )
    )
    assert env["AWS_ACCESS_KEY_ID"] == "example-access-key"
    assert env["AWS_EC2_METADATA_DISABLED"] == "true"


@pytest.mark.parametrize("bad_version", [False, True])
def test_backport_applies_inside_a_parent_git_checkout(
    tmp_path, monkeypatch, bad_version
):
    import io
    import tarfile
    import zipfile

    # Homebrew's Cellar lives below its own Git checkout. A normal git apply
    # can silently skip paths in such a nested working directory.
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    work = tmp_path / "owned-build"
    work.mkdir()
    original = b"old response\n"
    patched = b"fixed response\n"
    plan = {
        "source": {"url": "https://example.com/source", "sha256": "unused"},
        "patches": [{"commit": "example", "patch_sha256": "unused"}],
        "modules": {"response.py": hashlib.sha256(patched).hexdigest()},
    }

    def download(url, digest, target):
        if target.name == "source.tar.gz":
            with tarfile.open(target, "w:gz") as archive:
                info = tarfile.TarInfo("urllib3-2.6.3/src/urllib3/response.py")
                info.size = len(original)
                archive.addfile(info, io.BytesIO(original))
                info = tarfile.TarInfo("urllib3-2.6.3/PKG-INFO")
                info.size = 0
                archive.addfile(info, io.BytesIO())
        else:
            target.write_text(
                "diff --git a/src/urllib3/response.py b/src/urllib3/response.py\n"
                "--- a/src/urllib3/response.py\n+++ b/src/urllib3/response.py\n"
                "@@ -1 +1 @@\n-old response\n+fixed response\n"
            )

    run = subprocess.run

    def command(args, **kwargs):
        if args[0] == "git":
            return run(args, **kwargs)
        assert "wheel" in args
        assert kwargs["env"]["SETUPTOOLS_SCM_PRETEND_VERSION"] == aws.build_version(
            plan
        )
        wheels = work / "wheels"
        wheels.mkdir()
        with zipfile.ZipFile(wheels / "example.whl", "w") as wheel:
            wheel.write(
                work / "source/urllib3-2.6.3/src/urllib3/response.py",
                "urllib3/response.py",
            )
            version = aws.build_version(plan)
            wheel.writestr(
                f"urllib3-{version}.dist-info/METADATA",
                "Name: urllib3\nVersion: "
                + ("2.6.3" if bad_version else version)
                + "\n",
            )

    monkeypatch.setattr(aws, "download", download)
    monkeypatch.setattr(aws.subprocess, "run", command)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / ".git"))
    if bad_version:
        with pytest.raises(ValueError, match="version identity"):
            aws.build_wheel(work, Path("/example/python"), plan)
    else:
        result = aws.build_wheel(work, Path("/example/python"), plan)
        assert result.is_file()
    assert (
        work / "source/urllib3-2.6.3/src/urllib3/response.py"
    ).read_bytes() == patched


def test_backport_policy_tracks_source_identity():
    root = Path(__file__).resolve().parents[1]
    version = aws.build_version(aws.source_inputs())
    rules = [
        r
        for r in json.loads((root / "macos/grype.json").read_text())["ignore"]
        if r["package"]["name"] == "^urllib3$"
    ]
    advisories = {
        "GHSA-mf9v-mfxr-j63j",
        "GHSA-qccp-gfcp-xxvc",
        "GHSA-vxq7-64xx-v4gw",
        "GHSA-8988-9cw3-xx77",
    }
    assert len(rules) == 12
    for cli in ("2.39.6", "2.40.0", "2.40.1"):
        scoped = [r for r in rules if f"/awscli/{cli}/" in r["package"]["location"]]
        assert len(scoped) == 4
        assert {r["vulnerability"] for r in scoped} == advisories
    assert all(
        r["package"]["version"] == version
        and f"urllib3-{version}.dist-info/METADATA" in r["package"]["location"]
        for r in rules
    )
    for path in ("scripts/check-macos-security.py", "macos/security-dispositions.md"):
        assert version in (root / path).read_text()


def test_graphicsmagick_policy_tracks_exact_scope():
    root = Path(__file__).resolve().parents[1]
    rules = [
        rule
        for rule in json.loads((root / "macos/grype.json").read_text())["ignore"]
        if rule["package"]["name"] == "^graphicsmagick$"
    ]
    advisories = {"CVE-2007-0770", "CVE-2025-32460"}
    versions = {"1.3.49", "1.3.49_1"}
    assert len(rules) == 4
    assert {
        (
            rule["vulnerability"],
            rule["package"]["version"],
            rule["package"]["type"],
            rule["package"]["location"],
        )
        for rule in rules
    } == {
        (
            advisory,
            version,
            "homebrew",
            f"opt/homebrew/Cellar/graphicsmagick/{version}/.brew/graphicsmagick.rb",
        )
        for advisory in advisories
        for version in versions
    }
