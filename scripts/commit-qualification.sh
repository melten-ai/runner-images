#!/usr/bin/env bash
# Verify evidence against the dispatched source, then append without replacing history.
set -euo pipefail
bundle=${1:?Qualification bundle is required}
branch=${2:?Destination branch is required}
test "$(git branch --show-current)" = "$branch"
test -z "$(git status --porcelain)"
python3 scripts/record-qualification.py "$bundle" "$PWD" "${@:3}"
git add -- qualifications/tart
if git diff --cached --quiet; then
	echo 'This exact image qualification is already recorded.'
	exit 0
fi
git -c user.name='github-actions[bot]' \
	-c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
	commit -m 'chore: record qualified Tart image'
# Rebase only this generated record over concurrent main changes; abort on conflict.
git fetch origin "$branch"
if ! git -c user.name='github-actions[bot]' \
	-c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
	rebase "origin/$branch"; then
	git rebase --abort
	exit 1
fi
git push origin "HEAD:$branch"
