"""One local invocation builds/uploads; the hosted release workflow finishes it."""

import fcntl
import json
import os
import platform
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import cast

from runner_images.qualification import digest, read_object
from runner_images.rebuild import input_id


def invoke(
    command: list[str], root: Path, env: dict[str, str], *, output: bool = False
) -> str:
    result = subprocess.run(
        command,
        cwd=root,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if output else None,
        timeout=None,
    )
    return result.stdout.strip() if output else ""


def consume_release(
    root: Path, profile: str, receipt: Path, env: dict[str, str]
) -> None:
    """Run an explicitly registered, host-local consumer after publication."""
    config = Path(env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
    hook = config / "runner-images" / profile / "after-release.json"
    if not hook.exists():
        return
    command: object = json.loads(hook.read_text())
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(arg, str) and arg for arg in cast(list[object], command))
    ):
        raise ValueError("after-release.json must contain a nonempty command array")
    invoke([*cast(list[str], command), str(receipt.parent.resolve())], root, env)


def rebuild(root: Path, profile: str) -> None:
    if profile not in ("xcode", "xcode-android"):
        raise ValueError("unknown image profile")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("run the issue command on an Apple Silicon Mac")
    (root / "artifacts").mkdir(exist_ok=True)
    with (root / "artifacts/rebuild.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        perform(root, profile)


def perform(root: Path, profile: str) -> None:
    env = dict(os.environ)
    env["GH_TOKEN"] = invoke(["gh", "auth", "token"], root, env, output=True)
    env["TART_REGISTRY_PASSWORD"] = env["GH_TOKEN"]
    env["TART_REGISTRY_USERNAME"] = invoke(
        ["gh", "api", "user", "--jq", ".login"], root, env, output=True
    )
    journal = root / "artifacts/rebuild.json"
    state = read_object(journal) if journal.exists() else {}

    def save() -> None:
        temporary = journal.with_suffix(".tmp")
        temporary.write_text(json.dumps(state) + "\n")
        temporary.replace(journal)

    if not state or state.get("done") or state.get("released_receipt"):
        if invoke(["git", "status", "--porcelain"], root, env, output=True):
            raise ValueError("isolated build checkout has uncommitted source changes")
        invoke(["git", "fetch", "origin", "main", "--tags"], root, env)
        invoke(["git", "checkout", "-B", "main", "origin/main"], root, env)
        desired = input_id(root, profile)
        for path in (root / f"qualifications/tart/{profile}").glob("*/release.json"):
            released = read_object(path)
            if (
                released.get("status") == "released"
                and released.get("input_id") == desired
            ):
                if released.get("qualification_sha256") != digest(
                    path.parent / "qualification.json"
                ):
                    raise ValueError("released qualification receipt is mismatched")
                state = {"done": False, "released_receipt": str(path.relative_to(root))}
                save()
                consume_release(root, profile, path, env)
                state["done"] = True
                save()
                print(f"Already released: {released['url']}")
                return
        # Fail before the expensive VM build if the finalizer is not activated.
        invoke(
            [
                "gh",
                "workflow",
                "view",
                "release-tart.yml",
                "--repo",
                "circlesac/runner-images",
                "--ref",
                "main",
            ],
            root,
            env,
            output=True,
        )
        state = cast(
            dict[str, object],
            {
                "image": f"macos-26-{profile}-local-{uuid.uuid4().hex}",
                "source": invoke(["git", "rev-parse", "HEAD"], root, env, output=True),
            },
        )
        save()
    source = str(state["source"])
    image = str(state["image"])
    bundle = root / "artifacts/tart" / image
    if not (bundle / "qualification.json").exists():
        # A failed build keeps diagnostics. Its next attempt gets a fresh name.
        if bundle.exists():
            image = f"macos-26-{profile}-local-{uuid.uuid4().hex}"
            state["image"] = image
            bundle = root / "artifacts/tart" / image
            save()
        invoke(["git", "checkout", "-B", "main", source], root, env)
        while True:
            machines = cast(
                list[dict[str, object]],
                json.loads(
                    invoke(["tart", "list", "--format", "json"], root, env, output=True)
                ),
            )
            if sum(m.get("State") == "running" for m in machines) < 2:
                break
            print("Waiting for a free macOS VM slot…", flush=True)
            time.sleep(30)
        invoke(
            [
                "bash",
                "macos/build-image.sh",
                image,
                "ghcr.io/cirruslabs/macos-tahoe-xcode:latest",
                profile,
            ],
            root,
            env,
        )
    if not (bundle / "publication.json").exists():
        invoke(["git", "checkout", "-B", "main", source], root, env)
        invoke(
            ["grype", f"sbom:{bundle / 'sbom.spdx.json'}", "--fail-on", "high"],
            root,
            env,
        )
        invoke(["python3", "scripts/publish-candidate.py", str(bundle)], root, env)
    identity = digest(bundle / "qualification.json")
    qualification = f"qualifications/tart/{profile}/{identity}"
    if not state.get("recorded"):
        pending = root / qualification
        if pending.exists():
            # A failed validation/commit can leave only our generated directory.
            # Preserve it outside the source tree before replaying this stage.
            recovery = root / "artifacts/recovery" / uuid.uuid4().hex
            recovery.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pending), recovery)
        invoke(["git", "checkout", "-B", "main", source], root, env)
        # Use the existing gh session for Git too; never change global config.
        invoke(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "config",
                "--local",
                "credential.https://github.com.helper",
                "!gh auth git-credential",
            ],
            root,
            env,
        )
        invoke(
            [
                "bash",
                "scripts/commit-qualification.sh",
                str(bundle),
                "main",
                "--require-publication",
            ],
            root,
            env,
        )
        state["recorded"] = True
        save()
    title = f"Release Tart {qualification}"

    def find_run() -> dict[str, object] | None:
        runs = cast(
            list[dict[str, object]],
            json.loads(
                invoke(
                    [
                        "gh",
                        "run",
                        "list",
                        "--repo",
                        "circlesac/runner-images",
                        "--workflow",
                        "release-tart.yml",
                        "--branch",
                        "main",
                        "--event",
                        "workflow_dispatch",
                        "--limit",
                        "100",
                        "--json",
                        "databaseId,displayTitle,status,conclusion",
                    ],
                    root,
                    env,
                    output=True,
                )
            ),
        )
        return next((run for run in runs if run.get("displayTitle") == title), None)

    run = find_run()
    if run is None:
        if not state.get("dispatched"):
            invoke(
                [
                    "gh",
                    "workflow",
                    "run",
                    "release-tart.yml",
                    "--repo",
                    "circlesac/runner-images",
                    "--ref",
                    "main",
                    "-f",
                    f"qualification={qualification}",
                ],
                root,
                env,
            )
            state["dispatched"] = True
            save()
        deadline = time.monotonic() + 120
        while run is None and time.monotonic() < deadline:
            time.sleep(2)
            run = find_run()
        if run is None:
            raise TimeoutError(
                "release was requested; rerun the same issue command to reconnect"
            )
    run_id = str(run["databaseId"])
    if run.get("status") == "completed" and run.get("conclusion") != "success":
        invoke(
            ["gh", "run", "rerun", run_id, "--repo", "circlesac/runner-images"],
            root,
            env,
        )
    invoke(
        [
            "gh",
            "run",
            "watch",
            run_id,
            "--repo",
            "circlesac/runner-images",
            "--exit-status",
        ],
        root,
        env,
    )
    # A successful Actions run alone is insufficient: read its durable receipt.
    invoke(["git", "fetch", "origin", "main"], root, env)
    invoke(["git", "checkout", "-B", "main", "origin/main"], root, env)
    receipt = root / qualification / "release.json"
    released = read_object(receipt)
    if (
        released.get("status") != "released"
        or released.get("qualification_sha256") != identity
    ):
        raise ValueError("published release receipt is missing or mismatched")
    consume_release(root, profile, receipt, env)
    state["done"] = True
    save()
    print(
        f"Release and issue reconciliation completed: https://github.com/circlesac/runner-images/actions/runs/{run_id}"
    )
