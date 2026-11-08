#!/usr/bin/env bash
# Generated cloud receipts only; retain concurrent macOS/reference commits.
set -euo pipefail
git add -- qualifications/cloud
if git diff --cached --quiet; then exit 0; fi
git -c user.name='github-actions[bot]' \
	-c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
	commit -m 'chore: record released cloud runner image'
git fetch origin main
if ! git -c user.name='github-actions[bot]' \
	-c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
	rebase origin/main; then
	git rebase --abort
	exit 1
fi
git push origin HEAD:main
