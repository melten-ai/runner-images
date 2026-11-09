"""refresh_python_cache under stock Bash 3.2 with Homebrew mocked at the boundary."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "macos/image/refresh-python-cache.sh"
LINK_NAMES = ("python", "python3", "python3.12", "pip", "pip3", "pip3.12")


class Guest:
    """Synthetic guest: tool cache, mock brew and a mock python@MINOR keg."""

    def __init__(self, tmp_path: Path) -> None:
        self.cache = tmp_path / "hostedtoolcache"
        self.cache.mkdir()
        self.tools = tmp_path / "bin"
        self.tools.mkdir()
        self.opt = tmp_path / "homebrew/opt"
        self.opt.mkdir(parents=True)
        self.template = tmp_path / "formula-template"
        self.template.mkdir()
        self.log = tmp_path / "boundary.log"
        self.frameworks = tmp_path / "frameworks"
        self.script = tmp_path / "refresh-python-cache.sh"
        self.script.write_text(
            SCRIPT.read_text().replace(
                "/Library/Frameworks/Python.framework/Versions", str(self.frameworks)
            )
        )
        sudo = self.tools / "sudo"
        sudo.write_text("""#!/bin/sh
if [ "$1" = ln ] && [ -n "${TEST_LINK_FAIL:-}" ]; then exit 1; fi
exec "$@"
""")
        sudo.chmod(0o755)
        brew = self.tools / "brew"
        brew.write_text(
            f"""#!/bin/sh
stdin=pipe; [ -c /dev/stdin ] && stdin=devnull
printf 'brew %s stdin=%s\\n' "$*" "$stdin" >>"{self.log}"
for formula; do :; done
case "$1" in
--prefix) echo "{self.opt}/$2" ;;
list) test -d "{self.opt}/$formula" ;;
install|upgrade)
	[ -z "${{TEST_BREW_FAIL:-}}" ] || {{ echo 'brew: synthetic failure' >&2; exit 1; }}
	rm -rf "{self.opt}/$formula"
	cp -R "{self.template}/$formula" "{self.opt}/$formula"
	;;
*) echo "unexpected brew $*" >&2; exit 2 ;;
esac
"""
        )
        brew.chmod(0o755)
        self.env = {
            "PATH": f"{self.tools}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(tmp_path / "home"),
            "RUNNER_TOOL_CACHE": str(self.cache),
        }

    def keg(
        self,
        minor: str = "3.12",
        reports: str = "3.12.11",
        level: str = "final",
        pip_minor: str | None = None,
        interpreter: bool = True,
        installed: bool = False,
    ) -> Path:
        """Define what brew install/upgrade of python@MINOR will provide."""
        keg = self.template / f"python@{minor}"
        if keg.exists():
            subprocess.run(["rm", "-rf", str(keg)], check=True)
        (keg / "bin").mkdir(parents=True)
        major, mid, micro = reports.split(".")
        python = keg / f"bin/python{minor}"
        if interpreter:
            python.write_text(
                f"""#!/bin/sh
stdin=pipe; [ -c /dev/stdin ] && stdin=devnull
printf 'python %s stdin=%s\\n' "$0" "$stdin" >>"{self.log}"
while [ $# -gt 0 ] && [ "$1" != -c ]; do shift; done
[ "$1" = -c ] || exit 2
exec {sys.executable} -c "import collections, sys
fields = 'major minor micro releaselevel serial'
sys.version_info = collections.namedtuple('version_info', fields)(
    {major}, {mid}, {micro}, '{level}', 0)
$2"
"""
            )
            python.chmod(0o755)
        pip = keg / f"bin/pip{minor}"
        pip.write_text(
            f"""#!/bin/sh
stdin=pipe; [ -c /dev/stdin ] && stdin=devnull
printf 'pip %s stdin=%s\\n' "$0" "$stdin" >>"{self.log}"
[ "$1" = --version ] || exit 2
echo "pip 25.3 from {keg}/lib/pip (python {pip_minor or minor})"
"""
        )
        pip.chmod(0o755)
        if installed:
            subprocess.run(["cp", "-R", str(keg), str(self.opt / keg.name)], check=True)
        return self.opt / keg.name

    def cached(self, version: str, arch: str = "arm64", complete: bool = True) -> Path:
        entry = self.cache / "Python" / version / arch
        (entry / "bin").mkdir(parents=True)
        (entry / "bin/python").write_text(f"stale {version} {arch}")
        if complete:
            (entry.parent / f"{arch}.complete").write_text("")
        return entry

    def run(
        self, minor: str = "3.12", streamed: bool = False, **env: str
    ) -> subprocess.CompletedProcess[bytes]:
        environment = {**self.env, **env}
        if streamed:
            # Production streams the script into `bash -s`, so stdin IS the
            # script: any subprocess that reads stdin would swallow it.
            source = b"set -euo pipefail\n" + self.script.read_bytes()
            source += f"\nrefresh_python_cache '{minor}'\n".encode()
            command = ["/bin/bash", "-s"]
        else:
            source = b""
            command = [
                "/bin/bash",
                "-c",
                'set -euo pipefail; source "$1"; refresh_python_cache "$2"',
                "_",
                str(self.script),
                minor,
            ]
        return subprocess.run(
            command, input=source, env=environment, capture_output=True, timeout=60
        )

    def boundary(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


def snapshot(root: Path) -> dict[str, str]:
    """Every entry under root with its kind and content or link target."""
    entries: dict[str, str] = {}
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(directory) / name
            key = str(path.relative_to(root))
            if path.is_symlink():
                entries[key] = f"link {os.readlink(path)}"
            elif path.is_dir():
                entries[key] = "dir"
            else:
                entries[key] = f"file {path.read_text()}"
    return entries


@pytest.fixture
def guest(tmp_path: Path) -> Guest:
    return Guest(tmp_path)


def test_sourcing_defines_the_function_without_acting(guest: Guest):
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'set -euo pipefail; source "$1"; declare -F refresh_python_cache',
            "_",
            str(SCRIPT),
        ],
        env=guest.env,
        input=b"",
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"refresh_python_cache\n"
    assert guest.boundary() == []
    assert snapshot(guest.cache) == {}


def test_streamed_refresh_installs_homebrew_python_and_publishes_real_version(
    guest: Guest,
):
    keg = guest.keg(reports="3.12.11")
    result = guest.run(streamed=True)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"", "function output would pollute the job PATH"
    version = guest.cache / "Python/3.12.11"
    assert (version / "arm64.complete").is_file()
    assert not (version / "arm64.partial").exists()
    for name in LINK_NAMES:
        link = version / "arm64/bin" / name
        assert link.is_symlink(), name
        target = keg / (
            "bin/python3.12" if name.startswith("python") else "bin/pip3.12"
        )
        assert Path(os.readlink(link)) == target
        assert os.access(link, os.X_OK)
    calls = guest.boundary()
    assert (
        subprocess.run(
            [str(version / "arm64/bin/python"), "-c", "print(1)"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
        ).stdout
        == b"1\n"
    )
    assert calls == [
        "brew list --versions python@3.12 stdin=devnull",
        "brew install --quiet python@3.12 stdin=devnull",
        "brew --prefix python@3.12 stdin=devnull",
        f"python {keg}/bin/python3.12 stdin=devnull",
        f"python {version}/arm64.partial/bin/python stdin=devnull",
        f"pip {version}/arm64.partial/bin/pip stdin=devnull",
    ], "install exactly once, verify the keg, then verify through the staged links"


def test_rerun_upgrades_the_formula_and_retires_the_patch_it_replaces(guest: Guest):
    guest.keg(reports="3.12.11")
    assert guest.run().returncode == 0
    guest.keg(reports="3.12.12")
    result = guest.run()
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b""
    assert "brew upgrade --quiet python@3.12 stdin=devnull" in guest.boundary()
    assert "brew install --quiet python@3.12 stdin=devnull" not in guest.boundary()[6:]
    assert (guest.cache / "Python/3.12.12/arm64.complete").is_file()
    assert (guest.cache / "Python/3.12.12/arm64/bin/python3.12").is_symlink()
    assert not (guest.cache / "Python/3.12.11").exists()


def test_superseded_patches_retire_while_everything_else_is_preserved(guest: Guest):
    guest.keg(reports="3.12.11", installed=True)
    guest.cached("3.12.10")
    older = guest.cached("3.12.9")
    (older.parent / "notes.txt").write_text("operator note")
    guest.cached("3.12.9", arch="x64")
    guest.cached("3.12.8", complete=False)
    newer = guest.cached("3.12.99")
    other_minor = guest.cached("3.11.9")
    prerelease = guest.cached("3.12.10-beta.1")
    (guest.cache / "node/20.0.0").mkdir(parents=True)
    (guest.cache / "node/20.0.0/arm64.complete").write_text("")
    (guest.cache / "Python/README").write_text("unrelated file")
    before = snapshot(guest.cache)

    result = guest.run()
    assert result.returncode == 0, result.stderr.decode()
    after = snapshot(guest.cache)
    assert not (guest.cache / "Python/3.12.10").exists()
    assert not (guest.cache / "Python/3.12.8").exists()
    assert not (older).exists()
    assert not (older.parent / "arm64.complete").exists()
    assert (older.parent / "notes.txt").read_text() == "operator note"
    assert (older.parent / "x64/bin/python").read_text() == "stale 3.12.9 x64"
    assert (older.parent / "x64.complete").is_file()
    removed = {key for key in before if key not in after}
    assert removed == {
        key
        for key in before
        if key.startswith(("Python/3.12.10/", "Python/3.12.8/"))
        or key in {"Python/3.12.10", "Python/3.12.8"}
        or key.startswith("Python/3.12.9/arm64")
    }
    for entry in (newer, other_minor, prerelease):
        kept = {
            k: v
            for k, v in before.items()
            if k.startswith(str(entry.parent.relative_to(guest.cache)))
        }
        assert kept and all(after[key] == value for key, value in kept.items())
    assert after["node/20.0.0/arm64.complete"] == "file "
    assert after["Python/README"] == "file unrelated file"
    assert after["Python/3.12.11/arm64.complete"] == "file "


@pytest.mark.parametrize(
    "failure",
    ["brew", "missing-interpreter", "minor-mismatch", "prerelease", "pip-mismatch"],
)
def test_failed_refresh_preserves_existing_caches_and_writes_no_marker(
    guest: Guest, failure: str
):
    env: dict[str, str] = {}
    if failure == "brew":
        guest.keg(reports="3.12.11")
        env["TEST_BREW_FAIL"] = "1"
    elif failure == "missing-interpreter":
        guest.keg(reports="3.12.11", interpreter=False)
    elif failure == "minor-mismatch":
        guest.keg(reports="3.13.1")
    elif failure == "prerelease":
        guest.keg(reports="3.12.11", level="candidate")
    else:
        guest.keg(reports="3.12.11", pip_minor="3.11")
    guest.cached("3.12.10")
    guest.cached("3.11.9")
    before = snapshot(guest.cache)

    result = guest.run(**env)
    assert result.returncode != 0
    assert result.stdout == b""
    assert b"refresh_python_cache" in result.stderr or failure == "brew"
    assert snapshot(guest.cache) == before, "failure must not touch the cache"
    if failure == "brew":
        assert "brew --prefix python@3.12 stdin=devnull" not in guest.boundary()


@pytest.mark.parametrize(
    "minor", ["3", "3.12.1", "2.7", "3.x", "", "3.12 ", "3.12/../x"]
)
def test_invalid_minor_is_rejected_before_any_external_call(guest: Guest, minor: str):
    guest.keg(reports="3.12.11")
    guest.cached("3.12.10")
    before = snapshot(guest.cache)
    result = guest.run(minor=minor)
    assert result.returncode != 0
    assert result.stdout == b""
    assert guest.boundary() == []
    assert snapshot(guest.cache) == before


@pytest.mark.parametrize("problem", ["unset", "missing"])
def test_tool_cache_must_exist_before_any_external_call(guest: Guest, problem: str):
    guest.keg(reports="3.12.11")
    env = dict(guest.env)
    if problem == "unset":
        del env["RUNNER_TOOL_CACHE"]
    else:
        env["RUNNER_TOOL_CACHE"] = str(guest.cache / "absent")
    guest.env = env
    result = guest.run()
    assert result.returncode != 0
    assert guest.boundary() == []
    assert not (guest.cache / "absent").exists()


@pytest.mark.parametrize(
    "location", ["root", "family", "version", "arm64", "old-version", "old-arm64"]
)
def test_symlinked_cache_paths_never_redirect_writes_or_removal(
    guest: Guest, location: str, tmp_path: Path
):
    guest.keg(reports="3.12.11")
    outside = tmp_path / "outside"
    (outside / "arm64/bin").mkdir(parents=True)
    (outside / "arm64/bin/python").write_text("not ours")
    (outside / "arm64.complete").write_text("")
    (outside / "3.12.10/arm64/bin").mkdir(parents=True)
    (outside / "3.12.10/arm64/bin/python").write_text("not ours either")
    family = guest.cache / "Python"
    if location == "root":
        guest.env["RUNNER_TOOL_CACHE"] = str(tmp_path / "cache-link")
        (tmp_path / "cache-link").symlink_to(outside)
    elif location == "family":
        family.symlink_to(outside)
    elif location == "version":
        family.mkdir()
        (family / "3.12.11").symlink_to(outside)
    elif location == "arm64":
        (family / "3.12.11").mkdir(parents=True)
        (family / "3.12.11/arm64").symlink_to(outside / "arm64")
    elif location == "old-version":
        family.mkdir()
        (family / "3.12.10").symlink_to(outside / "3.12.10")
    else:
        (family / "3.12.10").mkdir(parents=True)
        (family / "3.12.10/arm64").symlink_to(outside / "3.12.10/arm64")
        (family / "3.12.10/arm64.complete").write_text("")
    before_outside = snapshot(outside)
    before_cache = snapshot(guest.cache)

    result = guest.run()
    assert snapshot(outside) == before_outside, "nothing outside the cache may change"
    assert result.stdout == b""
    if location.startswith("old"):
        assert result.returncode == 0, result.stderr.decode()
        assert (family / "3.12.11/arm64.complete").is_file()
        kept = {k: v for k, v in before_cache.items() if k.startswith("Python/3.12.10")}
        assert kept and all(snapshot(guest.cache)[k] == v for k, v in kept.items())
    else:
        assert result.returncode != 0
        assert snapshot(guest.cache) == before_cache
        if location in ("root", "family"):
            assert guest.boundary() == []


@pytest.mark.parametrize("failure", ["", "link", "probe", "backup", "symlink"])
def test_legacy_framework_consumers_follow_the_verified_patch_or_are_restored(
    guest: Guest, failure: str
):
    guest.keg(reports="3.12.16")
    template = guest.template / "python@3.12"
    framework = template / "Frameworks/Python.framework/Versions/3.12"
    (framework / "bin").mkdir(parents=True)
    target = framework / "bin/python3.12"
    if failure == "probe":
        target.write_text("#!/bin/sh\necho 3.12.10\n")
    else:
        target.symlink_to("../../../../../bin/python3.12")
    target.chmod(0o755)
    legacy = guest.frameworks / "3.12"
    legacy.mkdir(parents=True)
    (legacy / "old-runtime").write_text("preserve until verified")
    old_cache = guest.cached("3.12.10")
    if failure == "backup":
        legacy.with_name("3.12.runner-image-old").mkdir()
    if failure == "symlink":
        legacy.rename(guest.frameworks / "outside")
        legacy.symlink_to(guest.frameworks / "outside")
    before_cache = snapshot(guest.cache)
    result = guest.run(**({"TEST_LINK_FAIL": "1"} if failure == "link" else {}))
    if failure:
        assert result.returncode != 0
        assert (legacy / "old-runtime").read_text() == "preserve until verified"
        assert (old_cache.parent / "arm64.complete").exists()
        assert not (guest.cache / "Python/3.12.16/arm64.complete").exists()
        # No existing cache entry may be changed on a framework failure.
        after = snapshot(guest.cache)
        assert all(after.get(k) == v for k, v in before_cache.items())
    else:
        assert result.returncode == 0, result.stderr.decode()
        assert legacy.is_symlink()
        assert not legacy.with_name("3.12.runner-image-old").exists()
        assert not (old_cache.parent / "arm64.complete").exists()
        invoked = subprocess.run(
            [
                str(legacy / "bin/python3.12"),
                "-c",
                'print("%d.%d.%d" % sys.version_info[:3])',
            ],
            env=guest.env,
            capture_output=True,
            timeout=10,
        )
        assert invoked.returncode == 0
        assert invoked.stdout == b"3.12.16\n"
