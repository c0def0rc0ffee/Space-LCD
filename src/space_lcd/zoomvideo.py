"""
<summary>
The Zoom screen as stored video: one clip per picture, the last picture fading into this
one and then the zoom into its point, played from the screen's own storage at 24 frames a
second and switched at each change.
</summary>
<remarks>
Every zoom frame changes the whole screen, so streamed on the SPACE it would manage about
three frames a second (full frames at 0.34 s, measured 07/11/2026). Rob asked for video.

Each clip is the zoom (zoom_seconds) and then its last, fully zoomed frame held for
HOLD_SECONDS. The screen loops what it plays, so the hold is slack: the service switches
to the next clip at the clock's change, and a late switch shows the held frame, never the
zoom starting again. The next clip opens on that same frame, fading into the new picture,
so a switch on time is seamless.

All of a list's clips are made first, by a separate low priority process that only draws
and encodes on this PC, then uploaded together, once, through storage.py and its fences
(our names, 2 MB at most, never overwrite, never delete). Until then, and on a cooler
without stored video, the zoom is streamed. Playing only starts at a change, so the clip
and the clock agree. Clips are named after the two pictures and points, the speed, depth,
rotation and picture boost, so any change makes a new set; old ones stay on the screen.

While a new set is being made after a change of settings, the set already on the screen
goes on playing, and the new one takes over at a change of picture: streamed, the zoom only
managed 2.8 full frames a second, which Rob saw as jerky (07/11/2026). With no set on the
screen at all, the service holds the picture's opening view still instead of streaming.

A zoom changes every pixel every frame, which H.264 does not squeeze well, so each clip is
encoded at rising compression until it fits MAX_BYTES.
</remarks>
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set, Tuple

from . import config, screens
from .glitchvideo import _stamp
from .storage import MAX_UPLOAD, ScreenStorage, StorageError
from .videofan import FAILURES, LINK_FAILURES

log = logging.getLogger("space_lcd")

FPS = 24
HOLD_SECONDS = 3.0
"""<summary>The fully zoomed frame held at the end of each clip, so a late switch never shows the zoom restarting.</summary>"""

START_WINDOW = 1.0
"""<summary>Seconds after a change in which playing may start; later, the zoom streams until the next change, so clip and clock agree.</summary>"""

QUALITIES = (23, 26, 29, 32, 35)
"""<summary>The x264 quality settings tried in turn, best first, until a clip fits MAX_BYTES.</summary>"""

MAX_BYTES = int(MAX_UPLOAD * 0.95)
"""<summary>The largest clip made: under the storage fence's 2 MB with room to spare.</summary>"""

CLIP_VERSION = 1
"""<summary>Bump when the drawing of the clips changes, so cached and screen copies are replaced.</summary>"""

CACHE = config.APP_HOME / "zoom-clips"

Active = Tuple[Tuple[str, ...], int]
"""<summary>A set of clips on the screen: their names in playing order and the seconds each one is given.</summary>"""

Spec = dict
"""<summary>One clip as plain data, so it can go to the maker process as JSON: from, from_point, to, to_point, depth, seconds, rotation, tune.</summary>"""


def specs(settings: config.Settings) -> List[Spec]:
    """<summary>The clips of the settings' zoom list, in order: each from the picture before (the last, for the first) to its own.</summary>"""
    entries = settings.zoom
    out = []
    for index, (current, point) in enumerate(entries):
        previous, previous_point = entries[index - 1]
        out.append({"from": str(previous), "from_point": list(previous_point), "to": str(current),
                    "to_point": list(point), "depth": settings.zoom_depth, "seconds": settings.zoom_seconds,
                    "rotation": settings.rotation, "tune": [settings.picture_brightness, settings.picture_contrast]})
    return out


def clip_name(spec: Spec) -> str:
    """<summary>The file name of one clip, on this PC and on the screen: twelve hex characters of a hash of everything drawn into it.</summary>"""
    key = "{}@{:.3f},{:.3f}>{}@{:.3f},{:.3f}|d{}|s{}|r{}|t{}x{}".format(
        _stamp(Path(spec["from"])), *spec["from_point"], _stamp(Path(spec["to"])), *spec["to_point"],
        spec["depth"], spec["seconds"], spec["rotation"], *spec["tune"])
    return "spacelcd_zm{}_{}.mp4".format(CLIP_VERSION, hashlib.sha1(key.encode("utf-8")).hexdigest()[:12])


def make_clip(spec: Spec, out: Path) -> Path:
    """
    <summary>Draws one clip with the Zoom screen itself and encodes it with ffmpeg, as small as it must be.</summary>
    <exception cref="ValueError">Even the most compressed encode is over MAX_BYTES.</exception>
    <exception cref="subprocess.CalledProcessError">ffmpeg failed.</exception>
    <exception cref="FileNotFoundError">ffmpeg is not installed.</exception>
    <remarks>
    Frames are turned by fanvideo.SCREEN_TURN, as stored video must be. The held frame is
    drawn once and copied. Each encode is written inside the frames' temporary folder and
    only a clip that fits is moved to out, so a killed or doubled maker never leaves a short
    or oversized file to be uploaded, and nothing outside the temporary folder is removed.
    </remarks>
    """
    from . import fanvideo, render
    from .sensors import Stats

    render.apply_pictures(*spec["tune"])
    seconds = float(spec["seconds"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        part = Path(folder) / "clip.mp4"
        held: Optional[Path] = None
        for i in range(int(FPS * (seconds + HOLD_SECONDS))):
            target = Path(folder) / "f{:04d}.png".format(i)
            age = min(i / FPS, seconds)
            if age >= seconds and held is not None:
                shutil.copyfile(held, target)
                continue
            state = render.ZoomState(Path(spec["to"]), tuple(spec["to_point"]), Path(spec["from"]),
                                     tuple(spec["from_point"]), float(spec["depth"]), seconds, age)
            fanvideo.for_screen(render.draw_frame(Stats(), int(spec["rotation"]), theme="zoom", zoom=state)).save(target)
            if age >= seconds:
                held = target
        for quality in QUALITIES:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS),
                            "-i", str(Path(folder) / "f%04d.png"), "-c:v", "libx264", "-crf", str(quality),
                            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", "-f", "mp4", str(part)],
                           check=True)
            if part.stat().st_size <= MAX_BYTES:
                staged = out.with_name("{}.{}.part".format(out.name, os.getpid()))
                shutil.copyfile(part, staged)
                staged.replace(out)
                return out
        size = part.stat().st_size
    raise ValueError("zoom clip {} is {} bytes even at the most compression".format(out.name, size))


def spawn_maker(missing: Sequence[Spec], cache: Path) -> subprocess.Popen:
    """<summary>Starts the clip maker: this module run with -m in a separate, low priority process that never opens the screen.</summary>"""
    command = ["nice", "-n", "10", sys.executable, "-m", "space_lcd.zoomvideo", "--cache", str(cache),
               json.dumps(list(missing))]
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)


class ZoomVideo:
    """
    <summary>Plays the Zoom screen's clips from the screen's storage for one service session.</summary>
    <remarks>
    Mirrors glitchvideo.GlitchVideo: any failure marks the session failed and the zoom is
    streamed instead; dirty means a STOP or PLAY went out since the caller last sent a full
    frame. cache is where clips are made, spawn starts the maker (the tests pass a fake).
    </remarks>
    """

    def __init__(self, make_storage: Callable[[], ScreenStorage], cache: Path = CACHE,
                 spawn: Callable[[Sequence[Spec], Path], object] = spawn_maker) -> None:
        """<summary>Nothing is sent or started until prepare() or update().</summary>"""
        self._make_storage = make_storage
        self._cache = cache
        self._spawn = spawn
        self._maker = None
        self.ready: Set[str] = set()
        """<summary>Clip names known to be on the screen at the right size this session.</summary>"""
        self.active: Optional[Active] = None
        """<summary>The set of clips being played: the settings' own once it is all on the screen, until then the last one.</summary>"""
        self.playing: Optional[str] = None
        self.failed = False
        self.dirty = False
        self.stop_pending = False

    def _all_ready(self, settings: config.Settings) -> bool:
        """<summary>Whether every clip of the settings' list is on the screen.</summary>"""
        names = [clip_name(spec) for spec in specs(settings)]
        return bool(names) and all(name in self.ready for name in names)

    @staticmethod
    def _position(active: Active, now: datetime) -> Tuple[int, float]:
        """<summary>Which clip of a set the clock is on, and how many seconds into it.</summary>"""
        names, seconds = active
        position = now.timestamp() % (seconds * len(names))
        index = int(position // seconds)
        return index, position - index * seconds

    def _choose(self, settings: config.Settings, now: datetime) -> Optional[Active]:
        """
        <summary>The set to play: the settings' own once all of it is on the screen, taken up only at a change of picture; until then the set already playing.</summary>
        """
        own: Active = (tuple(clip_name(spec) for spec in specs(settings)), settings.zoom_seconds)
        if self._all_ready(settings) and (self.active in (None, own) or self.playing is None
                                          or self._position(own, now)[1] < START_WINDOW):
            self.active = own
        return self.active

    def holding(self, settings: config.Settings, theme: str, refreshing: bool) -> bool:
        """
        <summary>Whether the service should hold the zoom still rather than stream it: video is on and working, the clips are just not playing yet.</summary>
        """
        return settings.fan_mode == "video" and theme == "zoom" and not refreshing and not self.failed \
            and bool(settings.zoom)

    def wanted(self, settings: config.Settings, theme: str, refreshing: bool, now: datetime) -> bool:
        """
        <summary>Whether clips should be playing: a set is on the screen, and either one is playing or a change has just come.</summary>
        """
        if not self.holding(settings, theme, refreshing):
            return False
        active = self._choose(settings, now)
        return active is not None and (self.playing is not None or self._position(active, now)[1] < START_WINDOW)

    def left(self, settings: config.Settings, now: datetime) -> float:
        """<summary>Seconds until the next change of the set playing, when the next clip must start.</summary>"""
        active = self._choose(settings, now)
        if active is None:
            return 0.0
        return max(0.0, active[1] - self._position(active, now)[1])

    def screen_reopened(self) -> None:
        """<summary>Opening the screen sends STOP_VIDEO and STOP_MEDIA; clips stay on its storage.</summary>"""
        self.playing = None
        self.dirty = False
        self.stop_pending = False

    def _made(self, spec: Spec) -> Optional[Path]:
        """<summary>The finished clip on this PC, or None while it is not made yet.</summary>"""
        path = self._cache / clip_name(spec)
        return path if path.is_file() and path.stat().st_size > 0 else None

    def at_change(self, settings: config.Settings, now: datetime) -> bool:
        """<summary>Whether the set playing has just reached a change of picture: the moment new clips may be uploaded.</summary>"""
        active = self._choose(settings, now)
        return active is not None and self._position(active, now)[1] < START_WINDOW

    def prepare(self, settings: config.Settings, now: datetime, upload: bool = True) -> bool:
        """
        <summary>Gets the list's clips onto the screen: starts the maker for any not made, then uploads them all once every one is made.</summary>
        <param name="upload">False keeps to making: used while an older set plays, so its clip is never cut short
        except at a change of picture.</param>
        <returns>True when anything was sent, so the caller must send a full frame next.</returns>
        <remarks>
        The uploads go together, after one STOP, about 1.4 MB a second (07/11/2026), so a
        list of five takes a few seconds once. A failure marks the session failed and is
        logged; only a serial port error is raised, for the service's reconnect.
        </remarks>
        """
        if self.failed or settings.fan_mode != "video" or self._all_ready(settings):
            return False
        wanted = specs(settings)
        if not wanted:
            return False
        if self._maker is not None:
            code = self._maker.poll()
            if code is None:
                return False
            self._maker = None
            if code != 0:
                self.failed = True
                log.warning("zoom clips could not be made (exit %s), streaming instead", code)
                return False
        missing = [spec for spec in wanted if self._made(spec) is None]
        if missing:
            self._maker = self._spawn(missing, self._cache)
            return False
        if not upload:
            return False
        try:
            storage = self._make_storage()
            self.dirty = True
            if not storage.stop_media():
                raise StorageError("no media_stop before uploading the zoom clips")
            self.playing = None
            for spec in wanted:
                name = clip_name(spec)
                content = self._made(spec).read_bytes()
                size = storage.file_size(name)
                if size != len(content):
                    if size != 0:
                        raise StorageError("{} is {} bytes on the screen, expected {}".format(name, size, len(content)))
                    started = time.monotonic()
                    storage.upload(name, content)
                    log.info("uploaded zoom clip %s (%d bytes, %.1f s)", name, len(content), time.monotonic() - started)
                self.ready.add(name)
            self.dirty = False  # the caller sends the full frame now, on this True
        except LINK_FAILURES:
            raise  # the screen went away: the service reconnects
        except FAILURES as error:
            self.failed = True
            log.warning("zoom video unavailable this session, streaming instead: %s", error)
        return True

    def update(self, settings: config.Settings, now: datetime) -> None:
        """
        <summary>Plays the clip of the set's picture showing now, switching with STOP then PLAY at each change.</summary>
        <exception cref="StorageError">Or another FAILURES error: the caller marks the session failed.</exception>
        """
        active = self._choose(settings, now)
        if active is None:
            return
        name = active[0][self._position(active, now)[0]]
        if self.playing == name:
            return
        storage = self._make_storage()
        self.dirty = True
        if (self.playing is not None or self.stop_pending) and not storage.stop_media():
            raise StorageError("no media_stop when switching zoom clips")
        self.stop_pending = False
        if not storage.play(name):
            raise StorageError("no play_video_success for {}".format(name))
        log.info("zoom video %s", name)
        self.playing = name

    def leave(self) -> bool:
        """
        <summary>Stops a playing clip.</summary>
        <returns>True when the screen may be showing a stopped video, so the caller must send a full frame next.</returns>
        <remarks>Never raises, like VideoFan.leave(); a failed stop is sent again before the next play.</remarks>
        """
        if self.playing is None and not self.dirty:
            return False
        was_playing, self.dirty, self.playing = self.playing is not None, False, None
        if was_playing:
            try:
                if not self._make_storage().stop_media():
                    raise StorageError("no media_stop")
            except FAILURES as error:
                self.stop_pending = True
                log.warning("could not stop the zoom clip: %s", error)
        return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    <summary>The clip maker: makes each clip described in the JSON list given, then exits.</summary>
    <returns>0 when every clip was made or already there, 1 when one could not be.</returns>
    <remarks>Run by spawn_maker(), never by hand in normal use. Touches only this PC: never the screen.</remarks>
    """
    parser = argparse.ArgumentParser(description="Make the Zoom screen's clips.")
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("specs", help="a JSON list of clips, as zoomvideo.specs() writes them")
    args = parser.parse_args(argv)
    from . import render
    render.set_font_root(config.try_load()[0].upstream_dir)
    for spec in json.loads(args.specs):
        out = args.cache / clip_name(spec)
        if out.is_file() and out.stat().st_size > 0:
            continue
        try:
            make_clip(spec, out)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print("zoom clip {} not made: {}".format(out.name, error), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
