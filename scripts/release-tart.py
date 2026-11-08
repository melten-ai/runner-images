"""Trusted hosted release entry point; local builds submit qualification evidence."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.release import prepare, publish_release, write_outputs  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
commands = parser.add_subparsers(dest="command", required=True)
prepare_command = commands.add_parser("prepare")
prepare_command.add_argument("qualification")
commands.add_parser("publish")
args = parser.parse_args()
if args.command == "prepare":
    write_outputs(prepare(Path.cwd(), args.qualification))
else:
    publish_release(Path.cwd())
