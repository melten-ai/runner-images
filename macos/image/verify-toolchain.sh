#!/bin/bash
# Run in a job or after provisioning, using exactly the PATH jobs receive.
set -euo pipefail

toolset="$HOME/.runner-image/github-macos-toolset.json"
test -s "$toolset"
for tool in git gh jq curl wget python python3 pip pipx node npm ruby gem bundle fastlane pod java javac go rustup rustc cargo rustfmt cargo-clippy cmake ninja clang swift xcodebuild codesign security tar gzip unzip shasum; do
	command -v "$tool"
done
python3 --version
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
