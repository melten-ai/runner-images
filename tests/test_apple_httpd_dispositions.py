"""Default-disabled modules and uncataloged servers cannot waive image CVEs."""

import json
from pathlib import Path


def test_module_and_server_absence_dispositions_are_revoked():
    root = Path(__file__).resolve().parents[1]
    rules = json.loads((root / "macos/grype.json").read_text())["ignore"]
    scoped = {
        "^openldap$": ("2.4.28", "binary", "usr/bin/ldapsearch"),
        "^httpd$": ("2.4.67", "binary", "usr/sbin/httpd"),
        "^sqlite$": (
            "3.54.1",
            "homebrew",
            "opt/homebrew/Cellar/sqlite/3.54.1/.brew/sqlite.rb",
        ),
        "^activesupport$": (
            "7.2.4",
            "gem",
            "opt/homebrew/lib/ruby/gems/3.4.0/specifications/activesupport-7.2.4.gemspec",
        ),
        "^Microsoft\\.AspNetCore\\.WebUtilities$": (
            "8.0.25",
            "dotnet",
            "Users/admin/actions-runner/bin/Runner.Worker.deps.json",
        ),
    }
    for name, (version, package_type, location) in scoped.items():
        selected = [r for r in rules if r["package"]["name"] == name]
        assert selected
        observed = {
            (r["package"]["version"], r["package"]["type"], r["package"]["location"])
            for r in selected
        }
        assert observed == {(version, package_type, location)} or (
            name == "^activesupport$"
            and observed
            == {
                (version, package_type, location),
                (
                    version,
                    package_type,
                    "Users/admin/.rbenv/versions/4.0.7/lib/ruby/gems/4.0.0/specifications/activesupport-7.2.4.gemspec",
                ),
            }
        )
        assert all(r["reason"] and "https://" in r["reason"] for r in selected)
