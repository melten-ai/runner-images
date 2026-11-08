"""Hash stopped Tart image files without copying a large VM archive."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.qualification import fingerprint  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
args = parser.parse_args()
try:
    print(json.dumps(fingerprint(args.directory), indent=2))
except (OSError, ValueError):
    print("Image fingerprint failed: stopped VM files are incomplete.", file=sys.stderr)
    sys.exit(2)
