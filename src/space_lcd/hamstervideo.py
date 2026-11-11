"""
<summary>
The hamster loops the screen plays from its own storage: three speeds by CPU temperature,
each one seamless 3 second loop at 24 frames a second, drawn with the hamster theme.
</summary>
<remarks>
Mirrors fanvideo.py. Each loop turns the wheel a whole number of rung spacings and the
legs a whole number of strides, so the end meets the start: 120, 360 and 720 degrees per
loop (2, 6 and 12 strides). The fastest is 10 degrees a frame, under the 15 degree point
where twelve rungs would seem to turn backwards. Loop names carry palette_tag() of the
hamster colours, so new colours get a new set; old sets stay on the screen's storage.
</remarks>
"""

import hashlib
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Optional, Tuple

from . import config

FPS = 24
SECONDS = 3

SPEEDS = (("slow", 120.0), ("medium", 360.0), ("fast", 720.0))
"""<summary>Speed names and wheel degrees per 3 second loop.</summary>"""

TEMP_BANDS = (55.0, 70.0)
"""<summary>CPU temperatures where the hamster steps up a speed.</summary>"""

HYSTERESIS = 3.0
SWITCH_SECONDS = 10.0
LOOP_VERSION = 3
"""<summary>3 from 1.0.29: frames turned by fanvideo.SCREEN_TURN, so the hamster plays upright.</summary>"""

HAMSTER_COLOURS = ("background", "wheel", "hamster")
"""<summary>The palette colours drawn in a hamster loop.</summary>"""

CACHE = config.APP_HOME / "hamster-loops"


def palette_tag(palette: Mapping[str, Tuple[int, int, int]]) -> str:
    """
    <summary>Six hex characters naming the hamster colours in a palette.</summary>
    <remarks>A rotation other than 0 counts too, as in fanvideo.palette_tag().</remarks>
    """
    from . import render
    defaults = {key: colour for key, _, colour in render.PALETTE}
    used = ",".join("{}={}".format(key, tuple(palette.get(key, defaults[key]))) for key in HAMSTER_COLOURS)
    if palette.get("rotation", 0):
        used += ",rotation={}".format(palette["rotation"])
    return hashlib.sha1(used.encode("ascii")).hexdigest()[:6]


def loop_name(speed: int, tag: str) -> str:
    """<summary>The file name of one loop, e.g. spacelcd_ham1_a1b2c3_fast.mp4.</summary>"""
    return "spacelcd_ham{}_{}_{}.mp4".format(LOOP_VERSION, tag, SPEEDS[speed][0])


def choose(temp: Optional[float], current: Optional[int]) -> int:
    """
    <summary>The speed for this temperature, holding the current one within HYSTERESIS of a band edge.</summary>
    """
    if temp is None:
        return current if current is not None else 0
    plain = 0 if temp < TEMP_BANDS[0] else (1 if temp < TEMP_BANDS[1] else 2)
    if current is None or plain == current:
        return plain
    if plain < current and temp > TEMP_BANDS[current - 1] - HYSTERESIS:
        return current
    if plain > current and temp < TEMP_BANDS[plain - 1] + HYSTERESIS:
        return current
    return plain


def make_loop(speed: int, out: Path, palette: Mapping[str, Tuple[int, int, int]] = {}) -> Path:
    """
    <summary>Draws one loop with the hamster theme and encodes it with ffmpeg.</summary>
    <remarks>Sets each frame's wheel angle exactly by swapping the theme's step function, then restores it.</remarks>
    """
    from . import fanvideo, render, themes
    from .sensors import Stats

    frames = FPS * SECONDS
    step = SPEEDS[speed][1] / frames
    original = themes.hamster_step
    render.apply_palette(palette)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    try:
        with tempfile.TemporaryDirectory() as folder:
            for i in range(frames):
                angle = step * i
                themes.hamster_step = lambda temp, now, a=angle: (a % 360, math.radians(a * themes.STRIDES_PER_TURN))
                fanvideo.for_screen(render.draw_frame(Stats(cpu_temp=50), rotation=int(palette.get("rotation", 0)),
                                                      theme="hamster")).save(Path(folder) / "f{:03d}.png".format(i))
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS),
                            "-i", str(Path(folder) / "f%03d.png"), "-c:v", "libx264", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", "-f", "mp4", str(part)],
                           check=True)
        # ffmpeg writes straight into its output, so a killed encode would leave a short
        # file under the permanent name, to be uploaded and never replaced. The rename
        # means the permanent name only ever holds a finished loop.
        part.replace(out)
    finally:
        themes.hamster_step = original
    return out


def cached_loop(speed: int, palette: Mapping[str, Tuple[int, int, int]] = {}) -> Path:
    """<summary>The loop file on this PC in the palette's hamster colours, made first if needed.</summary>"""
    path = CACHE / loop_name(speed, palette_tag(palette))
    if not path.is_file() or path.stat().st_size == 0:
        make_loop(speed, path, palette)
    return path


def make_all() -> int:
    """<summary>Makes every hamster loop for the current palette that is not cached yet; used by install.sh.</summary>"""
    settings, problem = config.try_load()
    if problem:
        print("settings file not used for the {} loops, default colours drawn: {}".format("hamster", problem))
    palette = settings.look()
    made = 0
    for speed in range(len(SPEEDS)):
        if not (CACHE / loop_name(speed, palette_tag(palette))).is_file():
            cached_loop(speed, palette)
            made += 1
    return made


if __name__ == "__main__":
    import sys
    from . import render
    render.set_font_root(config.try_load()[0].upstream_dir)
    print("hamster loops made: {}".format(make_all()))
    sys.exit(0)
