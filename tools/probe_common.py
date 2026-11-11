#!/usr/bin/env python3
"""
<summary>
What every probe tool does before it touches the screen: refuses to run while the Space
LCD service is active, finds exactly one awake screen, and opens it exclusively.
</summary>
<remarks>
Two processes writing to the same serial port interleave their bytes, and on this screen
an interleaved upload is the one known way to corrupt a stored file. So a probe never
opens the port while the service holds it: it asks systemd first, and opens with
exclusive=True so a second opener (a probe started twice, or the service starting while
a probe runs) is refused by the kernel rather than sharing the line.

Imported by the probes as a plain module: they are run as scripts from the project root,
and Python puts the script's own folder, tools/, on the path.
</remarks>
"""

import subprocess
from typing import Optional

import serial
from serial.tools.list_ports import comports

AWAKE_IDS = ((0x1D6B, 0x0121), (0x1D6B, 0x0106))
"""<summary>USB vendor and product IDs of an awake revision C screen.</summary>"""

SERVICE = "space-lcd.service"


def service_state() -> str:
    """<summary>The systemd user service's active state, or "unknown" when systemctl cannot say.</summary>"""
    try:
        result = subprocess.run(["systemctl", "--user", "is-active", SERVICE], capture_output=True, text=True,
                                timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() or "unknown"


def find_screen() -> Optional[str]:
    """
    <summary>The device path of the one awake screen, or None with the reason printed.</summary>
    <remarks>Does not wake a sleeping screen (USB 1a86:ca21); the service does that when it starts.</remarks>
    """
    state = service_state()
    if state not in ("inactive", "failed", "unknown"):
        print("STOPPED: the Space LCD service is {}. Stop it first: systemctl --user stop space-lcd".format(state))
        return None
    if state == "unknown":
        print("Could not ask systemd whether the service is running; make sure it is stopped.")
    ports = [p for p in comports() if (p.vid, p.pid) in AWAKE_IDS]
    if len(ports) != 1:
        print("STOPPED: expected exactly one awake screen, found {}. Nothing sent.".format(len(ports)))
        return None
    return ports[0].device


def open_screen(device: str) -> serial.Serial:
    """
    <summary>Opens the screen's port the way the service does, but exclusively.</summary>
    <exception cref="serial.SerialException">The port is held by another process, or gone.</exception>
    """
    return serial.Serial(device, 115200, timeout=1, rtscts=True, exclusive=True)
