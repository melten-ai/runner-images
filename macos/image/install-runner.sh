#!/bin/bash
# Executed inside an isolated build guest. Installs the GitHub Actions runner.
set -euo pipefail

RUNNER_VERSION="${1:?actions/runner version (e.g. 2.338.2) is required}"
RUNNER_DIR="${2:?runner directory is required}"
RUNNER_SHA="${3:?runner SHA256 is required}"
[[ "$RUNNER_SHA" =~ ^[a-f0-9]{64}$ ]]

[[ "$RUNNER_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
	echo "Runner version must look like 2.338.2." >&2
	exit 1
}

[[ "$RUNNER_DIR" == "$HOME/"* && "$RUNNER_DIR" != "$HOME/" && ! -L "$RUNNER_DIR" ]] || {
	echo "Runner directory must be below the guest home and must not be a symlink." >&2
	exit 1
}

echo "==> Installing GitHub Actions runner ${RUNNER_VERSION} to ${RUNNER_DIR}"
mkdir -p "$RUNNER_DIR"
find "$RUNNER_DIR" -mindepth 1 -maxdepth 1 ! -name _work -exec rm -rf {} +

curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 -o /tmp/actions-runner.tar.gz \
	"https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-osx-arm64-${RUNNER_VERSION}.tar.gz"
printf '%s  /tmp/actions-runner.tar.gz\n' "$RUNNER_SHA" | shasum -a 256 -c -
tar xzf /tmp/actions-runner.tar.gz -C "$RUNNER_DIR"
rm -f /tmp/actions-runner.tar.gz

npm_workspace="$(mktemp -d)"
trap 'rm -rf "$npm_workspace"' EXIT
for runtime in node20 node24; do
	case "$runtime" in
	node20) tag="next-11" ;;
	node24) tag="latest" ;;
	esac
	prefix="${RUNNER_DIR}/externals/${runtime}"
	test -f "${prefix}/bin/node" || {
		echo "missing official runner runtime: ${runtime}" >&2
		exit 1
	}
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
			echo "Node 24 requires the compatible npm 12 release line" >&2
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
	echo "==> Installed runner ${runtime} npm ${version}"
done

# Fleet managers can start run.sh with `nohup ... &`: nohup ignores SIGHUP, and a
# background command of a non-interactive shell starts with SIGINT and SIGQUIT
# ignored. Every job process would inherit all three (GitHub's runners do not),
# so restore the defaults before the runner starts.
mv "${RUNNER_DIR}/run.sh" "${RUNNER_DIR}/run-runner.sh"
cat >"${RUNNER_DIR}/run.sh" <<'RUN'
#!/bin/bash
exec /usr/bin/perl -e '$SIG{HUP} = $SIG{INT} = $SIG{QUIT} = "DEFAULT"; exec @ARGV or die "exec: $!\n"' \
  "$(cd "$(dirname "$0")" && pwd)/run-runner.sh" "$@"
RUN
chmod +x "${RUNNER_DIR}/run.sh"

test -x "${RUNNER_DIR}/run.sh"
test -x "${RUNNER_DIR}/run-runner.sh"
sync
