#!/bin/bash
# Run in a job or after provisioning, using exactly the PATH jobs receive.
set -euo pipefail

# Stock macOS Bash 3.2 does not apply errexit to failed [[ ... ]] checks.
check_output() {
	local expected=$1 output
	shift
	output=$("$@") || {
		echo "Native smoke command failed: $1" >&2
		return 1
	}
	[[ $output == "$expected" ]] || {
		echo "Unexpected native smoke output: $1" >&2
		return 1
	}
}

sw_vers
toolset="$HOME/.runner-image/github-macos-toolset.json"
test -s "$toolset"
for tool in git gh jq curl wget python python3 pip pipx 2to3 node npm ruby gem bundle fastlane pod java javac go rustup rustc cargo rustfmt cargo-clippy cmake ninja clang clang++ swift swiftc xcodebuild codesign security tar gzip unzip shasum; do
	command -v "$tool"
done
python3 --version

conversion=$(mktemp -d /tmp/runner-image-conversion.XXXXXX)
trap 'rm -rf "$conversion"' EXIT
printf 'print "runner-image"\n' >"$conversion/legacy.py"
2to3 -w -n "$conversion/legacy.py" >/dev/null
check_output runner-image python3 "$conversion/legacy.py" || exit 1
node --version
ruby --version
go version
rustc --version
cargo --version
rustfmt --version
cargo clippy --version
xcodebuild -version
java -version

test -n "${ImageOS:-}" && test -n "${ImageVersion:-}"
test "$ImageOS" = "macos$(sw_vers -productVersion | cut -d. -f1)"
test "$RUNNER_TOOL_CACHE" = "$AGENT_TOOLSDIRECTORY"
while IFS='|' read -r name pattern; do
	case "$name" in Go) name=go ;; Node) name=node ;; esac
	compgen -G "$RUNNER_TOOL_CACHE/$name/$pattern/arm64.complete" >/dev/null || {
		echo "Missing hosted tool cache entry: $name $pattern arm64" >&2
		exit 1
	}
	printf 'cached: %s %s arm64\n' "$name" "$pattern"
done < <(jq -er '.toolcache[] | select(.url) | .name as $name | .arch.arm64.versions[] | $name + "|" + .' "$toolset")

while IFS= read -r major; do
	name="JAVA_HOME_${major}_arm64"
	test -x "${!name}/bin/java"
	"${!name}/bin/java" -version
done < <(jq -er '.java.arm64.versions[]' "$toolset")

# Exercise the same compiler/runtime contracts as the Linux image smoke.
printf '#include <stdio.h>\nint main(void){puts("runner-smoke");return 0;}\n' >"$conversion/hello.c"
clang "$conversion/hello.c" -o "$conversion/hello-c"
check_output runner-smoke "$conversion/hello-c" || exit 1
printf '#include <iostream>\nint main(){std::cout << "runner-smoke";}\n' >"$conversion/hello.cpp"
clang++ -std=c++17 "$conversion/hello.cpp" -o "$conversion/hello-cpp"
check_output runner-smoke "$conversion/hello-cpp" || exit 1
cat >"$conversion/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.20)
project(runner_smoke LANGUAGES C)
add_executable(runner-smoke hello.c)
CMAKE
cmake -S "$conversion" -B "$conversion/cmake-build" -G Ninja
cmake --build "$conversion/cmake-build"
check_output runner-smoke "$conversion/cmake-build/runner-smoke" || exit 1
printf 'fn main(){print!("runner-smoke");}\n' >"$conversion/hello.rs"
rustc "$conversion/hello.rs" -o "$conversion/hello-rust"
check_output runner-smoke "$conversion/hello-rust" || exit 1
cargo new --quiet --bin --vcs none "$conversion/cargo-smoke"
cargo build --quiet --offline --manifest-path "$conversion/cargo-smoke/Cargo.toml"
"$conversion/cargo-smoke/target/debug/cargo-smoke"
python3 -m venv "$conversion/venv"
"$conversion/venv/bin/python" -c 'import ssl, sqlite3; assert ssl.OPENSSL_VERSION'
node -e 'require("node:assert").strictEqual(require("node:crypto").createHash("sha256").update("runner-smoke").digest("hex").length, 64)'
check_output runner-smoke ruby -ropenssl -rjson -e 'print JSON.parse(%q({"result":"runner-smoke"}))["result"]' || exit 1
printf 'package main\nimport "fmt"\nfunc main(){fmt.Print("runner-smoke")}\n' >"$conversion/hello.go"
check_output runner-smoke /usr/bin/env GOCACHE="$conversion/go-cache" go run "$conversion/hello.go" || exit 1
printf 'class RunnerSmoke { public static void main(String[] args) { System.out.print("runner-smoke"); } }\n' >"$conversion/RunnerSmoke.java"
javac -d "$conversion" "$conversion/RunnerSmoke.java"
check_output runner-smoke java -cp "$conversion" RunnerSmoke || exit 1
printf 'print("runner-smoke")\n' >"$conversion/hello.swift"
swiftc -module-cache-path "$conversion/swift-cache" "$conversion/hello.swift" -o "$conversion/hello-swift"
check_output runner-smoke "$conversion/hello-swift" || exit 1

# These exact Apple bundles have narrowly scoped scanner dispositions. Stop
# qualification when their runtime predicates change, even without a version bump.
for apple_python in \
	/Applications/Xcode_27.2.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 \
	/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3; do
	"$apple_python" -I -B - <<'APPLE_PYTHON'
import _bz2
import bz2
import ctypes
import inspect
import json
import pathlib
import subprocess
import sys
import tarfile

if sys.version_info[:3] != (3, 9, 6):
    raise RuntimeError("Reassess Apple Python dispositions for the changed runtime")
for module in (_bz2, tarfile):
    pathlib.Path(module.__file__).resolve().relative_to(pathlib.Path(sys.base_prefix).resolve())
if (
    any(hasattr(tarfile, name) for name in ("data_filter", "tar_filter", "_NAMED_FILTERS"))
    or hasattr(tarfile.TarFile, "extraction_filter")
    or any("filter" in inspect.signature(method).parameters
           for method in (tarfile.TarFile.extract, tarfile.TarFile.extractall))
):
    raise RuntimeError("Reassess tarfile dispositions: extraction filters are present")
linked = subprocess.check_output(["/usr/bin/otool", "-L", _bz2.__file__], text=True)
libraries = [line.strip().split(" (", 1)[0] for line in linked.splitlines() if line.startswith("\t")]
if libraries.count("/usr/lib/libbz2.1.0.dylib") < 1 or any(
    "bz2" in name and name != "/usr/lib/libbz2.1.0.dylib" for name in libraries
):
    raise RuntimeError("Reassess bzip2 disposition: _bz2 library identity changed")
library = ctypes.CDLL("/usr/lib/libbz2.1.0.dylib")
library.BZ2_bzlibVersion.restype = ctypes.c_char_p
version = library.BZ2_bzlibVersion().decode().split(",", 1)[0]
if version != "1.0.8":
    raise RuntimeError("Reassess bzip2 disposition: linked library version changed")
payload = b"runner-image-bzip2" * 64
if bz2.decompress(bz2.compress(payload)) != payload:
    raise RuntimeError("Apple Python bzip2 roundtrip failed")
print(json.dumps({"apple_python_dispositions": "verified", "executable": sys.executable,
                  "python": "3.9.6", "tarfile_extraction_filters": False,
                  "bz2_module": _bz2.__file__, "linked_libraries": libraries,
                  "bzip2_version": version, "bzip2_roundtrip": "passed"}, sort_keys=True))
APPLE_PYTHON
done
echo 'native_build_smoke=passed'
