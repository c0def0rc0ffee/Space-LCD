"""
<summary>
Phase 0 of PLAN.md: measures how fast the screen can be fed, from the frames the service
sends anyway, and writes a summary to the journal once a minute.
</summary>
<remarks>
Nothing extra is sent to the screen for this. Each send is timed from the first byte to
the screen's status reply, so the figures include the round trip. Partial updates are
fitted to a straight line, seconds = overhead + area / speed, which is what the fan
animation in Phase 2 needs: the fixed cost per update and the pixels per second after it.
The fit needs partial updates of different sizes; until it has them it reports averages.
</remarks>
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

SUMMARY_SECONDS = 60
"""<summary>How often the summary is logged.</summary>"""

KEEP = 500
"""<summary>How many recent partial updates the fit uses.</summary>"""


@dataclass
class FrameTimer:
    """
    <summary>Collects send timings and produces the periodic summary line.</summary>
    <remarks>
    Call record() after each send with its kind, its area in pixels and how long it took.
    Call due() each loop; when it returns a line, log it. Counts and averages cover the
    period since the last summary; the partial update fit covers the last KEEP updates.
    </remarks>
    """

    full: List[float] = field(default_factory=list)
    partial: List[Tuple[int, float]] = field(default_factory=list)
    recent: List[Tuple[int, float]] = field(default_factory=list)
    resends: int = 0
    last_summary: float = field(default_factory=time.monotonic)

    def record(self, kind: str, area: int, seconds: float) -> None:
        """
        <summary>Stores one send.</summary>
        <param name="kind">"full" or "partial".</param>
        <param name="area">Pixels sent; a full frame is 480 times 480.</param>
        <param name="seconds">Time from first byte to status reply.</param>
        """
        if kind == "full":
            self.full.append(seconds)
        else:
            self.partial.append((area, seconds))
            self.recent.append((area, seconds))
            del self.recent[:-KEEP]

    def record_resend(self) -> None:
        """<summary>Counts one reply in which the screen asked for a full frame.</summary>"""
        self.resends += 1

    def fit(self) -> Optional[Tuple[float, float]]:
        """
        <summary>Least squares fit of partial update time against area.</summary>
        <returns>(overhead in seconds, pixels per second), or None until there are at least
        five updates covering a spread of sizes.</returns>
        """
        if len(self.recent) < 5:
            return None
        areas = [a for a, _ in self.recent]
        times = [s for _, s in self.recent]
        n = len(areas)
        mean_a, mean_t = sum(areas) / n, sum(times) / n
        spread = sum((a - mean_a) ** 2 for a in areas)
        if spread < (0.25 * mean_a) ** 2 * n or spread == 0:
            return None
        slope = sum((a - mean_a) * (t - mean_t) for a, t in zip(areas, times)) / spread
        if slope <= 0:
            return None
        return mean_t - slope * mean_a, 1.0 / slope

    def summary(self) -> str:
        """<summary>The summary line for the period so far.</summary>"""
        parts = []
        if self.full:
            average = sum(self.full) / len(self.full)
            parts.append("full frames {}, {:.2f} s each ({:.1f} a second)".format(
                len(self.full), average, 1 / average if average else 0))
        if self.partial:
            area = sum(a for a, _ in self.partial) / len(self.partial)
            seconds = sum(s for _, s in self.partial) / len(self.partial)
            parts.append("partial updates {}, {:.0f} px and {:.3f} s on average".format(
                len(self.partial), area, seconds))
        if self.resends:
            parts.append("screen asked for a full frame {} times".format(self.resends))
        fitted = self.fit()
        if fitted:
            parts.append("partial fit: {:.3f} s overhead + {:.0f} px a second".format(*fitted))
        return "timing: " + ("; ".join(parts) if parts else "nothing sent")

    def due(self, now: Optional[float] = None) -> Optional[str]:
        """
        <summary>Returns the summary line once every SUMMARY_SECONDS, and starts a new period.</summary>
        <param name="now">Monotonic time; the current time when None. Passed in by tests.</param>
        <returns>The line to log, or None when it is not yet time.</returns>
        """
        now = time.monotonic() if now is None else now
        if now - self.last_summary < SUMMARY_SECONDS:
            return None
        line = self.summary()
        self.full.clear()
        self.partial.clear()
        self.resends = 0
        self.last_summary = now
        return line
