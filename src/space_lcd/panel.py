"""
<summary>
Thin wrapper around the upstream revision C driver: opens the screen, sets it up, and sends
frames, sending only the changed rectangle after the first one.
</summary>
<remarks>
This is the only module that talks to the hardware. It deliberately uses a small subset of
the upstream driver: wake (opening the sleeping serial port), HELLO, stop video and media,
brightness, options (no flip, no sleep timer), and bitmap updates. It never sends RESTART
or TURNOFF, both of which upstream's own program sends at start and stop.

The upstream library is imported lazily from the checkout install.sh made, so nothing here
is needed by the tests or the preview. Importing it creates a log.log file in the working
directory and changes the process locale; the import is done from the state directory so
the log lands there and not in the project tree.
</remarks>
"""

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from PIL import Image

from .frames import changed_box, changed_tiles
from .render import SIZE
from .timing import FrameTimer

log = logging.getLogger("space_lcd")

STATE_DIR = Path.home() / ".local" / "state" / "space-lcd"
"""<summary>Working directory for the upstream library's log file.</summary>"""

RESEND = re.compile(rb"needReSend:(\d)")
"""
<summary>
The flag in the screen's QUERY_STATUS reply (needReSend:0|renderCnt:n|theme:s) that asks
for a full frame. Upstream reads the reply and throws it away; the vendor app sends a full
frame on its next tick when the flag is 1, and so does this program from 1.0.11.
</summary>
"""

TILE_WORTH_IT = 0.1
"""<summary>Changes smaller than this share of the screen go as one rectangle without trying tiles.</summary>"""

FULL_FRAME_SHARE = 0.75
"""
<summary>
When more than this share of the screen changed, as on a theme switch, a full frame is sent
instead of partial updates. Partials cost about the same per pixel as a full frame
(JOURNAL.md), so they win until the change is most of the screen. Raised from 0.5 on
10/10/2026 for the full screen fan, after partials of up to 125,000 pixels were accepted.
</summary>
"""


class Panel:
    """
    <summary>One open connection to the cooler screen.</summary>
    <remarks>
    Call open() first, then show() once per frame, then close(). After an error, close it
    and make a new Panel: the screen can reappear under a different serial port name.
    </remarks>
    """

    def __init__(self, upstream_dir: Path, com_port: str, brightness: int) -> None:
        """
        <summary>Records the settings. Nothing is sent until open().</summary>
        <param name="upstream_dir">The turing-smart-screen-python checkout.</param>
        <param name="com_port">"AUTO" to find the screen by its USB IDs, or a path such as
        /dev/ttyACM1.</param>
        <param name="brightness">Backlight level, 0 to 100.</param>
        """
        self._upstream_dir = upstream_dir
        self._com_port = com_port
        self._brightness = brightness
        self._lcd = None
        self._previous: Optional[Image.Image] = None
        self.timer = FrameTimer()
        self._replies: list = []
        self._shown_reply = False
        self._count = None

    def _import_driver(self):
        """
        <summary>Imports the upstream revision C driver from the checkout.</summary>
        <returns>The LcdCommRevC class and the Orientation enum.</returns>
        <exception cref="FileNotFoundError">The checkout is missing: run install.sh.</exception>
        """
        if not (self._upstream_dir / "library" / "lcd" / "lcd_comm_rev_c.py").is_file():
            raise FileNotFoundError(
                "upstream driver not found at {}: run install.sh".format(self._upstream_dir))
        if str(self._upstream_dir) not in sys.path:
            sys.path.insert(0, str(self._upstream_dir))
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        previous_dir = os.getcwd()
        os.chdir(STATE_DIR)
        try:
            from library.lcd.lcd_comm import Orientation
            from library.lcd.lcd_comm_rev_c import Count, LcdCommRevC
        finally:
            os.chdir(previous_dir)
        self._count = Count
        # Upstream logs every command at DEBUG to the console, which floods the journal.
        logging.getLogger("turing").setLevel(logging.INFO)
        return LcdCommRevC, Orientation

    def open(self) -> None:
        """
        <summary>Wakes the screen if needed and prepares it for frames.</summary>
        <remarks>
        Brightness is the last command sent, after OPTIONS, so nothing overrides it.
        Waking can take up to 15 seconds: the sleeping screen shows up as 1a86:ca21 and
        comes back as 1d6b:0121 on a different ttyACM node. If no screen is found the
        upstream driver exits the process with status 0, which is why the service uses
        Restart=always rather than on-failure.
        </remarks>
        """
        driver, orientation = self._import_driver()
        self._lcd = driver(com_port=self._com_port, display_width=SIZE, display_height=SIZE)
        self._capture_replies()
        self._lcd.InitializeComm()
        self._lcd.ScreenOn()
        # OPTIONS before brightness, the reverse of upstream's order: OPTIONS carries its own
        # brightness byte, fixed at 0x2d (45 of 255) upstream, so sent second it can leave the
        # screen dim whatever brightness was set. See docs/recovery.md.
        self._lcd.SetOrientation(orientation.PORTRAIT)
        self._lcd.SetBrightness(self._brightness)
        self._previous = None
        log.info("screen open, brightness %d", self._brightness)

    def _capture_replies(self) -> None:
        """
        <summary>Keeps the replies upstream reads and discards, so QUERY_STATUS can be acted on.</summary>
        <remarks>
        Upstream's _send_command calls self.ReadData() and drops the result. Shadowing
        ReadData on this one driver object keeps a copy; nothing extra is read or sent.
        </remarks>
        """
        original = self._lcd.ReadData

        def keep(size):
            """<summary>Reads as upstream would, and remembers the bytes.</summary>"""
            data = original(size)
            if data:
                self._replies.append(bytes(data))
            return data

        self._lcd.ReadData = keep

    def _resend_requested(self) -> bool:
        """<summary>Whether any reply since the last send asked for a full frame. Clears the kept replies.</summary>"""
        if not self._shown_reply:
            for reply in self._replies:
                if b"needReSend" in reply:
                    text = reply.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
                    log.info("screen status reply: %s", text[:120])
                    self._shown_reply = True
                    break
        asked = any(m.group(1) == b"1" for reply in self._replies for m in RESEND.finditer(reply))
        self._replies.clear()
        return asked

    def _restart_sequence(self) -> None:
        """
        <summary>Sets the partial update sequence number back to 0, as the vendor app does after every full frame.</summary>
        <remarks>
        The number is bytes 10 to 13 of each UPDATE_BITMAP header. Upstream keeps it in the
        class attribute Count.Start, counts it up from 0 once per process and never resets
        it, so after the first full frame the screen saw numbers it did not expect and asked
        for a full frame almost every time (114 of 120 updates in a minute on 10/10/2026).
        </remarks>
        """
        if self._count is not None:
            self._count.Start = 0

    def _pieces(self, box, frame: Image.Image):
        """
        <summary>Decides how to send a change: as one rectangle, as several tiles, or (None) as a full frame.</summary>
        <param name="box">The bounding box of everything that changed.</param>
        <returns>A list of rectangles to send as partial updates, or None for a full frame.</returns>
        <remarks>
        A full frame is used with no previous frame, or when even the tiles add up to more
        than FULL_FRAME_SHARE of the screen (a theme switch or a pixel shift step). Tiles
        are used when they cover clearly less than the single box would, and also when the
        box alone is over the share but the tiles are not: tiles are never worse than a
        full frame, which they replaced.
        </remarks>
        """
        left, top, right, bottom = box
        area = (right - left) * (bottom - top)
        if self._previous is None:
            return None
        if area <= TILE_WORTH_IT * SIZE * SIZE:
            return [box]
        tiles = changed_tiles(self._previous, frame)
        tiled = sum((r - l) * (b - t) for l, t, r, b in tiles)
        if tiled > FULL_FRAME_SHARE * SIZE * SIZE:
            return None
        if tiled < 0.8 * area or area > FULL_FRAME_SHARE * SIZE * SIZE:
            return tiles
        return [box]

    def show(self, frame: Image.Image) -> None:
        """
        <summary>Puts a frame on the screen, sending only what changed since the last one.</summary>
        <param name="frame">An RGB image SIZE by SIZE, as draw_frame() returns.</param>
        <exception cref="RuntimeError">open() has not been called.</exception>
        """
        if self._lcd is None:
            raise RuntimeError("Panel.show() called before open()")
        box = changed_box(self._previous, frame)
        if box is None:
            return
        pieces = self._pieces(box, frame)
        if pieces is None:
            started = time.monotonic()
            self._lcd.DisplayPILImage(frame)
            self._restart_sequence()
            self.timer.record("full", SIZE * SIZE, time.monotonic() - started)
        else:
            for left, top, right, bottom in pieces:
                started = time.monotonic()
                self._lcd.DisplayPILImage(frame.crop((left, top, right, bottom)), x=left, y=top)
                self.timer.record("partial", (right - left) * (bottom - top), time.monotonic() - started)
        self._previous = frame
        if self._resend_requested():
            self._previous = None  # the next frame goes out whole, as the vendor app does
            self.timer.record_resend()
        line = self.timer.due()
        if line:
            log.info("%s", line)

    @property
    def brightness(self) -> int:
        """<summary>The level last sent to the screen, or the one open() will send.</summary>"""
        return self._brightness

    def storage(self):
        """
        <summary>Storage and playback commands over this panel's open serial port.</summary>
        <exception cref="RuntimeError">open() has not been called.</exception>
        <remarks>Shares the port with the frame sending; the service is single threaded, so they never overlap.</remarks>
        """
        from .storage import ScreenStorage
        if self._lcd is None:
            raise RuntimeError("Panel.storage() called before open()")
        return ScreenStorage(self._lcd.lcd_serial)

    def force_full_frame(self) -> None:
        """<summary>Makes the next show() send a full frame, after something other than frames was on screen.</summary>"""
        self._previous = None

    def set_brightness(self, level: int) -> None:
        """
        <summary>Changes the backlight while running. Sends SET_BRIGHTNESS only.</summary>
        <param name="level">0 to 100.</param>
        <exception cref="RuntimeError">open() has not been called.</exception>
        """
        if self._lcd is None:
            raise RuntimeError("Panel.set_brightness() called before open()")
        self._lcd.SetBrightness(level)
        self._brightness = level
        log.info("brightness %d", level)

    def close(self) -> None:
        """
        <summary>Closes the serial port. Leaves the screen on, showing the last frame.</summary>
        <remarks>Safe to call more than once, and after a failed open().</remarks>
        """
        if self._lcd is not None:
            try:
                self._lcd.closeSerial()
            except Exception:  # the port may already be gone after an unplug
                pass
        self._lcd = None
        self._previous = None
