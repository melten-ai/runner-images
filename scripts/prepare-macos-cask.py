"""Prefetch the public Azure CLI cask through the authenticated host asset API."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.macos_host import prepare_azure_cli  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("vm")
args = parser.parse_args()
prepare_azure_cli(args.vm)
