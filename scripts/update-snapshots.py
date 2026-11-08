"""Write validated observations; unchanged observations do not rewrite files."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner_images.snapshots import update  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
try:
    for name in update(args.source, args.destination):
        print(name)
except (OSError, ValueError):
    print(
        "Snapshot update failed: missing or invalid hosted observations.",
        file=sys.stderr,
    )
    sys.exit(2)
