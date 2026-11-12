"""Exercise supplier-fixed kernel dispositions against the actual scanner.

The public data manifest `cloud/kernel-fixes.json` is the single source of the
kernel ignore rules in `cloud/grype.json`; this check fails when they drift.
The actual scanner must first report every dispositioned advisory for the
exact bound kernel artifacts, then move only those identities to
`ignoredMatches`. Adjacent names, versions, package types and locations —
including a newer unbound kernel ABI — must retain their original findings,
and remaining High/Critical findings must still return exit 2.
"""

import copy
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def kernel_rules(manifest):
    rules = []
    for key in ("gcp", "azure"):
        platform = manifest["platforms"][key]
        release = platform["kernel_release"]
        location = platform["boot_image"]["path"]
        installed = platform["image_package"]["version"]
        for cve in sorted(platform["cves"]):
            entry = platform["cves"][cve]
            rules.append(
                {
                    "vulnerability": cve,
                    "namespace": "nvd:cpe",
                    "reason": (
                        "Supplier-fixed backport, not a risk acceptance: Canonical "
                        f"released {platform['tracker_source_package']} "
                        f"{entry['fixed']} ({entry['usn']}) for Ubuntu "
                        f"{platform['ubuntu_series']}, fixing {cve} at or before the "
                        f"installed {installed}. Source: "
                        f"https://ubuntu.com/security/cves/{cve}.json (tracker JSON "
                        f"SHA-256 {entry['tracker_sha256']}; HTTPS-served, not "
                        "cryptographic-signature verified). Scope is bound to the "
                        f"exact {release} artifacts by the native predicates in "
                        "cloud/kernel-fixes.json, enforced before SBOM collection "
                        "by cloud/linux/verify-kernel.py: exact "
                        f"/{location} SHA-256 and dpkg ownership, exact "
                        f"{platform['image_package']['name']} and "
                        f"{platform['modules_package']['name']} source/version "
                        "identities, the modules md5sums digest, clean dpkg "
                        "--verify, and native dpkg --compare-versions installed "
                        ">= fixed."
                    ),
                    "package": {
                        "name": "^linux-kernel$",
                        "version": release,
                        "type": "linux-kernel",
                        "location": location,
                    },
                }
            )
    return rules


def check_policy():
    manifest = json.loads((root / "cloud/kernel-fixes.json").read_text())
    policy = json.loads((root / "cloud/grype.json").read_text())["ignore"]
    node = [rule for rule in policy if rule["package"]["name"] == "^node$"]
    kernel = [rule for rule in policy if rule["package"]["name"] == "^linux-kernel$"]
    assert policy == node + kernel, "unexpected scanner policy rule scope or order"
    assert kernel == kernel_rules(manifest), (
        "cloud/grype.json kernel rules do not match cloud/kernel-fixes.json"
    )
    for cve in ("CVE-2026-98432", "CVE-2026-100075"):
        assert cve not in manifest["platforms"]["azure"]["cves"], (
            f"{cve} is not released for the Azure kernel and must stay blocking"
        )
    return manifest


def identity(match):
    return match["artifact"]["id"], match["vulnerability"]["id"]


def main(scanner, directory):
    manifest = check_policy()
    work = Path(directory)
    empty = work / "empty.json"
    empty.write_text("{}")
    artifacts = []
    approved = {}
    for key in ("gcp", "azure"):
        platform = manifest["platforms"][key]
        release = platform["kernel_release"]
        artifact = {
            "id": f"{key}-bound",
            "name": "linux-kernel",
            "version": release,
            "type": "linux-kernel",
            "foundBy": "linux-kernel-cataloger",
            "locations": [{"path": platform["boot_image"]["path"]}],
            "cpes": [{"cpe": f"cpe:2.3:o:linux:linux_kernel:{release}:*:*:*:*:*:*:*"}],
        }
        approved[key] = artifact["id"]
        artifacts.append(artifact)
        unbound = release.replace("-1017-", "-1019-")
        for suffix, field, value in (
            ("unbound", None, unbound),
            ("location", "locations", [{"path": f"usr/lib/debug/{release}"}]),
            ("name", "name", "linux"),
            ("type", "type", "binary"),
        ):
            nearby = copy.deepcopy(artifact)
            nearby["id"] = f"{key}-{suffix}"
            if field is None:
                nearby["version"] = value
                nearby["locations"] = [{"path": f"boot/vmlinuz-{value}"}]
                nearby["cpes"] = [
                    {"cpe": f"cpe:2.3:o:linux:linux_kernel:{value}:*:*:*:*:*:*:*"}
                ]
            else:
                nearby[field] = value
            artifacts.append(nearby)
    sbom = work / "sbom.json"
    sbom.write_text(
        json.dumps(
            {
                "artifacts": artifacts,
                "source": {
                    "id": "example-root",
                    "name": "example-root",
                    "type": "directory",
                    "metadata": {"path": "/example"},
                },
                "descriptor": {"name": "syft", "version": "1.57.1"},
                "schema": {
                    "version": "16.1.13",
                    "url": "https://raw.githubusercontent.com/anchore/syft/main/"
                    "schema/json/schema-16.1.13.json",
                },
            }
        )
    )
    reports = []
    for config in (empty, root / "cloud/grype.json"):
        output = work / f"report-{len(reports)}.json"
        result = subprocess.run(
            [
                scanner,
                f"sbom:{sbom}",
                "--config",
                str(config),
                "--fail-on",
                "high",
                "-o",
                "json",
                "--file",
                str(output),
            ],
            text=True,
            capture_output=True,
            timeout=900,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        reports.append(json.loads(output.read_text()))
    before, after = reports
    baseline = {identity(match) for match in before["matches"]}
    expected = {
        (approved[key], cve)
        for key in approved
        for cve in manifest["platforms"][key]["cves"]
    }
    assert expected <= baseline, "Baseline must contain every dispositioned finding"
    assert {identity(match) for match in after["ignoredMatches"]} == expected
    assert {identity(match) for match in after["matches"]} == baseline - expected
    for cve in ("CVE-2026-98432", "CVE-2026-100075"):
        assert (approved["azure"], cve) in {
            identity(match) for match in after["matches"]
        }, f"unreleased Azure {cve} must remain a blocking finding"
    assert all(
        any(match["artifact"]["id"] == artifact["id"] for match in after["matches"])
        for artifact in artifacts
        if artifact["id"] not in approved.values()
    ), "Adjacent and unbound kernel identities must still block publication"
    for key in approved:
        count = len(manifest["platforms"][key]["cves"])
        print(f"{key}: {count} supplier-fixed kernel findings move; others block")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        main(sys.argv[1] if len(sys.argv) == 2 else "grype", temporary)
