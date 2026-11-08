#!/usr/bin/env bash
# Bootstrap for the single command in a rebuild issue. Persistent state permits resume.
set -euo pipefail
profile=${1:?Image profile is required}
[[ "$profile" == xcode || "$profile" == xcode-android ]]
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]]
command -v gh >/dev/null
gh auth status --hostname github.com >/dev/null 2>&1
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
# Load fixes from main even after a pre-publication failure. Keep immutable
# uploaded candidates on their source until the hosted finalizer completes.
if [[ ! -f artifacts/rebuild.json ]] || python3 - "$profile" <<'PYTHON'
import json
import sys
from pathlib import Path
from runner_images.rebuild import input_id
journal = Path("artifacts/rebuild.json")
state = json.loads(journal.read_text())
if not state.get("done") and not state.get("released_receipt"):
    if state.get("recorded") or (Path("artifacts/tart") / str(state.get("image")) / "publication.json").exists():
        raise SystemExit(1)
    state.setdefault("input_id", input_id(Path.cwd(), sys.argv[1]))
    temporary = journal.with_suffix(".tmp")
    temporary.write_text(json.dumps(state) + "\n")
    temporary.replace(journal)
PYTHON
then
	git fetch origin main
	git merge --ff-only origin/main
fi
# This private checkout is owned by this command; ordinary developer trees are untouched.
python3 scripts/rebuild-macos.py "$profile"
