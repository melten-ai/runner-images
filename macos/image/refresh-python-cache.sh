#!/bin/bash
# Source-only helper for the streamed guest toolchain: defines
# refresh_python_cache and nothing else. No set options, no invocation here.
#
# actions/python-versions stops publishing binaries once a Python branch is in
# security-only maintenance, so the hosted-style tool cache would otherwise keep
# that branch's last binary release (3.11.9, 3.12.10) while Homebrew already
# ships the current security patch. refresh_python_cache MINOR installs or
# upgrades the official Homebrew python@MINOR formula, verifies the real
# interpreter version, publishes $RUNNER_TOOL_CACHE/Python/<version>/arm64/bin
# as command links into the Homebrew prefix, writes arm64.complete, and only
# then retires superseded older patches of the same minor. Messages go to
# stderr; stdout stays clean for the caller's job PATH. Bash 3.2 compatible.

refresh_python_cache() {
	local minor=${1-} minor_pattern='^3\.[0-9]+$' version_pattern='^3\.[0-9]+\.[0-9]+$'
	[[ $minor =~ $minor_pattern ]] || {
		echo "refresh_python_cache: expected a stable minor such as 3.12, got '$minor'" >&2
		return 1
	}
	if [ -z "${RUNNER_TOOL_CACHE:-}" ] || [ ! -d "$RUNNER_TOOL_CACHE" ]; then
		echo "refresh_python_cache: RUNNER_TOOL_CACHE must name an existing directory" >&2
		return 1
	fi
	local family="$RUNNER_TOOL_CACHE/Python" path
	for path in "$RUNNER_TOOL_CACHE" "$family"; do
		if [ -L "$path" ]; then
			echo "refresh_python_cache: refusing symlinked tool cache path $path" >&2
			return 1
		fi
	done

	local formula="python@$minor" prefix interpreter pip
	if brew list --versions "$formula" >/dev/null 2>&1 </dev/null; then
		brew upgrade --quiet "$formula" </dev/null >&2 || return 1
	else
		brew install --quiet "$formula" </dev/null >&2 || return 1
	fi
	prefix=$(brew --prefix "$formula" </dev/null) || return 1
	interpreter="$prefix/bin/python$minor"
	pip="$prefix/bin/pip$minor"
	for path in "$interpreter" "$pip"; do
		if [ ! -x "$path" ]; then
			echo "refresh_python_cache: $formula did not provide $path" >&2
			return 1
		fi
	done

	# The directory name must be the version the interpreter actually runs.
	local report version level
	report=$("$interpreter" -c 'import sys
v = sys.version_info
print("%d.%d.%d %s" % (v[0], v[1], v[2], v.releaselevel))' </dev/null) || return 1
	version=${report%% *}
	level=${report#* }
	if [ "$level" != final ] || ! [[ $version =~ $version_pattern ]] || [ "${version%.*}" != "$minor" ]; then
		echo "refresh_python_cache: $interpreter reports '$report', not a stable $minor release" >&2
		return 1
	fi

	local version_dir="$family/$version" cache="$family/$version/arm64" staging="$family/$version/arm64.partial" name linked
	for path in "$version_dir" "$cache" "$staging"; do
		if [ -L "$path" ]; then
			echo "refresh_python_cache: refusing symlinked cache entry $path" >&2
			return 1
		fi
	done
	rm -rf "$staging"
	mkdir -p "$staging/bin" || return 1
	for name in python python3 "python$minor"; do
		ln -s "$interpreter" "$staging/bin/$name" || return 1
	done
	for name in pip pip3 "pip$minor"; do
		ln -s "$pip" "$staging/bin/$name" || return 1
	done
	linked=$("$staging/bin/python" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' </dev/null) || linked=
	if [ "$linked" != "$version" ]; then
		echo "refresh_python_cache: linked python reports '$linked', expected $version" >&2
		rm -rf "$staging"
		rmdir "$version_dir" 2>/dev/null || true
		return 1
	fi
	linked=$("$staging/bin/pip" --version </dev/null) || linked=
	case "$linked" in
	*"(python $minor)") ;;
	*)
		echo "refresh_python_cache: linked pip reports '$linked', expected python $minor" >&2
		rm -rf "$staging"
		rmdir "$version_dir" 2>/dev/null || true
		return 1
		;;
	esac
	# Python.org's old cache installers also leave a shared framework. Keep
	# its same-minor consumers working against the verified Homebrew runtime.
	local legacy="/Library/Frameworks/Python.framework/Versions/$minor"
	local framework="$prefix/Frameworks/Python.framework/Versions/$minor"
	local backup="$legacy.runner-image-old"
	if [ -e "$legacy" ] || [ -L "$legacy" ]; then
		if [ -L "$legacy" ]; then
			[ "$(readlink "$legacy")" = "$framework" ] || {
				echo "refresh_python_cache: refusing unexpected framework link $legacy" >&2
				return 1
			}
		else
			if [ ! -x "$framework/bin/python$minor" ] || [ -e "$backup" ] || [ -L "$backup" ]; then
				echo "refresh_python_cache: framework target is unavailable or a backup already exists" >&2
				return 1
			fi
			sudo mv "$legacy" "$backup" </dev/null >&2 || return 1
			if ! sudo ln -s "$framework" "$legacy" </dev/null >&2; then
				sudo mv "$backup" "$legacy" </dev/null >&2 || true
				return 1
			fi
			linked=$("$legacy/bin/python$minor" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' </dev/null) || linked=
			if [ "$linked" != "$version" ]; then
				sudo rm "$legacy" </dev/null >&2 || return 1
				sudo mv "$backup" "$legacy" </dev/null >&2 || return 1
				echo "refresh_python_cache: framework verification failed; restored the old framework" >&2
				return 1
			fi
			sudo rm -rf "$backup" </dev/null >&2 || return 1
			echo "refresh_python_cache: refreshed shared Python $minor framework to $version" >&2
		fi
	fi
	rm -f "$version_dir/arm64.complete"
	rm -rf "$cache"
	mv "$staging" "$cache" || return 1
	touch "$version_dir/arm64.complete" || return 1
	echo "refresh_python_cache: published Python $version arm64 from $prefix" >&2

	# Retire only older patches of this minor; keep newer patches, other
	# minors, other architectures and anything unexpected in those directories.
	local patch=${version##*.} candidate old
	for candidate in "$family/$minor".*; do
		old=${candidate##*/}
		[[ $old =~ $version_pattern ]] || continue
		[ "${old##*.}" -lt "$patch" ] || continue
		if [ -L "$candidate" ] || [ -L "$candidate/arm64" ]; then
			echo "refresh_python_cache: leaving symlinked cache entry $candidate alone" >&2
			continue
		fi
		[ -d "$candidate" ] || continue
		if [ -e "$candidate/arm64" ] || [ -e "$candidate/arm64.complete" ]; then
			rm -rf "$candidate/arm64" "$candidate/arm64.complete" || return 1
			rmdir "$candidate" 2>/dev/null || true
			echo "refresh_python_cache: retired Python $old arm64 (superseded by $version)" >&2
		fi
	done
}
