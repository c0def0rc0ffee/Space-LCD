"""
<summary>
The Glitch slideshow as stored video: each change from one picture to the next is a short
clip played from the screen's own storage at 24 frames a second, then the new picture holds
as an ordinary frame until the next change.
</summary>
<remarks>
Streamed, a glitch managed three or four frames: nearly every pixel changes, so each goes
as a full frame, 0.34 s each on the SPACE (measured 07/11/2026). Rob asked for video.

Each clip is GLITCH_SECONDS of glitch and then the new picture held still, CLIP_SECONDS in
all. The screen loops what it plays (the play once flag has never been tried on this
screen, so it is not used), so the service sends STOP once WINDOW_SECONDS have passed,
inside the hold and well before the clip could come round again. STOP leaves the held
picture on the screen and the service then sends the same picture as a full frame.

Clips are drawn with the glitch theme itself (themes.draw_glitch) by a separate, low
priority process (this module run with -m), because one clip of two photographs took 6 s
to make (07/11/2026) and the service must keep the screen going meanwhile. That process
only draws and encodes on this PC; it never opens the screen. Clips are cached under CACHE
and named after the two pictures (path, size and modification time), the rotation and
CLIP_VERSION, so a changed picture gets a new clip.

The service uploads a finished clip while a picture holds, with PREFETCH_MARGIN seconds to
spare before the change, through storage.py and its fences: our names only, 2 MB at most,
never overwrite, never delete. A change whose clip is not on the screen yet is streamed as
before, so the first time through a new folder some changes stream. Old clips stay on the
screen's storage, as old fan loops do.
</remarks>
"""

import argparse
import hashlib
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

from . import config, pictures, screens
from .storage import ScreenStorage, StorageError
from .videofan import FAILURES, LINK_FAILURES

log = logging.getLogger("space_lcd")

FPS = 24
CLIP_SECONDS = 3.0
"""<summary>Clip length: the glitch, then the new picture held still for the rest.</summary>"""

WINDOW_SECONDS = 1.7
"""
<summary>
Seconds after a change during which the clip is the screen's business; after this the
service stops it and holds the picture as a frame. Past the glitch (themes.GLITCH_SECONDS,
1.2) and 1.3 s short of the clip's end, so a late STOP still lands in the hold.
</summary>
"""

PREFETCH_MARGIN = 5.0
"""
<summary>
Fewest seconds that must be left on a picture before a finished clip is uploaded during its
hold. Fan loops of about 200 KB went up about every 2 s on 07/11/2026 (making included);
glitch clips of photographs are nearer 400 KB. With less time left the upload waits.
</summary>
"""

CLIP_VERSION = 2
"""<summary>Bump when the drawing of the clips changes, so cached and screen copies are replaced. 2 from 1.0.29: turned upright.</summary>"""

CACHE = config.APP_HOME / "glitch-clips"

Pair = Tuple[Path, Path]
Tune = Tuple[int, int]
"""<summary>Picture brightness and contrast in per cent, as render.PICTURE_TUNE.</summary>"""


def _stamp(path: Path) -> str:
    """<summary>A picture's identity for clip names: its path, size and modification time.</summary>"""
    try:
        info = path.stat()
        return "{}|{}|{}".format(path, info.st_size, int(info.st_mtime))
    except OSError:
        return str(path)


def clip_name(previous: Path, current: Path, rotation: int = 0, tune: Tune = (100, 100)) -> str:
    """
    <summary>The file name of the clip from previous to current, on this PC and on the screen.</summary>
    <remarks>
    Twelve hex characters of a hash, so the name passes the storage fence (spacelcd_, lower
    case, short). The picture boost counts only when it is not (100, 100), so clips made
    before the boost existed keep their names and stay valid on the screen.
    </remarks>
    """
    key = "{}>{}|r{}".format(_stamp(previous), _stamp(current), rotation)
    if tuple(tune) != (100, 100):
        key += "|t{}x{}".format(*tune)
    return "spacelcd_gl{}_{}.mp4".format(CLIP_VERSION, hashlib.sha1(key.encode("utf-8")).hexdigest()[:12])


def make_clip(previous: Path, current: Path, out: Path, rotation: int = 0, tune: Tune = (100, 100)) -> Path:
    """
    <summary>Draws one clip with the glitch theme and encodes it with ffmpeg to out.</summary>
    <exception cref="subprocess.CalledProcessError">ffmpeg failed.</exception>
    <exception cref="FileNotFoundError">ffmpeg is not installed.</exception>
    <remarks>
    Frame i is the glitch theme at age i / FPS; from GLITCH_SECONDS on it is the plain new
    picture, drawn once and copied for the rest of the hold. Every frame is turned by
    fanvideo.SCREEN_TURN, as stored videos must be. Written to a temporary name
    and renamed, as the fan loops are, so a killed encode never leaves a short file; the
    temporary name carries the process id, since a maker left running by a restarted
    service may be making the same clip.
    </remarks>
    """
    from . import fanvideo, render, themes
    from .sensors import Stats

    render.apply_pictures(*tune)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name("{}.{}.part".format(out.name, os.getpid()))
    with tempfile.TemporaryDirectory() as folder:
        held: Optional[Path] = None
        for i in range(int(FPS * CLIP_SECONDS)):
            target = Path(folder) / "f{:03d}.png".format(i)
            age = i / FPS
            if not themes.glitching(age) and held is not None:
                shutil.copyfile(held, target)
                continue
            fanvideo.for_screen(render.draw_frame(Stats(), rotation, theme="glitch", background=current,
                                                  previous=previous, age=age)).save(target)
            if not themes.glitching(age):
                held = target
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS),
                        "-i", str(Path(folder) / "f%03d.png"), "-c:v", "libx264", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", "-f", "mp4", str(part)],
                       check=True)
    part.replace(out)
    return out


def changes(settings: config.Settings, now: datetime) -> List[Pair]:
    """
    <summary>Every change of the slideshow as (from, to), starting with the next one to come.</summary>
    <returns>Empty with fewer than two pictures in the folder.</returns>
    """
    found = pictures.folder_pictures(settings.slideshow_folder)
    current, _ = screens.picture(settings, "glitch", now)
    if len(found) < 2 or current not in found:
        return []
    start = found.index(current)
    return [(found[(start + k) % len(found)], found[(start + k + 1) % len(found)]) for k in range(len(found))]


def tune_of(settings: config.Settings) -> Tune:
    """<summary>The picture boost in the settings.</summary>"""
    return settings.picture_brightness, settings.picture_contrast


def spawn_maker(pairs: Sequence[Pair], rotation: int, cache: Path, tune: Tune = (100, 100)) -> subprocess.Popen:
    """
    <summary>Starts the clip maker: this module run with -m in a separate, low priority process.</summary>
    <remarks>It inherits the service's environment (PYTHONPATH) and writes errors to the service's journal.</remarks>
    """
    command = ["nice", "-n", "10", sys.executable, "-m", "space_lcd.glitchvideo",
               "--cache", str(cache), "--rotation", str(rotation), "--brightness", str(tune[0]),
               "--contrast", str(tune[1])]
    for previous, current in pairs:
        command += [str(previous), str(current)]
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)


class GlitchVideo:
    """
    <summary>Plays the Glitch slideshow's changes from the screen's storage for one service session.</summary>
    <remarks>
    Mirrors videofan.VideoFan: make_storage gives a ScreenStorage for the open screen; any
    failure marks the session failed and the streamed glitch is drawn instead; dirty means
    a STOP or PLAY went out since the caller last sent a full frame. cache is where clips
    are made, spawn starts the maker (the tests pass one that writes files at once).
    </remarks>
    """

    def __init__(self, make_storage: Callable[[], ScreenStorage], cache: Path = CACHE,
                 spawn: Callable[..., object] = spawn_maker) -> None:
        """<summary>Nothing is sent or started until update() or prefetch().</summary>"""
        self._make_storage = make_storage
        self._cache = cache
        self._spawn = spawn
        self._maker = None
        self.ready: Set[str] = set()
        """<summary>Clip names known to be on the screen at the right size this session.</summary>"""
        self.playing: Optional[str] = None
        self.failed = False
        self.dirty = False
        self.stop_pending = False

    def wanted(self, settings: config.Settings, theme: str, refreshing: bool, now: datetime) -> bool:
        """<summary>Whether a change is under way that a clip should be showing: the first WINDOW_SECONDS of a picture.</summary>"""
        if settings.fan_mode != "video" or theme != "glitch" or refreshing or self.failed:
            return False
        previous, age = screens.transition(settings, theme, now)
        return previous is not None and age is not None and 0 <= age < WINDOW_SECONDS

    def left(self, settings: config.Settings, now: datetime) -> float:
        """<summary>Seconds until the clip's window closes and the picture should go back to a frame.</summary>"""
        age = screens.transition(settings, "glitch", now)[1]
        return max(0.0, WINDOW_SECONDS - (age or 0.0))

    def screen_reopened(self) -> None:
        """<summary>Opening the screen sends STOP_VIDEO and STOP_MEDIA; clips stay on its storage.</summary>"""
        self.playing = None
        self.dirty = False
        self.stop_pending = False

    def _made(self, previous: Path, current: Path, rotation: int, tune: Tune) -> Optional[Path]:
        """<summary>The finished clip on this PC, or None while it is not made yet.</summary>"""
        path = self._cache / clip_name(previous, current, rotation, tune)
        return path if path.is_file() and path.stat().st_size > 0 else None

    def _make_missing(self, settings: config.Settings, now: datetime) -> None:
        """
        <summary>Starts the maker on every change not made yet, next first, unless it is still busy.</summary>
        <remarks>A maker that failed is reported once and the session goes back to streaming the glitch.</remarks>
        """
        if self._maker is not None:
            code = self._maker.poll()
            if code is None:
                return
            self._maker = None
            if code != 0:
                self.failed = True
                log.warning("glitch clips could not be made (exit %s), streaming instead", code)
                return
        tune = tune_of(settings)
        missing = [pair for pair in changes(settings, now) if self._made(*pair, settings.rotation, tune) is None]
        if missing:
            self._maker = self._spawn(missing, settings.rotation, self._cache, tune)

    def _upload(self, storage: ScreenStorage, name: str, path: Path) -> None:
        """
        <summary>Uploads one finished clip if the screen lacks it.</summary>
        <exception cref="StorageError">The screen holds a file of that name at another size (left alone, never overwritten), or a STOP got no answer.</exception>
        <remarks>Stops any playing video first, as the vendor does before uploads; the caller sends a full frame afterwards.</remarks>
        """
        content = path.read_bytes()
        self.dirty = True
        if not storage.stop_media():
            raise StorageError("no media_stop before uploading a glitch clip")
        self.playing = None
        size = storage.file_size(name)
        if size != len(content):
            if size != 0:
                raise StorageError("{} is {} bytes on the screen, expected {}".format(name, size, len(content)))
            started = time.monotonic()
            storage.upload(name, content)
            log.info("uploaded glitch clip %s (%d bytes, %.1f s)", name, len(content), time.monotonic() - started)
        self.ready.add(name)

    def update(self, settings: config.Settings, now: datetime) -> None:
        """
        <summary>Plays the clip for the change under way, if it is made; otherwise starts the maker and leaves this change to the frames.</summary>
        <exception cref="StorageError">Or another FAILURES error: the caller marks the session failed.</exception>
        """
        current, _ = screens.picture(settings, "glitch", now)
        previous, _ = screens.transition(settings, "glitch", now)
        if current is None or previous is None:
            return
        name = clip_name(previous, current, settings.rotation, tune_of(settings))
        storage = self._make_storage()
        if name not in self.ready:
            path = self._made(previous, current, settings.rotation, tune_of(settings))
            if path is None:
                self._make_missing(settings, now)
                return
            self._upload(storage, name, path)
            if not self.wanted(settings, "glitch", False, datetime.now()):
                return  # the upload took the whole window: the frames take over
        if self.playing == name:
            return
        self.dirty = True
        if (self.playing is not None or self.stop_pending) and not storage.stop_media():
            raise StorageError("no media_stop when switching glitch clips")
        self.stop_pending = False
        if not storage.play(name):
            raise StorageError("no play_video_success for {}".format(name))
        log.info("glitch video %s", name)
        self.playing = name

    def prefetch(self, settings: config.Settings, now: datetime) -> bool:
        """
        <summary>While a picture holds: keeps the maker going, and uploads the next change's clip once it is made.</summary>
        <returns>True when anything was sent, so the caller must send a full frame next.</returns>
        <remarks>
        A failure marks the session failed and is logged, and the streamed glitch takes over;
        only a serial port error is raised, for the service's reconnect. Nothing during a
        change's window, and no upload with fewer than PREFETCH_MARGIN seconds left.
        </remarks>
        """
        if self.failed or settings.fan_mode != "video" or self.wanted(settings, "glitch", False, now):
            return False  # never during a change: an upload would freeze the glitch on the screen
        self._make_missing(settings, now)
        upcoming = changes(settings, now)
        if not upcoming:
            return False
        previous, current = upcoming[0]
        name = clip_name(previous, current, settings.rotation, tune_of(settings))
        path = self._made(previous, current, settings.rotation, tune_of(settings))
        _, left = screens.picture(settings, "glitch", now)
        if name in self.ready or path is None or (left is not None and left < PREFETCH_MARGIN):
            return False
        try:
            self._upload(self._make_storage(), name, path)
            self.dirty = False  # the caller sends the full frame now, on this True
        except LINK_FAILURES:
            raise  # the screen went away: the service reconnects
        except FAILURES as error:
            self.failed = True
            log.warning("glitch video unavailable this session, streaming instead: %s", error)
        return True

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
                log.warning("could not stop the glitch clip: %s", error)
        return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    <summary>The clip maker: makes each missing clip for the pairs of pictures given, then exits.</summary>
    <returns>0 when every clip was made or already there, 1 when one could not be.</returns>
    <remarks>Run by spawn_maker(), never by hand in normal use. Touches only this PC: never the screen.</remarks>
    """
    parser = argparse.ArgumentParser(description="Make the Glitch slideshow's clips.")
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--rotation", type=int, default=0)
    parser.add_argument("--brightness", type=int, default=100)
    parser.add_argument("--contrast", type=int, default=100)
    parser.add_argument("pictures", nargs="*", type=Path, help="pairs: from to from to ...")
    args = parser.parse_args(argv)
    from . import render
    render.set_font_root(config.try_load()[0].upstream_dir)
    tune = (args.brightness, args.contrast)
    pairs = list(zip(args.pictures[0::2], args.pictures[1::2]))
    for previous, current in pairs:
        out = args.cache / clip_name(previous, current, args.rotation, tune)
        if out.is_file() and out.stat().st_size > 0:
            continue
        try:
            make_clip(previous, current, out, args.rotation, tune)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print("glitch clip {} not made: {}".format(out.name, error), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
