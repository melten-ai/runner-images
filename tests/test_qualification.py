import json
import shutil
import subprocess
from pathlib import Path

import pytest

from runner_images.qualification import (
    digest,
    finalize,
    fingerprint,
    qualify,
    read_object,
    record,
)


def test_comparison_then_image_identity_then_immutable_record(example):
    repo, bundle, image, metadata = example
    qualify(bundle, image, metadata)
    assert not (bundle / "qualification.json").exists()
    assert (
        read_object(bundle / "comparison-passed.json")["status"] == "comparison-passed"
    )
    with pytest.raises(ValueError):
        qualify(bundle, image, metadata)
    finalize(bundle, image)
    receipt = read_object(bundle / "qualification.json")
    assert receipt["status"] == "passed"
    assert receipt["image_files"] == fingerprint(image)
    assert receipt["inventory_sha256"] == digest(bundle / "inventory.json")
    destination = record(bundle, repo, metadata["source_commit"])
    assert (destination / "qualification.json").read_bytes() == (
        bundle / "qualification.json"
    ).read_bytes()
    assert (destination / "inventory.json").read_bytes() == (
        bundle / "inventory.json"
    ).read_bytes()
    assert "KST" in (destination / "README.md").read_text()
    assert "Actions run" in (destination / "README.md").read_text()
    assert record(bundle, repo, metadata["source_commit"]) == destination
    # A damaged previously published record must never be silently overwritten.
    (destination / "inventory.json").write_text("preserve this evidence")
    with pytest.raises(ValueError):
        record(bundle, repo, metadata["source_commit"])
    assert (destination / "inventory.json").read_text() == "preserve this evidence"


def test_changed_image_cannot_receive_a_pass_receipt(example):
    _, bundle, image, metadata = example
    qualify(bundle, image, metadata)
    (image / "disk.img").write_bytes(b"changed bytes")
    with pytest.raises(ValueError):
        finalize(bundle, image)
    assert not (bundle / "qualification.json").exists()


def test_final_receipt_is_never_replaced(example):
    _, bundle, image, metadata = example
    qualify(bundle, image, metadata)
    finalize(bundle, image)
    first = (bundle / "qualification.json").read_bytes()
    qualify(bundle, image, metadata)
    with pytest.raises(ValueError):
        finalize(bundle, image)
    assert (bundle / "qualification.json").read_bytes() == first


@pytest.mark.parametrize("kind", ["missing", "empty", "symlink"])
def test_invalid_vm_files_are_not_fingerprinted(example, kind):
    _, _, image, _ = example
    target = image / "disk.img"
    target.unlink()
    if kind == "empty":
        target.touch()
    if kind == "symlink":
        target.symlink_to(image / "nvram.bin")
    with pytest.raises(ValueError):
        fingerprint(image)


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile", "unknown"),
        ("image", "../unsafe"),
        ("source_commit", "main"),
    ],
)
def test_invalid_build_metadata(example, field, value):
    _, bundle, image, metadata = example
    metadata[field] = value
    with pytest.raises(ValueError):
        qualify(bundle, image, metadata)
    assert not (bundle / "qualification.json").exists()


def test_mismatch_has_diagnostics_but_no_verified_record(example):
    _, bundle, image, metadata = example
    (bundle / "accepted-missing.txt").write_text("")
    with pytest.raises(ValueError):
        qualify(bundle, image, metadata)
    assert read_object(bundle / "comparison.json")["unexpected_missing"] == [
        "cmd rustc"
    ]
    assert not (bundle / "comparison-passed.json").exists()
    assert not (bundle / "qualification.json").exists()


@pytest.mark.parametrize("content", ["[]", "{}", '{"disk.img":"bad"}'])
def test_invalid_base_identity(example, content):
    _, bundle, image, metadata = example
    (bundle / "base-fingerprint.json").write_text(content)
    with pytest.raises(ValueError):
        qualify(bundle, image, metadata)


@pytest.mark.parametrize(
    "change",
    [
        "status",
        "schema",
        "source",
        "run",
        "private-base",
        "inventory",
        "policy",
        "comparison",
        "image-files",
        "image-hash",
        "build",
        "profile",
        "image-keys",
        "runner-version",
        "time",
        "naive-time",
    ],
)
def test_untrusted_or_changed_bundle_is_not_recorded(example, change):
    repo, bundle, image, metadata = example
    qualify(bundle, image, metadata)
    finalize(bundle, image)
    file = bundle / "qualification.json"
    receipt = json.loads(file.read_text())
    if change == "status":
        receipt["status"] = "comparison-passed"
    if change == "schema":
        receipt["schema_version"] = True
    if change == "source":
        receipt["build"]["source_commit"] = "b" * 40
    if change == "run":
        receipt["build"]["run_url"] = "https://example.com/untrusted"
    if change == "private-base":
        receipt["build"]["base"] = "private-image"
    if change == "inventory":
        (bundle / "inventory.json").write_text("tampered")
    if change == "policy":
        (repo / "macos/accepted-missing/xcode.txt").write_text("")
    if change == "comparison":
        receipt["comparison"]["extra"] = ["cmd fake"]
    if change == "image-files":
        receipt["image_files"] = []
    if change == "image-hash":
        receipt["image_files"]["disk.img"] = "bad"
    if change == "build":
        receipt["build"] = []
    if change == "image-keys":
        receipt["image_files"] = {"disk.img": "a" * 64}
    if change == "runner-version":
        receipt["build"]["runner_version"] = "unknown"
    if change == "time":
        del receipt["qualified_at"]
    if change == "naive-time":
        receipt["qualified_at"] = "2026-11-07T12:00:00"
    if change == "profile":
        receipt["build"]["profile"] = "unknown"
    file.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        record(bundle, repo, metadata["source_commit"])
    assert not (repo / "qualifications").exists()


def test_git_recording_and_rerun_preserve_remote_history(example, tmp_path):
    repo, bundle, image, metadata = example
    root = Path(__file__).resolve().parents[1]
    for directory in ("scripts", "runner_images"):
        shutil.copytree(
            root / directory,
            repo / directory,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    (repo / ".gitignore").write_text("__pycache__/\n")
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)], capture_output=True, check=True
    )

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("config", "user.name", "Example Maintainer")
    git("config", "user.email", "maintainer@example.com")
    git("add", ".")
    git("commit", "-m", "source")
    revision = git("rev-parse", "HEAD")
    metadata["source_commit"] = metadata["reference_commit"] = revision
    git("remote", "add", "origin", str(remote))
    git("push", "-u", "origin", "main")
    qualify(bundle, image, metadata)
    finalize(bundle, image)
    command = ["bash", "scripts/commit-qualification.sh", str(bundle), "main"]
    subprocess.run(command, cwd=repo, capture_output=True, text=True, check=True)
    first = git("rev-parse", "HEAD")
    assert git("ls-remote", "origin", "refs/heads/main").split()[0] == first
    assert git("rev-list", "--count", "HEAD") == "2"
    # A record-job rerun checks out the original dispatched source again.
    git("checkout", "-B", "main", revision)
    subprocess.run(command, cwd=repo, capture_output=True, text=True, check=True)
    assert git("rev-parse", "HEAD") == first
    assert git("ls-remote", "origin", "refs/heads/main").split()[0] == first


def test_native_check_evidence_is_required(example):
    _, bundle, image, metadata = example
    (bundle / "toolchain-check.txt").write_text("")
    with pytest.raises(ValueError):
        qualify(bundle, image, metadata)
    assert not (bundle / "comparison-passed.json").exists()


def test_local_receipt_binds_sbom_and_preserves_immutable_copy(example):
    repo, bundle, image, metadata = example
    metadata.update(image="macos-26-xcode-local-" + "c" * 32, run_url="")
    path = bundle / "sbom.spdx.json"
    path.write_text('{"spdxVersion":"SPDX-2.3","packages":[{"name":"example"}]}')
    qualify(bundle, image, metadata)
    finalize(bundle, image)
    destination = record(bundle, repo, metadata["source_commit"])
    assert "local Apple Silicon Mac" in (destination / "README.md").read_text()
    assert (destination / path.name).read_bytes() == path.read_bytes()
    original = path.read_text()
    path.write_text("{}")
    with pytest.raises(ValueError, match="SBOM"):
        record(bundle, repo, metadata["source_commit"])
    path.write_text(original)
    (destination / path.name).write_text("immutable existing evidence")
    with pytest.raises(ValueError, match="immutable"):
        record(bundle, repo, metadata["source_commit"])


def test_empty_guest_sbom_cannot_receive_qualification(example):
    _, bundle, image, metadata = example
    (bundle / "sbom.spdx.json").write_text('{"spdxVersion":"SPDX-2.3","packages":[]}')
    with pytest.raises(ValueError, match="SBOM"):
        qualify(bundle, image, metadata)
    assert not (bundle / "comparison-passed.json").exists()
