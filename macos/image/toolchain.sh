#!/bin/bash
# Executed inside the guest. Match GitHub's current macOS toolset for command
# line build tools, default runtimes and the Python/Node/Go tool cache.
set -euo pipefail

main() {
	eval "$(/opt/homebrew/bin/brew shellenv)"
	export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_ENV_HINTS=1
	local toolset formula ruby_minor python_minor node_major
	work=$(mktemp -d /tmp/runner-toolchain.XXXXXX)
	trap 'rm -rf "$work"' EXIT
	curl -fsSL --retry 5 \
		https://raw.githubusercontent.com/actions/runner-images/main/images/macos/toolsets/toolset-26.json \
		-o "$work/toolset.json"
	toolset="$work/toolset.json"
	jq -e '
    (.brew.common_packages | type == "array" and length > 0) and
    ([.toolcache[] | select(.url)] | length > 0) and
    all(.toolcache[] | select(.url); (.arch.arm64.versions | type == "array" and length > 0))
  ' "$toolset" >/dev/null
	ruby_minor=$(jq -er '.ruby.default' "$toolset")
	python_minor=$(jq -er '.toolcache[] | select(.name == "Python") | .arch.arm64.versions | map(split(".")[0:2] | map(tonumber)) | sort | last | map(tostring) | join(".")' "$toolset")
	node_major=$(jq -er '.node.default' "$toolset")
	brew update --quiet >&2
	while IFS= read -r formula; do
		# Packer is distributed through HashiCorp's tap, as in the hosted image.
		if [[ "$formula" == packer ]]; then formula=hashicorp/tap/packer; fi
		if [[ "$formula" == azure-cli ]]; then
			brew install --cask --quiet "$formula" </dev/null >&2
		else
			brew install --quiet "$formula" </dev/null >&2
		fi
	done < <(jq -er '.brew.common_packages[]' "$toolset")
	brew install --quiet "ruby@$ruby_minor" "python@$python_minor" "node@$node_major" rustup pipx jq curl wget >&2

	local job_path prefix
	prefix=$(brew --prefix rustup)
	job_path="$prefix/bin:$HOME/.cargo/bin"
	for formula in "ruby@$ruby_minor" "python@$python_minor" "node@$node_major"; do
		prefix=$(brew --prefix "$formula")
		job_path+=":$prefix/bin"
		if [[ -d "$prefix/libexec/bin" ]]; then job_path+=":$prefix/libexec/bin"; fi
		if [[ "$formula" == ruby* ]]; then
			job_path+=":$("$prefix/bin/ruby" -e 'print Gem.default_bindir')"
		fi
	done
	while IFS= read -r formula; do
		if [[ "$formula" == packer ]]; then formula=hashicorp/tap/packer; fi
		if [[ "$formula" == azure-cli ]]; then continue; fi
		prefix=$(brew --prefix "$formula")
		if [[ -d "$prefix/bin" ]]; then job_path+=":$prefix/bin"; fi
		if [[ -d "$prefix/sbin" ]]; then job_path+=":$prefix/sbin"; fi
	done < <(jq -er '.brew.common_packages[]' "$toolset")
	job_path+=":/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
	export PATH="$job_path"
	rustup set profile minimal >&2
	rustup default stable >&2
	rustup component add rustfmt clippy >&2
	local gem_name
	while IFS= read -r gem_name; do
		gem install --no-document "$gem_name" </dev/null >&2
	done < <(jq -er '.ruby.rubygems[]' "$toolset")

	# The setup-* actions and the tarballs' setup.sh share this cache contract.
	export RUNNER_TOOL_CACHE="$HOME/hostedtoolcache"
	export AGENT_TOOLSDIRECTORY="$RUNNER_TOOL_CACHE"
	mkdir -p "$RUNNER_TOOL_CACHE"
	mkdir -p "$HOME/.runner-image"
	printf 'LANG=en_US.UTF-8\nPIPX_HOME=%s/.local/pipx\nPIPX_BIN_DIR=%s/.local/bin\n' "$HOME" "$HOME" >"$HOME/.runner-image/environment"
	job_path="$HOME/.local/bin:$job_path"
	local java_major metadata java_version java_cache java_work java_root java_url java_sha
	while IFS= read -r java_major; do
		metadata="$work/java-$java_major.json"
		curl -fsSL --retry 5 "https://api.adoptium.net/v3/assets/feature_releases/$java_major/ga?architecture=aarch64&heap_size=normal&image_type=jdk&jvm_impl=hotspot&os=mac&page=0&page_size=1&project=jdk&sort_order=DESC&vendor=eclipse" -o "$metadata"
		java_version=$(jq -er '.[0].version_data.semver | gsub("\\+"; "-")' "$metadata")
		java_cache="$RUNNER_TOOL_CACHE/Java_Temurin-Hotspot_jdk/$java_version"
		if [[ ! -f "$java_cache/arm64.complete" ]]; then
			java_url=$(jq -er '.[0].binaries[0].package.link' "$metadata")
			java_sha=$(jq -er '.[0].binaries[0].package.checksum' "$metadata")
			java_work="$work/java-$java_major"
			mkdir -p "$java_work"
			curl -fsSL --retry 5 "$java_url" -o "$java_work/jdk.tar.gz"
			printf '%s  %s\n' "$java_sha" "$java_work/jdk.tar.gz" | shasum -a 256 -c - >&2
			tar -xzf "$java_work/jdk.tar.gz" -C "$java_work"
			java_root=$(find "$java_work" -mindepth 1 -maxdepth 1 -type d | head -1)
			test -x "$java_root/Contents/Home/bin/java"
			mkdir -p "$java_cache"
			rm -rf "$java_cache/arm64"
			mv "$java_root" "$java_cache/arm64"
			touch "$java_cache/arm64.complete"
			rm -rf "$java_work"
		fi
		printf 'JAVA_HOME_%s_arm64=%s/arm64/Contents/Home\n' "$java_major" "$java_cache" >>"$HOME/.runner-image/environment"
		if [[ "$java_major" == "$(jq -er '.java.arm64.default' "$toolset")" ]]; then
			export JAVA_HOME="$java_cache/arm64/Contents/Home"
			printf 'JAVA_HOME=%s\n' "$JAVA_HOME" >>"$HOME/.runner-image/environment"
		fi
	done < <(jq -er '.java.arm64.versions[]' "$toolset")
	test -x "$JAVA_HOME/bin/java"
	job_path="$JAVA_HOME/bin:$job_path"
	local name cache_name url pattern selected version archive tool_work manifest
	while IFS='|' read -r name url; do
		cache_name="$name"
		case "$name" in Go) cache_name=go ;; Node) cache_name=node ;; esac
		manifest="$work/$name.json"
		curl -fsSL --retry 5 "$url" -o "$manifest"
		while IFS= read -r pattern; do
			selected=$(jq -er --arg pattern "$pattern" '
        [ .[] | select(.version | test("^[0-9]+\\.[0-9]+\\.[0-9]+$")) |
          select(.version | startswith($pattern | rtrimstr("*"))) |
          .version as $version | .files[] |
          select(.platform == "darwin" and .arch == "arm64") |
          {version:$version, url:.download_url} ]
        | sort_by(.version | split(".") | map(tonumber)) | last
        // error("No stable Darwin arm64 artifact for " + $pattern)' "$manifest")
			version=$(jq -er '.version' <<<"$selected")
			if [[ -f "$RUNNER_TOOL_CACHE/$cache_name/$version/arm64.complete" ]]; then continue; fi
			archive=$(jq -er '.url' <<<"$selected")
			tool_work="$work/$name-$version"
			mkdir -p "$tool_work"
			curl -fsSL --retry 5 "$archive" -o "$tool_work/tool.tar.gz"
			tar -xzf "$tool_work/tool.tar.gz" -C "$tool_work"
			rm "$tool_work/tool.tar.gz"
			(cd "$tool_work" && /bin/bash ./setup.sh </dev/null) >&2
			test -f "$RUNNER_TOOL_CACHE/$cache_name/$version/arm64.complete"
			rm -rf "$tool_work"
		done < <(jq -er --arg name "$name" '.toolcache[] | select(.name == $name) | .arch.arm64.versions[]' "$toolset")
	done < <(jq -er '.toolcache[] | select(.url) | .name + "|" + .url' "$toolset")

	# Expose the newest cached Go compiler for scripts that invoke Go directly.
	local go_version go_variable cache_version
	go_version=$(find "$RUNNER_TOOL_CACHE/go" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
	test -n "$go_version"
	while IFS= read -r prefix; do
		cache_version=${prefix##*/}
		go_variable="GOROOT_$(cut -d. -f1,2 <<<"$cache_version" | tr . _)_ARM64"
		printf '%s=%s/arm64\n' "$go_variable" "$prefix" >>"$HOME/.runner-image/environment"
	done < <(find "$RUNNER_TOOL_CACHE/go" -mindepth 1 -maxdepth 1 -type d | sort -t. -k1,1n -k2,2n -k3,3n)
	for formula in HOMEBREW_PREFIX HOMEBREW_CELLAR HOMEBREW_REPOSITORY HOMEBREW_NO_AUTO_UPDATE HOMEBREW_NO_INSTALL_CLEANUP; do
		printf '%s=%s\n' "$formula" "${!formula}" >>"$HOME/.runner-image/environment"
	done
	job_path="$RUNNER_TOOL_CACHE/go/$go_version/arm64/bin:$job_path"
	export PATH="$job_path"
	for formula in git gh jq python3 node ruby go rustc cargo rustfmt cargo-clippy cmake ninja clang; do
		command -v "$formula" >&2
	done
	mkdir -p "$HOME/.runner-image"
	cp "$toolset" "$HOME/.runner-image/github-macos-toolset.json"
	printf '%s\n' "$job_path"
}

main "$@" </dev/null
