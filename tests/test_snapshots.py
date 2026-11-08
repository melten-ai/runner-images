import json
import shutil
import subprocess
from pathlib import Path

import pytest

from runner_images.snapshots import update


def observations(tmp_path):
    source = tmp_path / "observations"
    for label, operating_system, arch in (
        ("ubuntu-24.04", "linux", "x86_64"),
        ("macos-26", "darwin", "arm64"),
    ):
        file = source / f"reference-{label}" / "inventory.json"
        file.parent.mkdir(parents=True)
        file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "os": operating_system,
                    "arch": arch,
                    "image_os": label,
                    "image_version": "20261107.1",
                    "records": ["cmd bash", "cmd python3"],
                }
            )
        )
    return source


def test_initial_repeat_and_change(tmp_path):
    source = observations(tmp_path)
    target = tmp_path / "snapshots"
    assert update(source, target) == ["ubuntu-24.04.json", "macos-26.json"]
    previous = {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.iterdir()
    }
    assert update(source, target) == []
    assert previous == {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.iterdir()
    }
    file = source / "reference-macos-26/inventory.json"
    data = json.loads(file.read_text())
    data["records"].append("cmd rustc")
    file.write_text(json.dumps(data))
    assert update(source, target) == ["macos-26.json"]
    assert "cmd rustc" in json.loads((target / "macos-26.json").read_text())["records"]


@pytest.mark.parametrize("change", ["missing", "platform", "provenance"])
def test_incomplete_matrix_preserves_previous_files(tmp_path, change):
    source = observations(tmp_path)
    target = tmp_path / "snapshots"
    update(source, target)
    previous = {p.name: p.read_bytes() for p in target.iterdir()}
    file = source / "reference-macos-26/inventory.json"
    if change == "missing":
        file.unlink()
    else:
        data = json.loads(file.read_text())
        data["arch" if change == "platform" else "image_version"] = (
            "x86_64" if change == "platform" else ""
        )
        file.write_text(json.dumps(data))
    with pytest.raises((OSError, ValueError)):
        update(source, target)
    assert previous == {p.name: p.read_bytes() for p in target.iterdir()}


def test_git_publisher_commits_changes_only(tmp_path):
    root = Path(__file__).resolve().parents[1]
    remote = tmp_path / "remote.git"
    repo = tmp_path / "checkout"

    def git(*args, cwd=repo):
        return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()

    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)], check=True, capture_output=True
    )
    for directory in ("scripts", "runner_images"):
        shutil.copytree(
            root / directory,
            repo / directory,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    (repo / ".gitignore").write_text("__pycache__/\n")
    git("config", "user.name", "Example Maintainer")
    git("config", "user.email", "maintainer@example.com")
    git("add", ".")
    git("commit", "-m", "initial")
    git("remote", "add", "origin", str(remote))
    git("push", "-u", "origin", "main")
    source = observations(tmp_path)
    command = ["bash", "scripts/commit-snapshots.sh", str(source), "main"]
    subprocess.run(command, cwd=repo, check=True, capture_output=True)
    first = git("rev-parse", "HEAD")
    assert (
        git("log", "-1", "--format=%s")
        == "chore: update GitHub-hosted runner inventories"
    )
    assert git("ls-remote", "origin", "refs/heads/main").split()[0] == first
    again = subprocess.run(
        command, cwd=repo, check=True, capture_output=True, text=True
    )
    assert "unchanged" in again.stdout
    assert git("rev-parse", "HEAD") == first
    (repo / "unrelated.txt").write_text("preserve this work")
    blocked = subprocess.run(command, cwd=repo, capture_output=True)
    assert blocked.returncode != 0
    assert git("rev-parse", "HEAD") == first
