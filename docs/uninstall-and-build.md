<!--
<summary>Space LCD: how to uninstall it, how to build a release, and where things live in the source tree.</summary>
<remarks>Moved from the README with light edits; the README links each section here.</remarks>
-->

# Uninstalling, building and layout

Back to the [README](../README.md).

## Uninstalling

Stop the service and turn off its start at boot:

```bash
systemctl --user disable --now space-lcd
```

Then remove what `install.sh` put in place:

- `~/.local/share/space-lcd/` (the Python environment, app copy and upstream driver)
- `~/.config/space-lcd/` (your settings)
- `~/.local/state/space-lcd/` (the working folder)
- `~/.config/systemd/user/space-lcd.service`
- `~/.local/share/applications/space-lcd.desktop`
- `~/.config/autostart/space-lcd-tray.desktop`

On a Kraken, also remove `/etc/udev/rules.d/71-space-lcd-kraken.rules` (with sudo). Clips
the program stored on a SPACE stay on its storage; they are named `spacelcd_*` in
`/mnt/SDCARD/video/`.

## Building a release

```bash
./build-zip.sh
```

Runs the tests (none of which open the screen) and the house rules gate, packages the Dist
and Git zips, and updates the local App copy. Add `--no-bump` to rebuild the current
version without consuming a number.

## Layout

- `src/space_lcd/` the program: sensors, themes, the cooler drivers (`panel.py` for the
  SPACE, `kraken.py`), stored video, the service loop (`__main__.py`), the control panel
  (`ui.py`, `window.py`) and its tray icon (`tray.py`). `assets/` holds the Machine Cult's
  emblem picture.
- `tests/` unit tests; none of them open the screen.
- `service/` the systemd user unit.
- `tools/` the Kraken udev rule, the house rules check, and probe tools that refuse to run
  while the service is.
- `docs/` recovery notes, the stored video plan, this guide, the usage guide and the README
  screenshots.
- `install.sh` per user install.
