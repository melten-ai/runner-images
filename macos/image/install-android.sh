#!/bin/bash

set -euo pipefail

JAVA_FEATURE_VERSION=${1:?Java feature version is required}
ANDROID_COMMAND_LINE_TOOLS_VERSION=${2:?Android command-line tools version is required}
shift 2
ANDROID_PACKAGES=("$@")

[[ "$JAVA_FEATURE_VERSION" =~ ^[0-9]+$ ]] || {
	echo "Java feature version must be numeric." >&2
	exit 1
}
[[ "$ANDROID_COMMAND_LINE_TOOLS_VERSION" =~ ^[0-9]+$ ]] || {
	echo "Android command-line tools version must be numeric." >&2
	exit 1
}
((${#ANDROID_PACKAGES[@]} > 0)) || {
	echo "At least one Android package is required." >&2
	exit 1
}

case "$ANDROID_COMMAND_LINE_TOOLS_VERSION" in
14742923) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=20.0 ;;
12266719) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=16.0 ;;
11479570) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=13.0 ;;
11076708) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=12.0 ;;
10406996) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=11.0 ;;
9862592) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=10.0 ;;
9477386) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=9.0 ;;
9123335) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=8.0 ;;
8512546) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=7.0 ;;
*) ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION=$ANDROID_COMMAND_LINE_TOOLS_VERSION ;;
esac

for command in curl jq tar unzip; do
	command -v "$command" >/dev/null 2>&1 || {
		echo "$command is required in the base image." >&2
		exit 1
	}
done

BUILD_DIR=$(mktemp -d /tmp/runner-image-android.XXXXXX)
cleanup() {
	rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

echo "==> Resolving Temurin JDK $JAVA_FEATURE_VERSION"
TEMURIN_METADATA=$(curl -fsSL \
	"https://api.adoptium.net/v3/assets/feature_releases/${JAVA_FEATURE_VERSION}/ga?architecture=aarch64&heap_size=normal&image_type=jdk&jvm_impl=hotspot&os=mac&page=0&page_size=1&project=jdk&sort_method=DEFAULT&sort_order=DESC&vendor=eclipse")
TEMURIN_VERSION=$(jq -er '.[0].version_data.semver' <<<"$TEMURIN_METADATA")
TEMURIN_URL=$(jq -er '.[0].binaries[0].package.link' <<<"$TEMURIN_METADATA")
TEMURIN_CACHE_VERSION=${TEMURIN_VERSION/+/-}
TEMURIN_CACHE_ROOT="$HOME/hostedtoolcache/Java_Temurin-Hotspot_jdk/${TEMURIN_CACHE_VERSION}"

echo "==> Caching Temurin JDK $TEMURIN_VERSION"
curl -fsSL "$TEMURIN_URL" -o "$BUILD_DIR/temurin.tar.gz"
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

echo "==> Installing Android command-line tools $ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION"
curl -fsSL \
	"https://dl.google.com/android/repository/commandlinetools-mac-${ANDROID_COMMAND_LINE_TOOLS_VERSION}_latest.zip" \
	-o "$BUILD_DIR/android-command-line-tools.zip"
mkdir -p "$BUILD_DIR/android-command-line-tools"
unzip -q "$BUILD_DIR/android-command-line-tools.zip" -d "$BUILD_DIR/android-command-line-tools"
ANDROID_SDK_ROOT="$HOME/android-sdk"
ANDROID_HOME=$ANDROID_SDK_ROOT
ANDROID_COMMAND_LINE_TOOLS_ROOT="$ANDROID_SDK_ROOT/cmdline-tools/$ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION"
rm -rf "$ANDROID_COMMAND_LINE_TOOLS_ROOT"
mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"
mv "$BUILD_DIR/android-command-line-tools/cmdline-tools" "$ANDROID_COMMAND_LINE_TOOLS_ROOT"
ln -sfn "$ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION" "$ANDROID_SDK_ROOT/cmdline-tools/latest"
export ANDROID_HOME ANDROID_SDK_ROOT
export PATH="$JAVA_HOME/bin:$ANDROID_COMMAND_LINE_TOOLS_ROOT/bin:$PATH"

for _ in $(seq 1 100); do
	echo y
done >"$BUILD_DIR/android-license-answers"
sdkmanager --licenses <"$BUILD_DIR/android-license-answers" >/dev/null

echo "==> Installing Android packages: ${ANDROID_PACKAGES[*]}"
for package in "${ANDROID_PACKAGES[@]}"; do
	# Install one package at a time so sdkmanager finishes extracting each
	# archive before the next package starts. A single multi-package invocation
	# can return while the final package is still being materialized.
	sdkmanager "$package"
done

cat >"$HOME/.runner-image/android-facts" <<EOF
macos_version=$(sw_vers -productVersion)
macos_build=$(sw_vers -buildVersion)
xcode_version=$(xcodebuild -version | awk 'NR == 1 { print $2 }')
xcode_build=$(xcodebuild -version | awk 'NR == 2 { print $3 }')
java_temurin=$TEMURIN_VERSION
android_command_line_tools=$ANDROID_COMMAND_LINE_TOOLS_SHORT_VERSION
android_packages=${ANDROID_PACKAGES[*]}
prepared_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

echo "==> Verifying cached toolchain"
test -x "$TEMURIN_CACHE_ROOT/arm64/Contents/Home/bin/java"
test -f "$TEMURIN_CACHE_ROOT/arm64.complete"
test -x "$ANDROID_COMMAND_LINE_TOOLS_ROOT/bin/sdkmanager"
INSTALLED_ANDROID_PACKAGES=$(sdkmanager --list_installed | awk -F '|' '
  NF > 1 {
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $1)
    if ($1 != "Path") print $1
  }
')
for package in "${ANDROID_PACKAGES[@]}"; do
	if [[ "$package" == tools ]] && grep -Fxq 'Pkg.Path=tools' "$ANDROID_SDK_ROOT/tools/source.properties"; then
		continue
	fi
	grep -Fxq -- "$package" <<<"$INSTALLED_ANDROID_PACKAGES" || {
		echo "Android package $package was not installed." >&2
		exit 1
	}
	case "$package" in
	platform-tools)
		test -x "$ANDROID_SDK_ROOT/platform-tools/adb" || {
			echo "Android package platform-tools is missing adb." >&2
			exit 1
		}
		;;
	build-tools\;*)
		build_tools_version=${package#build-tools;}
		test -x "$ANDROID_SDK_ROOT/build-tools/$build_tools_version/aapt" || {
			echo "Android package $package is missing aapt." >&2
			exit 1
		}
		;;
	esac
done
cat "$HOME/.runner-image/android-facts"

mkdir -p "$HOME/.runner-image"
printf 'ANDROID_HOME=%s\nANDROID_SDK_ROOT=%s\n' "$ANDROID_HOME" "$ANDROID_SDK_ROOT" >>"$HOME/.runner-image/environment"
