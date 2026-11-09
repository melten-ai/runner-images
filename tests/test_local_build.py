import fcntl
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_registry import registry_fixture

from runner_images import local_build, registry
from runner_images.qualification import digest, finalize, qualify, record
from runner_images.rebuild import input_id


@pytest.fixture
def local(example, monkeypatch):
    root, seed, image_directory, metadata = example
    workflow = root / ".github/workflows/release-tart.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("synthetic finalizer")
    (seed / "sbom.syft.json").write_text(
        json.dumps({"artifacts": [{"name": "example-tool"}]})
    )
    (seed / "sbom.spdx.json").write_text(
        json.dumps({"spdxVersion": "SPDX-2.3", "packages": [{"name": "example-tool"}]})
    )
    monkeypatch.setattr(local_build.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(local_build.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(local_build.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "local config"))
    state = {
        "calls": [],
        "runs": [],
        "fail": "",
        "dirty": False,
        "occupied": False,
        "release_done": False,
    }

    def invoke(command, directory, env, *, output=False):
        state["calls"].append(command)
        if command[:3] == ["gh", "auth", "token"]:
            return "synthetic-secret"
        if command[:3] == ["gh", "api", "user"]:
            return "example-user"
        if command[:3] == ["git", "status", "--porcelain"]:
            return " M source" if state["dirty"] else ""
        if command[:2] == ["git", "rev-parse"]:
            return metadata["source_commit"]
        if command[:2] == ["tart", "list"]:
            if state["occupied"]:
                state["occupied"] = False
                return json.dumps([{"State": "running"}] * 2)
            return "[]"
        if command[:2] == ["bash", "macos/build-image.sh"]:
            image = command[2]
            bundle = root / "artifacts/tart" / image
            bundle.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(seed, bundle)
            if state["fail"] == "build":
                raise subprocess.CalledProcessError(1, command)
            qualify(
                bundle, image_directory, {**metadata, "image": image, "run_url": ""}
            )
            finalize(bundle, image_directory)
        if command[0] == "grype" and state["fail"] == "scan":
            raise subprocess.CalledProcessError(1, command)
        if command[:3] == ["git", "checkout", "-B"] and command[-1] == "origin/main":
            if state.get("new_inputs"):
                workflow.write_text("updated synthetic finalizer")
        if command[:2] == ["python3", "scripts/publish-candidate.py"]:
            if state["fail"] == "upload":
                raise subprocess.CalledProcessError(1, command)
            bundle = Path(command[2])
            registry_fixture(
                monkeypatch,
                digest(bundle / "qualification.json"),
                input_id(root, "xcode"),
            )
            monkeypatch.setattr(registry.subprocess, "run", lambda *a, **kw: None)
            registry.publish(bundle, root, image_directory)
        if command[:2] == ["bash", "scripts/commit-qualification.sh"]:
            bundle = Path(command[2])
            destination = record(bundle, root, metadata["source_commit"])
            registry.record_publication(bundle, root, destination)
            if state["fail"] == "record":
                raise subprocess.CalledProcessError(1, command)
        if command[:3] == ["gh", "run", "list"]:
            return "[]" if state.get("invisible") else json.dumps(state["runs"])
        if command[:3] == ["gh", "workflow", "run"]:
            key = command[-1].split("=", 1)[1]
            state["runs"] = [
                {
                    "databaseId": 12345,
                    "displayTitle": f"Release Tart {key}",
                    "status": "in_progress",
                    "conclusion": "",
                }
            ]
        if command[:3] == ["gh", "run", "rerun"]:
            state["runs"][0]["status"] = "in_progress"
        if command[:3] == ["gh", "run", "watch"]:
            if state["fail"] == "release":
                state["runs"][0].update(status="completed", conclusion="failure")
                raise subprocess.CalledProcessError(1, command)
            state["runs"][0].update(status="completed", conclusion="success")
            path = next((root / "qualifications/tart/xcode").glob("*/publication.json"))
            released = {
                **json.loads(path.read_text()),
                "status": "released",
                "url": "https://example.com/release",
                "qualification_sha256": path.parent.name,
            }
            path.with_name("release.json").write_text(json.dumps(released))
        return ""

    monkeypatch.setattr(local_build, "invoke", invoke)
    return root, state


def count(state, prefix):
    return sum(command[: len(prefix)] == prefix for command in state["calls"])


def test_one_invocation_finishes_and_duplicate_invocation_is_noop(local, capsys):
    root, state = local
    state["occupied"] = True
    local_build.rebuild(root, "xcode")
    journal = json.loads((root / "artifacts/rebuild.json").read_text())
    assert journal["done"] and journal["recorded"]
    assert count(state, ["bash", "macos/build-image.sh"]) == 1
    assert count(state, ["gh", "workflow", "run"]) == 1
    scan = next(command for command in state["calls"] if command[0] == "grype")
    assert "sbom.syft.json" in " ".join(scan)
    assert scan[scan.index("--config") + 1] == "macos/grype.json"
    assert scan[scan.index("--file") + 1].endswith("/vulnerabilities.json")
    local_build.rebuild(root, "xcode")
    assert count(state, ["bash", "macos/build-image.sh"]) == 1
    assert count(state, ["gh", "workflow", "run"]) == 1
    output = capsys.readouterr().out
    assert "Already released" in output
    assert "synthetic-secret" not in output
    assert all(
        "synthetic-secret" not in " ".join(command) for command in state["calls"]
    )


@pytest.mark.parametrize("failure", ["build", "upload", "record", "release"])
def test_repeating_same_command_resumes_failed_stage_without_duplicate_release(
    local, failure
):
    root, state = local
    state["fail"] = failure
    with pytest.raises(subprocess.CalledProcessError):
        local_build.rebuild(root, "xcode")
    first = json.loads((root / "artifacts/rebuild.json").read_text())
    assert not first.get("done")
    state["fail"] = ""
    local_build.rebuild(root, "xcode")
    assert json.loads((root / "artifacts/rebuild.json").read_text())["done"]
    assert count(state, ["bash", "macos/build-image.sh"]) == (
        2 if failure == "build" else 1
    )
    assert count(state, ["gh", "workflow", "run"]) == 1
    if failure == "release":
        assert count(state, ["gh", "run", "rerun"]) == 1


def test_host_source_and_concurrent_invocation_guard(local, monkeypatch):
    root, state = local
    with pytest.raises(ValueError, match="unknown"):
        local_build.rebuild(root, "unknown")
    monkeypatch.setattr(local_build.platform, "system", lambda: "Linux")
    with pytest.raises(ValueError, match="Apple Silicon"):
        local_build.rebuild(root, "xcode")
    monkeypatch.setattr(local_build.platform, "system", lambda: "Darwin")
    (root / "artifacts").mkdir()
    with (root / "artifacts/rebuild.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            local_build.rebuild(root, "xcode")
    state["dirty"] = True
    with pytest.raises(ValueError, match="uncommitted"):
        local_build.rebuild(root, "xcode")
    assert not count(state, ["bash", "macos/build-image.sh"])


def test_lost_dispatch_response_reconnects_to_existing_run(local):
    root, state = local
    state["fail"] = "release"
    with pytest.raises(subprocess.CalledProcessError):
        local_build.rebuild(root, "xcode")
    journal = root / "artifacts/rebuild.json"
    value = json.loads(journal.read_text())
    value.pop("dispatched")
    journal.write_text(json.dumps(value))
    state["fail"] = ""
    local_build.rebuild(root, "xcode")
    assert count(state, ["gh", "workflow", "run"]) == 1


def test_invoke_streaming_and_machine_output(tmp_path):
    assert (
        local_build.invoke(
            ["python3", "-c", 'print("synthetic")'], tmp_path, {}, output=True
        )
        == "synthetic"
    )
    assert local_build.invoke(["python3", "-c", "pass"], tmp_path, {}) == ""


def test_dispatch_visibility_timeout_preserves_request_and_reconnects(
    local, monkeypatch
):
    root, state = local
    state["invisible"] = True
    times = iter([0, 0, 121])
    monkeypatch.setattr(local_build.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError, match="requested"):
        local_build.rebuild(root, "xcode")
    assert count(state, ["gh", "workflow", "run"]) == 1
    state["invisible"] = False
    local_build.rebuild(root, "xcode")
    assert count(state, ["gh", "workflow", "run"]) == 1
    assert json.loads((root / "artifacts/rebuild.json").read_text())["done"]


def test_consumer_failure_retries_without_rebuilding_or_duplicate_release(
    local, monkeypatch
):
    root, state = local
    config = root / "local config/runner-images/melten-ai/xcode/after-release.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(["/example/consumer", "argument with spaces"]))
    original = local_build.invoke
    calls = []
    fail = True

    def invoke(command, directory, env, *, output=False):
        if command[0] == "/example/consumer":
            calls.append(command)
            assert (
                json.loads((Path(command[-1]) / "release.json").read_text())["status"]
                == "released"
            )
            if fail:
                raise subprocess.CalledProcessError(1, command)
            return ""
        return original(command, directory, env, output=output)

    monkeypatch.setattr(local_build, "invoke", invoke)
    with pytest.raises(subprocess.CalledProcessError):
        local_build.rebuild(root, "xcode")
    assert not json.loads((root / "artifacts/rebuild.json").read_text()).get("done")
    fail = False
    local_build.rebuild(root, "xcode")
    local_build.rebuild(root, "xcode")
    assert (
        len(calls) == 3
    )  # Includes the Already released path: late registration works.
    assert all(c[1] == "argument with spaces" for c in calls)
    assert count(state, ["bash", "macos/build-image.sh"]) == 1
    assert count(state, ["gh", "workflow", "run"]) == 1
    assert json.loads((root / "artifacts/rebuild.json").read_text())["done"]


@pytest.mark.parametrize("command", [[], "echo unsafe", [""], [42]])
def test_invalid_consumer_registration_fails_after_release(local, command):
    root, _ = local
    config = root / "local config/runner-images/melten-ai/xcode/after-release.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(command))
    with pytest.raises(ValueError, match="command array"):
        local_build.rebuild(root, "xcode")
    assert not json.loads((root / "artifacts/rebuild.json").read_text()).get("done")


def test_missing_release_receipt_never_invokes_consumer(local, monkeypatch):
    root, _ = local
    original = local_build.invoke

    def invoke(command, directory, env, *, output=False):
        result = original(command, directory, env, output=output)
        if command[:3] == ["gh", "run", "watch"]:
            path = next((root / "qualifications/tart/xcode").glob("*/release.json"))
            value = json.loads(path.read_text())
            value["qualification_sha256"] = "wrong"
            path.write_text(json.dumps(value))
        return result

    monkeypatch.setattr(local_build, "invoke", invoke)
    with pytest.raises(ValueError, match="mismatched"):
        local_build.rebuild(root, "xcode")
    assert not json.loads((root / "artifacts/rebuild.json").read_text()).get("done")


def test_already_published_on_another_host_records_local_completion(local):
    root, state = local
    local_build.rebuild(root, "xcode")
    journal = root / "artifacts/rebuild.json"
    journal.unlink()
    local_build.rebuild(root, "xcode")
    assert json.loads(journal.read_text())["done"]
    assert count(state, ["bash", "macos/build-image.sh"]) == 1


def test_late_consumer_failure_records_pending_and_can_resume(local, monkeypatch):
    root, state = local
    local_build.rebuild(root, "xcode")
    original = local_build.consume_release
    monkeypatch.setattr(
        local_build,
        "consume_release",
        lambda *a: (_ for _ in ()).throw(ValueError("consumer failed")),
    )
    with pytest.raises(ValueError, match="consumer failed"):
        local_build.rebuild(root, "xcode")
    journal = root / "artifacts/rebuild.json"
    assert not json.loads(journal.read_text())["done"]
    monkeypatch.setattr(local_build, "consume_release", original)
    local_build.rebuild(root, "xcode")
    assert json.loads(journal.read_text())["done"]
    assert count(state, ["bash", "macos/build-image.sh"]) == 1


def test_empty_xdg_config_home_uses_standard_home_fallback(local, monkeypatch):
    root, state = local
    monkeypatch.setattr(local_build.Path, "home", lambda: root)
    config = root / ".config/runner-images/melten-ai/xcode/after-release.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(["/example/consumer"]))
    local_build.consume_release(
        root, "xcode", root / "release.json", {"XDG_CONFIG_HOME": ""}
    )
    assert state["calls"][-1] == ["/example/consumer", str(root.resolve())]


def test_bootstrap_uses_own_factory_checkout_without_touching_other_factory(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    state = tmp_path / "state"
    other = state / "runner-images/example-owner/xcode/source"
    other.mkdir(parents=True)
    (other / "marker").write_text("other factory")
    scripts = {
        "uname": """#!/bin/bash
if [[ $1 == -s ]]; then echo Darwin; else echo arm64; fi
""",
        "gh": """#!/bin/bash
if [[ $1 == auth && $2 == status ]]; then
  [[ $3 == --hostname && $4 == github.com ]] || exit 1
fi
if [[ $1 == repo ]]; then
  [[ $3 == https://github.com/melten-ai/runner-images.git ]] || exit 9
  mkdir -p "$4/.git"
fi
""",
        "git": "#!/bin/bash\nexit 0\n",
        "python3": """#!/bin/bash
if [[ $1 != -c ]]; then pwd >"$TEST_BOOTSTRAP_RESULT"; fi
""",
    }
    for name in ("tart", "jq", "grype"):
        scripts[name] = "#!/bin/bash\nexit 0\n"
    for name, content in scripts.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o755)
    result = tmp_path / "result"
    env = dict(
        os.environ,
        PATH=str(tools) + ":" + os.environ["PATH"],
        XDG_STATE_HOME=str(state),
        TEST_BOOTSTRAP_RESULT=str(result),
    )
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["bash", str(root / "macos/rebuild.sh"), "xcode"], env=env, check=True
    )
    assert (
        Path(result.read_text().strip())
        == state / "runner-images/melten-ai/xcode/source"
    )
    assert (other / "marker").read_text() == "other factory"


def test_other_factory_callback_is_never_invoked(local):
    root, state = local
    config = root / "local config"
    other = config / "runner-images/example-owner/xcode/after-release.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps(["/example/other-consumer"]))
    env = {"XDG_CONFIG_HOME": str(config)}
    local_build.consume_release(root, "xcode", root / "release.json", env)
    assert not state["calls"]
    own = config / "runner-images/melten-ai/xcode/after-release.json"
    own.parent.mkdir(parents=True)
    own.write_text(json.dumps(["/example/own-consumer"]))
    local_build.consume_release(root, "xcode", root / "release.json", env)
    assert state["calls"] == [["/example/own-consumer", str(root.resolve())]]


def test_fixed_inputs_replace_failed_unpublished_build(local):
    root, state = local
    state["fail"] = "scan"
    with pytest.raises(subprocess.CalledProcessError):
        local_build.rebuild(root, "xcode")
    journal = root / "artifacts/rebuild.json"
    previous = json.loads(journal.read_text())
    state["new_inputs"] = True
    state["fail"] = ""
    local_build.rebuild(root, "xcode")
    current = json.loads(journal.read_text())
    assert current["done"]
    assert current["image"] != previous["image"]
    assert (root / "artifacts/tart" / previous["image"] / "qualification.json").exists()
    assert any(
        json.loads(p.read_text()) == previous
        for p in (root / "artifacts/recovery").rglob("rebuild.json")
    )
    assert count(state, ["bash", "macos/build-image.sh"]) == 2


def test_pending_published_consumer_finishes_original_receipt_before_new_inputs(
    local, monkeypatch
):
    root, state = local
    local_build.rebuild(root, "xcode")
    calls = []
    fail = True

    def consume(root, profile, receipt, env):
        calls.append(str(receipt))
        if fail:
            raise ValueError("consumer failed")

    monkeypatch.setattr(local_build, "consume_release", consume)
    with pytest.raises(ValueError, match="consumer failed"):
        local_build.rebuild(root, "xcode")
    receipt = json.loads((root / "artifacts/rebuild.json").read_text())[
        "released_receipt"
    ]
    state["new_inputs"] = True
    fail = False
    before = len(state["calls"])
    local_build.rebuild(root, "xcode")
    assert calls == [str(root / receipt)] * 2
    assert not any(c[:2] == ["git", "fetch"] for c in state["calls"][before:])
    assert count(state, ["bash", "macos/build-image.sh"]) == 1
    assert json.loads((root / "artifacts/rebuild.json").read_text())["done"]


def test_invoke_interrupt_allows_child_cleanup(tmp_path):
    import signal
    import sys
    import time

    parent = tmp_path / "parent.py"
    child = tmp_path / "child.py"
    ready = tmp_path / "ready"
    cleaned = tmp_path / "cleaned"
    child.write_text(
        "import signal,time\nfrom pathlib import Path\n"
        "def stop(*_):\n time.sleep(.8)\n"
        f" Path({str(cleaned)!r}).touch()\n raise SystemExit(130)\n"
        "signal.signal(signal.SIGINT,stop)\n"
        f"Path({str(ready)!r}).touch()\n"
        "while True: time.sleep(.05)\n"
    )
    parent.write_text(
        "from pathlib import Path\nfrom runner_images.local_build import invoke\n"
        "try:\n"
        f" invoke([{sys.executable!r},{str(child)!r}],Path({str(tmp_path)!r}),{{}})\n"
        "except KeyboardInterrupt: pass\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    process = subprocess.Popen(
        [sys.executable, str(parent)], env=env, start_new_session=True
    )
    try:
        deadline = time.monotonic() + 10
        while (
            not ready.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert ready.exists()
        os.killpg(process.pid, signal.SIGINT)
        assert process.wait(timeout=10) == 0
        assert cleaned.exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@pytest.mark.parametrize("phase", ["failed", "released", "recorded", "uploaded"])
def test_bootstrap_refreshes_recoverable_source_and_preserves_publication(
    local, tmp_path, phase
):
    import sys

    root, _ = local
    tools = tmp_path / "bootstrap-bin"
    tools.mkdir()
    state = tmp_path / "bootstrap-state"
    checkout = state / "runner-images/melten-ai/xcode/source"
    checkout.parent.mkdir(parents=True)
    checkout.symlink_to(root, target_is_directory=True)
    (root / ".git").mkdir()
    journal = root / "artifacts/rebuild.json"
    journal.parent.mkdir(exist_ok=True)
    value = {"image": "example-image", "source": "a" * 40, "done": False}
    if phase == "released":
        value["released_receipt"] = "qualifications/tart/xcode/example/release.json"
    if phase == "recorded":
        value["recorded"] = True
    if phase == "uploaded":
        publication = root / "artifacts/tart/example-image/publication.json"
        publication.parent.mkdir(parents=True)
        publication.write_text("{}")
    journal.write_text(json.dumps(value))
    before = input_id(root, "xcode")
    log = tmp_path / "git-commands"
    scripts = {
        "uname": (
            "#!/bin/bash\nif [[ $1 == -s ]]; then echo Darwin; else echo arm64; fi\n"
        ),
        "gh": '#!/bin/bash\n[[ $* == "auth status --hostname github.com" ]]\n',
        "git": '#!/bin/bash\necho "$*" >>"$TEST_GIT_LOG"\n',
        "python3": (
            "#!/bin/bash\n"
            "if [[ $1 == scripts/rebuild-macos.py ]]; then exit 0; fi\n"
            'exec "$TEST_PYTHON" "$@"\n'
        ),
    }
    for tool in ("tart", "jq", "grype"):
        scripts[tool] = "#!/bin/bash\nexit 0\n"
    for name, content in scripts.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o755)
    subprocess.run(
        [
            "bash",
            str(Path(__file__).resolve().parents[1] / "macos/rebuild.sh"),
            "xcode",
        ],
        env=dict(
            os.environ,
            PATH=str(tools) + os.pathsep + os.environ["PATH"],
            PYTHONPATH=str(Path(__file__).resolve().parents[1]),
            XDG_STATE_HOME=str(state),
            TEST_PYTHON=sys.executable,
            TEST_GIT_LOG=str(log),
        ),
        check=True,
        capture_output=True,
    )
    saved = json.loads(journal.read_text())
    assert all(saved[key] == item for key, item in value.items())
    assert log.exists() == (phase in ("failed", "released"))
    if phase == "failed":
        assert saved["input_id"] == before
