"""Repository behavioral gate: uv run python scripts/test.py."""

import subprocess
import sys

subprocess.run([sys.executable, "scripts/check.py"], check=True)
sys.exit(
    subprocess.run(
        [sys.executable, "-m", "pytest", *sys.argv[1:]], check=False
    ).returncode
)
