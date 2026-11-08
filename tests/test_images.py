import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from runner_images.compare import compare, load
from runner_images.inventory import collect


def executable(path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


def snapshot(tmp_path, records=None, **overrides):
    value = {
        "schema_version": 1,
        "os": "darwin",
        "arch": "arm64",
        "image_os": "macos26",
        "image_version": "reference-build",
        "records": ["cmd bash"] if records is None else records,
        **overrides,
    }
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(value))
    return load(path)


def test_collect_real_files(tmp_path, monkeypatch):
    tools = tmp_path / "bin"
    tools.mkdir()
    executable(tools / "compiler")
    (tools / "not-executable").write_text("data")
    (tools / "directory").mkdir()
    (tools / "alias").symlink_to(tools / "compiler")
    (tools / "broken").symlink_to(tools / "missing")
    cache = tmp_path / "cache"
    (cache / "Python/3.14").mkdir(parents=True)
    (cache / "Python/3.14/arm64.complete").touch()
    (cache / "Ruby/3.4/arm64.complete").mkdir(parents=True)
    (cache / "Node/24").mkdir(parents=True)  # Incomplete cache is not usable.
    monkeypatch.setattr("platform.machine", lambda: "aarch64")
    result = collect(
        {
            "PATH": os.pathsep.join(
                [str(tools), str(tools), str(tmp_path / "absent"), ""]
            ),
            "RUNNER_TOOL_CACHE": str(cache),
            "JAVA_HOME": "secret-path-that-must-not-appear",
            "GITHUB_TOKEN": "secret-token-that-must-not-appear",
            "INVENTORY_OUTPUT": "job-only-output-path",
            "HOME": "private-home",
            "__CF_USER_TEXT_ENCODING": "session-metadata",
            "ImageOS": "macos26",
            "ImageVersion": "build-id",
        }
    )
    assert result["records"] == [
        "cache Python",
        "cmd alias",
        "cmd compiler",
        "env ImageOS",
        "env ImageVersion",
        "env JAVA_HOME",
    ]
    assert result["arch"] == "arm64"
    assert result["image_version"] == "build-id"
    assert "secret" not in json.dumps(result)
    assert result == collect(
        {
            "PATH": str(tools),
            "RUNNER_TOOL_CACHE": str(cache),
            "JAVA_HOME": "changed-value",
            "ImageOS": "macos26",
            "ImageVersion": "build-id",
        }
    )


def test_default_environment(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("RUNNER_TOOL_CACHE", "/nonexistent-example-cache")
    assert collect()["schema_version"] == 1


def test_unreadable_collection_fails(tmp_path, monkeypatch):
    def denied(_):
        raise PermissionError("do not leak private path")

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(PermissionError):
        collect({"PATH": str(tmp_path)})


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"os": None},
        {"os": "windows"},
        {"arch": "mips"},
        {"records": []},
        {"records": "cmd bash"},
        {"records": ["env JAVA_HOME"]},
        {"records": ["cmd bash", "cmd bash"]},
        {"records": ["cmd z", "cmd a"]},
        {"records": [42]},
        {"records": ["cmd tool\ninjected"]},
        {"records": ["unknown x"]},
    ],
)
def test_reject_invalid_snapshot(tmp_path, overrides):
    with pytest.raises(ValueError):
        snapshot(tmp_path, **overrides)


def test_reject_nonobject(tmp_path):
    file = tmp_path / "snapshot.json"
    for content in ("[]", "null", "{broken"):
        file.write_text(content)
        with pytest.raises(ValueError):
            load(file)


def test_exact_and_extra(tmp_path):
    reference = snapshot(tmp_path)
    candidate = snapshot(
        tmp_path, ["cmd bash", "cmd compiler"], image_version="candidate-build"
    )
    result = compare(reference, candidate)
    assert result == {
        "compatible": True,
        "missing": [],
        "unexpected_missing": [],
        "stale_allowlist": [],
        "extra": ["cmd compiler"],
    }


def test_missing_and_reviewed_exceptions(tmp_path):
    reference = snapshot(
        tmp_path, ["cache Python", "cmd bash", "cmd rustc", "env JAVA_HOME"]
    )
    candidate = snapshot(tmp_path)
    result = compare(reference, candidate)
    assert result["compatible"] is False
    assert result["unexpected_missing"] == [
        "cache Python",
        "cmd rustc",
        "env JAVA_HOME",
    ]
    allow = tmp_path / "allow.txt"
    allow.write_text("cache Python\ncmd rustc\nenv JAVA_HOME\n")
    assert compare(reference, candidate, allow)["compatible"] is True
    candidate = snapshot(tmp_path, ["cmd bash", "cmd rustc"])
    assert compare(reference, candidate, allow)["stale_allowlist"] == ["cmd rustc"]
    allow.write_text("not-a-record\n")
    with pytest.raises(ValueError):
        compare(reference, candidate, allow)


def test_platform_mismatch(tmp_path):
    reference = snapshot(tmp_path)
    for options in ({"os": "linux"}, {"arch": "x86_64"}):
        with pytest.raises(ValueError):
            compare(reference, snapshot(tmp_path, **options))


def test_scripts_real_contract(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PRIVATE_EXAMPLE_TOKEN": "sensitive-value-example"}
    captured = subprocess.run(
        [sys.executable, str(root / "scripts/inventory.py")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "sensitive-value-example" not in captured.stdout
    file = tmp_path / "actual.json"
    file.write_text(captured.stdout)
    args = [sys.executable, str(root / "scripts/compare.py"), str(file), str(file)]
    success = subprocess.run(args, capture_output=True, text=True)
    assert success.returncode == 0
    assert json.loads(success.stdout)["compatible"] is True
    candidate = json.loads(captured.stdout)
    candidate["records"].append("cmd synthetic-missing-compiler")
    candidate["records"].sort()
    other = tmp_path / "other.json"
    other.write_text(json.dumps(candidate))
    failed = subprocess.run(
        [*args[:2], str(other), str(file)], capture_output=True, text=True
    )
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["unexpected_missing"] == [
        "cmd synthetic-missing-compiler"
    ]
    file.write_text('{"private": "sensitive-value-example"}')
    invalid = subprocess.run(args, capture_output=True, text=True)
    assert invalid.returncode == 2
    assert invalid.stdout == ""
    assert "sensitive-value-example" not in invalid.stderr
    misuse = subprocess.run([*args, "--unknown"], capture_output=True)
    assert misuse.returncode == 2


def test_record_values_rejected(tmp_path):
    with pytest.raises(ValueError):
        snapshot(tmp_path, ["cmd bash", "env JAVA_HOME=/private/path value"])
