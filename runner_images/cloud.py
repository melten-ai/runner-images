"""Cloud base-image lifecycle: input deduplication, smoke evidence and release."""

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import cast
from urllib.parse import quote
from urllib.request import urlopen

from runner_images.compare import compare, load
from runner_images.qualification import digest, read_object
from runner_images.rebuild import gh


def command(args: list[str], root: Path, *, output: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE if output else None,
        timeout=14400,
    )
    return result.stdout.strip() if output else ""


def api(args: list[str], root: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(command(args, root, output=True)))


def config(provider: str) -> dict[str, str]:
    if provider not in ("gcp", "azure"):
        raise ValueError("unknown cloud provider")
    required = (
        ("GCP_PROJECT", "GCP_ZONE", "GCP_NETWORK", "GCP_MACHINE_TYPE")
        if provider == "gcp"
        else (
            "AZURE_SUBSCRIPTION_ID",
            "AZURE_BUILD_RESOURCE_GROUP",
            "AZURE_LOCATION",
            "AZURE_VM_SIZE",
            "AZURE_RESOURCE_GROUP",
            "AZURE_GALLERY",
            "AZURE_IMAGE_DEFINITION",
        )
    )
    result = {key.lower(): os.environ.get(key, "") for key in required}
    if any(
        not value or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", value)
        for value in result.values()
    ):
        raise ValueError("cloud destination variables are missing or invalid")
    if (
        provider == "azure"
        and result["azure_build_resource_group"] == result["azure_resource_group"]
    ):
        raise ValueError("Azure build and gallery groups must be separate")
    return result


def inputs(
    root: Path,
    provider: str,
    settings: dict[str, str],
    base: str,
    toolchains: dict[str, object],
) -> str:
    names = [
        "snapshots/github-hosted/ubuntu-24.04.json",
        ".github/workflows/release-cloud.yml",
        ".github/workflows/cloud-image.yml",
        "runner_images/cloud.py",
        "runner_images/compare.py",
        "runner_images/inventory.py",
        "scripts/cloud-image.py",
    ]
    names += [
        str(p.relative_to(root))
        for p in (root / "cloud").rglob("*")
        if p.is_file()
        and (
            p.suffix in (".hcl", ".sh")
            or (p.parent.name == "linux" and p.suffix == ".py")
        )
    ]
    value = hashlib.sha256(
        json.dumps([provider, settings, base, toolchains], sort_keys=True).encode()
    )
    for name in sorted(names):
        value.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return value.hexdigest()


def releases(identity: str) -> dict[str, object] | None:
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
            if f"<!-- runner-images:cloud:{identity} -->" in str(release.get("body")):
                return release
        if len(batch) < 100:
            return None
        page += 1


def state_path(root: Path, provider: str) -> Path:
    if provider not in ("gcp", "azure"):
        raise ValueError("unknown cloud provider")
    return root / f"artifacts/cloud/{provider}/state.json"


def plan(root: Path, provider: str) -> dict[str, str]:
    settings = config(provider)
    if provider == "gcp":
        base = api(
            [
                "gcloud",
                "compute",
                "images",
                "describe-from-family",
                "ubuntu-2404-lts-amd64",
                "--project=ubuntu-os-cloud",
                "--format=json",
            ],
            root,
        )
        source = str(base["selfLink"])
        source_version = str(base["name"])
    else:
        base = api(
            [
                "az",
                "vm",
                "image",
                "show",
                "--urn",
                "Canonical:ubuntu-24_04-lts:server:latest",
                "--location",
                settings["azure_location"],
                "--subscription",
                settings["azure_subscription_id"],
                "--output",
                "json",
            ],
            root,
        )
        source_version = str(base["name"])
        source = f"Canonical:ubuntu-24_04-lts:server:{source_version}"
    toolchains = api(
        [sys.executable, "cloud/linux/install-toolchains.py", "--resolve"], root
    )
    identity = inputs(root, provider, settings, source, toolchains)
    workspace = state_path(root, provider).parent
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "toolchains-plan.json").write_text(
        json.dumps(toolchains, sort_keys=True) + "\n"
    )
    previous = root / f"qualifications/cloud/{provider}/{identity}/release.json"
    if previous.exists():
        receipt = read_object(previous)
        if receipt.get("status") != "released" or receipt.get("input_id") != identity:
            raise ValueError("invalid previous cloud release record")
        evidence = read_object(previous.with_name("image.json"))
        evidence["settings"] = settings
        if evidence.get("input_id") != identity:
            raise ValueError("previous image metadata does not match the destination")
        (workspace / "state.json").write_text(json.dumps(evidence))
        verify_image(root, provider)
        return {"needed": "false", "record": "false"}
    existing = releases(identity)
    if existing and not existing.get("draft"):
        # Recover a published release if a previous run failed before the Git commit.
        command(
            [
                "gh",
                "release",
                "download",
                str(existing["tag_name"]),
                "--repo",
                "melten-ai/runner-images",
                "--pattern",
                "image.json",
                "--dir",
                str(workspace),
                "--clobber",
            ],
            root,
        )
        receipt = read_object(workspace / "image.json")
        if (
            receipt.get("input_id") != identity
            or receipt.get("provider") != provider
            or receipt.get("base_image") != source
        ):
            raise ValueError("release recovery does not match cloud build inputs")
        assets = cast(list[dict[str, object]], existing.get("assets", []))
        if not any(
            asset.get("name") == "image.json"
            and asset.get("digest") == "sha256:" + digest(workspace / "image.json")
            for asset in assets
        ):
            raise ValueError("cloud recovery receipt checksum mismatch")
        commit = cast(
            dict[str, object],
            gh(
                "GET",
                "repos/melten-ai/runner-images/commits/"
                f"{quote(str(existing['tag_name']), safe='')}",
            ),
        )
        if commit.get("sha") != receipt.get("source_commit"):
            raise ValueError("cloud recovery source tag mismatch")
        receipt["settings"] = settings
        (workspace / "state.json").write_text(json.dumps(receipt))
        verify_image(root, provider)
        store(root, provider, existing)
        return {"needed": "false", "record": "true"}
    revision = command(["git", "rev-parse", "HEAD"], root, output=True)
    run = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    if not run.isdigit() or not attempt.isdigit():
        raise ValueError("invalid Actions run identity")
    build_id = f"ri-{provider}-{run}-{attempt}"
    target = workspace / "version.go"
    target.write_text('package image\nconst Version = "0.0.0"\n')
    version = command(
        [
            "npx",
            "--yes",
            "@circlesac/oneup@^26.10.0",
            "version",
            "--source",
            "git",
            "--tag-prefix",
            f"cloud-{provider}@",
            "--target",
            str(target),
        ],
        root,
        output=True,
    ).splitlines()[-1]
    if not re.fullmatch(r"[0-9]{2}\.[0-9]{1,2}\.[0-9]+", version):
        raise ValueError("invalid cloud CalVer version")
    # Failed drafts reserve their version; remove/repair only through this pipeline.
    if existing:
        version = str(existing["tag_name"]).split("@")[-1]
    if provider == "gcp":
        image_id = f"projects/{settings['gcp_project']}/global/images/{build_id}"
    else:
        image_id = (
            f"/subscriptions/{settings['azure_subscription_id']}/resourceGroups/"
            f"{settings['azure_resource_group']}/providers/Microsoft.Compute/galleries/"
            f"{settings['azure_gallery']}/images/{settings['azure_image_definition']}/versions/{version}"
        )
    state = {
        "provider": provider,
        "input_id": identity,
        "source_commit": revision,
        "base_image": source,
        "base_version": source_version,
        "version": version,
        "build_id": build_id,
        "image_id": image_id,
        "settings": settings,
        "qualification_level": "provisioning-smoke",
        "run_id": run,
    }
    state_path(root, provider).write_text(json.dumps(state, indent=2) + "\n")
    return {"needed": "true", "record": "true"}


def build(root: Path, provider: str) -> None:
    path = state_path(root, provider)
    state = read_object(path)
    settings = cast(dict[str, str], state["settings"])
    with urlopen("https://api.ipify.org", timeout=30) as response:
        address = str(ipaddress.IPv4Address(response.read().decode().strip()))
    values: dict[str, object] = {
        key: ""
        for key in (
            "gcp_project",
            "gcp_zone",
            "gcp_network",
            "gcp_machine_type",
            "azure_subscription",
            "azure_build_resource_group",
            "azure_location",
            "azure_vm_size",
            "azure_resource_group",
            "azure_gallery",
            "azure_definition",
        )
    }
    values.update(settings)
    values.update(
        azure_subscription=settings.get("azure_subscription_id", ""),
        azure_definition=settings.get("azure_image_definition", ""),
        provider=provider,
        build_id=str(state["build_id"]),
        version=str(state["version"]),
        source_image=str(state["base_version"]),
        source_version=str(state["base_version"]),
        output_directory=str(path.parent) + "/",
        source_commit=str(state["source_commit"]),
        input_id=str(state["input_id"]),
        toolchains=read_object(path.parent / "toolchains-plan.json"),
        builder_ip=address,
    )
    values.pop("azure_subscription_id", None)
    values.pop("azure_image_definition", None)
    variables = path.parent / "variables.pkrvars.json"
    variables.write_text(json.dumps(values))
    if provider == "gcp":
        command(
            [
                "gcloud",
                "compute",
                "firewall-rules",
                "create",
                str(state["build_id"]),
                "--project",
                settings["gcp_project"],
                "--network",
                settings["gcp_network"],
                "--allow=tcp:22",
                f"--source-ranges={address}/32",
                f"--description=runner-images:{state['build_id']}",
                "--target-tags",
                str(state["build_id"]),
                "--quiet",
            ],
            root,
        )
    command(["packer", "init", "cloud"], root)
    command(
        [
            "packer",
            "build",
            "-on-error=cleanup",
            "-only="
            + ("googlecompute.ubuntu" if provider == "gcp" else "azure-arm.ubuntu"),
            f"-var-file={variables}",
            "cloud",
        ],
        root,
    )
    observed = verify_image(root, provider)
    manifest = read_object(path.parent / "packer-manifest.json")
    artifacts = cast(list[dict[str, object]], manifest.get("builds", []))
    expected = str(state["build_id"]) if provider == "gcp" else str(state["image_id"])
    if not any(
        str(artifact.get("artifact_id", "")).lower() == expected.lower()
        for artifact in artifacts
    ):
        raise ValueError("Packer manifest does not identify the expected cloud image")
    state["observed_image_id"] = observed.get("id")
    path.write_text(json.dumps(state, indent=2) + "\n")
    inventory = load(path.parent / "inventory.json")
    if inventory["os"] != "linux" or inventory["arch"] != "x86_64":
        raise ValueError("unexpected cloud image platform")
    if not (path.parent / "toolchain-check.txt").stat().st_size:
        raise ValueError("native cloud checks are empty")
    sbom = read_object(path.parent / "sbom.spdx.json")
    if sbom.get("spdxVersion") != "SPDX-2.3" or not sbom.get("packages"):
        raise ValueError("cloud image SBOM is empty or invalid")
    native_sbom = read_object(path.parent / "sbom.syft.json")
    if not native_sbom.get("artifacts"):
        raise ValueError("native cloud image SBOM is empty or invalid")
    if read_object(path.parent / "toolchains.json") != read_object(
        path.parent / "toolchains-plan.json"
    ):
        raise ValueError("installed toolchains differ from the planned build inputs")
    # Basic profile smoke is the release gate; hosted parity is an honest report.
    comparison = compare(
        load(root / "snapshots/github-hosted/ubuntu-24.04.json"), inventory, None
    )
    (path.parent / "comparison.json").write_text(
        json.dumps(comparison, indent=2) + "\n"
    )
    evidence = {
        **{key: value for key, value in state.items() if key != "settings"},
        "evidence_sha256": {
            name: digest(path.parent / name)
            for name in (
                "inventory.json",
                "toolchain-check.txt",
                "sbom.spdx.json",
                "sbom.syft.json",
                "toolchains.json",
                "comparison.json",
                "packer-manifest.json",
            )
        },
    }
    (path.parent / "image.json").write_text(json.dumps(evidence, indent=2) + "\n")


def verify_image(root: Path, provider: str) -> dict[str, object]:
    state = read_object(state_path(root, provider))
    settings = cast(dict[str, str], state["settings"])
    if provider == "gcp":
        image = api(
            [
                "gcloud",
                "compute",
                "images",
                "describe",
                str(state["build_id"]),
                "--project",
                settings["gcp_project"],
                "--format=json",
            ],
            root,
        )
        tags = cast(dict[str, str], image.get("labels", {}))
        ready = image.get("status") == "READY"
    else:
        image = api(
            [
                "az",
                "sig",
                "image-version",
                "show",
                "--ids",
                str(state["image_id"]),
                "--subscription",
                settings["azure_subscription_id"],
                "-o",
                "json",
            ],
            root,
        )
        tags = cast(dict[str, str], image.get("tags", {}))
        ready = image.get("provisioningState") == "Succeeded"
    if (
        not ready
        or tags.get("owner") != "runner-images"
        or tags.get("build") != state["build_id"]
        or tags.get("source") != state["source_commit"]
        or tags.get("recipe")
        != str(state["input_id"])[: 40 if provider == "gcp" else 64]
    ):
        raise ValueError("cloud image is not ready or not owned by this build")
    if not image.get("id") or (
        state.get("observed_image_id") is not None
        and state["observed_image_id"] != image["id"]
    ):
        raise ValueError("cloud image identity changed after qualification")
    return image


def store(root: Path, provider: str, released: dict[str, object]) -> None:
    path = state_path(root, provider)
    evidence = read_object(path.parent / "image.json")
    target = root / f"qualifications/cloud/{provider}/{evidence['input_id']}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "image.json").write_bytes((path.parent / "image.json").read_bytes())
    (target / "release.json").write_text(
        json.dumps(
            {
                "status": "released",
                "input_id": evidence["input_id"],
                "image_id": evidence["image_id"],
                "source_commit": evidence["source_commit"],
                "url": released["html_url"],
                "qualification_level": "provisioning-smoke",
            },
            indent=2,
        )
        + "\n"
    )


def publish(root: Path, provider: str) -> None:
    path = state_path(root, provider)
    evidence = read_object(path.parent / "image.json")
    verify_image(root, provider)
    hashes = cast(dict[str, str], evidence["evidence_sha256"])
    for name, expected in hashes.items():
        if Path(name).name != name or digest(path.parent / name) != expected:
            raise ValueError("cloud build evidence changed before publication")
    assets = [
        path.parent / name
        for name in (
            "image.json",
            *hashes,
            "vulnerabilities.json",
            "provenance.sigstore.json",
            "sbom.sigstore.json",
        )
    ]
    if any(not asset.is_file() or not asset.stat().st_size for asset in assets):
        raise ValueError("cloud release checks/attestations are incomplete")
    tag = f"cloud-{provider}@{evidence['version']}"
    notes = path.parent / "notes.md"
    notes.write_text(
        f"<!-- runner-images:cloud:{evidence['input_id']} -->\n"
        f"Ubuntu 24.04 {provider} runner base. Image: `{evidence['image_id']}`.\n\n"
        f"Source: `{evidence['source_commit']}`. Base: `{evidence['base_image']}`.\n\n"
        "Provisioning-VM native smoke checks, SBOM/security scanning and cloud image "
        "read-back passed. Fresh-image boot and actual Actions-job validation are "
        "not part of this qualification. Hosted parity differences are attached.\n"
    )
    released = releases(str(evidence["input_id"]))
    if released is None:
        command(
            [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                "melten-ai/runner-images",
                "--draft",
                "--target",
                str(evidence["source_commit"]),
                "--title",
                f"{provider} Ubuntu 24.04 {evidence['version']}",
                "--notes-file",
                str(notes),
                *map(str, assets),
            ],
            root,
        )
    command(
        [
            "gh",
            "release",
            "upload",
            tag,
            "--repo",
            "melten-ai/runner-images",
            "--clobber",
            *map(str, assets),
        ],
        root,
    )
    released = releases(str(evidence["input_id"]))
    if released is None:
        raise ValueError("cloud release could not be read back")
    remote = cast(list[dict[str, object]], released["assets"])
    if any(
        not any(
            a.get("name") == p.name and a.get("digest") == "sha256:" + digest(p)
            for a in remote
        )
        for p in assets
    ):
        raise ValueError("cloud release asset digest mismatch")
    command(
        [
            "gh",
            "release",
            "edit",
            tag,
            "--repo",
            "melten-ai/runner-images",
            "--draft=false",
            "--notes-file",
            str(notes),
            "--target",
            str(evidence["source_commit"]),
        ],
        root,
    )
    released = releases(str(evidence["input_id"]))
    if released is None or released.get("draft") or released.get("prerelease"):
        raise ValueError("cloud release is not published")
    commit = cast(
        dict[str, object],
        gh("GET", f"repos/melten-ai/runner-images/commits/{quote(tag, safe='')}"),
    )
    if commit.get("sha") != evidence["source_commit"]:
        raise ValueError("cloud release tag points to the wrong source")
    store(root, provider, released)


def cleanup(root: Path, provider: str) -> None:
    path = state_path(root, provider)
    if not path.exists():
        return
    state = read_object(path)
    settings = cast(dict[str, str], state["settings"])
    build_id = str(state["build_id"])
    failures: list[str] = []

    def owned(tags: dict[str, str]) -> None:
        if tags.get("owner") != "runner-images" or tags.get("build") != build_id:
            raise ValueError("refusing to delete a resource without matching ownership")

    def remove_gcp(kind: str) -> None:
        args = [
            "gcloud",
            "compute",
            kind,
            "list",
            "--project",
            settings["gcp_project"],
            f"--filter=name={build_id}",
            "--format=json",
        ]
        records = cast(
            list[dict[str, object]], json.loads(command(args, root, output=True))
        )
        for resource in records:
            if kind == "firewall-rules":
                if resource.get("description") != f"runner-images:{build_id}":
                    raise ValueError("refusing to delete a foreign firewall rule")
            else:
                owned(cast(dict[str, str], resource.get("labels", {})))
            deletion = [
                "gcloud",
                "compute",
                kind,
                "delete",
                build_id,
                "--project",
                settings["gcp_project"],
                "--quiet",
            ]
            if kind in ("instances", "disks"):
                deletion += ["--zone", settings["gcp_zone"]]
            command(deletion, root)

    if provider == "gcp":
        for kind in ("instances", "disks", "firewall-rules"):
            try:
                remove_gcp(kind)
            except (OSError, ValueError, subprocess.SubprocessError):
                failures.append(kind)
    else:
        try:
            previous: set[str] | None = None
            while True:
                resources = cast(
                    list[dict[str, object]],
                    json.loads(
                        command(
                            [
                                "az",
                                "resource",
                                "list",
                                "--subscription",
                                settings["azure_subscription_id"],
                                "--resource-group",
                                settings["azure_build_resource_group"],
                                "--query",
                                "[?tags.owner=='runner-images' && "
                                f"tags.build=='{build_id}']",
                                "-o",
                                "json",
                            ],
                            root,
                            output=True,
                        )
                    ),
                )
                ids = {str(resource["id"]) for resource in resources}
                if not ids:
                    break
                if ids == previous:
                    raise ValueError(
                        "temporary Azure resource deletion made no progress"
                    )
                previous = ids
                order = {
                    "microsoft.compute/virtualmachines": 0,
                    "microsoft.network/networkinterfaces": 1,
                    "microsoft.compute/disks": 2,
                }
                resources.sort(key=lambda r: order.get(str(r.get("type")).lower(), 3))
                for resource in resources:
                    owned(cast(dict[str, str], resource.get("tags", {})))
                    try:
                        command(
                            [
                                "az",
                                "resource",
                                "delete",
                                "--ids",
                                str(resource["id"]),
                                "--subscription",
                                settings["azure_subscription_id"],
                            ],
                            root,
                        )
                    except subprocess.SubprocessError:
                        pass  # Dependencies can disappear later in this pass; re-list.
        except (OSError, ValueError, subprocess.SubprocessError):
            failures.append("temporary-resources")
    # Never delete an image referenced by a published release, even when recording
    # its Git receipt failed. An unavailable release API preserves the image too.
    try:
        released = releases(str(state["input_id"]))
        if released is None or released.get("draft"):
            if provider == "gcp":
                remove_gcp("images")
            else:
                images = cast(
                    list[dict[str, object]],
                    json.loads(
                        command(
                            [
                                "az",
                                "sig",
                                "image-version",
                                "list",
                                "--subscription",
                                settings["azure_subscription_id"],
                                "--resource-group",
                                settings["azure_resource_group"],
                                "--gallery-name",
                                settings["azure_gallery"],
                                "--gallery-image-definition",
                                settings["azure_image_definition"],
                                "--query",
                                f"[?name=='{state['version']}']",
                                "-o",
                                "json",
                            ],
                            root,
                            output=True,
                        )
                    ),
                )
                for image in images:
                    owned(cast(dict[str, str], image.get("tags", {})))
                    command(
                        [
                            "az",
                            "sig",
                            "image-version",
                            "delete",
                            "--ids",
                            str(state["image_id"]),
                            "--subscription",
                            settings["azure_subscription_id"],
                        ],
                        root,
                    )
    except (OSError, ValueError, subprocess.SubprocessError):
        failures.append("unpublished-image")
    (path.parent / "cleanup.json").write_text(json.dumps({"failed": failures}) + "\n")
    if failures:
        raise RuntimeError("cloud cleanup incomplete: " + ", ".join(failures))
