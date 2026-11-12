import subprocess
from pathlib import Path

import pytest

from runner_images import macos_host


def document(**changes):
    values = {
        "channel": "channel-0",
        "preview": "0",
        "major": "23",
        "os": "macosx",
        "filename": "commandlinetools-mac_x86_64-123_latest.zip",
        "checksum": "a" * 40,
        "algorithm": "sha1",
    }
    values.update(changes)
    return f"""<repository><remotePackage path="cmdline-tools;latest">
<revision><major>{values["major"]}</major><minor>0</minor><preview>{values["preview"]}</preview></revision>
<channelRef ref="{values["channel"]}"/>
<archives><archive><host-os>{values["os"]}</host-os><complete>
<url>{values["filename"]}</url>
<checksum type="{values["algorithm"]}">{values["checksum"]}</checksum>
</complete></archive></archives></remotePackage></repository>"""


def test_current_official_macos_archive_and_revision_are_resolved():
    value = macos_host.parse_android_tools(document())
    assert value["version"] == "23.0"
    assert (
        value["url"]
        == "https://dl.google.com/android/repository/commandlinetools-mac_x86_64-123_latest.zip"
    )
    assert value["checksum_algorithm"] == "sha1" and value["checksum"] == "a" * 40
    assert (
        macos_host.parse_android_tools(document(algorithm="sha256", checksum="b" * 64))[
            "checksum_algorithm"
        ]
        == "sha256"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"channel": "channel-2"},
        {"preview": "1"},
        {"major": "bad"},
        {"os": "linux"},
        {"filename": "../../bad.zip"},
        {"checksum": "abc"},
        {"algorithm": "md5"},
    ],
)
def test_unstable_or_unverified_archive_is_rejected(changes):
    with pytest.raises(ValueError):
        macos_host.parse_android_tools(document(**changes))


def test_missing_or_duplicate_stable_release_is_rejected():
    with pytest.raises(ValueError):
        macos_host.parse_android_tools("<repository/>")
    one = document().replace("<repository>", "").replace("</repository>", "")
    with pytest.raises(ValueError):
        macos_host.parse_android_tools("<repository>" + one + one + "</repository>")


def test_host_download_boundary_uses_retried_file_and_preserves_errors(monkeypatch):
    def run(args, **kwargs):
        assert args[0] == "curl" and "--retry-all-errors" in args
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["check"]
        Path(args[args.index("-o") + 1]).write_text(document())

    monkeypatch.setattr(macos_host.subprocess, "run", run)
    assert macos_host.resolve_android_tools()["version"] == "23.0"

    def fail(args, **kwargs):
        raise subprocess.CalledProcessError(22, args)

    monkeypatch.setattr(macos_host.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        macos_host.resolve_android_tools()
