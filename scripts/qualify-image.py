"""Write a receipt only when the built image passes its reviewed comparison."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.qualification import qualify  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("bundle", type=Path)
parser.add_argument("image_directory", type=Path)
parser.add_argument("--image", required=True)
parser.add_argument("--profile", choices=["xcode", "xcode-android"], required=True)
parser.add_argument("--base", required=True)
parser.add_argument("--source-commit", required=True)
parser.add_argument("--reference-commit", required=True)
parser.add_argument("--runner-version", required=True)
parser.add_argument("--runner-dir", default="/Users/admin/actions-runner")
parser.add_argument("--run-url", default="")
args = parser.parse_args()
try:
    qualify(
        args.bundle,
        args.image_directory,
        {
            "image": args.image,
            "profile": args.profile,
            "base": args.base,
            "source_commit": args.source_commit,
            "reference_commit": args.reference_commit,
            "runner_version": args.runner_version,
            "runner_dir": args.runner_dir,
            "run_url": args.run_url,
        },
    )
except (OSError, ValueError):
    print(
        "Image qualification failed; no verified receipt was issued.", file=sys.stderr
    )
    sys.exit(1)
