"""Compare collected snapshots; exit 1 for a difference, 2 for invalid input."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner_images.compare import compare, load  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("reference", type=Path)
parser.add_argument("candidate", type=Path)
parser.add_argument("--allowlist", type=Path)
args = parser.parse_args()
try:
    result = compare(load(args.reference), load(args.candidate), args.allowlist)
except (OSError, ValueError):
    print(
        "Comparison failed: invalid, unreadable, or incompatible input.",
        file=sys.stderr,
    )
    sys.exit(2)
print(json.dumps(result, indent=2))
sys.exit(0 if result["compatible"] else 1)
