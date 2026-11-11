"""
<summary>
Draws one 480 by 480 frame for the round screen: the shared drawing helpers, and
draw_frame(), which hands the drawing to the chosen theme in themes.py.
</summary>
<remarks>
The panel is circular, so every theme keeps its content inside a 240 pixel radius from the
centre and the corners are forced to black. Frames are drawn at twice the size and scaled
down so arcs and text have smooth edges. Drawing is deterministic: the same stats, theme
and settings always give the same pixels, which is what lets the panel code send only the
part that changed.
</remarks>
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Mapping, NamedTuple, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from .sensors import Stats

SIZE = 480
"""<summary>Panel width and height in pixels.</summary>"""

SCALE = 2
"""<summary>Supersampling factor used for smooth edges.</summary>"""

CANVAS = SIZE * SCALE
"""<summary>Width and height of the supersampled canvas themes draw on.</summary>"""

Colour = Tuple[int, int, int]

PALETTE: Sequence[Tuple[str, str, Colour]] = (
    ("background", "Background", (0, 0, 0)),
    ("track", "Empty rings and frames", (30, 37, 48)),
    ("text", "Text", (235, 238, 242)),
    ("muted", "Labels", (130, 140, 155)),
    ("cool", "Cool temperature", (45, 212, 191)),
    ("warm", "Warm temperature", (245, 158, 11)),
    ("hot", "Hot temperature", (239, 68, 68)),
    ("load", "CPU load", (96, 165, 250)),
    ("gpu", "GPU", (167, 139, 250)),
    ("download", "Download", (45, 212, 191)),
    ("upload", "Upload", (167, 139, 250)),
    ("fan_cold", "Fan when cool", (59, 130, 246)),
    ("fan_hot", "Fan when hot", (239, 68, 68)),
    ("hub", "Fan hub", (22, 27, 36)),
    ("hamster", "Hamster fur", (214, 150, 82)),
    ("wheel", "Hamster wheel", (148, 163, 184)),
    ("cog", "Machine Cult cogs", (181, 137, 58)),
    ("bone", "Machine Cult bone", (226, 214, 180)),
    ("eye", "Machine Cult eye", (230, 36, 30)),
)
"""
<summary>
Every colour the screens use: settings key, the name the control panel shows, and the
default. Each becomes a module attribute named after the key in capitals (COOL, FAN_HOT
and so on), which the themes read at draw time; apply_palette() changes them.
</summary>
"""

PRESETS: Dict[str, Dict[str, Colour]] = {
    "Default": {},
    "Ocean": {"cool": (56, 189, 248), "warm": (129, 140, 248), "hot": (244, 114, 182), "load": (34, 211, 238),
              "gpu": (99, 102, 241), "download": (56, 189, 248), "upload": (129, 140, 248),
              "fan_cold": (14, 165, 233), "fan_hot": (217, 70, 239), "track": (22, 34, 52)},
    "Sunset": {"cool": (251, 191, 36), "warm": (249, 115, 22), "hot": (225, 29, 72), "load": (251, 146, 60),
               "gpu": (244, 63, 94), "download": (251, 191, 36), "upload": (244, 63, 94),
               "fan_cold": (250, 204, 21), "fan_hot": (220, 38, 38), "track": (48, 30, 30)},
    "Neon": {"cool": (74, 222, 128), "warm": (250, 204, 21), "hot": (255, 0, 128), "load": (0, 255, 200),
             "gpu": (217, 70, 239), "download": (74, 222, 128), "upload": (217, 70, 239),
             "fan_cold": (0, 255, 170), "fan_hot": (255, 0, 128), "track": (25, 25, 35)},
    "Mono": {"cool": (230, 230, 230), "warm": (190, 190, 190), "hot": (255, 255, 255), "load": (170, 170, 170),
             "gpu": (200, 200, 200), "download": (230, 230, 230), "upload": (160, 160, 160),
             "fan_cold": (150, 150, 150), "fan_hot": (255, 255, 255), "muted": (120, 120, 120)},
    "Amber Console": {"background": (14, 9, 2), "track": (36, 26, 4), "text": (255, 184, 28),
                      "muted": (118, 85, 13), "cool": (255, 184, 28), "warm": (255, 136, 0),
                      "hot": (255, 72, 40), "load": (196, 141, 22), "gpu": (255, 206, 96),
                      "download": (255, 184, 28), "upload": (176, 127, 19), "fan_cold": (150, 108, 16),
                      "fan_hot": (255, 72, 40), "hub": (26, 17, 4), "hamster": (255, 184, 28),
                      "wheel": (118, 85, 13), "cog": (176, 127, 19), "bone": (255, 184, 28),
                      "eye": (255, 72, 40)},
    "Wrist Console": {"background": (5, 16, 9), "track": (13, 36, 18), "text": (96, 255, 128),
                      "muted": (44, 118, 59), "cool": (96, 255, 128), "warm": (210, 255, 110),
                      "hot": (255, 110, 80), "load": (74, 196, 98), "gpu": (150, 255, 170),
                      "download": (96, 255, 128), "upload": (66, 176, 88), "fan_cold": (56, 150, 75),
                      "fan_hot": (230, 255, 120), "hub": (8, 26, 13), "hamster": (96, 255, 128),
                      "wheel": (44, 118, 59), "cog": (66, 176, 88), "bone": (96, 255, 128),
                      "eye": (255, 110, 80)},
}
"""
<summary>Ready made palettes offered by the control panel: only the colours that differ from the default.</summary>
<remarks>
Amber Console and Wrist Console take the two phosphor consoles of the Deckplate icon themes:
one lit colour on its dark tube, the quieter roles as the same phosphor lit less hard, and a
warm red kept for hot, because an alarm drawn in the tube's own colour would not read as one.
Wrist Console's fan runs to a bright yellow green instead: blended with the red, its middle
temperatures came out beige.
</remarks>
"""

def apply_palette(colours: Mapping[str, Colour]) -> None:
    """
    <summary>Sets every palette colour: the given ones, and the default for any not given.</summary>
    <param name="colours">Settings key to colour; unknown keys (such as the "rotation" that
    config.Settings.look() adds for the loop makers) are ignored.</param>
    <remarks>
    Changes module attributes, so it applies to everything drawn afterwards in this
    process. The service and the control panel each call it with the settings in use.
    </remarks>
    """
    module = globals()
    for key, _, default in PALETTE:
        module[key.upper()] = tuple(colours.get(key, default))


BACKGROUND: Colour
TRACK: Colour
TEXT: Colour
MUTED: Colour
COOL: Colour
WARM: Colour
HOT: Colour
LOAD: Colour
GPU: Colour
DOWNLOAD: Colour
UPLOAD: Colour
FAN_COLD: Colour
FAN_HOT: Colour
HUB: Colour
HAMSTER: Colour
WHEEL: Colour
COG: Colour
BONE: Colour
EYE: Colour
apply_palette({})

PICTURE_TUNE: Tuple[int, int] = (100, 100)
"""
<summary>Brightness and contrast, in per cent, applied to every picture a screen loads; (100, 100) leaves pictures as they are.</summary>
<remarks>Set by apply_pictures() from the settings, like the palette, so the service, the panel and the clip makers agree.</remarks>
"""

PICTURE_RANGE = (50, 250)
"""<summary>Lowest and highest picture brightness and contrast, in per cent. Over 100 is the boost for a dim screen.</summary>"""


def apply_pictures(brightness: int = 100, contrast: int = 100) -> None:
    """<summary>Sets PICTURE_TUNE for everything drawn afterwards in this process.</summary>"""
    global PICTURE_TUNE
    PICTURE_TUNE = (int(brightness), int(contrast))


def tune_picture(picture: Image.Image) -> Image.Image:
    """
    <summary>A picture with PICTURE_TUNE applied: brightness first, then contrast.</summary>
    <returns>The same image when both are 100, so untouched pictures cost nothing and stay byte for byte as they were.</returns>
    """
    brightness, contrast = PICTURE_TUNE
    if brightness != 100:
        picture = ImageEnhance.Brightness(picture).enhance(brightness / 100.0)
    if contrast != 100:
        picture = ImageEnhance.Contrast(picture).enhance(contrast / 100.0)
    return picture


TEMP_MIN = 20.0
TEMP_MAX = 95.0
"""<summary>Temperature rings run from empty at TEMP_MIN to full at TEMP_MAX.</summary>"""

CPU_LIMITS = (60.0, 75.0)
GPU_LIMITS = (70.0, 83.0)
"""
<summary>
Where temperature colours turn amber and then red. The GPU limits are higher because a
graphics card under load runs hotter than a CPU and is designed to.
</summary>
"""

DEFAULT_THEME = "dial"
"""<summary>The theme used when the settings name none, or name one that no longer exists.</summary>"""

FONT_DIRS: Sequence[Path] = (
    Path("res/fonts/roboto"),
    Path("/usr/share/fonts/truetype/dejavu"),
)
"""
<summary>
Where fonts are looked for. The first entry is relative to the upstream driver checkout
and is filled in by set_font_root(); the second is the system DejaVu set, used when the
upstream checkout is not there.
</summary>
"""

_font_root: Optional[Path] = None
_font_cache: dict = {}


class ZoomState(NamedTuple):
    """
    <summary>Where the Zoom screen is: the picture and point zoomed into now, the one before, how deep, and how far through.</summary>
    <remarks>Points are fractions of the picture's width and height; age is the seconds since this picture came up.</remarks>
    """

    current: Path
    point: Tuple[float, float]
    previous: Optional[Path]
    previous_point: Tuple[float, float]
    depth: float
    seconds: float
    age: float


@dataclass
class Context:
    """
    <summary>Per frame options a theme may use beyond the stats themselves.</summary>
    <remarks>
    headline replaces the clock text where a theme shows one, for the "stopped" frame.
    background is the picture for the image theme; other themes ignore it. previous and
    age are for the glitch slideshow: the picture before this one, and the seconds since
    this one came up (screens.transition()); None when there is no change to show. zoom is
    the Zoom screen's state (screens.zoom_state()), None for every other screen.
    </remarks>
    """

    headline: Optional[str] = None
    background: Optional[Path] = None
    previous: Optional[Path] = None
    age: Optional[float] = None
    zoom: Optional[ZoomState] = None


def set_font_root(upstream_dir: Path) -> None:
    """
    <summary>Lets the renderer use the Roboto fonts bundled with the upstream driver.</summary>
    <param name="upstream_dir">The turing-smart-screen-python checkout.</param>
    <remarks>Clears the font cache, so call it before the first frame, not between frames.</remarks>
    """
    global _font_root
    _font_root = upstream_dir
    _font_cache.clear()


def font(size: int, bold: bool = False, light: bool = False) -> ImageFont.ImageFont:
    """
    <summary>Loads a font at a pixel size, preferring Roboto, then DejaVu Sans.</summary>
    <param name="size">Size in final screen pixels. Scaled up here for supersampling.</param>
    <param name="bold">Whether to use the bold weight.</param>
    <param name="light">Whether to use the thin weight. Roboto only; DejaVu falls back to regular.</param>
    <returns>A loaded font. Falls back to Pillow's built in font rather than failing.</returns>
    """
    key = (size, bold, light)
    if key in _font_cache:
        return _font_cache[key]
    roboto = "Roboto-Bold.ttf" if bold else ("Roboto-Light.ttf" if light else "Roboto-Regular.ttf")
    candidates = []
    if _font_root is not None:
        candidates.append(_font_root / FONT_DIRS[0] / roboto)
    candidates.append(FONT_DIRS[1] / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"))
    loaded: ImageFont.ImageFont = ImageFont.load_default(size * SCALE)
    for path in candidates:
        if path.is_file():
            loaded = ImageFont.truetype(str(path), size * SCALE)
            break
    _font_cache[key] = loaded
    return loaded


def temp_colour(temp: Optional[float], limits: Tuple[float, float] = CPU_LIMITS) -> Colour:
    """
    <summary>Picks the colour for a temperature.</summary>
    <param name="temp">Degrees Celsius, or None when unknown.</param>
    <param name="limits">Where it turns amber and where it turns red.</param>
    <returns>Teal, amber or red. Grey when unknown.</returns>
    """
    if temp is None:
        return MUTED
    if temp < limits[0]:
        return COOL
    if temp < limits[1]:
        return WARM
    return HOT


def fraction(value: Optional[float], low: float, high: float) -> float:
    """
    <summary>Maps a value onto 0 to 1 between low and high, clamped at both ends.</summary>
    <returns>0 when the value is None.</returns>
    """
    if value is None:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def arc(draw: ImageDraw.ImageDraw, radius: int, width: int, start: float, end: float,
        colour: Colour, centre: Tuple[int, int] = (SIZE // 2, SIZE // 2)) -> None:
    """
    <summary>Draws one arc, in final screen pixels.</summary>
    <param name="radius">Outer radius.</param>
    <param name="width">Thickness, measured inwards from the radius.</param>
    <param name="start">Start angle. Pillow angles: 0 is three o'clock, clockwise.</param>
    <param name="end">End angle. Nothing is drawn when end is not after start.</param>
    <param name="centre">Centre point; defaults to the middle of the screen.</param>
    """
    if end <= start:
        return
    cx, cy = centre[0] * SCALE, centre[1] * SCALE
    r = radius * SCALE
    draw.arc((cx - r, cy - r, cx + r, cy + r), start, end, fill=colour, width=width * SCALE)


def ring(draw: ImageDraw.ImageDraw, radius: int, width: int, start: float, sweep: float,
         filled: float, colour: Colour) -> None:
    """
    <summary>Draws a grey track and the filled part of it on top.</summary>
    <param name="start">Where the track begins, in Pillow angles.</param>
    <param name="sweep">How far the whole track runs clockwise, in degrees.</param>
    <param name="filled">How much of the sweep to fill, 0 to 1.</param>
    """
    arc(draw, radius, width, start, start + sweep, TRACK)
    arc(draw, radius, width, start, start + sweep * filled, colour)


def text(draw: ImageDraw.ImageDraw, x: int, y: int, value: str, size: int, colour: Colour,
         bold: bool = False, light: bool = False, anchor: str = "mm") -> None:
    """
    <summary>Draws text at a point given in final screen pixels.</summary>
    <param name="anchor">Pillow text anchor; the default centres on the point.</param>
    """
    draw.text((x * SCALE, y * SCALE), value, font=font(size, bold, light), fill=colour, anchor=anchor)


def number(value: Optional[float], suffix: str = "", digits: int = 0) -> str:
    """
    <summary>Formats a reading for display, or "--" when it is missing.</summary>
    """
    if value is None:
        return "--"
    return "{:.{}f}{}".format(value, digits, suffix)


@lru_cache(maxsize=1)
def _round_mask(size: int = SIZE) -> Image.Image:
    """
    <summary>A mask that is white inside the visible circle and black in the corners.</summary>
    <param name="size">Width and height of the mask: SIZE, or a smaller screen's frame size.</param>
    """
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def draw_frame(stats: Stats, rotation: int = 0, headline: Optional[str] = None,
               theme: str = DEFAULT_THEME, background: Optional[Path] = None,
               shift: Tuple[int, int] = (0, 0), size: int = SIZE,
               previous: Optional[Path] = None, age: Optional[float] = None,
               zoom: Optional[ZoomState] = None) -> Image.Image:
    """
    <summary>Draws the full screen for one snapshot in the chosen theme.</summary>
    <param name="stats">The readings to show. Missing readings show as "--".</param>
    <param name="rotation">Clockwise quarter turns in degrees, to match how the pump head
    is mounted.</param>
    <param name="headline">Text in place of the clock, such as "stopped".</param>
    <param name="theme">A key of themes.THEMES. An unknown key falls back to DEFAULT_THEME.</param>
    <param name="background">Picture for the image theme.</param>
    <param name="shift">Anti burn-in offset in screen pixels, applied before rotation; see burnin.py.</param>
    <param name="size">The cooler's screen size (devices.Device.size). Below SIZE the theme's
    small layout is drawn, when it has one, and the frame is scaled down to this size.</param>
    <param name="previous">The picture shown before background, for the glitch slideshow.</param>
    <param name="age">Seconds since background came up, for the glitch slideshow.</param>
    <param name="zoom">The Zoom screen's state, for that screen only.</param>
    <returns>An RGB image exactly size by size, black outside the circle.</returns>
    <remarks>
    Every theme draws in the same SIZE pixel coordinates whatever the screen; a small layout
    only uses fewer, larger items so they can still be read once scaled down. At SIZE the
    result is exactly what it was before size existed: the SPACE's frames do not change.
    </remarks>
    """
    from .themes import THEMES  # here rather than at the top: themes imports this module

    chosen = THEMES.get(theme) or THEMES[DEFAULT_THEME]
    draw = chosen.small if size < SIZE and chosen.small is not None else chosen.draw
    canvas = Image.new("RGB", (CANVAS, CANVAS), BACKGROUND)
    draw(canvas, ImageDraw.Draw(canvas), stats, Context(headline, background, previous, age, zoom))

    frame = canvas.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    if shift != (0, 0):
        moved = Image.new("RGB", (SIZE, SIZE), BACKGROUND)
        moved.paste(frame, shift)
        frame = moved
    if rotation:
        frame = frame.rotate(-rotation)
    black = Image.new("RGB", (SIZE, SIZE), BACKGROUND)
    frame = Image.composite(frame, black, _round_mask())
    if size != SIZE:
        frame = frame.resize((size, size), Image.Resampling.LANCZOS)
    return frame
