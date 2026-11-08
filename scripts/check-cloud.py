"""Validate Packer schemas with synthetic configuration and no cloud credentials."""

import argparse
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("packer", nargs="?", default="packer")
args = parser.parse_args()
for command in (
    [args.packer, "fmt", "-check", "cloud"],
    [args.packer, "init", "cloud"],
    *(
        [
            args.packer,
            "validate",
            f"-only={builder}",
            "-var-file=cloud/validation.pkrvars.json",
            "cloud",
        ]
        for builder in ("googlecompute.ubuntu", "azure-arm.ubuntu")
    ),
):
    subprocess.run(command, check=True)
