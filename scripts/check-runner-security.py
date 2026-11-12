"""Exercise accepted runner Node risk against the actual scanner, fail closed."""

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
scanner = sys.argv[1] if len(sys.argv) == 2 else "grype"
expected_cves = {
    "CVE-2026-111497",
    "CVE-2026-56848",
    "CVE-2026-48937",
    "CVE-2026-56846",
    "CVE-2026-105482",
    "CVE-2026-106069",
    "CVE-2026-48617",
    "CVE-2026-58043",
}
paths = {
    "macos": ["Users/admin/actions-runner/externals/node20/bin/node"],
    "cloud": [
        "opt/actions-runner/externals/node20/bin/node",
        "opt/actions-runner/externals/node20_alpine/bin/node",
    ],
}


def identity(match):
    return match["artifact"]["id"], match["vulnerability"]["id"]


with tempfile.TemporaryDirectory() as directory:
    work = Path(directory)
    empty = work / "empty.json"
    empty.write_text("{}")
    for profile, locations in paths.items():
        policy = root / profile / "grype.json"
        rules = [
            rule
            for rule in json.loads(policy.read_text())["ignore"]
            if rule["package"]["name"] == "^node$"
        ]
        assert len(rules) == len(locations) * len(expected_cves)
        assert {
            (rule["vulnerability"], rule["package"]["location"]) for rule in rules
        } == {(cve, path) for cve in expected_cves for path in locations}
        assert all(
            rule["namespace"] == "nvd:cpe"
            and rule["package"]["version"] == "20.20.2"
            and rule["package"]["type"] == "binary"
            and "accepted vulnerability" in rule["reason"]
            for rule in rules
        )
        artifacts = []
        approved = set()
        for path in locations:
            artifact = {
                "id": str(len(artifacts)),
                "name": "node",
                "version": "20.20.2",
                "type": "binary",
                "foundBy": "binary-classifier-cataloger",
                "locations": [{"path": path}],
                "cpes": [{"cpe": "cpe:2.3:a:nodejs:node.js:20.20.2:*:*:*:*:*:*:*"}],
            }
            approved.add(artifact["id"])
            artifacts.append(artifact)
            for field, value in (
                ("name", "other-node"),
                ("version", "20.20.1"),
                ("type", "other-binary"),
                ("locations", [{"path": path.replace("node20", "node24")}]),
                ("locations", [{"path": "usr/local/bin/node"}]),
            ):
                nearby = copy.deepcopy(artifact)
                nearby["id"] = str(len(artifacts))
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
        for config in (empty, policy):
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
                timeout=180,
            )
            assert result.returncode == 2, result.stdout + result.stderr
            reports.append(json.loads(output.read_text()))
        before, after = reports
        baseline = {identity(m) for m in before["matches"]}
        expected = {(key, cve) for key in approved for cve in expected_cves}
        assert expected <= baseline, "Baseline must contain every accepted finding"
        assert {identity(m) for m in after["ignoredMatches"]} == expected
        assert {identity(m) for m in after["matches"]} == baseline - expected
        assert all(
            any(m["artifact"]["id"] == a["id"] for m in after["matches"])
            for a in artifacts
            if a["id"] not in approved
        ), "Adjacent identities must still block publication"
        print(f"{profile}: {len(expected)} accepted Node matches; other findings block")
