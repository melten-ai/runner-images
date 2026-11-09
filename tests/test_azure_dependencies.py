import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "refresh_azure_cli",
    Path(__file__).resolve().parents[1] / "macos/image/refresh-azure-cli.py",
)
assert spec and spec.loader
azure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(azure)


def report():
    return {
        "install": [
            {
                "metadata": {"name": "azure-cli", "version": "2.92.0"},
                "download_info": {
                    "url": "https://files.pythonhosted.org/packages/azure_cli.whl",
                    "archive_info": {"hashes": {"sha256": "a" * 64}},
                },
            }
        ]
    }


@pytest.mark.parametrize(
    "bad", ["", "host", "hash", "source", "name", "duplicate", "missing"]
)
def test_resolved_closure_requires_unique_official_hashed_wheels(bad):
    data = report()
    item = data["install"][0]
    if bad == "host":
        item["download_info"]["url"] = "https://example.com/injected.whl"
    elif bad == "hash":
        item["download_info"]["archive_info"]["hashes"]["sha256"] = "bad"
    elif bad == "source":
        item["download_info"]["url"] = "https://files.pythonhosted.org/package.tar.gz"
    elif bad == "name":
        item["metadata"]["name"] = "azure-cli\nextra-package"
    elif bad == "duplicate":
        data["install"].append(item)
    elif bad == "missing":
        data["install"] = []
    if bad:
        with pytest.raises(ValueError):
            azure.locked_requirements(data)
    else:
        assert azure.locked_requirements(data) == (
            "azure-cli==2.92.0 --hash=sha256:" + "a" * 64 + "\n"
        )


@pytest.mark.parametrize("failure", ["", "staged", "activated"])
def test_refresh_resolves_before_activation_and_restores_failed_live_tree(
    tmp_path, monkeypatch, failure
):
    cask = tmp_path / "azure-cli"
    site = cask / "2.92.0/libexec/lib/python3.14/site-packages"
    metadata = site / "azure_cli-2.92.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Name: azure-cli\nVersion: 2.92.0\n")
    (site / "original").write_text("original-vendor-bytes")
    receipt = tmp_path / "evidence/azure.json"
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["check"]
        if "--dry-run" in args:
            assert (site / "original").read_text() == "original-vendor-bytes"
            assert "--ignore-installed" in args and "azure-cli==2.92.0" in args
            Path(args[args.index("--report") + 1]).write_text(json.dumps(report()))
        else:
            assert "--require-hashes" in args and "--no-deps" in args
            assert "--only-binary=:all:" in args
            target = Path(args[args.index("--target") + 1])
            target.mkdir()
            (target / "updated").write_text("new-dependencies")

    probes = []

    def probe(python, candidate, version, config):
        probes.append(candidate)
        assert version == "2.92.0" and (candidate / "updated").exists()
        assert not (candidate / "original").exists()
        if (failure == "staged" and candidate != site) or (
            failure == "activated" and candidate == site
        ):
            raise subprocess.CalledProcessError(1, "native probe")

    monkeypatch.setattr(azure.subprocess, "run", run)
    monkeypatch.setattr(azure, "probe", probe)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            azure.refresh(cask, receipt)
        assert (site / "original").read_text() == "original-vendor-bytes"
        assert not receipt.exists() and not (site / "updated").exists()
    else:
        azure.refresh(cask, receipt)
        assert (site / "updated").exists() and not (site / "original").exists()
        assert json.loads(receipt.read_text()) == report()
        assert probes[-1] == site
    assert len(calls) == 2 and not list(site.parent.glob(".runner-image-azure-*"))


def test_failed_directory_switch_restores_original_tree(tmp_path, monkeypatch):
    site, replacement, backup = (tmp_path / name for name in ("site", "new", "old"))
    site.mkdir()
    (site / "original").touch()
    replacement.mkdir()
    replace = azure.os.replace

    def fail(source, destination):
        if source == replacement:
            raise OSError("injected filesystem failure")
        return replace(source, destination)

    monkeypatch.setattr(azure.os, "replace", fail)
    with pytest.raises(OSError):
        azure.activate(site, replacement, backup, lambda: None)
    assert (site / "original").exists() and not backup.exists()
