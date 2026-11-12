#!/bin/bash

set -euo pipefail

android_tools_revision() {
	awk -F= '{key=$1; gsub(/[[:space:]]/, "", key); if(key=="Pkg.Revision") {v=$2; gsub(/[[:space:]]/, "", v); print v}}' "$1/source.properties"
}

android_tools_is_older() {
	jq -en --arg old "$1" --arg current "$2" '(($old | split(".") | map(tonumber)) + [0,0] | .[0:3]) < (($current | split(".") | map(tonumber)) + [0,0] | .[0:3])' >/dev/null
}

android_tools_runtime_ok() {
	local tools=$1 selected=$2 actual
	[ "$(android_tools_revision "$tools")" = "$selected" ] || return 1
	if [ -x "$tools/bin/android" ]; then
		actual=$("$tools/bin/android" --no-metrics --version) || return 1
		[[ $actual =~ [0-9]+\.[0-9]+ ]] || return 1
	else
		actual=$("$tools/bin/sdkmanager" --version) || return 1
		[ "$actual" = "$selected" ] || return 1
	fi
}

verify_android_package() {
	local sdk=$1 package=$2
	python3 - "$sdk" "$package" <<'VERIFY' || return 1
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
root = Path(sys.argv[1]).resolve()
package = sys.argv[2]
metadata = (root / package.replace(";", "/") / "package.xml").resolve()
if not metadata.is_relative_to(root):
    raise ValueError("Android package path escapes SDK root")
entries = ET.parse(metadata).getroot().iter()
if not any(p.tag.rsplit("}", 1)[-1] == "localPackage" and p.get("path") == package for p in entries):
    raise ValueError("Installed Android package metadata differs from request")
VERIFY
	case "$package" in
	platform-tools) test -x "$sdk/platform-tools/adb" || return 1 ;;
	build-tools\;*) test -x "$sdk/build-tools/${package#build-tools;}/aapt" || return 1 ;;
	platforms\;*) test -s "$sdk/platforms/${package#platforms;}/android.jar" || return 1 ;;
	esac
}

activate_android_tools() {
	local sdk=$1 selected=$2 work=$3 tools previous='' revision
	[[ $selected =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || return 1
	tools="$sdk/cmdline-tools"
	[ ! -L "$sdk" ] && [ ! -L "$tools" ] && [ ! -L "$tools/$selected" ] || return 1
	android_tools_runtime_ok "$tools/$selected" "$selected" || return 1
	if [ -d "$tools/latest" ] && [ ! -L "$tools/latest" ]; then
		revision=$(android_tools_revision "$tools/latest") || return 1
		[[ $revision =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || return 1
		if [ "$revision" = "$selected" ] || { [ -e "$tools/$revision" ] && android_tools_is_older "$revision" "$selected"; }; then previous="$work/previous-command-line-tools"; else previous="$tools/$revision"; fi
		[ ! -e "$previous" ] && [ ! -L "$previous" ] || return 1
		mv "$tools/latest" "$previous" || return 1
	fi
	if ! ln -sfn "$selected" "$tools/latest"; then
		if [ -n "$previous" ]; then mv "$previous" "$tools/latest" || return 1; fi
		return 1
	fi
}

# Retire only verified, older command-line tool copies after replacement works.
retire_old_android_tools() {
	local sdk=$1 selected=$2 tools candidate version revision current
	[[ $selected =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || return 1
	tools="$sdk/cmdline-tools"
	[ ! -L "$sdk" ] && [ ! -L "$tools" ] && [ ! -L "$tools/$selected" ] || return 1
	if [ -d "$tools/latest" ] && [ ! -L "$tools/latest" ]; then
		current="$tools/latest"
	else
		[ "$(readlink "$tools/latest")" = "$selected" ] || return 1
		current="$tools/$selected"
	fi
	android_tools_runtime_ok "$current" "$selected" || return 1
	for candidate in "$tools"/*; do
		version=${candidate##*/}
		[[ $version =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || continue
		[ ! -L "$candidate" ] && [ -d "$candidate" ] || continue
		if ! android_tools_is_older "$version" "$selected"; then continue; fi
		[ -f "$candidate/source.properties" ] || continue
		revision=$(android_tools_revision "$candidate")
		[ "$revision" = "$version" ] || continue
		rm -rf "$candidate" || return 1
		echo "Retired superseded Android command-line tools $version" >&2
	done
}

JAVA_FEATURE_VERSION=${1:?Java feature version is required}
ANDROID_TOOL_MANIFEST=${2:?Resolved Android command-line tools JSON is required}
shift 2
ANDROID_PACKAGES=("$@")

[[ "$JAVA_FEATURE_VERSION" =~ ^[0-9]+$ ]] || {
	echo "Java feature version must be numeric." >&2
	exit 1
}
COMMAND_LINE_TOOLS_ONLY=false
if ((${#ANDROID_PACKAGES[@]} == 0)); then
	COMMAND_LINE_TOOLS_ONLY=true
fi
ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=$(jq -er '.version | select(test("^[0-9]+\\.[0-9]+(\\.[0-9]+)?$"))' <<<"$ANDROID_TOOL_MANIFEST")
ANDROID_COMMAND_LINE_TOOLS_URL=$(jq -er '.url | select(test("^https://dl\\.google\\.com/android/repository/commandlinetools-mac(_x86_64|_aarch64)?-[0-9]+_latest\\.zip$"))' <<<"$ANDROID_TOOL_MANIFEST")
ANDROID_CHECKSUM_ALGORITHM=$(jq -er '.checksum_algorithm | select(. == "sha1" or . == "sha256")' <<<"$ANDROID_TOOL_MANIFEST")
ANDROID_CHECKSUM=$(jq -er '.checksum' <<<"$ANDROID_TOOL_MANIFEST")
case "$ANDROID_CHECKSUM_ALGORITHM" in
sha1)
	hash_bits=1
	digest_length=40
	;;
sha256)
	hash_bits=256
	digest_length=64
	;;
esac
[[ "$ANDROID_CHECKSUM" =~ ^[a-f0-9]+$ && ${#ANDROID_CHECKSUM} -eq $digest_length ]] || {
	echo 'Invalid Android checksum' >&2
	exit 1
}

for command in curl jq tar unzip; do
	command -v "$command" >/dev/null 2>&1 || {
		echo "$command is required in the base image." >&2
		exit 1
	}
done

ANDROID_SDK_ROOT="$HOME/android-sdk"
if [[ -L "$ANDROID_SDK_ROOT" ]]; then
	echo "Android SDK root must not be a symlink." >&2
	exit 1
fi
if [[ -e "$ANDROID_SDK_ROOT" && ! -d "$ANDROID_SDK_ROOT" ]]; then
	echo "Android SDK root must be a directory." >&2
	exit 1
fi
if [[ -L "$ANDROID_SDK_ROOT/cmdline-tools" ]]; then
	echo "Android command-line tools root must not be a symlink." >&2
	exit 1
fi
if [[ -e "$ANDROID_SDK_ROOT/cmdline-tools" && ! -d "$ANDROID_SDK_ROOT/cmdline-tools" ]]; then
	echo "Android command-line tools root must be a directory." >&2
	exit 1
fi
if [[ "$COMMAND_LINE_TOOLS_ONLY" == true ]]; then
	assert_android_sdk_safe_for_refresh "$ANDROID_SDK_ROOT" || {
		echo "Android SDK layout is unsafe for command-line tools refresh." >&2
		exit 1
	}
fi

BUILD_DIR=$(mktemp -d /tmp/runner-image-android.XXXXXX)
cleanup() {
	rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

TEMURIN_VERSION=''
TEMURIN_CACHE_ROOT=''
if [[ "$COMMAND_LINE_TOOLS_ONLY" == false ]]; then
	echo "==> Resolving Temurin JDK $JAVA_FEATURE_VERSION"
	TEMURIN_METADATA=$(curl -fsSL \
		"https://api.adoptium.net/v3/assets/feature_releases/${JAVA_FEATURE_VERSION}/ga?architecture=aarch64&heap_size=normal&image_type=jdk&jvm_impl=hotspot&os=mac&page=0&page_size=1&project=jdk&sort_method=DEFAULT&sort_order=DESC&vendor=eclipse")
	TEMURIN_VERSION=$(jq -er '.[0].version_data.semver' <<<"$TEMURIN_METADATA")
	TEMURIN_URL=$(jq -er '.[0].binaries[0].package.link' <<<"$TEMURIN_METADATA")
	TEMURIN_SHA=$(jq -er '.[0].binaries[0].package.checksum | select(test("^[a-f0-9]{64}$"))' <<<"$TEMURIN_METADATA")
	TEMURIN_CACHE_VERSION=${TEMURIN_VERSION/+/-}
	TEMURIN_CACHE_ROOT="$HOME/hostedtoolcache/Java_Temurin-Hotspot_jdk/${TEMURIN_CACHE_VERSION}"

	echo "==> Caching Temurin JDK $TEMURIN_VERSION"
	curl -fsSL "$TEMURIN_URL" -o "$BUILD_DIR/temurin.tar.gz"
	printf '%s  %s\n' "$TEMURIN_SHA" "$BUILD_DIR/temurin.tar.gz" | shasum -a 256 -c -
	mkdir -p "$BUILD_DIR/temurin"
	tar xzf "$BUILD_DIR/temurin.tar.gz" -C "$BUILD_DIR/temurin"
	TEMURIN_SOURCE=$(find "$BUILD_DIR/temurin" -mindepth 1 -maxdepth 1 -type d | head -1)
	[[ -n "$TEMURIN_SOURCE" && -d "$TEMURIN_SOURCE/Contents/Home" ]] || {
		echo "Downloaded Temurin archive has an unexpected layout." >&2
		exit 1
	}
	rm -rf "$TEMURIN_CACHE_ROOT/arm64" "$TEMURIN_CACHE_ROOT/arm64.complete"
	mkdir -p "$TEMURIN_CACHE_ROOT/arm64"
	cp -R "$TEMURIN_SOURCE/." "$TEMURIN_CACHE_ROOT/arm64/"
	touch "$TEMURIN_CACHE_ROOT/arm64.complete"
	JAVA_HOME="$TEMURIN_CACHE_ROOT/arm64/Contents/Home"
	export JAVA_HOME
else
	toolset="$HOME/.runner-image/github-macos-toolset.json"
	[[ "$JAVA_FEATURE_VERSION" == "$(jq -er '.java.arm64.default' "$toolset")" ]] || {
		echo "Inherited Android refresh must use the toolset default Java." >&2
		exit 1
	}
	JAVA_HOME=$(
		grep -E '^JAVA_HOME=' "$HOME/.runner-image/environment" | tail -1 | cut -d= -f2-
	)
	[[ -n "$JAVA_HOME" && -x "$JAVA_HOME/bin/java" ]] || {
		echo "Toolset Java must be cached before inherited Android refresh." >&2
		exit 1
	}
	export JAVA_HOME
fi

echo "==> Installing Android command-line tools $ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION"
curl -fsSL \
	"$ANDROID_COMMAND_LINE_TOOLS_URL" \
	-o "$BUILD_DIR/android-command-line-tools.zip"
printf '%s  %s\n' "$ANDROID_CHECKSUM" "$BUILD_DIR/android-command-line-tools.zip" | shasum -a "$hash_bits" -c -
mkdir -p "$BUILD_DIR/android-command-line-tools"
unzip -q "$BUILD_DIR/android-command-line-tools.zip" -d "$BUILD_DIR/android-command-line-tools"
ANDROID_HOME=$ANDROID_SDK_ROOT
ANDROID_COMMAND_LINE_TOOLS_ROOT="$ANDROID_SDK_ROOT/cmdline-tools/$ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION"
rm -rf "$ANDROID_COMMAND_LINE_TOOLS_ROOT"
mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"
mv "$BUILD_DIR/android-command-line-tools/cmdline-tools" "$ANDROID_COMMAND_LINE_TOOLS_ROOT"
export ANDROID_HOME ANDROID_SDK_ROOT
export PATH="$JAVA_HOME/bin:$ANDROID_COMMAND_LINE_TOOLS_ROOT/bin:$PATH"
if [ -x "$ANDROID_COMMAND_LINE_TOOLS_ROOT/bin/android" ]; then
	printf '#!/bin/bash\nexec %q --no-metrics "$@"\n' "$ANDROID_COMMAND_LINE_TOOLS_ROOT/bin/android" >"$BUILD_DIR/android-cli"
	chmod 700 "$BUILD_DIR/android-cli"
	export ANDROID_CLI_BIN="$BUILD_DIR/android-cli"
fi

if ((${#ANDROID_PACKAGES[@]} > 0)); then
	for _ in $(seq 1 100); do
		echo y
	done >"$BUILD_DIR/android-license-answers"
	sdkmanager --licenses <"$BUILD_DIR/android-license-answers" >/dev/null

	echo "==> Installing Android packages: ${ANDROID_PACKAGES[*]}"
	for package in "${ANDROID_PACKAGES[@]}"; do
		# Install one package at a time so sdkmanager finishes extracting each
		# archive before the next package starts. A single multi-package invocation
		# can return while the final package is still being materialized.
		sdkmanager --sdk_root="$ANDROID_SDK_ROOT" "$package"
	done
fi

{
	echo "macos_version=$(sw_vers -productVersion)"
	echo "macos_build=$(sw_vers -buildVersion)"
	echo "xcode_version=$(xcodebuild -version | awk 'NR == 1 { print $2 }')"
	echo "xcode_build=$(xcodebuild -version | awk 'NR == 2 { print $3 }')"
	if [[ "$COMMAND_LINE_TOOLS_ONLY" == false ]]; then
		echo "java_temurin=$TEMURIN_VERSION"
	else
		echo "android_maintenance=command-line-tools-only"
	fi
	echo "android_command_line_tools=$ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION"
	if ((${#ANDROID_PACKAGES[@]} > 0)); then
		echo "android_packages=${ANDROID_PACKAGES[*]}"
	fi
	echo "prepared_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$HOME/.runner-image/android-facts"

echo "==> Verifying cached toolchain"
if [[ "$COMMAND_LINE_TOOLS_ONLY" == false ]]; then
	test -x "$TEMURIN_CACHE_ROOT/arm64/Contents/Home/bin/java"
	test -f "$TEMURIN_CACHE_ROOT/arm64.complete"
fi
test -x "$ANDROID_COMMAND_LINE_TOOLS_ROOT/bin/sdkmanager"
if ((${#ANDROID_PACKAGES[@]} > 0)); then
	for package in "${ANDROID_PACKAGES[@]}"; do
		if [[ "$package" == tools ]] && grep -Fxq 'Pkg.Path=tools' "$ANDROID_SDK_ROOT/tools/source.properties"; then
			continue
		fi
		verify_android_package "$ANDROID_SDK_ROOT" "$package" || {
			echo "Android package $package is missing installed metadata or files." >&2
			exit 1
		}
	done
fi
activate_android_tools "$ANDROID_SDK_ROOT" "$ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION" "$BUILD_DIR"
retire_old_android_tools "$ANDROID_SDK_ROOT" "$ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION"
jq --argjson selected "$ANDROID_TOOL_MANIFEST" '.runner_image_android_policy = $selected | .android["cmdline-tools"] = ($selected.url | split("/") | last)' "$HOME/.runner-image/github-macos-toolset.json" >"$BUILD_DIR/effective-toolset.json"
mv "$BUILD_DIR/effective-toolset.json" "$HOME/.runner-image/github-macos-toolset.json"
cat "$HOME/.runner-image/android-facts"

mkdir -p "$HOME/.runner-image"
if [[ "$COMMAND_LINE_TOOLS_ONLY" == false ]]; then
	printf 'ANDROID_HOME=%s\nANDROID_SDK_ROOT=%s\n' "$ANDROID_HOME" "$ANDROID_SDK_ROOT" >>"$HOME/.runner-image/environment"
fi
