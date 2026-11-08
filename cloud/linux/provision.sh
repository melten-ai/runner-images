#!/usr/bin/env bash
# Shared maintained base profile, not the full GitHub-hosted software catalogue.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo cloud-init status --wait
sudo apt-get update
sudo -E apt-get -y -o Dpkg::Options::=--force-confdef \
	-o Dpkg::Options::=--force-confold upgrade
sudo -E apt-get install -y --no-install-recommends \
	build-essential clang cmake ninja-build git curl wget jq ca-certificates gnupg \
	zip unzip xz-utils python3 python3-venv python3-pip \
	openjdk-21-jdk-headless
# Use Docker's signed upstream packages rather than the older distro bundle.
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 https://download.docker.com/linux/ubuntu/gpg -o /tmp/runner-docker.asc
sudo install -m 0644 /tmp/runner-docker.asc /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable' | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo -E apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-compose-plugin
# The official Buildx security release can precede its apt package. Remove the
# older package completely before installing the verified upstream plugin.
sudo apt-get remove -y docker-buildx-plugin
sudo python3 /tmp/runner-source/install-toolchains.py /usr/local /tmp/runner-evidence/toolchains.json --specifications /tmp/runner-source/toolchains-plan.json
sudo ln -s /usr/local/go/bin/go /usr/local/bin/go
sudo ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt
# Refresh mounted Snap packages as well as apt packages before measuring the image.
if command -v snap >/dev/null; then
	sudo snap refresh
	while read -r name revision; do
		sudo snap remove "$name" --revision="$revision"
	done < <(LC_ALL=C snap list --all | awk 'NR > 1 && $NF ~ /(^|,)disabled(,|$)/ {print $1, $3}')
fi
sudo systemctl enable --now docker
sudo usermod -aG docker runnerbuilder
sudo mkdir -p /opt/actions-runner
sudo chown runnerbuilder:runnerbuilder /opt/actions-runner
cd /opt/actions-runner
url=$(jq -er '."actions-runner".url' /tmp/runner-source/toolchains-plan.json)
checksum=$(jq -er '."actions-runner".sha256' /tmp/runner-source/toolchains-plan.json)
curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 "$url" -o /tmp/actions-runner.tar.gz
printf '%s  %s\n' "$checksum" /tmp/actions-runner.tar.gz | sha256sum --check
tar xzf /tmp/actions-runner.tar.gz
sudo ./bin/installdependencies.sh
sudo python3 /tmp/runner-source/install-toolchains.py /opt/actions-runner /tmp/runner-evidence/toolchains.json --runner-npm
# No repository/organization registration, tokens, or cloud identity in the VM.
# The scanner is a temporary build dependency, not a shipped runner tool.
url=$(jq -er '.syft.url' /tmp/runner-source/toolchains-plan.json)
checksum=$(jq -er '.syft.sha256' /tmp/runner-source/toolchains-plan.json)
curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 "$url" -o /tmp/syft.tar.gz
printf '%s  %s\n' "$checksum" /tmp/syft.tar.gz | sha256sum --check
mkdir -p /tmp/runner-tools
tar xzf /tmp/syft.tar.gz -C /tmp/runner-tools syft
