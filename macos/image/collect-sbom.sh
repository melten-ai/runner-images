#!/usr/bin/env bash
# Catalogue installed guest software before the stopped-image qualification hash.
set -euo pipefail
brew install syft >&2
mkdir -p /private/tmp/runner-image-sbom
export SYFT_GOLANG_CAPTURE_SYMBOLS=all
syft scan dir:/ --override-default-catalogers image --scope all-layers \
	--exclude './dev/**' --exclude './System/**' --exclude './private/**' \
	--exclude './Volumes/**' --exclude './Users/admin/Library/**' \
	-o syft-json=/private/tmp/runner-image-sbom/sbom.syft.json \
	-o spdx-json=/private/tmp/runner-image-sbom/sbom.spdx.json
