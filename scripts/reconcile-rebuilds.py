"""Run only within the serialized, trusted rebuild-issues workflow."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.rebuild import reconcile  # noqa: E402

reconcile(Path.cwd())
