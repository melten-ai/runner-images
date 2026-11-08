"""Build, publish and await release completion using the existing gh login."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.local_build import rebuild  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("profile", choices=["xcode", "xcode-android"])
args = parser.parse_args()
rebuild(Path.cwd(), args.profile)
