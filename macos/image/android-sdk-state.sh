#!/bin/bash
# Detect inherited Android command-line tools or reject unsafe layouts.
set -euo pipefail

android_sdk_numeric_version() {
	[[ "$1" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]
}

android_sdk_valid_latest_link() {
	local tools=$1 link=$2
	[[ -n "$link" && "$link" != /* && "$link" != *..* ]] || return 1
	android_sdk_numeric_version "$link" || return 1
	[[ ! -L "$tools/$link" && -d "$tools/$link" ]] || return 1
	return 0
}

android_sdk_entry_is_tools_root() {
	local tools=$1
	[[ -d "$tools" && ! -L "$tools" ]] || return 1
	if [[ -x "$tools/bin/sdkmanager" || -x "$tools/bin/android" ]]; then
		return 0
	fi
	return 1
}

android_sdk_resolve_latest_selection() {
	local tools=$1 link selected=''
	if [[ -L "$tools/latest" ]]; then
		link=$(readlink "$tools/latest" || true)
		android_sdk_valid_latest_link "$tools" "$link" || return 1
		selected="$tools/$link"
	elif [[ -d "$tools/latest" && ! -L "$tools/latest" ]]; then
		selected="$tools/latest"
	else
		return 1
	fi
	printf '%s\n' "$selected"
}

android_sdk_newest_versioned_tools() {
	local tools=$1 entry version selected='' best=''
	for entry in "$tools"/*; do
		[[ -e "$entry" ]] || continue
		version=${entry##*/}
		[[ "$version" == latest ]] && continue
		[[ -d "$entry" && ! -L "$entry" ]] || continue
		android_sdk_numeric_version "$version" || continue
		android_sdk_entry_is_tools_root "$entry" || continue
		selected=$entry
		if [[ -z "$best" ]]; then
			best=$selected
			continue
		fi
		if jq -en --arg old "${best##*/}" --arg current "$version" \
			'(($old | split(".") | map(tonumber)) + [0,0] | .[0:3]) < (($current | split(".") | map(tonumber)) + [0,0] | .[0:3])' \
			>/dev/null; then
			best=$selected
		fi
	done
	[[ -n "$best" ]] && printf '%s\n' "$best"
}

android_sdk_latest_link_unsafe() {
	local tools=$1
	[[ -L "$tools/latest" ]] || return 1
	local link
	link=$(readlink "$tools/latest" || true)
	android_sdk_valid_latest_link "$tools" "$link" || return 0
	return 1
}

android_sdk_reject_unsafe_cmdline_entries() {
	local tools=$1 entry name
	shopt -s nullglob
	for entry in "$tools"/*; do
		name=${entry##*/}
		if [[ "$name" == latest ]]; then
			if [[ -L "$entry" ]]; then
				android_sdk_latest_link_unsafe "$tools" && return 1
			elif [[ -e "$entry" && ! -d "$entry" ]]; then
				return 1
			fi
			continue
		fi
		[[ -L "$entry" ]] && return 1
	done
	return 0
}

android_sdk_selected_tools_root() {
	local sdk=${1:-$HOME/android-sdk} tools selected=''
	tools=$sdk/cmdline-tools
	[[ -d "$tools" && ! -L "$tools" ]] || return 1
	if [[ -L "$tools/latest" || -e "$tools/latest" ]]; then
		selected=$(android_sdk_resolve_latest_selection "$tools" || true)
	else
		selected=$(android_sdk_newest_versioned_tools "$tools" || true)
	fi
	[[ -n "$selected" ]] || return 1
	android_sdk_entry_is_tools_root "$selected" || return 1
	printf '%s\n' "$selected"
}

detect_android_sdk_state() {
	local sdk=${1:-$HOME/android-sdk} tools selected=''
	tools="$sdk/cmdline-tools"
	if [[ -L "$sdk" ]]; then
		echo unsafe
		return 0
	fi
	if [[ ! -e "$sdk" ]]; then
		echo absent
		return 0
	fi
	if [[ ! -d "$sdk" ]]; then
		echo unsafe
		return 0
	fi
	if [[ -L "$tools" ]]; then
		echo unsafe
		return 0
	fi
	if [[ ! -e "$tools" ]]; then
		echo absent
		return 0
	fi
	if [[ ! -d "$tools" ]]; then
		echo unsafe
		return 0
	fi
	if ! android_sdk_reject_unsafe_cmdline_entries "$tools"; then
		echo unsafe
		return 0
	fi
	if ! selected=$(android_sdk_selected_tools_root "$sdk"); then
		if [[ -L "$tools/latest" || -e "$tools/latest" ]]; then
			echo unsafe
		else
			echo absent
		fi
		return 0
	fi
	case "$selected" in
	"$sdk/cmdline-tools"/*) echo present ;;
	*) echo unsafe ;;
	esac
}

assert_android_sdk_safe_for_refresh() {
	local sdk=${1:-$HOME/android-sdk} state
	state=$(detect_android_sdk_state "$sdk")
	[[ "$state" == present ]]
}

if [[ "${1:-}" == detect || "${1:-}" == assert-writable ]] &&
	[[ "${BASH_SOURCE[0]-}" == "${0}" || -z "${BASH_SOURCE[0]:-}" ]]; then
	case "$1" in
	detect)
		if [[ -n "${2:-}" ]]; then
			detect_android_sdk_state "$2"
		else
			detect_android_sdk_state
		fi
		;;
	assert-writable)
		if [[ -n "${2:-}" ]]; then
			assert_android_sdk_safe_for_refresh "$2" || {
				echo 'Android SDK layout is unsafe for refresh.' >&2
				exit 1
			}
		else
			assert_android_sdk_safe_for_refresh || {
				echo 'Android SDK layout is unsafe for refresh.' >&2
				exit 1
			}
		fi
		;;
	esac
fi
