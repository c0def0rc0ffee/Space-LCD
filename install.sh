#!/usr/bin/env bash
# <summary>
# Installs Space LCD for the current user. Needs no sudo and never talks to the
# screen: it only puts files in place, registers a systemd user service without starting,
# stopping or enabling it, adds the control panel to the application menu, and starts its
# tray icon at login.
# </summary>
# <remarks>
# Run from the App folder or an unpacked Dist zip:  bash install.sh
# (bash explicitly: the build and Syncthing both strip the execute bit.)
# Safe to run again to update. It refreshes the app copy, the Python packages and the
# upstream driver pin, keeps an existing settings file, and leaves the service state alone.
#
# Everything goes outside the synced project tree, because Syncthing strips exec bits and
# would carry the venv between machines:
#   ~/.local/share/space-lcd/   venv, app copy, upstream driver checkout
#   ~/.config/space-lcd/        config.ini
#   ~/.local/state/space-lcd/   working directory (the upstream log file lands here)
#   ~/.config/systemd/user/           space-lcd.service
#   ~/.local/share/applications/      space-lcd.desktop (the control panel's menu entry)
#   ~/.config/autostart/              space-lcd-tray.desktop (the tray icon at login)
#
# The screen appears as /dev/ttyACM*, which only the dialout group can open. This script
# checks for that and prints the one command to fix it, but cannot run it. On a PC with an
# NZXT Kraken 2023 it checks the Kraken's hidraw node instead.
# </remarks>
set -euo pipefail

# The upstream driver, pinned. Move the pin deliberately after checking what changed in
# library/lcd/lcd_comm_rev_c.py, never by following the default branch.
UPSTREAM_URL="https://github.com/mathoudebine/turing-smart-screen-python.git"
UPSTREAM_COMMIT="2b33ab4f00a096916dd6a1174441a53a7ec33b03"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_home="$HOME/.local/share/space-lcd"
config_dir="$HOME/.config/space-lcd"
state_dir="$HOME/.local/state/space-lcd"
unit_dir="$HOME/.config/systemd/user"
apps_dir="$HOME/.local/share/applications"
autostart_dir="$HOME/.config/autostart"

# <summary>Prints a message to stderr and stops.</summary>
die() { echo "ERROR: $*" >&2; exit 1; }

for tool in python3 git rsync; do
    command -v "$tool" >/dev/null || die "$tool not found: sudo apt install $tool"
done
python3 -c 'import venv, ensurepip' 2>/dev/null || die "python3 venv support missing: sudo apt install python3-venv"
[ -d "$here/src/space_lcd" ] || die "run this from the folder that holds src/space_lcd"

mkdir -p "$app_home" "$config_dir" "$state_dir" "$unit_dir"

echo "Python environment: $app_home/venv"
[ -x "$app_home/venv/bin/python" ] || python3 -m venv "$app_home/venv"
"$app_home/venv/bin/pip" install -q --upgrade -r "$here/requirements.txt"

echo "Upstream driver: $app_home/upstream at ${UPSTREAM_COMMIT:0:7}"
if [ ! -d "$app_home/upstream/.git" ]; then
    git clone -q "$UPSTREAM_URL" "$app_home/upstream"
fi
if ! git -C "$app_home/upstream" cat-file -e "$UPSTREAM_COMMIT^{commit}" 2>/dev/null; then
    git -C "$app_home/upstream" fetch -q origin
fi
git -C "$app_home/upstream" -c advice.detachedHead=false checkout -q "$UPSTREAM_COMMIT"

echo "App copy: $app_home/app"
rsync -a --delete --exclude='__pycache__/' "$here/src/" "$app_home/app/"

# Before 01/10/2026 the app was called CPU LCD Display and installed under these names.
# Nothing old is removed here: the leftovers are listed at the end for you to decide on.
old_name="cpu-lcd-display"
old_config="$HOME/.config/$old_name/config.ini"

# The fan and hamster screens play loops stored on the screen; they are drawn once here
# with ffmpeg so the service never waits for them. Without ffmpeg they are streamed instead.
# A settings file that does not load gets the default colours and a message, so a typo in
# it cannot stop the install half way, after the app copy but before the service unit.
if command -v ffmpeg >/dev/null; then
    PYTHONPATH="$app_home/app" "$app_home/venv/bin/python" -m space_lcd.fanvideo
    PYTHONPATH="$app_home/app" "$app_home/venv/bin/python" -m space_lcd.hamstervideo
else
    echo "Animation loops: ffmpeg not found (sudo apt install ffmpeg); the fan and hamster will be streamed instead"
fi

if [ -f "$config_dir/config.ini" ]; then
    echo "Settings: kept $config_dir/config.ini"
elif [ -f "$old_config" ]; then
    # A copy, not a move, with the upstream path pointed at the new install.
    sed "s#/$old_name/#/space-lcd/#g" "$old_config" > "$config_dir/config.ini"
    echo "Settings: copied from $old_config"
else
    cp "$here/config.default.ini" "$config_dir/config.ini"
    echo "Settings: created $config_dir/config.ini"
fi

cp "$here/service/space-lcd.service" "$unit_dir/space-lcd.service"
systemctl --user daemon-reload
echo "Service: installed, state unchanged"

# The menu entry is written here rather than shipped, because a .desktop file cannot
# expand ~ or $HOME and so has to carry this machine's absolute paths. Exec quotes them,
# since a home folder with a space in its path would otherwise split into two arguments.
# The menu entry goes through the tray (--tray --show): it starts the tray and opens the
# panel, or asks a tray that is already running to open it, so there is only ever one panel.
PYTHONPATH="$app_home/app" "$app_home/venv/bin/python" -m space_lcd.ui --write-icon "$app_home/icon.png"
mkdir -p "$apps_dir"
cat > "$apps_dir/space-lcd.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Space LCD
GenericName=Cooler screen control panel
Comment=Choose the theme and settings for the cooler's round screen
Exec=env PYTHONPATH="$app_home/app" "$app_home/venv/bin/python" -m space_lcd.ui --tray --show
Icon=$app_home/icon.png
Terminal=false
Categories=Settings;HardwareSettings;
StartupWMClass=space-lcd
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database -q "$apps_dir" || true
echo "Menu entry: Space LCD (control panel)"

# The tray icon at login, turned off in System Settings, Autostart. Depending on the desktop
# that removes the file or marks it Hidden=true, so the first install leaves a marker and a
# later one never brings back an entry that was removed, nor rewrites one marked hidden.
autostart_file="$autostart_dir/space-lcd-tray.desktop"
autostart_marker="$app_home/tray-autostart-offered"
mkdir -p "$autostart_dir"
if grep -qs '^Hidden=true' "$autostart_file" || { [ ! -e "$autostart_file" ] && [ -e "$autostart_marker" ]; }; then
    echo "Tray icon at login: left off, as set in System Settings"
else
    touch "$autostart_marker"
    cat > "$autostart_file" <<AUTOSTART
[Desktop Entry]
Type=Application
Name=Space LCD tray icon
Comment=Opens and closes the Space LCD control panel from the system tray
Exec=env PYTHONPATH="$app_home/app" "$app_home/venv/bin/python" -m space_lcd.ui --tray
Icon=$app_home/icon.png
Terminal=false
NoDisplay=true
X-KDE-autostart-phase=2
AUTOSTART
    echo "Tray icon at login: on (starts at your next login, or open Space LCD from the menu now)"
fi

echo
# An NZXT Kraken 2023 (USB 1e71:300e) is a HID device, not serial: it needs the udev rule
# from tools/setup-kraken-udev.sh, not dialout. Found from sysfs only, like the service.
kraken_node=""
for uevent in /sys/class/hidraw/hidraw*/device/uevent; do
    if [ -r "$uevent" ] && grep -qi '^HID_ID=.*:00001E71:0000300E$' "$uevent"; then
        kraken_node="/dev/$(basename "$(dirname "$(dirname "$uevent")")")"
        break
    fi
done
if [ -n "$kraken_node" ]; then
    if [ -r "$kraken_node" ] && [ -w "$kraken_node" ]; then
        echo "Kraken access: OK ($kraken_node)"
    else
        echo "Kraken access: MISSING for $kraken_node. Run this once:"
        echo "  sudo bash \"$here/tools/setup-kraken-udev.sh\""
    fi
# The account's groups and this session's can differ: a session started before usermod
# does not have the new group until the next login.
elif id -nG | tr ' ' '\n' | grep -qx dialout; then
    echo "Serial access: OK (you are in the dialout group)"
elif id -nG "$USER" | tr ' ' '\n' | grep -qx dialout; then
    echo "Serial access: OK after your next login (the account is in dialout, this session is not yet)"
else
    echo "Serial access: MISSING. Run this once, then log out and back in:"
    echo "  sudo usermod -aG dialout $USER"
fi
# <summary>Prints one leftover from the old name if it exists, with its size.</summary>
leftover() { [ -e "$1" ] && printf '  %s (%s)\n' "$1" "$(du -sh "$1" 2>/dev/null | cut -f1)"; return 0; }

old_unit="$unit_dir/$old_name.service"
if [ -e "$old_unit" ] || [ -e "$HOME/.local/share/$old_name" ] || [ -e "$apps_dir/$old_name.desktop" ]; then
    echo
    echo "Left over from the old CPU LCD Display install, not touched:"
    leftover "$old_unit"
    leftover "$HOME/.local/share/$old_name"
    leftover "$HOME/.config/$old_name"
    leftover "$HOME/.local/state/$old_name"
    leftover "$apps_dir/$old_name.desktop"
    if systemctl --user is-active -q "$old_name.service" 2>/dev/null; then
        echo "WARNING: the old $old_name service is RUNNING. Two services would fight over the"
        echo "screen. Stop and disable it before starting Space LCD:"
        echo "  systemctl --user disable --now $old_name.service"
    fi
fi

echo
echo "Open Space LCD from the application menu to choose a theme and start the"
echo "service. If the service is already running, restart it there to load this version."
