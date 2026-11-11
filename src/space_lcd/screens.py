"""
<summary>
Chooses which screen (theme) is showing at a given moment, from the single chosen theme or
the screen cycle, and how often that screen needs a new frame.
</summary>
<remarks>
The cycle is worked out from the wall clock, not counted by the service, so the control
panel can show the same answer without asking the service, and a restart does not reset
the order. Phase 1 of PLAN.md. Burn-in refreshes sit above all of this; see __main__.py.
</remarks>
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from . import config, pictures, render, rules
from .themes import THEMES


def current(settings: config.Settings, now: datetime) -> Tuple[str, Optional[float]]:
    """
    <summary>The theme to show now and, when cycling, the seconds until the next screen.</summary>
    <param name="settings">The settings in use.</param>
    <param name="now">The current local time.</param>
    <returns>The theme key, and seconds left on it (None when not cycling).</returns>
    <remarks>
    An empty or switched off cycle falls back to the single theme. Entries are taken in the
    order written, each for its own seconds, starting from the epoch, so every process works
    out the same position.
    </remarks>
    """
    if not settings.cycle_on or not settings.cycle:
        return settings.theme, None
    total = sum(seconds for _, seconds in settings.cycle)
    position = now.timestamp() % total
    for key, seconds in settings.cycle:
        if position < seconds:
            return key, seconds - position
        position -= seconds
    return settings.cycle[-1][0], None  # unreachable with positive times; kept as a safe answer


def interval(settings: config.Settings, theme: str, animation: Optional[float] = None) -> float:
    """
    <summary>Seconds between frames for a theme: its own when it animates, else the settings' interval.</summary>
    <param name="animation">The cooler's own interval for animated themes (devices.Device.animation_interval); None keeps the theme's.</param>
    """
    own = THEMES[theme].interval if theme in THEMES else None
    if own is not None and animation is not None:
        return animation
    return own if own is not None else settings.interval


def picture(settings: config.Settings, theme: str, now: datetime) -> Tuple[Optional[Path], Optional[float]]:
    """
    <summary>The picture a theme shows now and the seconds until it changes.</summary>
    <returns>
    For the slideshow, the glitch slideshow and the playlist, the current picture and the
    time left on it; for the fan over a picture its picture and None; for every other theme
    the chosen picture (used by the "image" theme) and None.
    </returns>
    """
    if theme == "fan_reveal":
        return settings.fan_picture, None
    if theme == "zoom":
        return pictures.pick([(path, settings.zoom_seconds) for path, _ in settings.zoom], now)
    if theme in ("slideshow", "glitch"):
        return pictures.slideshow(settings.slideshow_folder, settings.slideshow_seconds, now)
    if theme == "playlist":
        return pictures.pick(settings.playlist, now)
    return settings.background, None


def zoom_state(settings: config.Settings, now: datetime) -> Optional[render.ZoomState]:
    """
    <summary>Where the Zoom screen is now, worked out from the wall clock like the slideshow.</summary>
    <returns>None with no zoom pictures. With one picture it fades from its own end into its start.</returns>
    """
    entries = settings.zoom
    if not entries:
        return None
    seconds = settings.zoom_seconds
    position = now.timestamp() % (seconds * len(entries))
    index = int(position // seconds)
    (current, point), (previous, previous_point) = entries[index], entries[index - 1]
    return render.ZoomState(current, point, previous, previous_point, float(settings.zoom_depth),
                            float(seconds), position - index * seconds)


def transition(settings: config.Settings, theme: str, now: datetime) -> Tuple[Optional[Path], Optional[float]]:
    """
    <summary>The picture before the current one and the seconds the current one has shown, for a theme that animates a change.</summary>
    <returns>For the glitch slideshow, pictures.slideshow_turn(); (None, None) for every other theme.</returns>
    """
    if theme != "glitch":
        return None, None
    return pictures.slideshow_turn(settings.slideshow_folder, settings.slideshow_seconds, now)


def effective(settings: config.Settings, stats, now: datetime) -> Tuple[str, Optional[Path], Optional[float], str]:
    """
    <summary>
    Everything about what is on screen now: the theme actually drawn, its picture, the
    seconds until something changes by the clock, and why (for the control panel).
    </summary>
    <param name="stats">The latest Stats; the Smart picture reads its temperature, running
    processes and Steam game.</param>
    <returns>(theme key to draw, picture or artwork folder, seconds to the next timed change
    or None, reason). The reason is the cycle's theme, or for the Smart picture the rule
    that matched.</returns>
    <remarks>
    The Smart picture's fallback can be any listed screen, including a slideshow, so the
    picture timing is worked out after the rules have chosen. Burn-in refreshes are not
    part of this; they sit above it in __main__.py.
    </remarks>
    """
    theme, cycle_left = current(settings, now)
    reason = theme
    if theme == "smart":
        theme, background, reason = rules.resolve(settings, stats.cpu_temp, stats.running, stats.steam_app)
        if theme in ("game", "picture"):
            return theme, background, cycle_left, reason
    background, picture_left = picture(settings, theme, now)
    waits = [w for w in (cycle_left, picture_left) if w is not None]
    return theme, background, (min(waits) if waits else None), reason
