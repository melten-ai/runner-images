#!/usr/bin/env bash
# Commit only generated release receipts; preserve concurrent main history.
set -euo pipefail
git add -- qualifications/tart
if git diff --cached --quiet; then exit 0; fi
git -c user.name='github-actions[bot]' \
	-c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
	commit -m 'chore: record published Tart release'
git fetch origin main
if ! git -c user.name='github-actions[bot]' \
	-c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
	rebase origin/main; then
	git rebase --abort
	exit 1
fi
git push origin HEAD:main
