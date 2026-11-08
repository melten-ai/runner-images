#!/bin/bash

# Select a physical interface that Tart can bridge. A VPN may own the default
# route through utun*, so the default route alone is not a safe signal.
select_tart_bridge_interface() {
	local default_interface candidate
	local candidates=()

	while IFS= read -r candidate; do
		[[ -n "$candidate" ]] && candidates+=("$candidate")
	done < <(
		networksetup -listallhardwareports 2>/dev/null | awk '
      /^Device: en[0-9]+$/ { print $2 }
    '
	)

	default_interface=$(route -n get default 2>/dev/null | awk '$1 == "interface:" { print $2; exit }' || true)
	if [[ -n "$default_interface" ]]; then
		for candidate in "${candidates[@]}"; do
			if [[ "$candidate" == "$default_interface" ]] && tart_bridge_interface_is_active "$candidate"; then
				printf '%s\n' "$candidate"
				return 0
			fi
		done
	fi

	for candidate in "${candidates[@]}"; do
		if tart_bridge_interface_is_active "$candidate"; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done

	echo "No active physical network interface is available for Tart bridged networking." >&2
	return 1
}

tart_bridge_interface_is_active() {
	ifconfig "$1" 2>/dev/null | grep -q 'status: active' &&
		[[ -n "$(ipconfig getifaddr "$1" 2>/dev/null || true)" ]]
}
