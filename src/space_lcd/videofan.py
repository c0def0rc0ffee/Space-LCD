"""
<summary>
Stage 4 of docs/device-video-plan.md: animated screens (the fan, the hamster) played as
smooth stored video. Keeps each screen's loops on the screen, plays the one that suits the
CPU, and gets out of the way cleanly when another screen or a burn-in refresh takes over.
</summary>
<remarks>
The service calls wanted() every loop. While it is true it calls update() instead of
sending frames; when it turns false it calls leave() and then sends a full frame, since
the screen was showing the video, not the last frame the panel remembers.

Switching needs STOP before PLAY (measured 10/10/2026: a PLAY on top of a playing video
answers success but changes nothing). Within one screen in one set of colours, loops change
at most every switch_seconds; a change of screen or of colours switches at once.

Any failure (no ffmpeg, an upload refused, a reply missing) marks the controller failed
for the rest of the session, and the service draws the animation as streamed frames
instead; the log says why. Nothing is retried in a loop against the hardware. A serial
port error is different: it is the screen going away, so the service's reconnect path
handles it (LINK_FAILURES), and stored video is tried again once the screen is back.

The screen keeps showing whatever a STOP left behind, so after any STOP or PLAY the
controller is "dirty" until leave() has told the caller to send a full frame. This holds
even when the upload after a STOP fails: the next frame must still be a full one.
</remarks>
"""

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Dict, Hashable, Optional, Sequence

import serial

from . import config, fanvideo, hamstervideo
from .sensors import Stats
from .storage import ScreenStorage, StorageError

log = logging.getLogger("space_lcd")

LINK_FAILURES = (serial.SerialException,)
"""<summary>The serial port failing: the screen was unplugged or reset. The caller reconnects rather than giving up on stored video.</summary>"""

FAILURES = (StorageError, OSError, ValueError, RuntimeError, subprocess.SubprocessError)
"""
<summary>Errors that switch the session back to streamed frames: the screen, the serial port, the fences, ffmpeg.</summary>
<remarks>LINK_FAILURES are a subset of OSError; callers that reconnect test for them first.</remarks>
"""


@dataclass(frozen=True)
class LoopSet:
    """
    <summary>One animated screen's stored loops: which exist, what each is called, how to make it, and which suits the readings.</summary>
    <remarks>
    keys are the loops (any hashable). name(key, tag) is the file name on the screen and
    on this PC, file(key, palette) the loop on this PC (made if needed), choose(stats,
    current) the loop for the readings given the one playing, tag(palette) names the colours
    a loop is drawn in. switch_seconds limits how often loops change.
    </remarks>
    """

    keys: Sequence[Hashable]
    name: Callable[[Hashable, str], str]
    file: Callable[[Hashable, dict], "object"]
    choose: Callable[[Stats, Optional[Hashable]], Hashable]
    tag: Callable[[dict], str]
    switch_seconds: float


LOOP_SETS: Dict[str, LoopSet] = {
    "fan": LoopSet(
        keys=[(s, c) for s in range(len(fanvideo.SPEEDS)) for c in range(len(fanvideo.COLOURS))],
        name=lambda key, tag: fanvideo.loop_name(key[0], key[1], tag),
        file=lambda key, palette: fanvideo.cached_loop(key[0], key[1], palette),
        choose=lambda stats, current: fanvideo.choose(stats.cpu_load, stats.cpu_temp, current),
        tag=fanvideo.palette_tag,
        switch_seconds=fanvideo.SWITCH_SECONDS),
    "hamster": LoopSet(
        keys=list(range(len(hamstervideo.SPEEDS))),
        name=hamstervideo.loop_name,
        file=hamstervideo.cached_loop,
        choose=lambda stats, current: hamstervideo.choose(stats.cpu_temp, current),
        tag=hamstervideo.palette_tag,
        switch_seconds=hamstervideo.SWITCH_SECONDS),
}
"""<summary>Every screen that can play as stored video, keyed by theme.</summary>"""


class VideoFan:
    """
    <summary>Drives the stored loops of the animated screens for one service session.</summary>
    <remarks>
    make_storage returns a ScreenStorage for the open screen; it is called when needed,
    since the screen can be reopened under a new port. loop_file, when given, replaces how
    fan loops are fetched (the tests pass a fake). sets defaults to LOOP_SETS. clock is a
    monotonic clock. The name is kept from when it only played the fan.
    </remarks>
    """

    def __init__(self, make_storage: Callable[[], ScreenStorage],
                 loop_file: Optional[Callable[..., "object"]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sets: Optional[Dict[str, LoopSet]] = None) -> None:
        """<summary>Nothing is sent until update().</summary>"""
        self._make_storage = make_storage
        self._clock = clock
        self._sets = dict(sets or LOOP_SETS)
        if loop_file is not None:
            fan = self._sets["fan"]
            self._sets["fan"] = LoopSet(fan.keys, fan.name, lambda key, palette: loop_file(key[0], key[1], palette),
                                        fan.choose, fan.tag, fan.switch_seconds)
        self.prepared: set = set()
        self.playing: Optional[Hashable] = None
        self.playing_theme: Optional[str] = None
        self.playing_tag: Optional[str] = None
        self.last_switch = float("-inf")
        self.failed = False
        self.dirty = False
        """<summary>A STOP or PLAY has been sent since the caller last drew a full frame.</summary>"""
        self.stop_pending = False
        """<summary>A STOP failed in leave(), so the next play must be preceded by one even though nothing is recorded as playing.</summary>"""

    def wanted(self, settings: config.Settings, theme: str, refreshing: bool) -> bool:
        """<summary>Whether the screen should be playing a stored animation now.</summary>"""
        return settings.fan_mode == "video" and theme in self._sets and not refreshing and not self.failed

    def _forget(self) -> None:
        """<summary>Records that nothing is playing.</summary>"""
        self.playing = None
        self.playing_theme = None
        self.playing_tag = None

    def screen_reopened(self) -> None:
        """
        <summary>Forgets what was playing: opening the screen sends STOP_VIDEO and STOP_MEDIA.</summary>
        <remarks>Files stay on the screen, so loops are not checked again this session.</remarks>
        """
        self._forget()
        self.last_switch = float("-inf")
        self.dirty = False
        self.stop_pending = False

    def _prepare(self, storage: ScreenStorage, loops: LoopSet, palette: dict, tag: str, theme: str) -> None:
        """
        <summary>Makes any missing loop of one set on this PC and uploads any missing one to the screen.</summary>
        <remarks>
        Stops any playing video first, as the vendor does before uploads. A loop on the
        screen with the wrong size is left alone and reported, never overwritten or deleted
        automatically: that would need a human look first. What was playing is forgotten
        only after the stop, and the controller stays dirty whatever happens next, so a
        failed upload still ends in a full frame from the caller.
        </remarks>
        """
        self.dirty = True
        if not storage.stop_media():
            raise StorageError("no media_stop before preparing the {} loops".format(theme))
        self._forget()
        for key in loops.keys:
            name = loops.name(key, tag)
            content = loops.file(key, palette).read_bytes()
            size = storage.file_size(name)
            if size == len(content):
                continue
            if size != 0:
                raise StorageError("{} is {} bytes on the screen, expected {}".format(name, size, len(content)))
            storage.upload(name, content)
            log.info("uploaded %s loop %s (%d bytes)", theme, name, len(content))
        self.prepared.add((theme, tag))

    def update(self, stats: Stats, palette: Optional[dict] = None, theme: str = "fan") -> None:
        """
        <summary>Prepares a screen's loops once, then plays the loop that suits the readings, switching when allowed.</summary>
        <exception cref="StorageError">Or another FAILURES error: the caller marks the session failed.</exception>
        """
        palette = palette or {}
        loops = self._sets[theme]
        tag = loops.tag(palette)
        storage = self._make_storage()
        if (theme, tag) not in self.prepared:
            self._prepare(storage, loops, palette, tag, theme)
        same = self.playing_theme == theme and self.playing_tag == tag
        current = self.playing if same else None
        wanted = loops.choose(stats, current)
        now = self._clock()
        if same and (wanted == current or now - self.last_switch < loops.switch_seconds):
            return
        name = loops.name(wanted, tag)
        self.dirty = True
        if (self.playing is not None or self.stop_pending) and not storage.stop_media():
            raise StorageError("no media_stop when switching loops")
        self.stop_pending = False
        if not storage.play(name):
            raise StorageError("no play_video_success for {}".format(name))
        log.info("%s video %s", theme, name)
        self.playing, self.playing_theme, self.playing_tag, self.last_switch = wanted, theme, tag, now

    def leave(self) -> bool:
        """
        <summary>Stops a playing stored animation.</summary>
        <returns>True when the screen may be showing a stopped video, so the caller must send a full frame next.</returns>
        <remarks>
        Never raises: a failure here is logged and the caller carries on with frames. A
        stop that fails is remembered, so the next play sends STOP first; this screen
        ignores a PLAY on top of a playing video. It is not retried here, since the
        caller asks every frame and a dead screen would be hammered.
        </remarks>
        """
        if self.playing is None and not self.dirty:
            return False
        was_playing, self.dirty = self.playing is not None, False
        self._forget()
        if was_playing:
            try:
                if not self._make_storage().stop_media():
                    raise StorageError("no media_stop")
            except FAILURES as error:
                self.stop_pending = True
                log.warning("could not stop the stored animation: %s", error)
        return True
