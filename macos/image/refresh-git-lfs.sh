#!/bin/bash
# Executed only inside an unregistered temporary image-build guest.
set -euo pipefail

verify_git_lfs() {
	local receipt=${1:-$HOME/.runner-image/git-lfs-build.json} binary expected version description actual
	binary=$(jq -er '.binary' "$receipt") || return 1
	expected=$(jq -er '.binary_sha256' "$receipt") || return 1
	version=$(jq -er '.version' "$receipt") || return 1
	description=$(jq -er '.description' "$receipt") || return 1
	[[ "$description" == "git-lfs/$version (Melten; darwin arm64; go "* ]] || return 1
	[[ "$binary" =~ ^/opt/homebrew/Cellar/git-lfs/[0-9]+\.[0-9]+\.[0-9]+/bin/git-lfs$ ]] || return 1
	[ -f "$binary" ] && [ ! -L "$binary" ] || return 1
	actual=$(/usr/bin/shasum -a 256 "$binary") || return 1
	[ "${actual%% *}" = "$expected" ] || return 1
	actual=$("$binary" version) || return 1
	[ "$actual" = "$description" ] || return 1
	/usr/bin/codesign --verify --strict "$binary" || return 1
	printf 'git_lfs_version=%s\ngit_lfs_sha256=%s\n' "$version" "$expected"
}

git_lfs_smoke() {
	/bin/bash -s -- "$1" "$2" <<'LFS_SMOKE'
set -euo pipefail
binary=$1
work=$2
mkdir -p "$work"
export PATH="${binary%/*}:$PATH"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
# No network account or developer Git configuration participates in this test.
git init --bare -b main "$work/remote.git" >/dev/null
git init -b main "$work/repository" >/dev/null
cd "$work/repository"
git config user.name 'Example Builder'
git config user.email 'builder@example.com'
git lfs install --local >/dev/null
git lfs track '*.payload' >/dev/null
python3 -c 'from pathlib import Path; Path("example.payload").write_bytes(bytes(range(256))*4096)'
cp example.payload "$work/expected.payload"
git add .gitattributes example.payload
git commit -m 'Synthetic LFS fixture' >/dev/null
git show HEAD:example.payload | grep -Fx 'version https://git-lfs.github.com/spec/v1' >/dev/null
git remote add origin "$work/remote.git"
git push origin main >/dev/null
git clone --no-checkout "$work/remote.git" "$work/consumer" >/dev/null
cd "$work/consumer"
git lfs install --local >/dev/null
git checkout main >/dev/null
cmp example.payload "$work/expected.payload"
git lfs fsck
printf 'lfs_pointer_push_clone_materialization=passed\n'
LFS_SMOKE
}

refresh_git_lfs() {
	local work toolset go_version prefix binary source source_version identity version description digest uid gid mode attempt
	git_lfs_work=$(mktemp -d /private/tmp/runner-image-git-lfs.XXXXXX)
	work=$git_lfs_work
	trap 'chmod -R u+w "$git_lfs_work"; rm -rf "$git_lfs_work"' EXIT
	toolset="$HOME/.runner-image/github-macos-toolset.json"
	go_version=$(jq -er '.runner_image_go_policy.versions | map(split(".") | map(tonumber)) | sort | last | map(tostring) | join(".")' "$toolset")
	export PATH="$HOME/hostedtoolcache/go/$go_version/arm64/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
	export GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
	export GOPRIVATE='' GONOPROXY='' GONOSUMDB=''
	export GOMODCACHE="$work/modules" GOCACHE="$work/cache"
	export GOOS=darwin GOARCH=arm64 CGO_ENABLED=0
	printf '[init]\n defaultBranch = master\n' >"$work/gitconfig"
	export GIT_CONFIG_GLOBAL="$work/gitconfig" GIT_CONFIG_NOSYSTEM=1
	[ "$(go version)" = "go version go$go_version darwin/arm64" ] || {
		echo "Git LFS rebuild did not resolve the required Go $go_version compiler." >&2
		exit 1
	}
	prefix=$(brew --prefix git-lfs)
	binary=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$prefix/bin/git-lfs")
	[[ "$binary" =~ ^/opt/homebrew/Cellar/git-lfs/[0-9]+\.[0-9]+\.[0-9]+/bin/git-lfs$ ]] || {
		echo "Git LFS rebuild found an unsupported binary location: $binary" >&2
		exit 1
	}
	# Only this transfer retries transient guest-network failures, matching
	# the factory curl policy (5 retries, 2s delay); the source checks below
	# never retry.
	for attempt in 1 2 3 4 5 6; do
		go mod download -json github.com/git-lfs/git-lfs/v3@latest >"$work/source.json" && break
		[ "$attempt" -lt 6 ] || {
			cat "$work/source.json" >&2
			exit 1
		}
		sleep 2
	done
	source=$(jq -er '.Dir' "$work/source.json")
	source_version=$(jq -er '.Version | select(test("^v[0-9]+\\.[0-9]+\\.[0-9]+$")) | ltrimstr("v")' "$work/source.json")
	[ "$binary" = "/opt/homebrew/Cellar/git-lfs/$source_version/bin/git-lfs" ] || {
		echo 'The installed git-lfs formula must match the latest stable source release.' >&2
		exit 1
	}
	cp -R "$source" "$work/source"
	chmod -R u+w "$work/source"
	cd "$work/source"
	go get golang.org/x/net@latest github.com/klauspost/compress@latest
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
	version=$source_version
	description="git-lfs/$version (Melten; darwin arm64; go $go_version; git patched.${identity:0:12})"
	CGO_ENABLED=0 go build -trimpath -ldflags "-X github.com/git-lfs/git-lfs/v3/config.Vendor=Melten -X github.com/git-lfs/git-lfs/v3/config.GitCommit=patched.${identity:0:12}" -o "$work/git-lfs" .
	/usr/bin/codesign --force --sign - --identifier git-lfs --timestamp=none "$work/git-lfs"
	/usr/bin/codesign --verify --strict "$work/git-lfs"
	[ "$("$work/git-lfs" version)" = "$description" ] || exit 1
	git_lfs_smoke "$work/git-lfs" "$work/candidate-smoke"
	go version -m "$work/git-lfs" >"$work/binary-modules.txt"
	digest=$(/usr/bin/shasum -a 256 "$work/git-lfs")
	digest=${digest%% *}
	mkdir -p "$HOME/.runner-image"
	jq -n --slurpfile source "$work/source.json" --slurpfile modules "$work/modules.json" \
		--arg version "$version" --arg description "$description" --arg compiler "go$go_version" --arg binary "$binary" --arg digest "$digest" \
		'{source:($source[0] | {Path,Version,Sum,GoModSum}),modules:$modules[0],version:$version,description:$description,compiler:$compiler,binary:$binary,binary_sha256:$digest,upstream_tests:"passed",signature:"ad-hoc"}' >"$work/receipt.json"
	cp -p "$binary" "$work/original"
	uid=$(/usr/bin/stat -f %u "$binary")
	gid=$(/usr/bin/stat -f %g "$binary")
	mode=$(/usr/bin/stat -f %Lp "$binary")
	sudo -n /usr/bin/install -o "$uid" -g "$gid" -m "$mode" "$work/git-lfs" "$binary.melten-new"
	sudo -n /bin/mv "$binary.melten-new" "$binary"
	if ! verify_git_lfs "$work/receipt.json" || ! git_lfs_smoke "$binary" "$work/installed-smoke"; then
		sudo -n /usr/bin/install -o "$uid" -g "$gid" -m "$mode" "$work/original" "$binary.melten-restore"
		sudo -n /bin/mv "$binary.melten-restore" "$binary"
		echo 'Git LFS activation failed; restored the previous executable.' >&2
		exit 1
	fi
	cp "$work/receipt.json" "$HOME/.runner-image/git-lfs-build.json"
	cp go.mod "$HOME/.runner-image/git-lfs.go.mod"
	cp go.sum "$HOME/.runner-image/git-lfs.go.sum"
	jq --slurpfile agent "$work/receipt.json" '.runner_image_git_lfs_policy = $agent[0]' "$toolset" >"$work/toolset.json"
	mv "$work/toolset.json" "$toolset"
	/bin/sync
}

case "${1:?Expected refresh or verify}" in
refresh) refresh_git_lfs ;;
verify) verify_git_lfs ;;
*)
	echo 'Expected refresh or verify' >&2
	exit 1
	;;
esac
