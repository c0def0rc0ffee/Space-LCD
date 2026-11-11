#!/usr/bin/env python3
"""
<summary>
Kraken Phase 1: puts one test picture on the NZXT Kraken 2023's screen and times it, then
times a run of numbered frames to measure the frame rate. --liquid is the recovery step.
</summary>
<remarks>
Without --send or --liquid it draws the test picture, writes it to a PNG in the state
directory, prints every report it would send, and sends nothing. With --send, through
space_lcd.kraken (firmware 2.x path only, nothing written to the cooler's storage):
  1 30 01 screen info; reads brightness and orientation from the 31 01 reply
  2 30 02 01 40 00 00 01 orientation, only if brightness is not already 40
  3 the test picture twice (as liquidctl does after start): each is 36 01 00 01 06, a 20
    byte bulk header and 115,200 bytes of RGB565 on endpoint 0x02, then 36 02
  4 after 5 s, FRAMES numbered pictures, the same three steps each, timed
The last numbered picture stays on screen. --liquid sends 38 01 02 00 instead, which puts
back NZXT's own coolant temperature screen (liquidctl "set lcd screen liquid").
Run only on Rob's yes.
</remarks>
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from space_lcd import kraken, render  # noqa: E402
from space_lcd.sensors import Stats  # noqa: E402

FRAMES = 20
BRIGHTNESS = 40


def numbered(n: int) -> Image.Image:
    """<summary>A plain test frame: a ring and a large number, so each frame differs.</summary>"""
    image = Image.new("RGB", (kraken.SIZE, kraken.SIZE), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 233, 233), outline=render.COOL, width=10)
    draw.text((120, 120), str(n), font=render.font(40, bold=True), fill=render.TEXT, anchor="mm")
    return image


def test_picture() -> Image.Image:
    """<summary>The dial at 480, scaled to 240: what the Kraken would show before the Phase 2 layouts.</summary>"""
    stats = Stats(cpu_temp=45.0, cpu_load=12.0, gpu_temp=40.0, gpu_load=3.0, ram_used_gb=6.2, ram_total_gb=32.0)
    return render.draw_frame(stats, theme="dial").resize((kraken.SIZE, kraken.SIZE), Image.Resampling.LANCZOS)


def main() -> int:
    """<summary>Dry run by default; --send runs the test, --liquid the recovery.</summary>"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = sys.argv[1:]
    node, _ = kraken.find_hidraw()
    print("Kraken 2023 at {}".format(node))
    if "--liquid" in args:
        panel = kraken.KrakenPanel(BRIGHTNESS)
        try:
            print("reply:", (panel.liquid() or b"").hex(" "))
        finally:
            panel.close()
        return 0
    picture = test_picture()
    if "--send" not in args:
        out = Path.home() / ".local" / "state" / "space-lcd" / "kraken-test-preview.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        picture.save(out)
        print("Would send: 30 01; 30 02 01 {:02x} 00 00 01 <orientation> if brightness differs;".format(BRIGHTNESS))
        print("then the test picture twice and {} numbered frames, each 36 01 00 01 06, bulk header".format(FRAMES))
        print("  {} 06 00 00 00 00 c2 01 00, 115,200 bytes, 36 02".format(kraken.BULK_MAGIC.hex(" ")))
        print("Preview written to {}. Dry run: nothing sent.".format(out))
        return 0
    panel = kraken.KrakenPanel(BRIGHTNESS)
    try:
        panel.open()
        started = time.monotonic()
        panel.show(picture)
        print("test picture (sent twice): {:.3f} s".format(time.monotonic() - started))
        time.sleep(5)
        started = time.monotonic()
        for n in range(1, FRAMES + 1):
            panel.show(numbered(n))
        took = time.monotonic() - started
        print("{} frames in {:.3f} s: {:.3f} s a frame, {:.1f} frames a second".format(
            FRAMES, took, took / FRAMES, FRAMES / took))
    finally:
        panel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
