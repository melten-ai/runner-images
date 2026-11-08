"""Reconcile durable candidate uploads with one rebuild issue per profile."""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast

from runner_images.qualification import read_object


def input_id(root: Path, profile: str) -> str:
    if profile not in ("xcode", "xcode-android"):
        raise ValueError("unknown profile")
    # Hash build inputs, excluding generated evidence and Linux observations.
    names = [
        ".github/workflows/release-tart.yml",
        "snapshots/github-hosted/macos-26.json",
        f"macos/accepted-missing/{profile}.txt",
    ]
    for directory, pattern in (
        ("macos", "*.sh"),
        ("runner_images", "*.py"),
        ("scripts", "*.py"),
        ("scripts", "*.sh"),
    ):
        names.extend(
            str(p.relative_to(root))
            for p in (root / directory).rglob(pattern)
            if p.name
            not in ("cloud.py", "cloud-image.py", "commit-cloud.sh", "check-cloud.py")
        )
    result = hashlib.sha256(profile.encode())
    for name in sorted(set(names)):
        result.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return result.hexdigest()


def gh(method: str, endpoint: str, payload: dict[str, object] | None = None) -> object:
    command = ["gh", "api", "--method", method, endpoint]
    if payload is None:
        return json.loads(subprocess.check_output(command, text=True, timeout=90))
    result = subprocess.run(
        [*command, "--input", "-"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=90,
    )
    return json.loads(result.stdout)


def issues() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    page = 1
    while True:
        batch = gh(
            "GET",
            f"repos/circlesac/runner-images/issues?state=all&per_page=100&page={page}",
        )
        if not isinstance(batch, list):
            raise ValueError("invalid issue response")
        entries = cast(list[dict[str, object]], batch)
        result.extend(entries)
        if len(entries) < 100:
            return result
        page += 1


def reconcile(root: Path) -> None:
    existing = issues()  # REST pagination, never eventually indexed search.
    for profile in ("xcode", "xcode-android"):
        desired = input_id(root, profile)
        marker = f"<!-- runner-images:rebuild:{profile} -->"
        # The API includes additional user fields; compare only the login.
        matching = [
            i
            for i in existing
            if str(i.get("body", "")).startswith(marker)
            and isinstance(i.get("user"), dict)
            and cast(dict[str, object], i["user"]).get("login") == "github-actions[bot]"
            and "pull_request" not in i
        ]
        uploaded: dict[str, object] | None = None
        for path in sorted(
            (root / f"qualifications/tart/{profile}").glob("*/release.json")
        ):
            receipt = read_object(path)
            if (
                receipt.get("input_id") == desired
                and receipt.get("status") == "released"
            ):
                uploaded = receipt
        command = (
            "(bootstrap=$(mktemp) && trap 'rm -f \"$bootstrap\"' EXIT && "
            "gh api -H 'Accept: application/vnd.github.raw+json' "
            "'repos/circlesac/runner-images/contents/macos/rebuild.sh?ref=main' "
            f'> "$bootstrap" && bash "$bootstrap" {profile})'
        )
        body = (
            f"{marker}\n## Rebuild Tart image: {profile}\n\n"
            f"Required inputs: `{desired}`\n\n"
            "Run this one command in a terminal on your Apple Silicon Mac:\n\n"
            f"```sh\n{command}\n```\n\n"
            "It prepares an isolated checkout, builds and checks the image, "
            "uploads it, records evidence and waits for GitHub to publish the "
            "release and close this issue. Existing gh authentication is reused; "
            "required Homebrew tools are installed automatically. "
            "Run the same command again after an interruption to resume. "
            "If this Mac has a registered local consumer, it is called after "
            "publication and on replay; its failure fails the command. "
            "This public issue tracks publication, while consumer adoption is "
            "recorded locally.\n\n"
            "Failures keep this issue open. A newer reference/recipe remains "
            "outstanding even if an older build finishes.\n"
        )
        if uploaded:
            body += f"\nReleased image: `{uploaded['reference']}`\n"
            body += f"\n[GitHub Release]({uploaded['url']})\n"
        state = "closed" if uploaded else "open"
        if not matching:
            if not uploaded:
                gh(
                    "POST",
                    "repos/circlesac/runner-images/issues",
                    {
                        "title": f"Rebuild macOS Tart image ({profile})",
                        "body": body,
                    },
                )
            continue
        matching.sort(key=lambda i: int(str(i["number"])))
        canonical = matching[0]
        for issue in matching:
            endpoint = f"repos/circlesac/runner-images/issues/{issue['number']}"
            target_state = state if issue is canonical else "closed"
            payload: dict[str, object] = {}
            if issue is canonical and issue.get("body") != body:
                payload["body"] = body
            if issue.get("state") != target_state:
                payload.update(
                    state=target_state,
                    state_reason=(
                        "completed"
                        if uploaded and issue is canonical
                        else "not_planned"
                    )
                    if target_state == "closed"
                    else "reopened",
                )
            if payload:
                gh("PATCH", endpoint, payload)
