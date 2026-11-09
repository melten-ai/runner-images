"""Exercise the real scanner's exact Mac vulnerability-disposition boundaries."""

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
python_dispositions = {
    "CVE-2022-26488",
    "CVE-2022-42919",
    "CVE-2026-3087",
    "CVE-2026-3298",
    "CVE-2024-4030",
    "CVE-2025-4517",
    "CVE-2025-4138",
    "CVE-2025-4330",
    "CVE-2025-4435",
    "CVE-2026-11940",
    "CVE-2026-82049",
    "CVE-2019-12900",
}
curl_dispositions = {
    "CVE-2026-12064",
    "CVE-2026-86410",
    "CVE-2026-9547",
    "CVE-2026-87865",
    "CVE-2026-87866",
    "CVE-2025-0725",
    "CVE-2026-107928",
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
    artifacts += [
        {
            "id": "2",
            "name": "imagemagick",
            "version": "7.1.2-35",
            "type": "homebrew",
            "foundBy": "homebrew-cataloger",
            "locations": [
                {
                    "path": "opt/homebrew/Cellar/imagemagick/7.1.2-35/"
                    ".brew/imagemagick.rb"
                }
            ],
            "cpes": [
                {"cpe": "cpe:2.3:a:imagemagick:imagemagick:7.1.2-35:*:*:*:*:*:*:*"}
            ],
        },
        {
            "id": "3",
            "name": "graphicsmagick",
            "version": "1.3.49",
            "type": "homebrew",
            "foundBy": "homebrew-cataloger",
            "locations": [
                {
                    "path": "opt/homebrew/Cellar/graphicsmagick/1.3.49/"
                    ".brew/graphicsmagick.rb"
                }
            ],
            "cpes": [
                {"cpe": "cpe:2.3:a:graphicsmagick:graphicsmagick:1.3.49:*:*:*:*:*:*:*"}
            ],
        },
    ]
    artifacts.append(
        {
            "id": "4",
            "name": "curl",
            "version": "8.7.1",
            "type": "binary",
            "foundBy": "binary-classifier-cataloger",
            "locations": [{"path": "usr/bin/curl"}],
            "cpes": [{"cpe": "cpe:2.3:a:haxx:curl:8.7.1:*:*:*:*:*:*:*"}],
        }
    )
    expected_dispositions = {
        **{str(index): python_dispositions for index in (0, 1)},
        "2": {"CVE-2017-5506"},
        "3": {"CVE-2025-32460"},
        "4": curl_dispositions,
    }
    required_matches = dict(expected_dispositions)
    # Each adjacent case must still be reported by the real scanner. Keeping
    # the CPE constant isolates rule scope from version-range feed changes.
    for original in tuple(artifacts):
        for field, value in (
            ("name", "OtherPackage"),
            ("version", "0.0.1"),
            ("type", "binary" if original["type"] != "binary" else "homebrew"),
            ("locations", [{"path": "Applications/Other.app/Contents/Info.plist"}]),
        ):
            nearby = copy.deepcopy(original)
            nearby.update({"id": str(len(artifacts)), field: value})
            required_matches[nearby["id"]] = expected_dispositions[original["id"]]
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
        if m["vulnerability"]["id"]
        in expected_dispositions.get(m["artifact"]["id"], set())
    }
    assert all(
        {
            m["vulnerability"]["id"]
            for m in before["matches"]
            if m["artifact"]["id"] == key
        }
        >= vulnerabilities
        for key, vulnerabilities in required_matches.items()
    ), "The unmodified scanner must report every disposition and adjacent fixture"
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
        f"Verified {len(ignored)} scoped dispositions; nearby name, version, "
        "type and location matches remain blocking."
    )
