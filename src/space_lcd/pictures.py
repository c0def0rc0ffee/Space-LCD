"""
<summary>
Phase 4 of PLAN.md: which picture the folder slideshow and the playlist show at a given
moment, and how long until the next one.
</summary>
<remarks>
Like the screen cycle, both are worked out from the wall clock, so the service and the
control panel agree without talking to each other and a restart does not reset the order.
A picture that cannot be found or read is skipped by the drawing code, not here, so the
timing never shifts when one file goes missing.
</remarks>
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
"""<summary>File types the slideshow picks up from a folder. Animated files show their first frame.</summary>"""

PLAYLIST_SIZE = 5
"""<summary>How many pictures the playlist holds.</summary>"""

PICTURE_SECONDS = (1, 3600)
"""<summary>Shortest and longest time one picture may show for.</summary>"""

_folder_cache: dict = {}


def folder_pictures(folder: Optional[Path]) -> List[Path]:
    """
    <summary>The pictures in a folder, sorted by name, cached until the folder changes.</summary>
    <param name="folder">The folder, or None when none has been chosen.</param>
    <returns>The picture files; empty when the folder is missing, unreadable or has none.</returns>
    <remarks>Only the folder itself is read, not subfolders. Hidden files are left out.</remarks>
    """
    if folder is None:
        return []
    try:
        stamp = folder.stat().st_mtime
    except OSError:
        return []
    key = str(folder)
    cached = _folder_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        found = sorted((p for p in folder.iterdir()
                        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in PICTURE_SUFFIXES),
                       key=lambda p: p.name.lower())
    except OSError:
        return []
    _folder_cache[key] = (stamp, found)
    return found


def pick(entries: Sequence[Tuple[Path, int]], now: datetime) -> Tuple[Optional[Path], Optional[float]]:
    """
    <summary>The picture showing now from a list of (picture, seconds), and the seconds until the next.</summary>
    <returns>(None, None) for an empty list.</returns>
    <remarks>Entries play in order, each for its own seconds, round and round from the epoch.</remarks>
    """
    total = sum(seconds for _, seconds in entries)
    if not entries or total <= 0:
        return None, None
    position = now.timestamp() % total
    for path, seconds in entries:
        if position < seconds:
            return path, seconds - position
        position -= seconds
    return entries[-1][0], None


def slideshow_turn(folder: Optional[Path], seconds: int, now: datetime) -> Tuple[Optional[Path], Optional[float]]:
    """
    <summary>For the glitch slideshow: the picture shown before the current one, and the seconds the current one has shown.</summary>
    <returns>(None, None) with fewer than two pictures, since there is nothing to change from.</returns>
    <remarks>Worked out from the wall clock like slideshow(), so it agrees with it to the second.</remarks>
    """
    found = folder_pictures(folder)
    if len(found) < 2 or seconds <= 0:
        return None, None
    position = now.timestamp() % (seconds * len(found))
    index = int(position // seconds)
    return found[index - 1], position - index * seconds


def slideshow(folder: Optional[Path], seconds: int, now: datetime) -> Tuple[Optional[Path], Optional[float]]:
    """<summary>The slideshow picture showing now and the seconds until the next one.</summary>"""
    return pick([(path, seconds) for path in folder_pictures(folder)], now)
