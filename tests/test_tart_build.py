import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def prepare_guest_android_layout(home: Path, layout: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    if layout == "absent":
        return
    tools = home / "android-sdk/cmdline-tools"
    if layout in ("present", "present_canonical"):
        (tools / "23.0/bin").mkdir(parents=True)
        (tools / "23.0/source.properties").write_text("Pkg.Revision=23.0\n")
        sdkmanager = tools / "23.0/bin/sdkmanager"
        sdkmanager.write_text("#!/bin/sh\necho 23.0\n")
        sdkmanager.chmod(0o755)
        (tools / "latest").symlink_to("23.0")
        return
    if layout == "unsafe_escape":
        tools.mkdir(parents=True)
        outside = home / "outside"
        (outside / "bin").mkdir(parents=True)
        (outside / "bin/sdkmanager").write_text("#!/bin/sh\necho 1\n")
        (outside / "bin/sdkmanager").chmod(0o755)
        (tools / "latest").symlink_to("../../outside")
        return
    if layout == "unsafe":
        tools.mkdir(parents=True)
        (tools / "latest").symlink_to("99.0")
        return
    raise ValueError(f"unknown layout {layout}")


def install_gh_cli(tools):
    gh = tools / "gh"
    gh.write_text("""#!/usr/bin/env python3
import json,os,sys
if "repos/actions/runner/releases/latest" in sys.argv:
    print(json.dumps({"tag_name":"v2.338.2","assets":[{
        "name":"actions-runner-osx-arm64-2.338.2.tar.gz",
        "digest":"sha256:"+"a"*64}]}))
elif "repos/anchore/syft/releases/latest" in sys.argv:
    print(json.dumps({"name":"syft_1.99.0_darwin_arm64.tar.gz",
        "browser_download_url":"https://example.invalid/syft.tar.gz",
        "digest":"sha256:"+"b"*64}))
elif "--method" in sys.argv:
    print(json.dumps({"assets":[{
        "browser_download_url":"https://github.com/Azure/azure-cli/releases/download/"
                               "azure-cli-2.92.0/azure-cli-2.92.0-macos-arm64.tar.gz",
        "url":"https://api.github.com/repos/Azure/azure-cli/releases/assets/123",
        "digest":"sha256:"+os.environ["TEST_CASK_SHA"]}]}))
else: sys.stdout.buffer.write(b"synthetic official Azure archive")
""")
    gh.chmod(0o755)


SYNTHETIC_TART = """#!/usr/bin/env python3
import sys, os, json, time, shutil
from pathlib import Path
a=sys.argv[1:]
state=Path(os.environ["TEST_TART_STATE"])
images=json.loads(state.read_text())
command=a[0]
if command=="list":
    if "--format" in a:
        status="running" if os.environ.get("TEST_FAIL_STAGE")=="capacity" else "stopped"
        print(json.dumps([{"Name":name,
            "State":status if "-build-" not in name else "running"}
            for name in images]))
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
    (state.parent/(a[1]+".stopped")).unlink(missing_ok=True)
    if os.environ.get("TEST_FAIL_STAGE")=="boot": sys.exit(7)
    while not (state.parent/(a[1]+".stopped")).exists(): time.sleep(.01)
    sys.exit(0)
if command=="exec":
    if "/usr/bin/sync" in a: sys.exit(125)
    if "hw.model" in a: print("VirtualMac2,1")
    if "-productVersion" in a: print("26.7.2")
    if "/usr/sbin/softwareupdate" in a:
        if os.environ.get("TEST_FAIL_STAGE")=="os-update": sys.exit(24)
        print("No new software available.")
    if "/opt/homebrew/bin/brew" in a:
        if os.environ.get("TEST_FAIL_STAGE")=="cask": sys.exit(23)
        if "info" in a:
            print(json.dumps({"casks":[{"token":"azure-cli","version":"2.92.0",
                "url":"https://github.com/Azure/azure-cli/releases/download/"
                      "azure-cli-2.92.0/azure-cli-2.92.0-macos-arm64.tar.gz",
                "sha256":os.environ["TEST_CASK_SHA"]}]}))
        elif "--cache" in a:
            print("/Users/admin/Library/Caches/Homebrew/downloads/example.tgz")
    if "/usr/bin/shasum" in a: print(os.environ["TEST_CASK_SHA"]+"  example.tgz")
    if os.environ.get("TEST_FAIL_STAGE")=="boot" and "/usr/bin/true" in a: sys.exit(1)
    if "android.platform_min_version" in " ".join(a): print("21 35 35.0.0")
    if any(".java.arm64.default" in part for part in a): print("21")
    if "github-macos-toolset.json" in " ".join(a) and "cat " in " ".join(a):
        print('{"example":"toolset"}')
    body=sys.stdin.read() if "-i" in a else ""
    if (
        "-s" in a
        and body
        and "detect_android_sdk_state" in body
        and a.index("-s") + 1 < len(a)
        and a[a.index("-s") + 1] == "detect"
    ):
        import subprocess
        guest_home = os.environ.get("TEST_GUEST_HOME", "")
        if not guest_home:
            print("absent")
        else:
            proc = subprocess.run(
                ["bash", "-s", "detect"],
                input=body,
                env={**os.environ, "HOME": guest_home},
                capture_output=True,
                text=True,
                timeout=5,
            )
            print((proc.stdout.strip() or "absent"))
        state.write_text(json.dumps(images))
        sys.exit(0)
    trace=os.environ.get("TEST_ANDROID_TRACE")
    if (
        trace
        and "assert_android_sdk_safe_for_refresh" in body
        and "JAVA_FEATURE_VERSION=" in body
    ):
        Path(trace).write_text("install-android" + chr(10))
    if (
        os.environ.get("TEST_FAIL_STAGE") == "inherited-android"
        and trace
        and body
        and "JAVA_FEATURE_VERSION=" in body
    ):
        sys.exit(31)
    if "brew update" in body:
        assert "refresh_python_cache()" in body, "missing Python cache helper"
        assert "refresh_image_npm()" in body, "missing npm helper"
        if os.environ.get("TEST_FAIL_STAGE")=="provision": sys.exit(5)
        path="/example/bin:/usr/bin:/bin"
        print("invalid" if os.environ.get("TEST_FAIL_STAGE")=="job-path" else path)
    if "verify_guest_agent()" in body:
        stage="agent-refresh" if a[-1]=="refresh" else "agent-verify"
        if os.environ.get("TEST_FAIL_STAGE")==stage: sys.exit(27)
        print("guest_agent_identity=verified")
    if "verify_git_lfs()" in body:
        if os.environ.get("TEST_FAIL_STAGE")=="lfs-refresh": sys.exit(28)
        print("git_lfs_identity=verified")
    if "def rewrite_classpath(" in body:
        failing = os.environ.get("TEST_FAIL_STAGE")
        joined = " ".join(a)
        if failing == "bc-refresh" and "refresh" in joined:
            sys.exit(29)
        if failing == "bc-verify" and "verify" in joined:
            sys.exit(29)
        print("android_bc_dependencies=verified")
    if "SYFT_GOLANG_CAPTURE_SYMBOLS=all" in body:
        if os.environ.get("TEST_FAIL_STAGE")=="sbom": sys.exit(8)
    if "/bin/cat" in a and "/private/tmp/runner-image-sbom/sbom.syft.json" in a:
        print(json.dumps({"artifacts":[{"name":"synthetic"}]}))
    if "/bin/cat" in a and "/private/tmp/runner-image-sbom/sbom.spdx.json" in a:
        if os.environ.get("TEST_FAIL_STAGE")=="sbom-read": sys.exit(9)
        print(json.dumps({"spdxVersion":"SPDX-2.3","packages":[{"name":"synthetic"}]}))
    if "Missing hosted tool cache entry" in body:
        print("synthetic native checks passed")
        if os.environ.get("TEST_FAIL_STAGE")=="verify": sys.exit(6)
    if "json.dumps(collect()" in body:
        print(json.dumps({"schema_version":1,"os":"darwin","arch":"arm64",
            "image_os":"macos26","image_version":"example-build",
            "records":["cmd bash"]}))
state.write_text(json.dumps(images))
"""


def write_synthetic_tart(path: Path) -> None:
    path.write_text(SYNTHETIC_TART)
    path.chmod(0o755)


STANDARD_BUILD_COMMANDS = {
    "syft": "exit 99",
    "uname": 'if [ "$1" = -s ]; then echo Darwin; else echo arm64; fi',
    "networksetup": "echo 'Device: en0'",
    "route": "echo 'interface: en0'",
    "ifconfig": "echo 'status: active'",
    "ipconfig": "echo '192.0.2.10'",
    "curl": 'echo \'{"tag_name":"v2.338.2","assets":[{'
    '"name":"actions-runner-osx-arm64-2.338.2.tar.gz","digest":"sha256:'
    + "a" * 64
    + "\"}]}'",
    "git": """
case "$*" in
 *rev-parse*) echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
 *status*) if [ "$TEST_FAIL_STAGE" = dirty ]; then echo " M recipe"; fi ;;
esac
""",
}


def install_standard_android_curl_patch(tools: Path) -> None:
    tools.joinpath("curl").write_text(
        "#!/bin/sh\n"
        + STANDARD_BUILD_COMMANDS["curl"]
        + """
if case "$*" in *repository2-1.xml*) true;; *) false;; esac; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then shift; output=$1; break; fi; shift
  done
  cat >"$output" <<'XML'
<repository><remotePackage path="cmdline-tools;latest">
<revision><major>23</major><minor>0</minor></revision>
<channelRef ref="channel-0"/><archives><archive><host-os>macosx</host-os>
<complete><url>commandlinetools-mac-123_latest.zip</url>
<checksum>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</checksum>
</complete></archive></archives></remotePackage></repository>
XML
else exit 22; fi
"""
    )


def test_build_lifecycle_and_failure_cleanup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    tools = tmp_path / "bin"
    tools.mkdir()
    state = tmp_path / "state.json"
    state.write_text(json.dumps(["base", "unrelated-image"]))
    write_synthetic_tart(tools / "tart")
    install_gh_cli(tools)
    install_standard_android_curl_patch(tools)
    reference = tmp_path / "reference.json"
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text("")
    for name, script in STANDARD_BUILD_COMMANDS.items():
        if name == "curl":
            continue
        (tools / name).write_text("#!/bin/sh\n" + script + "\n")
    for path in tools.iterdir():
        path.chmod(0o755)
    for failure in (
        "dirty",
        "capacity",
        "boot",
        "provision",
        "cask",
        "os-update",
        "job-path",
        "agent-refresh",
        "agent-verify",
        "lfs-refresh",
        "bc-refresh",
        "bc-verify",
        "verify",
        "sbom",
        "sbom-read",
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
        image = (
            f"test-image-{tmp_path.parent.name}-{tmp_path.name}-{failure or 'success'}"
        )
        prepare_guest_android_layout(tmp_path / "guest-home-absent", "absent")
        android_trace = tmp_path / f"android-trace-{failure or 'success'}.txt"
        env = {
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "TEST_TART_STATE": str(state),
            "TART_HOME": str(tmp_path / "tart"),
            "TEST_FAIL_STAGE": "" if failure == "android" else failure,
            "TEST_GUEST_HOME": str(tmp_path / "guest-home-absent"),
            "TEST_ANDROID_TRACE": str(android_trace),
            "TEST_CASK_SHA": hashlib.sha256(
                b"synthetic official Azure archive"
            ).hexdigest(),
        }
        result = subprocess.run(
            [
                "/bin/bash",
                str(root / "macos/build-image.sh"),
                image,
                "base",
                "xcode-android"
                if failure == "android" or failure.startswith("bc-")
                else "xcode",
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
            if failure == "android":
                assert (
                    "android_bc_dependencies=verified"
                    in (bundle / "toolchain-check.txt").read_text()
                )
                assert android_trace.read_text() == "install-android\n"
            elif not failure:
                assert not android_trace.exists()
            repeated = subprocess.run(
                ["bash", str(root / "macos/build-image.sh"), image, "base"],
                env=env,
                capture_output=True,
            )
            assert repeated.returncode != 0
            assert json.loads(state.read_text()) == images
        shutil.rmtree(bundle, ignore_errors=True)


@pytest.mark.parametrize(
    "profile,layout,fail_stage,expect_ok,expect_android",
    [
        ("xcode", "present_canonical", "", True, True),
        ("xcode", "absent", "", True, False),
        ("xcode", "unsafe_escape", "", False, False),
        ("xcode-android", "absent", "", True, True),
        ("xcode", "present_canonical", "inherited-android", False, True),
        ("xcode", "present_canonical", "bc-refresh", False, True),
        ("xcode", "present_canonical", "bc-verify", False, True),
    ],
)
def test_android_sdk_maintenance_during_build(
    tmp_path, profile, layout, fail_stage, expect_ok, expect_android
):
    root = Path(__file__).resolve().parents[1]
    tools = tmp_path / "bin"
    tools.mkdir()
    state = tmp_path / "state.json"
    state.write_text(json.dumps(["base"]))
    trace = tmp_path / "android-trace.txt"
    write_synthetic_tart(tools / "tart")
    install_gh_cli(tools)
    for name, script in STANDARD_BUILD_COMMANDS.items():
        if name != "curl":
            (tools / name).write_text("#!/bin/sh\n" + script + "\n")
    install_standard_android_curl_patch(tools)
    for path in tools.iterdir():
        path.chmod(0o755)
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "os": "darwin",
                "arch": "arm64",
                "image_os": "macos26",
                "image_version": "reference-build",
                "records": ["cmd bash"],
            }
        )
    )
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text("")
    guest_home = tmp_path / "guest-home"
    prepare_guest_android_layout(guest_home, layout)
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "TEST_TART_STATE": str(state),
        "TART_HOME": str(tmp_path / "tart"),
        "TEST_FAIL_STAGE": fail_stage,
        "TEST_GUEST_HOME": str(guest_home),
        "TEST_ANDROID_TRACE": str(trace),
        "TEST_CASK_SHA": hashlib.sha256(
            b"synthetic official Azure archive"
        ).hexdigest(),
    }
    image = f"android-maintenance-{profile}-{layout}-{fail_stage or 'ok'}"
    bundle = root / "artifacts/tart" / image
    shutil.rmtree(bundle, ignore_errors=True)
    result = subprocess.run(
        [
            "/bin/bash",
            str(root / "macos/build-image.sh"),
            image,
            "base",
            profile,
            "/Users/admin/actions-runner",
            str(reference),
            str(allowlist),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if expect_ok:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            json.loads((bundle / "qualification.json").read_text())["status"]
            == "passed"
        )
        toolchain = (bundle / "toolchain-check.txt").read_text()
        if expect_android:
            assert trace.read_text() == "install-android\n"
            assert "android_bc_dependencies=verified" in toolchain
        else:
            assert not trace.exists()
            assert "android_bc_dependencies=verified" not in toolchain
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not (bundle / "qualification.json").exists()
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
    install_gh_cli(tools)
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
