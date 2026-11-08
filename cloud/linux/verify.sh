#!/usr/bin/env bash
# These execute inside the provisioning VM. Fresh-image boot tests are separate.
set -euo pipefail
exec > >(tee /tmp/runner-evidence/toolchain-check.txt) 2>&1
for command in gcc clang cmake ninja git git-lfs curl jq python3 node npm go java rustc cargo docker; do
	command -v "$command"
done
/opt/actions-runner/bin/Runner.Listener --version
python3 --version
node --version
go version
java -version
rustc --version
cargo --version
git-lfs version
npm --version
for runtime in node20 node24; do
	/opt/actions-runner/externals/"$runtime"/bin/node /opt/actions-runner/externals/"$runtime"/lib/node_modules/npm/bin/npm-cli.js --version
done
docker compose version
docker buildx version
sudo docker info --format '{{.ServerVersion}}'
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
printf '#include <stdio.h>\nint main(void){puts("runner-smoke");return 0;}\n' >"$work/hello.c"
gcc "$work/hello.c" -o "$work/hello"
[[ $("$work/hello") == runner-smoke ]]
printf 'fn main(){print!("runner-smoke");}\n' >"$work/hello.rs"
rustc "$work/hello.rs" -o "$work/hello-rust"
[[ $("$work/hello-rust") == runner-smoke ]]
cargo new --quiet --bin --vcs none "$work/cargo-smoke"
cargo build --quiet --offline --manifest-path "$work/cargo-smoke/Cargo.toml"
"$work/cargo-smoke/target/debug/cargo-smoke"
python3 -m venv "$work/venv"
"$work/venv/bin/python" -c 'import ssl, sqlite3, venv; assert ssl.OPENSSL_VERSION'
node -e 'require("node:assert").strictEqual(2 + 2, 4)'
printf 'package main\nimport "fmt"\nfunc main(){fmt.Print("runner-smoke")}\n' >"$work/hello.go"
[[ $(go run "$work/hello.go") == runner-smoke ]]
python3 - <<'PY'
import json
import sys
sys.path.insert(0, '/tmp/runner-source')
from runner_images.inventory import collect
with open('/tmp/runner-evidence/inventory.json', 'w') as stream:
    json.dump(collect(), stream, indent=2)
PY
# Remove package downloads before scanning the installed image, not after it.
sudo apt-get clean
sudo env SYFT_GOLANG_CAPTURE_SYMBOLS=all /tmp/runner-tools/syft scan dir:/ --override-default-catalogers image \
	--exclude './proc/**' --exclude './sys/**' --exclude './dev/**' \
	--exclude './tmp/**' --exclude './var/log/**' --exclude './var/lib/cloud/**' \
	--exclude './var/lib/waagent/**' --exclude './root/**' \
	--exclude './home/**/.ssh/**' --exclude './var/cache/apt/archives/**' \
	-o syft-json=/tmp/runner-evidence/sbom.syft.json \
	-o spdx-json=/tmp/runner-evidence/sbom.spdx.json
