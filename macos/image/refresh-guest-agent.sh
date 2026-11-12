#!/bin/bash
# Executed only inside an unregistered temporary image-build guest.
set -euo pipefail

verify_guest_agent() {
	local receipt="$HOME/.runner-image/guest-agent-build.json" binary expected version actual
	binary=$(jq -er '.binary' "$receipt") || return 1
	expected=$(jq -er '.binary_sha256' "$receipt") || return 1
	version=$(jq -er '.version' "$receipt") || return 1
	[[ "$binary" =~ ^/opt/homebrew/Cellar/tart-guest-agent/[0-9]+\.[0-9]+\.[0-9]+/bin/tart-guest-agent$ ]] || return 1
	[ -f "$binary" ] && [ ! -L "$binary" ] || return 1
	actual=$(/usr/bin/shasum -a 256 "$binary") || return 1
	[ "${actual%% *}" = "$expected" ] || return 1
	actual=$("$binary" --version) || return 1
	[[ "$actual" == "tart-guest-agent version $version-"* ]] || return 1
	/usr/bin/codesign --verify --strict "$binary" || return 1
	printf 'guest_agent_version=%s\nguest_agent_sha256=%s\n' "$version" "$expected"
}

refresh_guest_agent() {
	local work toolset go_version prefix binary source source_version identity version digest uid gid mode
	guest_agent_work=$(mktemp -d /private/tmp/runner-image-guest-agent.XXXXXX)
	work=$guest_agent_work
	trap 'chmod -R u+w "$guest_agent_work"; rm -rf "$guest_agent_work"' EXIT
	toolset="$HOME/.runner-image/github-macos-toolset.json"
	go_version=$(jq -er '.runner_image_go_policy.versions | map(split(".") | map(tonumber)) | sort | last | map(tostring) | join(".")' "$toolset")
	export PATH="$HOME/hostedtoolcache/go/$go_version/arm64/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
	export GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
	export GOPRIVATE='' GONOPROXY='' GONOSUMDB=''
	export GOMODCACHE="$work/modules" GOCACHE="$work/cache"
	[ "$(go version)" = "go version go$go_version darwin/arm64" ]
	prefix=$(brew --prefix tart-guest-agent)
	binary=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$prefix/bin/tart-guest-agent")
	[[ "$binary" =~ ^/opt/homebrew/Cellar/tart-guest-agent/[0-9]+\.[0-9]+\.[0-9]+/bin/tart-guest-agent$ ]] || exit 1
	go mod download -json github.com/cirruslabs/tart-guest-agent@latest >"$work/source.json"
	source=$(jq -er '.Dir' "$work/source.json")
	source_version=$(jq -er '.Version | select(test("^v[0-9]+\\.[0-9]+\\.[0-9]+$")) | ltrimstr("v")' "$work/source.json")
	[ "$binary" = "/opt/homebrew/Cellar/tart-guest-agent/$source_version/bin/tart-guest-agent" ] || {
		echo 'The installed guest-agent formula must match the latest stable source release.' >&2
		exit 1
	}
	cp -R "$source" "$work/source"
	chmod -R u+w "$work/source"
	cd "$work/source"
	go get golang.org/x/net@latest golang.org/x/image@latest
	go mod verify
	go test ./... >"$work/tests.log" 2>&1 || {
		cat "$work/tests.log" >&2
		exit 1
	}
	go list -m -json all | jq -s '[.[] | {Path,Version,Sum,GoModSum}]' >"$work/modules.json"
	identity=$({
		jq -c '{Path,Version,Sum,GoModSum}' "$work/source.json"
		cat go.mod go.sum
		go version
	} | /usr/bin/shasum -a 256)
	version="$source_version+melten.${identity:0:12}"
	CGO_ENABLED=1 go build -trimpath -ldflags "-X github.com/cirruslabs/tart-guest-agent/internal/version.Version=$version -X github.com/cirruslabs/tart-guest-agent/internal/version.Commit=patched" -o "$work/tart-guest-agent" ./cmd
	/usr/bin/codesign --force --sign - --identifier tart-guest-agent --timestamp=none "$work/tart-guest-agent"
	/usr/bin/codesign --verify --strict "$work/tart-guest-agent"
	[[ $("$work/tart-guest-agent" --version) == "tart-guest-agent version $version-patched" ]] || exit 1
	go version -m "$work/tart-guest-agent" >"$work/binary-modules.txt"
	digest=$(/usr/bin/shasum -a 256 "$work/tart-guest-agent")
	digest=${digest%% *}
	mkdir -p "$HOME/.runner-image"
	jq -n --slurpfile source "$work/source.json" --slurpfile modules "$work/modules.json" \
		--arg version "$version" --arg compiler "go$go_version" --arg binary "$binary" --arg digest "$digest" \
		'{source:($source[0] | {Path,Version,Sum,GoModSum}),modules:$modules[0],version:$version,compiler:$compiler,binary:$binary,binary_sha256:$digest,upstream_tests:"passed",signature:"ad-hoc"}' >"$work/receipt.json"
	uid=$(/usr/bin/stat -f %u "$binary")
	gid=$(/usr/bin/stat -f %g "$binary")
	mode=$(/usr/bin/stat -f %Lp "$binary")
	sudo -n /usr/bin/install -o "$uid" -g "$gid" -m "$mode" "$work/tart-guest-agent" "$binary.melten-new"
	sudo -n /bin/mv "$binary.melten-new" "$binary"
	cp "$work/receipt.json" "$HOME/.runner-image/guest-agent-build.json"
	cp go.mod "$HOME/.runner-image/guest-agent.go.mod"
	cp go.sum "$HOME/.runner-image/guest-agent.go.sum"
	verify_guest_agent
	jq --slurpfile agent "$work/receipt.json" '.runner_image_guest_agent_policy = $agent[0]' "$toolset" >"$work/toolset.json"
	mv "$work/toolset.json" "$toolset"
	/bin/sync
}

case "${1:?Expected refresh or verify}" in
refresh) refresh_guest_agent ;;
verify) verify_guest_agent ;;
*)
	echo 'Expected refresh or verify' >&2
	exit 1
	;;
esac
