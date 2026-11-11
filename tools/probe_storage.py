#!/usr/bin/env python3
"""
<summary>
Stage 1 of docs/device-video-plan.md: asks the screen three questions and writes nothing.
</summary>
<remarks>
Sends, in order and nothing else:
  1. HELLO               01 ef 69 00 00 00 01 00 00 00 c5 d3, padded to 250 bytes
  2. GET_STORAGE_INFO    64 ef 69 00 00 00 01, padded to 250 bytes
  3. GET_FILE_SIZE       6e ef 69 00 00 00 0f 00 00 00 + "/root/img/t.png", padded to 250 bytes
Every packet sent and every reply received is printed in hex and text.

Run it only with the Space LCD service stopped and the screen awake (it wakes when the
service starts and stays awake for a while after it stops):

    systemctl --user stop space-lcd
    ~/.local/share/space-lcd/venv/bin/python tools/probe_storage.py

It finds the awake screen (USB 1d6b:0121) itself and refuses to run if it cannot, or if
it finds more than one, or if the service is running (probe_common.py: the port is
opened exclusively so two writers can never share it). It does not wake a sleeping
screen. Run only on Rob's yes.
</remarks>
"""

import sys
import time

from probe_common import find_screen, open_screen

PACKETS = (
    ("HELLO", bytes.fromhex("01 ef 69 00 00 00 01 00 00 00 c5 d3"), 23),
    ("GET_STORAGE_INFO", bytes.fromhex("64 ef 69 00 00 00 01"), 1024),
    ("GET_FILE_SIZE /root/img/t.png", bytes.fromhex("6e ef 69 00 00 00 0f 00 00 00") + b"/root/img/t.png", 1024),
)
"""<summary>The three queries: name, packet before padding, and the most bytes to read back.</summary>"""


def show(label: str, data: bytes) -> None:
    """<summary>Prints bytes as hex and as printable text, trimming trailing zero padding.</summary>"""
    trimmed = data.rstrip(b"\x00")
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in trimmed)
    print("{:<5} {} bytes: {}".format(label, len(data), trimmed.hex(" ")))
    print("      text: {}".format(text))


def main() -> int:
    """<summary>Finds the awake screen, sends the three queries, prints the replies.</summary>"""
    device = find_screen()
    if device is None:
        return 1
    print("Screen: {}".format(device))
    with open_screen(device) as link:
        for name, packet, reply_size in PACKETS:
            padded = packet + bytes(250 - len(packet))
            print("\n{}".format(name))
            link.reset_input_buffer()
            link.write(padded)
            show("sent", padded)
            time.sleep(0.05)
            show("reply", link.read(reply_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
