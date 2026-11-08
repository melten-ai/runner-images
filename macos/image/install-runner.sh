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

curl -fsSL -o /tmp/actions-runner.tar.gz \
	"https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-osx-arm64-${RUNNER_VERSION}.tar.gz"
printf '%s  /tmp/actions-runner.tar.gz\n' "$RUNNER_SHA" | shasum -a 256 -c -
tar xzf /tmp/actions-runner.tar.gz -C "$RUNNER_DIR"
rm -f /tmp/actions-runner.tar.gz

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
