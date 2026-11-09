"""Resolve the current stable macOS Android CLI and its official checksum."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.macos_host import resolve_android_tools  # noqa: E402

print(json.dumps(resolve_android_tools()))
