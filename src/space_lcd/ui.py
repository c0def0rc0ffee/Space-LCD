"""
<summary>
The control panel: python -m space_lcd.ui. Picks the theme with live previews, and
sets brightness, rotation, update interval, the picture for the image theme, and whether
the background service is running and starts when the computer starts. With --tray it
runs as a system tray icon instead, which opens and closes the panel (tray.py).
</summary>
<remarks>
The panel never talks to the screen itself. It writes the settings file, which the
background service picks up within one update interval, and it starts and stops that
service through systemctl --user. So the screen only changes while the service runs, and
the previews here work whether or not the cooler is connected.

Previews are drawn with the same code as the screen, from live readings taken in this
process, so what you see is what the screen will show to the pixel, apart from scaling.
</remarks>
"""

import argparse
import getpass
import subprocess
import sys
from pathlib import Path
from typing import Callable, Tuple

from PIL import Image

from . import config, render
from .sensors import SensorReader, Stats

SERVICE = "space-lcd.service"
"""<summary>The systemd user unit install.sh installs.</summary>"""


class ServiceControl:
    """
    <summary>Starts, stops and reports on the background service through systemctl --user.</summary>
    <remarks>
    Each call runs systemctl and waits for it, capped at ten seconds for an action and
    two for a status query, since the panel asks for the status every few seconds on the
    window's own thread. Starting the service is what makes the screen change; this class
    sends nothing to the screen directly.
    </remarks>
    """

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        """<summary>runner is swapped for a fake in tests so no real service is touched.</summary>"""
        self._run = runner

    def _systemctl(self, *args: str, wait: float = 10.0) -> Tuple[int, str]:
        """<summary>Runs one systemctl --user command and returns its status and first line of output.</summary>"""
        try:
            result = self._run(["systemctl", "--user", *args], capture_output=True, text=True,
                               timeout=wait, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            return 1, str(error)
        output = (result.stdout or result.stderr or "").strip().splitlines()
        return result.returncode, output[0] if output else ""

    def status(self) -> Tuple[str, bool]:
        """
        <summary>Reports whether the service is running and whether its unit is enabled (lingering(), checked separately, decides boot or login).</summary>
        <returns>The active state ("active", "inactive", "failed", "activating", or
        "not installed") and whether it is enabled.</returns>
        """
        code, enabled = self._systemctl("is-enabled", SERVICE, wait=2.0)
        if code != 0 and (enabled in ("", "not-found") or "No such file" in enabled):
            return "not installed", False
        _, active = self._systemctl("is-active", SERVICE, wait=2.0)
        return active or "unknown", enabled == "enabled"

    def start(self) -> Tuple[int, str]:
        """<summary>Starts the service now.</summary>"""
        return self._systemctl("start", SERVICE)

    def stop(self) -> Tuple[int, str]:
        """<summary>Stops the service; it leaves a "stopped" frame on the screen.</summary>"""
        return self._systemctl("stop", SERVICE)

    def restart(self) -> Tuple[int, str]:
        """<summary>Stops and starts the service, which also reconnects to the screen.</summary>"""
        return self._systemctl("restart", SERVICE)

    def set_enabled(self, enabled: bool) -> Tuple[int, str]:
        """
        <summary>Turns starting when the computer starts on or off, without starting or stopping it now.</summary>
        <returns>The status and message of the first command that failed, else of the last one run.</returns>
        <remarks>
        On, it enables the unit and then turns on systemd lingering for this user, which is
        what starts a user service at boot rather than at login. Off, it only disables the
        unit: lingering is left on, since other user services may rely on it, and on its own
        it starts nothing of ours. Lingering is allowed for oneself without a password on
        stock systemd; where a distribution asks for one, the desktop's own password prompt
        appears, so that step waits up to a minute.
        </remarks>
        """
        code, message = self._systemctl("enable" if enabled else "disable", SERVICE)
        if code != 0 or not enabled or self.lingering():
            return code, message
        return self._loginctl("--no-pager", "enable-linger", getpass.getuser(), wait=60.0)

    def _loginctl(self, *args: str, wait: float = 10.0) -> Tuple[int, str]:
        """<summary>Runs one loginctl command and returns its status and first line of output.</summary>"""
        try:
            result = self._run(["loginctl", *args], capture_output=True, text=True, timeout=wait, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            return 1, str(error)
        output = (result.stdout or result.stderr or "").strip().splitlines()
        return result.returncode, output[0] if output else ""

    def lingering(self) -> bool:
        """
        <summary>Whether systemd starts this user's enabled services at boot, before anyone logs in.</summary>
        <returns>False when it is off or cannot be read, such as on a system without loginctl.</returns>
        """
        code, value = self._loginctl("--value", "show-user", getpass.getuser(), "--property=Linger", wait=2.0)
        return code == 0 and value == "yes"


def write_icon(path: Path) -> None:
    """
    <summary>Writes the app icon: the dial theme with fixed sample figures, 256 pixels square.</summary>
    <param name="path">Where to save the PNG.</param>
    <remarks>Used by install.sh for the menu entry. Corners are made transparent.</remarks>
    """
    from datetime import datetime

    sample = Stats(cpu_temp=46, cpu_load=35, gpu_temp=48, gpu_load=20,
                   ram_used_gb=16.0, ram_total_gb=64.0, now=datetime(2026, 1, 1, 12, 0))
    frame = render.draw_frame(sample, theme="dial").convert("RGBA")
    mask = render._round_mask()
    frame.putalpha(mask)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.resize((256, 256), Image.Resampling.LANCZOS).save(path)


def main(argv=None) -> int:
    """
    <summary>Starts the control panel, the tray icon with --tray, or with --write-icon only writes the icon.</summary>
    <remarks>
    --show with --tray opens the panel at once, or asks a tray that is already running to
    open it; the menu entry uses that, so it never opens a second panel beside the tray's.
    </remarks>
    """
    parser = argparse.ArgumentParser(prog="space_lcd.ui", description="Space LCD control panel.")
    parser.add_argument("--config", type=Path, default=config.CONFIG_PATH, help="settings file")
    parser.add_argument("--write-icon", type=Path, metavar="FILE", help="write the app icon and exit")
    parser.add_argument("--tray", action="store_true", help="run as a system tray icon")
    parser.add_argument("--show", action="store_true", help="with --tray, open the control panel too")
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    render.set_font_root(config.try_load(args.config)[0].upstream_dir)

    if args.write_icon:
        write_icon(args.write_icon)
        return 0

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .window import ControlPanel

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Space LCD")
    app.setDesktopFileName("space-lcd")
    icon = config.APP_HOME / "icon.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    if args.tray:
        from . import tray
        return tray.run(app, lambda: ControlPanel(args.config, ServiceControl(), SensorReader()), icon, args.show)
    window = ControlPanel(args.config, ServiceControl(), SensorReader())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
