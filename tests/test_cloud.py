import copy
import json
import re
import subprocess
from pathlib import Path

import pytest

from runner_images import cloud


def test_cloud_security_policy_changes_build_identity(fixture):
    root, provider, _ = fixture
    cloud.plan(root, provider)
    before = json.loads(cloud.state_path(root, provider).read_text())["input_id"]
    (root / "cloud/grype.json").write_text("changed scanner policy")
    cloud.plan(root, provider)
    assert (
        json.loads(cloud.state_path(root, provider).read_text())["input_id"] != before
    )


@pytest.fixture(params=["gcp", "azure"])
def fixture(tmp_path, monkeypatch, request):
    provider = request.param
    for name in (
        "snapshots/github-hosted/ubuntu-24.04.json",
        ".github/workflows/release-cloud.yml",
        ".github/workflows/cloud-image.yml",
        "runner_images/cloud.py",
        "runner_images/compare.py",
        "runner_images/inventory.py",
        "scripts/cloud-image.py",
        "cloud/runner.pkr.hcl",
        "cloud/grype.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic recipe")
    inventory = {
        "schema_version": 1,
        "os": "linux",
        "arch": "x86_64",
        "image_os": "ubuntu24",
        "image_version": "example-base",
        "records": ["cmd bash", "cmd gcc", "cmd missing-hosted-tool"],
    }
    (tmp_path / "snapshots/github-hosted/ubuntu-24.04.json").write_text(
        json.dumps(inventory)
    )
    settings = {
        "GCP_PROJECT": "example-project",
        "GCP_MACHINE_TYPE": "e2-standard-4",
        "GCP_ZONE": "us-central1-a",
        "GCP_NETWORK": "example-runner-network",
        "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
        "AZURE_BUILD_RESOURCE_GROUP": "example-builds",
        "AZURE_LOCATION": "eastus",
        "AZURE_VM_SIZE": "Standard_D4s_v5",
        "AZURE_RESOURCE_GROUP": "example-images",
        "AZURE_GALLERY": "examplegallery",
        "AZURE_IMAGE_DEFINITION": "ubuntu2404",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    external = {
        "commands": [],
        "releases": [],
        "resources": {},
        "fail": "",
        "version": "26.11.0",
        "base": "ubuntu-example-20261108",
        "definition_features": [
            {"name": "SecurityType", "value": "TrustedLaunchSupported"},
            {"name": "DiskControllerTypes", "value": "SCSI, NVMe"},
        ],
        "toolchains": {
            "node": {"version": "v24.0.0", "sha256": "a" * 64},
            "git-lfs": {
                "version": "v3.8.1",
                "net_version": "v0.62.0",
                "compress_version": "v1.20.4",
            },
        },
    }

    def current():
        path = cloud.state_path(tmp_path, provider)
        return json.loads(path.read_text()) if path.exists() else {}

    def commands(args, root, *, output=False):
        external["commands"].append(args)
        if args[1:] == ["cloud/linux/install-toolchains.py", "--resolve"]:
            return json.dumps(external["toolchains"])
        if args[:2] == ["git", "rev-parse"]:
            return "a" * 40
        if args[0] == "oneup":
            return external["version"]
        if "describe-from-family" in args:
            return json.dumps(
                {
                    "name": external["base"],
                    "selfLink": "projects/ubuntu-os-cloud/global/images/"
                    + external["base"],
                }
            )
        if args[:4] == ["az", "vm", "image", "show"]:
            return json.dumps({"name": external["base"]})
        if args[:4] == ["az", "sig", "image-definition", "show"]:
            return json.dumps(
                {"features": copy.deepcopy(external["definition_features"])}
            )
        if args[:4] == ["az", "sig", "image-definition", "update"]:
            features = external["definition_features"]
            if "--add" in args:
                features.append(json.loads(args[args.index("--add") + 2]))
            if "--set" in args:
                position = args.index("--set") + 1
                while position < len(args) and not args[position].startswith("--"):
                    target, value = args[position].split("=", 1)
                    selected = re.fullmatch(r"features\[(\d+)\]\.(\w+)", target)
                    features[int(selected.group(1))][selected.group(2)] = value
                    position += 1
            return ""
        state = current()
        tags = {
            "owner": "runner-images",
            "build": state.get("build_id"),
            "source": state.get("source_commit"),
            "recipe": str(state.get("input_id"))[: 40 if provider == "gcp" else 64],
        }
        if args[0] in ("gcloud", "az") and (
            "describe" in args or ("show" in args and "image-version" in args)
        ):
            if external["fail"] == "image":
                tags["owner"] = "foreign-owner"
            return json.dumps(
                {
                    "id": state.get("image_id"),
                    "status": "READY",
                    "provisioningState": "Succeeded",
                    "tags": tags,
                    "labels": tags,
                }
            )
        if args[:2] == ["packer", "build"]:
            if external["fail"] == "packer":
                raise subprocess.CalledProcessError(1, args)
            folder = cloud.state_path(root, provider).parent
            observed = {**inventory, "records": ["cmd bash", "cmd gcc"]}
            if external["fail"] == "platform":
                observed["arch"] = "arm64"
            (folder / "inventory.json").write_text(json.dumps(observed))
            (folder / "toolchain-check.txt").write_text(
                "native smoke passed" if external["fail"] != "native" else ""
            )
            (folder / "sbom.spdx.json").write_text(
                json.dumps(
                    {
                        "spdxVersion": "SPDX-2.3",
                        "packages": []
                        if external["fail"] == "sbom"
                        else [{"name": "example"}],
                    }
                )
            )
            (folder / "sbom.syft.json").write_text(
                json.dumps(
                    {
                        "artifacts": []
                        if external["fail"] == "native-sbom"
                        else [{"name": "example", "version": "1.0"}],
                    }
                )
            )
            (folder / "toolchains.json").write_text(
                json.dumps(
                    {}
                    if external["fail"] == "receipts"
                    else json.loads((folder / "toolchains-plan.json").read_text())
                )
            )
            (folder / "git-lfs-build.json").write_text(
                json.dumps(
                    {
                        "inputs": {}
                        if external["fail"] == "lfs-receipt"
                        else external["toolchains"]["git-lfs"]
                    }
                )
            )
            artifact = state["build_id"] if provider == "gcp" else state["image_id"]
            (folder / "packer-manifest.json").write_text(
                json.dumps(
                    {
                        "builds": [
                            {
                                "artifact_id": artifact
                                if external["fail"] != "manifest"
                                else "foreign-image"
                            }
                        ]
                    }
                )
            )
        if args[:3] == ["gh", "release", "create"]:
            notes = Path(args[args.index("--notes-file") + 1]).read_text()
            external["releases"].append(
                {
                    "id": 42,
                    "tag_name": args[3],
                    "draft": True,
                    "body": notes,
                    "html_url": "https://example.com/release",
                    "prerelease": False,
                    "assets": [],
                }
            )
        if args[:3] == ["gh", "release", "upload"]:
            assets = [Path(p) for p in args[args.index("--clobber") + 1 :]]
            external["releases"][0]["assets"] = [
                {"name": p.name, "digest": "sha256:" + cloud.digest(p)} for p in assets
            ]
            if external["fail"] == "asset":
                external["releases"][0]["assets"][0]["digest"] = "sha256:wrong"
        if args[:3] == ["gh", "release", "edit"]:
            external["releases"][0]["draft"] = external["fail"] == "publish"
        if args[:3] == ["gh", "release", "download"]:
            asset = external["image_asset"]
            directory = Path(args[args.index("--dir") + 1])
            (directory / "image.json").write_text(asset)
        if "list" in args and args[0] in ("az", "gcloud"):
            key = (
                args[2]
                if args[0] == "gcloud"
                else ("gallery" if "image-version" in args else "resources")
            )
            return json.dumps(external["resources"].get(key, []))
        if "delete" in args:
            if external["fail"] == "cleanup":
                raise subprocess.CalledProcessError(1, args)
            if args[:3] == ["az", "resource", "delete"]:
                selected = args[args.index("--ids") + 1]
                external["resources"]["resources"] = [
                    r for r in external["resources"]["resources"] if r["id"] != selected
                ]
        return ""

    def github(method, endpoint, payload=None):
        if "/commits/" in endpoint:
            return {"sha": "a" * 40 if external["fail"] != "tag" else "b" * 40}
        if external["fail"] == "github":
            raise subprocess.CalledProcessError(1, ["gh"])
        return copy.deepcopy(external["releases"])

    class Address:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"192.0.2.1"

    monkeypatch.setattr(cloud, "command", commands)
    monkeypatch.setattr(cloud, "gh", github)
    monkeypatch.setattr(cloud, "urlopen", lambda *a, **kw: Address())
    return tmp_path, provider, external


def signed(root, provider):
    for name in (
        "vulnerabilities.json",
        "provenance.sigstore.json",
        "sbom.sigstore.json",
    ):
        (cloud.state_path(root, provider).parent / name).write_text(
            '{"synthetic":"external scan/signing result"}'
        )


def test_plan_build_publish_noop_and_new_inputs(fixture):
    root, provider, state = fixture
    assert cloud.plan(root, provider) == {"needed": "true", "record": "true"}
    cloud.build(root, provider)
    folder = cloud.state_path(root, provider).parent
    evidence = json.loads((folder / "image.json").read_text())
    assert "settings" not in evidence
    variables = json.loads((folder / "variables.pkrvars.json").read_text())
    assert variables["source_image"] == state["base"]
    if provider == "gcp":
        assert evidence["base_image"].endswith("/images/" + state["base"])
        assert "/" not in variables["source_image"]
    report = json.loads((folder / "comparison.json").read_text())
    assert report["unexpected_missing"] == ["cmd missing-hosted-tool"]
    assert not report["compatible"]  # basic image release never claims full parity
    signed(root, provider)
    cloud.publish(root, provider)
    assert len(state["releases"]) == 1 and not state["releases"][0]["draft"]
    record = next((root / f"qualifications/cloud/{provider}").glob("*/release.json"))
    assert json.loads(record.read_text())["qualification_level"] == "provisioning-smoke"
    assert cloud.plan(root, provider)["needed"] == "false"
    state["base"] = "new-upstream-image"
    assert cloud.plan(root, provider)["needed"] == "true"


def test_build_declares_nvme_support_starting_at_exact_built_version(fixture):
    root, provider, state = fixture
    state["definition_features"] = [
        {"name": "SecurityType", "value": "TrustedLaunchSupported"},
        {"name": "IsAcceleratedNetworkSupported", "value": "true"},
    ]
    cloud.plan(root, provider)
    cloud.build(root, provider)
    if provider == "gcp":
        assert not any("image-definition" in c for c in state["commands"])
        return
    updates = [
        c
        for c in state["commands"]
        if c[:4] == ["az", "sig", "image-definition", "update"]
    ]
    assert len(updates) == 1
    update = updates[0]
    assert update[update.index("--allow-update-image") + 1] == "true"
    # The feature must carry the exact first supporting image version; older
    # versions never retroactively claim NVMe support.
    assert json.loads(update[update.index("--add") + 2]) == {
        "name": "DiskControllerTypes",
        "value": "SCSI,NVMe",
        "startsAtVersion": "26.11.0",
    }
    assert "--set" not in update
    assert "--remove" not in update
    assert not any("SecurityType" in argument for argument in update)
    assert state["definition_features"] == [
        {"name": "SecurityType", "value": "TrustedLaunchSupported"},
        {"name": "IsAcceleratedNetworkSupported", "value": "true"},
        {
            "name": "DiskControllerTypes",
            "value": "SCSI,NVMe",
            "startsAtVersion": "26.11.0",
        },
    ]
    # The definition supports NVMe before Packer publishes the version.
    assert state["commands"].index(update) < next(
        i for i, c in enumerate(state["commands"]) if c[:2] == ["packer", "build"]
    )
    # A rebuild sees the reconciled definition and stays read-only.
    cloud.build(root, provider)
    assert (
        sum(
            c[:4] == ["az", "sig", "image-definition", "update"]
            for c in state["commands"]
        )
        == 1
    )


def test_partial_controller_declaration_widens_only_from_built_version(fixture):
    root, provider, state = fixture
    state["definition_features"] = [{"name": "DiskControllerTypes", "value": "SCSI"}]
    cloud.plan(root, provider)
    cloud.build(root, provider)
    if provider == "gcp":
        assert not any("image-definition" in c for c in state["commands"])
        return
    updates = [
        c
        for c in state["commands"]
        if c[:4] == ["az", "sig", "image-definition", "update"]
    ]
    assert len(updates) == 1
    position = updates[0].index("--set")
    assert updates[0][position + 1 : position + 3] == [
        "features[0].value=SCSI,NVMe",
        "features[0].startsAtVersion=26.11.0",
    ]
    assert "--add" not in updates[0]
    assert state["definition_features"] == [
        {
            "name": "DiskControllerTypes",
            "value": "SCSI,NVMe",
            "startsAtVersion": "26.11.0",
        }
    ]


def test_build_leaves_already_correct_definition_untouched(fixture):
    root, provider, state = fixture
    before = copy.deepcopy(state["definition_features"])
    cloud.plan(root, provider)
    cloud.build(root, provider)
    assert not any(
        c[:4] == ["az", "sig", "image-definition", "update"] for c in state["commands"]
    )
    assert state["definition_features"] == before


@pytest.mark.parametrize(
    "failure",
    [
        "packer",
        "image",
        "platform",
        "native",
        "sbom",
        "native-sbom",
        "receipts",
        "lfs-receipt",
        "manifest",
    ],
)
def test_failed_build_cannot_reach_release(fixture, failure):
    root, provider, state = fixture
    cloud.plan(root, provider)
    state["fail"] = failure
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        cloud.build(root, provider)
    assert not state["releases"]
    assert not (root / "qualifications/cloud").exists()


@pytest.mark.parametrize("failure", ["asset", "publish", "tag"])
def test_partial_release_replay_never_duplicates_or_records_early(fixture, failure):
    root, provider, state = fixture
    cloud.plan(root, provider)
    cloud.build(root, provider)
    signed(root, provider)
    state["fail"] = failure
    with pytest.raises(ValueError):
        cloud.publish(root, provider)
    assert not (root / "qualifications/cloud").exists()
    state["fail"] = ""
    cloud.publish(root, provider)
    assert len(state["releases"]) == 1
    assert (root / f"qualifications/cloud/{provider}").exists()


@pytest.mark.parametrize("artifact", ["inventory.json", "git-lfs-build.json"])
def test_missing_attestations_and_tampered_evidence_cannot_release(fixture, artifact):
    root, provider, state = fixture
    cloud.plan(root, provider)
    cloud.build(root, provider)
    with pytest.raises(ValueError, match="incomplete"):
        cloud.publish(root, provider)
    signed(root, provider)
    (cloud.state_path(root, provider).parent / artifact).write_text("tampered")
    with pytest.raises(ValueError, match="changed"):
        cloud.publish(root, provider)
    assert not state["releases"]


def test_published_release_git_recovery_preserves_existing_image(fixture):
    root, provider, state = fixture
    cloud.plan(root, provider)
    cloud.build(root, provider)
    signed(root, provider)
    cloud.publish(root, provider)
    folder = cloud.state_path(root, provider).parent
    state["image_asset"] = (folder / "image.json").read_text()
    import shutil

    shutil.rmtree(root / "qualifications")
    assert cloud.plan(root, provider) == {"needed": "false", "record": "true"}
    assert len(state["releases"]) == 1
    assert (root / f"qualifications/cloud/{provider}").exists()


def test_cleanup_removes_only_owned_resources_and_keeps_published_images(fixture):
    root, provider, state = fixture
    cloud.cleanup(root, provider)  # failed auth/plan: nothing has been created
    cloud.plan(root, provider)
    current = json.loads(cloud.state_path(root, provider).read_text())
    tags = {"owner": "runner-images", "build": current["build_id"]}
    if provider == "gcp":
        for kind in ("instances", "disks", "images"):
            state["resources"][kind] = [{"labels": tags}]
        state["resources"]["firewall-rules"] = [
            {"description": "runner-images:" + current["build_id"]}
        ]
    else:
        state["resources"]["resources"] = [
            {
                "id": "example-vm",
                "type": "Microsoft.Compute/virtualMachines",
                "tags": tags,
            }
        ]
        state["resources"]["gallery"] = [{"id": current["image_id"], "tags": tags}]
    cloud.cleanup(root, provider)
    assert json.loads(
        (cloud.state_path(root, provider).parent / "cleanup.json").read_text()
    ) == {"failed": []}
    state["releases"] = [
        {
            "body": "<!-- runner-images:cloud:" + current["input_id"] + " -->",
            "draft": False,
        }
    ]
    state["commands"].clear()
    cloud.cleanup(root, provider)
    assert not any(
        "delete" in c and ("images" in c or "image-version" in c)
        for c in state["commands"]
    )


def test_cleanup_refuses_foreign_resources_and_reports_provider_failure(fixture):
    root, provider, state = fixture
    cloud.plan(root, provider)
    if provider == "gcp":
        state["resources"]["instances"] = [{"labels": {"owner": "foreign"}}]
        state["resources"]["firewall-rules"] = [{"description": "foreign"}]
    else:
        state["resources"]["resources"] = [
            {"id": "foreign-id", "tags": {"owner": "foreign"}}
        ]
    with pytest.raises(RuntimeError):
        cloud.cleanup(root, provider)
    assert not any("delete" in c for c in state["commands"])
    state["resources"] = {}
    state["fail"] = "github"
    with pytest.raises(RuntimeError, match="unpublished-image"):
        cloud.cleanup(root, provider)
    assert not any(
        "delete" in c and ("images" in c or "image-version" in c)
        for c in state["commands"]
    )


def test_config_and_invalid_actions_identity_fail_before_build(fixture, monkeypatch):
    root, provider, state = fixture
    with pytest.raises(ValueError):
        cloud.config("unknown")
    with pytest.raises(ValueError):
        cloud.state_path(root, "unknown")
    monkeypatch.setenv("GITHUB_RUN_ID", "invalid")
    with pytest.raises(ValueError, match="run identity"):
        cloud.plan(root, provider)
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    state["version"] = "bad-version"
    with pytest.raises(ValueError, match="CalVer"):
        cloud.plan(root, provider)
    monkeypatch.setenv("GCP_PROJECT" if provider == "gcp" else "AZURE_GALLERY", "")
    with pytest.raises(ValueError, match="variables"):
        cloud.config(provider)


def test_azure_build_group_must_not_hold_released_images(monkeypatch):
    for key in (
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_VM_SIZE",
        "AZURE_LOCATION",
        "AZURE_RESOURCE_GROUP",
        "AZURE_BUILD_RESOURCE_GROUP",
        "AZURE_GALLERY",
        "AZURE_IMAGE_DEFINITION",
    ):
        monkeypatch.setenv(key, "same")
    with pytest.raises(ValueError, match="separate"):
        cloud.config("azure")


def test_command_boundary_output_and_failure(tmp_path):
    assert (
        cloud.command(["python3", "-c", 'print("synthetic")'], tmp_path, output=True)
        == "synthetic"
    )
    assert cloud.command(["python3", "-c", "pass"], tmp_path) == ""
    with pytest.raises(subprocess.CalledProcessError):
        cloud.command(["python3", "-c", "raise SystemExit(7)"], tmp_path)


def test_release_pagination(monkeypatch):
    calls = []

    def page(method, endpoint):
        calls.append(endpoint)
        return (
            [{"body": "unrelated"}] * 100
            if endpoint.endswith("&page=1")
            else [{"body": "<!-- runner-images:cloud:example -->"}]
        )

    monkeypatch.setattr(cloud, "gh", page)
    assert cloud.releases("example")
    assert calls[-1].endswith("&page=2")


def test_recovery_refuses_wrong_destination_and_bad_checksum(fixture):
    root, provider, state = fixture
    cloud.plan(root, provider)
    cloud.build(root, provider)
    signed(root, provider)
    cloud.publish(root, provider)
    import shutil

    shutil.rmtree(root / "qualifications")
    path = cloud.state_path(root, provider).parent / "image.json"
    original = json.loads(path.read_text())
    state["image_asset"] = json.dumps({**original, "provider": "unknown"})
    with pytest.raises(ValueError, match="inputs"):
        cloud.plan(root, provider)
    # A parseable but differently serialized receipt must also match its asset hash.
    state["image_asset"] = json.dumps(original)
    with pytest.raises(ValueError, match="checksum"):
        cloud.plan(root, provider)


def test_cleanup_reports_stalled_deletion_and_attempts_other_resources(fixture):
    root, provider, state = fixture
    cloud.plan(root, provider)
    current = json.loads(cloud.state_path(root, provider).read_text())
    tags = {"owner": "runner-images", "build": current["build_id"]}
    state["fail"] = "cleanup"
    if provider == "gcp":
        state["resources"]["instances"] = [{"labels": tags}]
        state["resources"]["disks"] = [{"labels": tags}]
    else:
        state["resources"]["resources"] = [{"id": "example-owned-vm", "tags": tags}]
    with pytest.raises(RuntimeError):
        cloud.cleanup(root, provider)
    if provider == "gcp":
        assert any("instances" in c and "delete" in c for c in state["commands"])
        assert any("disks" in c and "delete" in c for c in state["commands"])
    else:
        assert sum(c[:3] == ["az", "resource", "list"] for c in state["commands"]) == 2


@pytest.mark.parametrize(
    "component,field,version",
    [
        ("node", "version", "v24.0.1"),
        ("git-lfs", "net_version", "v0.63.0"),
        ("git-lfs", "compress_version", "v1.20.5"),
    ],
)
def test_new_tool_release_rebuilds_without_a_new_base_or_recipe(
    fixture, component, field, version
):
    root, provider, state = fixture
    state["toolchains"]["git-lfs"] = {
        "version": "v3.8.1",
        "net_version": "v0.62.0",
        "compress_version": "v1.20.4",
    }
    cloud.plan(root, provider)
    cloud.build(root, provider)
    signed(root, provider)
    cloud.publish(root, provider)
    assert cloud.plan(root, provider)["needed"] == "false"
    previous = json.loads(cloud.state_path(root, provider).read_text())["input_id"]
    state["toolchains"][component][field] = version
    assert cloud.plan(root, provider)["needed"] == "true"
    assert (
        json.loads(cloud.state_path(root, provider).read_text())["input_id"] != previous
    )
    cloud.build(root, provider)
    folder = cloud.state_path(root, provider).parent
    assert (
        json.loads((folder / "variables.pkrvars.json").read_text())["toolchains"]
        == state["toolchains"]
    )
    assert json.loads((folder / "toolchains.json").read_text()) == state["toolchains"]


def test_build_rejects_a_guest_that_changed_planned_tool_versions(fixture):
    root, provider, state = fixture
    cloud.plan(root, provider)
    planned = json.loads(
        (cloud.state_path(root, provider).parent / "toolchains-plan.json").read_text()
    )
    planned["node"]["version"] = "v24.0.1"
    (cloud.state_path(root, provider).parent / "toolchains-plan.json").write_text(
        json.dumps(planned)
    )
    # Simulate a guest independently choosing a different upstream version.
    original = cloud.command

    def commands(args, root, *, output=False):
        result = original(args, root, output=output)
        if args[:2] == ["packer", "build"]:
            (cloud.state_path(root, provider).parent / "toolchains.json").write_text(
                json.dumps(state["toolchains"])
            )
        return result

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cloud, "command", commands)
        with pytest.raises(ValueError, match="differ from the planned"):
            cloud.build(root, provider)
    assert not (cloud.state_path(root, provider).parent / "image.json").exists()


def test_publication_rechecks_qualified_cloud_identity(fixture, monkeypatch):
    root, provider, external = fixture
    cloud.plan(root, provider)
    cloud.build(root, provider)
    signed(root, provider)
    original = cloud.command

    def command(args, root, *, output=False):
        result = original(args, root, output=output)
        if args[0] in ("gcloud", "az") and (
            "describe" in args or ("show" in args and "image-version" in args)
        ):
            image = json.loads(result)
            image["id"] = "changed-resource-identity"
            return json.dumps(image)
        return result

    monkeypatch.setattr(cloud, "command", command)
    with pytest.raises(ValueError, match="identity changed"):
        cloud.publish(root, provider)
    assert not external["releases"]


def test_failed_captured_command_keeps_diagnostic_output(tmp_path, capsys):
    with pytest.raises(subprocess.CalledProcessError):
        cloud.command(
            [
                "python3",
                "-c",
                'print("synthetic version failure"); raise SystemExit(7)',
            ],
            tmp_path,
            output=True,
        )
    assert "synthetic version failure" in capsys.readouterr().err


def test_controller_update_preserves_prior_nvme_version_boundary():
    definition = {
        "features": [
            {
                "name": "DiskControllerTypes",
                "value": "NVMe",
                "startsAtVersion": "25.1.0",
            }
        ]
    }
    before = copy.deepcopy(definition)
    with pytest.raises(ValueError, match="NVMe-only"):
        cloud.controller_update(definition, "26.11.0")
    assert definition == before


def test_controller_update_rejects_version_before_declared_support():
    definition = {
        "features": [
            {
                "name": "DiskControllerTypes",
                "value": "SCSI,NVMe",
                "startsAtVersion": "26.12.0",
            }
        ]
    }
    with pytest.raises(ValueError, match="precedes"):
        cloud.controller_update(definition, "26.11.0")
