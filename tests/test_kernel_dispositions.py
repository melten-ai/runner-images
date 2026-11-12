"""Kernel disposition checker behavior and policy/data coupling."""

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "cloud/linux/verify-kernel.py"
RELEASE = "7.0.0-1001-gcp"
OTHER = "7.0.0-1001-azure"
VERSION = "7.0.0-1001.1~24.04.1"
KERNEL = b"example kernel bytes"
MD5SUMS = b"0123456789abcdef0123456789abcdef  lib/modules/example.ko\n"

FAKE_PRELUDE = """#!/usr/bin/env python3
import json
import os
import sys

with open(os.path.join(os.path.dirname(__file__), os.pardir, "db.json")) as stream:
    db = json.load(stream)
args = sys.argv[1:]
if db.get("broken"):
    print("fixture tool: internal fixture error", file=sys.stderr)
    sys.exit(2)
"""

FAKE_DPKG = (
    FAKE_PRELUDE
    + """
if args[0] == "-S":
    owner = db["owners"].get(args[1])
    if owner is None:
        print("dpkg-query: no path found matching pattern", file=sys.stderr)
        sys.exit(1)
    print(owner + ": " + args[1])
elif args[0] == "--verify":
    lines = [line for pkg in args[1:] for line in db["verify"].get(pkg, [])]
    if lines:
        print("\\n".join(lines))
        sys.exit(1)
elif args[0] == "--compare-versions":
    sys.exit(1 if list(args[1:4]) in db.get("compare_false", []) else 0)
else:
    sys.exit(2)
"""
)

FAKE_DPKG_QUERY = (
    FAKE_PRELUDE
    + """
if args[0] == "--control-path":
    path = db["control_path"].get(args[1])
    if path is None:
        print("dpkg-query: control file not found", file=sys.stderr)
        sys.exit(1)
    print(path)
elif args[:2] == ["-W", "-f"]:
    record = db["packages"].get(args[3])
    if record is None:
        print("dpkg-query: no packages found matching", file=sys.stderr)
        sys.exit(1)
    assert "Status-Status" in args[2] and "source:Package" in args[2]
    row = [
        record["status"],
        record["version"],
        record["source"],
        record["source_version"],
    ]
    sys.stdout.write("\\t".join(row) + "\\n")
else:
    sys.exit(2)
"""
)

FAKE_UNAME = (
    FAKE_PRELUDE
    + """
assert args == ["-r"]
print(db["uname"])
"""
)


def load_gate():
    spec = importlib.util.spec_from_file_location(
        "check_kernel_security", ROOT / "cloud/check-kernel-security.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kernel(tmp_path):
    root = tmp_path / "root"
    (root / "boot").mkdir(parents=True)
    (root / "boot" / f"vmlinuz-{RELEASE}").write_bytes(KERNEL)
    (root / "etc").mkdir()
    (root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    (root / "proc").mkdir()
    (root / "proc/version").write_text(
        f"Linux version {RELEASE} (builder@example) #1 SMP\n"
    )
    md5sums = root / f"var/lib/dpkg/info/linux-modules-{RELEASE}.md5sums"
    md5sums.parent.mkdir(parents=True)
    md5sums.write_bytes(MD5SUMS)
    package = {
        "status": "installed",
        "version": VERSION,
        "source_version": VERSION,
    }
    db = {
        "owners": {f"/boot/vmlinuz-{RELEASE}": f"linux-image-{RELEASE}"},
        "packages": {
            f"linux-image-{RELEASE}": {**package, "source": "linux-signed-gcp-7.0"},
            f"linux-modules-{RELEASE}": {**package, "source": "linux-gcp-7.0"},
        },
        "control_path": {f"linux-modules-{RELEASE}": str(md5sums)},
        "verify": {},
        "uname": RELEASE,
    }
    manifest = {
        "schema": 1,
        "platforms": {
            "gcp": {
                "os_release": {"id": "ubuntu", "version_id": "24.04"},
                "kernel_release": RELEASE,
                "boot_image": {
                    "path": f"boot/vmlinuz-{RELEASE}",
                    "sha256": hashlib.sha256(KERNEL).hexdigest(),
                },
                "image_package": {
                    "name": f"linux-image-{RELEASE}",
                    "source": "linux-signed-gcp-7.0",
                    "version": VERSION,
                },
                "modules_package": {
                    "name": f"linux-modules-{RELEASE}",
                    "source": "linux-gcp-7.0",
                    "version": VERSION,
                    "md5sums_sha256": hashlib.sha256(MD5SUMS).hexdigest(),
                },
                "cves": {
                    "CVE-2026-1": {"fixed": VERSION},
                    "CVE-2026-2": {"fixed": "7.0.0-1000.0~24.04.1"},
                },
            },
            "azure": {
                "os_release": {"id": "ubuntu", "version_id": "24.04"},
                "kernel_release": OTHER,
                "boot_image": {"path": f"boot/vmlinuz-{OTHER}", "sha256": "0" * 64},
                "image_package": {
                    "name": f"linux-image-{OTHER}",
                    "source": "linux-signed-azure-7.0",
                    "version": VERSION,
                },
                "modules_package": {
                    "name": f"linux-modules-{OTHER}",
                    "source": "linux-azure-7.0",
                    "version": VERSION,
                    "md5sums_sha256": "0" * 64,
                },
                "cves": {"CVE-2026-1": {"fixed": VERSION}},
            },
        },
    }
    binaries = tmp_path / "bin"
    binaries.mkdir()
    database = tmp_path / "db.json"
    for name, template in (
        ("dpkg", FAKE_DPKG),
        ("dpkg-query", FAKE_DPKG_QUERY),
        ("uname", FAKE_UNAME),
    ):
        tool = binaries / name
        tool.write_text(template)
        tool.chmod(0o755)
    return {
        "root": root,
        "db": db,
        "database": database,
        "manifest": manifest,
        "path": tmp_path / "kernel-fixes.json",
        "binaries": binaries,
        "md5sums": md5sums,
    }


def execute(kernel):
    kernel["database"].write_text(json.dumps(kernel["db"]))
    kernel["path"].write_text(json.dumps(kernel["manifest"]))
    environment = {
        **os.environ,
        "PATH": f"{kernel['binaries']}:{os.environ['PATH']}",
    }
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            str(kernel["path"]),
            "--root",
            str(kernel["root"]),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def observations(result):
    assert result.returncode == 0, result.stderr
    lines = [
        line.removeprefix("kernel-dispositions: ")
        for line in result.stdout.splitlines()
        if line.startswith("kernel-dispositions: ")
    ]
    return {record["platform"]: record for record in map(json.loads, lines)}


def test_verified_bound_kernel_observation(kernel):
    recorded = observations(execute(kernel))
    assert recorded["azure"]["status"] == "inactive"
    bound = recorded["gcp"]
    assert bound["status"] == "verified"
    assert bound["vmlinuz_sha256"] == hashlib.sha256(KERNEL).hexdigest()
    assert bound["md5sums_sha256"] == hashlib.sha256(MD5SUMS).hexdigest()
    assert bound["version"] == VERSION
    assert bound["cves"] == 2
    assert bound["fixed_versions"] == sorted([VERSION, "7.0.0-1000.0~24.04.1"])
    assert bound["running_is_bound"] is True


def test_absent_bound_kernel_is_inactive_without_waiving_new_kernel(kernel):
    (kernel["root"] / f"boot/vmlinuz-{RELEASE}").unlink()
    (kernel["root"] / "boot/vmlinuz-7.0.0-1002-gcp").write_bytes(b"new kernel")
    result = execute(kernel)
    recorded = observations(result)
    assert recorded["gcp"]["status"] == "inactive"
    assert recorded["azure"]["status"] == "inactive"
    assert "verified" not in result.stdout


def test_unbound_running_kernel_skips_proc_binding(kernel):
    kernel["db"]["uname"] = "7.0.0-1002-gcp"
    (kernel["root"] / "proc/version").write_text("Linux version 7.0.0-1002-gcp\n")
    recorded = observations(execute(kernel))
    assert recorded["gcp"]["status"] == "verified"
    assert recorded["gcp"]["running_is_bound"] is False


def altered_kernel(kernel):
    (kernel["root"] / f"boot/vmlinuz-{RELEASE}").write_bytes(b"tampered kernel")


def symlinked_kernel(kernel):
    bound = kernel["root"] / f"boot/vmlinuz-{RELEASE}"
    copy_path = kernel["root"] / "boot/vmlinuz-copy"
    copy_path.write_bytes(KERNEL)
    bound.unlink()
    bound.symlink_to(copy_path)


def wrong_owner(kernel):
    kernel["db"]["owners"][f"/boot/vmlinuz-{RELEASE}"] = "linux-image-other"


def unowned_file(kernel):
    kernel["db"]["owners"] = {}


def wrong_version(kernel):
    kernel["db"]["packages"][f"linux-image-{RELEASE}"]["version"] = "7.0.0-999.9"


def wrong_source(kernel):
    kernel["db"]["packages"][f"linux-image-{RELEASE}"]["source"] = "linux-other"


def wrong_source_version(kernel):
    kernel["db"]["packages"][f"linux-modules-{RELEASE}"]["source_version"] = "0.1"


def not_installed(kernel):
    kernel["db"]["packages"][f"linux-modules-{RELEASE}"]["status"] = "half-installed"


def missing_package(kernel):
    del kernel["db"]["packages"][f"linux-modules-{RELEASE}"]


def altered_module_manifest(kernel):
    kernel["md5sums"].write_bytes(b"tampered module manifest\n")


def missing_module_manifest(kernel):
    kernel["md5sums"].unlink()


def symlinked_module_manifest(kernel):
    copy_path = kernel["md5sums"].with_name("md5sums-copy")
    copy_path.write_bytes(MD5SUMS)
    kernel["md5sums"].unlink()
    kernel["md5sums"].symlink_to(copy_path)


def missing_control_path(kernel):
    kernel["db"]["control_path"] = {}


def altered_module_data(kernel):
    # Real dpkg --verify output shape for a modified module file.
    kernel["db"]["verify"][f"linux-modules-{RELEASE}"] = [
        f"??5??????   /lib/modules/{RELEASE}/kernel/net/example.ko"
    ]


def installed_below_fixed(kernel):
    kernel["db"]["compare_false"] = [[VERSION, "ge", "7.0.0-1000.0~24.04.1"]]


def wrong_os_identity(kernel):
    (kernel["root"] / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="22.04"\n')


def missing_os_release(kernel):
    (kernel["root"] / "etc/os-release").unlink()


def mismatched_proc_version(kernel):
    (kernel["root"] / "proc/version").write_text("Linux version 7.0.0-999-gcp\n")


def dpkg_command_failure(kernel):
    kernel["db"]["broken"] = True


@pytest.mark.parametrize(
    "fault",
    [
        altered_kernel,
        symlinked_kernel,
        wrong_owner,
        unowned_file,
        wrong_version,
        wrong_source,
        wrong_source_version,
        not_installed,
        missing_package,
        altered_module_manifest,
        missing_module_manifest,
        symlinked_module_manifest,
        missing_control_path,
        altered_module_data,
        installed_below_fixed,
        wrong_os_identity,
        missing_os_release,
        mismatched_proc_version,
        dpkg_command_failure,
    ],
)
def test_present_bound_kernel_faults_fail_without_passing_report(kernel, fault):
    before = {path for path in kernel["root"].rglob("*")}
    fault(kernel)
    present = {path for path in kernel["root"].rglob("*")}
    result = execute(kernel)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "kernel disposition verification failed" in result.stderr
    assert {path for path in kernel["root"].rglob("*")} == present
    assert before  # the fixture really had content to tamper with


def test_policy_rules_match_public_manifest():
    gate = load_gate()
    manifest = gate.check_policy()
    assert len(manifest["platforms"]["gcp"]["cves"]) == 758
    assert len(manifest["platforms"]["azure"]["cves"]) == 738
    policy = json.loads((ROOT / "cloud/grype.json").read_text())["ignore"]
    node = [rule for rule in policy if rule["package"]["name"] == "^node$"]
    kernel = [rule for rule in policy if rule["package"]["name"] == "^linux-kernel$"]
    canonical = gate.kernel_rules(manifest)
    assert len(node) == 16
    assert sum(rule in canonical for rule in kernel) == 758 + 738
    for rule in canonical:
        assert rule["package"]["name"] == "^linux-kernel$"
        assert rule["package"]["type"] == "linux-kernel"
        assert rule["namespace"] == "nvd:cpe"
        assert rule["package"]["location"].startswith("boot/vmlinuz-")
        assert "not a risk acceptance" in rule["reason"]
        assert "https://ubuntu.com/security/cves/" in rule["reason"]


def test_policy_detects_manifest_drift():
    gate = load_gate()
    manifest = json.loads((ROOT / "cloud/kernel-fixes.json").read_text())
    rules = gate.kernel_rules(manifest)
    dropped = copy.deepcopy(manifest)
    cve = next(iter(dropped["platforms"]["gcp"]["cves"]))
    del dropped["platforms"]["gcp"]["cves"][cve]
    assert gate.kernel_rules(dropped) != rules
    retargeted = copy.deepcopy(manifest)
    retargeted["platforms"]["azure"]["kernel_release"] = "7.0.0-1020-azure"
    assert gate.kernel_rules(retargeted) != rules
    reversioned = copy.deepcopy(manifest)
    first = next(iter(reversioned["platforms"]["azure"]["cves"].values()))
    first["fixed"] = "9.9.9"
    assert gate.kernel_rules(reversioned) != rules


def test_unreleased_azure_advisories_are_not_dispositioned():
    manifest = json.loads((ROOT / "cloud/kernel-fixes.json").read_text())
    for cve in ("CVE-2026-98432", "CVE-2026-100075"):
        assert cve not in manifest["platforms"]["azure"]["cves"]
        assert cve in manifest["platforms"]["gcp"]["cves"]


def test_node_risk_rules_are_preserved():
    policy = json.loads((ROOT / "cloud/grype.json").read_text())["ignore"]
    node = [rule for rule in policy if rule["package"]["name"] == "^node$"]
    assert policy[:16] == node
    assert {rule["package"]["location"] for rule in node} == {
        "opt/actions-runner/externals/node20/bin/node",
        "opt/actions-runner/externals/node20_alpine/bin/node",
    }
    assert all(
        rule["package"]["version"] == "20.20.2"
        and rule["package"]["type"] == "binary"
        and "accepted vulnerability" in rule["reason"]
        for rule in node
    )


def test_verification_flow_wiring():
    verify = (ROOT / "cloud/linux/verify.sh").read_text()
    invocation = verify.index(
        "sudo python3 /tmp/runner-source/verify-kernel.py "
        "/tmp/runner-source/kernel-fixes.json"
    )
    assert invocation < verify.index("syft scan dir:/")
    recipe = (ROOT / "cloud/runner.pkr.hcl").read_text()
    assert 'source      = "cloud/linux/verify-kernel.py"' in recipe
    assert 'source      = "cloud/kernel-fixes.json"' in recipe
    assert "sudo rm -rf /tmp/runner-source" in recipe
    assert "shared_gallery_image_version_exclude_from_latest = true" in recipe
    workflow = (ROOT / ".github/workflows/check.yml").read_text()
    assert "cloud/check-kernel-security.py" in workflow
