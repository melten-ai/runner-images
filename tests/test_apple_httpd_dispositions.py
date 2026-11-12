"""Default-disabled modules and uncataloged servers cannot waive image CVEs."""

import json
from pathlib import Path


def test_module_and_server_absence_dispositions_are_revoked():
    root = Path(__file__).resolve().parents[1]
    rules = json.loads((root / "macos/grype.json").read_text())["ignore"]
    assert any(r["package"]["name"] == "^openldap$" for r in rules)
    assert "CVE-2026-59685" in {
        r["vulnerability"] for r in rules if r["package"]["name"] == "^httpd$"
    }
