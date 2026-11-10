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
httpd_module_dispositions = {
    "CVE-2025-3891",
    "CVE-2026-29167",
    "CVE-2026-34355",
    "CVE-2026-34356",
    "CVE-2026-42535",
    "CVE-2026-42536",
    "CVE-2026-44185",
    "CVE-2026-44186",
    "CVE-2026-46729",
    "CVE-2026-47360",
    "CVE-2026-48005",
    "CVE-2026-48913",
    "CVE-2026-56153",
    "CVE-2026-56154",
    "CVE-2026-56449",
    "CVE-2026-57941",
    "CVE-2026-59685",
    "CVE-2026-59797",
    "CVE-2026-63045",
    "CVE-2026-63292",
    "CVE-2026-63686",
    "CVE-2026-63718",
    "CVE-2026-73636",
    "CVE-2026-73637",
    "CVE-2026-93546",
}
openldap_server_dispositions = {
    "CVE-2012-1164",
    "CVE-2013-4449",
    "CVE-2014-9713",
    "CVE-2015-1545",
    "CVE-2017-14159",
    "CVE-2017-17740",
    "CVE-2017-9287",
    "CVE-2019-13057",
    "CVE-2019-13565",
    "CVE-2020-12243",
    "CVE-2020-25692",
    "CVE-2020-25709",
    "CVE-2020-25710",
    "CVE-2020-36221",
    "CVE-2020-36222",
    "CVE-2020-36223",
    "CVE-2020-36224",
    "CVE-2020-36225",
    "CVE-2020-36226",
    "CVE-2020-36227",
    "CVE-2020-36228",
    "CVE-2020-36229",
    "CVE-2020-36230",
    "CVE-2021-27212",
    "CVE-2022-29155",
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
aws_backports = {
    "GHSA-mf9v-mfxr-j63j",
    "GHSA-qccp-gfcp-xxvc",
    "GHSA-vxq7-64xx-v4gw",
    "GHSA-8988-9cw3-xx77",
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
    artifacts.append(
        {
            "id": "6",
            "name": "openldap",
            "version": "2.4.28",
            "type": "binary",
            "foundBy": "binary-classifier-cataloger",
            "locations": [{"path": "usr/bin/ldapsearch"}],
            "cpes": [{"cpe": "cpe:2.3:a:openldap:openldap:2.4.28:*:*:*:*:*:*:*"}],
            "purl": "pkg:generic/openldap@2.4.28",
            "metadataType": "binary-signature",
            "metadata": {"matches": [{"classifier": "openldap-search-binary"}]},
        }
    )
    artifacts.append(
        {
            "id": "7",
            "name": "httpd",
            "version": "2.4.67",
            "type": "binary",
            "foundBy": "httpd-binary-cataloger",
            "locations": [{"path": "usr/sbin/httpd"}],
            "cpes": [{"cpe": "cpe:2.3:a:apache:http_server:2.4.67:*:*:*:*:*:*:*"}],
            "purl": "pkg:generic/httpd@2.4.67",
        }
    )
    artifacts.append(
        {
            "id": "5",
            "name": "urllib3",
            "version": "2.6.3+melten.9ff67c841cad",
            "type": "python",
            "foundBy": "python-installed-package-cataloger",
            "locations": [
                {
                    "path": "opt/homebrew/Cellar/awscli/2.39.6/libexec/lib/"
                    "python3.15/site-packages/urllib3-2.6.3+melten."
                    "9ff67c841cad.dist-info/METADATA"
                }
            ],
            "purl": "pkg:pypi/urllib3@2.6.3%2Bmelten.9ff67c841cad",
        }
    )
    expected_dispositions = {
        **{str(index): python_dispositions for index in (0, 1)},
        "2": {"CVE-2017-5506"},
        "3": {"CVE-2025-32460"},
        "4": curl_dispositions,
        "5": aws_backports,
        "6": openldap_server_dispositions,
        "7": httpd_module_dispositions,
    }
    required_matches = dict(expected_dispositions)
    # Each adjacent case must still be reported by the real scanner. Keeping
    # the CPE constant isolates rule scope from version-range feed changes.
    for original in tuple(artifacts):
        for field, value in (
            ("name", "OtherPackage"),
            ("version", "2.6.3" if original["type"] == "python" else "0.0.1"),
            ("type", "binary" if original["type"] != "binary" else "homebrew"),
            ("locations", [{"path": "Applications/Other.app/Contents/Info.plist"}]),
        ):
            nearby = copy.deepcopy(original)
            nearby.update({"id": str(len(artifacts)), field: value})
            # Changing a Python package name/type can remove its language-feed
            # lookup entirely. Compare every observed finding in both scans;
            # require baseline hits where that lookup remains defined.
            if original["type"] != "python" or field not in ("name", "type"):
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
        f"Verified {len(ignored)} scoped dispositions; all other observed findings "
        "remain unchanged and High/Critical still block."
    )
