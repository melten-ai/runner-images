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
    "CVE-2015-20107",
    "CVE-2018-25032",
    "CVE-2020-10735",
    "CVE-2021-28861",
    "CVE-2022-37454",
    "CVE-2022-45061",
    "CVE-2023-24329",
    "CVE-2023-36632",
    "CVE-2023-6597",
    "CVE-2024-0397",
    "CVE-2024-4032",
    "CVE-2024-6232",
    "CVE-2024-7592",
    "CVE-2024-8088",
    "CVE-2024-9287",
    "CVE-2025-13836",
    "CVE-2025-8194",
    "CVE-2026-115557",
    "CVE-2026-11972",
    "CVE-2026-15308",
    "CVE-2026-19445",
    "CVE-2026-19553",
    "CVE-2026-3644",
    "CVE-2026-4224",
    "CVE-2026-4786",
    "CVE-2026-6100",
    "CVE-2026-7210",
    "CVE-2026-9669",
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
    "CVE-2024-6197",
    "CVE-2025-9086",
    "CVE-2026-10536",
    "CVE-2026-11856",
    "CVE-2026-13608",
    "CVE-2026-18924",
    "CVE-2026-19931",
    "CVE-2026-5773",
    "CVE-2026-6276",
    "CVE-2026-80230",
    "CVE-2026-80231",
    "CVE-2026-82209",
    "CVE-2026-8286",
    "CVE-2026-86409",
    "CVE-2026-86414",
    "CVE-2026-87863",
    "CVE-2026-87864",
    "CVE-2026-8924",
    "CVE-2026-8927",
    "CVE-2026-8932",
    "CVE-2026-92386",
    "CVE-2026-92388",
    "CVE-2026-92389",
    "CVE-2026-92390",
    "CVE-2026-92391",
    "CVE-2026-94266",
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
        "2": {"CVE-2017-5506", "CVE-2014-9826"},
        "3": {"CVE-2025-32460", "CVE-2007-0770"},
        "4": curl_dispositions,
        "5": aws_backports,
        "6": set(),
        "7": {"CVE-2026-59685"},
    }
    legacy_packages = [
        {
            "name": "bundler",
            "version": "1.17.2",
            "type": "gem",
            "foundBy": "ruby-installed-gemspec-cataloger",
            "purl": "pkg:gem/bundler@1.17.2",
            "locations": [
                {
                    "path": (
                        "Library/Ruby/Gems/2.6.0/specifications/default/bundler-"
                        "1.17.2.gemspec"
                    )
                }
            ],
            "accepted_cves": [
                "GHSA-fp4w-jxhp-m23p",
                "GHSA-g98m-96g9-wfjq",
                "GHSA-jvgm-pfqv-887x",
            ],
        },
        {
            "name": "guava",
            "version": "33.4.0-jre",
            "type": "java-archive",
            "foundBy": "java-archive-cataloger",
            "purl": "pkg:maven/com.google.guava/guava@33.4.0-jre",
            "locations": [
                {
                    "path": (
                        "Users/admin/.android/cli/bundles/cd7cde7182f533706c1649"
                        "60fc5d89cd8091be8f/main.jar"
                    )
                }
            ],
            "accepted_cves": ["GHSA-xxph-c9ww-hj94"],
        },
        {
            "name": "guava",
            "version": "33.4.0-jre",
            "type": "java-archive",
            "foundBy": "java-archive-cataloger",
            "purl": "pkg:maven/com.google.guava/guava@33.4.0-jre",
            "locations": [
                {
                    "path": (
                        "Users/admin/android-sdk/cmdline-tools/23.0/lib/external"
                        "/com/google/guava/guava/33.4.0-jre/guava-33.4.0-jre.jar"
                    )
                }
            ],
            "accepted_cves": ["GHSA-xxph-c9ww-hj94"],
        },
        {
            "name": "json",
            "version": "2.1.0",
            "type": "gem",
            "foundBy": "ruby-installed-gemspec-cataloger",
            "purl": "pkg:gem/json@2.1.0",
            "locations": [
                {
                    "path": (
                        "Library/Ruby/Gems/2.6.0/specifications/default/json-2.1"
                        ".0.gemspec"
                    )
                }
            ],
            "accepted_cves": ["GHSA-jphg-qwrw-7w9g"],
        },
        {
            "name": "json",
            "version": "2.18.0",
            "type": "gem",
            "foundBy": "ruby-installed-gemspec-cataloger",
            "purl": "pkg:gem/json@2.18.0",
            "locations": [
                {
                    "path": (
                        "Users/admin/.rbenv/versions/4.0.7/lib/ruby/gems/4.0.0/s"
                        "pecifications/default/json-2.18.0.gemspec"
                    )
                }
            ],
            "accepted_cves": ["GHSA-3m6g-2423-7cp3"],
        },
        {
            "name": "json",
            "version": "2.18.0",
            "type": "gem",
            "foundBy": "ruby-installed-gemspec-cataloger",
            "purl": "pkg:gem/json@2.18.0",
            "locations": [
                {
                    "path": (
                        "opt/homebrew/Library/Homebrew/vendor/portable-ruby/4.0."
                        "9/lib/ruby/gems/4.0.0/specifications/default/json-2.18."
                        "0.gemspec"
                    )
                }
            ],
            "accepted_cves": ["GHSA-3m6g-2423-7cp3"],
        },
        {
            "name": "protobuf-java",
            "version": "2.6.1",
            "type": "java-archive",
            "foundBy": "java-archive-cataloger",
            "purl": "pkg:maven/com.google.protobuf/protobuf-java@2.6.1",
            "locations": [
                {
                    "path": (
                        "Users/admin/android-sdk/cmdline-tools/23.0/lib/external"
                        "/lint-psi/kotlin-compiler/kotlin-compiler-mvn.jar"
                    )
                }
            ],
            "accepted_cves": ["GHSA-735f-pc8j-v9w8", "GHSA-wrvw-hg22-4m67"],
        },
        {
            "name": "setuptools",
            "version": "58.0.4",
            "type": "python",
            "foundBy": "python-installed-package-cataloger",
            "purl": "pkg:pypi/setuptools@58.0.4",
            "locations": [
                {
                    "path": (
                        "Applications/Xcode_27.2.app/Contents/Developer/Library/"
                        "Frameworks/Python3.framework/Versions/3.9/lib/python3.9"
                        "/site-packages/setuptools-58.0.4.dist-info/METADATA"
                    )
                }
            ],
            "accepted_cves": [
                "GHSA-5rjg-fvgr-3xxf",
                "GHSA-cx63-2mw6-8hw5",
                "GHSA-r9hx-vwmv-q579",
            ],
        },
        {
            "name": "setuptools",
            "version": "58.0.4",
            "type": "python",
            "foundBy": "python-installed-package-cataloger",
            "purl": "pkg:pypi/setuptools@58.0.4",
            "locations": [
                {
                    "path": (
                        "Library/Developer/CommandLineTools/Library/Frameworks/P"
                        "ython3.framework/Versions/3.9/lib/python3.9/site-packag"
                        "es/setuptools-58.0.4.dist-info/METADATA"
                    )
                }
            ],
            "accepted_cves": [
                "GHSA-5rjg-fvgr-3xxf",
                "GHSA-cx63-2mw6-8hw5",
                "GHSA-r9hx-vwmv-q579",
            ],
        },
        {
            "name": "setuptools",
            "version": "65.5.0",
            "type": "python",
            "foundBy": "python-installed-package-cataloger",
            "purl": "pkg:pypi/setuptools@65.5.0",
            "locations": [
                {
                    "path": (
                        "Users/admin/android-sdk/ndk/28.2.13676358/toolchains/ll"
                        "vm/prebuilt/darwin-x86_64/python3/lib/python3.11/site-p"
                        "ackages/setuptools-65.5.0.dist-info/METADATA"
                    )
                }
            ],
            "accepted_cves": [
                "GHSA-5rjg-fvgr-3xxf",
                "GHSA-cx63-2mw6-8hw5",
                "GHSA-r9hx-vwmv-q579",
            ],
        },
        {
            "name": "stringio",
            "version": "0.0.2",
            "type": "gem",
            "foundBy": "ruby-installed-gemspec-cataloger",
            "purl": "pkg:gem/stringio@0.0.2",
            "locations": [
                {
                    "path": (
                        "Library/Ruby/Gems/2.6.0/specifications/default/stringio"
                        "-0.0.2.gemspec"
                    )
                }
            ],
            "accepted_cves": ["GHSA-v5h6-c2hv-hv3r"],
        },
        {
            "name": "wheel",
            "version": "0.37.0",
            "type": "python",
            "foundBy": "python-installed-package-cataloger",
            "purl": "pkg:pypi/wheel@0.37.0",
            "locations": [
                {
                    "path": (
                        "Applications/Xcode_27.2.app/Contents/Developer/Library/"
                        "Frameworks/Python3.framework/Versions/3.9/lib/python3.9"
                        "/site-packages/wheel-0.37.0.dist-info/METADATA"
                    )
                }
            ],
            "accepted_cves": ["GHSA-qwmp-2cf2-g9g6"],
        },
        {
            "name": "wheel",
            "version": "0.37.0",
            "type": "python",
            "foundBy": "python-installed-package-cataloger",
            "purl": "pkg:pypi/wheel@0.37.0",
            "locations": [
                {
                    "path": (
                        "Library/Developer/CommandLineTools/Library/Frameworks/P"
                        "ython3.framework/Versions/3.9/lib/python3.9/site-packag"
                        "es/wheel-0.37.0.dist-info/METADATA"
                    )
                }
            ],
            "accepted_cves": ["GHSA-qwmp-2cf2-g9g6"],
        },
    ]
    legacy_packages += [
        {
            "name": "future",
            "version": "0.18.2",
            "type": "python",
            "foundBy": "python-installed-package-cataloger",
            "purl": "pkg:pypi/future@0.18.2",
            "locations": [
                {
                    "path": (
                        "Applications/Xcode_27.2.app/Contents/Developer/Library/"
                        "Frameworks/Python3.framework/Versions/3.9/lib/python3.9"
                        "/site-packages/future-0.18.2.dist-info/METADATA"
                    )
                }
            ],
            "accepted_cves": ["GHSA-v3c5-jqr6-7qm8"],
        },
        {
            "name": "future",
            "version": "0.18.2",
            "type": "python",
            "foundBy": "python-installed-package-cataloger",
            "purl": "pkg:pypi/future@0.18.2",
            "locations": [
                {
                    "path": (
                        "Library/Developer/CommandLineTools/Library/Frameworks/P"
                        "ython3.framework/Versions/3.9/lib/python3.9/site-packag"
                        "es/future-0.18.2.dist-info/METADATA"
                    )
                }
            ],
            "accepted_cves": ["GHSA-v3c5-jqr6-7qm8"],
        },
        {
            "name": "jline-builtins",
            "version": "3.24.1",
            "type": "java-archive",
            "foundBy": "java-archive-cataloger",
            "purl": "pkg:maven/org.jline/jline-builtins@3.24.1",
            "locations": [
                {
                    "path": (
                        "Users/admin/android-sdk/cmdline-tools/23.0/lib/external"
                        "/lint-psi/kotlin-compiler/kotlin-compiler-mvn.jar"
                    )
                }
            ],
            "accepted_cves": ["GHSA-r2xf-8xr9-62gw"],
        },
        {
            "name": "jline-remote-telnet",
            "version": "3.24.1",
            "type": "java-archive",
            "foundBy": "java-archive-cataloger",
            "purl": "pkg:maven/org.jline/jline-remote-telnet@3.24.1",
            "locations": [
                {
                    "path": (
                        "Users/admin/android-sdk/cmdline-tools/23.0/lib/external"
                        "/lint-psi/kotlin-compiler/kotlin-compiler-mvn.jar"
                    )
                }
            ],
            "accepted_cves": ["GHSA-2r2c-cx56-8933", "GHSA-47qp-hqvx-6r3f"],
        },
    ]
    legacy_packages += [
        {
            "name": "guava",
            "version": "33.6.0-jre",
            "type": "java-archive",
            "foundBy": "java-archive-cataloger",
            "purl": "pkg:maven/com.google.guava/guava@33.6.0-jre",
            "locations": [
                {
                    "path": (
                        "opt/homebrew/Cellar/kotlin/2.4.21/libexec/lib/kotlin-ma"
                        "in-kts.jar"
                    )
                }
            ],
            "accepted_cves": ["GHSA-xxph-c9ww-hj94"],
        }
    ]
    legacy_ids = set()
    for package in legacy_packages:
        package["id"] = str(len(artifacts))
        expected_dispositions[package["id"]] = set(package.pop("accepted_cves"))
        legacy_ids.add(package["id"])
        artifacts.append(package)
    required_matches = {
        **expected_dispositions,
        "6": openldap_server_dispositions,
        "7": httpd_module_dispositions,
    }
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
            if (original["id"] in legacy_ids and field == "locations") or (
                original["id"] not in legacy_ids
                and (original["type"] != "python" or field not in ("name", "type"))
            ):
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
