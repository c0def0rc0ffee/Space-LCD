"""
<summary>
Reads and writes the user settings file, falling back to built in defaults for anything
missing.
</summary>
<remarks>
The file lives at ~/.config/space-lcd/config.ini. install.sh seeds it from
config.default.ini on first install and never overwrites it afterwards. Nothing in it is
secret; it is kept outside the project only because it is per machine.

The control panel writes it with save(), which edits values in place so the comments
survive. The background service notices the change by its modification time and applies
it without a restart; see changed().

Every parser here is built by _parser(): no value interpolation, so a picture path with a
"%" in it reads back as written. Keys are matched the way configparser matches them, case
ignored and with ":" as well as "=", so save() never adds a second copy of a key written
by hand as "Theme = twin". Section names are case sensitive, as in configparser. Values in a [DEFAULT] section still apply to every section,
as configparser defines; nothing here writes one.
</remarks>
"""

import configparser
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Dict, Optional, Tuple

CONFIG_PATH = Path.home() / ".config" / "space-lcd" / "config.ini"
"""<summary>Where the settings file is read from unless a path is passed in.</summary>"""

APP_HOME = Path.home() / ".local" / "share" / "space-lcd"
"""<summary>Install root: holds the venv, the app copy and the upstream driver checkout.</summary>"""

ROTATIONS = (0, 90, 180, 270)
"""<summary>The only rotations accepted: quarter turns.</summary>"""


@dataclass(frozen=True)
class Settings:
    """
    <summary>
    Every value the program reads from the settings file, already validated.
    </summary>
    <remarks>
    rotation turns the drawn picture before it is sent, so it can match however the pump
    head is mounted. It is applied by this program, not by the screen. background is only
    used by the "image" theme and is None until a picture has been chosen.
    refresh_until is the end of a burn-in refresh the control panel asked for, local time;
    it stays in the file after it passes and is simply ignored from then on.
    refresh_daily_at and refresh_daily_minutes are the daily schedule (None means none);
    refresh_skip_until ends tonight's scheduled run early without cancelling tomorrow's.
    refresh_brightness, when set, is used only while a refresh runs; brightness is untouched.
    cycle lists the screens to rotate through, each with its seconds; it is used only when
    cycle_on is set, and theme is the single screen otherwise.
    playlist holds up to five (picture, seconds) pairs from playlist_1 to playlist_5, in order.
    fan_mode "video" plays the fan from loops stored on the screen; "stream" sends frames.
    cooler is "auto" (find it by USB ID, devices.detect()) or a key of devices.DEVICES.
    The rules_ values drive the Smart picture screen (rules.py): Steam artwork on or off,
    up to five (process, picture) rules, two temperature edges with a picture per band,
    and the theme shown when nothing applies. palette holds the colours changed from the
    default, as (key, (r, g, b)) pairs sorted by key, from the [colours] section.
    fan_picture is the picture behind the blades of the Fan over a picture screen.
    picture_brightness and picture_contrast, in per cent, boost every picture a screen
    shows (render.PICTURE_TUNE). The zoom_ values drive the Zoom screen: up to five
    (picture, point) pairs, the point as fractions of the width and height, the seconds each
    zoom takes and how many times it magnifies.
    </remarks>
    """

    com_port: str = "AUTO"
    cooler: str = "auto"
    brightness: int = 40
    rotation: int = 0
    interval: float = 2.0
    theme: str = "dial"
    background: Optional[Path] = None
    pixel_shift: bool = True
    refresh_until: Optional[datetime] = None
    refresh_daily_at: Optional[time] = None
    refresh_daily_minutes: int = 60
    refresh_skip_until: Optional[datetime] = None
    refresh_brightness: Optional[int] = None
    cycle_on: bool = False
    cycle: Tuple[Tuple[str, int], ...] = ()
    slideshow_folder: Optional[Path] = None
    slideshow_seconds: int = 10
    playlist: Tuple[Tuple[Path, int], ...] = ()
    fan_mode: str = "video"
    rules_steam: bool = True
    rules_apps: Tuple[Tuple[str, Path], ...] = ()
    rules_temp_edges: Tuple[float, float] = (50.0, 70.0)
    rules_temp_pictures: Tuple[Optional[Path], Optional[Path], Optional[Path]] = (None, None, None)
    rules_fallback: str = "dial"
    palette: Tuple[Tuple[str, Tuple[int, int, int]], ...] = ()
    fan_picture: Optional[Path] = None
    picture_brightness: int = 100
    picture_contrast: int = 100
    zoom: Tuple[Tuple[Path, Tuple[float, float]], ...] = ()
    zoom_seconds: int = 8
    zoom_depth: int = 6
    upstream_dir: Path = APP_HOME / "upstream"

    def look(self) -> Dict[str, object]:
        """
        <summary>Everything drawn into a stored loop: the palette changes, and the rotation when it is not 0.</summary>
        <returns>Colour key to (r, g, b), plus "rotation" in degrees for a turned pump head.</returns>
        <remarks>
        render.apply_palette() ignores "rotation"; the loop makers draw with it and name
        their loops after it (fanvideo.palette_tag()). Leaving out 0 keeps every loop name
        what it was before the rotation counted, so loops already on the screen stay valid.
        </remarks>
        """
        look: Dict[str, object] = dict(self.palette)
        if self.rotation:
            look["rotation"] = self.rotation
        return look


def _path(value: str) -> Optional[Path]:
    """<summary>Turns a settings value into a path, with ~ expanded. Blank gives None.</summary>"""
    value = value.strip()
    return Path(os.path.expanduser(value)) if value else None


def _when(value: str) -> Optional[datetime]:
    """
    <summary>Reads an ISO local date and time such as 2026-10-10T21:45:00. Blank gives None.</summary>
    <exception cref="ValueError">The value is not an ISO date and time, or carries a time zone.</exception>
    <remarks>
    A zone offset ("+00:00") is refused here: everything that compares these times uses local
    wall clock time without a zone, and comparing the two kinds raises TypeError, which the
    service would hit on every frame instead of reporting the file once.
    </remarks>
    """
    value = value.strip()
    if not value:
        return None
    when = datetime.fromisoformat(value)
    if when.tzinfo is not None:
        raise ValueError("refresh times are local time without a zone, got {!r}".format(value))
    return when


def _clock(value: str) -> Optional[time]:
    """
    <summary>Reads a time of day such as 23:30. Blank gives None.</summary>
    <exception cref="ValueError">The value is not HH:MM, or carries a time zone.</exception>
    <remarks>A zone offset is refused for the reason given in _when(): burnin.py combines it with today's local date and compares the result with local time.</remarks>
    """
    value = value.strip()
    if not value:
        return None
    when = time.fromisoformat(value)
    if when.tzinfo is not None:
        raise ValueError("the daily refresh time is local time without a zone, got {!r}".format(value))
    return when


def _number(value: str) -> Optional[int]:
    """<summary>Reads a whole number. Blank gives None.</summary>"""
    value = value.strip()
    return int(value) if value else None


CYCLE_SECONDS = (5, 3600)
"""<summary>Shortest and longest time a screen may show for in the cycle.</summary>"""


def parse_cycle(value: str) -> Tuple[Tuple[str, int], ...]:
    """
    <summary>Reads the screen cycle, written as "dial 60, twin 30": theme and seconds, comma separated.</summary>
    <exception cref="ValueError">An entry is not a theme followed by a whole number of seconds.</exception>
    <remarks>Theme names and limits are checked in load(), which knows the theme list.</remarks>
    """
    entries = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        if len(pieces) != 2:
            raise ValueError("cycle entries are a theme and seconds, got {!r}".format(part))
        entries.append((pieces[0], int(pieces[1])))
    return tuple(entries)


def format_cycle(entries) -> str:
    """<summary>Writes the screen cycle back in the form parse_cycle() reads.</summary>"""
    return ", ".join("{} {}".format(key, seconds) for key, seconds in entries)


ZOOM_SLOTS = 5
ZOOM_SECONDS = (3, 20)
ZOOM_DEPTH = (2, 12)
"""
<summary>
The Zoom screen's limits: pictures in its list, seconds one zoom may take, and how many times
it may magnify. Twenty seconds of zoom is about the most that fits a stored clip's 2 MB.
</summary>
"""


def parse_zoom_entry(value: str) -> Optional[Tuple[Path, Tuple[float, float]]]:
    """
    <summary>Reads one zoom line, written "x, y | picture path" with x and y from 0 to 1. Blank gives None.</summary>
    <exception cref="ValueError">The point is missing, not two numbers, or outside 0 to 1, or there is no path.</exception>
    <remarks>The point comes first so a path containing "|" still reads correctly.</remarks>
    """
    value = value.strip()
    if not value:
        return None
    point, bar, path = value.partition("|")
    pieces = [piece.strip() for piece in point.split(",")]
    if not bar or len(pieces) != 2 or not path.strip():
        raise ValueError("zoom entries are 'x, y | picture', got {!r}".format(value))
    x, y = float(pieces[0]), float(pieces[1])
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("zoom points are 0 to 1 across and down, got {}, {}".format(x, y))
    return Path(os.path.expanduser(path.strip())), (x, y)


def format_zoom_entry(path: Optional[Path], point: Tuple[float, float]) -> str:
    """<summary>Writes one zoom line back in the form parse_zoom_entry() reads; blank without a picture.</summary>"""
    return "" if path is None else "{:.3f}, {:.3f} | {}".format(point[0], point[1], path)


def parse_playlist_entry(value: str) -> Optional[Tuple[Path, int]]:
    """
    <summary>Reads one playlist line, written "seconds | picture path". Blank gives None.</summary>
    <exception cref="ValueError">The seconds are missing or not a whole number, or there is no path.</exception>
    <remarks>Seconds come first so a path containing "|" or spaces still reads correctly.</remarks>
    """
    value = value.strip()
    if not value:
        return None
    seconds, separator, path = value.partition("|")
    if not separator or not path.strip():
        raise ValueError("playlist entries are 'seconds | picture', got {!r}".format(value))
    return Path(os.path.expanduser(path.strip())), int(seconds)


def format_playlist_entry(path: Optional[Path], seconds: int) -> str:
    """<summary>Writes one playlist line back in the form parse_playlist_entry() reads; blank without a picture.</summary>"""
    return "" if path is None else "{} | {}".format(seconds, path)


def _parser() -> configparser.ConfigParser:
    """<summary>A parser set up the one way this module reads files: no interpolation.</summary>"""
    return configparser.ConfigParser(interpolation=None)


def _read(path: Path) -> configparser.ConfigParser:
    """
    <summary>Reads a file into a fresh parser.</summary>
    <exception cref="configparser.Error">The file is not an ini file: a repeated section or key, a line outside any section.</exception>
    <remarks>A missing file reads as empty, which is how a first run gets the defaults.</remarks>
    """
    parser = _parser()
    parser.read(path, encoding="utf-8")
    return parser


def _read_quietly(path: Path) -> configparser.ConfigParser:
    """<summary>Like _read(), but a file that does not parse reads as empty, for the panel's slot views.</summary>"""
    try:
        return _read(path)
    except configparser.Error:
        return _parser()


def raw(key: str, path: Path = CONFIG_PATH, section: str = "display") -> str:
    """
    <summary>One value exactly as written in the file, or "" when absent or when the file does not parse.</summary>
    <remarks>For the control panel's numbered slots, whose positions the loaded Settings do not keep.</remarks>
    """
    return _read_quietly(path).get(section, key, fallback="").strip()


def playlist_slots(path: Path = CONFIG_PATH) -> list:
    """
    <summary>The five playlist slots by position, each (picture, seconds) or None when empty.</summary>
    <remarks>
    Settings.playlist leaves empty slots out, which is right for playing but loses where
    each picture sits; the control panel needs the positions. A slot that does not read
    is shown as empty rather than stopping the panel. This is looser than load(), on
    purpose: load() refuses the file so the service keeps its last good settings, while
    the panel still shows the slots that are fine.
    </remarks>
    """
    parser = _read_quietly(path)
    display = parser["display"] if parser.has_section("display") else {}
    slots = []
    for n in range(1, 6):
        try:
            slots.append(parse_playlist_entry(display.get("playlist_{}".format(n), "")))
        except ValueError:
            slots.append(None)
    return slots


def parse_colour(value: str) -> Tuple[int, int, int]:
    """
    <summary>Reads a colour written "#2dd4bf" (the # is optional).</summary>
    <exception cref="ValueError">Not six hex digits.</exception>
    """
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError("colours are six hex digits such as #2dd4bf, got {!r}".format(value))
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def format_colour(colour) -> str:
    """<summary>Writes a colour as "#rrggbb", the form parse_colour() reads.</summary>"""
    return "#{:02x}{:02x}{:02x}".format(*colour)


def _yes(value: str) -> bool:
    """
    <summary>Reads yes/no, true/false, on/off or 1/0, the way configparser does.</summary>
    <exception cref="ValueError">Any other value.</exception>
    """
    lowered = value.strip().lower()
    if lowered not in configparser.ConfigParser.BOOLEAN_STATES:
        raise ValueError("expected yes or no, got {}".format(value))
    return configparser.ConfigParser.BOOLEAN_STATES[lowered]


def load(path: Path = CONFIG_PATH) -> Settings:
    """
    <summary>
    Loads settings from an ini file. A missing file or missing keys give the defaults.
    </summary>
    <param name="path">The ini file to read.</param>
    <returns>The validated settings.</returns>
    <exception cref="ValueError">A value is present but out of range, such as a
    brightness of 150, a rotation that is not a quarter turn or an unknown theme.</exception>
    <exception cref="configparser.Error">The file is not an ini file at all: see _read().</exception>
    """
    from .themes import THEMES  # here, not at the top, so loading settings stays light
    from . import render, rules

    parser = _read(path)
    defaults = Settings()

    display = parser["display"] if parser.has_section("display") else {}
    driver = parser["driver"] if parser.has_section("driver") else {}
    colours = parser["colours"] if parser.has_section("colours") else {}

    settings = Settings(
        com_port=display.get("com_port", defaults.com_port).strip() or "AUTO",
        cooler=display.get("cooler", defaults.cooler).strip().lower() or defaults.cooler,
        brightness=int(display.get("brightness", defaults.brightness)),
        rotation=int(display.get("rotation", defaults.rotation)),
        interval=float(display.get("interval", defaults.interval)),
        theme=display.get("theme", defaults.theme).strip() or defaults.theme,
        background=_path(display.get("background", "")),
        pixel_shift=_yes(display.get("pixel_shift", "yes")),
        refresh_until=_when(display.get("refresh_until", "")),
        refresh_daily_at=_clock(display.get("refresh_daily_at", "")),
        refresh_daily_minutes=int(display.get("refresh_daily_minutes", defaults.refresh_daily_minutes)),
        refresh_skip_until=_when(display.get("refresh_skip_until", "")),
        refresh_brightness=_number(display.get("refresh_brightness", "")),
        cycle_on=_yes(display.get("cycle_on", "no")),
        cycle=parse_cycle(display.get("cycle", "")),
        fan_mode=display.get("fan_mode", defaults.fan_mode).strip() or defaults.fan_mode,
        rules_steam=_yes(display.get("rules_steam", "yes")),
        rules_apps=tuple(rule for rule in (rules.parse_app_rule(display.get("rules_app_{}".format(n), ""))
                                           for n in range(1, rules.APP_RULES + 1)) if rule is not None),
        rules_temp_edges=rules.parse_edges(display.get("rules_temp_edges", "50, 70")),
        rules_temp_pictures=tuple(_path(display.get("rules_temp_{}".format(n), ""))
                                  for n in range(1, rules.TEMP_BANDS + 1)),
        rules_fallback=display.get("rules_fallback", defaults.rules_fallback).strip() or defaults.rules_fallback,
        palette=tuple(sorted((key, parse_colour(value)) for key, value in colours.items() if value.strip())),
        fan_picture=_path(display.get("fan_picture", "")),
        picture_brightness=int(display.get("picture_brightness", defaults.picture_brightness)),
        picture_contrast=int(display.get("picture_contrast", defaults.picture_contrast)),
        zoom=tuple(entry for entry in (parse_zoom_entry(display.get("zoom_{}".format(n), ""))
                                       for n in range(1, ZOOM_SLOTS + 1)) if entry is not None),
        zoom_seconds=int(display.get("zoom_seconds", defaults.zoom_seconds)),
        zoom_depth=int(display.get("zoom_depth", defaults.zoom_depth)),
        slideshow_folder=_path(display.get("slideshow_folder", "")),
        slideshow_seconds=int(display.get("slideshow_seconds", defaults.slideshow_seconds)),
        playlist=tuple(entry for entry in (parse_playlist_entry(display.get("playlist_{}".format(n), ""))
                                           for n in range(1, 6)) if entry is not None),
        upstream_dir=_path(driver.get("upstream_dir", "")) or defaults.upstream_dir,
    )

    if not 0 <= settings.brightness <= 100:
        raise ValueError("brightness must be between 0 and 100, got {}".format(settings.brightness))
    if settings.rotation not in ROTATIONS:
        raise ValueError("rotation must be 0, 90, 180 or 270, got {}".format(settings.rotation))
    if not math.isfinite(settings.interval) or settings.interval < 0.5:
        raise ValueError("interval must be at least 0.5 seconds, got {}".format(settings.interval))
    if settings.refresh_brightness is not None and not 0 <= settings.refresh_brightness <= 100:
        raise ValueError("refresh_brightness must be between 0 and 100, got {}".format(settings.refresh_brightness))
    if not 1 <= settings.refresh_daily_minutes <= 1440:
        raise ValueError("refresh_daily_minutes must be 1 to 1440, got {}".format(settings.refresh_daily_minutes))
    for key, seconds in settings.cycle:
        if key not in THEMES or not THEMES[key].listed:
            raise ValueError("cycle names an unknown theme: {}".format(key))
        if not CYCLE_SECONDS[0] <= seconds <= CYCLE_SECONDS[1]:
            raise ValueError("cycle times must be {} to {} seconds, got {}".format(*CYCLE_SECONDS, seconds))
    from .devices import COOLER_CHOICES
    if settings.cooler not in COOLER_CHOICES:
        raise ValueError("cooler must be one of {}, got {}".format(", ".join(COOLER_CHOICES), settings.cooler))
    if settings.fan_mode not in ("video", "stream"):
        raise ValueError("fan_mode must be video or stream, got {}".format(settings.fan_mode))
    from .pictures import PICTURE_SECONDS
    for seconds in [settings.slideshow_seconds] + [s for _, s in settings.playlist]:
        if not PICTURE_SECONDS[0] <= seconds <= PICTURE_SECONDS[1]:
            raise ValueError("picture times must be {} to {} seconds, got {}".format(*PICTURE_SECONDS, seconds))
    for name in ("picture_brightness", "picture_contrast"):
        if not render.PICTURE_RANGE[0] <= getattr(settings, name) <= render.PICTURE_RANGE[1]:
            raise ValueError("{} must be {} to {}, got {}".format(name, *render.PICTURE_RANGE, getattr(settings, name)))
    if not ZOOM_SECONDS[0] <= settings.zoom_seconds <= ZOOM_SECONDS[1]:
        raise ValueError("zoom_seconds must be {} to {}, got {}".format(*ZOOM_SECONDS, settings.zoom_seconds))
    if not ZOOM_DEPTH[0] <= settings.zoom_depth <= ZOOM_DEPTH[1]:
        raise ValueError("zoom_depth must be {} to {}, got {}".format(*ZOOM_DEPTH, settings.zoom_depth))
    known = {key for key, _, _ in render.PALETTE}
    for key, _ in settings.palette:
        if key not in known:
            raise ValueError("unknown colour {!r} in [colours]".format(key))
    if settings.rules_fallback not in THEMES or not THEMES[settings.rules_fallback].listed \
            or settings.rules_fallback == "smart":
        raise ValueError("rules_fallback must be a screen other than smart, got {}".format(settings.rules_fallback))
    if settings.theme not in THEMES or not THEMES[settings.theme].listed:
        raise ValueError("theme must be one of {}, got {}".format(", ".join(THEMES), settings.theme))
    return settings


def try_load(path: Path = CONFIG_PATH) -> Tuple[Settings, Optional[str]]:
    """
    <summary>Loads the settings, or the defaults with a message when the file cannot be used.</summary>
    <returns>The settings and None, or the defaults and what was wrong with the file.</returns>
    <remarks>
    For the places that must keep going with something, the service at start, the loop
    makers in install.sh, the panel: a typo in the file is reported, never a crash.
    </remarks>
    """
    try:
        return load(path), None
    except (ValueError, configparser.Error) as error:
        return Settings(), str(error)


def save(values: Dict[str, object], path: Path = CONFIG_PATH, section: str = "display") -> None:
    """
    <summary>Writes the given keys into one section, keeping every comment and other line.</summary>
    <param name="values">Key to new value. None writes an empty value.</param>
    <param name="path">The ini file. Created, with the section, if it does not exist.</param>
    <param name="section">The section the keys belong to.</param>
    <remarks>
    A key already in the section is changed where it stands; a missing key is added at the
    end of the section. The file is written to a temporary name and renamed into place, so
    the service never reads half a file. Values are not validated here; load() does that.
    Existing keys are found the way configparser reads them (case ignored, "=" or ":"),
    so a hand written "Theme = twin" is replaced, not joined by a second "theme" line
    that would make the file unreadable. Section names are case sensitive in
    configparser, so they are matched exactly.
    </remarks>
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = {key.lower(): "" if value is None else str(value) for key, value in values.items()}

    start = next((i for i, line in enumerate(lines) if line.strip() == "[{}]".format(section)), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[{}]".format(section))
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))

    for i in range(start + 1, end):
        stripped = lines[i].strip()
        if not stripped or stripped[0] in "#;" or not any(d in stripped for d in "=:"):
            continue
        key = re.split(r"[=:]", stripped, maxsplit=1)[0].strip().lower()
        if key in pending:
            lines[i] = "{} = {}".format(key, pending.pop(key))

    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    for key, value in pending.items():
        lines.insert(insert_at, "{} = {}".format(key, value))
        insert_at += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def modified(path: Path = CONFIG_PATH) -> float:
    """<summary>The file's modification time, or 0 when it does not exist.</summary>"""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def changed(old: Settings, new: Settings) -> Dict[str, object]:
    """
    <summary>Lists which settings differ between two loads.</summary>
    <returns>Field name to new value, for the fields that changed only.</returns>
    """
    return {name: getattr(new, name) for name in old.__dataclass_fields__
            if getattr(old, name) != getattr(new, name)}

