"""Install the newest same-major macOS update in an owned temporary Tart build VM."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.macos_update import UpdateError, update_os  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("vm")
parser.add_argument("bridge")
parser.add_argument("log", type=Path)
args = parser.parse_args()
try:
    print(update_os(args.vm, args.bridge, args.log))
except UpdateError as error:
    sys.exit(f"macOS update failed: {error}")
