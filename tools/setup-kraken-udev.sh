#!/usr/bin/env bash
# <summary>
# Installs the udev rule that lets the logged in user open an NZXT Kraken 2023's HID node,
# which is root only by default. Run once with sudo; safe to run again.
# </summary>
# <remarks>
# Sends nothing to the cooler. It writes one rules file, reloads udev and re-triggers the
# hidraw subsystem so the new permissions apply without unplugging. The USB node for the
# picture data (interface 0) is already open to all users on Kubuntu, so only hidraw needs
# a rule. uaccess gives the user at the seat access; plugdev 0660 covers the systemd user
# service when nobody is at the seat. Matches 1e71:300e only: the standard Kraken 2023.
# </remarks>

set -euo pipefail

RULES=/etc/udev/rules.d/71-space-lcd-kraken.rules
CONTENT='# Space LCD: NZXT Kraken 2023 (1e71:300e) HID node for the logged in user.
KERNEL=="hidraw*", ATTRS{idVendor}=="1e71", ATTRS{idProduct}=="300e", MODE="0660", GROUP="plugdev", TAG+="uaccess"'

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0" >&2
    exit 1
fi

if [[ -f "$RULES" ]] && [[ "$(cat "$RULES")" == "$CONTENT" ]]; then
    echo "Rule already in place: $RULES"
else
    printf '%s\n' "$CONTENT" > "$RULES"
    chmod 644 "$RULES"
    echo "Wrote $RULES"
fi

udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw --action=change
udevadm settle
ls -l /dev/hidraw* | grep -E 'plugdev' || echo "No hidraw node picked up the rule yet; is the cooler plugged in?"
