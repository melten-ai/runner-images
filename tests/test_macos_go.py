"""Supported-Go policy and cache retirement under the actual guest shell."""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def policy(tmp_path):
    toolset = tmp_path / "toolset.json"
    toolset.write_text(
        json.dumps(
            {
                "toolcache": [
                    {
                        "name": "Go",
                        "arch": {"arm64": {"versions": ["1.24.*", "1.25.*"]}},
                    },
                    {"name": "Python", "arch": {"arm64": {"versions": ["3.12.*"]}}},
                ]
            }
        )
    )
    releases = tmp_path / "releases.json"
    releases.write_text(
        json.dumps(
            [
                {"version": "go1.27.3", "stable": True},
                {"version": "go1.26.10", "stable": True},
                {"version": "go1.28rc1", "stable": False},
            ]
        )
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    source = (
        (ROOT / "macos/image/toolchain.sh")
        .read_text()
        .rsplit('\nmain "$@" </dev/null', 1)[0]
    )

    def run(command):
        return subprocess.run(
            ["/bin/bash", "-s"],
            input=source + "\n" + command + "\necho completed\n",
            text=True,
            capture_output=True,
            timeout=10,
            env={**os.environ, "RUNNER_TOOL_CACHE": str(cache)},
        )

    def install(version, arch="arm64", reported=None):
        parent = cache / "go" / version
        binary = parent / arch / "bin" / "go"
        binary.parent.mkdir(parents=True)
        binary.write_text(
            "#!/bin/sh\necho "
            + shlex.quote(reported or f"go version go{version} darwin/{arch}")
            + "\n"
        )
        binary.chmod(0o755)
        (parent / f"{arch}.complete").touch()
        return parent

    return toolset, releases, cache, run, install


def configure(toolset, releases):
    return (
        "configure_supported_go "
        + shlex.quote(str(toolset))
        + " "
        + shlex.quote(str(releases))
    )


def test_policy_replaces_hosted_old_versions_but_preserves_other_families(policy):
    toolset, releases, _, run, _ = policy
    result = run(configure(toolset, releases))
    assert result.returncode == 0, result.stderr
    value = json.loads(toolset.read_text())
    assert value["toolcache"][0]["arch"]["arm64"]["versions"] == ["1.26.10", "1.27.3"]
    assert value["runner_image_go_policy"]["versions"] == ["1.26.10", "1.27.3"]
    assert value["toolcache"][1]["arch"]["arm64"]["versions"] == ["3.12.*"]


@pytest.mark.parametrize(
    "invalid", [[], [{"version": "go1.28rc1", "stable": False}], {"bad": "data"}]
)
def test_missing_release_policy_stops_without_replacing_toolset(policy, invalid):
    toolset, releases, _, run, _ = policy
    before = toolset.read_bytes()
    releases.write_text(json.dumps(invalid))
    result = run(configure(toolset, releases))
    assert result.returncode != 0
    assert "completed" not in result.stdout
    assert toolset.read_bytes() == before


def test_retirement_keeps_current_versions_other_architectures_and_symlinks(
    policy, tmp_path
):
    toolset, releases, cache, run, install = policy
    current = [install(v) for v in ("1.26.10", "1.27.3")]
    old = install("1.24.13")
    intel = install("1.24.13", "x64")
    obsolete = install("1.25.14")
    unusual = install("unexpected")
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep").touch()
    (cache / "go" / "1.23.1").symlink_to(external, target_is_directory=True)
    result = run(
        configure(toolset, releases)
        + "\nretire_unsupported_go "
        + shlex.quote(str(toolset))
    )
    assert result.returncode == 0, result.stderr
    assert all((p / "arm64.complete").exists() for p in current)
    assert not (old / "arm64").exists() and not (old / "arm64.complete").exists()
    assert (intel / "x64.complete").exists()
    assert not obsolete.exists()
    assert (unusual / "arm64.complete").exists()
    assert (external / "keep").exists()
    assert (cache / "go" / "1.23.1").is_symlink()


@pytest.mark.parametrize("failure", ["missing", "version", "arch", "marker", "link"])
def test_replacements_must_execute_before_any_retirement(policy, tmp_path, failure):
    toolset, releases, cache, run, install = policy
    old = install("1.24.13")
    install("1.26.10")
    if failure != "missing":
        reported = {
            "version": "go version go1.27.2 darwin/arm64",
            "arch": "go version go1.27.3 darwin/amd64",
        }.get(failure)
        target = install("1.27.3", reported=reported)
        if failure == "marker":
            (target / "arm64.complete").unlink()
        if failure == "link":
            (cache / "go" / "1.27.3").rename(tmp_path / "outside")
            (cache / "go" / "1.27.3").symlink_to(
                tmp_path / "outside", target_is_directory=True
            )
    result = run(
        configure(toolset, releases)
        + "\nretire_unsupported_go "
        + shlex.quote(str(toolset))
    )
    assert result.returncode != 0
    assert "completed" not in result.stdout
    assert (old / "arm64.complete").exists()
