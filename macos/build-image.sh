#!/usr/bin/env bash
# Usage: bash macos/build-image.sh IMAGE [BASE] [xcode|xcode-android] [RUNNER_DIR] [REFERENCE_JSON] [ALLOWLIST]
# Builds an unregistered local Tart image; does not change any running fleet.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=macos/network.sh
source "$root/macos/network.sh"
image=${1:?A new local image name is required}
base=${2:-ghcr.io/cirruslabs/macos-tahoe-xcode:latest}
profile=${3:-xcode}
runner_dir=${4:-/Users/admin/actions-runner}
reference=${5:-$root/snapshots/github-hosted/macos-26.json}
allowlist=${6:-$root/macos/accepted-missing/$profile.txt}
tart_home=${TART_HOME:-$HOME/.tart}
bundle="$root/artifacts/tart/$image"
source_revision=$(git -C "$root" rev-parse HEAD)
reference_revision=$(git -C "$(dirname "$reference")" rev-parse HEAD)
[[ -z $(git -C "$root" status --porcelain) ]] || {
	echo 'Commit the image recipe before building a qualified image.' >&2
	exit 1
}
[[ -f "$reference" && -f "$allowlist" && ! -e "$bundle" ]]
[[ "$image" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && "$image" != "$base" ]]
[[ "$profile" == xcode || "$profile" == xcode-android ]]
[[ "$runner_dir" =~ ^/Users/[A-Za-z0-9_-]+/[A-Za-z0-9._/-]+$ ]]
[[ "$runner_dir" != *..* ]]
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || {
	echo 'Apple Silicon macOS is required.' >&2
	exit 1
}
for command in tart curl jq python3; do command -v "$command" >/dev/null; done
image_exists() { tart list --quiet | grep -Fxq -- "$1"; }
active=$(tart list --format json | python3 -c 'import json,sys; print(sum(v["State"] == "running" for v in json.load(sys.stdin)))')
if ((active >= 2)); then
	echo 'Both macOS VM slots are occupied; retry when a slot is free.' >&2
	exit 1
fi
# Build under a new name; replacing a fleet image is a separate operator action.
if image_exists "$image"; then
	echo 'Destination already exists; choose a new image name.' >&2
	exit 1
fi
temporary="${image}-build-$(date +%s)-$$"
build_log=$(mktemp "${TMPDIR:-/tmp}/runner-image-build.XXXXXX")
build_pid=''
running=false
published=false
qualified=false
cleanup() {
	status=$?
	if [[ "$running" == true ]]; then
		tart stop "$temporary" --timeout 60 >/dev/null 2>&1 || true
		wait "$build_pid" 2>/dev/null || true
	fi
	if image_exists "$temporary"; then tart delete "$temporary" >/dev/null 2>&1 || true; fi
	if ((status != 0)); then
		if [[ "$published" == true && "$qualified" == false ]]; then tart delete "$image" >/dev/null 2>&1 || true; fi
		echo "Image build failed; previous images are preserved. Tart log: $build_log" >&2
	else
		rm -f "$build_log"
	fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# OCI tags are mutable. Refresh them even when Tart has a cached image;
# local names and immutable digest references retain their normal cache.
if [[ "$base" == */* && "$base" != *@sha256:* ]] || ! image_exists "$base"; then
	tart pull "$base"
fi
mkdir -p "$bundle"
cp "$reference" "$bundle/reference.json"
cp "$allowlist" "$bundle/accepted-missing.txt"
tart clone "$base" "$temporary"
python3 "$root/scripts/image-fingerprint.py" "$tart_home/vms/$temporary" >"$bundle/base-fingerprint.json"
tart set "$temporary" --cpu 4 --memory 8192
bridge=$(select_tart_bridge_interface)
tart run "$temporary" --no-graphics --net-bridged "$bridge" >"$build_log" 2>&1 &
build_pid=$!
running=true
for _ in $(seq 1 120); do
	if ! kill -0 "$build_pid" 2>/dev/null; then
		echo 'Tart exited before the guest agent became ready.' >&2
		exit 1
	fi
	if tart exec "$temporary" /usr/bin/true >/dev/null 2>&1; then break; fi
	sleep 2
done
tart exec "$temporary" /usr/bin/true

echo '==> Installing the official macOS toolset and runtime caches'
job_path=$(tart exec -i "$temporary" /bin/bash -s <"$root/macos/image/toolchain.sh")
[[ "$job_path" == */usr/bin* ]]
if [[ "$profile" == xcode-android ]]; then
	# shellcheck disable=SC2016 # The quoted variables are evaluated inside the guest.
	android_config=$( # shellcheck disable=SC2016 # The quoted variables are evaluated inside the guest.
		tart exec "$temporary" /bin/bash -c '
    jq -r "[.java.arm64.default, (.android[\"cmdline-tools\"] | capture(\"mac-(?<version>[0-9]+)_\").version), .android.platform_min_version, .android.build_tools_min_version] | join(\" \")" "$HOME/.runner-image/github-macos-toolset.json"
  '
	)
	read -r java sdk platform_version build_tools <<<"$android_config"
	tart exec -i "$temporary" /bin/bash -s -- "$java" "$sdk" \
		platform-tools "platforms;android-$platform_version" "build-tools;$build_tools" \
		<"$root/macos/image/install-android.sh"
	job_path+=":/Users/admin/android-sdk/platform-tools:/Users/admin/android-sdk/cmdline-tools/latest/bin"
fi

release=$(curl -fsSL --retry 5 https://api.github.com/repos/actions/runner/releases/latest)
version=$(jq -er '.tag_name | ltrimstr("v")' <<<"$release")
checksum=$(jq -er --arg version "$version" '.assets[] | select(.name == "actions-runner-osx-arm64-" + $version + ".tar.gz") | .digest | ltrimstr("sha256:")' <<<"$release")
tart exec -i "$temporary" /bin/bash -s -- "$version" "$runner_dir" "$checksum" \
	<"$root/macos/image/install-runner.sh"
# shellcheck disable=SC2016 # The quoted variables are evaluated inside the guest.
printf 'PATH=%s\n' "$job_path" | tart exec -i "$temporary" /bin/bash -c 'cat >"$1/.env"' -- "$runner_dir"
# No registration token, organization, or host identity is baked into the image.
# shellcheck disable=SC2016 # The quoted variables are evaluated inside the guest.
tart exec "$temporary" /bin/bash -c '
  set -euo pipefail
  cat "$HOME/.runner-image/environment" >>"$1/.env"
  printf "ImageOS=macos%s\nImageVersion=%s\nRUNNER_TOOL_CACHE=%s/hostedtoolcache\nAGENT_TOOLSDIRECTORY=%s/hostedtoolcache\nCARGO_HOME=%s/.cargo\nRUSTUP_HOME=%s/.rustup\n" \
    "$(sw_vers -productVersion | cut -d. -f1)" "$(date -u +%Y%m%d.%H%M)" "$HOME" "$HOME" "$HOME" "$HOME" >>"$1/.env"
' -- "$runner_dir"
guest_job() {
	# shellcheck disable=SC2016 # The quoted variables are evaluated inside the guest.
	tart exec -i "$temporary" /bin/bash -c '
    set -euo pipefail
    runner_dir=$1
    shift
    while IFS="=" read -r key value; do export "$key=$value"; done <"$runner_dir/.env"
    exec "$@"
  ' -- "$runner_dir" "$@"
}
guest_job /bin/bash -s <"$root/macos/image/collect-sbom.sh" >"$bundle/sbom.spdx.json"
guest_job /bin/bash -s <"$root/macos/image/verify-toolchain.sh" 2>&1 | tee "$bundle/toolchain-check.txt"
if [[ "$profile" == xcode-android ]]; then
	# shellcheck disable=SC2016 # The quoted paths are evaluated inside the guest.
	guest_job /bin/bash -c '
    "$HOME/android-sdk/platform-tools/adb" version
    "$HOME/android-sdk/cmdline-tools/latest/bin/sdkmanager" --version
  ' </dev/null 2>&1 | tee -a "$bundle/toolchain-check.txt"
fi

{
	cat "$root/runner_images/inventory.py"
	printf '\nimport json\nprint(json.dumps(collect(), indent=2))\n'
} | guest_job python3 - >"$bundle/inventory.json"
# Preserve the exact upstream toolset used by this build with the comparison inputs.
# shellcheck disable=SC2016 # HOME is evaluated inside the guest.
guest_job /bin/bash -c 'cat "$HOME/.runner-image/github-macos-toolset.json"' </dev/null >"$bundle/toolset.json"
tart exec "$temporary" /usr/bin/sync
tart stop "$temporary" --timeout 120
wait "$build_pid" || true
running=false
[[ $(git -C "$root" rev-parse HEAD) == "$source_revision" && -z $(git -C "$root" status --porcelain) ]] || {
	echo 'Image recipe changed during the build; refusing qualification.' >&2
	exit 1
}
python3 "$root/scripts/qualify-image.py" "$bundle" "$tart_home/vms/$temporary" \
	--image "$image" --profile "$profile" --base "$base" \
	--source-commit "$source_revision" --reference-commit "$reference_revision" \
	--runner-version "$version" \
	--runner-dir "$runner_dir" \
	--run-url "${GITHUB_SERVER_URL:+$GITHUB_SERVER_URL/${GITHUB_REPOSITORY:?}/actions/runs/${GITHUB_RUN_ID:?}}"
# Recheck in case another process created the destination while we built.
if image_exists "$image"; then
	echo 'Destination appeared during build.' >&2
	exit 1
fi
tart rename "$temporary" "$image"
published=true
python3 "$root/scripts/finalize-image.py" "$bundle" "$tart_home/vms/$image"
qualified=true
echo "Qualified local image $image; evidence: artifacts/tart/$image/qualification.json"
