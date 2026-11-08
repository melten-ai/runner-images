import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from runner_images import registry
from runner_images.qualification import digest, finalize, qualify, record
from runner_images.rebuild import input_id


def prepare(example):
    root, bundle, image, metadata = example
    path = root / ".github/workflows/release-tart.yml"
    path.parent.mkdir(parents=True)
    path.write_text("synthetic workflow")
    qualify(bundle, image, metadata)
    finalize(bundle, image)
    return root, bundle, image, metadata


def registry_fixture(monkeypatch, qualification_hash, inputs):
    config = json.dumps(
        {
            "config": {
                "Labels": {
                    "ai.melten.qualification": qualification_hash,
                    "ai.melten.inputs": inputs,
                }
            }
        }
    ).encode()
    descriptor = "sha256:" + hashlib.sha256(config).hexdigest()
    raw = json.dumps({"config": {"digest": descriptor}}).encode()
    calls = []

    def fetch(url, headers):
        calls.append((url, headers))
        if "/token?" in url:
            return b'{"token":"synthetic-registry-token"}'
        return config if "/blobs/" in url else raw

    monkeypatch.setattr(registry, "fetch", fetch)
    return calls, "sha256:" + hashlib.sha256(raw).hexdigest()


def test_upload_readback_record_and_rerun(example, monkeypatch):
    root, bundle, image, metadata = prepare(example)
    calls, identity = registry_fixture(
        monkeypatch, digest(bundle / "qualification.json"), input_id(root, "xcode")
    )
    uploads = []
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kw: uploads.append(command)
    )
    monkeypatch.setenv("TART_REGISTRY_PASSWORD", "synthetic-password")
    registry.publish(bundle, root, image)
    assert uploads[0][:3] == ["tart", "push", metadata["image"]]
    receipt = json.loads((bundle / "publication.json").read_text())
    assert receipt["reference"].endswith("@" + identity)
    assert all("synthetic-password" not in json.dumps(c) for c in uploads)
    assert "Authorization" in calls[0][1]
    destination = record(bundle, root, metadata["source_commit"])
    registry.record_publication(bundle, root, destination)
    original = (destination / "publication.json").read_bytes()
    registry.record_publication(bundle, root, destination)
    assert (destination / "publication.json").read_bytes() == original
    (destination / "publication.json").write_text("preserve immutable evidence")
    with pytest.raises(ValueError, match="immutable"):
        registry.record_publication(bundle, root, destination)


@pytest.mark.parametrize("failure", ["before", "upload", "during", "readback"])
def test_upload_failures_never_issue_completion_proof(example, monkeypatch, failure):
    root, bundle, image, _ = prepare(example)
    registry_fixture(
        monkeypatch,
        "wrong-qualification"
        if failure == "readback"
        else digest(bundle / "qualification.json"),
        input_id(root, "xcode"),
    )

    def upload(*args, **kwargs):
        if failure == "upload":
            raise subprocess.CalledProcessError(1, ["tart"])
        if failure == "during":
            (image / "disk.img").write_bytes(b"changed during upload")

    monkeypatch.setattr(subprocess, "run", upload)
    if failure == "before":
        (image / "nvram.bin").write_bytes(b"changed before upload")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        registry.publish(bundle, root, image)
    assert not (bundle / "publication.json").exists()


def test_hosted_record_rejects_forged_missing_or_wrong_revision_proof(
    example, monkeypatch
):
    root, bundle, image, metadata = prepare(example)
    registry_fixture(
        monkeypatch, digest(bundle / "qualification.json"), input_id(root, "xcode")
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    destination = record(bundle, root, metadata["source_commit"])
    with pytest.raises(FileNotFoundError):
        registry.record_publication(bundle, root, destination)
    registry.publish(bundle, root, image)
    path = bundle / "publication.json"
    original = json.loads(path.read_text())
    for key, value in [
        ("input_id", "old"),
        ("status", "failed"),
        ("qualification_sha256", "forged"),
        ("reference", 42),
        ("reference", "ghcr.io/untrusted/image@sha256:" + "a" * 64),
    ]:
        path.write_text(json.dumps({**original, key: value}))
        with pytest.raises(ValueError):
            registry.record_publication(bundle, root, destination)
    path.write_text(json.dumps(original))
    registry_fixture(monkeypatch, "forged", input_id(root, "xcode"))
    with pytest.raises(ValueError):
        registry.record_publication(bundle, root, destination)
    assert not (destination / "publication.json").exists()


def test_manifest_rejects_bad_destination_and_corrupt_bytes(monkeypatch):
    package = "ghcr.io/melten-ai/runner-images/macos-26-xcode"
    _, identity = registry_fixture(monkeypatch, "receipt", "inputs")
    assert registry.manifest(package + "@" + identity, "receipt", "inputs").endswith(
        identity
    )
    for reference in (
        "ghcr.io/untrusted/image:latest",
        package + "@sha256:" + "0" * 64,
    ):
        with pytest.raises(ValueError):
            registry.manifest(reference, "receipt", "inputs")
    for descriptor in ("invalid", "sha256:" + "0" * 64):
        raw = json.dumps({"config": {"digest": descriptor}}).encode()
        monkeypatch.setattr(
            registry,
            "fetch",
            lambda url, headers, raw=raw: (
                b'{"token":"synthetic"}' if "/token?" in url else raw
            ),
        )
        with pytest.raises(ValueError):
            registry.manifest(package + ":macos-26-xcode-1-1", "receipt", "inputs")


def test_fetch_has_timeout_and_returns_bytes(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"synthetic manifest"

    def open_url(request, **kwargs):
        calls.append((request.full_url, kwargs))
        return Response()

    monkeypatch.setattr(registry, "urlopen", open_url)
    assert registry.fetch("https://ghcr.io/example", {}) == b"synthetic manifest"
    assert calls[0][1]["timeout"] == 90


def test_publisher_refuses_dirty_recipe_before_touching_registry(tmp_path):
    subprocess.run(
        ["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True
    )
    (tmp_path / "uncommitted-recipe.py").write_text("synthetic dirty source")
    command = Path(__file__).resolve().parents[1] / "scripts/publish-candidate.py"
    result = subprocess.run(
        ["python3", str(command), "nonexistent-bundle"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "clean, committed recipe" in result.stderr
    assert not (tmp_path / "qualifications").exists()
