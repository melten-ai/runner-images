"""Install the newest same-major macOS update inside an owned temporary Tart VM.

Every disk and OS command runs in the guest through `tart exec`. Success is
proven only by the guest reporting the target version after its restart.
"""

import json
import plistlib
import re
import subprocess
import time
from pathlib import Path
from typing import Any, cast

Version = tuple[int, int, int]


class UpdateError(RuntimeError):
    pass


def version(text: str) -> Version | None:
    if not re.fullmatch(r"\d+(\.\d+){0,2}", text):
        return None
    parts = [int(part) for part in text.split(".")] + [0, 0]
    return parts[0], parts[1], parts[2]


def select(current: Version, listing: str) -> tuple[str, Version] | None:
    """Return the exact catalog label of the newest same-major macOS update."""
    best: tuple[str, Version] | None = None
    for label, title, offered in re.findall(
        r"^\* Label: (.+?)\s*\n\s+Title: (.+?), Version: ([^,\s]+),",
        listing,
        re.MULTILINE,
    ):
        parsed = version(offered)
        if (
            title.startswith("macOS ")
            and parsed
            and parsed[0] == current[0]
            and parsed > current
            and (best is None or parsed > best[1])
        ):
            best = label, parsed
    return best


def guest(
    vm: str, *argv: str, timeout: float = 120, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    command = ["tart", "exec", *(["-i"] if input_text is not None else []), vm, *argv]
    try:
        return subprocess.run(
            command,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise UpdateError(f"{' '.join(argv)} timed out after {timeout:g}s") from error


def checked(vm: str, *argv: str, timeout: float = 120) -> str:
    result = guest(vm, *argv, timeout=timeout)
    if result.returncode:
        raise UpdateError(
            f"{' '.join(argv)} exited {result.returncode}:\n"
            f"{(result.stdout + result.stderr)[-4000:]}"
        )
    return result.stdout


def diskutil(vm: str, *args: str, timeout: float = 600) -> str:
    return checked(
        vm, "/usr/bin/sudo", "-n", "/usr/sbin/diskutil", *args, timeout=timeout
    )


def plist(vm: str, *args: str) -> dict[str, Any]:
    try:
        data = plistlib.loads(diskutil(vm, *args).encode())
    except plistlib.InvalidFileException as error:
        raise UpdateError(f"diskutil {' '.join(args)} returned no plist") from error
    if not isinstance(data, dict):
        raise UpdateError(f"diskutil {' '.join(args)} returned an unexpected plist")
    return data  # pyright: ignore[reportUnknownVariableType]


def partitions(vm: str) -> list[tuple[Any, Any, Any]]:
    value = plist(vm, "list", "-plist", "disk0").get("AllDisksAndPartitions")
    disks = cast(list[Any], value) if isinstance(value, list) else []
    if (
        len(disks) != 1
        or disks[0].get("DeviceIdentifier") != "disk0"
        or disks[0].get("Content") != "GUID_partition_scheme"
    ):
        raise UpdateError("guest disk0 is not a single GUID partitioned disk")
    return [
        (part.get("DeviceIdentifier"), part.get("Content"), part.get("Size"))
        for part in disks[0].get("Partitions", [])
    ]


def container(vm: str, store: str) -> dict[str, Any]:
    matches = [
        item
        for item in plist(vm, "apfs", "list", "-plist").get("Containers", [])
        if [s.get("DeviceIdentifier") for s in item.get("PhysicalStores", [])]
        == [store]
    ]
    if len(matches) != 1:
        raise UpdateError(f"expected one APFS container on {store}")
    return matches[0]


def layout(vm: str) -> tuple[bool, int]:
    """Return (has system recovery, main store size) for the known layouts only."""
    info = plist(vm, "info", "-plist", "/")
    stores = [s.get("APFSPhysicalStore") for s in info.get("APFSPhysicalStores", [])]
    if stores != ["disk0s2"] or info.get("APFSContainerReference") != container(
        vm, "disk0s2"
    ).get("ContainerReference"):
        raise UpdateError(f"unexpected root APFS physical stores {stores}")
    parts = partitions(vm)
    if len(parts) == 4 and parts[3] == ("disk0s4", "Apple_Boot", 134217728):
        parts = parts[:3]
    size = parts[1][2] if len(parts) > 1 else None
    if (
        len(parts) not in (2, 3)
        or parts[0] != ("disk0s1", "Apple_APFS_ISC", 524288000)
        or parts[1][:2] != ("disk0s2", "Apple_APFS")
        or not isinstance(size, int)
        or size <= 0
    ):
        raise UpdateError(f"unknown guest partition layout {parts}")
    if len(parts) == 2:
        return False, size
    roles = [v.get("Roles") for v in container(vm, "disk0s3").get("Volumes", [])]
    if parts[2][:2] != ("disk0s3", "Apple_APFS_Recovery") or roles not in (
        [["Recovery"]],
        [["Recovery"], ["Update"]],
        [["Update"], ["Recovery"]],
    ):
        raise UpdateError(f"partial or unknown recovery layout {parts} {roles}")
    return True, size


def repair(vm: str, size: int) -> None:
    """Add the system recovery partition with the sequence tested on Tart."""
    shrink = 5368684544
    target = size - shrink
    minimum = plist(vm, "apfs", "resizeContainer", "disk0s2", "limits", "-plist").get(
        "MinimumSizePreferred"
    )
    if not isinstance(minimum, int) or minimum <= 0 or target < minimum:
        raise UpdateError(
            f"shrinking disk0s2 to {target} bytes is below its minimum {minimum}"
        )
    diskutil(vm, "verifyDisk", "disk0")
    diskutil(vm, "verifyVolume", container(vm, "disk0s2")["ContainerReference"])
    brew = "/opt/homebrew/bin/brew"
    preinstalled = guest(vm, brew, "list", "--formula", "gptfdisk").returncode == 0
    if not preinstalled:
        checked(vm, brew, "install", "--formula", "gptfdisk", timeout=1800)
    diskutil(vm, "apfs", "resizeContainer", "disk0s2", f"{target}B", timeout=3600)
    parts = partitions(vm)
    if len(parts) != 2 or parts[1][:3] != ("disk0s2", "Apple_APFS", target):
        raise UpdateError(f"unexpected layout after resize {parts}")
    diskutil(
        vm,
        "addPartition",
        "disk0",
        "%52637672-7900-11AA-AA11-00306543ECAC%",
        "%noformat%",
        "0",
    )
    parts = partitions(vm)
    if len(parts) == 4 and parts[3] == ("disk0s4", "Apple_Boot", 134217728):
        parts = parts[:3]
    if (
        len(parts) != 3
        or parts[2][0] != "disk0s3"
        or not isinstance(parts[2][2], int)
        or not 0 < parts[2][2] <= shrink
    ):
        raise UpdateError(f"unexpected layout after adding recovery {parts}")
    diskutil(vm, "apfs", "createContainer", "disk0s3")
    reference = container(vm, "disk0s3").get("ContainerReference")
    if not isinstance(reference, str) or not re.fullmatch(r"disk\d+", reference):
        raise UpdateError("recovery APFS container has no reference")
    diskutil(
        vm, "apfs", "addVolume", reference, "APFS", "Recovery", "-role", "R", "-nomount"
    )
    checked(
        vm,
        "/usr/bin/sudo",
        "-n",
        "/opt/homebrew/bin/sgdisk",
        "-t",
        "3:AF0C",
        "-c",
        "3:Recovery",
        "/dev/disk0",
    )
    diskutil(vm, "verifyDisk", "disk0")
    if not layout(vm)[0]:
        raise UpdateError("system recovery partition is still missing")
    if not preinstalled:
        checked(vm, brew, "uninstall", "--formula", "gptfdisk", timeout=600)


def state(vm: str) -> str:
    result = subprocess.run(
        ["tart", "list", "--format", "json"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise UpdateError(f"tart list exited {result.returncode}: {result.stderr}")
    matches = [item for item in json.loads(result.stdout) if item.get("Name") == vm]
    if len(matches) != 1:
        raise UpdateError(f"Tart VM {vm} is not present")
    return str(matches[0].get("State"))


def boot(vm: str) -> str | None:
    result = guest(vm, "/usr/sbin/sysctl", "-n", "kern.boottime", timeout=30)
    match = re.search(r"sec = (\d+)", result.stdout)
    return match.group(1) if not result.returncode and match else None


def wait(
    vm: str, bridge: str, log: Path, current: Version, target: Version, booted: str
) -> str:
    """Wait for the restarted guest to report the target same-major version."""
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    observed = "unreachable"
    while time.monotonic() - started < 3600:
        status = state(vm)
        if status == "stopped":
            if process and process.poll():
                raise UpdateError(f"tart run exited {process.returncode}; see {log}")
            if not process or process.poll() is not None:
                with log.open("a") as output:
                    process = subprocess.Popen(
                        ["tart", "run", vm, "--no-graphics", "--net-bridged", bridge],
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                    )
        elif status != "running":
            raise UpdateError(f"{vm} entered unexpected Tart state {status}")
        else:
            try:
                now = boot(vm)
                if now is None:
                    time.sleep(15)
                    continue
                result = guest(vm, "/usr/bin/sw_vers", "-productVersion", timeout=30)
            except UpdateError:
                time.sleep(15)
                continue
            observed = result.stdout.strip()
            reported = version(observed) if not result.returncode else None
            if reported:
                if reported[0] != current[0] or reported < current:
                    raise UpdateError(
                        f"guest restarted into unexpected macOS {observed}"
                    )
                if reported >= target and now != booted:
                    return observed
        time.sleep(15)
    raise UpdateError(
        f"guest did not report macOS {'.'.join(map(str, target))} within the "
        f"post-install deadline; last observed {observed}"
    )


def update_os(vm: str, bridge: str, log: Path) -> str:
    """Update an owned temporary build VM and return its verified version."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*-build-\d+-\d+", vm):
        raise UpdateError(f"{vm} is not a temporary build VM name")
    if not re.fullmatch(r"[a-z][a-z0-9]*", bridge):
        raise UpdateError(f"{bridge} is not a network interface name")
    if state(vm) != "running":
        raise UpdateError(f"{vm} is not running")
    model = checked(vm, "/usr/sbin/sysctl", "-n", "hw.model").strip()
    if not model.startswith("VirtualMac"):
        raise UpdateError(f"refusing to update non-virtual Mac model {model}")
    text = checked(vm, "/usr/bin/sw_vers", "-productVersion").strip()
    current = version(text)
    if not current:
        raise UpdateError(f"unexpected guest macOS version {text}")
    listing = guest(vm, "/usr/sbin/softwareupdate", "--list", timeout=900)
    if listing.returncode:
        raise UpdateError(f"softwareupdate --list exited {listing.returncode}")
    selected = select(current, listing.stdout + listing.stderr)
    if not selected:
        return text
    label, target = selected
    # Cirrus publishes this disposable guest's default account in its image
    # templates. Verify it before disk writes; never request host credentials.
    owner = guest(vm, "/usr/bin/dscl", ".", "-authonly", "admin", "admin")
    if owner.returncode:
        raise UpdateError("OS updates require the Cirrus default guest owner account")
    has_recovery, size = layout(vm)
    if not has_recovery:
        repair(vm, size)
    booted = boot(vm)
    if not booted:
        raise UpdateError("guest boot time is unavailable")
    try:
        installer = guest(
            vm,
            "/usr/bin/sudo",
            "-n",
            "/usr/sbin/softwareupdate",
            "--install",
            label,
            "--restart",
            "--user",
            "admin",
            "--stdinpass",
            timeout=4 * 3600,
            input_text="admin\n",
        )
        code, output = installer.returncode, installer.stdout + installer.stderr
    except UpdateError as error:
        # A restart can leave the stream open; the guest version decides.
        code, output = None, str(error)
    with log.open("a") as handle:
        handle.write(f"softwareupdate --install {label} exited {code}\n{output}\n")
    # softwareupdate can exit 0 after a fatal error without restarting. A
    # nonzero code is expected when the restart disconnects tart exec.
    if re.search(r"(?m)^sudo:", output) or (
        code == 0 and re.search(r"(?i)\b(error|failed|failure)\b", output)
    ):
        raise UpdateError(f"softwareupdate failed for {label}:\n{output[-4000:]}")
    return wait(vm, bridge, log, current, target, booted)
