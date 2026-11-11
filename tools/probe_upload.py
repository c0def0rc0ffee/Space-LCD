#!/usr/bin/env python3
"""
<summary>
Stage 2 of docs/device-video-plan.md: uploads one 67 byte, 1 by 1 pixel PNG to the
screen's card storage, checks it, and deletes it again.
</summary>
<remarks>
Without --send it only prints every packet it would send, and touches nothing.
With --send, in order, stopping at the first unexpected answer:
  1. HELLO                       expect "chs_"
  2. STOP_VIDEO, then STOP_MEDIA expect "media_stop" (the service sends these at every start)
  3. LIST_DIR /mnt/SDCARD/img/   prints what is there (creates the folder if missing)
  4. LIST_DIR /mnt/SDCARD/video/ prints what is there (creates the folder if missing)
  5. GET_FILE_SIZE t.png         expect "0", else stop: never overwrite anything
  6. UPLOAD_FILE header, 67 B    expect "create_success" within 3 s, else stop
  7. one data block              expect "file_rev_done" within 10 s
  8. GET_FILE_SIZE t.png         expect "67"
  9. DELETE_FILE t.png, then GET_FILE_SIZE, expect "0"
The file fits in one block, so the upload cannot be interrupted part way, and no
padding is ever sent to complete one. Never sends RESTART 0x84, 0x82 or 0x81.

Run only on Rob's yes, with the Space LCD service stopped (it refuses to run otherwise,
and opens the port exclusively; see probe_common.py) and the screen awake:

    systemctl --user stop space-lcd
    ~/.local/share/space-lcd/venv/bin/python tools/probe_upload.py --send
</remarks>
"""

import struct
import sys
import time

from probe_common import find_screen, open_screen

FOLDER = "/mnt/SDCARD/img/"
VIDEO_FOLDER = "/mnt/SDCARD/video/"
TARGET = FOLDER + "t.png"
"""<summary>The only paths this script names. The test file name contains nothing like "update".</summary>"""

PNG = bytes.fromhex(
    # One line per PNG part, each short enough that the house rules gate does
    # not read it as a key: signature, IHDR, its CRC, IDAT with CRC, IEND.
    "89504e470d0a1a0a"
    "0000000d4948445200000001000000010800000000"
    "3a7e9b55"
    "0000000a49444154789c63600000000200010d0a2db4"
    "0000000049454e44ae426082")
"""<summary>A 1 by 1 pixel greyscale PNG, 67 bytes. Checked to decode before anything is sent.</summary>"""


def packet(op: int, path: str = "", tail: bytes = b"") -> bytes:
    """
    <summary>One 250 byte command: op ef 69, BE32 path length, 00 00 00, path, tail, zero padding.</summary>
    <remarks>Commands without a path use length 1, as HELLO, STOP_VIDEO and STOP_MEDIA do.</remarks>
    """
    encoded = path.encode("ascii")
    body = bytes((op, 0xEF, 0x69)) + struct.pack(">I", len(encoded) if encoded else 1) + b"\x00\x00\x00"
    body += encoded + tail
    assert len(body) <= 250
    return body + bytes(250 - len(body))


HELLO = bytes.fromhex("01 ef 69 00 00 00 01 00 00 00 c5 d3") + bytes(238)
STOP_VIDEO = packet(0x79)
STOP_MEDIA = packet(0x96)
DATA_BLOCK = PNG + bytes(250 - len(PNG))
"""<summary>The fixed packets. The data phase is a single block: 67 bytes of file, then zeros.</summary>"""


def show(label: str, data: bytes) -> None:
    """<summary>Prints bytes as hex (trailing zeros trimmed) and as printable text.</summary>"""
    trimmed = data.rstrip(b"\x00")
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in trimmed)
    print("  {:<5} {:>4} B: {}".format(label, len(data), trimmed.hex(" ")[:150]))
    if text:
        print("        text: {}".format(text[:200]))


def ask(link, name: str, data: bytes, size: int, wait: float = 1.0) -> bytes:
    """<summary>Sends one packet, reads up to size bytes within wait seconds, prints both.</summary>"""
    print(name)
    link.reset_input_buffer()
    link.write(data)
    show("sent", data)
    link.timeout = wait
    reply = link.read(size)
    show("reply", reply)
    return reply


def stop(reason: str) -> int:
    """<summary>Prints why the run stopped early. Nothing further is sent.</summary>"""
    print("\nSTOPPED: {}. Nothing more was sent.".format(reason))
    return 1


def run(link) -> int:
    """<summary>The nine steps, stopping at the first unexpected answer.</summary>"""
    if b"chs_" not in ask(link, "1 HELLO", HELLO, 23):
        return stop("no HELLO answer")
    ask(link, "2a STOP_VIDEO", STOP_VIDEO, 0, 0.2)
    time.sleep(0.2)  # the vendor waits 200 ms after STOP_VIDEO; read(0) returns at once
    if b"media_stop" not in ask(link, "2b STOP_MEDIA", STOP_MEDIA, 1024):
        return stop("no media_stop")
    ask(link, "3 LIST_DIR " + FOLDER, packet(0x65, FOLDER), 10240)
    ask(link, "4 LIST_DIR " + VIDEO_FOLDER, packet(0x65, VIDEO_FOLDER), 10240)
    if ask(link, "5 GET_FILE_SIZE (expect 0)", packet(0x6E, TARGET), 1024, 2.0).strip(b"\x00 ") != b"0":
        return stop("a file already exists at " + TARGET)
    header = packet(0x6F, TARGET, struct.pack("<I", len(PNG)))
    if b"create_success" not in ask(link, "6 UPLOAD_FILE header", header, 1024, 3.0):
        return stop("no create_success; the data block was not sent")
    if b"file_rev_done" not in ask(link, "7 data block", DATA_BLOCK, 1024, 10.0):
        print("  (no file_rev_done; checking the size anyway)")
    size = ask(link, "8 GET_FILE_SIZE (expect 67)", packet(0x6E, TARGET), 1024, 2.0).strip(b"\x00 ")
    ask(link, "9a DELETE_FILE", packet(0x66, TARGET), 1024, 2.0)
    after = ask(link, "9b GET_FILE_SIZE (expect 0)", packet(0x6E, TARGET), 1024, 2.0).strip(b"\x00 ")
    print("\nRESULT: uploaded size {}, after delete {}".format(size.decode("ascii", "replace"),
                                                             after.decode("ascii", "replace")))
    return 0 if size == b"67" and after == b"0" else 1


def main(argv) -> int:
    """<summary>Checks the test file, then prints the packets (default) or runs the steps (--send).</summary>"""
    from io import BytesIO
    from PIL import Image
    assert len(PNG) == 67 and Image.open(BytesIO(PNG)).size == (1, 1), "test PNG is not what it should be"
    if "--send" not in argv:
        print("Dry run: these are the packets --send would use. Nothing is sent.\n")
        for name, data in (("HELLO", HELLO), ("STOP_VIDEO", STOP_VIDEO), ("STOP_MEDIA", STOP_MEDIA),
                           ("LIST_DIR img", packet(0x65, FOLDER)), ("LIST_DIR video", packet(0x65, VIDEO_FOLDER)),
                           ("GET_FILE_SIZE", packet(0x6E, TARGET)),
                           ("UPLOAD_FILE", packet(0x6F, TARGET, struct.pack("<I", len(PNG)))),
                           ("data block", DATA_BLOCK), ("DELETE_FILE", packet(0x66, TARGET))):
            print(name)
            show("send", data)
        return 0
    device = find_screen()
    if device is None:
        return 1
    print("Screen: {}\n".format(device))
    with open_screen(device) as link:
        return run(link)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
