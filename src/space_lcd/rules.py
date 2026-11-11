"""
<summary>
Phase 5 of PLAN.md: the "Smart picture" screen, which picks what to show from what is
happening on the PC. Priority (agreed 10/10/2026, do not relitigate): a running Steam game,
then a running application with a picture of its own, then the CPU temperature band,
then a fallback screen.
</summary>
<remarks>
Everything is local. The running game is found from Steam's launcher process, which runs
every game as "reaper SteamLaunch AppId=<id> -- ..."; its artwork comes from Steam's own
cache on this PC. Nothing is downloaded and no Steam account is touched. Process names are
read by sensors.py once per reading, never logged.
</remarks>
"""

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config

APP_RULES = 5
"""<summary>How many application rules the settings hold.</summary>"""

TEMP_BANDS = 3
"""<summary>Temperature bands: below the first edge, between the edges, above the second.</summary>"""

STEAM_ROOTS: Sequence[Path] = (
    Path.home() / ".local" / "share" / "Steam",
    Path.home() / ".steam" / "steam",
    Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
)
"""<summary>Where Steam keeps its files: the normal install, its symlink, and the Flatpak build.</summary>"""

APP_ID = re.compile(r"^AppId=(\d+)$")

ART_NAMES = {
    "hero": ("library_hero.jpg",),
    "header": ("header.jpg", "library_header.jpg"),
    "cover": ("library_600x900.jpg", "library_capsule.jpg"),
    "logo": ("logo.png",),
}
"""
<summary>
The artwork the game picture uses, by role, with every name Steam has used for it. Older
caches keep these files directly in the game's folder; since 2025 Steam puts each one in
its own subfolder with a hashed name and calls some of them library_header and
library_capsule (seen for Forza Horizon 6, 11/10/2026). find_art() handles both.
</summary>
"""


def find_art(folder: Path) -> Dict[str, Path]:
    """
    <summary>The artwork files in a game's cache folder, by role (hero, header, cover, logo).</summary>
    <remarks>Looks in the folder itself first, then one level of subfolders; the first match per role wins.</remarks>
    """
    found: Dict[str, Path] = {}
    places = [folder]
    try:
        places += sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return found
    for role, names in ART_NAMES.items():
        for place in places:
            match = next((place / name for name in names if (place / name).is_file()), None)
            if match is not None:
                found[role] = match
                break
    return found


def steam_game(cmdlines: Iterable[Sequence[str]]) -> Optional[int]:
    """
    <summary>The Steam app id of a running game, from the command lines of running processes.</summary>
    <param name="cmdlines">Each process's arguments.</param>
    <returns>The first game id found, or None. AppId=0 (no game) is ignored.</returns>
    <remarks>Only command lines holding "SteamLaunch" count, so other programs mentioning AppId do not.</remarks>
    """
    for args in cmdlines:
        if "SteamLaunch" not in args:
            continue
        for arg in args:
            match = APP_ID.match(arg)
            if match and int(match.group(1)) > 0:
                return int(match.group(1))
    return None


STEAM_ENV = ("SteamAppId", "SteamGameId")
"""
<summary>
Environment variables Steam sets on every game it starts, whatever launcher or Proton
build runs it. Checked when the launcher command line does not show the id, as with the
newer containerised Steam runtime. Ids above 2**31 are Steam's own numbers for non Steam
shortcuts, which have no artwork in the cache.
</summary>
"""


def steam_game_from_env(environments: Iterable[dict]) -> Optional[int]:
    """
    <summary>The Steam app id of a running game, from the environments of running processes.</summary>
    <returns>The first plausible id (1 to 2**31), or None.</returns>
    """
    for env in environments:
        for key in STEAM_ENV:
            value = env.get(key, "")
            if value.isdigit() and 0 < int(value) < 2 ** 31:
                return int(value)
    return None


def artwork_folder(app_id: int, roots: Sequence[Path] = STEAM_ROOTS) -> Optional[Path]:
    """
    <summary>Steam's cached artwork folder for a game, or None when Steam has none on this PC.</summary>
    <remarks>Steam keeps one folder per game under appcache/librarycache; see find_art() for what is inside.</remarks>
    """
    for root in roots:
        folder = root / "appcache" / "librarycache" / str(app_id)
        if folder.is_dir() and find_art(folder):
            return folder
    return None


def resolve(settings: config.Settings, cpu_temp: Optional[float], running: Iterable[str],
            steam_app: Optional[int], roots: Sequence[Path] = STEAM_ROOTS) -> Tuple[str, Optional[Path], str]:
    """
    <summary>What the Smart picture screen shows now.</summary>
    <param name="settings">The settings in use.</param>
    <param name="cpu_temp">CPU temperature, or None.</param>
    <param name="running">Lowercase names of running processes.</param>
    <param name="steam_app">The running Steam game's id, or None.</param>
    <returns>
    (theme key to draw, picture or artwork folder, reason): "game" with Steam's artwork
    folder, "picture" with a chosen picture, or the fallback theme with no picture. The
    reason ("game 1091500", "app firefox", "temperature 2", "fallback") is for the panel.
    </returns>
    <remarks>A rule whose picture file is missing is skipped, so a moved picture falls through to the next rule.</remarks>
    """
    if settings.rules_steam and steam_app is not None:
        folder = artwork_folder(steam_app, roots)
        if folder is not None:
            return "game", folder, "game {}".format(steam_app)
    names = set(running)
    for process, picture in settings.rules_apps:
        if process.lower() in names and picture.is_file():
            return "picture", picture, "app {}".format(process)
    if cpu_temp is not None:
        low, high = settings.rules_temp_edges
        band = 0 if cpu_temp < low else (1 if cpu_temp < high else 2)
        pictures = settings.rules_temp_pictures
        picture = pictures[band] if band < len(pictures) else None  # a Settings built by hand may be short
        if picture is not None and picture.is_file():
            return "picture", picture, "temperature {}".format(band + 1)
    return settings.rules_fallback, None, "fallback"


def parse_app_rule(value: str) -> Optional[Tuple[str, Path]]:
    """
    <summary>Reads one application rule, written "process name | picture path". Blank gives None.</summary>
    <exception cref="ValueError">The process name or the picture is missing.</exception>
    """
    value = value.strip()
    if not value:
        return None
    process, separator, picture = value.partition("|")
    if not separator or not process.strip() or not picture.strip():
        raise ValueError("application rules are 'process | picture', got {!r}".format(value))
    return process.strip().lower(), Path(picture.strip()).expanduser()


def format_app_rule(process: str, picture: Optional[Path]) -> str:
    """<summary>Writes one application rule back; blank when either half is missing.</summary>"""
    return "" if not process.strip() or picture is None else "{} | {}".format(process.strip().lower(), picture)


def parse_edges(value: str) -> Tuple[float, float]:
    """
    <summary>Reads the two temperature band edges, written "50, 70".</summary>
    <exception cref="ValueError">Not two numbers, or the first is not below the second.</exception>
    """
    parts: List[float] = [float(part) for part in value.split(",") if part.strip()]
    if len(parts) != 2 or not parts[0] < parts[1]:
        raise ValueError("temperature edges are two rising numbers such as '50, 70', got {!r}".format(value))
    return parts[0], parts[1]


if __name__ == "__main__":
    # Diagnostic: python -m space_lcd.rules prints what the Smart picture can see now.
    from .sensors import read_processes
    names, game = read_processes()
    print("running programs seen: {}".format(len(names)))
    print("Steam game id: {}".format(game))
    folder = artwork_folder(game) if game else None
    print("artwork folder: {}".format(folder))
    for role, path in (find_art(folder) if folder else {}).items():
        print("  {}: {}".format(role, path.relative_to(folder)))
