"""Exercise the real scanner's exact Mac platform-disposition boundaries."""

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

paths = [
    "Applications/Xcode_27.2.app/Contents/Developer/Library/Frameworks/"
    "Python3.framework/Versions/3.9/Resources/Python.app/Contents/Info.plist",
    "Library/Developer/CommandLineTools/Library/Frameworks/"
    "Python3.framework/Versions/3.9/Resources/Python.app/Contents/Info.plist",
]
platform_only = {
    "CVE-2022-26488",
    "CVE-2022-42919",
    "CVE-2026-3087",
    "CVE-2026-3298",
    "CVE-2024-4030",
}
root = Path(__file__).resolve().parents[1]
scanner = sys.argv[1] if len(sys.argv) == 2 else "grype"


def identity(match):
    return (
        match["vulnerability"]["id"],
        match["vulnerability"]["namespace"],
        match["artifact"]["id"],
    )


with tempfile.TemporaryDirectory() as d:
    work = Path(d)
    empty = work / "unmodified-grype.json"
    empty.write_text("{}")
    artifacts = [
        {
            "id": str(index),
            "name": "Python",
            "version": "3.9.6",
            "type": "apple-app-bundle",
            "foundBy": "apple-app-bundle-cataloger",
            "locations": [{"path": path}],
            "cpes": [{"cpe": "cpe:2.3:a:python:python:3.9.6:*:*:*:*:*:*:*"}],
        }
        for index, path in enumerate(paths)
    ]
    # Each adjacent case must still be reported by the real scanner. Keeping
    # the CPE constant isolates rule scope from version-range feed changes.
    for field, value in (
        ("name", "OtherPython"),
        ("version", "3.9.7"),
        ("type", "binary"),
        ("locations", [{"path": "Applications/Other.app/Contents/Info.plist"}]),
    ):
        nearby = copy.deepcopy(artifacts[0])
        nearby.update({"id": str(len(artifacts)), field: value})
        artifacts.append(nearby)
    sbom = work / "sbom.syft.json"
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
    for config in (empty, root / "macos/grype.json"):
        output = work / f"scan-{len(reports)}.json"
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
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        reports.append(json.loads(output.read_text()))
    before, after = reports
    expected = {
        identity(m)
        for m in before["matches"]
        if m["vulnerability"]["id"] in platform_only
        and m["artifact"]["id"] in ("0", "1")
    }
    ignored = {identity(m) for m in after["ignoredMatches"]}
    assert expected and ignored == expected, (expected, ignored)
    assert {identity(m) for m in after["matches"]} == {
        identity(m) for m in before["matches"]
    } - expected
    assert all(
        rule["reason"] and "https://" in rule["reason"]
        for m in after["ignoredMatches"]
        for rule in m["appliedIgnoreRules"]
    )
    print(
        f"Verified {len(ignored)} platform-only dispositions; nearby name, version, "
        "type and location matches remain blocking."
    )
