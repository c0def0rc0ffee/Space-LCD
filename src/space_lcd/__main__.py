"""
<summary>
Command line entry point for the background service:
python -m space_lcd [--preview FILE [--theme NAME] | --once].
</summary>
<remarks>
With no options it runs until stopped, redrawing every interval, picking up settings
changes from the control panel as they are saved, and reconnecting after a screen error.
--preview draws one frame to a PNG and never opens the screen. --once sends a single frame
and exits, for hardware tests; it never uses the screen's stored video, so it uploads
nothing and leaves nothing playing. When stopped by a signal the loop sends a final
"stopped" frame so the screen does not keep showing stale numbers. A settings file that
does not load at start is reported and the defaults are used until it is fixed, the same
as a bad edit while running; the service must not sit in a restart loop over a typo.
</remarks>
"""

import argparse
import configparser
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from . import burnin, config, devices, render, screens, themes
from .panel import Panel
from .sensors import SensorReader, Stats
from .themes import THEMES
from .glitchvideo import GlitchVideo
from .zoomvideo import ZoomVideo
from .videofan import FAILURES as VIDEO_FAILURES, LINK_FAILURES, VideoFan

log = logging.getLogger("space_lcd")

RETRY_DELAY = 10
"""<summary>Seconds to wait before reconnecting after the screen stops answering.</summary>"""

RECONNECT_FIELDS = ("com_port", "upstream_dir", "cooler")
"""<summary>Settings that only take effect by closing the screen and opening it again.</summary>"""

_stop = False


def _request_stop(signum, frame) -> None:
    """<summary>Signal handler: asks the main loop to finish after the current frame.</summary>"""
    global _stop
    _stop = True


def _parse_args(argv):
    """<summary>Reads the command line options.</summary>"""
    parser = argparse.ArgumentParser(prog="space_lcd",
                                     description="Live system stats on the cooler's round screen.")
    parser.add_argument("--config", type=Path, default=config.CONFIG_PATH,
                        help="settings file (default: %(default)s)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--preview", type=Path, metavar="FILE",
                       help="draw one frame to a PNG file without touching the screen")
    group.add_argument("--once", action="store_true",
                       help="send a single frame to the screen and exit")
    parser.add_argument("--theme", choices=list(THEMES),
                        help="with --preview, draw this theme instead of the one in the settings")
    return parser.parse_args(argv)


def refresh_on(settings: config.Settings, now: datetime) -> bool:
    """<summary>Whether a burn-in refresh, by hand or scheduled, takes over the screen now.</summary>"""
    return burnin.active(settings.refresh_until, settings.refresh_daily_at,
                         settings.refresh_daily_minutes, settings.refresh_skip_until, now)


def frame_for(settings: config.Settings, stats: Stats, headline: Optional[str] = None,
              now: Optional[datetime] = None, size: int = render.SIZE, zoom_still: bool = False):
    """
    <summary>
    Draws the frame for this moment: the burn-in refresh colour while a refresh is running,
    otherwise the screen the cycle (or the single theme) says, with the anti burn-in shift
    when it is switched on.
    </summary>
    <param name="now">The time to draw for; the current time when None. Passed in by tests.</param>
    <param name="size">The cooler's screen size (devices.Device.size); smaller screens get the small layouts.</param>
    <param name="zoom_still">Draw the Zoom screen's opening view of its picture, still, instead of the zoom under way:
    what the screen holds while its clips are not playing yet.</param>
    <remarks>The "stopped" frame (a headline) is never replaced by a refresh colour.</remarks>
    """
    now = now or datetime.now()
    if headline is None and refresh_on(settings, now):
        frame = burnin.refresh_frame(now)
        return frame if size == render.SIZE else frame.resize((size, size))
    render.apply_palette(dict(settings.palette))
    render.apply_pictures(settings.picture_brightness, settings.picture_contrast)
    shift = burnin.shift_for(now) if settings.pixel_shift else (0, 0)
    theme, background, _, _ = screens.effective(settings, stats, now)
    previous, age = screens.transition(settings, theme, now)
    zoom = screens.zoom_state(settings, now) if theme == "zoom" else None
    if zoom is not None and zoom_still:
        zoom = zoom._replace(age=0.0, previous=None)
    return render.draw_frame(stats, settings.rotation, headline=headline, theme=theme, background=background,
                             shift=shift, size=size, previous=previous, age=age, zoom=zoom)


def step_seconds(settings: config.Settings, now: Optional[datetime] = None, stats: Optional[Stats] = None,
                 device: devices.Device = devices.SPACE) -> float:
    """
    <summary>
    How long to wait before the next frame: the refresh step while refreshing, otherwise the
    showing screen's interval, cut short so a cycle changes screen, and a slideshow or
    playlist changes picture, on time. The glitch slideshow runs at its animation rate only
    while a glitch is under way, and at the settings' interval while its picture holds.
    </summary>
    <param name="device">The cooler being driven; its animation_interval sets the fan and hamster rate.</param>
    """
    now = now or datetime.now()
    if refresh_on(settings, now):
        return burnin.REFRESH_STEP
    theme, _, left, _ = screens.effective(settings, stats or Stats(now=now), now)
    step = screens.interval(settings, theme, device.animation_interval)
    if theme == "glitch" and not themes.glitching(screens.transition(settings, theme, now)[1]):
        step = settings.interval  # holding still between glitches: no need for ten frames a second
    return min(step, left) if left is not None else step


def wanted_brightness(settings: config.Settings, now: datetime) -> int:
    """
    <summary>The brightness the screen should be at now.</summary>
    <returns>The burn-in brightness while a refresh runs, if one is set; otherwise the normal brightness.</returns>
    <remarks>
    The normal brightness in the settings is never changed by a refresh, which is what
    makes going back automatic: once the refresh ends this simply returns it again.
    </remarks>
    """
    if settings.refresh_brightness is not None and refresh_on(settings, now):
        return settings.refresh_brightness
    return settings.brightness


def sync_brightness(panel, wanted: int) -> None:
    """
    <summary>Sends SET_BRIGHTNESS only when the screen is not already at the wanted level.</summary>
    <param name="panel">The open screen, or a fake with a brightness attribute and set_brightness().</param>
    """
    if panel.brightness != wanted:
        panel.set_brightness(wanted)


def reload_settings(current: config.Settings, path: Path) -> Tuple[config.Settings, bool]:
    """
    <summary>Reads the settings file again after it changed.</summary>
    <param name="current">The settings in use.</param>
    <param name="path">The settings file.</param>
    <returns>The settings to use from now on, and whether the screen must be reopened.</returns>
    <remarks>
    A file that no longer loads, such as one with a typing mistake in it, is logged and
    ignored: the screen carries on with the last good settings rather than stopping.
    Brightness is not sent from here; the main loop compares wanted_brightness() with the
    screen every frame, which covers both a new setting and a refresh starting or ending.
    </remarks>
    """
    try:
        new = config.load(path)
    except (ValueError, configparser.Error) as error:
        log.warning("settings file not applied: %s", error)
        return current, False
    difference = config.changed(current, new)
    if not difference:
        return new, False
    log.info("settings changed: %s", ", ".join(sorted(difference)))
    return new, any(field in difference for field in RECONNECT_FIELDS)


def _pause(seconds: float) -> None:
    """<summary>Sleeps in short steps so a stop signal is acted on within a tenth of a second.</summary>"""
    end = time.monotonic() + seconds
    while not _stop and time.monotonic() < end:
        time.sleep(min(0.1, max(0.0, end - time.monotonic())))


def play_zoom(zoomer: ZoomVideo, settings: config.Settings, drawn: str, refreshing: bool, now: datetime,
              clock=datetime.now) -> Tuple[bool, bool]:
    """
    <summary>One loop's work for the Zoom clips: make or upload new ones, then play the clip for now if one may start.</summary>
    <param name="clock">The time after the upload; datetime.now, or a fake in tests.</param>
    <returns>Whether a clip is playing, so the loop sends no frame, and whether anything was sent, so the next frame
    must be a full one.</returns>
    <remarks>
    New clips are made while an older set plays, and uploaded only at a change of picture. The
    upload takes seconds, so whether to play is asked again at the time after it: a clip starts
    only inside a change's START_WINDOW and so agrees with the clock. Until 1.0.31 the time from
    before the upload was used, and the first clip after one started late and was cut short at
    the next change. When it is too late, the loop holds the still view until the next change.
    </remarks>
    """
    sent = zoomer.prepare(settings, now, upload=zoomer.at_change(settings, now))
    now = clock()
    if not zoomer.wanted(settings, drawn, refreshing, now):
        return False, sent
    zoomer.update(settings, now)
    return True, sent


def open_panel(device: devices.Device, settings: config.Settings):
    """
    <summary>A closed panel for the cooler: KrakenPanel for the Kraken 2023, panel.Panel for the SPACE.</summary>
    <remarks>Both have the same methods; nothing is sent until open().</remarks>
    """
    brightness = wanted_brightness(settings, datetime.now())
    if device is devices.KRAKEN_2023:
        from .kraken import KrakenPanel
        return KrakenPanel(brightness)
    return Panel(settings.upstream_dir, settings.com_port, brightness)


def run(settings: config.Settings, path: Path, once: bool) -> int:
    """
    <summary>Drives the screen until a stop signal, or for one frame when once is set.</summary>
    <param name="settings">The settings loaded at start.</param>
    <param name="path">The settings file, watched for changes while running.</param>
    <param name="once">Send one frame and return.</param>
    <returns>The process exit status.</returns>
    <remarks>
    Any error from the screen closes it and retries after RETRY_DELAY seconds, so an unplug
    or a cooler reset does not end the service. A change of serial port or cooler reopens
    at once. The cooler is worked out again on every open (devices.detect()); stored video
    is only used on a cooler that has it.
    Sensors are read every settings.interval seconds whatever the frame rate, so the fan
    can animate between readings. The Glitch slideshow plays each change as a stored clip
    (glitchvideo.py) and holds the picture as a frame in between, preparing the next clip
    then. The Zoom screen plays one clip per picture (zoomvideo.py), all uploaded together
    once made, switched at each change. The fan loops, the glitch clips and the zoom clips
    each stop the others before playing, since this screen ignores a PLAY on top of a
    playing video.
    </remarks>
    """
    reader = SensorReader()
    time.sleep(0.5)  # give psutil a real interval for the first CPU load figure
    seen = config.modified(path)
    stats, read_at = reader.read(), time.monotonic()
    panel = None
    fan = VideoFan(lambda: panel.storage())
    glitch = GlitchVideo(lambda: panel.storage())
    zoomer = ZoomVideo(lambda: panel.storage())
    while not _stop:
        device = devices.detect(settings.cooler)
        panel = open_panel(device, settings)
        reopen = False
        try:
            panel.open()
            log.info("cooler: %s", device.name)
            fan.screen_reopened()
            glitch.screen_reopened()
            zoomer.screen_reopened()
            while not _stop and not reopen:
                started = time.monotonic()
                if config.modified(path) != seen:
                    seen = config.modified(path)
                    settings, reopen = reload_settings(settings, path)
                    if reopen:
                        break
                sync_brightness(panel, wanted_brightness(settings, datetime.now()))
                # Readings follow the settings' interval even when an animated screen asks
                # for frames ten times a second: nvidia-smi alone takes tens of milliseconds.
                if time.monotonic() - read_at >= settings.interval:
                    stats, read_at = reader.read(), time.monotonic()
                now = datetime.now()
                drawn = screens.effective(settings, stats, now)[0]
                refreshing = refresh_on(settings, now)
                if not once and device.stored_video and fan.wanted(settings, drawn, refreshing):
                    try:
                        glitch.leave()
                        zoomer.leave()
                        fan.update(stats, settings.look(), drawn)
                        _pause(settings.interval - (time.monotonic() - started))
                        continue
                    except LINK_FAILURES:
                        raise  # the screen went away: reconnect, do not give up on stored video
                    except VIDEO_FAILURES as error:
                        log.warning("stored video unavailable this session, streaming instead: %s", error)
                        fan.failed = True
                if not once and device.stored_video and glitch.wanted(settings, drawn, refreshing, now):
                    try:
                        fan.leave()
                        zoomer.leave()
                        glitch.update(settings, now)
                        if glitch.playing is not None:
                            _pause(min(settings.interval, glitch.left(settings, datetime.now()))
                                   - (time.monotonic() - started))
                            continue
                    except LINK_FAILURES:
                        raise
                    except VIDEO_FAILURES as error:
                        log.warning("glitch video unavailable this session, streaming instead: %s", error)
                        glitch.failed = True
                if not once and device.stored_video and zoomer.wanted(settings, drawn, refreshing, now):
                    try:
                        fan.leave()
                        glitch.leave()
                        # New clips (after a change of settings) are made while the old set plays
                        # and uploaded at a change, where a STOP cuts nothing short.
                        playing, sent = play_zoom(zoomer, settings, drawn, refreshing, now)
                        if playing:
                            _pause(min(settings.interval, zoomer.left(settings, datetime.now()))
                                   - (time.monotonic() - started))
                            continue
                        if sent:  # the upload ran past the change: hold the still view until the next
                            panel.force_full_frame()
                    except LINK_FAILURES:
                        raise
                    except VIDEO_FAILURES as error:
                        log.warning("zoom video unavailable this session, streaming instead: %s", error)
                        zoomer.failed = True
                if fan.leave() | glitch.leave() | zoomer.leave():  # all run: any may need stopping
                    panel.force_full_frame()
                still = not once and device.stored_video and zoomer.holding(settings, drawn, refreshing)
                if still and zoomer.prepare(settings, now):
                    panel.force_full_frame()
                if not once and device.stored_video and drawn == "glitch" and not refreshing \
                        and glitch.prefetch(settings, now):
                    panel.force_full_frame()
                panel.show(frame_for(settings, stats, size=device.size, zoom_still=still))
                if still:  # the opening view holds until a change, when the clips take over
                    _pause(min(settings.interval, screens.picture(settings, "zoom", datetime.now())[1]
                               or settings.interval) - (time.monotonic() - started))
                    continue
                if once:
                    return 0
                _pause(step_seconds(settings, stats=stats, device=device) - (time.monotonic() - started))
            if not reopen:
                if fan.leave() | glitch.leave() | zoomer.leave():
                    panel.force_full_frame()
                sync_brightness(panel, settings.brightness)  # never leave the burn-in level behind
                panel.show(frame_for(settings, Stats(), headline="stopped", size=device.size))
                return 0
        except FileNotFoundError as error:
            log.error("%s", error)
            return 1
        except Exception:
            log.exception("screen error, retrying in %d seconds", RETRY_DELAY)
            if once:
                return 1
        finally:
            panel.close()
        if not reopen:
            _pause(RETRY_DELAY)
    return 0


def main(argv=None) -> int:
    """<summary>Parses options, loads settings and runs the chosen mode.</summary>"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    settings, problem = config.try_load(args.config)
    if problem:
        log.error("settings file not applied, using the defaults until it is fixed: %s", problem)
    render.set_font_root(settings.upstream_dir)

    if args.preview:
        if args.theme:
            settings = config.Settings(**{**settings.__dict__, "theme": args.theme})
        reader = SensorReader()
        time.sleep(0.5)
        frame_for(settings, reader.read(), size=devices.detect(settings.cooler).size).save(args.preview)
        print("preview written to {}".format(args.preview))
        return 0

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _request_stop)
    return run(settings, args.config, args.once)


if __name__ == "__main__":
    sys.exit(main())
