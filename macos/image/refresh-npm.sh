#!/bin/bash
# Source-only helper shared by runner, hosted cache and Homebrew Node installs.
# Run in a subshell so cleanup cannot replace the caller's temporary-work trap.
refresh_image_npm() (
	set -euo pipefail
	prefix=${1:?Node prefix required}
	runtime=${2:?Node runtime required}
	case "$runtime" in
	node20) tag=next-11 ;;
	node22 | node24) tag=latest ;;
	*)
		echo "Unsupported Node runtime: $runtime" >&2
		exit 1
		;;
	esac
	test -f "$prefix/bin/node" || {
		echo "missing Node runtime: $runtime" >&2
		exit 1
	}
	npm_workspace=$(mktemp -d)
	trap 'rm -rf "$npm_workspace"' EXIT
	metadata="$(curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 \
		"https://registry.npmjs.org/npm/${tag}")"
	version="$(jq -er '.version | select(type == "string")' <<<"$metadata")"
	url="$(jq -er '.dist.tarball | select(type == "string")' <<<"$metadata")"
	integrity="$(jq -er '.dist.integrity | select(type == "string")' <<<"$metadata")"
	[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
		echo "npm must be a stable release" >&2
		exit 1
	}
	if [[ "$runtime" == node20 ]]; then
		[[ "$version" =~ ^11\. ]] || {
			echo "Node 20 requires the compatible npm 11 release line" >&2
			exit 1
		}
	else
		[[ "$version" =~ ^12\. ]] || {
			echo "Node 22+ requires the compatible npm 12 release line" >&2
			exit 1
		}
	fi
	[[ "$integrity" =~ ^sha512- ]] || {
		echo "npm archive requires official SHA-512 integrity" >&2
		exit 1
	}
	expected="$(printf '%s' "${integrity#sha512-}" | base64 -d | od -An -tx1 | tr -d ' \n')"
	[[ ${#expected} -eq 128 ]] || {
		echo "invalid npm SHA-512 integrity" >&2
		exit 1
	}
	archive="${npm_workspace}/npm.tgz"
	curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 -o "$archive" "$url"
	actual="$(shasum -a 512 "$archive" | awk '{print $1}')"
	[[ "$actual" == "$expected" ]] || {
		echo "official npm archive checksum mismatch" >&2
		exit 1
	}
	mkdir -p "${npm_workspace}/extracted"
	tar xzf "$archive" -C "${npm_workspace}/extracted"
	[[ "$(jq -er '.version' "${npm_workspace}/extracted/package/package.json")" == "$version" ]] || {
		echo "npm archive version differs from registry metadata" >&2
		exit 1
	}
	rm -rf "${prefix}/lib/node_modules/npm"
	mkdir -p "${prefix}/lib/node_modules"
	cp -R "${npm_workspace}/extracted/package" "${prefix}/lib/node_modules/npm"
	mkdir -p "${prefix}/bin"
	ln -sf ../lib/node_modules/npm/bin/npm-cli.js "${prefix}/bin/npm"
	ln -sf ../lib/node_modules/npm/bin/npx-cli.js "${prefix}/bin/npx"
	npm_runtime="$("${prefix}/bin/node" "${prefix}/lib/node_modules/npm/bin/npm-cli.js" --version </dev/null)" || {
		echo "runner ${runtime} npm runtime failed" >&2
		exit 1
	}
	[[ "$npm_runtime" == "$version" ]] || {
		echo "runner ${runtime} npm reports ${npm_runtime}, expected ${version}" >&2
		exit 1
	}
	rm -rf "${npm_workspace}/extracted" "$archive"
	echo "==> Installed ${runtime} npm ${version}" >&2
)
