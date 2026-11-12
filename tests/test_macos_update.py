import io
import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from runner_images import macos_update
from runner_images.macos_update import UpdateError, select, update_os

VM = "macos-26-xcode-example-build-1762650000-4242"
LABEL = "macOS Tahoe 26.7.2-25G332"
LISTING = """Software Update Tool

Finding available software
Software Update found the following new or updated software:
* Label: Safari27.2
\tTitle: Safari, Version: 27.2, Size: 210000KiB, Recommended: YES,
* Label: macOS Tahoe 26.7.1-25G301
\tTitle: macOS Tahoe 26.7.1, Version: 26.7.1, Size: 2900000KiB, Recommended: YES, Action: restart,
* Label: macOS Tahoe 26.7.2-25G332
\tTitle: macOS Tahoe 26.7.2, Version: 26.7.2, Size: 3000000KiB, Recommended: YES, Action: restart,
* Label: macOS 27.2-26C100
\tTitle: macOS 27.2, Version: 27.2, Size: 15000000KiB, Recommended: YES, Action: restart,
"""  # noqa: E501
MAIN = 139475673088
SHRINK = 5368684544
MUTATIONS = (
    "resizeContainer",
    "addPartition",
    "createContainer",
    "addVolume",
    "sgdisk",
    "--install",
    "brew install",
)


def ok(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class Guest:
    """Fake Tart CLI plus a guest whose disk state follows diskutil commands."""

    def __init__(self, recovery=False):
        self.calls: list[list[str]] = []
        self.popens: list[tuple[list[str], dict]] = []
        self.clock = 0.0
        self.state = "running"
        self.model = "VirtualMac2,1"
        self.version = "26.6.2"
        self.boot = 100
        self.listing = LISTING
        self.minimum = 60_000_000_000
        self.fail: str | None = None
        self.parts = [
            {
                "DeviceIdentifier": "disk0s1",
                "Content": "Apple_APFS_ISC",
                "Size": 524288000,
            },
            {"DeviceIdentifier": "disk0s2", "Content": "Apple_APFS", "Size": MAIN},
        ]
        self.containers = [
            {
                "ContainerReference": "disk3",
                "PhysicalStores": [{"DeviceIdentifier": "disk0s2"}],
                "Volumes": [{"Roles": ["System"]}],
            },
        ]
        if recovery:
            self.parts.append(
                {
                    "DeviceIdentifier": "disk0s3",
                    "Content": "Apple_APFS_Recovery",
                    "Size": 5368709120,
                }
            )
            self.containers.append(
                {
                    "ContainerReference": "disk1",
                    "PhysicalStores": [{"DeviceIdentifier": "disk0s3"}],
                    "Volumes": [{"Roles": ["Recovery"]}],
                }
            )
        self.helper_partition = False
        self.installer = self.restarting

    def restarting(self):
        self.state = "stopped"
        self.version, self.boot = "26.7.2", 200
        return ok(code=255, stderr="Error: connection to the guest agent was lost\n")

    def run(self, argv, **kwargs):
        assert 0 < kwargs["timeout"] <= 14400
        interactive = argv[:3] == ["tart", "exec", "-i"]
        if interactive:
            assert kwargs["input"] == "admin\n" and kwargs["stdin"] is None
            argv = argv[:2] + argv[3:]
        else:
            assert kwargs["stdin"] is subprocess.DEVNULL and kwargs.get("input") is None
        self.calls.append(argv)
        assert argv[0] == "tart", "host must only invoke tart"
        if argv[1:] == ["list", "--format", "json"]:
            return ok(
                json.dumps(
                    [
                        {"Name": "macos-26-xcode-other-build-1-2", "State": "running"},
                        {"Name": VM, "State": self.state},
                    ]
                )
            )
        assert argv[1:3] == ["exec", VM], f"unexpected Tart target {argv}"
        command = " ".join(argv[3:])
        if self.fail and self.fail in command:
            return ok(code=1, stderr=f"synthetic failure: {self.fail}\n")
        if self.state != "running":
            return ok(code=1, stderr="Error: VM is not running\n")
        if command == "/usr/bin/dscl . -authonly admin admin":
            return ok()
        if command == "/usr/sbin/sysctl -n hw.model":
            return ok(self.model + "\n")
        if command == "/usr/sbin/sysctl -n kern.boottime":
            return ok(f"{{ sec = {self.boot}, usec = 0 }} Mon Nov  9 00:00:00 2026\n")
        if command == "/usr/bin/sw_vers -productVersion":
            return ok(self.version + "\n")
        if command == "/usr/sbin/softwareupdate --list":
            return ok(self.listing)
        if command.startswith("/opt/homebrew/bin/brew list"):
            return ok(code=1)
        if command.startswith("/opt/homebrew/bin/brew "):
            return ok()
        assert argv[3:5] == ["/usr/bin/sudo", "-n"], f"unprivileged write {argv}"
        args = argv[5:]
        if args[0] == "/usr/sbin/softwareupdate":
            assert args[1:] == [
                "--install",
                LABEL,
                "--restart",
                "--user",
                "admin",
                "--stdinpass",
            ]
            assert interactive
            return self.installer()
        if args[0] == "/opt/homebrew/bin/sgdisk":
            assert args[1:] == ["-t", "3:AF0C", "-c", "3:Recovery", "/dev/disk0"]
            self.parts[2]["Content"] = "Apple_APFS_Recovery"
            return ok()
        assert args[0] == "/usr/sbin/diskutil"
        args = args[1:]
        if args == ["info", "-plist", "/"]:
            info = {
                "APFSContainerReference": "disk3",
                "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}],
            }
            return ok(plistlib.dumps(info).decode())
        if args == ["list", "-plist", "disk0"]:
            disk = {
                "DeviceIdentifier": "disk0",
                "Content": "GUID_partition_scheme",
                "Partitions": self.parts,
            }
            return ok(plistlib.dumps({"AllDisksAndPartitions": [disk]}).decode())
        if args == ["apfs", "list", "-plist"]:
            return ok(plistlib.dumps({"Containers": self.containers}).decode())
        if args == ["apfs", "resizeContainer", "disk0s2", "limits", "-plist"]:
            return ok(plistlib.dumps({"MinimumSizePreferred": self.minimum}).decode())
        if args in (["verifyDisk", "disk0"], ["verifyVolume", "disk3"]):
            return ok()
        if args[:3] == ["apfs", "resizeContainer", "disk0s2"]:
            assert args[3] == f"{MAIN - SHRINK}B"
            self.parts[1]["Size"] = MAIN - SHRINK
            return ok()
        if args[0] == "addPartition":
            assert args[1:] == [
                "disk0",
                "%52637672-7900-11AA-AA11-00306543ECAC%",
                "%noformat%",
                "0",
            ]
            self.parts.append(
                {
                    "DeviceIdentifier": "disk0s3",
                    "Content": "Apple_APFS_Recovery",
                    "Size": SHRINK - 4096,
                }
            )
            if self.helper_partition:
                self.parts.append(
                    {
                        "DeviceIdentifier": "disk0s4",
                        "Content": "Apple_Boot",
                        "Size": 134217728,
                    }
                )
            return ok()
        if args == ["apfs", "createContainer", "disk0s3"]:
            self.parts[2]["Content"] = "Apple_APFS"
            self.containers.append(
                {
                    "ContainerReference": "disk4",
                    "PhysicalStores": [{"DeviceIdentifier": "disk0s3"}],
                    "Volumes": [],
                }
            )
            return ok()
        if args == [
            "apfs",
            "addVolume",
            "disk4",
            "APFS",
            "Recovery",
            "-role",
            "R",
            "-nomount",
        ]:
            self.containers[-1]["Volumes"].append({"Roles": ["Recovery"]})
            return ok()
        raise AssertionError(f"unexpected guest command {argv}")

    def popen(self, argv, **kwargs):
        self.popens.append((argv, kwargs))
        if argv[:2] == ["tart", "run"]:
            assert argv == ["tart", "run", VM, "--no-graphics", "--net-bridged", "en0"]
            assert (
                kwargs["stdout"].mode == "a" and kwargs["stderr"] is subprocess.STDOUT
            )
            self.state = "running"
            return Process()
        assert argv[0:4] == ["tart", "exec", "-i", VM]
        assert kwargs["stdin"] is subprocess.PIPE
        assert kwargs["stdout"].writable()
        self.calls.append(["tart", "exec", VM, *argv[4:]])
        result = self.installer()
        kwargs["stdout"].write(result.stdout + result.stderr)
        return InstallProcess(result)

    def sleep(self, seconds):
        self.clock += seconds
        if self.state == "stopped" and self.version == "26.7.2":
            self.state = "running"

    def mutations(self):
        return [
            call
            for call in self.calls
            if call[-1] != "-plist" and any(m in " ".join(call) for m in MUTATIONS)
        ]


class Process:
    def __init__(self):
        self.returncode = None
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()

    def poll(self):
        return self.returncode

    def terminate(self):
        return None

    def kill(self):
        self.returncode = -9

    def wait(self):
        return self.returncode

    def communicate(self, timeout=None):
        return "", ""


class InstallProcess:
    def __init__(self, result):
        self.returncode = result.returncode
        self.stdout = io.StringIO(result.stdout + result.stderr)
        self.stdin = io.StringIO()

    def poll(self):
        return self.returncode

    def terminate(self):
        return None

    def kill(self):
        self.returncode = -9

    def wait(self):
        return self.returncode

    def communicate(self, timeout=None):
        return self.stdout.read(), ""


@pytest.fixture
def guest(monkeypatch):
    fake = Guest()
    monkeypatch.setattr(macos_update.subprocess, "run", fake.run)
    monkeypatch.setattr(macos_update.subprocess, "Popen", fake.popen)
    monkeypatch.setattr(macos_update.time, "monotonic", lambda: fake.clock)
    monkeypatch.setattr(macos_update.time, "sleep", fake.sleep)
    return fake


def test_selects_highest_same_major_exact_label():
    assert select((26, 6, 2), LISTING) == (LABEL, (26, 7, 2))
    assert select((26, 7, 2), LISTING) is None
    assert select((25, 9, 0), LISTING) is None


def test_already_current_makes_no_mutation(guest, tmp_path):
    guest.listing = LISTING.split("* Label: macOS Tahoe")[0]
    assert update_os(VM, "en0", tmp_path / "log") == "26.6.2"
    assert not guest.mutations() and not guest.popens
    assert not any("diskutil" in " ".join(c) for c in guest.calls)


def test_existing_recovery_updates_without_partition_changes(guest, tmp_path):
    recovered = Guest(recovery=True)
    guest.parts, guest.containers = recovered.parts, recovered.containers
    log = tmp_path / "log"
    assert update_os(VM, "en0", log) == "26.7.2"
    assert [" ".join(c[5:]) for c in guest.mutations()] == [
        f"/usr/sbin/softwareupdate --install {LABEL} --restart --user admin --stdinpass"
    ]
    assert len(guest.popens) == 2
    assert "exited 255" in log.read_text()


def test_repairs_missing_recovery_then_updates(guest, tmp_path):
    assert update_os(VM, "en0", tmp_path / "log") == "26.7.2"
    disk = "/usr/bin/sudo -n /usr/sbin/diskutil"
    assert [" ".join(c[3:]) for c in guest.mutations()] == [
        "/opt/homebrew/bin/brew install --formula gptfdisk",
        f"{disk} apfs resizeContainer disk0s2 {MAIN - SHRINK}B",
        f"{disk} addPartition disk0 "
        "%52637672-7900-11AA-AA11-00306543ECAC% %noformat% 0",
        f"{disk} apfs createContainer disk0s3",
        f"{disk} apfs addVolume disk4 APFS Recovery -role R -nomount",
        "/usr/bin/sudo -n /opt/homebrew/bin/sgdisk -t 3:AF0C -c 3:Recovery /dev/disk0",
        f"/usr/bin/sudo -n /usr/sbin/softwareupdate --install {LABEL} --restart "
        "--user admin --stdinpass",
    ]
    commands = [" ".join(c[3:]) for c in guest.calls]
    assert "/opt/homebrew/bin/brew uninstall --formula gptfdisk" in commands
    assert guest.parts[2]["Content"] == "Apple_APFS_Recovery"


@pytest.mark.parametrize(
    "change",
    [
        lambda g: setattr(g, "model", "Mac15,3"),
        lambda g: g.parts[0].update(Size=1),
        lambda g: g.parts.append(
            {"DeviceIdentifier": "disk0s3", "Content": "Apple_APFS", "Size": 1}
        ),
        lambda g: g.parts.extend(Guest(recovery=True).parts[2:] * 2),
        lambda g: (
            g.parts.extend(Guest(recovery=True).parts[2:]),
            g.containers.append(
                {
                    "ContainerReference": "disk1",
                    "PhysicalStores": [{"DeviceIdentifier": "disk0s3"}],
                    "Volumes": [],
                }
            ),
        ),
        lambda g: setattr(g, "minimum", MAIN - SHRINK + 1),
        lambda g: setattr(g, "state", "stopped"),
    ],
    ids=[
        "physical-mac",
        "isc-size",
        "partial",
        "extra",
        "no-volume",
        "unsafe-resize",
        "not-running",
    ],
)
def test_rejects_unsafe_targets_before_writes(guest, tmp_path, change):
    change(guest)
    with pytest.raises(UpdateError):
        update_os(VM, "en0", tmp_path / "log")
    assert not guest.mutations() and not guest.popens


@pytest.mark.parametrize(
    ("vm", "bridge"),
    [("macos-26-xcode", "en0"), (f"{VM}; rm", "en0"), ("other", "en0"), (VM, "en0 x")],
)
def test_rejects_wrong_names_without_commands(guest, tmp_path, vm, bridge):
    with pytest.raises(UpdateError):
        update_os(vm, bridge, tmp_path / "log")
    assert not guest.calls


def test_failed_repair_command_stops(guest, tmp_path):
    guest.fail = "addPartition"
    with pytest.raises(UpdateError, match="synthetic failure: addPartition"):
        update_os(VM, "en0", tmp_path / "log")
    assert not any("--install" in " ".join(c) for c in guest.calls)
    assert not any("createContainer" in " ".join(c) for c in guest.calls)


def test_installer_exit_zero_with_fatal_error_is_rejected(guest, tmp_path):
    guest.installer = lambda: ok(
        "Downloading macOS Tahoe 26.7.2\n"
        "Error: (MobileSoftwareUpdateErrorDomain error 1256.)\n"
    )
    with pytest.raises(UpdateError, match="1256"):
        update_os(VM, "en0", tmp_path / "log")
    assert len(guest.popens) == 1 and guest.version == "26.6.2"
    assert "1256" in (tmp_path / "log").read_text()


def test_sudo_refusal_is_rejected(guest, tmp_path):
    guest.installer = lambda: ok(code=1, stderr="sudo: a password is required\n")
    with pytest.raises(UpdateError, match="password"):
        update_os(VM, "en0", tmp_path / "log")


def test_installer_stream_timeout_still_requires_guest_version(guest, tmp_path):
    guest.installer = guest.restarting
    assert update_os(VM, "en0", tmp_path / "log") == "26.7.2"


def test_restart_stream_closes_after_guest_boot_changes(guest, tmp_path, monkeypatch):
    class StreamingProcess(Process):
        def __init__(self):
            super().__init__()
            self.returncode = None
            self.stdout = io.StringIO("installer output\n")

        def terminate(self):
            self.returncode = 0

    def popen(argv, **kwargs):
        guest.popens.append((argv, kwargs))
        guest.restarting()
        kwargs["stdout"].write("installer output\n")
        return StreamingProcess()

    monkeypatch.setattr(macos_update.subprocess, "Popen", popen)
    assert update_os(VM, "en0", tmp_path / "log") == "26.7.2"
    assert guest.clock <= 15


@pytest.mark.parametrize("reported", ["27.2", "26.6.1", "garbage"])
def test_wrong_post_reboot_version_is_not_success(guest, tmp_path, reported):
    def restart():
        result = guest.restarting()
        guest.version = reported
        return result

    guest.installer = restart
    with pytest.raises(UpdateError, match="unexpected macOS|deadline"):
        update_os(VM, "en0", tmp_path / "log")
    assert guest.clock <= 3600 + 15


def test_old_version_after_restart_waits_only_until_deadline(guest, tmp_path):
    def restart():
        result = guest.restarting()
        guest.version = "26.6.2"
        return result

    guest.installer = restart
    with pytest.raises(UpdateError, match="deadline; last observed 26.6.2"):
        update_os(VM, "en0", tmp_path / "log")
    assert 3600 <= guest.clock <= 3600 + 15


def test_exit_zero_without_restart_fails_at_deadline(guest, tmp_path):
    guest.installer = lambda: ok("Downloading macOS Tahoe 26.7.2\nDone.\n")
    with pytest.raises(UpdateError, match="deadline"):
        update_os(VM, "en0", tmp_path / "log")
    assert guest.clock == 3600 and len(guest.popens) == 1


def test_failed_restart_is_not_retried(guest, tmp_path, monkeypatch):
    def popen(argv, **kwargs):
        guest.popens.append((argv, kwargs))
        process = Process()
        process.returncode = 1
        return process

    monkeypatch.setattr(macos_update.subprocess, "Popen", popen)
    with pytest.raises(UpdateError, match="deadline"):
        update_os(VM, "en0", tmp_path / "log")
    assert len(guest.popens) == 1


def test_repeated_stops_are_bounded(guest, tmp_path, monkeypatch):
    def popen(argv, **kwargs):
        guest.popens.append((argv, kwargs))
        process = Process()
        process.returncode = 0
        return process

    monkeypatch.setattr(macos_update.subprocess, "Popen", popen)
    with pytest.raises((UpdateError, ValueError), match="deadline|closed"):
        update_os(VM, "en0", tmp_path / "log")
    assert guest.clock == 3600 and len(guest.popens) == 1


def test_reports_failed_tart_list(guest, tmp_path, monkeypatch):
    monkeypatch.setattr(
        macos_update.subprocess, "run", lambda argv, **kwargs: ok(code=1, stderr="x")
    )
    with pytest.raises(UpdateError, match="tart list exited 1"):
        update_os(VM, "en0", tmp_path / "log")


def test_script_wrapper_reports_errors(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/update-macos.py"),
            "not-a-build",
            "en0",
            str(tmp_path / "log"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "not a temporary build VM name" in result.stderr


def test_target_version_without_changed_boot_is_rejected(guest, tmp_path):
    def install():
        guest.version = "26.7.2"
        return ok("Done.")

    guest.installer = install
    with pytest.raises(UpdateError, match="deadline"):
        update_os(VM, "en0", tmp_path / "log")
    assert guest.clock == 3600


def test_transient_reboot_probe_timeout_recovers(guest, tmp_path, monkeypatch):
    original = guest.run
    timed_out = False

    def run(argv, **kwargs):
        nonlocal timed_out
        if guest.boot == 200 and "kern.boottime" in argv and not timed_out:
            timed_out = True
            raise subprocess.TimeoutExpired(argv, 30)
        return original(argv, **kwargs)

    monkeypatch.setattr(macos_update.subprocess, "run", run)
    assert update_os(VM, "en0", tmp_path / "log") == "26.7.2"
    assert timed_out and guest.clock > 0


def test_diskutil_generated_boot_helper_is_preserved(guest, tmp_path):
    guest.helper_partition = True
    assert update_os(VM, "en0", tmp_path / "log") == "26.7.2"
    assert guest.parts[3] == {
        "DeviceIdentifier": "disk0s4",
        "Content": "Apple_Boot",
        "Size": 134217728,
    }


def test_custom_guest_credentials_fail_before_disk_writes(guest, tmp_path):
    guest.fail = "dscl"
    with pytest.raises(UpdateError, match="default guest owner"):
        update_os(VM, "en0", tmp_path / "log")
    assert not guest.mutations() and not guest.popens
    assert not any("diskutil" in " ".join(c) for c in guest.calls)


def test_staged_update_volume_resumes_without_partition_writes(guest, tmp_path):
    recovered = Guest(recovery=True)
    guest.parts, guest.containers = recovered.parts, recovered.containers
    guest.containers[-1]["Volumes"].append({"Roles": ["Update"]})
    assert update_os(VM, "en0", tmp_path / "log") == "26.7.2"
    assert not any("resizeContainer" in c or "addPartition" in c for c in guest.calls)


@pytest.mark.parametrize("mode", ["exit", "reboot", "timeout"])
def test_installer_real_process_stream_and_cleanup(tmp_path, monkeypatch, mode):
    real_popen = subprocess.Popen
    real_sleep = macos_update.time.sleep
    ready = tmp_path / "ready"
    children = []
    script = (
        "import pathlib,sys,time; "
        "assert sys.stdin.read() == 'admin\\n'; "
        "sys.stdout.write('x' * 1000000); sys.stdout.flush(); "
        f"pathlib.Path({str(ready)!r}).touch(); "
        + ("time.sleep(60)" if mode != "exit" else "")
    )

    def popen(argv, **kwargs):
        assert argv[:4] == ["tart", "exec", "-i", VM]
        child = real_popen([sys.executable, "-c", script], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(macos_update.subprocess, "Popen", popen)
    monkeypatch.setattr(macos_update, "state", lambda vm: "running")
    monkeypatch.setattr(
        macos_update,
        "boot",
        lambda vm: "200" if mode == "reboot" and ready.exists() else "100",
    )
    monkeypatch.setattr(macos_update.time, "sleep", lambda _: real_sleep(0.005))
    try:
        if mode == "timeout":
            with pytest.raises(UpdateError, match="timed out"):
                macos_update.install_and_restart(VM, LABEL, "100", timeout=0.3)
        else:
            code, output = macos_update.install_and_restart(VM, LABEL, "100", timeout=3)
            assert code == 0 if mode == "exit" else code != 0
            assert output == "x" * 1000000
        assert len(children) == 1 and children[0].poll() is not None
        assert children[0].stdin is None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
            if child.stdout:
                child.stdout.close()
