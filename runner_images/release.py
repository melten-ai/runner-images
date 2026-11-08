"""Validate local-build evidence, publish one CalVer release, retain its receipt."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import cast

from runner_images.qualification import digest, read_object, record
from runner_images.rebuild import gh
from runner_images.registry import record_publication


def qualification_path(root: Path, key: str) -> Path:
    if not re.fullmatch(r"qualifications/tart/(xcode|xcode-android)/[a-f0-9]{64}", key):
        raise ValueError("invalid qualification path")
    path = root / key
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("qualification path must not contain symlinks")
    if digest(path / "qualification.json") != path.name:
        raise ValueError("qualification path does not match its content")
    return path


def prepare(root: Path, key: str) -> dict[str, str]:
    original = qualification_path(root, key)
    receipt = read_object(original / "qualification.json")
    build = cast(dict[str, str], receipt["build"])
    if build.get("run_url") != "" or original.parent.name != build.get("profile"):
        raise ValueError("release requires matching local-build evidence")
    source = build["source_commit"]
    if not re.fullmatch(r"[a-f0-9]{40}", source):
        raise ValueError("invalid source commit")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", source, "origin/main"],
        cwd=root,
        check=True,
    )
    workspace = root / "artifacts/release"
    workspace.mkdir(parents=True, exist_ok=True)
    trusted = workspace / "source"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(trusted), source],
        cwd=root,
        check=True,
    )
    bundle = workspace / "bundle"
    shutil.copytree(original, bundle)
    for origin, name in (
        (trusted / "snapshots/github-hosted/macos-26.json", "reference.json"),
        (
            trusted / f"macos/accepted-missing/{build['profile']}.txt",
            "accepted-missing.txt",
        ),
    ):
        shutil.copyfile(origin, bundle / name)
    if (
        receipt.get("sbom_sha256") != digest(bundle / "sbom.spdx.json")
        or read_object(bundle / "sbom.spdx.json").get("spdxVersion") != "SPDX-2.3"
    ):
        raise ValueError("release requires a qualification-bound guest SBOM")
    destination = record(bundle, trusted, source)
    record_publication(bundle, trusted, destination)
    publication = read_object(bundle / "publication.json")
    reference = str(publication["reference"])
    predicate = {
        "builder": "local-apple-silicon-mac",
        "qualification": receipt,
        "publication": publication,
        "validation": "GitHub independently repeated comparison and registry read-back",
    }
    (workspace / "provenance.json").write_text(json.dumps(predicate, indent=2) + "\n")
    result = {
        "key": key,
        "source": source,
        "profile": build["profile"],
        "reference": reference,
        "subject": reference.split("@")[0],
        "digest": reference.split("@")[1],
        "bundle": str(bundle),
        "input_id": str(publication["input_id"]),
    }
    (workspace / "prepared.json").write_text(json.dumps(result) + "\n")
    return result


def existing_release(identity: str) -> dict[str, object] | None:
    page = 1
    while True:
        batch = cast(
            list[dict[str, object]],
            gh(
                "GET",
                f"repos/melten-ai/runner-images/releases?per_page=100&page={page}",
            ),
        )
        for release in batch:
            if f"<!-- runner-images:qualification:{identity} -->" in str(
                release.get("body")
            ):
                if release.get("prerelease"):
                    raise ValueError("unexpected prerelease for this qualification")
                return release
        if len(batch) < 100:
            return None
        page += 1


def publish_release(root: Path) -> dict[str, str]:
    prepared = read_object(root / "artifacts/release/prepared.json")
    key = str(prepared["key"])
    original = qualification_path(root, key)
    identity = original.name
    # These signed bundles are produced only after the independent hosted checks.
    assets = [
        original / name
        for name in (
            "qualification.json",
            "publication.json",
            "inventory.json",
            "toolset.json",
            "toolchain-check.txt",
            "sbom.spdx.json",
        )
    ]
    workspace = root / "artifacts/release"
    assets.extend(
        workspace / name
        for name in (
            "provenance.json",
            "provenance.sigstore.json",
            "sbom.sigstore.json",
            "vulnerabilities.json",
        )
    )
    if any(not path.is_file() or not path.stat().st_size for path in assets):
        raise ValueError("release checks/attestations are incomplete")
    release = existing_release(identity)
    if release is None:
        target = workspace / "version.go"
        target.write_text('package release\nconst Version = "0.0.0"\n')
        version = (
            subprocess.check_output(
                [
                    "npx",
                    "--yes",
                    "@circlesac/oneup@^26.10.0",
                    "version",
                    "--source",
                    "git",
                    "--tag-prefix",
                    f"tart-{prepared['profile']}@",
                    "--target",
                    str(target),
                ],
                cwd=root,
                text=True,
                timeout=180,
            )
            .strip()
            .splitlines()[-1]
        )
        if not re.fullmatch(r"[0-9]{2}\.[0-9]{1,2}\.[0-9]+", version):
            raise ValueError("oneup returned an invalid CalVer")
        tag = f"tart-{prepared['profile']}@{version}"
        notes = workspace / "notes.md"
        notes.write_text(
            f"<!-- runner-images:qualification:{identity} -->\n"
            f"Verified macOS 26 image ({prepared['profile']}).\n\n"
            f"```sh\ntart clone {prepared['reference']} example-runner\n```\n\n"
            f"Recipe: `{prepared['source']}`. "
            f"[Qualification](https://github.com/melten-ai/runner-images/tree/main/{key}).\n\n"
            "Built on a local Apple Silicon Mac. GitHub repeated the capability "
            "comparison, verified GHCR identity, scanned the guest SBOM and signed "
            "the validation evidence. Registry access follows package permissions.\n"
        )
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                "melten-ai/runner-images",
                "--draft",
                "--target",
                str(prepared["source"]),
                "--title",
                f"macOS 26 {prepared['profile']} {version}",
                "--notes-file",
                str(notes),
                *map(str, assets),
            ],
            cwd=root,
            check=True,
            timeout=600,
        )
        release = existing_release(identity)
        if release is None:
            raise ValueError("release publication could not be read back")
    # A create/upload failure can leave a partially published release. Repair assets
    # on replay before recording success; never mistake its mere existence for success.
    subprocess.run(
        [
            "gh",
            "release",
            "upload",
            str(release["tag_name"]),
            "--repo",
            "melten-ai/runner-images",
            "--clobber",
            *map(str, assets),
        ],
        cwd=root,
        check=True,
        timeout=600,
    )
    release = cast(
        dict[str, object],
        gh("GET", f"repos/melten-ai/runner-images/releases/{release['id']}"),
    )
    remote_assets = cast(list[dict[str, object]], release["assets"])
    for path in assets:
        expected = "sha256:" + digest(path)
        if not any(
            a.get("name") == path.name and a.get("digest") == expected
            for a in remote_assets
        ):
            raise ValueError("published release assets do not match their checksums")
    if release.get("draft"):
        subprocess.run(
            [
                "gh",
                "release",
                "edit",
                str(release["tag_name"]),
                "--repo",
                "melten-ai/runner-images",
                "--draft=false",
            ],
            cwd=root,
            check=True,
            timeout=90,
        )
        release = cast(
            dict[str, object],
            gh("GET", f"repos/melten-ai/runner-images/releases/{release['id']}"),
        )
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("release is not publicly published")
    commit = gh("GET", f"repos/melten-ai/runner-images/commits/{release['tag_name']}")
    if cast(dict[str, object], commit).get("sha") != prepared["source"]:
        raise ValueError("release tag points to a different source")
    result = {
        "status": "released",
        "input_id": str(prepared["input_id"]),
        "reference": str(prepared["reference"]),
        "tag": str(release["tag_name"]),
        "url": str(release["html_url"]),
        "qualification_sha256": identity,
    }
    (original / "release.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def write_outputs(outputs: dict[str, str]) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")
