import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shell(tmp_path):
    source = (ROOT / "macos/image/toolchain.sh").read_text()
    definitions, entrypoint = source.rsplit('\nmain "$@" </dev/null', 1)
    assert entrypoint.strip() == ""
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "brew.jsonl"
    brew = tools / "brew"
    brew.write_text(
        f"#!{sys.executable}\n"
        + """import sys,os,json,stat
from pathlib import Path
assert stat.S_ISCHR(os.fstat(0).st_mode), "brew must not consume the script"
args=sys.argv[1:]
with Path(os.environ["TEST_BREW_LOG"]).open("a") as f: f.write(json.dumps(args)+"\\n")
if args[0]==os.environ.get("TEST_FAIL"): raise SystemExit(7)
if args[0]=="list":
    installed=os.environ.get("TEST_INSTALLED", "")
    if "--versions" in args:
        raise SystemExit(0 if args[-1] in installed.splitlines() else 1)
    print(installed)
if args[0]=="uses": print(os.environ.get("TEST_USERS", ""))
"""
    )
    brew.chmod(0o755)

    def run(command, installed="", fail="", users=""):
        env = {
            **os.environ,
            "PATH": f"{tools}:/usr/bin:/bin",
            "TEST_BREW_LOG": str(log),
            "TEST_INSTALLED": installed,
            "TEST_FAIL": fail,
            "TEST_USERS": users,
            "HOME": str(tmp_path / "home"),
        }
        result = subprocess.run(
            ["/bin/bash", "-s"],
            input=definitions + "\n" + command + "\necho completed\n",
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )
        calls = (
            [json.loads(s) for s in log.read_text().splitlines()]
            if log.exists()
            else []
        )
        return result, calls

    return run


def test_upgrade_does_not_execute_inherited_third_party_taps(shell):
    result, calls = shell(
        "upgrade_core_formulas",
        "jq\nhomebrew/core/openssl@3\nbuildkite/buildkite/agent@3\nunknown/tap/tool",
    )
    assert result.returncode == 0, result.stderr
    assert calls == [
        ["list", "--formula", "--full-name"],
        ["upgrade", "--formula", "--quiet", "jq", "openssl@3"],
    ]


@pytest.mark.parametrize("installed", ["", "unknown/tap/tool"])
def test_no_core_formula_means_no_upgrade(shell, installed):
    result, calls = shell("upgrade_core_formulas", installed)
    assert result.returncode == 0
    assert calls == [["list", "--formula", "--full-name"]]


@pytest.mark.parametrize("failure", ["list", "upgrade"])
def test_failed_package_operation_stops_before_following_steps(shell, failure):
    result, _ = shell("upgrade_core_formulas", "jq", failure)
    assert result.returncode != 0
    assert "completed" not in result.stdout


def test_invalid_listing_cannot_inject_brew_options(shell):
    result, calls = shell("upgrade_core_formulas", "--force")
    assert result.returncode != 0
    assert calls == [["list", "--formula", "--full-name"]]


@pytest.mark.parametrize(
    "name,full",
    [
        ("packer", "hashicorp/tap/packer"),
        ("applesimutils", "wix-incubator/brew/applesimutils"),
    ],
)
def test_only_selected_formula_gets_explicit_trust(shell, name, full):
    result, calls = shell(f"install_image_formula {name}")
    assert result.returncode == 0, result.stderr
    assert calls == [
        ["trust", "--formula", full],
        ["install", "--quiet", full],
        ["upgrade", "--formula", "--quiet", full],
    ]


def test_unreviewed_tap_is_rejected_without_trusting_or_installing(shell):
    result, calls = shell("install_image_formula unknown/tap/tool")
    assert result.returncode != 0
    assert calls == []


def test_core_formula_does_not_need_additional_trust(shell):
    result, calls = shell("install_image_formula jq")
    assert result.returncode == 0
    assert calls == [["install", "--quiet", "jq"]]


def test_rejected_trust_does_not_fall_back_to_whole_tap_trust(shell):
    result, calls = shell("install_image_formula packer", fail="trust")
    assert result.returncode != 0
    assert calls == [["trust", "--formula", "hashicorp/tap/packer"]]


def test_unused_extras_removed_and_unrelated_ruby_preserved(shell, tmp_path):
    versions = tmp_path / "home/.rbenv/versions"
    for version in ("2.7.8", "3.4.12"):
        (versions / version / "openssl").mkdir(parents=True)
    outside = tmp_path / "external-ruby"
    outside.mkdir()
    (outside / "keep").touch()
    (versions / "2.7.7").symlink_to(outside, target_is_directory=True)
    result, calls = shell("retire_unused_inherited_tools", "otel-cli\ngitlab-runner")
    assert result.returncode == 0, result.stderr
    assert ["uninstall", "--formula", "otel-cli"] in calls
    assert ["uninstall", "--formula", "gitlab-runner"] in calls
    assert not (versions / "2.7.8").exists()
    assert (versions / "3.4.12/openssl").exists()
    assert (versions / "2.7.7").is_symlink() and (outside / "keep").exists()


def test_formula_dependents_prevent_removing_inherited_tool(shell):
    result, calls = shell(
        "retire_unused_inherited_tools", "otel-cli", users="example-dependent"
    )
    assert result.returncode == 0, result.stderr
    assert ["uses", "--installed", "otel-cli"] in calls
    assert not any(call[0] == "uninstall" for call in calls)
