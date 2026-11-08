"""Build/release cloud base images; PRs can validate recipes without cloud access."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images import cloud  # noqa: E402
from runner_images.qualification import digest  # noqa: E402
from runner_images.release import write_outputs  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("command", choices=["plan", "build", "publish", "cleanup"])
parser.add_argument("provider", choices=["gcp", "azure"])
args = parser.parse_args()
root = Path.cwd()
if args.command == "plan":
    write_outputs(cloud.plan(root, args.provider))
elif args.command == "build":
    cloud.build(root, args.provider)
    write_outputs(
        {
            "digest": "sha256:"
            + digest(cloud.state_path(root, args.provider).parent / "image.json")
        }
    )
elif args.command == "publish":
    cloud.publish(root, args.provider)
else:
    cloud.cleanup(root, args.provider)
