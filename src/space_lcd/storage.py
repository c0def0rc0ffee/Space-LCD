"""
<summary>
Talks to the screen's own storage: file size, upload, play, stop and delete, as proven on
this 2.1 inch screen (ROM 1.88) in stages 2 and 3 of docs/device-video-plan.md.
</summary>
<remarks>
Everything here is fenced in, because a mistake in this area is the one way this program
could leave the screen needing the case opened:

- files only go into VIDEO_FOLDER and only with names matching SAFE_NAME, so nothing of
  the stock firmware, its videos or a firmware update file can ever be touched;
- uploads are capped at MAX_UPLOAD, far below the 28 MB that hung the 8.8 inch;
- an upload is one write of the whole data phase, never stopped part way and never padded
  out to finish one that was;
- the commands 0x84 (restart), 0x82 (unknown) and 0x81 are not in this module at all.

Works over any object with write(), read(), flush(), reset_input_buffer() and a timeout
attribute: a pyserial port in the service and the probe tools, a fake in the tests.
</remarks>
"""

import re
import struct
import time
from typing import Optional

VIDEO_FOLDER = "/mnt/SDCARD/video/"
"""<summary>The only folder files are written to: the card storage's video folder.</summary>"""

SAFE_NAME = re.compile(r"^(?!.*update)spacelcd_[a-z0-9_]{1,40}\.mp4$")
"""
<summary>The only file names written or deleted: ours, lowercase, and never one containing "update".</summary>
<remarks>The vendor's firmware update is a file the screen looks for by name; the lookahead keeps that word out of anything this module can write.</remarks>
"""

MAX_UPLOAD = 2 * 1024 * 1024
"""<summary>Largest upload allowed. The screen's real limit is unknown; the 8.8 inch hung at about 28 MB.</summary>"""


class StorageError(RuntimeError):
    """<summary>The screen did not answer a storage command as expected. Nothing more was sent after it.</summary>"""


def packet(op: int, path: str = "", tail: bytes = b"", flag: int = 0) -> bytes:
    """
    <summary>One 250 byte command: op ef 69, BE32 path length (1 with no path), flag, 00 00, path, tail, zero padding.</summary>
    """
    encoded = path.encode("ascii")
    body = bytes((op, 0xEF, 0x69)) + struct.pack(">I", len(encoded) if encoded else 1) + bytes((flag, 0, 0))
    body += encoded + tail
    if len(body) > 250:
        raise ValueError("command too long")
    return body + bytes(250 - len(body))


def data_phase(content: bytes) -> bytes:
    """<summary>The file cut into 249 byte pieces, each followed by one 00; the last block zero padded to 250.</summary>"""
    out = bytearray()
    for start in range(0, len(content), 249):
        piece = content[start:start + 249]
        out += piece + b"\x00" if len(piece) == 249 else piece + bytes(250 - len(piece))
    return bytes(out)


def checked_path(name: str) -> str:
    """
    <summary>The full screen path for one of our files, refusing any name outside the fence.</summary>
    <exception cref="ValueError">The name does not match SAFE_NAME.</exception>
    """
    if not SAFE_NAME.match(name):
        raise ValueError("refusing file name {!r}".format(name))
    return VIDEO_FOLDER + name


STOP_VIDEO = packet(0x79)
STOP_MEDIA = packet(0x96)


class ScreenStorage:
    """
    <summary>Storage and playback commands over an open link to the screen.</summary>
    <remarks>
    Each request clears pending input first, so a late reply to an earlier command is
    never mistaken for the answer to this one. Replies are matched by substring, as the
    firmware sends them without terminators.
    </remarks>
    """

    def __init__(self, link) -> None:
        """<summary>link is an open serial port, or a fake with the same methods.</summary>"""
        self._link = link

    def _request(self, data: bytes, size: int, wait: float) -> bytes:
        """<summary>Sends one packet or data phase and reads up to size bytes within wait seconds.</summary>"""
        self._link.reset_input_buffer()
        self._link.write(data)
        self._link.flush()
        previous = self._link.timeout
        self._link.timeout = wait
        try:
            return bytes(self._link.read(size)) if size else b""
        finally:
            self._link.timeout = previous

    def _request_until(self, data: bytes, token: bytes, wait: float) -> bytes:
        """
        <summary>Sends data, then reads until token arrives or wait seconds pass, whichever is first.</summary>
        <remarks>
        For replies shorter than a fixed read size, such as the 23 byte
        "file_rev_done.img_show_" after an upload: a plain read(1024) would sit out its
        whole timeout (30 s per upload, measured 10/10/2026).
        </remarks>
        """
        self._link.reset_input_buffer()
        self._link.write(data)
        self._link.flush()
        previous = self._link.timeout
        self._link.timeout = 0.05
        received = bytearray()
        deadline = time.monotonic() + wait
        try:
            while token not in received and time.monotonic() < deadline:
                received += self._link.read(1024)
        finally:
            self._link.timeout = previous
        return bytes(received)

    def file_size(self, name: str) -> int:
        """
        <summary>The size of one of our files on the screen; 0 means it is not there.</summary>
        <exception cref="StorageError">The reply is not a number.</exception>
        """
        reply = self._request(packet(0x6E, checked_path(name)), 1024, 2.0).strip(b"\x00 ")
        if not reply.isdigit():
            raise StorageError("unexpected file size reply {!r}".format(reply[:40]))
        return int(reply)

    def stop_media(self) -> bool:
        """<summary>STOP_VIDEO, 200 ms, then STOP_MEDIA polled up to five times for media_stop.</summary>"""
        self._request(STOP_VIDEO, 0, 0.2)
        time.sleep(0.2)
        for _ in range(5):
            if b"media_stop" in self._request(STOP_MEDIA, 1024, 1.0):
                return True
            time.sleep(0.4)
        return False

    def upload(self, name: str, content: bytes) -> None:
        """
        <summary>Uploads one of our files, refusing to overwrite, and checks its size afterwards.</summary>
        <exception cref="StorageError">A file is already there, the screen refused the header,
        or the size afterwards does not match. Nothing more is sent after the failure.</exception>
        <exception cref="ValueError">The name is outside the fence or the file is too big.</exception>
        <remarks>
        Call stop_media() first, as the vendor does. The data phase is a single write; a
        stop signal arriving meanwhile is only acted on once it has gone.
        </remarks>
        """
        path = checked_path(name)
        if not 0 < len(content) <= MAX_UPLOAD:
            raise ValueError("upload of {} bytes refused".format(len(content)))
        if self.file_size(name) != 0:
            raise StorageError("{} already exists".format(name))
        if b"create_success" not in self._request(packet(0x6F, path, struct.pack("<I", len(content))), 1024, 3.0):
            raise StorageError("no create_success for {}".format(name))
        self._request_until(data_phase(content), b"file_rev_done", 30.0)
        size = self.file_size(name)
        if size != len(content):
            raise StorageError("{} is {} bytes on the screen, expected {}".format(name, size, len(content)))

    def play(self, name: str, loop: bool = True) -> bool:
        """<summary>Plays one of our videos; True when the screen answers play_video_success.</summary>"""
        reply = self._request(packet(0x78, checked_path(name), flag=1 if loop else 0), 1024, 6.0)
        return b"play_video_success" in reply

    def delete(self, name: str) -> bool:
        """<summary>Deletes one of our files and checks it is gone. The firmware sends no reply to the delete itself.</summary>"""
        self._request(packet(0x66, checked_path(name)), 1024, 0.5)
        return self.file_size(name) == 0


def hello(link) -> Optional[str]:
    """<summary>Sends HELLO and returns the screen's ID text, or None without a valid answer.</summary>"""
    link.reset_input_buffer()
    link.write(bytes.fromhex("01 ef 69 00 00 00 01 00 00 00 c5 d3") + bytes(238))
    link.flush()
    reply = bytes(link.read(23))
    text = "".join(chr(b) for b in reply if 32 <= b < 127)
    return text if text.startswith("chs_") else None
