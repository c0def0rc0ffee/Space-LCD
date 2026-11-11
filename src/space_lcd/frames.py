"""
<summary>
Works out which rectangle of the screen changed between two frames.
</summary>
<remarks>
Sending a whole 480 by 480 frame over the screen's serial link takes around a second, so
after the first frame only the changed rectangle is sent. Usually that is the temperature
digits and the ends of the rings.
</remarks>
"""

from typing import List, Optional, Tuple

from PIL import Image, ImageChops

Box = Tuple[int, int, int, int]


def changed_box(previous: Optional[Image.Image], current: Image.Image) -> Optional[Box]:
    """
    <summary>Finds the smallest rectangle that holds every pixel that differs.</summary>
    <param name="previous">The frame already on screen, or None if nothing has been sent.</param>
    <param name="current">The frame about to be sent. Must be RGB.</param>
    <returns>
    (left, top, right, bottom) with right and bottom exclusive, the whole frame when there
    is no previous frame or the sizes differ, or None when nothing changed.
    </returns>
    """
    whole = (0, 0, current.width, current.height)
    if previous is None or previous.size != current.size or previous.mode != current.mode:
        return whole
    return ImageChops.difference(previous, current).getbbox()


TILE = 96
"""<summary>Tile size for changed_tiles(): 480 is five tiles across and down.</summary>"""


def changed_tiles(previous: Image.Image, current: Image.Image, tile: int = TILE) -> List[Box]:
    """
    <summary>Splits what changed into one tight rectangle per tile, so far apart changes go as small pieces.</summary>
    <param name="previous">The frame on screen. Must be the same size and mode as current.</param>
    <param name="current">The frame about to be sent.</param>
    <param name="tile">Grid size in pixels.</param>
    <returns>Rectangles (left, top, right exclusive, bottom exclusive) in screen pixels, in reading order.</returns>
    <remarks>
    One bounding box round a spinning fan and a temperature ring on the far edge covers most
    of the screen and so goes out as a full frame, which takes 0.33 s. Cut into tiles, the
    same change is a few small partial updates. Each one costs about a millisecond of
    overhead (JOURNAL.md), so the extra pieces are cheap.
    </remarks>
    """
    difference = ImageChops.difference(previous, current)
    boxes: List[Box] = []
    for top in range(0, current.height, tile):
        for left in range(0, current.width, tile):
            right, bottom = min(left + tile, current.width), min(top + tile, current.height)
            inner = difference.crop((left, top, right, bottom)).getbbox()
            if inner:
                boxes.append((left + inner[0], top + inner[1], left + inner[2], top + inner[3]))
    return boxes
