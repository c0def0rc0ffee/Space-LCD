"""
<summary>
Driver for the round 240 by 240 screen of the NZXT Kraken 2023 (USB 1e71:300e), firmware
2.x only: opens it, sends whole frames, sets brightness. Same shape as panel.Panel.
</summary>
<remarks>
The protocol is liquidctl's (liquidctl/driver/kraken3.py, KrakenZ3, commit 292dc56): HID
reports on the cooler's hidraw node for commands, and USB bulk endpoint 0x02 on interface 0
for picture data. Only the firmware 2.x picture path is used (_send_2023_data_fw2): nothing
is written to the cooler's storage buckets, and the 1.x bucket path is deliberately not
here (PLAN.md, agreed 30/10/2026). docs/recovery.md lists every report this sends.

The nzxt-kraken3 kernel driver keeps the HID interface and pushes status reports (75 xx)
to every hidraw reader; they are drained and skipped, never acted on. Interface 0 has no
kernel driver; if one is ever found there, open() refuses rather than detaching it.

The Kraken takes whole frames only, so show() skips unchanged frames but never sends a
rectangle. liquidctl sends the first picture twice after start, reason unknown ("some
sort of framebuffer swapping"); this does the same, once per open().
</remarks>
"""

import logging
import os
import select
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from .timing import FrameTimer

log = logging.getLogger("space_lcd")

VENDOR, PRODUCT = 0x1E71, 0x300E
SIZE = 240
"""<summary>Screen width and height in pixels.</summary>"""

REPORT = 64
"""<summary>HID report length. Every command is padded with zeros to it; the first byte is the report number.</summary>"""

BULK_ENDPOINT = 0x02
BULK_INTERFACE = 0
BULK_MAGIC = bytes([0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD, 0xEF, 0x98, 0x76, 0x54, 0x32, 0x10])
"""<summary>The fixed start of every bulk transfer header, as liquidctl sends it.</summary>"""

START_TRANSFER = bytes([0x36, 0x01, 0x00, 0x01, 0x06])
END_TRANSFER = bytes([0x36, 0x02])
LCD_INFO = bytes([0x30, 0x01])
LCD_INFO_REPLY = bytes([0x31, 0x01])
STATUS_PREFIX = 0x75
"""<summary>First byte of the status reports the kernel driver has the cooler send; skipped here.</summary>"""

READ_TIMEOUT_S = 1.0
MAX_READS = 12


def find_hidraw() -> Tuple[Path, str]:
    """
    <summary>The hidraw node and USB serial of the one Kraken 2023, from sysfs only.</summary>
    <exception cref="RuntimeError">None found, or more than one.</exception>
    """
    wanted = "HID_ID=0003:0000{:04X}:0000{:04X}".format(VENDOR, PRODUCT)
    found = []
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            uevent = (node / "device" / "uevent").read_text()
        except OSError:
            continue
        if wanted in uevent:
            serial = next((line.split("=", 1)[1] for line in uevent.splitlines() if line.startswith("HID_UNIQ=")), "")
            found.append((Path("/dev") / node.name, serial))
    if len(found) != 1:
        raise RuntimeError("expected exactly one Kraken 2023, found {}".format(len(found)))
    return found[0]


def usb_address() -> Tuple[int, int]:
    """
    <summary>The USB bus number and device address of the Kraken whose hidraw node find_hidraw() picks, from sysfs.</summary>
    <remarks>
    Matching on these rather than the serial number string means nothing is asked of the
    cooler to find it: asking for the string is a USB request of its own. The hidraw
    node's device is the HID interface; its USB device folder, which holds busnum and
    devnum, is the nearest parent that has them.
    </remarks>
    <exception cref="RuntimeError">sysfs does not show them.</exception>
    """
    node, _ = find_hidraw()
    folder = (Path("/sys/class/hidraw") / node.name / "device").resolve()
    for parent in folder.parents:
        if (parent / "busnum").is_file() and (parent / "devnum").is_file():
            return int((parent / "busnum").read_text()), int((parent / "devnum").read_text())
    raise RuntimeError("could not find the Kraken's USB bus and address in sysfs")


def present() -> bool:
    """<summary>Whether a Kraken 2023 is plugged in, from sysfs only. Sends nothing.</summary>"""
    try:
        find_hidraw()
    except RuntimeError:
        return False
    return True


def rgb565(frame: Image.Image) -> bytes:
    """
    <summary>A SIZE by SIZE frame as RGB565, two bytes a pixel, high byte first.</summary>
    <remarks>Bit for bit what liquidctl's _prepare_static_file_rgb16 produces, done with numpy.</remarks>
    """
    pixels = np.asarray(frame.convert("RGB"), dtype=np.uint16)
    value = ((pixels[..., 0] >> 3) << 11) | ((pixels[..., 1] >> 2) << 5) | (pixels[..., 2] >> 3)
    return value.astype(">u2").tobytes()


class KrakenPanel:
    """
    <summary>One open connection to the Kraken 2023 screen.</summary>
    <remarks>
    Call open() first, then show() once per frame, then close(). After an error, close it
    and make a new one. Rotation is done in the frame by the caller; the cooler's own
    orientation setting is read and sent back unchanged with brightness, and frames are
    turned to match it, as liquidctl does.
    </remarks>
    """

    def __init__(self, brightness: int) -> None:
        """<summary>Records the settings. Nothing is sent until open().</summary><param name="brightness">0 to 100.</param>"""
        self._brightness = brightness
        self._fd: Optional[int] = None
        self._usb = None
        self._orientation = 0
        self._previous: Optional[bytes] = None
        self._first = True
        self.timer = FrameTimer()

    def _write(self, data: bytes) -> None:
        """<summary>Sends one HID report, padded to REPORT bytes.</summary>"""
        os.write(self._fd, data + bytes(REPORT - len(data)))

    def _drain(self) -> None:
        """<summary>Throws away queued reports, as liquidctl's clear_enqueued_reports does.</summary>"""
        while select.select([self._fd], [], [], 0)[0]:
            os.read(self._fd, REPORT)

    def _read(self, prefix: Optional[bytes] = None) -> Optional[bytes]:
        """
        <summary>Reads the next reply that is not a status report, or with prefix the first that starts with it.</summary>
        <returns>The reply, or None after READ_TIMEOUT_S with nothing suitable.</returns>
        """
        for _ in range(MAX_READS):
            if not select.select([self._fd], [], [], READ_TIMEOUT_S)[0]:
                return None
            reply = os.read(self._fd, REPORT)
            if prefix is not None:
                if reply[:len(prefix)] == prefix:
                    return reply
            elif reply[:1] != bytes([STATUS_PREFIX]):
                return reply
        return None

    def _command(self, data: bytes) -> Optional[bytes]:
        """<summary>Drains, sends one report, and returns the reply (logged when missing).</summary>"""
        self._drain()
        self._write(data)
        reply = self._read()
        if reply is None:
            log.warning("kraken: no reply to %s", data.hex(" "))
        return reply

    def _open_bulk(self):
        """
        <summary>Finds the cooler's USB device by serial and claims interface 0 for picture data.</summary>
        <exception cref="RuntimeError">pyusb missing, no device, or a kernel driver on interface 0.</exception>
        <remarks>Never detaches a kernel driver and never sets a configuration: the HID interface stays the kernel's.</remarks>
        """
        try:
            import usb.core
            import usb.util
        except ImportError as error:
            raise RuntimeError("pyusb is not installed: run install.sh") from error
        bus, address = usb_address()
        devices = [d for d in usb.core.find(find_all=True, idVendor=VENDOR, idProduct=PRODUCT)
                   if (d.bus, d.address) == (bus, address)]
        if len(devices) != 1:
            raise RuntimeError("expected one Kraken 2023 USB device, found {}".format(len(devices)))
        device = devices[0]
        if device.is_kernel_driver_active(BULK_INTERFACE):
            raise RuntimeError("a kernel driver holds the Kraken's interface 0; refusing to detach it")
        usb.util.claim_interface(device, BULK_INTERFACE)
        return device

    def open(self) -> None:
        """
        <summary>Opens the HID node and the bulk interface, reads the screen settings, sets brightness.</summary>
        <remarks>
        Sends LCD_INFO (30 01) and reads brightness (byte 0x18) and orientation (byte 0x1a)
        from the 31 01 reply. Brightness is only sent when it differs, as 30 02 01 level 00 00
        01 orientation.
        </remarks>
        <exception cref="RuntimeError">The cooler is missing, held, or does not answer.</exception>
        """
        node, _ = find_hidraw()
        self._fd = os.open(node, os.O_RDWR)
        self._usb = self._open_bulk()
        self._drain()
        self._write(LCD_INFO)
        info = self._read(LCD_INFO_REPLY)
        if info is None:
            raise RuntimeError("the Kraken did not answer the screen info request")
        current, self._orientation = info[0x18], info[0x1A] % 4
        log.info("kraken screen open: brightness %d, orientation %d", current, self._orientation * 90)
        if current != self._brightness:
            self.set_brightness(self._brightness)
        self._previous = None
        self._first = True

    def _send(self, data: bytes) -> None:
        """<summary>One picture: start (36 01 00 01 06), header and pixels on bulk, end (36 02).</summary>"""
        self._command(START_TRANSFER)
        header = BULK_MAGIC + bytes([0x06, 0x00, 0x00, 0x00]) + len(data).to_bytes(4, "little")
        self._usb.write(BULK_ENDPOINT, header)
        self._usb.write(BULK_ENDPOINT, data)
        self._command(END_TRANSFER)

    def show(self, frame: Image.Image) -> None:
        """
        <summary>Puts a frame on the screen. Unchanged frames are not sent.</summary>
        <param name="frame">An RGB image SIZE by SIZE.</param>
        <exception cref="RuntimeError">open() has not been called.</exception>
        """
        if self._fd is None:
            raise RuntimeError("KrakenPanel.show() called before open()")
        if self._orientation:
            frame = frame.rotate(self._orientation * -90)
        data = rgb565(frame)
        if data == self._previous:
            return
        started = time.monotonic()
        self._send(data)
        if self._first:
            self._send(data)
            self._first = False
        self.timer.record("full", SIZE * SIZE, time.monotonic() - started)
        self._previous = data
        line = self.timer.due()
        if line:
            log.info("%s", line)

    @property
    def brightness(self) -> int:
        """<summary>The level last sent, or the one open() will send.</summary>"""
        return self._brightness

    def force_full_frame(self) -> None:
        """<summary>Makes the next show() send even an unchanged frame.</summary>"""
        self._previous = None

    def set_brightness(self, level: int) -> None:
        """
        <summary>Sends 30 02 01 level 00 00 01 orientation. The orientation is the one read at open().</summary>
        <param name="level">0 to 100.</param>
        """
        if self._fd is None:
            raise RuntimeError("KrakenPanel.set_brightness() called before open()")
        level = max(0, min(100, int(level)))
        self._command(bytes([0x30, 0x02, 0x01, level, 0x00, 0x00, 0x01, self._orientation]))
        self._brightness = level
        log.info("kraken brightness %d", level)

    def storage(self):
        """<summary>The Kraken has no stored video support here.</summary><exception cref="RuntimeError">Always.</exception>"""
        raise RuntimeError("the Kraken 2023 has no stored video support")

    def liquid(self) -> Optional[bytes]:
        """
        <summary>Puts back NZXT's own coolant temperature screen: 38 01 02 00 (liquidctl "liquid" mode). The recovery step.</summary>
        <returns>The cooler's reply, or None when it gave none.</returns>
        <remarks>Needs only the HID node, so it works when the bulk side will not open.</remarks>
        """
        if self._fd is None:
            node, _ = find_hidraw()
            self._fd = os.open(node, os.O_RDWR)
        return self._command(bytes([0x38, 0x01, 0x02, 0x00]))

    def close(self) -> None:
        """
        <summary>Releases the bulk interface and closes the HID node. Leaves the last frame on screen.</summary>
        <remarks>Safe to call more than once, and after a failed open().</remarks>
        """
        if self._usb is not None:
            try:
                import usb.util
                usb.util.release_interface(self._usb, BULK_INTERFACE)
                usb.util.dispose_resources(self._usb)
            except Exception:  # the device may already be gone after an unplug
                pass
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._usb = None
        self._fd = None
        self._previous = None
