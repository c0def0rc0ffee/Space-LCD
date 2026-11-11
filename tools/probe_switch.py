#!/usr/bin/env python3
"""
<summary>
Stage 4 measurement: how quickly the screen switches between two stored videos, and
whether it needs STOP in between. Uploads two test loops, switches, deletes them.
</summary>
<remarks>
Without --send it makes the two loops on this PC, prints what it would do, and sends
nothing. With --send, through space_lcd.storage (its fences apply: only spacelcd_ names
in /mnt/SDCARD/video/, 2 MB cap, no 0x84, 0x82 or 0x81):
  1 HELLO; STOP_VIDEO and STOP_MEDIA
  2 upload spacelcd_switchtest_a.mp4 (slow blue) and spacelcd_switchtest_b.mp4 (fast red),
    each refused if a file is already there, each size checked
  3 play B (red) 5 s; STOP, play A (blue) 5 s; STOP, play B (red) 5 s; each timed.
    (A first run on 10/10/2026 played B straight after A without a stop; the screen
    answered success but kept showing A.)
  4 STOP; delete both; check both read 0
Ctrl-C sends nothing further: a stop or delete landing in the middle of an upload's
data phase would be taken as file data. Whatever was uploaded is named, for deleting by
hand later with the storage module. Run --send only on Rob's yes, with the service
stopped (it refuses otherwise and opens the port exclusively; see probe_common.py) and
the screen awake.
</remarks>
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from space_lcd import fanvideo, render  # noqa: E402
from space_lcd.storage import ScreenStorage, StorageError, hello  # noqa: E402

TESTS = (("spacelcd_switchtest_a.mp4", 0, 0), ("spacelcd_switchtest_b.mp4", 2, 2))
"""<summary>Test file name, speed index and colour index: A slow blue, B fast red.</summary>"""


def timed(label: str, action) -> bool:
    """<summary>Runs one action, prints how long it took and what it returned.</summary>"""
    started = time.monotonic()
    result = action()
    print("  {:<38} {:>6.2f} s  -> {}".format(label, time.monotonic() - started, result))
    return result


def main(argv) -> int:
    """<summary>Makes the loops; with --send, runs the switching test on the screen.</summary>"""
    render.set_font_root(Path.home() / ".local/share/space-lcd/upstream")
    files = {}
    for name, speed, colour in TESTS:
        source = fanvideo.cached_loop(speed, colour)
        files[name] = source.read_bytes()
        print("{}: {} bytes (from {})".format(name, len(files[name]), source.name))
    if "--send" not in argv:
        print("\nDry run: nothing sent. --send would upload both, switch four times, then delete both.")
        return 0
    from probe_common import find_screen, open_screen
    device = find_screen()
    if device is None:
        return 1
    with open_screen(device) as link:
        screen = ScreenStorage(link)
        print("\nScreen {}: {}".format(device, hello(link)))
        uploaded = []
        try:
            if not timed("stop media", screen.stop_media):
                print("STOPPED: no media_stop")
                return 1
            for name in files:
                timed("upload " + name, lambda n=name: screen.upload(n, files[n]))
                uploaded.append(name)
            a, b = (name for name, _, _ in TESTS)
            print("\nWatch the screen: RED, then BLUE, then RED, a stop before each.", flush=True)
            print("  >>> now: RED", flush=True)
            timed("play B (fast red) first", lambda: screen.play(b))
            time.sleep(5)
            timed("stop", screen.stop_media)
            print("  >>> now: BLUE", flush=True)
            timed("play A (slow blue) after stop", lambda: screen.play(a))
            time.sleep(5)
            timed("stop", screen.stop_media)
            print("  >>> now: RED", flush=True)
            timed("play B (fast red) after stop", lambda: screen.play(b))
            time.sleep(5)
        except StorageError as error:
            print("STOPPED: {}".format(error))
        except KeyboardInterrupt:
            print("\nINTERRUPTED: nothing more sent. Left on the screen: {}".format(", ".join(uploaded) or "nothing"))
            return 130
        timed("stop", screen.stop_media)
        for name in uploaded:
            timed("delete " + name, lambda n=name: screen.delete(n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
