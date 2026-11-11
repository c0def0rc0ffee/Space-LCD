"""
<summary>
Anti burn-in: the colour cycle that treats image retention, and the slow pixel shift that
helps prevent it.
</summary>
<remarks>
The screen in this cooler showed the stock Windows picture for long enough to leave a
visible ghost of it. On an LCD that is image retention: it usually fades when the pixels
are driven through full white and saturated colours for a while, but long standing
retention can be partly permanent, and nothing here can promise to remove it.

Both features work from the clock rather than from counters, so the service and the
control panel agree on what is happening without talking to each other: the panel writes
an end time to the settings file, and the service shows the cycle until that time passes.
Every refresh frame changes the whole screen, so each one goes to the screen as a full
frame, the path already proven on this hardware; no new screen commands are involved.
</remarks>
"""

import random
from datetime import datetime, time, timedelta
from typing import Optional, Sequence, Tuple, Union

from PIL import Image

from .render import SIZE, Colour

NOISE = "noise"
"""<summary>Marks a step in REFRESH_SEQUENCE that shows random coloured pixels instead of a solid colour.</summary>"""

REFRESH_SEQUENCE: Sequence[Union[Colour, str]] = (
    (255, 255, 255),
    (0, 0, 0),
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 255),
    NOISE,
    NOISE,
    (255, 255, 255),
)
"""
<summary>
The steps the refresh cycles through, in order. Full white appears most often because it is
the part that does most of the work on LCD retention; black between colours makes every
pixel swing through its full range; the noise steps drive each pixel independently of its
neighbours, the approach of the well known screen fixing tools, which reaches the edges of
the retained shapes that whole field colours treat evenly.
</summary>
"""

REFRESH_STEP = 2.0
"""<summary>Seconds each refresh colour stays up. A full frame takes about a second to send.</summary>"""

REFRESH_CHOICES: Sequence[Tuple[str, int]] = (
    ("15 minutes", 15),
    ("1 hour", 60),
    ("4 hours", 240),
    ("8 hours (overnight)", 480),
    ("12 hours", 720),
    ("24 hours", 1440),
)
"""
<summary>Durations offered by the control panel, as label and minutes.</summary>
<remarks>24 hours is the most the daily schedule can hold (config caps refresh_daily_minutes at 1440); a longer run by hand is just Clear burn-in again when it ends.</remarks>
"""

SHIFT_MINUTES = 5
"""<summary>How long the picture stays at each shifted position.</summary>"""

SHIFT_ORBIT: Sequence[Tuple[int, int]] = (
    (0, 0), (2, 0), (2, 2), (0, 2), (-2, 2), (-2, 0), (-2, -2), (0, -2),
)
"""
<summary>
The positions the picture moves through, in screen pixels. Two pixels is enough to move
every hard edge off the pixels it was on, and small enough not to be noticed; the rings sit
at least 14 pixels inside the edge of the circle, so nothing is clipped.
</summary>
"""


def refreshing(until: Optional[datetime], now: datetime) -> bool:
    """
    <summary>Whether a refresh started by hand should be on screen at this moment.</summary>
    <param name="until">The end time from the settings file, or None when no refresh was asked for.</param>
    <param name="now">The current local time.</param>
    """
    return until is not None and now < until


def scheduled_until(daily_at: Optional[time], minutes: int, now: datetime) -> Optional[datetime]:
    """
    <summary>The end of today's (or last night's) scheduled refresh, if this moment is inside it.</summary>
    <param name="daily_at">The time of day the schedule starts, or None when no schedule is set.</param>
    <param name="minutes">How long each scheduled refresh lasts.</param>
    <param name="now">The current local time.</param>
    <returns>When the running scheduled refresh ends, or None outside the window.</returns>
    <remarks>
    Yesterday's start is checked too, so a window that crosses midnight (23:30 for eight
    hours) is still running at 03:00. Clock changes are not special cased: the window is
    worked out from the wall clock each time it is asked.
    </remarks>
    """
    if daily_at is None or minutes <= 0:
        return None
    for days_back in (0, 1):
        start = datetime.combine(now.date() - timedelta(days=days_back), daily_at)
        end = start + timedelta(minutes=minutes)
        if start <= now < end:
            return end
    return None


def next_scheduled(daily_at: Optional[time], now: datetime) -> Optional[datetime]:
    """<summary>The next time the scheduled refresh will start, for the control panel to show.</summary>"""
    if daily_at is None:
        return None
    start = datetime.combine(now.date(), daily_at)
    return start if start > now else start + timedelta(days=1)


def active(until: Optional[datetime], daily_at: Optional[time], minutes: int,
           skip_until: Optional[datetime], now: datetime) -> bool:
    """
    <summary>Whether any refresh, by hand or scheduled, should be on screen now.</summary>
    <param name="skip_until">Set when Stop is pressed during a scheduled refresh: the
    schedule is ignored until then, so tonight's run ends early and tomorrow's still happens.</param>
    <remarks>A refresh started by hand is never skipped; Stop clears it directly.</remarks>
    """
    if refreshing(until, now):
        return True
    if skip_until is not None and now < skip_until:
        return False
    return scheduled_until(daily_at, minutes, now) is not None


def refresh_frame(now: datetime) -> Image.Image:
    """
    <summary>The refresh frame for this moment: a solid colour or random noise filling the screen.</summary>
    <param name="now">The current local time; the frame steps every REFRESH_STEP seconds.</param>
    <returns>An RGB image SIZE by SIZE.</returns>
    <remarks>
    Rotation and the round mask are not applied: these frames look the same either way.
    Noise is seeded from the step number, so the same moment always gives the same frame
    (the tests rely on that) while consecutive noise steps still differ.
    </remarks>
    """
    tick = int(now.timestamp() // REFRESH_STEP)
    step = REFRESH_SEQUENCE[tick % len(REFRESH_SEQUENCE)]
    if step == NOISE:
        return Image.frombytes("RGB", (SIZE, SIZE), random.Random(tick).randbytes(SIZE * SIZE * 3))
    return Image.new("RGB", (SIZE, SIZE), step)


def shift_for(now: datetime) -> Tuple[int, int]:
    """
    <summary>The pixel shift for this moment, moving one step round SHIFT_ORBIT every SHIFT_MINUTES.</summary>
    <param name="now">The current local time.</param>
    <returns>(dx, dy) in screen pixels.</returns>
    <remarks>
    Each step changes most of the screen, so the panel sends one full frame every
    SHIFT_MINUTES; normal updates in between are unaffected.
    </remarks>
    """
    step = int(now.timestamp() // (SHIFT_MINUTES * 60)) % len(SHIFT_ORBIT)
    return SHIFT_ORBIT[step]
