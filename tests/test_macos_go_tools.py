import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("tool", ["guest-agent", "git-lfs"])
@pytest.mark.parametrize(
    "failure", ["", "checksum", "version", "signature", "linked", "path"]
)
def test_agent_verification_rejects_changed_bytes_identity_and_linked_paths(
    tmp_path, failure, tool
):
    root = Path(__file__).resolve().parents[1]
    cellar = tmp_path / "cellar"
    program = "tart-guest-agent" if tool == "guest-agent" else "git-lfs"
    version = "0.15.0+melten.example" if tool == "guest-agent" else "3.8.1"
    description = (
        "tart-guest-agent version 0.15.0+melten.example-patched"
        if tool == "guest-agent"
        else "git-lfs/3.8.1 (Melten; darwin arm64; go 1.27.3; git patched.example)"
    )
    binary = cellar / program / version.split("+")[0] / "bin" / program
    binary.parent.mkdir(parents=True)
    binary.write_text(
        "#!/bin/sh\necho '" + ("wrong" if failure == "version" else description) + "'\n"
    )
    binary.chmod(0o755)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if failure == "linked":
        original = binary.with_name("original")
        binary.rename(original)
        binary.symlink_to(original)
    evidence = tmp_path / ".runner-image"
    evidence.mkdir()
    (evidence / f"{tool}-build.json").write_text(
        json.dumps(
            {
                "binary": str(binary)
                if failure != "path"
                else str(tmp_path / "outside"),
                "binary_sha256": "0" * 64 if failure == "checksum" else digest,
                "version": version,
                "description": description,
            }
        )
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "codesign").write_text(
        "#!/bin/sh\nexit " + ("27" if failure == "signature" else "0") + "\n"
    )
    (tools / "codesign").chmod(0o755)
    # Map the external Homebrew root and codesign boundary to portable fixtures;
    # execute the original verification function, including byte/version checks.
    source = (root / f"macos/image/refresh-{tool}.sh").read_text()
    source = source.split("refresh_" + tool.replace("-", "_") + "() {", 1)[0]
    source = source.replace("/opt/homebrew/Cellar", str(cellar)).replace(
        "/usr/bin/codesign", "codesign"
    )
    result = subprocess.run(
        ["/bin/bash", "-s"],
        input=source + "\nverify_" + tool.replace("-", "_") + "\n",
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": str(tools) + ":" + os.environ["PATH"],
        },
        timeout=10,
    )
    assert (result.returncode == 0) == (not failure), result.stdout + result.stderr
    assert ("_sha256=" in result.stdout) == (not failure)


@pytest.mark.parametrize("scenario", ["transient", "exhausted", "rejected-version"])
def test_git_lfs_source_download_retries_transient_transfer_failures_only(
    tmp_path, scenario
):
    root = Path(__file__).resolve().parents[1]
    cellar = tmp_path / "cellar"
    binary = cellar / "git-lfs" / "3.8.1" / "bin" / "git-lfs"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho previous\n")
    binary.chmod(0o755)
    evidence = tmp_path / ".runner-image"
    evidence.mkdir()
    toolset = {"runner_image_go_policy": {"versions": ["1.27.3"]}}
    (evidence / "github-macos-toolset.json").write_text(json.dumps(toolset))
    upstream = tmp_path / "upstream-module"
    upstream.mkdir()
    (upstream / "go.mod").write_text("module github.com/git-lfs/git-lfs/v3\n")
    (upstream / "go.sum").write_text("")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    for helper in ("jq", "python3"):
        real = shutil.which(helper)
        assert real is not None
        (tools / helper).symlink_to(real)
    (tools / "brew").write_text('#!/bin/sh\necho "$FAKE_CELLAR/git-lfs/3.8.1"\n')
    (tools / "sleep").write_text('#!/bin/sh\necho "$1" >>"$FAKE_STATE/sleeps"\n')
    (tools / "go").write_text(
        """\
#!/bin/bash
set -eu
if [ "$1" = version ]; then
  echo 'go version go1.27.3 darwin/arm64'
elif [ "$1" = mod ] && [ "$2" = download ]; then
  attempts=$(($(cat "$FAKE_STATE/attempts" 2>/dev/null || echo 0) + 1))
  echo "$attempts" >"$FAKE_STATE/attempts"
  if [ "$attempts" -le "$FAKE_DOWNLOAD_FAILURES" ]; then
    echo '{"Path":"github.com/git-lfs/git-lfs/v3",' \\
      '"Error":"dial tcp 142.251.42.17:443: i/o timeout"}'
    exit 1
  fi
  jq -cn --arg version "$FAKE_SOURCE_VERSION" --arg dir "$FAKE_SOURCE_DIR" \\
    '{Path:"github.com/git-lfs/git-lfs/v3",Version:$version,Dir:$dir,
      Sum:"h1:example=",GoModSum:"h1:examplemod="}'
elif [ "$1" = get ]; then
  echo "reached-go-get in $PWD"
  cat go.mod
  exit 7
else
  echo "unexpected go invocation: $*" >&2
  exit 64
fi
"""
    )
    for tool in ("brew", "sleep", "go"):
        (tools / tool).chmod(0o755)
    # Map the Homebrew root, the guest toolchain PATH and the guest scratch
    # directory to portable fixtures; execute the original refresh flow with
    # the go/sleep network boundaries faked.
    source = (root / "macos/image/refresh-git-lfs.sh").read_text()
    source = source.replace("/opt/homebrew/Cellar", str(cellar))
    source = source.replace(
        '"$HOME/hostedtoolcache/go/$go_version/arm64/bin'
        ':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"',
        f'"{tools}:/usr/bin:/bin:/usr/sbin:/sbin"',
    )
    source = source.replace(
        "/private/tmp/runner-image-git-lfs", f"{scratch}/runner-image-git-lfs"
    )
    failures = {"transient": "2", "exhausted": "7", "rejected-version": "0"}
    result = subprocess.run(
        ["/bin/bash", "-s", "--", "refresh"],
        input=source,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PATH": str(tools) + ":" + os.environ["PATH"],
            "FAKE_STATE": str(state),
            "FAKE_CELLAR": str(cellar),
            "FAKE_SOURCE_DIR": str(upstream),
            "FAKE_SOURCE_VERSION": "v3.8.1-rc1"
            if scenario == "rejected-version"
            else "v3.8.1",
            "FAKE_DOWNLOAD_FAILURES": failures[scenario],
        },
        timeout=30,
    )
    attempts = int((state / "attempts").read_text())
    sleeps = (
        (state / "sleeps").read_text().split() if (state / "sleeps").exists() else []
    )
    advanced = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("reached-go-get in ")
    ]
    if scenario == "transient":
        # Two transient proxy failures, then success: the refresh must advance
        # into the copied source tree from the final download's Dir.
        assert result.returncode == 7, result.stdout + result.stderr
        assert len(advanced) == 1
        prefix = f"reached-go-get in {scratch}/runner-image-git-lfs."
        assert advanced[0].startswith(prefix) and advanced[0].endswith("/source")
        assert "module github.com/git-lfs/git-lfs/v3" in result.stdout
        assert attempts == 3
        assert sleeps == ["2", "2"]
    elif scenario == "exhausted":
        # Bounded exhaustion: nonzero failure, last error JSON on stderr, and
        # no advancement past the download.
        assert result.returncode == 1, result.stdout + result.stderr
        assert "i/o timeout" in result.stderr
        assert advanced == []
        assert attempts == 6
        assert sleeps == ["2"] * 5
    else:
        # A completed transfer whose version fails verification is never
        # retried as a download.
        assert result.returncode != 0
        assert advanced == []
        assert attempts == 1
        assert sleeps == []
    assert not (evidence / "git-lfs-build.json").exists()
    toolset_path = evidence / "github-macos-toolset.json"
    assert json.loads(toolset_path.read_text()) == toolset
    assert binary.read_text() == "#!/bin/sh\necho previous\n"
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("tool", ["guest-agent", "git-lfs"])
@pytest.mark.parametrize("status", [0, 27])
def test_module_cache_cleanup_handles_read_only_directories_and_keeps_exit_status(
    tmp_path, status, tool
):
    root = Path(__file__).resolve().parents[1]
    source = (root / f"macos/image/refresh-{tool}.sh").read_text()
    trap = next(
        line.strip() for line in source.splitlines() if line.strip().startswith("trap ")
    )
    work = tmp_path / "owned-work"
    module = work / "modules/example.org/module@v1.0.0"
    module.mkdir(parents=True)
    (module / "go.mod").write_text("module example.org/module\n")
    (module / "go.mod").chmod(0o444)
    module.chmod(0o555)
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            "set -eu; " + tool.replace("-", "_") + "_work=$1; " + trap + '; exit "$2"',
            "--",
            str(work),
            str(status),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == status, result.stdout + result.stderr
    assert not work.exists()
