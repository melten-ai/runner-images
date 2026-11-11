"""Exercise dependency activation, integrity, rollback and replay boundaries."""

import hashlib
import io
import json
import types
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    module = types.ModuleType("android_bc")
    source = Path(__file__).resolve().parents[1] / "macos/image/refresh-android-bc.py"
    exec(compile(source.read_text(), str(source), "exec"), module.__dict__)
    root = tmp_path / "sdk/cmdline-tools/23.0"
    selected = {}
    downloads = {}
    originals = {}
    for name in ("bcprov-jdk18on", "bcpkix-jdk18on", "bcutil-jdk18on"):
        path = root / f"lib/external/org/bouncycastle/{name}/1.80.2/{name}-1.80.2.jar"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"original " + name.encode())
        path.chmod(0o640)
        originals[path] = path.read_bytes()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", "Bundle-Version: 1.86\r\n")
            jar.writestr("example.class", name)
        url = "https://example.com/" + name
        downloads[url] = archive.getvalue()
        selected[name] = {
            "version": "1.86",
            "url": url,
            "sha256": hashlib.sha256(archive.getvalue()).hexdigest(),
        }
    monkeypatch.setattr(module, "resolve", lambda: selected)
    monkeypatch.setattr(module, "fetch", lambda url: downloads[url])
    consumer = root / "lib/consumer-classpath.jar"
    with zipfile.ZipFile(consumer, "w") as archive:
        archive.writestr("resources/stamp.txt", "preserve vendor stamp")
        archive.writestr(
            "META-INF/MANIFEST.MF",
            "Class-Path: "
            + " ".join(str(p.relative_to(root / "lib")) for p in originals)
            + "\r\n\r\n",
        )
    consumer.chmod(0o640)
    originals[consumer] = consumer.read_bytes()

    def probe(candidate, paths, version):
        assert candidate == root and version == "1.86"
        assert set(paths) == set(selected)
        assert module.classpaths(root, paths)
        for name, path in paths.items():
            assert module.sha(path) == selected[name]["sha256"]
        return {"crypto": "verified", "apkanalyzer": "verified"}

    monkeypatch.setattr(module, "probe", probe)
    return module, root, selected, downloads, originals


def test_refresh_verify_and_replay_preserve_paths_modes_and_exact_closure(fixture):
    module, root, selected, downloads, originals = fixture
    receipt = module.refresh(root)
    assert receipt["inputs"] == selected
    assert module.verify(root) == receipt
    assert all(path.stat().st_mode & 0o777 == 0o640 for path in root.rglob("*.jar"))
    assert all(not path.exists() for path in originals if "bouncycastle" in str(path))
    assert (root / "lib/consumer-classpath.jar").read_bytes() != originals[
        root / "lib/consumer-classpath.jar"
    ]
    with zipfile.ZipFile(root / "lib/consumer-classpath.jar") as archive:
        assert archive.read("resources/stamp.txt") == b"preserve vendor stamp"
    downloads.clear()  # An unchanged replay must not redownload the jars.
    assert module.refresh(root) == receipt
    assert len(list(root.rglob("*.jar"))) == 4


@pytest.mark.parametrize("failure", ["checksum", "manifest", "probe", "network"])
def test_failure_leaves_original_bytes_modes_and_no_success_receipt(
    fixture, monkeypatch, failure
):
    module, root, selected, downloads, originals = fixture
    name = "bcprov-jdk18on"
    if failure == "checksum":
        selected[name]["sha256"] = "0" * 64
    elif failure == "manifest":
        selected[name]["version"] = "1.87"
    elif failure == "network":
        downloads.clear()
    else:

        def fail(*args):
            raise RuntimeError("consumer rejected candidate")

        monkeypatch.setattr(module, "probe", fail)
    with pytest.raises((ValueError, RuntimeError, KeyError)):
        module.refresh(root)
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert all(path.stat().st_mode & 0o777 == 0o640 for path in originals)
    assert not (root / "lib/runner-images-bc.json").exists()


@pytest.mark.parametrize("change", ["bytes", "receipt", "obsolete", "extra"])
def test_verification_rejects_tampering_and_vulnerable_receipts(fixture, change):
    module, root, selected, _, originals = fixture
    module.refresh(root)
    path = next(iter(module.files(root).values()))
    report = root / "lib/runner-images-bc.json"
    receipt = json.loads(report.read_text())
    if change == "bytes":
        path.write_bytes(b"tampered")
    elif change == "receipt":
        receipt["files"] = {}
    elif change == "obsolete":
        for spec in receipt["inputs"].values():
            spec["version"] = "1.80.2"
    else:
        path.with_name(path.stem + "-extra.jar").write_bytes(b"unexpected copy")
    report.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        module.verify(root)


def test_dependency_symlink_escape_is_rejected_before_download(fixture, tmp_path):
    module, root, _, downloads, originals = fixture
    path = next(iter(originals))
    external = tmp_path / "external.jar"
    external.write_bytes(b"keep")
    path.unlink()
    path.symlink_to(external)
    downloads.clear()
    with pytest.raises(ValueError, match="escapes"):
        module.refresh(root)
    assert external.read_bytes() == b"keep"


def test_failure_after_retirement_restores_original_dependencies_and_classpaths(
    fixture, monkeypatch
):
    module, root, _, _, originals = fixture

    def fail_verification(*args, **kwargs):
        assert not any(p.exists() for p in originals if "bouncycastle" in str(p)), (
            "Exercise failure after the old JARs have been retired"
        )
        raise ValueError("receipt verification failed")

    monkeypatch.setattr(module, "verify", fail_verification)
    with pytest.raises(ValueError, match="receipt verification"):
        module.refresh(root)
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert len(list(root.rglob("*.jar"))) == 4
    assert not (root / "lib/runner-images-bc.json").exists()


@pytest.mark.parametrize("failure", ["", "mixed", "preview", "checksum"])
def test_resolver_requires_matching_stable_official_closure(monkeypatch, failure):
    module = types.ModuleType("android_bc_resolver")
    source = Path(__file__).resolve().parents[1] / "macos/image/refresh-android-bc.py"
    exec(compile(source.read_text(), str(source), "exec"), module.__dict__)
    requested = []

    def fetch(url):
        requested.append(url)
        assert url.startswith("https://repo.maven.apache.org/maven2/org/bouncycastle/")
        if url.endswith("maven-metadata.xml"):
            version = "1.86"
            if failure == "mixed" and "/bcutil-jdk18on/" in url:
                version = "1.85"
            if failure == "preview":
                version = "1.87-SNAPSHOT"
            return (
                "<metadata><versioning><release>"
                + version
                + "</release></versioning></metadata>"
            ).encode()
        assert url.endswith(".jar.sha256")
        return b"invalid" if failure == "checksum" else b"a" * 64

    monkeypatch.setattr(module, "fetch", fetch)
    if failure:
        with pytest.raises(ValueError):
            module.resolve()
    else:
        result = module.resolve()
        assert len(result) == 3 and len(requested) == 6
        assert all(value["version"] == "1.86" for value in result.values())
