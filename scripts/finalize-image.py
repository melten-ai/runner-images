"""Issue the verified receipt after local image publication and an identity check."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.qualification import finalize  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("bundle", type=Path)
parser.add_argument("image_directory", type=Path)
args = parser.parse_args()
try:
    finalize(args.bundle, args.image_directory)
except (OSError, ValueError):
    print("Image finalization failed; no verified receipt was issued.", file=sys.stderr)
    sys.exit(2)
