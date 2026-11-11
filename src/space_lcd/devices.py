"""
<summary>
The coolers this program can drive, what each one's screen can do, and which one is in
this PC.
</summary>
<remarks>
Detection reads sysfs only and sends nothing to either cooler, so the control panel can
call it freely. With the cooler setting on auto, a Kraken 2023 found by its USB IDs wins;
otherwise it is the ID-COOLING SPACE, exactly as before the Kraken was added, so a PC with
only the SPACE behaves as 1.0.22 did. A PC with both is not supported: set cooler by hand.
</remarks>
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Device:
    """
    <summary>One cooler model's screen.</summary>
    <remarks>
    size is the frame width and height sent to it. stored_video says whether it can play
    loops from its own storage (the SPACE only). cooler_sensors says whether its pump,
    fan and coolant readings reach Linux, which is what the Cooler screen needs.
    animation_interval is the seconds between frames for animated screens (fan, hamster)
    when streamed to this cooler; None keeps each theme's own interval.
    </remarks>
    """

    key: str
    name: str
    size: int
    stored_video: bool
    cooler_sensors: bool
    animation_interval: Optional[float] = None


SPACE = Device("space", "ID-COOLING SPACE LCD", 480, stored_video=True, cooler_sensors=False)
KRAKEN_2023 = Device("kraken2023", "NZXT Kraken 2023", 240, stored_video=False, cooler_sensors=True,
                     animation_interval=0.06)
"""
<summary>The Kraken 2023's screen.</summary>
<remarks>
animation_interval 0.06 s, about 16 frames a second: a 240 frame takes 0.041 s to send
(JOURNAL 30/10/2026) and about 0.018 s to draw on this PC, so 0.06 s is as fast as it goes
without frames queueing. The SPACE's fan stays at the theme's 0.1 s.
</remarks>
"""

DEVICES: Dict[str, Device] = {d.key: d for d in (SPACE, KRAKEN_2023)}
"""<summary>Every supported cooler by the key the settings file stores. Keys never change once shipped.</summary>"""

COOLER_CHOICES = ("auto",) + tuple(DEVICES)
"""<summary>Values the cooler setting accepts.</summary>"""


def detect(choice: str = "auto") -> Device:
    """
    <summary>The cooler to drive: the one named, or on auto the one found in this PC.</summary>
    <param name="choice">A key of DEVICES, or "auto".</param>
    <remarks>Auto falls back to the SPACE when no Kraken is found, including on a PC with no cooler at all.</remarks>
    """
    if choice in DEVICES:
        return DEVICES[choice]
    from . import kraken
    return KRAKEN_2023 if kraken.present() else SPACE
