#!/usr/bin/env python3
"""
<summary>
Kraken Phase 0: reads the NZXT Kraken 2023's firmware version, which decides whether the
picture upload is the plain 2.x path (agreed) or the 1.x storage bucket path (not built).
</summary>
<remarks>
Without --send it finds the cooler from sysfs, prints the one report it would send, and
sends nothing. With --send it writes one 64 byte HID report to the cooler's hidraw node:
10 01 followed by 62 zero bytes (the first byte is the report number, as liquidctl's
KrakenX3._write sends it), then reads up to 12 replies for the one starting 11 01 and
prints bytes 0x11 to 0x13 of it as the version (liquidctl parse_firm_info). Other replies,
such as the 75 status reports the kernel driver asks for, are skipped and counted.

Nothing is written to the cooler's storage, and no setting changes. The nzxt-kraken3
kernel driver sends this same request itself when it binds. Needs the udev rule from
tools/setup-kraken-udev.sh. Run --send only on Rob's yes.
</remarks>
"""

import os
import select
import sys
from pathlib import Path
from typing import Optional

KRAKEN_2023 = ("1e71", "300e")
"""<summary>USB vendor and product ID of the standard Kraken 2023, as sysfs writes them.</summary>"""

REQUEST = bytes([0x10, 0x01]) + bytes(62)
"""<summary>The firmware info request, padded to the 64 byte report length.</summary>"""

REPLY_PREFIX = bytes([0x11, 0x01])
MAX_READS = 12
"""<summary>Replies read before giving up, as liquidctl's _MAX_READ_ATTEMPTS.</summary>"""

READ_TIMEOUT_S = 1.0


def find_hidraw() -> Optional[Path]:
    """
    <summary>The /dev/hidraw node of the one Kraken 2023, or None with the reason printed.</summary>
    <remarks>Reads sysfs only. Each hidraw node's device/uevent carries the HID_ID with vendor and product.</remarks>
    """
    wanted = "0003:0000{}:0000{}".format(KRAKEN_2023[0].upper(), KRAKEN_2023[1].upper())
    found = []
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            uevent = (node / "device" / "uevent").read_text()
        except OSError:
            continue
        if "HID_ID=" + wanted in uevent:
            found.append(Path("/dev") / node.name)
    if len(found) != 1:
        print("STOPPED: expected exactly one Kraken 2023, found {}. Nothing sent.".format(len(found)))
        return None
    return found[0]


def read_version(fd: int) -> Optional[str]:
    """
    <summary>Reads replies until the firmware one arrives; returns "major.minor.patch" or None.</summary>
    """
    skipped = 0
    for _ in range(MAX_READS):
        ready, _, _ = select.select([fd], [], [], READ_TIMEOUT_S)
        if not ready:
            print("No reply within {} s.".format(READ_TIMEOUT_S))
            break
        reply = os.read(fd, 64)
        if reply[:2] == REPLY_PREFIX and len(reply) > 0x13:
            print("Reply: {}".format(reply.hex(" ")))
            if skipped:
                print("({} other reports skipped)".format(skipped))
            return "{}.{}.{}".format(reply[0x11], reply[0x12], reply[0x13])
        skipped += 1
    print("No firmware reply among {} reports.".format(skipped))
    return None


def main() -> int:
    """<summary>Dry run by default; --send sends the one request.</summary><returns>0 on a version read, 1 otherwise.</returns>"""
    node = find_hidraw()
    if node is None:
        return 1
    print("Kraken 2023 at {}".format(node))
    print("Would send 64 bytes: {}".format(REQUEST.hex(" ")))
    if "--send" not in sys.argv[1:]:
        print("Dry run: nothing sent. Add --send to send it.")
        return 0
    try:
        fd = os.open(node, os.O_RDWR)
    except PermissionError:
        print("STOPPED: no permission on {}. Run: sudo bash tools/setup-kraken-udev.sh".format(node))
        return 1
    try:
        os.write(fd, REQUEST)
        version = read_version(fd)
    finally:
        os.close(fd)
    if version is None:
        return 1
    print("Firmware {}".format(version))
    print("2.x: the agreed picture path applies." if version.startswith("2.")
          else "Not 2.x: STOP, the plan goes back to Rob.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
