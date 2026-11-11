"""
<summary>
The themes the screen can show, and the registry the daemon and the control panel read.
</summary>
<remarks>
Each theme is a function that draws onto the supersampled canvas from render.py, using
coordinates in final screen pixels through the render helpers. To add one, write the
function and add it to THEMES; the control panel picks it up from there, and the key is
what the settings file stores, so never rename a key once it has shipped.

Themes must keep anything that changes every frame small and in a fixed place, because the
screen is sent only the rectangle that changed. A theme with a ticking element in each
corner of the circle would make every update almost a full frame.
</remarks>
"""

import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from . import render as r
from .sensors import Stats

CENTRE = r.SIZE // 2

DrawFunction = Callable[[Image.Image, ImageDraw.ImageDraw, Stats, r.Context], None]


@dataclass(frozen=True)
class Theme:
    """
    <summary>One entry in the theme list: what the control panel shows and how to draw it.</summary>
    <remarks>
    interval is the seconds between frames this theme needs; None uses the update interval
    from the settings. listed False hides a theme from the control panel: those are drawn
    only on behalf of another screen, as the Smart picture draws "game" and "picture". Animated themes set a short one; JOURNAL.md has the figures that
    say how short is realistic.
    </remarks>
    """

    name: str
    description: str
    draw: DrawFunction
    interval: Optional[float] = None
    listed: bool = True
    small: Optional[DrawFunction] = None
    needs_cooler: bool = False
    """
    <summary>
    small is the layout for screens smaller than render.SIZE (the Kraken 2023's 240), drawn
    in the same 480 pixel coordinates with fewer, larger items; None means the normal
    layout simply scales down, which suits pictures and the animations. needs_cooler marks
    a screen only worth offering on a cooler that reports pump, fan and coolant readings.
    </summary>
    """


def _clock(stats: Stats, ctx: r.Context) -> str:
    """<summary>The headline if one was given, otherwise the time as HH:MM.</summary>"""
    return ctx.headline or stats.now.strftime("%H:%M")


def draw_dial(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    The original theme: CPU temperature on a thick outer ring, CPU load on a thin inner
    ring, big CPU figure in the middle, GPU and memory underneath.
    </summary>
    """
    r.ring(draw, 222, 24, 135, 270, r.fraction(stats.cpu_temp, r.TEMP_MIN, r.TEMP_MAX),
           r.temp_colour(stats.cpu_temp))
    r.ring(draw, 188, 9, 135, 270, r.fraction(stats.cpu_load, 0, 100), r.LOAD)

    r.text(draw, CENTRE, 104, _clock(stats, ctx), 26, r.MUTED)
    r.text(draw, CENTRE, 158, "CPU", 26, r.MUTED, bold=True)
    r.text(draw, CENTRE, 230, r.number(stats.cpu_temp, "°"), 104, r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, CENTRE, 300, "LOAD " + r.number(stats.cpu_load, "%"), 26, r.LOAD, bold=True)

    gpu = "GPU " + r.number(stats.gpu_temp, "°") + "   " + r.number(stats.gpu_load, "%")
    r.text(draw, CENTRE, 348, gpu, 24, r.TEXT)
    ram = "RAM " + r.number(stats.ram_used_gb, "", 1) + " / " + r.number(stats.ram_total_gb, " GB")
    r.text(draw, CENTRE, 384, ram, 20, r.MUTED)


TWIN_GAP = 8
"""
<summary>
Degrees left empty either side of 9 and 3 o'clock in the twin theme. Both halves fill from
the left, so without the gap the CPU and GPU arcs meet at 9 o'clock and read as one.
</summary>
"""


def draw_twin(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    Two half dials: CPU across the top, GPU across the bottom. Each has a temperature arc
    outside and a load arc inside, both filling from left to right.
    </summary>
    <remarks>
    The bottom arcs fill anticlockwise in Pillow's angles, so they are drawn back from
    their left end rather than through ring(). The GPU uses its own, higher,
    colour limits.
    </remarks>
    """
    cpu_temp = r.fraction(stats.cpu_temp, r.TEMP_MIN, r.TEMP_MAX)
    gpu_temp = r.fraction(stats.gpu_temp, r.TEMP_MIN, r.TEMP_MAX)

    sweep = 180 - 2 * TWIN_GAP
    top, bottom_end = 180 + TWIN_GAP, 180 - TWIN_GAP
    r.ring(draw, 222, 20, top, sweep, cpu_temp, r.temp_colour(stats.cpu_temp))
    r.ring(draw, 194, 8, top, sweep, r.fraction(stats.cpu_load, 0, 100), r.LOAD)

    r.arc(draw, 222, 20, TWIN_GAP, bottom_end, r.TRACK)
    r.arc(draw, 222, 20, bottom_end - sweep * gpu_temp, bottom_end,
          r.temp_colour(stats.gpu_temp, r.GPU_LIMITS))
    r.arc(draw, 194, 8, TWIN_GAP, bottom_end, r.TRACK)
    r.arc(draw, 194, 8, bottom_end - sweep * r.fraction(stats.gpu_load, 0, 100), bottom_end, r.GPU)

    draw.line(((70 * r.SCALE, CENTRE * r.SCALE), (410 * r.SCALE, CENTRE * r.SCALE)),
              fill=r.TRACK, width=2 * r.SCALE)

    r.text(draw, CENTRE, 92, "CPU", 22, r.MUTED, bold=True)
    r.text(draw, CENTRE, 158, r.number(stats.cpu_temp, "°"), 76, r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, CENTRE, 214, "LOAD " + r.number(stats.cpu_load, "%"), 20, r.LOAD, bold=True)

    r.text(draw, CENTRE, 266, "LOAD " + r.number(stats.gpu_load, "%"), 20, r.GPU, bold=True)
    r.text(draw, CENTRE, 322, r.number(stats.gpu_temp, "°"), 76,
           r.temp_colour(stats.gpu_temp, r.GPU_LIMITS), bold=True)
    r.text(draw, CENTRE, 388, ctx.headline or "GPU", 22, r.MUTED, bold=True)


def draw_clock(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    A clock face: 60 minute ticks round the edge, lit up to the current minute, a large
    time, the date, and CPU and GPU temperatures underneath.
    </summary>
    <remarks>The date is DD/MM/YYYY, as the house style asks for in UI text.</remarks>
    """
    for minute in range(60):
        angle = math.radians(-90 + minute * 6)
        hour_mark = minute % 5 == 0
        inner = 200 if hour_mark else 212
        lit = ctx.headline is None and minute <= stats.now.minute
        colour = r.COOL if lit else r.TRACK
        x1 = (CENTRE + inner * math.cos(angle)) * r.SCALE
        y1 = (CENTRE + inner * math.sin(angle)) * r.SCALE
        x2 = (CENTRE + 226 * math.cos(angle)) * r.SCALE
        y2 = (CENTRE + 226 * math.sin(angle)) * r.SCALE
        draw.line(((x1, y1), (x2, y2)), fill=colour, width=(5 if hour_mark else 3) * r.SCALE)

    if ctx.headline:
        r.text(draw, CENTRE, 215, ctx.headline, 48, r.MUTED)
    else:
        r.text(draw, CENTRE, 210, stats.now.strftime("%H:%M"), 112, r.TEXT, light=True)
    r.text(draw, CENTRE, 296, stats.now.strftime("%a %d/%m/%Y"), 24, r.MUTED)

    r.text(draw, 178, 356, "CPU " + r.number(stats.cpu_temp, "°"), 26,
           r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, 302, 356, "GPU " + r.number(stats.gpu_temp, "°"), 26,
           r.temp_colour(stats.gpu_temp, r.GPU_LIMITS), bold=True)


def draw_minimal(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    No rings: two large figures, CPU and GPU temperature, and one line of load figures.
    Meant to be read at a glance through the side panel.
    </summary>
    """
    r.text(draw, CENTRE, 84, "CPU", 22, r.MUTED, bold=True)
    r.text(draw, CENTRE, 158, r.number(stats.cpu_temp, "°"), 100, r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, CENTRE, 252, "GPU", 22, r.MUTED, bold=True)
    r.text(draw, CENTRE, 326, r.number(stats.gpu_temp, "°"), 100,
           r.temp_colour(stats.gpu_temp, r.GPU_LIMITS), bold=True)
    loads = ctx.headline or ("CPU " + r.number(stats.cpu_load, "%") + "     GPU " + r.number(stats.gpu_load, "%"))
    r.text(draw, CENTRE, 410, loads, 20, r.MUTED)


_background_cache: Dict[Tuple[str, float], Image.Image] = {}
"""<summary>Decoded pictures by (path, mtime), fitted to the canvas; see _remember().</summary>"""

_logo_cache: Dict[Tuple[str, float], Image.Image] = {}
"""<summary>Decoded Steam logos by (path, mtime), RGBA at their file size; see _remember().</summary>"""

CACHE_PICTURES = 24
"""
<summary>How many decoded pictures each cache keeps before the oldest goes.</summary>
<remarks>
The control panel redraws every thumbnail every two seconds, so it needs one entry per
distinct picture on show (single picture, slideshow, playlist, rules, game art) or it
decodes them all again each time. A canvas sized RGB picture is under 700 KB.
</remarks>
"""


def _remember(cache: Dict[Tuple[str, float], Image.Image], key: Tuple[str, float], picture: Image.Image) -> Image.Image:
    """<summary>Puts one decoded picture in a cache, dropping the oldest entries past CACHE_PICTURES.</summary>"""
    cache[key] = picture
    while len(cache) > CACHE_PICTURES:
        del cache[next(iter(cache))]
    return picture


def _load_background(path: Optional[Path]) -> Optional[Image.Image]:
    """
    <summary>Loads the chosen picture, cropped to fill the canvas, cached until the file changes.</summary>
    <param name="path">The picture file, or None when none has been chosen.</param>
    <returns>An RGB image the size of the canvas, or None when there is no usable picture.</returns>
    <remarks>
    Animated files use their first frame only. A file that cannot be read gives None rather
    than an error, so a moved or deleted picture never stops the display. The brightness
    and contrast boost (render.PICTURE_TUNE) is applied here, once per picture, and is part
    of the cache key, so changing it redraws.
    </remarks>
    """
    if path is None:
        return None
    try:
        key = (str(path), path.stat().st_mtime, r.PICTURE_TUNE)
    except OSError:
        return None
    if key not in _background_cache:
        try:
            with Image.open(path) as source:
                picture = ImageOps.exif_transpose(source).convert("RGB")
                _remember(_background_cache, key,
                          r.tune_picture(ImageOps.fit(picture, (r.CANVAS, r.CANVAS), Image.Resampling.LANCZOS)))
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
    return _background_cache[key]


def _load_logo(path: Optional[Path]) -> Optional[Image.Image]:
    """
    <summary>Loads a game's logo as RGBA, cached until the file changes; None when it cannot be read.</summary>
    <remarks>Kept at its own size: draw_game() scales it to the canvas, which is cheap next to decoding.</remarks>
    """
    if path is None:
        return None
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    if key not in _logo_cache:
        try:
            with Image.open(path) as source:
                _remember(_logo_cache, key, source.convert("RGBA"))
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
    return _logo_cache[key]


def draw_image(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    A picture of your choice filling the screen, with CPU and GPU temperatures in a dark
    panel near the bottom so they stay readable over any picture.
    </summary>
    <remarks>Without a picture it shows a plain dark background and says so.</remarks>
    """
    picture = _load_background(ctx.background)
    if picture is not None:
        canvas.paste(picture)
    else:
        r.text(draw, CENTRE, 200, "No picture chosen", 24, r.MUTED)
        r.text(draw, CENTRE, 236, "Pick one in the control panel", 18, r.MUTED)

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(
        (95 * r.SCALE, 330 * r.SCALE, 385 * r.SCALE, 420 * r.SCALE),
        radius=24 * r.SCALE, fill=(0, 0, 0, 170))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB"))
    draw = ImageDraw.Draw(canvas)

    if ctx.headline:
        r.text(draw, CENTRE, 375, ctx.headline, 30, r.TEXT)
        return
    r.text(draw, 175, 350, "CPU", 16, r.TEXT, bold=True)
    r.text(draw, 305, 350, "GPU", 16, r.TEXT, bold=True)
    r.text(draw, 175, 389, r.number(stats.cpu_temp, "°"), 40, r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, 305, 389, r.number(stats.gpu_temp, "°"), 40,
           r.temp_colour(stats.gpu_temp, r.GPU_LIMITS), bold=True)


FAN_CENTRE = (CENTRE, CENTRE)
FAN_RADIUS = 196
"""
<summary>
Where the fan sits and the radius of its blades, in screen pixels: the fan fills the
screen, at Rob's request (10/10/2026, "nothing but a big fan"). Each frame changes most of
the circle, about 130,000 pixels, which the tiled partial updates send in about 0.15 s.
</summary>
"""

FAN_BLADES = 7
FAN_TEMPS = (40.0, 85.0)
"""<summary>Blade colour runs from the palette's fan_cold at or below the first temperature
to fan_hot at or above the second.</summary>"""

FAN_SPEED = (30.0, 360.0)
"""<summary>Spin speed in degrees a second at 0 and 100 per cent CPU load.</summary>"""

FAN_MAX_STEP = 22.0
"""
<summary>
The most the blades may turn between two frames. With seven blades the pattern repeats
every 51 degrees, so a step over half that looks like turning backwards (the wagon wheel
effect). Above this the fan shows speed with motion blur instead of a bigger step.
</summary>
"""

_fan_state = {"angle": 0.0, "last": None}


def _mix(a: r.Colour, b: r.Colour, amount: float) -> r.Colour:
    """<summary>A colour amount of the way from a to b, amount 0 to 1.</summary>"""
    return tuple(round(a[i] + (b[i] - a[i]) * amount) for i in range(3))


def _blade(angle: float, inner: float, outer: float) -> list:
    """
    <summary>One curved blade as a polygon in canvas pixels, leading edge out then trailing edge back.</summary>
    <param name="angle">Where the blade root points, in degrees.</param>
    <remarks>
    The sweep makes the tip trail the root by about 34 degrees, like a case fan. The tip
    follows the outer circle, so at full screen size the fan's outline stays round.
    </remarks>
    """
    cx, cy = FAN_CENTRE
    points = []
    steps = 8
    for i in range(steps + 1):
        f = i / steps
        radius = inner + (outer - inner) * f
        a = math.radians(angle + 34 * f * f)
        points.append(((cx + radius * math.cos(a)) * r.SCALE, (cy + radius * math.sin(a)) * r.SCALE))
    for i in range(1, steps):  # round the tip along the fan's edge instead of a straight cut
        a = math.radians(angle + 34 + 26 * i / steps)
        points.append(((cx + outer * math.cos(a)) * r.SCALE, (cy + outer * math.sin(a)) * r.SCALE))
    for i in range(steps, -1, -1):
        f = i / steps
        radius = inner + (outer - inner) * f
        a = math.radians(angle + 34 * f * f + 16 + 10 * f)
        points.append(((cx + radius * math.cos(a)) * r.SCALE, (cy + radius * math.sin(a)) * r.SCALE))
    return points


def fan_step(load: Optional[float], now: float) -> Tuple[float, float]:
    """
    <summary>Advances the fan by the time since the last frame and returns where it is.</summary>
    <param name="load">CPU load per cent, or None (treated as idle).</param>
    <param name="now">A monotonic time in seconds.</param>
    <returns>The blade angle in degrees, and the motion blur sweep in degrees.</returns>
    <remarks>
    State is kept per process, so the service and the control panel preview each spin
    their own fan. The first call after a long gap moves one capped step, not a jump.
    </remarks>
    """
    share = r.fraction(load, 0, 100)
    speed = FAN_SPEED[0] + (FAN_SPEED[1] - FAN_SPEED[0]) * share
    last = _fan_state["last"]
    elapsed = 0.1 if last is None else max(0.0, now - last)
    step = min(speed * elapsed, FAN_MAX_STEP)
    _fan_state["angle"] = (_fan_state["angle"] + step) % 360
    _fan_state["last"] = now
    blur = max(0.0, speed * elapsed - FAN_MAX_STEP * 0.5) if share > 0.25 else 0.0
    return _fan_state["angle"], min(blur, FAN_MAX_STEP)


def draw_fan(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    Nothing but a big case fan: it spins faster with CPU load and turns from blue to red as
    the CPU heats up. No ring and no figures, so nothing but the blades ever changes.
    </summary>
    <remarks>
    The only theme with its own interval (0.1 s). At high speed the blades get faint
    trailing copies, motion blur, because a real increase in step would alias. When stopped
    (a headline) the fan is drawn still and grey, without text.
    </remarks>
    """
    stopped = ctx.headline is not None
    heat = r.fraction(stats.cpu_temp, *FAN_TEMPS)
    blade_colour = r.MUTED if stopped or stats.cpu_temp is None else _mix(r.FAN_COLD, r.FAN_HOT, heat)
    angle, blur = (0.0, 0.0) if stopped else fan_step(stats.cpu_load, time.monotonic())

    cx, cy = FAN_CENTRE
    frame_r = (FAN_RADIUS + 10) * r.SCALE
    draw.ellipse((cx * r.SCALE - frame_r, cy * r.SCALE - frame_r, cx * r.SCALE + frame_r, cy * r.SCALE + frame_r),
                 outline=r.TRACK, width=8 * r.SCALE)

    inner = 52
    if blur > 0:
        for ghost in (0.75, 0.5, 0.25):
            colour = _mix(r.BACKGROUND, blade_colour, 0.35 * (1 - ghost) + 0.1)
            for blade in range(FAN_BLADES):
                draw.polygon(_blade(angle - blur * ghost + blade * 360 / FAN_BLADES, inner, FAN_RADIUS), fill=colour)
    for blade in range(FAN_BLADES):
        draw.polygon(_blade(angle + blade * 360 / FAN_BLADES, inner, FAN_RADIUS), fill=blade_colour)

    hub = (inner + 2) * r.SCALE
    draw.ellipse((cx * r.SCALE - hub, cy * r.SCALE - hub, cx * r.SCALE + hub, cy * r.SCALE + hub),
                 fill=r.HUB, outline=r.TRACK, width=4 * r.SCALE)


WHEEL_CENTRE = (CENTRE, 228)
WHEEL_RADIUS = 196
WHEEL_RUNGS = 12
"""
<summary>
The hamster wheel's size and its rungs. Twelve rungs repeat every 30 degrees, so a frame
step over 15 degrees would look like turning backwards; HAMSTER_MAX_STEP keeps under it.
</summary>
"""

HAMSTER_TEMPS = (40.0, 85.0)
HAMSTER_SPEED = (40.0, 300.0)
"""<summary>Wheel speed in degrees a second, from the first temperature (or cooler) to the second (or hotter).</summary>"""

HAMSTER_MAX_STEP = 10.0
"""
<summary>The most the wheel may turn between two frames; see WHEEL_RUNGS.</summary>
<remarks>
Was 14, just under the 15 degree point where the rungs seem to turn backwards; streamed at
about five frames a second the wheel sat right on it and looked unsure which way it turned
(Rob, 07/11/2026). 10 leaves a clear margin; a fast hamster streamed simply turns slower.
</remarks>
"""

STRIDES_PER_TURN = 6.0
"""
<summary>Hamster strides per full turn of the wheel, so the legs keep pace with the tread.</summary>
<remarks>
Was 9: at full speed a stride took four frames, so the legs jumped between quarter
positions and seemed to run backwards. 6 gives six frames a stride in the fastest loop,
and keeps every loop a whole number of strides (2, 6 and 12; see hamstervideo.py).
</remarks>
"""

_hamster_state = {"angle": 0.0, "last": None}


def hamster_step(temp: Optional[float], now: float) -> Tuple[float, float]:
    """
    <summary>Advances the wheel by the time since the last frame; returns the wheel angle and the stride phase.</summary>
    <param name="temp">CPU temperature, or None (treated as cool).</param>
    <param name="now">A monotonic time in seconds.</param>
    <returns>(wheel angle in degrees, stride phase in radians).</returns>
    <remarks>Per process, like the fan. The stride follows the wheel, so the legs never slip on the tread.</remarks>
    """
    share = r.fraction(temp, *HAMSTER_TEMPS)
    speed = HAMSTER_SPEED[0] + (HAMSTER_SPEED[1] - HAMSTER_SPEED[0]) * share
    last = _hamster_state["last"]
    elapsed = 0.1 if last is None else max(0.0, now - last)
    _hamster_state["angle"] = (_hamster_state["angle"] + min(speed * elapsed, HAMSTER_MAX_STEP)) % 360
    _hamster_state["last"] = now
    angle = _hamster_state["angle"]
    return angle, math.radians(angle * STRIDES_PER_TURN)


def _ellipse(draw: ImageDraw.ImageDraw, x: float, y: float, rx: float, ry: float, colour: r.Colour) -> None:
    """<summary>A filled ellipse centred on (x, y), in screen pixels.</summary>"""
    s = r.SCALE
    draw.ellipse(((x - rx) * s, (y - ry) * s, (x + rx) * s, (y + ry) * s), fill=colour)


def _hamster(draw: ImageDraw.ImageDraw, phase: float, still: bool) -> None:
    """
    <summary>The hamster, facing right, running on the bottom of the wheel.</summary>
    <param name="phase">Stride phase in radians: front and back legs swing in opposite pairs.</param>
    <param name="still">Draw it standing, legs together, for the stopped frame.</param>
    """
    fur = r.HAMSTER
    belly = _mix(fur, (255, 245, 230), 0.7)
    dark = _mix(fur, (0, 0, 0), 0.35)
    swing = 0.0 if still else math.sin(phase)
    bob = 0.0 if still else abs(math.sin(phase)) * 4
    floor = WHEEL_CENTRE[1] + WHEEL_RADIUS - 12  # paws on the rungs at the very bottom
    cx, cy = CENTRE - 6, floor - 52 - bob

    for offset, side in ((-34, 1), (36, -1)):  # far legs first, darker
        x = cx + offset + 14 * swing * side
        _ellipse(draw, x, floor - 10, 11, 14, dark)
    _ellipse(draw, cx - 62, cy + 6, 12, 8, dark)  # tail
    _ellipse(draw, cx, cy, 72, 46, fur)  # body
    _ellipse(draw, cx + 6, cy + 18, 50, 24, belly)
    for offset, side in ((-26, -1), (44, 1)):  # near legs
        x = cx + offset + 16 * swing * side
        _ellipse(draw, x, floor - 8, 12, 15, fur)
        _ellipse(draw, x + 4, floor + 3, 10, 5, belly)
    hx, hy = cx + 66, cy - 18  # head
    _ellipse(draw, hx - 6, hy - 34, 14, 16, fur)  # ear
    _ellipse(draw, hx - 6, hy - 33, 8, 10, (240, 150, 160))
    _ellipse(draw, hx, hy, 42, 36, fur)
    _ellipse(draw, hx + 12, hy + 12, 24, 18, belly)  # cheek
    _ellipse(draw, hx + 14, hy - 8, 6, 7, (20, 20, 24))  # eye
    _ellipse(draw, hx + 16, hy - 10, 2, 2, (255, 255, 255))
    _ellipse(draw, hx + 40, hy + 4, 6, 5, (240, 120, 140))  # nose


def draw_hamster(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    A hamster running in a wheel, faster as the CPU heats up. The wheel's rungs and spokes
    turn under it and its legs keep pace.
    </summary>
    <remarks>
    Animated at 0.1 s like the fan, or played as smooth stored loops (hamstervideo.py).
    The tread moves left under a hamster facing right, so the wheel turns clockwise,
    which is Pillow's increasing angle. Stopped (a headline), it stands still in a still wheel.
    The stand is drawn darkest and the three spokes darker than the rims, so the lines that
    never move cannot be mistaken for ones that do: with six spokes in the stand's colour
    the wheel read as turning both ways at once (07/11/2026).
    </remarks>
    """
    still = ctx.headline is not None
    angle, phase = (0.0, 0.0) if still else hamster_step(stats.cpu_temp, time.monotonic())
    cx, cy = WHEEL_CENTRE
    s = r.SCALE
    wheel = r.WHEEL
    shade = _mix(wheel, r.BACKGROUND, 0.5)
    stand = _mix(wheel, r.BACKGROUND, 0.72)

    base = cy + WHEEL_RADIUS + 18  # stand: two legs from the hub to the floor
    draw.line(((cx * s, cy * s), ((cx - 70) * s, base * s)), fill=stand, width=10 * s)
    draw.line(((cx * s, cy * s), ((cx + 70) * s, base * s)), fill=stand, width=10 * s)

    for spoke in range(3):
        a = math.radians(angle + spoke * 120)
        draw.line(((cx * s, cy * s), ((cx + (WHEEL_RADIUS - 20) * math.cos(a)) * s,
                                      (cy + (WHEEL_RADIUS - 20) * math.sin(a)) * s)), fill=shade, width=4 * s)
    for rim in (WHEEL_RADIUS, WHEEL_RADIUS - 22):
        draw.ellipse(((cx - rim) * s, (cy - rim) * s, (cx + rim) * s, (cy + rim) * s), outline=wheel, width=6 * s)
    for rung in range(WHEEL_RUNGS):
        a = math.radians(angle + rung * 360 / WHEEL_RUNGS)
        inner, outer = WHEEL_RADIUS - 22, WHEEL_RADIUS
        draw.line((((cx + inner * math.cos(a)) * s, (cy + inner * math.sin(a)) * s),
                   ((cx + outer * math.cos(a)) * s, (cy + outer * math.sin(a)) * s)), fill=wheel, width=5 * s)
    _ellipse(draw, cx, cy, 14, 14, wheel)
    _hamster(draw, phase, still)


def bits(value: Optional[float]) -> str:
    """
    <summary>Formats a speed in bits a second with a unit that keeps it to about three figures.</summary>
    <returns>For example "850 kb/s", "12.3 Mb/s", "1.05 Gb/s", or "--" when unknown.</returns>
    """
    if value is None or not math.isfinite(value) or value < 0:
        return "--"
    for unit, size in (("Gb/s", 1e9), ("Mb/s", 1e6), ("kb/s", 1e3)):
        if value >= size:
            shown = value / size
            return "{:.{}f} {}".format(shown, 0 if shown >= 100 else (1 if shown >= 10 else 2), unit)
    return "{:.0f} b/s".format(value)


def _speed_with_arrow(draw: ImageDraw.ImageDraw, y: int, value: str, size: int, colour: r.Colour, down: bool) -> None:
    """
    <summary>Draws a speed centred on the screen with a triangle arrow in front of it.</summary>
    <remarks>
    The arrow is drawn, not typed: Roboto has no arrow glyphs and shows an empty box for
    them, which looks broken on the screen.
    </remarks>
    """
    font = r.font(size, bold=True)
    width = font.getlength(value) / r.SCALE
    arrow = size * 0.55
    gap = size * 0.3
    start = CENTRE - (width + arrow + gap) / 2
    r.text(draw, int(start + arrow + gap), y, value, size, colour, bold=True, anchor="lm")
    half, tip = arrow / 2, arrow * 0.45
    x0, x1, xm = start * r.SCALE, (start + arrow) * r.SCALE, (start + half) * r.SCALE
    if down:
        points = [(x0, (y - tip) * r.SCALE), (x1, (y - tip) * r.SCALE), (xm, (y + tip) * r.SCALE)]
    else:
        points = [(x0, (y + tip) * r.SCALE), (x1, (y + tip) * r.SCALE), (xm, (y - tip) * r.SCALE)]
    draw.polygon(points, fill=colour)


def draw_network(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    Download and upload speed, large, over a graph of recent readings: download as a
    filled area, upload as a line, both on one scale that fits the busiest moment shown.
    </summary>
    <remarks>
    The graph moves every reading, so it is kept to a 300 by 110 pixel box in the middle;
    the figures above change only when the speed does. Speeds count every interface but
    loopback (see sensors.NetworkMeter).
    </remarks>
    """
    r.text(draw, CENTRE, 72, ctx.headline or "NETWORK", 22, r.MUTED, bold=True)
    _speed_with_arrow(draw, 140, bits(stats.net_down), 46, r.DOWNLOAD, down=True)
    _speed_with_arrow(draw, 206, bits(stats.net_up), 34, r.UPLOAD, down=False)

    left, top, width, height = 90, 260, 300, 110
    draw.rectangle((left * r.SCALE, top * r.SCALE, (left + width) * r.SCALE, (top + height) * r.SCALE),
                   outline=r.TRACK, width=2 * r.SCALE)
    history = list(stats.net_history)
    if len(history) >= 2:
        peak = max(max(down, up) for down, up in history) or 1.0
        step = width / (len(history) - 1)

        def point(index: int, value: float) -> Tuple[float, float]:
            """<summary>Canvas position of one reading in the graph box.</summary>"""
            return ((left + index * step) * r.SCALE, (top + height - 4 - (height - 8) * value / peak) * r.SCALE)

        area = [point(i, down) for i, (down, _) in enumerate(history)]
        area += [((left + width) * r.SCALE, (top + height - 2) * r.SCALE), (left * r.SCALE, (top + height - 2) * r.SCALE)]
        draw.polygon(area, fill=_mix(r.BACKGROUND, r.DOWNLOAD, 0.45))
        draw.line([point(i, down) for i, (down, _) in enumerate(history)], fill=r.DOWNLOAD, width=2 * r.SCALE)
        draw.line([point(i, up) for i, (_, up) in enumerate(history)], fill=r.UPLOAD, width=2 * r.SCALE)
        r.text(draw, left + width, top - 12, "peak " + bits(peak), 14, r.MUTED, anchor="rm")
    if stats.net_span_s:
        r.text(draw, CENTRE, 400, "last " + span(stats.net_span_s), 16, r.MUTED)


def span(seconds: float) -> str:
    """<summary>A graph's time span in words: "45 seconds", "2 minutes", "10 minutes".</summary>"""
    if seconds < 90:
        return "{:.0f} seconds".format(seconds)
    return "{:.0f} minutes".format(seconds / 60)


def uptime(seconds: Optional[float]) -> str:
    """
    <summary>Formats time since boot as "3d 4h", "4h 12m" or "12m".</summary>
    <remarks>A clock that has gone backwards (a negative or not a number value) shows as "--" rather than nonsense.</remarks>
    """
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--"
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return "{}d {}h".format(days, hours)
    if hours:
        return "{}h {}m".format(hours, minutes)
    return "{}m".format(minutes)


def draw_system(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    The machine at a glance: CPU model and core count, GPU model and memory, memory and disk
    use, uptime and kernel.
    </summary>
    <remarks>
    Names come pre-shortened from sensors.read_system_info(). Lines are kept short because
    the circle narrows towards the top and bottom; a very long name is cut with an ellipsis.
    </remarks>
    """
    s = stats.system

    def fit(value: Optional[str], limit: int) -> str:
        """<summary>The value, or "--", cut to limit characters.</summary>"""
        if not value:
            return "--"
        return value if len(value) <= limit else value[:limit - 1] + "\u2026"

    r.text(draw, CENTRE, 72, ctx.headline or "SYSTEM", 22, r.MUTED, bold=True)
    r.text(draw, CENTRE, 122, fit(s.cpu_name, 18), 30, r.COOL, bold=True)
    cores = "{} cores, {} threads".format(s.cpu_cores, s.cpu_threads) if s.cpu_cores else "--"
    r.text(draw, CENTRE, 156, cores, 18, r.MUTED)
    r.text(draw, CENTRE, 204, fit(s.gpu_name, 18), 30, r.GPU, bold=True)
    r.text(draw, CENTRE, 238, r.number(s.gpu_memory_gb, " GB video memory"), 18, r.MUTED)
    r.text(draw, CENTRE, 290, "RAM " + r.number(stats.ram_used_gb, "", 1) + " / " + r.number(stats.ram_total_gb, " GB"), 22, r.TEXT)
    r.text(draw, CENTRE, 324, "Disk " + r.number(stats.disk_used_gb) + " / " + r.number(stats.disk_total_gb, " GB"), 22, r.TEXT)
    r.text(draw, CENTRE, 358, "Up " + uptime(stats.uptime_s), 22, r.TEXT)
    r.text(draw, CENTRE, 394, fit(("Linux " + s.kernel) if s.kernel else None, 24), 16, r.MUTED)


def draw_picture(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>A picture filling the screen and nothing else: the slideshow and playlist screens.</summary>
    <remarks>
    Which picture is decided before drawing (screens.picture()) and arrives as
    ctx.background. With none, or one that cannot be read, it says what to do instead.
    </remarks>
    """
    picture = _load_background(ctx.background)
    if picture is not None:
        canvas.paste(picture)
        if ctx.headline:
            r.text(draw, CENTRE, 380, ctx.headline, 30, r.TEXT)
        return
    r.text(draw, CENTRE, 210, ctx.headline or "No pictures yet", 26, r.MUTED)
    r.text(draw, CENTRE, 250, "Add them in the control panel", 18, r.MUTED)


def draw_game(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>A Steam game's artwork: its wide background cropped to the circle, its logo across the middle.</summary>
    <remarks>
    ctx.background is Steam's artwork folder for the game (rules.artwork_folder()), not a
    picture. The first of the hero, header and cover pictures found (rules.find_art())
    is the background; the logo, when there is one, is scaled to 75 per cent of the width
    and centred. Missing files fall back quietly to the next.
    </remarks>
    """
    from .rules import find_art

    folder = ctx.background
    if folder is None:
        return
    art = find_art(folder)
    for role in ("hero", "header", "cover"):
        picture = _load_background(art.get(role))
        if picture is not None:
            canvas.paste(picture)
            break
    logo = _load_logo(art.get("logo"))
    if logo is not None:
        width = int(r.CANVAS * 0.75)
        height = max(1, int(logo.height * width / logo.width))
        if height > r.CANVAS * 0.45:
            height = int(r.CANVAS * 0.45)
            width = max(1, int(logo.width * height / logo.height))
        logo = logo.resize((width, height), Image.Resampling.LANCZOS)
        canvas.paste(logo, ((r.CANVAS - width) // 2, (r.CANVAS - height) // 2), logo)


SMALL_LABEL = 32
"""
<summary>
Smallest text size the small layouts use, in the 480 pixel coordinates: 16 pixels once
scaled to the Kraken's 240, about 2.6 mm on its 1.54 inch screen. The normal layouts go
down to 14, which would be 7 pixels there and cannot be read.
</summary>
"""


def draw_dial_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>The dial for a small screen: the two rings, CPU temperature, CPU load and GPU temperature.</summary>
    <remarks>The clock and memory lines are dropped; the headline ("stopped") takes the CPU label's place.</remarks>
    """
    r.ring(draw, 222, 30, 135, 270, r.fraction(stats.cpu_temp, r.TEMP_MIN, r.TEMP_MAX),
           r.temp_colour(stats.cpu_temp))
    r.ring(draw, 180, 12, 135, 270, r.fraction(stats.cpu_load, 0, 100), r.LOAD)
    r.text(draw, CENTRE, 140, ctx.headline or "CPU", 36, r.MUTED, bold=True)
    r.text(draw, CENTRE, 232, r.number(stats.cpu_temp, "°"), 136, r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, CENTRE, 322, "LOAD " + r.number(stats.cpu_load, "%"), 36, r.LOAD, bold=True)
    r.text(draw, CENTRE, 378, "GPU " + r.number(stats.gpu_temp, "°"), 36,
           r.temp_colour(stats.gpu_temp, r.GPU_LIMITS), bold=True)


def draw_twin_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>Twin gauges for a small screen: the same arcs, thicker, with only the two temperatures and labels.</summary>
    <remarks>The load figures are dropped; the inner arcs still show load.</remarks>
    """
    cpu_temp = r.fraction(stats.cpu_temp, r.TEMP_MIN, r.TEMP_MAX)
    gpu_temp = r.fraction(stats.gpu_temp, r.TEMP_MIN, r.TEMP_MAX)
    sweep = 180 - 2 * TWIN_GAP
    top, bottom_end = 180 + TWIN_GAP, 180 - TWIN_GAP
    r.ring(draw, 222, 26, top, sweep, cpu_temp, r.temp_colour(stats.cpu_temp))
    r.ring(draw, 188, 12, top, sweep, r.fraction(stats.cpu_load, 0, 100), r.LOAD)
    r.arc(draw, 222, 26, TWIN_GAP, bottom_end, r.TRACK)
    r.arc(draw, 222, 26, bottom_end - sweep * gpu_temp, bottom_end, r.temp_colour(stats.gpu_temp, r.GPU_LIMITS))
    r.arc(draw, 188, 12, TWIN_GAP, bottom_end, r.TRACK)
    r.arc(draw, 188, 12, bottom_end - sweep * r.fraction(stats.gpu_load, 0, 100), bottom_end, r.GPU)
    draw.line(((90 * r.SCALE, CENTRE * r.SCALE), (390 * r.SCALE, CENTRE * r.SCALE)),
              fill=r.TRACK, width=3 * r.SCALE)
    r.text(draw, CENTRE, 104, "CPU", SMALL_LABEL, r.MUTED, bold=True)
    r.text(draw, CENTRE, 172, r.number(stats.cpu_temp, "°"), 100, r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, CENTRE, 308, r.number(stats.gpu_temp, "°"), 100,
           r.temp_colour(stats.gpu_temp, r.GPU_LIMITS), bold=True)
    r.text(draw, CENTRE, 376, ctx.headline or "GPU", SMALL_LABEL, r.MUTED, bold=True)


def draw_clock_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>The clock for a small screen: thicker minute ticks, a larger time, the date and the CPU temperature.</summary>
    <remarks>The GPU temperature is dropped to make room. The date is DD/MM/YYYY, as the house style asks.</remarks>
    """
    for minute in range(60):
        angle = math.radians(-90 + minute * 6)
        hour_mark = minute % 5 == 0
        inner = 192 if hour_mark else 206
        lit = ctx.headline is None and minute <= stats.now.minute
        x1 = (CENTRE + inner * math.cos(angle)) * r.SCALE
        y1 = (CENTRE + inner * math.sin(angle)) * r.SCALE
        x2 = (CENTRE + 226 * math.cos(angle)) * r.SCALE
        y2 = (CENTRE + 226 * math.sin(angle)) * r.SCALE
        draw.line(((x1, y1), (x2, y2)), fill=r.COOL if lit else r.TRACK, width=(9 if hour_mark else 5) * r.SCALE)
    if ctx.headline:
        r.text(draw, CENTRE, 215, ctx.headline, 64, r.MUTED)
    else:
        r.text(draw, CENTRE, 205, stats.now.strftime("%H:%M"), 136, r.TEXT, light=True)
    r.text(draw, CENTRE, 296, stats.now.strftime("%d/%m/%Y"), 34, r.MUTED)
    r.text(draw, CENTRE, 360, "CPU " + r.number(stats.cpu_temp, "°"), 36, r.temp_colour(stats.cpu_temp), bold=True)


def draw_minimal_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """<summary>Minimal for a small screen: the two temperatures, larger, without the line of load figures.</summary>"""
    r.text(draw, CENTRE, 96, "CPU", 34, r.MUTED, bold=True)
    r.text(draw, CENTRE, 174, r.number(stats.cpu_temp, "°"), 124, r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, CENTRE, 270, ctx.headline or "GPU", 34, r.MUTED, bold=True)
    r.text(draw, CENTRE, 348, r.number(stats.gpu_temp, "°"), 124,
           r.temp_colour(stats.gpu_temp, r.GPU_LIMITS), bold=True)


def draw_image_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>Your picture for a small screen: the same picture, with a larger panel and figures.</summary>
    """
    picture = _load_background(ctx.background)
    if picture is not None:
        canvas.paste(picture)
    else:
        r.text(draw, CENTRE, 190, "No picture", 40, r.MUTED)
        r.text(draw, CENTRE, 240, "Choose one in", SMALL_LABEL, r.MUTED)
        r.text(draw, CENTRE, 276, "the control panel", SMALL_LABEL, r.MUTED)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(
        (80 * r.SCALE, 310 * r.SCALE, 400 * r.SCALE, 430 * r.SCALE), radius=30 * r.SCALE, fill=(0, 0, 0, 170))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB"))
    draw = ImageDraw.Draw(canvas)
    if ctx.headline:
        r.text(draw, CENTRE, 370, ctx.headline, 40, r.TEXT)
        return
    r.text(draw, 170, 338, "CPU", 28, r.TEXT, bold=True)
    r.text(draw, 310, 338, "GPU", 28, r.TEXT, bold=True)
    r.text(draw, 170, 392, r.number(stats.cpu_temp, "°"), 64, r.temp_colour(stats.cpu_temp), bold=True)
    r.text(draw, 310, 392, r.number(stats.gpu_temp, "°"), 64,
           r.temp_colour(stats.gpu_temp, r.GPU_LIMITS), bold=True)


def draw_network_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>Network for a small screen: both speeds, larger, over a shorter graph without its small labels.</summary>
    """
    r.text(draw, CENTRE, 92, ctx.headline or "NETWORK", SMALL_LABEL, r.MUTED, bold=True)
    _speed_with_arrow(draw, 170, bits(stats.net_down), 60, r.DOWNLOAD, down=True)
    _speed_with_arrow(draw, 250, bits(stats.net_up), 46, r.UPLOAD, down=False)
    left, top, width, height = 110, 300, 260, 90
    draw.rectangle((left * r.SCALE, top * r.SCALE, (left + width) * r.SCALE, (top + height) * r.SCALE),
                   outline=r.TRACK, width=3 * r.SCALE)
    history = list(stats.net_history)
    if len(history) >= 2:
        peak = max(max(down, up) for down, up in history) or 1.0
        step = width / (len(history) - 1)

        def point(index: int, value: float) -> Tuple[float, float]:
            """<summary>Canvas position of one reading in the graph box.</summary>"""
            return ((left + index * step) * r.SCALE, (top + height - 4 - (height - 8) * value / peak) * r.SCALE)

        area = [point(i, down) for i, (down, _) in enumerate(history)]
        area += [((left + width) * r.SCALE, (top + height - 2) * r.SCALE), (left * r.SCALE, (top + height - 2) * r.SCALE)]
        draw.polygon(area, fill=_mix(r.BACKGROUND, r.DOWNLOAD, 0.45))
        draw.line([point(i, down) for i, (down, _) in enumerate(history)], fill=r.DOWNLOAD, width=4 * r.SCALE)
        draw.line([point(i, up) for i, (_, up) in enumerate(history)], fill=r.UPLOAD, width=4 * r.SCALE)


def _fitted(value: str, size: int, smallest: int, width: int) -> Tuple[str, int]:
    """
    <summary>Shrinks a bold line until it fits a width (screen pixels), then cuts it with an ellipsis at the smallest size.</summary>
    <returns>The text to draw and the size to draw it at.</returns>
    """
    while size > smallest and r.font(size, bold=True).getlength(value) / r.SCALE > width:
        size -= 2
    while len(value) > 2 and r.font(size, bold=True).getlength(value) / r.SCALE > width:
        value = value[:-2].rstrip() + "\u2026"
    return value, size


def draw_system_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>System for a small screen: CPU and GPU model, memory and uptime.</summary>
    <remarks>Cores, video memory, disk and kernel are dropped; names are cut shorter to fit the larger text.</remarks>
    """
    s = stats.system

    def fit(value: Optional[str], limit: int) -> str:
        """<summary>The value, or "--", cut to limit characters.</summary>"""
        if not value:
            return "--"
        return value if len(value) <= limit else value[:limit - 1] + "…"

    r.text(draw, CENTRE, 92, ctx.headline or "SYSTEM", SMALL_LABEL, r.MUTED, bold=True)
    for y, name, colour in ((160, s.cpu_name, r.COOL), (232, s.gpu_name, r.GPU)):
        value, size = _fitted(fit(name, 22), 42, 34, 360)
        r.text(draw, CENTRE, y, value, size, colour, bold=True)
    r.text(draw, CENTRE, 306, "RAM " + r.number(stats.ram_used_gb, "", 1) + "/" + r.number(stats.ram_total_gb, " GB"),
           36, r.TEXT)
    r.text(draw, CENTRE, 366, "Up " + uptime(stats.uptime_s), 36, r.TEXT)


def draw_picture_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """<summary>The picture screens for a small screen: the picture as before; without one, larger words.</summary>"""
    picture = _load_background(ctx.background)
    if picture is not None:
        canvas.paste(picture)
        if ctx.headline:
            r.text(draw, CENTRE, 380, ctx.headline, 40, r.TEXT)
        return
    r.text(draw, CENTRE, 200, ctx.headline or "No pictures yet", 40, r.MUTED)
    r.text(draw, CENTRE, 252, "Add them in", SMALL_LABEL, r.MUTED)
    r.text(draw, CENTRE, 288, "the control panel", SMALL_LABEL, r.MUTED)


COOLANT_RANGE = (20.0, 60.0)
COOLANT_LIMITS = (38.0, 45.0)
"""
<summary>
The Cooler screen's ring runs from empty at the first range figure to full at the second,
and the coolant figure turns amber, then red, at the two limits. These are a starting
choice, not a manufacturer figure: coolant usually sits well under 40 degrees, much
cooler than the CPU, so the CPU's limits would never colour it. Change them here if they
read wrong in use.
</summary>
"""


def draw_cooler(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    The cooler itself: coolant temperature large on a ring, pump and fan speed underneath.
    Only coolers that report these (devices.Device.cooler_sensors) offer this screen.
    </summary>
    <remarks>
    One layout for every size: it is already sized for the small screen. Readings come
    from the cooler's kernel driver (sensors.read_cooler()); missing ones show as "--".
    </remarks>
    """
    r.ring(draw, 222, 30, 135, 270, r.fraction(stats.coolant_temp, *COOLANT_RANGE),
           r.temp_colour(stats.coolant_temp, COOLANT_LIMITS))
    r.text(draw, CENTRE, 140, ctx.headline or "LIQUID", 36, r.MUTED, bold=True)
    r.text(draw, CENTRE, 230, r.number(stats.coolant_temp, "°", 1), 116,
           r.temp_colour(stats.coolant_temp, COOLANT_LIMITS), bold=True)
    r.text(draw, CENTRE, 318, "PUMP " + r.number(stats.pump_rpm, " rpm"), 34, r.LOAD, bold=True)
    r.text(draw, CENTRE, 370, "FAN " + r.number(stats.fan_rpm, " rpm"), 34, r.TEXT, bold=True)


SCAN_PERIOD = 6.0
"""
<summary>
Seconds for one turn of the Scanner's sweep. Streamed at about ten frames a second that is
six degrees a frame, smooth enough; a sweep has no repeating pattern, so it cannot alias.
</summary>
"""

SCAN_RADIUS = 210
SCAN_RINGS = (70, 140, 210)
"""<summary>The sweep's length and the range rings, in screen pixels from the centre.</summary>"""

SCAN_TRAIL = 100.0
SCAN_TRAIL_STEPS = 20
SCAN_TRAIL_STRENGTH = 0.45
"""
<summary>
The fading wake behind the sweep: how many degrees long, in how many bands, and how
bright at its leading edge, as a share of the sweep colour over the background.
</summary>
"""

SCAN_CONTACTS = (("CPU", 232.0), ("GPU", 308.0))
"""
<summary>
The two contacts and their bearings, in Pillow's degrees (clockwise from three o'clock), so
both sit in the upper half, clear of the figures at the bottom.
</summary>
"""

SCAN_RANGE = (195.0, 45.0)
"""
<summary>
A contact's distance from the centre at render.TEMP_MIN and at render.TEMP_MAX: the hotter
the part, the closer it has come, as on a motion tracker.
</summary>
"""

SCAN_FADE = 300.0
"""<summary>Degrees of sweep after a contact is lit before it has faded to its dimmest.</summary>"""

SCAN_PING = 80.0
"""<summary>Degrees of sweep the ring that rises from a freshly lit contact lasts.</summary>"""


def scanner_angle(now: float) -> float:
    """
    <summary>Where the Scanner's sweep points at a moment, in Pillow's degrees.</summary>
    <param name="now">A monotonic time in seconds.</param>
    <remarks>
    Worked out from the clock, not advanced per frame like the fan, so it keeps no state and
    the service and the panel preview agree. A slow frame skips ahead rather than lagging.
    </remarks>
    """
    return (now % SCAN_PERIOD) / SCAN_PERIOD * 360.0


def _contact_point(bearing: float, temp: Optional[float]) -> Tuple[float, float]:
    """<summary>Where a contact sits in screen pixels: on its bearing, nearer the centre the hotter it runs.</summary>"""
    share = r.fraction(temp, r.TEMP_MIN, r.TEMP_MAX)
    reach = SCAN_RANGE[0] + (SCAN_RANGE[1] - SCAN_RANGE[0]) * share
    return CENTRE + reach * math.cos(math.radians(bearing)), CENTRE + reach * math.sin(math.radians(bearing))


def _draw_scanner(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context,
                  small: bool, now: Optional[float] = None) -> None:
    """
    <summary>The Scanner, for either screen size; see draw_scanner().</summary>
    <param name="small">The Kraken's layout: the same picture with fewer, larger figures.</param>
    <param name="now">A monotonic time; the current one when None. Tests pass their own.</param>
    """
    stopped = ctx.headline is not None
    angle = scanner_angle(time.monotonic() if now is None else now)
    s = r.SCALE
    c = CENTRE * s

    def box(radius: float) -> Tuple[float, float, float, float]:
        """<summary>The canvas box of a circle round the centre, radius in screen pixels.</summary>"""
        return (c - radius * s, c - radius * s, c + radius * s, c + radius * s)

    if not stopped:
        band = SCAN_TRAIL / SCAN_TRAIL_STEPS
        for step in range(SCAN_TRAIL_STEPS):
            strength = SCAN_TRAIL_STRENGTH * (step + 1) / SCAN_TRAIL_STEPS
            start = angle - SCAN_TRAIL + step * band
            draw.pieslice(box(SCAN_RADIUS), start, start + band + 0.5, fill=_mix(r.BACKGROUND, r.LOAD, strength))

    for radius in SCAN_RINGS:
        draw.ellipse(box(radius), outline=r.TRACK, width=3 * s)
    draw.line((c - SCAN_RADIUS * s, c, c + SCAN_RADIUS * s, c), fill=r.TRACK, width=2 * s)
    draw.line((c, c - SCAN_RADIUS * s, c, c + SCAN_RADIUS * s), fill=r.TRACK, width=2 * s)

    if not stopped:
        tip = (c + SCAN_RADIUS * s * math.cos(math.radians(angle)), c + SCAN_RADIUS * s * math.sin(math.radians(angle)))
        draw.line((c, c) + tip, fill=r.LOAD, width=4 * s)

    for (label, bearing), temp in zip(SCAN_CONTACTS, (stats.cpu_temp, stats.gpu_temp)):
        if temp is None:
            continue
        x, y = _contact_point(bearing, temp)
        colour = r.temp_colour(temp, r.CPU_LIMITS if label == "CPU" else r.GPU_LIMITS)
        since = SCAN_FADE if stopped else (angle - bearing) % 360
        lit = max(0.25, 1.0 - since / SCAN_FADE)
        _ellipse(draw, x, y, 11, 11, _mix(r.BACKGROUND, colour, lit))
        if since < SCAN_PING:
            ping = since / SCAN_PING
            reach = (12 + 26 * ping) * s
            draw.ellipse((x * s - reach, y * s - reach, x * s + reach, y * s + reach),
                         outline=_mix(r.BACKGROUND, colour, 1.0 - ping), width=3 * s)

    centre_dot = 6 * s
    draw.ellipse((c - centre_dot, c - centre_dot, c + centre_dot, c + centre_dot), fill=r.LOAD)

    if stopped:
        r.text(draw, CENTRE, 300, ctx.headline, 56 if small else 40, r.TEXT, bold=True)
        return
    cpu = r.temp_colour(stats.cpu_temp)
    gpu = r.temp_colour(stats.gpu_temp, r.GPU_LIMITS)
    if small:
        r.text(draw, 170, 318, "CPU", 30, r.MUTED, bold=True)
        r.text(draw, 310, 318, "GPU", 30, r.MUTED, bold=True)
        r.text(draw, 170, 372, r.number(stats.cpu_temp, "°"), 64, cpu, bold=True)
        r.text(draw, 310, 372, r.number(stats.gpu_temp, "°"), 64, gpu, bold=True)
        return
    r.text(draw, CENTRE, 92, "LOAD " + r.number(stats.cpu_load, "%"), 18, r.MUTED, bold=True)
    r.text(draw, 175, 350, "CPU", 16, r.MUTED, bold=True)
    r.text(draw, 305, 350, "GPU", 16, r.MUTED, bold=True)
    r.text(draw, 175, 389, r.number(stats.cpu_temp, "°"), 40, cpu, bold=True)
    r.text(draw, 305, 389, r.number(stats.gpu_temp, "°"), 40, gpu, bold=True)


def draw_scanner(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    A motion tracker: a sweep turning over range rings with a fading wake, and the CPU and
    GPU as two contacts that flare and ping as the sweep passes, then fade. The hotter a part
    runs, the nearer the centre its contact has crept. Temperatures underneath.
    </summary>
    <remarks>
    Animated at the fan's 0.1 s interval and always streamed (it is not in
    videofan.LOOP_SETS), since the contacts move with live readings that a stored loop could
    not show. What changes each frame is the wake, about a quarter of the circle, sent as
    tiles. Made to go with the console palettes; the sweep is the load
    colour and the contacts the temperature colours. Stopped, it is drawn still with the
    headline and no sweep.
    </remarks>
    """
    _draw_scanner(canvas, draw, stats, ctx, small=False)


def draw_scanner_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """<summary>The Scanner for a small screen: no load line, and the two temperatures larger.</summary>"""
    _draw_scanner(canvas, draw, stats, ctx, small=True)


FAN_SEE_THROUGH = (0.12, 0.85)
"""
<summary>
How much of the picture shows through the blades of the Fan over a picture, at 0 and at 100
per cent CPU load: a fan at rest hides what is behind it, and one spinning hard all but
vanishes, as a real one does.
</summary>
"""


def draw_fan_picture(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    The big fan, exactly as the Fan screen draws it, over a picture of your choice: the
    harder the CPU works, the faster it spins and the more the picture shows through.
    </summary>
    <remarks>
    The picture is settings.fan_picture, handed in as ctx.background. Shares the Fan
    screen's spin (fan_step()) and blade shape; the blades are drawn on a clear layer at the
    see through share for the load and laid over the picture. Always streamed: the picture
    is yours and changes, so there are no stored loops. Stopped, the fan stands still and
    grey over the picture.
    </remarks>
    """
    picture = _load_background(ctx.background)
    if picture is not None:
        canvas.paste(picture)
    stopped = ctx.headline is not None
    share = 0.0 if stopped else r.fraction(stats.cpu_load, 0, 100)
    see = FAN_SEE_THROUGH[0] + (FAN_SEE_THROUGH[1] - FAN_SEE_THROUGH[0]) * share
    solid = round(255 * (1.0 - see)) if picture is not None else 255
    heat = r.fraction(stats.cpu_temp, *FAN_TEMPS)
    blade_colour = r.MUTED if stopped or stats.cpu_temp is None else _mix(r.FAN_COLD, r.FAN_HOT, heat)
    angle, blur = (0.0, 0.0) if stopped else fan_step(stats.cpu_load, time.monotonic())

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    cx, cy = FAN_CENTRE
    s = r.SCALE
    frame_r = (FAN_RADIUS + 10) * s
    pen.ellipse((cx * s - frame_r, cy * s - frame_r, cx * s + frame_r, cy * s + frame_r),
                outline=r.TRACK + (255,), width=8 * s)
    inner = 52
    if blur > 0:
        for ghost in (0.75, 0.5, 0.25):
            ghost_alpha = round(solid * (0.35 * (1 - ghost) + 0.1))
            for blade in range(FAN_BLADES):
                pen.polygon(_blade(angle - blur * ghost + blade * 360 / FAN_BLADES, inner, FAN_RADIUS),
                            fill=blade_colour + (ghost_alpha,))
    for blade in range(FAN_BLADES):
        pen.polygon(_blade(angle + blade * 360 / FAN_BLADES, inner, FAN_RADIUS), fill=blade_colour + (solid,))
    hub = (inner + 2) * s
    pen.ellipse((cx * s - hub, cy * s - hub, cx * s + hub, cy * s + hub), fill=r.HUB + (255,),
                outline=r.TRACK + (255,), width=4 * s)
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB"))
    if picture is None:
        draw = ImageDraw.Draw(canvas)
        r.text(draw, CENTRE, 360, "Choose a picture", 22, r.MUTED, bold=True)
        r.text(draw, CENTRE, 390, "in the control panel", 18, r.MUTED)


GLITCH_SECONDS = 1.2
"""<summary>How long the glitch from one picture to the next lasts; the picture holds still the rest of its time.</summary>"""


def glitching(age: Optional[float]) -> bool:
    """<summary>Whether a picture this many seconds old is still glitching in.</summary>"""
    return age is not None and 0 <= age < GLITCH_SECONDS


def _glitch(old: Image.Image, new: Image.Image, progress: float, seed: str) -> Image.Image:
    """
    <summary>One frame of the glitch from old to new.</summary>
    <param name="old">The previous picture, canvas sized RGB.</param>
    <param name="new">The incoming picture, the same size.</param>
    <param name="progress">0 at the start of the glitch, 1 at its end.</param>
    <param name="seed">Names this frame, so the same moment always gives the same pixels.</param>
    <returns>The mixed frame.</returns>
    <remarks>
    The strength rises and falls over the glitch (a sine), worst in the middle where the
    picture underneath flips from old to new. On top: bands torn sideways, each taken from
    either picture; the red and blue channels pulled apart; and a few blocks of the other
    picture, inverted or posterised, the way a damaged video stream breaks up.
    </remarks>
    """
    rng = random.Random(seed)
    strength = math.sin(math.pi * progress)
    base = np.array(old if progress < 0.5 else new)
    other = np.array(new if progress < 0.5 else old)
    size = base.shape[0]
    s = r.SCALE
    for _ in range(int(4 + 16 * strength)):
        top = rng.randrange(0, size)
        height = rng.randrange(3 * s, 48 * s)
        shift = int(rng.uniform(-1, 1) * 110 * s * strength)
        source = other if rng.random() < 0.45 else base
        base[top:top + height] = np.roll(source[top:top + height], shift, axis=1)
    split = int(14 * s * strength)
    if split:
        base[:, :, 0] = np.roll(base[:, :, 0], split, axis=1)
        base[:, :, 2] = np.roll(base[:, :, 2], -split, axis=1)
    for _ in range(int(6 * strength)):
        w, h = rng.randrange(20 * s, 140 * s), rng.randrange(6 * s, 40 * s)
        x, y = rng.randrange(0, size - w), rng.randrange(0, size - h)
        block = other[y:y + h, x:x + w]
        base[y:y + h, x:x + w] = 255 - block if rng.random() < 0.5 else (block // 64) * 64
    return Image.fromarray(base, "RGB")


def draw_glitch(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    The slideshow folder's pictures in turn, each glitching in over the last: torn bands,
    split colour and broken blocks for GLITCH_SECONDS, then the picture holds still.
    </summary>
    <remarks>
    Uses the Slideshow screen's folder and time. ctx.background is the picture now,
    ctx.previous the one before and ctx.age how long this one has shown
    (screens.transition()). Frames come at the animation rate only while glitching; in
    between the service waits for the next change, and an unchanged frame is never sent.
    </remarks>
    """
    picture = _load_background(ctx.background)
    if picture is None:
        draw_picture(canvas, draw, stats, ctx)
        return
    old = _load_background(ctx.previous)
    if ctx.headline is None and old is not None and glitching(ctx.age):
        seed = "{}|{}|{}".format(ctx.background, ctx.previous, int(ctx.age / 0.05))
        canvas.paste(_glitch(old, picture, ctx.age / GLITCH_SECONDS, seed))
    else:
        canvas.paste(picture)
    if ctx.headline:
        r.text(draw, CENTRE, 380, ctx.headline, 30, r.TEXT)


CULT_TEETH = 24
CULT_TICK = 2.0
CULT_STEP = 5.0
"""
<summary>Teeth on the Machine Cult's outer cog, and how it turns: a step of CULT_STEP degrees every CULT_TICK seconds, like clockwork.</summary>
<remarks>
It turned smoothly until 1.0.34, but every frame then changed about 16 small patches round
the rim, and the SPACE answered by asking for a full frame about 69 times a minute, each a
0.34 s stall, so the whole screen jerked (measured 09/11/2026). A tick sends that burst only
once every CULT_TICK seconds. The step is a third of a tooth, under half the spacing, so it
always reads as turning forwards.
</remarks>
"""

CULT_RING = 188
CULT_DIGITS = 48
CULT_CURSOR = 0.2
"""<summary>The ring of binary inside the cog: its radius, how many digits, and the seconds the lit cursor spends on each.</summary>"""

CULT_PULSE = 2.4
"""<summary>Seconds for one slow pulse of the bionic eye.</summary>"""

_CULT_RANDOM = random.Random("machine cult")
_CULT_BITS = "".join(_CULT_RANDOM.choice("01") for _ in range(CULT_DIGITS))
"""<summary>The binary round the ring: fixed, from one seeded generator, so every process draws the same digits.</summary>"""


def _cog(draw: ImageDraw.ImageDraw, cx: float, cy: float, outer: float, inner: float, teeth: int,
         angle: float, colour: r.Colour) -> None:
    """
    <summary>A filled cog: a disc of radius inner with teeth out to outer, turned by angle, in screen pixels.</summary>
    <remarks>Each tooth is a trapezium, wider at its root, over 65 per cent of its spacing.</remarks>
    """
    s = r.SCALE
    points = []
    span = 360.0 / teeth
    for tooth in range(teeth):
        start = angle + tooth * span
        for share, radius in ((0.0, inner), (0.18, outer), (0.47, outer), (0.65, inner)):
            a = math.radians(start + share * span)
            points.append(((cx + radius * math.cos(a)) * s, (cy + radius * math.sin(a)) * s))
    draw.polygon(points, fill=colour)


def _skull(draw: ImageDraw.ImageDraw, cx: float, cy: float, ground: r.Colour, pulse: float) -> None:
    """
    <summary>A bone skull with one bionic eye and cables at the jaw, centred on (cx, cy).</summary>
    <param name="ground">The colour of the dark behind it, for the sockets and the gaps.</param>
    <param name="pulse">0 to 1, how bright the bionic eye glows now.</param>
    """
    s = r.SCALE
    bone, metal = r.BONE, r.COG
    shadow = _mix(bone, ground, 0.55)
    cable = _mix(metal, ground, 0.35)
    for side in (-1, 1):  # cables hanging from under the jaw, behind the skull, each ending in a plug
        for spread in (14, 30):
            x0 = cx + side * spread
            points = [(x0 * s, (cy + 50) * s), ((x0 + side * 4) * s, (cy + 78) * s),
                      ((x0 + side * 14) * s, (cy + 98) * s)]
            draw.line(points, fill=cable, width=6 * s, joint="curve")
            _ellipse(draw, x0 + side * 14, cy + 100, 5, 5, _mix(metal, ground, 0.15))
    _ellipse(draw, cx, cy - 14, 62, 58, bone)  # cranium
    draw.rounded_rectangle(((cx - 40) * s, (cy + 4) * s, (cx + 40) * s, (cy + 66) * s), radius=16 * s, fill=bone)
    _ellipse(draw, cx - 26, cy - 2, 18, 16, ground)  # the natural eye's socket
    _ellipse(draw, cx + 26, cy - 2, 21, 21, metal)  # the bionic eye: housing, rivets, lens, glow
    for rivet in range(4):
        a = math.radians(45 + rivet * 90)
        _ellipse(draw, cx + 26 + 16 * math.cos(a), cy - 2 + 16 * math.sin(a), 2.5, 2.5, _mix(metal, ground, 0.5))
    _ellipse(draw, cx + 26, cy - 2, 11, 11, _mix(ground, r.EYE, 0.45 + 0.55 * pulse))
    _ellipse(draw, cx + 23, cy - 5, 3, 3, _mix(r.EYE, (255, 255, 255), 0.4 + 0.4 * pulse))
    draw.polygon(((cx * s, (cy + 14) * s), ((cx - 9) * s, (cy + 32) * s), ((cx + 9) * s, (cy + 32) * s)),
                 fill=ground)  # nose
    draw.line(((cx - 34) * s, (cy + 44) * s, (cx + 34) * s, (cy + 44) * s), fill=shadow, width=3 * s)
    for tooth in range(-3, 4):  # teeth
        x = cx + tooth * 9.5
        draw.line((x * s, (cy + 36) * s, x * s, (cy + 60) * s), fill=shadow, width=2 * s)
    draw.line(((cx - 42) * s, (cy - 30) * s, (cx - 10) * s, (cy - 44) * s), fill=shadow, width=3 * s)  # a crack


CULT_PICTURE = Path(__file__).resolve().parent / "assets" / "machine-cult.webp"
"""
<summary>The emblem picture the Machine Cult shows, shipped inside the package.</summary>
<remarks>
Part of the code since 1.0.35 at Rob's request, so install.sh puts it on every PC with the
rest of the app (it copies the whole package). If it is missing or cannot be read, the
Machine Cult draws its own skull instead.
</remarks>
"""

CULT_EMBLEM = (240, 200, 136)
CULT_EMBLEM_SMALL = (240, 194, 152)
"""<summary>Where the emblem picture sits, as centre x, centre y and radius, on the full size and the small layout.</summary>"""

_cult_picture_cache: Dict[tuple, Tuple[Image.Image, Image.Image]] = {}


def _cult_picture(radius: int) -> Optional[Tuple[Image.Image, Image.Image]]:
    """
    <summary>The emblem picture cut to a circle, sized for a radius in screen pixels, cached until the file changes.</summary>
    <returns>The picture and its round mask, both in canvas pixels, or None when there is no readable file.</returns>
    <remarks>
    The circle is cut a little inside the picture's edge, so the black around a round emblem
    does not show as a ring against the screen's dark red ground.
    </remarks>
    """
    try:
        key = (str(CULT_PICTURE), CULT_PICTURE.stat().st_mtime, radius)
    except OSError:
        return None
    if key not in _cult_picture_cache:
        side = 2 * radius * r.SCALE
        try:
            with Image.open(CULT_PICTURE) as source:
                picture = ImageOps.fit(source.convert("RGB"), (side, side), Image.Resampling.LANCZOS)
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
        mask = Image.new("L", (side, side), 0)
        inset = round(side * 0.012)
        ImageDraw.Draw(mask).ellipse((inset, inset, side - 1 - inset, side - 1 - inset), fill=255)
        _remember(_cult_picture_cache, key, (picture, mask))
    return _cult_picture_cache[key]


def cult_angle(now: float) -> float:
    """<summary>Where the Machine Cult's outer cog has ticked round to at a moment, worked out from the clock like the Scanner.</summary>"""
    return (math.floor(now / CULT_TICK) * CULT_STEP) % 360.0


def _draw_cult(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context,
               small: bool, now: Optional[float] = None) -> None:
    """
    <summary>The Machine Cult, for either screen size; see draw_machine_cult().</summary>
    <param name="small">The Kraken's layout: no binary ring, and larger figures.</param>
    <remarks>Draws the picture at CULT_PICTURE in place of the skull when it can be read (_cult_picture()).</remarks>
    <param name="now">A monotonic time; the current one when None. Tests pass their own.</param>
    """
    stopped = ctx.headline is not None
    now = time.monotonic() if now is None else now
    ground = _mix(r.BACKGROUND, r.EYE, 0.09)
    s = r.SCALE
    _ellipse(draw, CENTRE, CENTRE, 240, 240, ground)
    _cog(draw, CENTRE, CENTRE, 236, 219, CULT_TEETH, 0.0 if stopped else cult_angle(now), r.COG)
    _ellipse(draw, CENTRE, CENTRE, 207, 207, ground)
    draw.ellipse(((CENTRE - 213) * s, (CENTRE - 213) * s, (CENTRE + 213) * s, (CENTRE + 213) * s),
                 outline=_mix(r.COG, ground, 0.5), width=2 * s)

    if not small:
        cursor = None if stopped else int(now / CULT_CURSOR) % CULT_DIGITS
        for i, bit in enumerate(_CULT_BITS):
            a = math.radians(-90 + i * 360 / CULT_DIGITS)
            behind = None if cursor is None else (cursor - i) % CULT_DIGITS
            glow = 1.0 if behind == 0 else (0.8 if behind == 1 else (0.62 if behind == 2 else 0.42))
            r.text(draw, round(CENTRE + CULT_RING * math.cos(a)), round(CENTRE + CULT_RING * math.sin(a)), bit, 14,
                   _mix(ground, r.BONE, glow), bold=True)

    ex, ey, radius = CULT_EMBLEM_SMALL if small else CULT_EMBLEM
    emblem = _cult_picture(radius)
    if emblem is not None:
        _ellipse(draw, ex, ey, radius + 5, radius + 5, _mix(r.COG, ground, 0.55))
        canvas.paste(emblem[0], ((ex - radius) * s, (ey - radius) * s), emblem[1])
    else:
        head_y = 186 if small else 176
        _cog(draw, CENTRE, head_y, 112, 98, 12, 7.5, _mix(r.COG, ground, 0.45))
        _ellipse(draw, CENTRE, head_y, 86, 86, ground)
        pulse = 0.5 if stopped else 0.5 + 0.5 * math.sin(2 * math.pi * now / CULT_PULSE)
        _skull(draw, CENTRE, head_y - 4, ground, pulse)

    label = _mix(ground, r.BONE, 0.65)

    def reading(temp: Optional[float], limits: Tuple[float, float]) -> r.Colour:
        """<summary>Bone while cool, so the default teal never jars with the red and brass; the warm and hot colours past the first limit.</summary>"""
        return r.BONE if temp is None or temp < limits[0] else r.temp_colour(temp, limits)

    cpu = reading(stats.cpu_temp, r.CPU_LIMITS)
    gpu = reading(stats.gpu_temp, r.GPU_LIMITS)
    if stopped:
        r.text(draw, CENTRE, 360 if emblem is None else 386, ctx.headline, 52 if small else 36, r.BONE, bold=True)
        return
    if emblem is not None:
        _emblem_readings(draw, stats, small, label, cpu, gpu)
        return
    if small:
        r.text(draw, 175, 326, "CPU", 28, label, bold=True)
        r.text(draw, 305, 326, "GPU", 28, label, bold=True)
        r.text(draw, 175, 378, r.number(stats.cpu_temp, "°"), 58, cpu, bold=True)
        r.text(draw, 305, 378, r.number(stats.gpu_temp, "°"), 58, gpu, bold=True)
        return
    r.text(draw, 185, 314, "CPU", 16, label, bold=True)
    r.text(draw, 295, 314, "GPU", 16, label, bold=True)
    r.text(draw, 185, 348, r.number(stats.cpu_temp, "°"), 36, cpu, bold=True)
    r.text(draw, 295, 348, r.number(stats.gpu_temp, "°"), 36, gpu, bold=True)
    known = stats.cpu_load is not None and math.isfinite(stats.cpu_load)
    load = format(max(0, min(255, round(stats.cpu_load))), "08b") if known else "--------"
    r.text(draw, CENTRE, 386, "LOAD " + load, 15, label, bold=True)


def _emblem_readings(draw: ImageDraw.ImageDraw, stats: Stats, small: bool, label: r.Colour,
                     cpu: r.Colour, gpu: r.Colour) -> None:
    """
    <summary>The Machine Cult's temperatures and load in the strip under the emblem picture.</summary>
    <remarks>Smaller and lower than over the drawn skull, so they stay clear of the picture and inside the binary ring.</remarks>
    """
    if small:
        r.text(draw, 180, 364, "CPU", 22, label, bold=True)
        r.text(draw, 300, 364, "GPU", 22, label, bold=True)
        r.text(draw, 180, 402, r.number(stats.cpu_temp, "°"), 48, cpu, bold=True)
        r.text(draw, 300, 402, r.number(stats.gpu_temp, "°"), 48, gpu, bold=True)
        return
    r.text(draw, 190, 350, "CPU", 14, label, bold=True)
    r.text(draw, 290, 350, "GPU", 14, label, bold=True)
    r.text(draw, 190, 374, r.number(stats.cpu_temp, "°"), 30, cpu, bold=True)
    r.text(draw, 290, 374, r.number(stats.gpu_temp, "°"), 30, gpu, bold=True)
    known = stats.cpu_load is not None and math.isfinite(stats.cpu_load)
    load = format(max(0, min(255, round(stats.cpu_load))), "08b") if known else "--------"
    r.text(draw, CENTRE, 400, "LOAD " + load, 13, label, bold=True)


def draw_machine_cult(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    A forge priest's cogitator: a bone skull with a pulsing bionic eye inside a cog, the
    great cog round the rim ticking round like clockwork, a ring of binary with a lit cursor running
    round it, and the temperatures and the CPU load (in binary) underneath.
    </summary>
    <remarks>
    In the manner of the Warhammer 40,000 tech priests but, like the Deckplate Grimdark
    icons, the drawn parts borrow the mood only. The emblem picture at CULT_PICTURE, which
    ships with the package, takes the skull's place, cut round and set inside the binary
    ring with the readings below it. Red and brass by default, from the
    palette's cog, bone and eye colours. Animated at the
    fan's 0.1 s and always streamed; what moves is kept small (tooth edges, one binary
    digit, the eye), so each frame is a modest set of tiles.
    </remarks>
    """
    _draw_cult(canvas, draw, stats, ctx, small=False)


def draw_machine_cult_small(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """<summary>The Machine Cult for a small screen: no binary ring or load line, and the two temperatures larger.</summary>"""
    _draw_cult(canvas, draw, stats, ctx, small=True)


ZOOM_FADE = 0.6
"""<summary>Seconds at the start of each picture in which the last picture, fully zoomed, fades into the new one at full view.</summary>"""

_zoom_levels: Dict[Tuple[str, float], Tuple[list, float, float]] = {}
_zoom_ends: Dict[tuple, Image.Image] = {}


def _zoom_source(path: Path) -> Optional[Tuple[list, float, float]]:
    """
    <summary>A picture ready to zoom into: its largest centred square at full size and halved copies, and where that square sits.</summary>
    <returns>(levels, left, top): levels[0] is the full size square, each next one half the last
    while still at least the canvas; left and top place the square in the original picture, in
    its pixels. None when the picture cannot be read.</returns>
    <remarks>
    Cached for the last few pictures. Every frame then crops from the smallest copy that is
    still sharp at that zoom, so a 12 megapixel photograph costs a few milliseconds a frame,
    not a full resize.
    </remarks>
    """
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    if key not in _zoom_levels:
        try:
            with Image.open(path) as source:
                picture = ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
        side = min(picture.size)
        left, top = (picture.width - side) / 2, (picture.height - side) / 2
        levels = [picture.crop((int(left), int(top), int(left) + side, int(top) + side))]
        while levels[-1].width // 2 >= r.CANVAS:
            levels.append(levels[-1].reduce(2))
        _zoom_levels[key] = (levels, left, top)
        while len(_zoom_levels) > 4:
            del _zoom_levels[next(iter(_zoom_levels))]
    return _zoom_levels[key]


def zoom_view(path: Path, point: Tuple[float, float], depth: float, progress: float) -> Optional[Image.Image]:
    """
    <summary>One canvas sized view of a picture zoomed towards a point.</summary>
    <param name="point">Where to zoom to, as fractions of the original picture's width and height.</param>
    <param name="depth">How many times the last view magnifies the first.</param>
    <param name="progress">0 for the whole (square) picture, 1 for fully zoomed.</param>
    <returns>The view with the picture boost applied, or None when the picture cannot be read.</returns>
    <remarks>
    The magnification grows exponentially with progress, so the zoom feels steady rather than
    rushing at the end. The view's centre moves from the picture's centre to the point in step
    with the magnification, so the point is centred when fully zoomed, and the view is kept
    inside the picture when the point is near an edge.
    </remarks>
    """
    source = _zoom_source(path)
    if source is None:
        return None
    levels, left, top = source
    side = levels[0].width
    full = r.CANVAS
    zoom = depth ** max(0.0, min(1.0, progress))
    window = side / zoom
    px = max(0.0, min(side, point[0] * (side + 2 * left) - left))
    py = max(0.0, min(side, point[1] * (side + 2 * top) - top))
    reach = (1 - 1 / zoom) / (1 - 1 / depth) if depth > 1 else 1.0
    cx = side / 2 + (px - side / 2) * reach
    cy = side / 2 + (py - side / 2) * reach
    cx = max(window / 2, min(side - window / 2, cx))
    cy = max(window / 2, min(side - window / 2, cy))
    level = 0
    while level + 1 < len(levels) and window / (2 ** (level + 1)) >= full:
        level += 1
    scale = 2 ** level
    box = ((cx - window / 2) / scale, (cy - window / 2) / scale, (cx + window / 2) / scale, (cy + window / 2) / scale)
    view = levels[level].resize((full, full), Image.Resampling.BICUBIC, box=box)
    return r.tune_picture(view)


def _zoom_end(path: Path, point: Tuple[float, float], depth: float) -> Optional[Image.Image]:
    """<summary>A picture fully zoomed, cached: the frame the next picture fades in from.</summary>"""
    key = (str(path), point, depth, r.PICTURE_TUNE)
    if key not in _zoom_ends:
        view = zoom_view(path, point, depth, 1.0)
        if view is None:
            return None
        _zoom_ends[key] = view
        while len(_zoom_ends) > 4:
            del _zoom_ends[next(iter(_zoom_ends))]
    return _zoom_ends[key]


def draw_zoom(canvas: Image.Image, draw: ImageDraw.ImageDraw, stats: Stats, ctx: r.Context) -> None:
    """
    <summary>
    Zooms steadily into the point chosen on a picture; once fully in, the next picture fades in
    at full view and the zoom starts again.
    </summary>
    <remarks>
    ctx.zoom (screens.zoom_state()) says which picture, where, how deep and how far through.
    Streamed, it runs at the animation rate; on the SPACE with stored video it plays as clips
    built by zoomvideo.py, and this drawing is what goes into them.
    </remarks>
    """
    state = ctx.zoom
    view = None if state is None else zoom_view(state.current, state.point, state.depth,
                                                state.age / state.seconds)
    if view is None:
        r.text(draw, CENTRE, 210, ctx.headline or "No zoom pictures yet", 26, r.MUTED)
        r.text(draw, CENTRE, 250, "Add them on the Zoom tab", 18, r.MUTED)
        return
    if state.previous is not None and state.age < ZOOM_FADE and ctx.headline is None:
        old = _zoom_end(state.previous, state.previous_point, state.depth)
        if old is not None:
            view = Image.blend(old, view, max(0.0, state.age) / ZOOM_FADE)
    canvas.paste(view)
    if ctx.headline:
        r.text(draw, CENTRE, 380, ctx.headline, 30, r.TEXT)


THEMES: Dict[str, Theme] = {
    "dial": Theme("Dial", "CPU temperature and load rings, with GPU and memory underneath.", draw_dial,
                  small=draw_dial_small),
    "twin": Theme("Twin gauges", "CPU on the top half, GPU on the bottom, each with temperature and load.", draw_twin,
                  small=draw_twin_small),
    "clock": Theme("Big clock", "A clock face with the date, and CPU and GPU temperatures.", draw_clock,
                   small=draw_clock_small),
    "minimal": Theme("Minimal", "Large CPU and GPU temperatures and a line of load figures.", draw_minimal,
                     small=draw_minimal_small),
    "image": Theme("Your picture", "A picture of your choice with CPU and GPU temperatures on top.", draw_image,
                   small=draw_image_small),
    "cooler": Theme("Cooler", "Coolant temperature, pump and fan speed, from coolers that report them.",
                    draw_cooler, needs_cooler=True),
    "fan": Theme("Fan", "Nothing but a big fan: faster with CPU load, blue to red as the CPU heats up.",
                 draw_fan, interval=0.1),
    "fan_reveal": Theme("Fan over a picture", "The big fan over a picture of yours, which shows through as it spins up.",
                        draw_fan_picture, interval=0.1),
    "hamster": Theme("Hamster", "A hamster running in a wheel, faster as the CPU heats up.", draw_hamster,
                     interval=0.1),
    "scanner": Theme("Scanner", "A motion tracker sweep: CPU and GPU as contacts that creep closer as they heat up.",
                     draw_scanner, interval=0.1, small=draw_scanner_small),
    "machine_cult": Theme("Machine Cult", "A skull with a bionic eye in a turning cog, binary round the rim, red and brass.",
                          draw_machine_cult, interval=0.1, small=draw_machine_cult_small),
    "network": Theme("Network", "Download and upload speed with a graph of recent minutes.", draw_network,
                     small=draw_network_small),
    "system": Theme("System", "CPU and GPU model, memory, disk, uptime and kernel.", draw_system,
                    small=draw_system_small),
    "slideshow": Theme("Slideshow", "Every picture in a folder in turn, each for the same time.", draw_picture,
                       small=draw_picture_small),
    "glitch": Theme("Glitch slideshow", "The slideshow folder's pictures, each glitching in over the last.",
                    draw_glitch, interval=0.1),
    "zoom": Theme("Zoom", "Zooms into a point you choose on each picture, then on to the next.", draw_zoom,
                  interval=0.1),
    "playlist": Theme("Playlist", "Up to five pictures, each for its own time.", draw_picture,
                      small=draw_picture_small),
    "smart": Theme("Smart picture", "Your game's artwork, then a picture per app, then one per temperature.",
                   draw_picture, small=draw_picture_small),
    "game": Theme("Game", "A Steam game's artwork.", draw_game, listed=False),
    "picture": Theme("Picture", "One picture, chosen by a rule.", draw_picture, listed=False,
                      small=draw_picture_small),
}
"""
<summary>
Every theme, keyed by the name stored in the settings file. Order here is the order the
control panel lists them in.
</summary>
"""
