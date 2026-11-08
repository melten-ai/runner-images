import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_build_lifecycle_and_failure_cleanup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    tools = tmp_path / "bin"
    tools.mkdir()
    state = tmp_path / "state.json"
    state.write_text(json.dumps(["base", "unrelated-image"]))
    tart = tools / "tart"
    tart.write_text("""#!/usr/bin/env python3
import sys, os, json, time, shutil
from pathlib import Path
a=sys.argv[1:]
state=Path(os.environ["TEST_TART_STATE"])
images=json.loads(state.read_text())
command=a[0]
if command=="list":
    if "--format" in a:
        status="running" if os.environ.get("TEST_FAIL_STAGE")=="capacity" else "stopped"
        print(json.dumps([{"State":status} for _ in images]))
    else: print("\\n".join(images))
    sys.exit(0)
home=Path(os.environ["TART_HOME"])/"vms"
if command=="clone":
    images.append(a[2])
    directory=home/a[2]; directory.mkdir(parents=True)
    for name in ["config.json","disk.img","nvram.bin"]:
        (directory/name).write_bytes(b"synthetic-image-bytes")
    (directory/"config.json").write_text(json.dumps({"cpuCount":4,"macAddress":"02:00:00:00:00:01"}))
if command=="delete":
    images.remove(a[1]); shutil.rmtree(home/a[1])
if command=="rename":
    if os.environ.get("TEST_FAIL_STAGE")=="rename": sys.exit(4)
    images.remove(a[1]); images.append(a[2])
    (home/a[1]).rename(home/a[2])
    if os.environ.get("TEST_FAIL_STAGE")=="image-changed":
        (home/a[2]/"disk.img").write_bytes(b"changed-after-comparison")
if command=="stop": (state.parent/(a[1]+".stopped")).touch()
if command=="run":
    if os.environ.get("TEST_FAIL_STAGE")=="boot": sys.exit(7)
    while not (state.parent/(a[1]+".stopped")).exists(): time.sleep(.01)
    sys.exit(0)
if command=="exec":
    if os.environ.get("TEST_FAIL_STAGE")=="boot" and "/usr/bin/true" in a: sys.exit(1)
    if "cmdline-tools" in " ".join(a): print("21 12266719 35 35.0.0")
    if "github-macos-toolset.json" in " ".join(a) and "cat " in " ".join(a):
        print('{"example":"toolset"}')
    body=sys.stdin.read() if "-i" in a else ""
    if "brew update" in body:
        if os.environ.get("TEST_FAIL_STAGE")=="provision": sys.exit(5)
        print("/example/bin:/usr/bin:/bin")
    if "syft scan dir:/" in body:
        print(json.dumps({"spdxVersion":"SPDX-2.3", "packages":[{"name":"synthetic"}]}))
        if os.environ.get("TEST_FAIL_STAGE")=="sbom": sys.exit(8)
    if "Missing hosted tool cache entry" in body:
        print("synthetic native checks passed")
        if os.environ.get("TEST_FAIL_STAGE")=="verify": sys.exit(6)
    if "json.dumps(collect()" in body:
        print(json.dumps({"schema_version":1,"os":"darwin","arch":"arm64",
            "image_os":"macos26","image_version":"example-build",
            "records":["cmd bash"]}))
state.write_text(json.dumps(images))
""")
    commands = {
        "uname": 'if [ "$1" = -s ]; then echo Darwin; else echo arm64; fi',
        "networksetup": "echo 'Device: en0'",
        "route": "echo 'interface: en0'",
        "ifconfig": "echo 'status: active'",
        "ipconfig": "echo '192.0.2.10'",
        "curl": 'echo \'{"tag_name":"v2.338.2","assets":[{'
        '"name":"actions-runner-osx-arm64-2.338.2.tar.gz","digest":"sha256:'
        + "a" * 64
        + "\"}]}'",
    }
    commands["git"] = """
case "$*" in
 *rev-parse*) echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
 *status*) if [ "$TEST_FAIL_STAGE" = dirty ]; then echo " M recipe"; fi ;;
esac
"""
    reference = tmp_path / "reference.json"
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text("")
    for name, script in commands.items():
        (tools / name).write_text("#!/bin/sh\n" + script + "\n")
    for path in tools.iterdir():
        path.chmod(0o755)
    for failure in (
        "dirty",
        "capacity",
        "boot",
        "provision",
        "verify",
        "sbom",
        "parity",
        "rename",
        "image-changed",
        "",
        "android",
    ):
        reference.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "os": "darwin",
                    "arch": "arm64",
                    "image_os": "macos26",
                    "image_version": "reference-build",
                    "records": ["cmd bash", "cmd rustc"]
                    if failure == "parity"
                    else ["cmd bash"],
                }
            )
        )
        image = f"test-image-{tmp_path.name}-{failure or 'success'}"
        env = {
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "TEST_TART_STATE": str(state),
            "TART_HOME": str(tmp_path / "tart"),
            "TEST_FAIL_STAGE": "" if failure == "android" else failure,
        }
        result = subprocess.run(
            [
                "bash",
                str(root / "macos/build-image.sh"),
                image,
                "base",
                "xcode-android" if failure == "android" else "xcode",
                "/Users/admin/actions-runner",
                str(reference),
                str(allowlist),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        images = json.loads(state.read_text())
        assert "base" in images and "unrelated-image" in images
        assert not any("-build-" in name for name in images)
        bundle = root / "artifacts/tart" / image
        receipt = bundle / "qualification.json"
        if failure and failure != "android":
            assert result.returncode != 0, result.stdout + result.stderr
            assert image not in images
            assert not receipt.exists()
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert image in images
            assert json.loads(receipt.read_text())["status"] == "passed"
            assert json.loads((bundle / "inventory.json").read_text())["records"] == [
                "cmd bash"
            ]
            repeated = subprocess.run(
                ["bash", str(root / "macos/build-image.sh"), image, "base"],
                env=env,
                capture_output=True,
            )
            assert repeated.returncode != 0
            assert json.loads(state.read_text()) == images
        shutil.rmtree(bundle, ignore_errors=True)


@pytest.mark.parametrize(
    "arguments",
    [
        ["../unsafe"],
        ["base", "base"],
        ["example", "base", "unknown-profile"],
        ["example", "base", "xcode", "/"],
    ],
)
def test_invalid_build_inputs(arguments):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", str(root / "macos/build-image.sh"), *arguments], capture_output=True
    )
    assert result.returncode != 0


def test_runner_installer_rejects_unsafe_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", str(root / "macos/image/install-runner.sh"), "2.338.2", "/", "a" * 64],
        capture_output=True,
    )
    assert result.returncode != 0
    assert b"below the guest home" in result.stderr


@pytest.mark.parametrize(
    "base,refresh",
    [
        ("ghcr.io/example-org/runner:latest", True),
        ("ghcr.io/example-org/runner:26.11", True),
        ("ghcr.io/example-org/runner@sha256:" + "a" * 64, False),
        ("local-base", False),
    ],
)
def test_mutable_base_refreshes_before_clone(tmp_path, base, refresh):
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / "source"
    (root / "macos").mkdir(parents=True)
    for name in ("build-image.sh", "network.sh"):
        shutil.copyfile(source / "macos" / name, root / "macos" / name)
    tools = tmp_path / "bin"
    tools.mkdir()
    cache = tmp_path / "cache"
    cache.write_text("cached-old")
    observed = tmp_path / "observed"
    (tools / "tart").write_text("""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args[0]=='list':
    result=json.dumps([{'State':'stopped'}])
    print(result if '--format' in args else os.environ['TEST_BASE'])
elif args[0]=='pull':
    Path(os.environ['TEST_CACHE']).write_text('remote-new')
elif args[0]=='clone':
    Path(os.environ['TEST_OBSERVED']).write_text(Path(os.environ['TEST_CACHE']).read_text())
    sys.exit(73)  # Stop before creating or booting any VM.
""")
    (tools / "git").write_text(
        '#!/bin/bash\ncase "$*" in *rev-parse*) echo synthetic-commit;; esac\n'
    )
    (tools / "uname").write_text(
        "#!/bin/bash\nif [[ $1 == -s ]]; then echo Darwin; else echo arm64; fi\n"
    )
    for tool in tools.iterdir():
        tool.chmod(0o755)
    reference = tmp_path / "reference.json"
    reference.write_text("{}")
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text("")
    result = subprocess.run(
        [
            "bash",
            str(root / "macos/build-image.sh"),
            "example-image",
            base,
            "xcode",
            "/Users/admin/actions-runner",
            str(reference),
            str(allowlist),
        ],
        env=dict(
            os.environ,
            PATH=str(tools) + os.pathsep + os.environ["PATH"],
            TEST_BASE=base,
            TEST_CACHE=str(cache),
            TEST_OBSERVED=str(observed),
        ),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 73, result.stderr
    assert observed.read_text() == ("remote-new" if refresh else "cached-old")
