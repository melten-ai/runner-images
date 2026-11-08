import json
import shutil

import pytest

from runner_images.qualification import fingerprint


@pytest.fixture
def example(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    image = tmp_path / "image"
    image.mkdir()
    for name in ("config.json", "disk.img", "nvram.bin"):
        (image / name).write_bytes(b"synthetic VM content")
    (image / "config.json").write_text(
        json.dumps({"cpuCount": 4, "macAddress": "02:00:00:00:00:01"})
    )
    reference = {
        "schema_version": 1,
        "os": "darwin",
        "arch": "arm64",
        "image_os": "macos26",
        "image_version": "reference-build",
        "records": ["cmd bash", "cmd rustc"],
    }
    (bundle / "reference.json").write_text(json.dumps(reference))
    candidate = {**reference, "records": ["cmd bash"]}
    (bundle / "inventory.json").write_text(json.dumps(candidate))
    (bundle / "accepted-missing.txt").write_text("cmd rustc\n")
    (bundle / "toolchain-check.txt").write_text("native checks passed\n")
    (bundle / "toolset.json").write_text('{"example":"toolset"}')
    (bundle / "base-fingerprint.json").write_text(json.dumps(fingerprint(image)))
    for target, origin in (
        ("snapshots/github-hosted/macos-26.json", "reference.json"),
        ("macos/accepted-missing/xcode.txt", "accepted-missing.txt"),
    ):
        path = repository / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle / origin, path)
    metadata = {
        "image": "macos-26-xcode-12345-1",
        "profile": "xcode",
        "base": "ghcr.io/cirruslabs/macos-tahoe-xcode:latest",
        "source_commit": "a" * 40,
        "reference_commit": "a" * 40,
        "runner_version": "2.338.2",
        "run_url": "https://github.com/circlesac/runner-images/actions/runs/12345",
    }
    return repository, bundle, image, metadata
