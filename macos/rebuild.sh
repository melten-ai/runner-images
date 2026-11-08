#!/usr/bin/env bash
# Bootstrap for the single command in a rebuild issue. Persistent state permits resume.
set -euo pipefail
profile=${1:?Image profile is required}
[[ "$profile" == xcode || "$profile" == xcode-android ]]
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]]
command -v gh >/dev/null
gh auth status >/dev/null 2>&1
for tool in tart jq grype; do
	if ! command -v "$tool" >/dev/null; then brew install "$tool"; fi
done
if ! python3 -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
	brew install python@3.13
	python_prefix=$(brew --prefix python@3.13)
	export PATH="$python_prefix/libexec/bin:$PATH"
fi
state="${XDG_STATE_HOME:-$HOME/.local/state}/runner-images/melten-ai/$profile"
mkdir -p "$state"
if [[ ! -d "$state/source/.git" ]]; then
	gh repo clone https://github.com/melten-ai/runner-images.git "$state/source" -- --branch main
fi
cd "$state/source"
if [[ ! -f artifacts/rebuild.json ]] || python3 -c 'import json; assert json.load(open("artifacts/rebuild.json")).get("done")' 2>/dev/null; then
	git fetch origin main
	git merge --ff-only origin/main
fi
# This private checkout is owned by this command; ordinary developer trees are untouched.
python3 scripts/rebuild-macos.py "$profile"
