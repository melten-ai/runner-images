#!/usr/bin/env bash
# Catalogue installed guest software before the stopped-image qualification hash.
set -euo pipefail
brew install syft >&2
syft scan dir:/ --scope all-layers \
	--exclude './dev/**' --exclude './System/**' --exclude './private/**' \
	--exclude './Volumes/**' --exclude './Users/admin/Library/**' \
	-o spdx-json
