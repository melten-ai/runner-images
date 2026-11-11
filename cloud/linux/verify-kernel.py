"""Bind supplier-fixed kernel dispositions to native image evidence, fail closed.

For every platform entry in the public manifest whose bound kernel file is
present, the exact vmlinuz bytes, dpkg ownership, binary/source package
identities and versions, the modules md5sums control digest, a clean
``dpkg --verify`` and native ``dpkg --compare-versions`` against each unique
supplier fixed version are all required. A present-but-changed artifact fails
qualification before SBOM collection. An absent bound kernel file only makes
that entry inactive: its scanner rules are pinned to the exact version and
path, so they cannot match any other kernel, and a new/unbound kernel keeps
all of its findings. This checker is read-only and uses no network.
"""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path


class Failure(RuntimeError):
    pass


def run(args):
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as error:
        raise Failure(f"cannot execute {args[0]}: {error}") from error


def query(args, context):
    result = run(args)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostics"
        raise Failure(f"{context}: {' '.join(args)} failed: {detail}")
    return result.stdout


def sha256(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def require_regular(path, context):
    try:
        info = os.lstat(path)
    except OSError as error:
        raise Failure(f"{context}: {path} is missing or unreadable: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise Failure(f"{context}: {path} is not a regular non-linked file")


def require_os_identity(root, expected):
    release_file = root / "etc/os-release"
    if not release_file.is_file():
        raise Failure(f"OS identity: {release_file} is missing")
    observed = {}
    for line in release_file.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            observed[key] = value.strip().strip('"')
    for key, value in (("ID", expected["id"]), ("VERSION_ID", expected["version_id"])):
        if observed.get(key) != value:
            raise Failure(
                f"OS identity: {key}={observed.get(key)!r} is not the expected "
                f"{value!r}"
            )


def require_package(name, expected, context):
    output = query(
        [
            "dpkg-query",
            "-W",
            "-f",
            "${db:Status-Status}\\t${Version}\\t${source:Package}\\t"
            "${source:Version}\\n",
            name,
        ],
        context,
    )
    fields = output.rstrip("\n").split("\t")
    if len(fields) != 4:
        raise Failure(f"{context}: unexpected dpkg-query output {output!r}")
    status, version, source, source_version = fields
    for label, observed, wanted in (
        ("status", status, "installed"),
        ("version", version, expected["version"]),
        ("source package", source, expected["source"]),
        ("source version", source_version, expected["version"]),
    ):
        if observed != wanted:
            raise Failure(f"{context}: {label} {observed!r} is not {wanted!r}")
    return version


def verify_platform(key, entry, root):
    release = entry["kernel_release"]
    context = f"kernel {release}"
    boot = entry["boot_image"]
    path = root / boot["path"]
    if not os.path.lexists(path):
        return {
            "platform": key,
            "release": release,
            "status": "inactive",
            "reason": "bound kernel file absent; pinned dispositions cannot "
            "match any other kernel version or path",
        }
    require_regular(path, context)
    observed_sha = sha256(path)
    if observed_sha != boot["sha256"]:
        raise Failure(
            f"{context}: {path} SHA-256 {observed_sha} is not the bound "
            f"{boot['sha256']}"
        )
    require_os_identity(root, entry["os_release"])
    image = entry["image_package"]
    modules = entry["modules_package"]
    recorded = "/" + boot["path"]
    ownership = query(["dpkg", "-S", recorded], context).splitlines()
    if ownership != [f"{image['name']}: {recorded}"]:
        raise Failure(
            f"{context}: {recorded} ownership {ownership!r} is not {image['name']}"
        )
    installed = require_package(image["name"], image, f"{context} image package")
    require_package(modules["name"], modules, f"{context} modules package")
    md5sums = query(
        ["dpkg-query", "--control-path", modules["name"], "md5sums"], context
    ).strip()
    if not md5sums:
        raise Failure(f"{context}: {modules['name']} has no md5sums control file")
    require_regular(md5sums, f"{context} modules md5sums")
    observed_md5sums = sha256(md5sums)
    if observed_md5sums != modules["md5sums_sha256"]:
        raise Failure(
            f"{context}: modules md5sums SHA-256 {observed_md5sums} is not the "
            f"bound {modules['md5sums_sha256']}"
        )
    verify = run(["dpkg", "--verify", image["name"], modules["name"]])
    discrepancies = (verify.stdout + verify.stderr).strip()
    if verify.returncode or discrepancies:
        raise Failure(
            f"{context}: dpkg --verify reported discrepancies or failed "
            f"(exit {verify.returncode}): {discrepancies or 'no diagnostics'}"
        )
    fixed_versions = sorted({record["fixed"] for record in entry["cves"].values()})
    for fixed in fixed_versions:
        comparison = run(["dpkg", "--compare-versions", installed, "ge", fixed])
        if comparison.returncode:
            raise Failure(
                f"{context}: installed {installed} is not >= supplier fixed "
                f"{fixed} (exit {comparison.returncode})"
            )
    running = query(["uname", "-r"], context).strip()
    bound_running = running == release
    if bound_running:
        version_file = root / "proc/version"
        if not version_file.is_file():
            raise Failure(f"{context}: running kernel has no {version_file}")
        proc = version_file.read_text()
        if not proc.startswith(f"Linux version {release} "):
            raise Failure(
                f"{context}: /proc/version {proc!r} does not identify "
                f"the running {release}"
            )
    return {
        "platform": key,
        "release": release,
        "status": "verified",
        "vmlinuz": recorded,
        "vmlinuz_sha256": observed_sha,
        "image_package": image["name"],
        "modules_package": modules["name"],
        "version": installed,
        "md5sums_sha256": observed_md5sums,
        "cves": len(entry["cves"]),
        "fixed_versions": fixed_versions,
        "running": running,
        "running_is_bound": bound_running,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/"))
    arguments = parser.parse_args()
    manifest = json.loads(arguments.manifest.read_text())
    observations = [
        verify_platform(key, manifest["platforms"][key], arguments.root)
        for key in sorted(manifest["platforms"])
    ]
    # A passing report is only printed after every bound predicate held.
    for observation in observations:
        print("kernel-dispositions: " + json.dumps(observation, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Failure as failure:
        print(f"kernel disposition verification failed: {failure}", file=sys.stderr)
        sys.exit(1)
