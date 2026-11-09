#!/usr/bin/env bash
# Catalogue installed guest software before the stopped-image qualification hash.
set -euo pipefail

work=$(mktemp -d /private/tmp/runner-image-sbom-scanner.XXXXXX)
trap 'rm -rf "$work"' EXIT

release=$(curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 \
	https://api.github.com/repos/anchore/syft/releases/latest)
asset=$(jq -er '
	.assets[]
	| select(.name | test("^syft_.*_darwin_arm64\\.tar\\.gz$"))
' <<<"$release")
url=$(jq -er '.browser_download_url' <<<"$asset")
digest=$(jq -er '.digest' <<<"$asset")
if [[ ! "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]; then
	echo "syft release asset is missing a valid sha256 digest" >&2
	exit 1
fi
sha256=${digest#sha256:}

archive="$work/syft.tar.gz"
curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 "$url" -o "$archive"
printf '%s  %s\n' "$sha256" "$archive" | shasum -a 256 -c - >&2
tar -xzf "$archive" -C "$work" syft
scanner="$work/syft"
test -x "$scanner"

mkdir -p /private/tmp/runner-image-sbom
# Installed system tools include root-readable directories such as authserver.
# Elevate only the verified scanner inside this disposable build guest.
sudo -n /usr/bin/env SYFT_GOLANG_CAPTURE_SYMBOLS=all "$scanner" scan dir:/ --override-default-catalogers image --scope all-layers \
	--exclude './dev/**' --exclude './System/**' --exclude './private/**' \
	--exclude './Volumes/**' --exclude './Users/admin/Library/**' \
	-o syft-json=/private/tmp/runner-image-sbom/sbom.syft.json \
	-o spdx-json=/private/tmp/runner-image-sbom/sbom.spdx.json
