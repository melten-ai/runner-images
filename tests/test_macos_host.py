import hashlib
import json
import subprocess

import pytest

from runner_images import macos_host


@pytest.fixture
def staging(monkeypatch):
    payload = b"synthetic official Azure archive"
    checksum = hashlib.sha256(payload).hexdigest()
    url = (
        "https://github.com/Azure/azure-cli/releases/download/"
        "azure-cli-2.92.0/azure-cli-2.92.0-macos-arm64.tar.gz"
    )
    api_url = "https://api.github.com/repos/Azure/azure-cli/releases/assets/123"
    cache = "/Users/admin/Library/Caches/Homebrew/downloads/example--azure-cli.tar.gz"
    state = {
        "cask": {
            "token": "azure-cli",
            "version": "2.92.0",
            "url": url,
            "sha256": checksum,
        },
        "asset": {
            "browser_download_url": url,
            "url": api_url,
            "digest": "sha256:" + checksum,
        },
        "cache": cache,
        "calls": [],
        "payload": payload,
        "transferred": None,
        "remote_hash": checksum,
        "fail": "",
    }

    def output(args, **kwargs):
        state["calls"].append(args)
        assert args[:3] == ["tart", "exec", "example-build-1-2"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        if "info" in args:
            return json.dumps({"casks": [state["cask"]]})
        if "--cache" in args:
            return state["cache"] + "\n"
        if "/usr/bin/shasum" in args:
            return state["remote_hash"] + "  " + cache
        return ""

    def gh(method, endpoint):
        assert method == "GET"
        assert endpoint == "repos/Azure/azure-cli/releases/tags/azure-cli-2.92.0"
        return {"assets": [state["asset"]]}

    def run(args, **kwargs):
        state["calls"].append(args)
        assert kwargs["check"] is True
        if args[0] == "gh":
            assert args == [
                "gh",
                "api",
                api_url,
                "-H",
                "Accept: application/octet-stream",
            ]
            kwargs["stdout"].write(state["payload"])
        else:
            assert args[:4] == ["tart", "exec", "-i", "example-build-1-2"]
            if state["fail"] == "transfer":
                raise subprocess.CalledProcessError(1, args)
            state["transferred"] = kwargs["stdin"].read()

    monkeypatch.setattr(macos_host.subprocess, "check_output", output)
    monkeypatch.setattr(macos_host.subprocess, "run", run)
    monkeypatch.setattr(macos_host, "gh", gh)
    return state


def test_verified_cask_reaches_guest_without_forwarding_credentials(staging):
    macos_host.prepare_azure_cli("example-build-1-2")
    assert staging["transferred"] == staging["payload"]
    assert not any("TOKEN" in str(args) or "-e" in args for args in staging["calls"])


@pytest.mark.parametrize(
    "failure",
    ["digest", "bytes", "cache", "url", "api", "version", "remote", "transfer"],
)
def test_cask_staging_fails_closed_at_the_affected_boundary(staging, failure):
    if failure == "digest":
        staging["asset"]["digest"] = "sha256:" + "a" * 64
    elif failure == "bytes":
        staging["payload"] = b"corrupted transfer"
    elif failure == "cache":
        staging["cache"] = "/Users/admin/Library/Caches/Homebrew/../../outside"
    elif failure == "url":
        staging["cask"]["url"] = "https://example.com/untrusted.tar.gz"
    elif failure == "api":
        staging["asset"]["url"] = (
            "https://api.github.com/repos/example/other/releases/assets/123"
        )
    elif failure == "version":
        staging["cask"]["version"] = "2.93.0-beta"
    elif failure == "remote":
        staging["remote_hash"] = "b" * 64
    else:
        staging["fail"] = "transfer"
    with pytest.raises(
        subprocess.CalledProcessError if failure == "transfer" else ValueError
    ):
        macos_host.prepare_azure_cli("example-build-1-2")
    if failure not in ["remote", "transfer"]:
        assert staging["transferred"] is None


def test_cask_preparation_refuses_non_build_vm_before_mutation(staging):
    with pytest.raises(ValueError, match="temporary build VM"):
        macos_host.prepare_azure_cli("existing-runner")
    assert staging["calls"] == []
