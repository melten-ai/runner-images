import json
import subprocess
from pathlib import Path

import pytest
from test_registry import registry_fixture

from runner_images import registry, release
from runner_images.qualification import digest, finalize, qualify, record
from runner_images.rebuild import input_id


@pytest.fixture
def uploaded(example, monkeypatch):
    root, bundle, image, metadata = example
    (root / ".gitignore").write_text("artifacts/\n")
    workflow = root / ".github/workflows/release-tart.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("synthetic trusted release workflow")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Example Maintainer")
    git("config", "user.email", "maintainer@example.com")
    git("add", ".")
    git("commit", "-m", "recipe")
    metadata.update(
        source_commit=git("rev-parse", "HEAD"),
        reference_commit=git("rev-parse", "HEAD"),
        run_url="",
        image="macos-26-xcode-local-" + "b" * 32,
    )
    (bundle / "sbom.spdx.json").write_text(
        json.dumps({"spdxVersion": "SPDX-2.3", "packages": [{"name": "example-tool"}]})
    )
    qualify(bundle, image, metadata)
    finalize(bundle, image)
    registry_fixture(
        monkeypatch, digest(bundle / "qualification.json"), input_id(root, "xcode")
    )
    original_run = subprocess.run
    monkeypatch.setattr(registry.subprocess, "run", lambda *args, **kw: None)
    registry.publish(bundle, root, image)
    monkeypatch.setattr(registry.subprocess, "run", original_run)
    target = record(bundle, root, metadata["source_commit"])
    registry.record_publication(bundle, root, target)
    git("add", "qualifications")
    git("commit", "-m", "uploaded image evidence")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return root, str(target.relative_to(root)), metadata, git


def signed_assets(root):
    workspace = root / "artifacts/release"
    for name in (
        "provenance.sigstore.json",
        "sbom.sigstore.json",
        "vulnerabilities.json",
    ):
        (workspace / name).write_text('{"synthetic":"signed validation boundary"}')


@pytest.fixture
def github(monkeypatch):
    state = {"releases": [], "calls": [], "version": "26.11.0", "fail": "", "sha": ""}
    original_run = subprocess.run

    def run(command, **kwargs):
        if command[0] != "gh":
            return original_run(command, **kwargs)
        state["calls"].append(command)
        if command[:3] == ["gh", "release", "create"]:
            notes = Path(command[command.index("--notes-file") + 1]).read_text()
            state["releases"].append(
                {
                    "id": 42,
                    "draft": True,
                    "prerelease": False,
                    "tag_name": command[3],
                    "body": notes,
                    "assets": [],
                    "html_url": "https://example.com/release",
                }
            )
            if state["fail"] == "create":
                raise subprocess.CalledProcessError(1, command)
        if command[:3] == ["gh", "release", "upload"]:
            paths = [Path(value) for value in command[command.index("--clobber") + 1 :]]
            assets = [
                {"name": path.name, "digest": "sha256:" + digest(path)}
                for path in paths
            ]
            if state["fail"] == "asset":
                assets[0]["digest"] = "sha256:" + "0" * 64
            state["releases"][0]["assets"] = assets
        if command[:3] == ["gh", "release", "edit"]:
            state["releases"][0]["draft"] = state["fail"] == "publish"
        return subprocess.CompletedProcess(command, 0)

    def api(method, endpoint, payload=None):
        if "/releases?" in endpoint:
            return state["releases"]
        if "/releases/" in endpoint:
            return state["releases"][0]
        if "/commits/" in endpoint:
            return {"sha": state["sha"]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(release, "gh", api)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: state["version"])
    return state


def test_real_git_preparation_independent_checks_release_and_replay(
    uploaded, monkeypatch
):
    root, key, metadata, _ = uploaded
    result = release.prepare(root, key)
    assert result["source"] == metadata["source_commit"]
    assert result["profile"] == "xcode"
    assert "@sha256:" in result["reference"]
    assert (
        json.loads((root / "artifacts/release/provenance.json").read_text())["builder"]
        == "local-apple-silicon-mac"
    )
    signed_assets(root)
    # Attach the external GitHub model only after real Git preparation.
    github = create_github(monkeypatch)
    github["sha"] = metadata["source_commit"]
    receipt = release.publish_release(root)
    assert receipt["status"] == "released"
    assert receipt["input_id"] == input_id(root, "xcode")
    assert len(github["releases"]) == 1 and not github["releases"][0]["draft"]
    assert release.publish_release(root) == receipt
    assert len(github["releases"]) == 1
    assert json.loads((root / key / "release.json").read_text()) == receipt
    # A caller cannot forge an uploaded-only completion into a released receipt.
    assert (root / key / "publication.json").exists()


# Pytest exposes the same external API model to tests that prepare Git first.
create_github = github.__wrapped__


@pytest.mark.parametrize("failure", ["create", "asset", "publish", "tag"])
def test_failed_publication_keeps_completion_absent_and_replays_same_release(
    uploaded, monkeypatch, failure
):
    root, key, metadata, _ = uploaded
    release.prepare(root, key)
    signed_assets(root)
    state = create_github(monkeypatch)
    state["sha"] = metadata["source_commit"] if failure != "tag" else "0" * 40
    state["fail"] = failure
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        release.publish_release(root)
    assert not (root / key / "release.json").exists()
    state["fail"] = ""
    state["sha"] = metadata["source_commit"]
    release.publish_release(root)
    assert len(state["releases"]) == 1
    assert (root / key / "release.json").exists()


def test_missing_attestations_or_invalid_version_cannot_publish(uploaded, monkeypatch):
    root, key, _, _ = uploaded
    release.prepare(root, key)
    state = create_github(monkeypatch)
    with pytest.raises(ValueError, match="incomplete"):
        release.publish_release(root)
    assert not state["releases"]
    signed_assets(root)
    state["version"] = "invalid-version"
    with pytest.raises(ValueError, match="CalVer"):
        release.publish_release(root)
    assert not state["releases"]


def test_preparation_refuses_untrusted_paths_sources_and_sbom(uploaded):
    root, key, _, git = uploaded
    with pytest.raises(ValueError, match="path"):
        release.qualification_path(root, "../untrusted")
    symbolic = root / "symbolic"
    symbolic.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        release.qualification_path(symbolic, key)
    original = root / key / "qualification.json"
    saved = original.read_text()
    original.write_text("{}")
    with pytest.raises(ValueError, match="content"):
        release.qualification_path(root, key)
    original.write_text(saved)
    (root / key / "sbom.spdx.json").write_text("{}")
    with pytest.raises(ValueError, match="SBOM"):
        release.prepare(root, key)


def test_release_pagination_and_prerelease_refusal(monkeypatch):
    calls = []

    def pages(method, endpoint):
        calls.append(endpoint)
        if endpoint.endswith("&page=1"):
            return [{"body": "unrelated"}] * 100
        return [
            {
                "body": "<!-- runner-images:qualification:example -->",
                "prerelease": False,
            }
        ]

    monkeypatch.setattr(release, "gh", pages)
    assert release.existing_release("example")
    assert "page=2" in calls[-1]
    monkeypatch.setattr(
        release,
        "gh",
        lambda *a: [
            {"body": "<!-- runner-images:qualification:example -->", "prerelease": True}
        ],
    )
    with pytest.raises(ValueError, match="prerelease"):
        release.existing_release("example")


def test_output_contract(tmp_path, monkeypatch):
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    release.write_outputs({"subject": "example", "digest": "sha256:example"})
    assert output.read_text() == "subject=example\ndigest=sha256:example\n"


def test_prepare_rejects_non_main_source_and_invalid_local_identity(uploaded):
    root, key, _, git = uploaded
    original = root / key

    def changed_receipt(**changes):
        receipt = json.loads((original / "qualification.json").read_text())
        receipt["build"].update(changes)
        content = json.dumps(receipt).encode()
        import hashlib

        target = original.parent / hashlib.sha256(content).hexdigest()
        target.mkdir(exist_ok=True)
        (target / "qualification.json").write_bytes(content)
        return str(target.relative_to(root))

    with pytest.raises(ValueError, match="local-build"):
        release.prepare(root, changed_receipt(run_url="https://example.com/untrusted"))
    with pytest.raises(ValueError, match="source commit"):
        release.prepare(root, changed_receipt(source_commit="main"))
    git("checkout", "-b", "untrusted-source")
    git("commit", "--allow-empty", "-m", "not on main")
    side_commit = git("rev-parse", "HEAD")
    git("checkout", "main")
    with pytest.raises(subprocess.CalledProcessError):
        release.prepare(root, changed_receipt(source_commit=side_commit))
