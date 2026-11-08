#!/usr/bin/env bash
# Run in a clean checkout of the destination branch, after downloading observations.
set -euo pipefail

source_directory=${1:?Observation directory is required}
branch=${2:?Destination branch is required}
test "$(git branch --show-current)" = "$branch"
test -z "$(git status --porcelain)"
git fetch origin "$branch"
git merge --ff-only "origin/$branch"
python3 scripts/update-snapshots.py "$source_directory" snapshots/github-hosted
git add -- snapshots/github-hosted
if git diff --cached --quiet; then
	echo 'Hosted inventories are unchanged; no commit.'
	exit 0
fi
git -c user.name='github-actions[bot]' \
	-c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
	commit -m 'chore: update GitHub-hosted runner inventories'
# A concurrent upstream edit rejects this push; never force or discard it.
git push origin "HEAD:$branch"
