"""
<summary>
The fan loops the screen plays from its own storage (stage 4 of docs/device-video-plan.md):
which loop suits the CPU right now, and making the loop files with ffmpeg.
</summary>
<remarks>
Nine loops: three speeds by CPU load times three colours by CPU temperature. Each is one
seamless 3 second loop at 24 frames a second, drawn with the same fan code as the streamed
fan theme. Each speed is a whole number of turns per loop, so the end meets the start.
Loops are cached on this PC under CACHE and only made again when LOOP_VERSION changes;
the screen copy is checked by size and only uploaded when missing.
</remarks>
"""

import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Optional, Tuple

from . import config

FPS = 24
SECONDS = 3
"""<summary>Loop length: 72 frames.</summary>"""

SPEEDS = (("slow", 1), ("medium", 2), ("fast", 4))
"""
<summary>
Speed names and turns per 3 second loop. Fast is 20 degrees a frame at 24 frames a
second, under the 25.7 degree point where seven blades start to look like they turn
backwards; it gets the same motion blur as the streamed fan.
</summary>
"""

COLOURS = (("cool", 40.0), ("mid", 62.0), ("hot", 85.0))
"""<summary>Colour step names and the CPU temperature each is drawn at, on the palette's fan_cold to fan_hot scale.</summary>"""

LOAD_BANDS = (25.0, 60.0)
TEMP_BANDS = (55.0, 70.0)
"""<summary>CPU load per cent and temperature where the loop steps up a speed or a colour.</summary>"""

HYSTERESIS = 3.0
"""<summary>How far past a band edge a reading must go before stepping back down, so it does not flicker.</summary>"""

SWITCH_SECONDS = 10.0
"""<summary>Fewest seconds between two loop changes.</summary>"""

SCREEN_TURN = 90
"""
<summary>
Degrees (Pillow's, anticlockwise) a frame must be turned before it goes into a stored video,
so the video plays the same way up as frames sent by the driver.
</summary>
<remarks>
With the driver set to PORTRAIT, as panel.open() does, upstream's revision C code turns
every frame it sends by rotate(90) before writing it (lcd_comm_rev_c.py,
_generate_full_image and _generate_update_image). A stored video is played straight onto
the panel without that turn, so stored loops played a quarter turn off: Rob saw the
glitch clips sideways and then the held picture the right way up, and the hamster on its
side never reached the bottom of the screen (07/11/2026). The fan loops are not turned:
the fan looks the same at any angle, and turning them would change every fan file and
force a fresh 2 MB upload for nothing seen.
</remarks>
"""


def for_screen(frame):
    """<summary>A frame turned by SCREEN_TURN, ready to be encoded into a stored video.</summary>"""
    return frame.rotate(SCREEN_TURN)


LOOP_VERSION = 2
"""<summary>Bump when the drawing of the loops changes, so cached and screen copies are replaced.</summary>"""

FAN_COLOURS = ("background", "track", "fan_cold", "fan_hot", "hub")
"""<summary>The palette colours drawn in a fan loop; a change to any of them means a new set of loops.</summary>"""


def palette_tag(palette: Mapping[str, Tuple[int, int, int]]) -> str:
    """
    <summary>Six hex characters naming the fan colours in a palette, e.g. "a1b2c3".</summary>
    <remarks>
    Only the colours in FAN_COLOURS count, so changing the text colour does not make new
    loops. A rotation other than 0 (config.Settings.look()) counts too, since it is drawn
    into the loop; at 0 the tag is what it was before the rotation counted.
    </remarks>
    """
    from . import render
    defaults = {key: colour for key, _, colour in render.PALETTE}
    used = ",".join("{}={}".format(key, tuple(palette.get(key, defaults[key]))) for key in FAN_COLOURS)
    if palette.get("rotation", 0):
        used += ",rotation={}".format(palette["rotation"])
    return hashlib.sha1(used.encode("ascii")).hexdigest()[:6]

CACHE = config.APP_HOME / "fan-loops"


def loop_name(speed: int, colour: int, tag: str) -> str:
    """
    <summary>The file name of one loop, on this PC and on the screen, e.g. spacelcd_fan2_a1b2c3_fast_hot.mp4.</summary>
    <param name="tag">palette_tag() of the colours the loop is drawn in.</param>
    """
    return "spacelcd_fan{}_{}_{}_{}.mp4".format(LOOP_VERSION, tag, SPEEDS[speed][0], COLOURS[colour][0])


def _band(value: Optional[float], edges: Tuple[float, float], current: Optional[int]) -> int:
    """
    <summary>Which of three bands a reading falls in, holding the current band within HYSTERESIS of its edges.</summary>
    """
    if value is None:
        return current if current is not None else 0
    plain = 0 if value < edges[0] else (1 if value < edges[1] else 2)
    if current is None or plain == current:
        return plain
    if plain < current and value > edges[current - 1] - HYSTERESIS:
        return current
    if plain > current and value < edges[plain - 1] + HYSTERESIS:
        return current
    return plain


def choose(load: Optional[float], temp: Optional[float], current: Optional[Tuple[int, int]]) -> Tuple[int, int]:
    """
    <summary>The (speed, colour) loop for these readings, given the loop playing now (or None).</summary>
    <remarks>Applies HYSTERESIS only; the SWITCH_SECONDS limit is the caller's, since it needs a clock.</remarks>
    """
    return (_band(load, LOAD_BANDS, current[0] if current else None),
            _band(temp, TEMP_BANDS, current[1] if current else None))


def make_loop(speed: int, colour: int, out: Path, palette: Mapping[str, Tuple[int, int, int]] = {}) -> Path:
    """
    <summary>Draws one loop with the fan renderer and encodes it with ffmpeg to out.</summary>
    <exception cref="subprocess.CalledProcessError">ffmpeg failed.</exception>
    <exception cref="FileNotFoundError">ffmpeg is not installed.</exception>
    <remarks>
    Sets each frame's angle exactly by swapping the theme's step function during the
    drawing only, then puts it back, so a running streamed fan is not disturbed.
    </remarks>
    """
    from . import render, themes
    from .sensors import Stats

    frames = FPS * SECONDS
    turns = SPEEDS[speed][1]
    step = 360.0 * turns / frames
    blur = max(0.0, step - themes.FAN_MAX_STEP * 0.5) if turns >= 4 else 0.0
    original = themes.fan_step
    render.apply_palette(palette)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    try:
        with tempfile.TemporaryDirectory() as folder:
            for i in range(frames):
                angle = (step * i) % 360
                themes.fan_step = lambda load, now, a=angle: (a, blur)
                frame = render.draw_frame(Stats(cpu_temp=COLOURS[colour][1], cpu_load=0),
                                          rotation=int(palette.get("rotation", 0)), theme="fan")
                frame.save(Path(folder) / "f{:03d}.png".format(i))
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS),
                            "-i", str(Path(folder) / "f%03d.png"), "-c:v", "libx264", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", "-f", "mp4", str(part)],
                           check=True)
        # ffmpeg writes straight into its output, so a killed encode would leave a short
        # file under the permanent name, to be uploaded and never replaced. The rename
        # means the permanent name only ever holds a finished loop.
        part.replace(out)
    finally:
        themes.fan_step = original
    return out


def cached_loop(speed: int, colour: int, palette: Mapping[str, Tuple[int, int, int]] = {}) -> Path:
    """<summary>The loop file on this PC in the palette's fan colours, made first if it is not in the cache yet.</summary>"""
    path = CACHE / loop_name(speed, colour, palette_tag(palette))
    if not path.is_file() or path.stat().st_size == 0:
        make_loop(speed, colour, path, palette)
    return path


def make_all() -> int:
    """
    <summary>Makes every loop for the current palette into the cache that is not there yet; used by install.sh so the service never waits for ffmpeg.</summary>
    <returns>How many loops were made.</returns>
    """
    settings, problem = config.try_load()
    if problem:
        print("settings file not used for the {} loops, default colours drawn: {}".format("fan", problem))
    palette = settings.look()
    made = 0
    for speed in range(len(SPEEDS)):
        for colour in range(len(COLOURS)):
            if not (CACHE / loop_name(speed, colour, palette_tag(palette))).is_file():
                cached_loop(speed, colour, palette)
                made += 1
    return made


if __name__ == "__main__":
    import sys
    from . import render
    render.set_font_root(config.try_load()[0].upstream_dir)
    print("fan loops made: {}".format(make_all()))
    sys.exit(0)
