#!/usr/bin/env bash
# Catalogue installed guest software before the stopped-image qualification hash.
set -euo pipefail
brew install syft >&2
export SYFT_GOLANG_CAPTURE_SYMBOLS=all
syft scan dir:/ --override-default-catalogers image --scope all-layers \
	--exclude './dev/**' --exclude './System/**' --exclude './private/**' \
	--exclude './Volumes/**' --exclude './Users/admin/Library/**' \
	-o syft-json
