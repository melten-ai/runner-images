#!/bin/bash
# Executed inside the guest. Match GitHub's current macOS toolset for command
# line build tools, default runtimes and the Python/Node/Go tool cache.
set -euo pipefail

image_formula_name() {
	case "$1" in
	packer) printf '%s\n' hashicorp/tap/packer ;;
	applesimutils) printf '%s\n' wix-incubator/brew/applesimutils ;;
	*) printf '%s\n' "$1" ;;
	esac
}

install_image_formula() {
	local formula
	formula=$(image_formula_name "$1")
	case "$formula" in
	azure-cli)
		brew install --cask --quiet "$formula" </dev/null >&2 || return 1
		brew upgrade --cask --quiet "$formula" </dev/null >&2 || return 1
		return
		;;
	hashicorp/tap/packer | wix-incubator/brew/applesimutils)
		# Authorize only the required formula, never an entire inherited tap.
		brew trust --formula "$formula" </dev/null >&2 || return 1
		;;
	*/*)
		echo "Unreviewed image formula tap: $formula" >&2
		return 1
		;;
	esac
	brew install --quiet "$formula" </dev/null >&2 || return 1
	if [[ "$formula" == */* ]]; then
		brew upgrade --formula --quiet "$formula" </dev/null >&2 || return 1
	fi
}

upgrade_core_formulas() {
	local installed formula
	local formulas=()
	installed=$(brew list --formula --full-name </dev/null) || return 1
	while IFS= read -r formula; do
		case "$formula" in
		'') continue ;;
		homebrew/core/*) formula=${formula#homebrew/core/} ;;
		*/*) continue ;;
		esac
		[[ "$formula" =~ ^[a-z0-9][a-z0-9@+._-]*$ ]] || return 1
		formulas+=("$formula")
	done <<<"$installed"
	if ((${#formulas[@]})); then
		brew upgrade --formula --quiet "${formulas[@]}" </dev/null >&2 || return 1
	fi
}

# go.dev/dl's default response contains the currently supported release lines.
# Retain that resolved policy in the effective toolset used by verification.
configure_supported_go() {
	local toolset=$1 releases=$2
	jq -e --slurpfile releases "$releases" '
    ([$releases[0][] | select(.stable == true) | .version |
      select(test("^go1\\.[0-9]+\\.[0-9]+$")) | ltrimstr("go")] | unique) as $versions |
    if ($versions | length) == 0 or ([.toolcache[] | select(.name == "Go")] | length) != 1
    then error("Supported Go releases or hosted Go cache are missing")
    else .toolcache |= map(if .name == "Go" then .arch.arm64.versions = $versions else . end) |
      .runner_image_go_policy = {source: "https://go.dev/dl/?mode=json", versions: $versions}
    end
  ' "$toolset" >"$toolset.supported" || return 1
	mv "$toolset.supported" "$toolset" || return 1
}

retire_unsupported_go() {
	local toolset=$1 family="$RUNNER_TOOL_CACHE/go" versions version candidate report
	versions=$(jq -er '.runner_image_go_policy.versions[]' "$toolset") || return 1
	for candidate in "$RUNNER_TOOL_CACHE" "$family"; do
		[ ! -L "$candidate" ] || return 1
	done
	# No retirement until every replacement is present and actually executes.
	while IFS= read -r version; do
		candidate="$family/$version"
		[ ! -L "$candidate" ] && [ ! -L "$candidate/arm64" ] || return 1
		test -f "$candidate/arm64.complete" || return 1
		report=$("$candidate/arm64/bin/go" version) || return 1
		[ "$report" = "go version go$version darwin/arm64" ] || return 1
	done <<<"$versions"
	for candidate in "$family"/*; do
		version=${candidate##*/}
		[[ $version =~ ^1\.[0-9]+\.[0-9]+$ ]] || continue
		if jq -e --arg version "$version" '.runner_image_go_policy.versions | index($version) != null' "$toolset" >/dev/null; then continue; fi
		# Never follow inherited links or remove another architecture.
		if [ -L "$candidate" ] || [ -L "$candidate/arm64" ]; then continue; fi
		[ -d "$candidate" ] || continue
		rm -rf "$candidate/arm64" "$candidate/arm64.complete" || return 1
		rmdir "$candidate" 2>/dev/null || true
		echo "Retired unsupported/superseded Go $version arm64 cache" >&2
	done
}

# Image-building tools run on the builder or as verified temporary scanners.
omit_guest_build_tools() {
	local toolset=$1
	jq -e '
    if (.brew.common_packages | type) != "array" then error("Missing hosted formulas")
    else .brew.common_packages |= map(select(. != "packer" and . != "hashicorp/tap/packer" and . != "syft")) |
      .runner_image_omissions = ["packer", "syft"] end
  ' "$toolset" >"$toolset.without-build-tools" || return 1
	mv "$toolset.without-build-tools" "$toolset" || return 1
}

# These extras are not part of this image's job tool contract.
retire_unused_inherited_tools() {
	local formula users candidate version
	for formula in otel-cli gitlab-runner syft hashicorp/tap/packer; do
		if brew list --versions "$formula" </dev/null >/dev/null 2>&1; then
			users=$(brew uses --installed "$formula" </dev/null) || return 1
			if [ -n "$users" ]; then
				echo "Retaining $formula: installed formula dependents exist" >&2
				continue
			fi
			# Remove every inherited keg; otherwise upgrade reinstalls the tool.
			brew uninstall --formula --force "$formula" </dev/null >&2 || return 1
		fi
	done
	# Ruby jobs use patched Homebrew Ruby. Preserve links and unrelated versions.
	for candidate in "$HOME"/.rbenv/versions/2.7.*; do
		version=${candidate##*/}
		[[ $version =~ ^2\.7\.[0-9]+$ ]] || continue
		if [ -L "$HOME/.rbenv" ] || [ -L "$HOME/.rbenv/versions" ] || [ -L "$candidate" ]; then continue; fi
		[ -d "$candidate" ] || continue
		rm -rf "$candidate" || return 1
		echo "Retired unused EOL Ruby $version with its private OpenSSL" >&2
	done
}

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
	mkdir -p "$HOME/.runner-image"
	cp "$toolset" "$HOME/.runner-image/upstream-github-macos-toolset.json"
	omit_guest_build_tools "$toolset"
	ruby_minor=$(jq -er '.ruby.default' "$toolset")
	python_minor=$(jq -er '.toolcache[] | select(.name == "Python") | .arch.arm64.versions | map(split(".")[0:2] | map(tonumber)) | sort | last | map(tostring) | join(".")' "$toolset")
	node_major=$(jq -er '.node.default' "$toolset")
	brew update --quiet >&2
	while IFS= read -r formula; do
		install_image_formula "$formula"
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
		formula=$(image_formula_name "$formula")
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
	curl -fsSL --retry 5 'https://go.dev/dl/?mode=json' -o "$work/supported-go.json"
	configure_supported_go "$toolset" "$work/supported-go.json"
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
		if [[ "$name" == Python ]]; then
			while IFS= read -r pattern; do
				refresh_python_cache "$(cut -d. -f1,2 <<<"$pattern")"
			done < <(jq -er '.toolcache[] | select(.name == "Python") | .arch.arm64.versions[]' "$toolset")
			continue
		fi
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

	retire_unsupported_go "$toolset"

	# The inherited base can retain outdated Cellar kegs after installations.
	retire_unused_inherited_tools
	upgrade_core_formulas
	brew cleanup --prune=all </dev/null >&2

	# npm's patched dependencies can advance independently of Node archives.
	local node_prefix node_version
	while IFS= read -r formula; do
		node_prefix=$(brew --prefix "$formula")
		node_version=$("$node_prefix/bin/node" -p 'process.versions.node.split(".")[0]')
		refresh_image_npm "$node_prefix" "node$node_version"
	done < <(brew list --formula | /usr/bin/grep -E '^node(@[0-9]+)?$')
	while IFS= read -r node_prefix; do
		node_version=$("$node_prefix/bin/node" -p 'process.versions.node.split(".")[0]')
		refresh_image_npm "$node_prefix" "node$node_version"
	done < <(find "$RUNNER_TOOL_CACHE/node" -mindepth 2 -maxdepth 2 -type d -name arm64 | sort)
	refresh_global_pnpm "$(brew --prefix)"

	# Python 3.13+ removed lib2to3; retain the hosted command on patched 3.12.
	prefix=$(brew --prefix python@3.12)
	test -x "$prefix/bin/2to3-3.12"
	mkdir -p "$HOME/.local/bin"
	ln -sfn "$prefix/bin/2to3-3.12" "$HOME/.local/bin/2to3"

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
