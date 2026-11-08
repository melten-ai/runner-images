"""Validate a qualification bundle against its trusted Git checkout and record it."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.qualification import record  # noqa: E402
from runner_images.registry import record_publication  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("bundle", type=Path)
parser.add_argument("repository", type=Path)
parser.add_argument("--require-publication", action="store_true")
args = parser.parse_args()
try:
    revision = subprocess.check_output(
        ["git", "-C", str(args.repository), "rev-parse", "HEAD"], text=True
    ).strip()
    destination = record(args.bundle, args.repository, revision)
    if args.require_publication or (args.bundle / "publication.json").exists():
        record_publication(args.bundle, args.repository, destination)
    print(destination.relative_to(args.repository))
except (OSError, ValueError, subprocess.CalledProcessError):
    print(
        "Qualification record rejected: evidence does not match trusted inputs.",
        file=sys.stderr,
    )
    sys.exit(2)
