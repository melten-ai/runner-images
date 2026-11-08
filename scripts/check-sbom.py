"""Exercise both real guest exclusion policies against Syft's directory resolver."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("syft", nargs="?", default="syft")
args = parser.parse_args()
for recipe, excluded in (
    ("cloud/linux/verify.sh", "proc"),
    ("macos/image/collect-sbom.sh", "System"),
):
    source = Path(recipe).read_text()
    patterns = re.findall(r"--exclude\s+'([^']+)'", source)
    assert patterns, f"{recipe}: exclusion policy is missing"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for folder, name in (("usr", "kept-example"), (excluded, "blocked-example")):
            metadata = (
                root
                / folder
                / "lib/python3.12/site-packages"
                / (name.replace("-", "_") + "-1.0.0.dist-info/METADATA")
            )
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n"
            )
        status = root / "var/lib/dpkg/status"
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(
            "Package: example-runtime\nStatus: install ok installed\n"
            "Architecture: amd64\nVersion: 1.2.3\n"
            "Source: example-source (9.8.7)\nDescription: synthetic package\n\n"
        )
        (root / "requirements-test.txt").write_text("declared-only-example==1.2.3\n")
        (root / "go.mod").write_text(
            "module example.org/test\n\ngo 1.25\n\n"
            "require example.org/declared-only v1.2.3\n"
        )
        command = [args.syft, "scan", f"dir:{root}", "-o", "syft-json"]
        selection = re.search(r"--override-default-catalogers\s+(\S+)", source)
        if selection:
            command += ["--override-default-catalogers", selection.group(1)]
        for pattern in patterns:
            command += ["--exclude", pattern]
        scope = re.search(r"--scope\s+(\S+)", source)
        if scope:
            command += ["--scope", scope.group(1)]
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=120
        )
        sbom = json.loads(result.stdout)
        packages = {package["name"] for package in sbom["artifacts"]}
        distro = next(p for p in sbom["artifacts"] if p["name"] == "example-runtime")
        assert distro["metadata"]["source"] == "example-source"
        assert distro["metadata"]["sourceVersion"] == "9.8.7"
        assert distro["locations"]
        assert "kept-example" in packages, packages
        assert "blocked-example" not in packages, packages
        if recipe.startswith("cloud/"):
            assert "declared-only-example" not in packages, packages
            assert "example.org/declared-only" not in packages, packages
    print(
        f"{recipe}: actual Syft scan retains installed software "
        "and excludes transient data"
    )
