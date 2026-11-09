import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("output,status", [("wrong-output", 0), ("runner-smoke", 7)])
def test_wrong_or_failed_compiled_program_stops_stock_bash(tmp_path, output, status):
    root = Path(__file__).resolve().parents[1]
    tools = tmp_path / "bin"
    tools.mkdir()
    source = (root / "macos/image/verify-toolchain.sh").read_text()
    toolset = tmp_path / "toolset.json"
    toolset.write_text(
        json.dumps({"toolcache": [], "java": {"arm64": {"versions": []}}})
    )
    script = tmp_path / "verify.sh"
    script.write_text(
        source.replace(
            'toolset="$HOME/.runner-image/github-macos-toolset.json"',
            "toolset=" + shlex.quote(str(toolset)),
        )
    )
    commands = (
        "git gh curl wget python pip pipx node npm ruby gem bundle fastlane pod java "
        "javac go rustup rustc cargo rustfmt cargo-clippy cmake ninja swift swiftc "
        "xcodebuild codesign security tar gzip unzip shasum"
    ).split()
    for name in commands:
        (tools / name).write_text("#!/bin/sh\nexit 0\n")
    (tools / "sw_vers").write_text("#!/bin/sh\necho 26.7.2\n")
    (tools / "python3").write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n'
    )
    (tools / "2to3").write_text(f"""#!{sys.executable}
import sys
from pathlib import Path
p = Path(sys.argv[-1])
p.write_text(p.read_text().replace('print "runner-image"', 'print("runner-image")'))
""")
    called = tmp_path / "c-called"
    continued = tmp_path / "cpp-called"
    program = f"#!/bin/sh\nprintf %s {shlex.quote(output)}\nexit {status}\n"
    (tools / "clang").write_text(f"""#!{sys.executable}
import sys
from pathlib import Path
Path({str(called)!r}).write_text("compiled")
p = Path(sys.argv[sys.argv.index("-o") + 1])
p.write_text({program!r})
p.chmod(0o755)
""")
    (tools / "clang++").write_text(
        "#!/bin/sh\ntouch " + shlex.quote(str(continued)) + "\nexit 73\n"
    )
    for path in tools.iterdir():
        path.chmod(0o755)
    cache = tmp_path / "cache"
    cache.mkdir()
    result = subprocess.run(
        ["/bin/bash", str(script)],
        env={
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "ImageOS": "macos26",
            "ImageVersion": "example",
            "RUNNER_TOOL_CACHE": str(cache),
            "AGENT_TOOLSDIRECTORY": str(cache),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert called.exists(), result.stdout + result.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    assert not continued.exists()
    assert "native_build_smoke=passed" not in result.stdout
