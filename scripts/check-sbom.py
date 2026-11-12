"""Exercise both real guest exclusion policies against Syft's directory resolver."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.qualification import verify_sbom_packages  # noqa: E402

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
        if recipe.startswith("macos/"):
            for name, folder in (
                (
                    "installed-brew-example",
                    "opt/homebrew/Cellar/installed-brew-example/1.2.3/.brew",
                ),
                (
                    "source-only-brew-example",
                    "opt/homebrew/Library/Taps/example/homebrew-tools/Formula",
                ),
            ):
                definition = root / folder / (name + ".rb")
                definition.parent.mkdir(parents=True)
                definition.write_text(
                    "class Example < Formula\n"
                    '  url "https://example.invalid/example-1.2.3.tar.gz"\n'
                    '  version "1.2.3"\nend\n'
                )
        native_path = root / "sbom.syft.json"
        spdx_path = root / "sbom.spdx.json"
        command = [
            args.syft,
            "scan",
            f"dir:{root}",
            "-o",
            f"syft-json={native_path}",
            "-o",
            f"spdx-json={spdx_path}",
        ]
        selection = re.search(r"--override-default-catalogers\s+(\S+)", source)
        if selection:
            command += ["--override-default-catalogers", selection.group(1)]
        for pattern in patterns:
            command += ["--exclude", pattern]
        scope = re.search(r"--scope\s+(\S+)", source)
        if scope:
            command += ["--scope", scope.group(1)]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        sbom = json.loads(native_path.read_text())
        verify_sbom_packages(sbom, json.loads(spdx_path.read_text()))
        packages = {package["name"] for package in sbom["artifacts"]}
        distro = next(p for p in sbom["artifacts"] if p["name"] == "example-runtime")
        assert distro["metadata"]["source"] == "example-source"
        assert distro["metadata"]["sourceVersion"] == "9.8.7"
        assert distro["locations"]
        assert "kept-example" in packages, packages
        if recipe.startswith("macos/"):
            assert "installed-brew-example" in packages, packages
            assert "source-only-brew-example" not in packages, packages
        assert "blocked-example" not in packages, packages
        assert "declared-only-example" not in packages, packages
        assert "example.org/declared-only" not in packages, packages
    print(
        f"{recipe}: actual Syft scan retains installed software "
        "and excludes transient data"
    )
