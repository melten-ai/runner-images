"""Collect the current runner. Invoke from the repository root."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner_images.inventory import collect  # noqa: E402

try:
    print(json.dumps(collect(), indent=2))
except OSError:
    print("Inventory collection failed; check filesystem permissions.", file=sys.stderr)
    sys.exit(2)
