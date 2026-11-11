"""
<summary>
Unit tests for everything that does not talk to the screen: settings, sensor parsing,
themes, change detection, live reload, service control and the control panel window.
</summary>
<remarks>
Run with: QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests (build-zip.sh does
this with the install venv). Nothing here imports the upstream driver, opens a serial port
or runs systemctl: service calls go to a fake runner. So the suite is safe to run with the
cooler connected and the service running.
</remarks>
"""

import importlib.util
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections import namedtuple
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from space_lcd import config, render  # noqa: E402
from datetime import time as clock, timedelta  # noqa: E402

from space_lcd import burnin, screens  # noqa: E402
from space_lcd.__main__ import (frame_for, reload_settings, step_seconds,  # noqa: E402
                                sync_brightness, wanted_brightness)
from space_lcd.frames import changed_box, changed_tiles  # noqa: E402
from space_lcd.sensors import (NetworkMeter, Stats, SystemInfo, parse_nvidia_smi,  # noqa: E402
                               pick_cpu_temp, short_cpu_name, short_gpu_name)
from space_lcd import themes  # noqa: E402
from space_lcd.themes import THEMES  # noqa: E402
from space_lcd.timing import SUMMARY_SECONDS, FrameTimer  # noqa: E402
from space_lcd.ui import ServiceControl, write_icon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

Reading = namedtuple("Reading", "label current")

REAL_MONOTONIC = time.monotonic
"""<summary>The real clock, kept before any test swaps time.monotonic so the swap can always be undone.</summary>"""

SAMPLE = Stats(cpu_temp=45.6, cpu_load=12.0, gpu_temp=48.0, gpu_load=9.0,
               ram_used_gb=16.8, ram_total_gb=62.7, now=datetime(2026, 9, 30, 23, 4))


class ConfigTests(unittest.TestCase):
    """<summary>Settings file parsing and validation.</summary>"""

    def _load(self, text: str) -> config.Settings:
        """<summary>Writes text to a temporary ini file and loads it.</summary>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text(text, encoding="utf-8")
            return config.load(path)

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load(Path("/nonexistent/config.ini")), config.Settings())

    def test_values_are_read(self):
        settings = self._load("[display]\ncom_port = /dev/ttyACM1\nbrightness = 70\n"
                              "rotation = 180\ninterval = 1.5\n")
        self.assertEqual(settings.com_port, "/dev/ttyACM1")
        self.assertEqual(settings.brightness, 70)
        self.assertEqual(settings.rotation, 180)
        self.assertEqual(settings.interval, 1.5)

    def test_refresh_times_with_a_zone_are_refused(self):
        """<summary>A hand written end time with a zone offset is refused at load, not left to fail every frame.</summary>"""
        with self.assertRaises(ValueError):
            self._load("[display]\nrefresh_until = 2026-11-08T03:00:00+00:00\n")
        with self.assertRaises(ValueError):
            self._load("[display]\nrefresh_daily_at = 03:00+00:00\n")
        self.assertEqual(self._load("[display]\nrefresh_until = 2026-11-08T03:00:00\n").refresh_until,
                         datetime(2026, 11, 8, 3, 0))

    def test_bad_values_are_refused(self):
        for text in ("[display]\nbrightness = 150\n", "[display]\nrotation = 45\n",
                     "[display]\ninterval = 0.1\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self._load(text)

    def test_shipped_default_file_loads(self):
        self.assertEqual(config.load(ROOT / "config.default.ini"), config.Settings())

    def test_unknown_theme_is_refused(self):
        with self.assertRaises(ValueError):
            self._load("[display]\ntheme = sparkles\n")

    def test_background_path_expands_home(self):
        settings = self._load("[display]\nbackground = ~/Pictures/a.png\n")
        self.assertEqual(settings.background, Path.home() / "Pictures" / "a.png")

    def test_save_keeps_comments_and_other_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            original = (ROOT / "config.default.ini").read_text(encoding="utf-8")
            path.write_text(original, encoding="utf-8")
            config.save({"theme": "clock", "brightness": 75, "background": None}, path)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(len(text.splitlines()), len(original.splitlines()))
            self.assertIn("# Backlight, 0 to 100.", text)
            settings = config.load(path)
            self.assertEqual((settings.theme, settings.brightness, settings.background), ("clock", 75, None))
            self.assertEqual(settings.upstream_dir, config.Settings().upstream_dir)

    def test_save_adds_missing_keys_and_sections(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[driver]\nupstream_dir = /opt/x\n", encoding="utf-8")
            config.save({"theme": "minimal"}, path)
            settings = config.load(path)
            self.assertEqual((settings.theme, settings.upstream_dir), ("minimal", Path("/opt/x")))

    def test_changed_lists_only_differences(self):
        old = config.Settings()
        new = config.Settings(theme="twin", brightness=80)
        self.assertEqual(config.changed(old, new), {"theme": "twin", "brightness": 80})

    def test_save_matches_keys_the_way_configparser_does(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\nTheme = twin\nbrightness: 55\n", encoding="utf-8")
            config.save({"theme": "clock", "brightness": 60}, path)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.lower().count("theme"), 1, "no second theme line: configparser would refuse the file")
            self.assertEqual(text.lower().count("brightness"), 1)
            self.assertEqual((config.load(path).theme, config.load(path).brightness), ("clock", 60))

    def test_try_load_reports_a_broken_file_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            for text in ("[display]\nbrightness = 150\n", "[display]\n[display]\n", "[display]\ntheme = a\ntheme = b\n"):
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    settings, problem = config.try_load(path)
                    self.assertEqual(settings, config.Settings())
                    self.assertTrue(problem)
                    self.assertEqual(config.raw("theme", path), "" if "theme" not in text else config.raw("theme", path))
                    self.assertEqual(len(config.playlist_slots(path)), 5, "the panel's slot view never raises")
            path.write_text("[display]\nbackground = ~/Pictures/100%.png\n", encoding="utf-8")
            self.assertEqual(config.try_load(path)[0].background, Path.home() / "Pictures" / "100%.png")

    def test_hidden_themes_and_odd_intervals_are_refused(self):
        for text in ("[display]\ncycle = game 30\n", "[display]\ncycle = picture 30\n", "[display]\ninterval = nan\n",
                     "[display]\ninterval = inf\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self._load(text)


class BurnInTests(unittest.TestCase):
    """<summary>The refresh cycle, the pixel shift, and how the service chooses between them.</summary>"""

    NOW = datetime(2026, 10, 10, 20, 0, 0)

    def test_settings_parse(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\npixel_shift = no\nrefresh_until = 2026-10-10T21:45:00\n", encoding="utf-8")
            settings = config.load(path)
        self.assertEqual((settings.pixel_shift, settings.refresh_until), (False, datetime(2026, 10, 10, 21, 45)))

    def test_bad_settings_are_refused(self):
        for text in ("[display]\npixel_shift = maybe\n", "[display]\nrefresh_until = tonight\n"):
            with self.subTest(text=text):
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "config.ini"
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        config.load(path)

    def test_refreshing_window(self):
        self.assertFalse(burnin.refreshing(None, self.NOW))
        self.assertTrue(burnin.refreshing(self.NOW + timedelta(minutes=1), self.NOW))
        self.assertFalse(burnin.refreshing(self.NOW, self.NOW))

    def test_cycle_covers_white_black_colours_and_noise(self):
        frames = [burnin.refresh_frame(self.NOW + timedelta(seconds=burnin.REFRESH_STEP * i))
                  for i in range(len(burnin.REFRESH_SEQUENCE))]
        solid = {f.getpixel((0, 0)) for f in frames if f.getcolors(1) is not None}
        self.assertTrue({(255, 255, 255), (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)} <= solid)
        noisy = [f for f in frames if f.getcolors(1000) is None]
        self.assertEqual(len(noisy), 2)
        self.assertNotEqual(noisy[0].tobytes(), noisy[1].tobytes())
        for frame in frames:
            self.assertEqual((frame.size, frame.mode), ((render.SIZE, render.SIZE), "RGB"))

    def test_schedule_window(self):
        at = clock(19, 30)
        self.assertEqual(burnin.scheduled_until(at, 60, self.NOW), datetime(2026, 10, 10, 20, 30))
        self.assertIsNone(burnin.scheduled_until(at, 15, self.NOW))
        self.assertIsNone(burnin.scheduled_until(None, 60, self.NOW))

    def test_schedule_crossing_midnight(self):
        at = clock(23, 30)
        three_am = datetime(2026, 10, 11, 3, 0)
        self.assertEqual(burnin.scheduled_until(at, 480, three_am), datetime(2026, 10, 11, 7, 30))
        self.assertIsNone(burnin.scheduled_until(at, 480, datetime(2026, 10, 11, 8, 0)))

    def test_next_scheduled(self):
        self.assertEqual(burnin.next_scheduled(clock(21, 0), self.NOW), datetime(2026, 10, 10, 21, 0))
        self.assertEqual(burnin.next_scheduled(clock(3, 0), self.NOW), datetime(2026, 10, 11, 3, 0))

    def test_skip_ends_tonight_only(self):
        at, minutes = clock(19, 30), 60
        skip = datetime(2026, 10, 10, 20, 30)
        self.assertFalse(burnin.active(None, at, minutes, skip, self.NOW))
        self.assertTrue(burnin.active(None, at, minutes, skip, self.NOW + timedelta(days=1)))
        self.assertTrue(burnin.active(self.NOW + timedelta(minutes=5), at, minutes, skip, self.NOW))

    def test_schedule_settings_parse_and_validate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\nrefresh_daily_at = 23:30\nrefresh_daily_minutes = 480\n", encoding="utf-8")
            settings = config.load(path)
            self.assertEqual((settings.refresh_daily_at, settings.refresh_daily_minutes), (clock(23, 30), 480))
            for bad in ("refresh_daily_at = late", "refresh_daily_minutes = 0"):
                path.write_text("[display]\n" + bad + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    config.load(path)

    def test_schedule_overrides_theme_then_returns(self):
        settings = config.Settings(refresh_daily_at=clock(19, 30), refresh_daily_minutes=60, pixel_shift=False)
        self.assertIsNone(changed_box(frame_for(settings, SAMPLE, now=self.NOW), burnin.refresh_frame(self.NOW)))
        later = self.NOW + timedelta(hours=1)
        self.assertIsNone(changed_box(frame_for(settings, SAMPLE, now=later), render.draw_frame(SAMPLE)))

    def test_shift_orbit_stays_small_and_moves(self):
        offsets = {burnin.shift_for(self.NOW + timedelta(minutes=burnin.SHIFT_MINUTES * i)) for i in range(8)}
        self.assertEqual(len(offsets), 8)
        self.assertTrue(all(abs(dx) <= 2 and abs(dy) <= 2 for dx, dy in offsets))

    def test_shifted_frame_moves_content_and_keeps_corners_black(self):
        plain = render.draw_frame(SAMPLE)
        moved = render.draw_frame(SAMPLE, shift=(2, 2))
        self.assertIsNotNone(changed_box(plain, moved))
        self.assertEqual(moved.getpixel((0, 0)), render.BACKGROUND)

    def test_service_shows_refresh_then_theme(self):
        settings = config.Settings(refresh_until=self.NOW + timedelta(minutes=15), pixel_shift=False)
        during = frame_for(settings, SAMPLE, now=self.NOW)
        self.assertIsNone(changed_box(during, burnin.refresh_frame(self.NOW)))
        self.assertEqual(step_seconds(settings, self.NOW), burnin.REFRESH_STEP)
        after = frame_for(settings, SAMPLE, now=self.NOW + timedelta(minutes=16))
        self.assertIsNone(changed_box(after, render.draw_frame(SAMPLE)))
        self.assertEqual(step_seconds(settings, self.NOW + timedelta(minutes=16)), settings.interval)

    def test_stopped_frame_is_never_a_refresh_colour(self):
        settings = config.Settings(refresh_until=self.NOW + timedelta(minutes=15), pixel_shift=False)
        stopped = frame_for(settings, Stats(now=self.NOW), headline="stopped", now=self.NOW)
        self.assertIsNone(changed_box(stopped, render.draw_frame(Stats(now=self.NOW), headline="stopped")))


class FakeLink:
    """
    <summary>Stands in for the serial port: records every write and answers from a script of replies.</summary>
    <remarks>Each write takes the next reply; a reply of None means the screen says nothing.</remarks>
    """

    def __init__(self, replies):
        """<summary>replies is consumed one per write; writes collects every packet sent.</summary>"""
        self.replies = list(replies)
        self.writes = []
        self.timeout = 1
        self._pending = b""

    def reset_input_buffer(self):
        """<summary>Drops an unread reply, as the real port drops its input buffer.</summary>"""
        self._pending = b""

    def write(self, data):
        """<summary>Records the packet and queues the next scripted reply for read().</summary>"""
        self.writes.append(bytes(data))
        reply = self.replies.pop(0) if self.replies else None
        self._pending = reply or b""

    def flush(self):
        """<summary>Nothing to flush on a fake; present because the storage module calls it.</summary>"""

    def read(self, size):
        """<summary>Hands out the queued reply in pieces of up to size bytes, then nothing.</summary>"""
        out, self._pending = self._pending[:size], self._pending[size:]
        return out


class StorageTests(unittest.TestCase):
    """<summary>The fenced storage commands, against a fake link: nothing here can reach a screen.</summary>"""

    def test_packet_layout_matches_the_proven_bytes(self):
        from space_lcd import storage
        size = storage.packet(0x6E, "/mnt/SDCARD/img/t.png")
        self.assertEqual(size[:10].hex(" "), "6e ef 69 00 00 00 15 00 00 00")
        self.assertEqual(len(size), 250)
        play = storage.packet(0x78, "/mnt/SDCARD/video/fantest.mp4", flag=1)
        self.assertEqual(play[:10].hex(" "), "78 ef 69 00 00 00 1d 01 00 00")

    def test_data_phase_blocks(self):
        from space_lcd import storage
        phase = storage.data_phase(bytes(range(256)) * 3)
        self.assertEqual(len(phase), 1000)
        self.assertTrue(all(phase[i * 250 + 249] == 0 for i in range(4)))

    def test_fence_refuses_anything_but_our_names(self):
        from space_lcd import storage
        for bad in ("update.app", "SPACE_01.mp4", "../x.mp4", "spacelcd_x.png", "spacelcd_update/../a.mp4"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                storage.checked_path(bad)
        self.assertEqual(storage.checked_path("spacelcd_fan1_slow_blue.mp4"),
                         "/mnt/SDCARD/video/spacelcd_fan1_slow_blue.mp4")

    def test_upload_sequence_and_checks(self):
        from space_lcd import storage
        content = b"x" * 300
        link = FakeLink([b"0", b"create_success", b"file_rev_done.img_show_", b"300"])
        storage.ScreenStorage(link).upload("spacelcd_test.mp4", content)
        ops = [w[0] for w in link.writes]
        self.assertEqual(ops[:2], [0x6E, 0x6F])
        at = 10 + len(storage.checked_path("spacelcd_test.mp4"))
        self.assertEqual(link.writes[1][at:at + 4], (300).to_bytes(4, "little"), "size follows the path, little endian")
        self.assertEqual(len(link.writes[2]), 500)
        self.assertEqual(ops[3], 0x6E)

    def test_upload_never_overwrites_and_stops_without_create_success(self):
        from space_lcd import storage
        link = FakeLink([b"12345"])
        with self.assertRaises(storage.StorageError):
            storage.ScreenStorage(link).upload("spacelcd_test.mp4", b"x")
        self.assertEqual(len(link.writes), 1, "nothing sent after finding a file there")
        link = FakeLink([b"0", None])
        with self.assertRaises(storage.StorageError):
            storage.ScreenStorage(link).upload("spacelcd_test.mp4", b"x")
        self.assertEqual(len(link.writes), 2, "no data phase without create_success")

    def test_upload_refuses_big_files(self):
        from space_lcd import storage
        with self.assertRaises(ValueError):
            storage.ScreenStorage(FakeLink([])).upload("spacelcd_test.mp4", bytes(storage.MAX_UPLOAD + 1))

    def test_no_restart_or_unknown_commands_exist(self):
        source = (ROOT / "src" / "space_lcd" / "storage.py").read_text(encoding="utf-8")
        code = source.split('"""', 2)[2]  # skip the module docstring, which names them as forbidden
        for op in ("0x84", "0x82", "0x81"):
            self.assertNotIn("packet({}".format(op), code)


class FanVideoTests(unittest.TestCase):
    """<summary>Stage 4: choosing loops and driving them, with fake storage and a fake clock.</summary>"""

    def test_bands_with_hysteresis(self):
        from space_lcd import fanvideo
        self.assertEqual(fanvideo.choose(10, 45, None), (0, 0))
        self.assertEqual(fanvideo.choose(90, 80, None), (2, 2))
        self.assertEqual(fanvideo.choose(26, 56, (0, 0)), (0, 0), "just over an edge holds the lower band")
        self.assertEqual(fanvideo.choose(30, 60, (0, 0)), (1, 1))
        self.assertEqual(fanvideo.choose(23, 53, (1, 1)), (1, 1), "just under an edge holds the higher band")
        self.assertEqual(fanvideo.choose(20, 50, (1, 1)), (0, 0))
        self.assertEqual(fanvideo.choose(None, None, (2, 1)), (2, 1))

    def test_loop_names_pass_the_storage_fence(self):
        from space_lcd import fanvideo, storage
        for speed in range(3):
            for colour in range(3):
                storage.checked_path(fanvideo.loop_name(speed, colour, fanvideo.palette_tag({})))
        for bad in ("spacelcd_update_x.mp4", "spacelcd_fw_UPDATE.mp4", "stock.mp4", "spacelcd_a.png"):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    storage.checked_path(bad)

    class FakeStorage:
        """
        <summary>Records storage calls; files maps name to size on the 'screen'.</summary>
        <remarks>stop_answers and upload_error, when set, make the screen misbehave on purpose.</remarks>
        """

        def __init__(self, files=None):
            self.files = dict(files or {})
            self.calls = []
            self.stop_answers = True
            self.upload_error = None

        def stop_media(self):
            """<summary>Records a STOP and answers as the screen would, or not, per stop_answers.</summary>"""
            self.calls.append("stop")
            return self.stop_answers

        def file_size(self, name):
            """<summary>The size on the 'screen', 0 when absent, as the real GET_FILE_SIZE reads.</summary>"""
            return self.files.get(name, 0)

        def upload(self, name, content):
            """<summary>Records an upload and stores its size, or raises upload_error.</summary>"""
            self.calls.append("upload")
            if self.upload_error is not None:
                raise self.upload_error
            self.files[name] = len(content)

        def play(self, name):
            """<summary>Records a PLAY by name and reports success.</summary>"""
            self.calls.append("play " + name)
            return True

    def _fan(self, storage, clock):
        """<summary>A VideoFan over fake storage, a four byte fake loop file, and a clock the test moves.</summary>"""
        from space_lcd.videofan import VideoFan

        class Loop:
            """<summary>Stands in for a loop file on disk.</summary>"""

            def read_bytes(self):
                return b"loop"

        return VideoFan(lambda: storage, loop_file=lambda s, c, palette=None: Loop(), clock=lambda: clock[0])

    def test_uploads_missing_loops_once_then_plays(self):
        storage, clock = self.FakeStorage(), [100.0]
        fan = self._fan(storage, clock)
        fan.update(Stats(cpu_load=10, cpu_temp=45))
        self.assertEqual(storage.calls.count("upload"), 9)
        from space_lcd import fanvideo
        self.assertEqual(storage.calls[-1], "play " + fanvideo.loop_name(0, 0, fanvideo.palette_tag({})))
        storage.calls.clear()
        fan.update(Stats(cpu_load=10, cpu_temp=45))
        self.assertEqual(storage.calls, [], "same loop: nothing sent")

    def test_switch_needs_stop_and_waits_its_turn(self):
        storage, clock = self.FakeStorage(), [100.0]
        fan = self._fan(storage, clock)
        fan.update(Stats(cpu_load=10, cpu_temp=45))
        storage.calls.clear()
        clock[0] = 105.0
        fan.update(Stats(cpu_load=90, cpu_temp=85))
        self.assertEqual(storage.calls, [], "under SWITCH_SECONDS: no change yet")
        clock[0] = 111.0
        fan.update(Stats(cpu_load=90, cpu_temp=85))
        from space_lcd import fanvideo
        self.assertEqual(storage.calls, ["stop", "play " + fanvideo.loop_name(2, 2, fanvideo.palette_tag({}))])

    def test_wrong_sized_loop_on_screen_is_never_overwritten(self):
        from space_lcd import fanvideo
        from space_lcd.storage import StorageError
        storage = self.FakeStorage({fanvideo.loop_name(0, 0, fanvideo.palette_tag({})): 999})
        with self.assertRaises(StorageError):
            self._fan(storage, [0.0]).update(Stats(cpu_load=10, cpu_temp=45))
        self.assertNotIn("upload", storage.calls)

    def test_leave_reports_whether_a_full_frame_is_needed(self):
        storage, clock = self.FakeStorage(), [100.0]
        fan = self._fan(storage, clock)
        self.assertFalse(fan.leave())
        fan.update(Stats(cpu_load=10, cpu_temp=45))
        self.assertTrue(fan.leave())
        self.assertEqual(storage.calls[-1], "stop")
        self.assertFalse(fan.leave(), "nothing left to clean up")

    def test_failed_upload_after_a_stop_still_needs_a_full_frame(self):
        from space_lcd.storage import StorageError
        storage, clock = self.FakeStorage(), [100.0]
        fan = self._fan(storage, clock)
        fan.update(Stats(cpu_load=10, cpu_temp=45))
        storage.calls.clear()
        storage.upload_error = StorageError("refused")
        with self.assertRaises(StorageError):
            fan.update(Stats(cpu_load=10, cpu_temp=45), {"fan_cold": (255, 0, 0)})
        self.assertEqual(storage.calls[:2], ["stop", "upload"], "the loop playing was stopped before the upload")
        self.assertTrue(fan.leave(), "the screen shows a stopped video, not the last frame")

    def test_a_failed_stop_is_sent_again_before_the_next_play(self):
        storage, clock = self.FakeStorage(), [100.0]
        fan = self._fan(storage, clock)
        fan.update(Stats(cpu_load=10, cpu_temp=45))
        storage.stop_answers = False
        self.assertTrue(fan.leave())
        storage.stop_answers = True
        storage.calls.clear()
        fan.update(Stats(cpu_load=10, cpu_temp=45))
        self.assertEqual(storage.calls[0], "stop", "a PLAY on top of a playing video is ignored by this screen")
        self.assertTrue(storage.calls[1].startswith("play "))

    def test_new_fan_colours_mean_new_loops_played_at_once(self):
        from space_lcd import fanvideo
        storage, clock = self.FakeStorage(), [100.0]
        fan = self._fan(storage, clock)
        fan.update(Stats(cpu_load=10, cpu_temp=45))
        storage.calls.clear()
        clock[0] = 101.0
        red_fan = {"fan_cold": (255, 0, 0)}
        fan.update(Stats(cpu_load=10, cpu_temp=45), red_fan)
        self.assertEqual(storage.calls.count("upload"), 9, "a new set for the new colours")
        self.assertEqual(storage.calls[-1], "play " + fanvideo.loop_name(0, 0, fanvideo.palette_tag(red_fan)),
                         "played straight away, not after the switch delay")

    def test_palette_tag_only_follows_fan_colours(self):
        from space_lcd import fanvideo
        self.assertEqual(fanvideo.palette_tag({}), fanvideo.palette_tag({"text": (1, 2, 3)}))
        self.assertNotEqual(fanvideo.palette_tag({}), fanvideo.palette_tag({"fan_hot": (1, 2, 3)}))

    def test_wanted_only_on_the_fan_in_video_mode_without_refresh(self):
        fan = self._fan(self.FakeStorage(), [0.0])
        video = config.Settings(fan_mode="video")
        self.assertTrue(fan.wanted(video, "fan", False))
        self.assertFalse(fan.wanted(video, "dial", False))
        self.assertFalse(fan.wanted(video, "fan", True))
        self.assertFalse(fan.wanted(config.Settings(fan_mode="stream"), "fan", False))
        fan.failed = True
        self.assertFalse(fan.wanted(video, "fan", False))


class HamsterTests(unittest.TestCase):
    """<summary>The hamster screen and its stored loops, with fake storage.</summary>"""

    def setUp(self):
        themes._hamster_state.update(angle=0.0, last=None)

    def test_runs_faster_when_hotter(self):
        themes.hamster_step(30, 10.0)
        cool, _ = themes.hamster_step(30, 10.05)
        themes._hamster_state.update(angle=0.0, last=None)
        themes.hamster_step(90, 10.0)
        hot, _ = themes.hamster_step(90, 10.05)
        self.assertLess(cool, hot)

    def test_step_stays_under_the_rung_aliasing_limit(self):
        themes.hamster_step(90, 0.0)
        before = themes._hamster_state["angle"]
        angle, _ = themes.hamster_step(90, 5.0)
        self.assertLessEqual((angle - before) % 360, 360 / themes.WHEEL_RUNGS / 2)

    def test_draws_and_stands_still_when_stopped(self):
        moving = render.draw_frame(SAMPLE, theme="hamster")
        still_a = render.draw_frame(Stats(), theme="hamster", headline="stopped")
        still_b = render.draw_frame(Stats(), theme="hamster", headline="stopped")
        self.assertEqual(moving.size, (render.SIZE, render.SIZE))
        self.assertIsNone(changed_box(still_a, still_b))

    def test_loops_are_seamless_whole_turns_and_strides(self):
        from space_lcd import hamstervideo
        for name, degrees in hamstervideo.SPEEDS:
            rungs = degrees / (360 / themes.WHEEL_RUNGS)
            strides = degrees / 360 * themes.STRIDES_PER_TURN
            self.assertEqual(rungs, int(rungs), name)
            self.assertEqual(strides, int(strides), name)
            self.assertLessEqual(degrees / (hamstervideo.FPS * hamstervideo.SECONDS), 360 / themes.WHEEL_RUNGS / 2, name)

    def test_speed_bands_with_hysteresis(self):
        from space_lcd import hamstervideo
        self.assertEqual(hamstervideo.choose(45, None), 0)
        self.assertEqual(hamstervideo.choose(80, None), 2)
        self.assertEqual(hamstervideo.choose(56, 0), 0)
        self.assertEqual(hamstervideo.choose(60, 0), 1)

    def test_names_pass_the_fence_and_change_with_colours(self):
        from space_lcd import hamstervideo, storage
        for speed in range(3):
            storage.checked_path(hamstervideo.loop_name(speed, hamstervideo.palette_tag({})))
        self.assertNotEqual(hamstervideo.palette_tag({}), hamstervideo.palette_tag({"hamster": (1, 2, 3)}))
        self.assertEqual(hamstervideo.palette_tag({}), hamstervideo.palette_tag({"fan_hot": (1, 2, 3)}))

    def test_controller_plays_hamster_loops_and_switches_screens_at_once(self):
        from space_lcd import fanvideo, hamstervideo, videofan

        class Loop:
            def read_bytes(self):
                return b"loop"

        sets = {key: videofan.LoopSet(s.keys, s.name, lambda k, p: Loop(), s.choose, s.tag, s.switch_seconds)
                for key, s in videofan.LOOP_SETS.items()}
        fake = FanVideoTests.FakeStorage()
        clock = [100.0]
        controller = videofan.VideoFan(lambda: fake, clock=lambda: clock[0], sets=sets)
        controller.update(Stats(cpu_temp=80, cpu_load=10), {}, "hamster")
        self.assertEqual(fake.calls.count("upload"), 3)
        self.assertEqual(fake.calls[-1], "play " + hamstervideo.loop_name(2, hamstervideo.palette_tag({})))
        fake.calls.clear()
        clock[0] = 101.0
        controller.update(Stats(cpu_temp=80, cpu_load=10), {}, "fan")
        self.assertEqual(fake.calls.count("upload"), 9)
        self.assertEqual(fake.calls[-1], "play " + fanvideo.loop_name(0, 2, fanvideo.palette_tag({})),
                         "a different screen plays at once, after a stop")
        self.assertIn("stop", fake.calls)


class PaletteTests(unittest.TestCase):
    """<summary>User colours: settings, presets, and that every screen draws with them.</summary>"""

    def tearDown(self):
        render.apply_palette({})

    def test_settings_read_and_check_colours(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[colours]\ncool = #ff0000\nhub = 00ff00\n", encoding="utf-8")
            self.assertEqual(config.load(path).palette, (("cool", (255, 0, 0)), ("hub", (0, 255, 0))))
            for bad in ("cool = red", "sparkle = #ffffff"):
                path.write_text("[colours]\n" + bad + "\n", encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    config.load(path)

    def test_apply_and_reset(self):
        render.apply_palette({"cool": (1, 2, 3)})
        self.assertEqual(render.COOL, (1, 2, 3))
        render.apply_palette({})
        self.assertEqual(render.COOL, (45, 212, 191))

    def test_presets_only_name_known_colours(self):
        known = {key for key, _, _ in render.PALETTE}
        for name, colours in render.PRESETS.items():
            self.assertTrue(set(colours) <= known, name)

    def test_every_screen_uses_the_background_colour(self):
        render.apply_palette({"background": (10, 20, 30)})
        for key in ("dial", "twin", "clock", "minimal", "fan", "hamster", "network", "system"):
            frame = render.draw_frame(SAMPLE, theme=key)
            self.assertEqual(frame.getpixel((240, 6)), (10, 20, 30), key)
            self.assertEqual(frame.getpixel((0, 0)), (10, 20, 30), key)

    def test_service_frames_follow_the_settings_palette(self):
        s = config.Settings(theme="minimal", palette=(("background", (40, 0, 0)),), pixel_shift=False)
        self.assertEqual(frame_for(s, SAMPLE, now=datetime.fromtimestamp(5)).getpixel((240, 6)), (40, 0, 0))


class ConsoleLookTests(unittest.TestCase):
    """<summary>The Deckplate console palettes, and the rotation drawn into stored loops.</summary>"""

    def tearDown(self):
        render.apply_palette({})

    def test_console_palettes_are_offered(self):
        for name in ("Amber Console", "Wrist Console"):
            self.assertEqual(set(render.PRESETS[name]), {key for key, _, _ in render.PALETTE}, name)

    def test_look_adds_the_rotation_only_when_turned(self):
        self.assertEqual(config.Settings(palette=(("cool", (1, 2, 3)),)).look(), {"cool": (1, 2, 3)})
        self.assertEqual(config.Settings(rotation=90).look(), {"rotation": 90})

    def test_loop_names_follow_the_rotation_and_stay_the_same_without_one(self):
        from space_lcd import fanvideo, hamstervideo
        for module in (fanvideo, hamstervideo):
            self.assertEqual(module.palette_tag({}), module.palette_tag(config.Settings().look()), module.__name__)
            self.assertNotEqual(module.palette_tag({}), module.palette_tag({"rotation": 180}), module.__name__)

    def test_a_finish_left_in_an_old_settings_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\nfinish = tube\n", encoding="utf-8")
            self.assertEqual(config.load(path).theme, "dial")


class HamsterReviewTests(unittest.TestCase):
    """<summary>The hamster fixes of 07/11/2026: footing, stride and step.</summary>"""

    def test_loops_stay_whole_strides_with_room_per_frame(self):
        from space_lcd import hamstervideo
        frames = hamstervideo.FPS * hamstervideo.SECONDS
        for _, degrees in hamstervideo.SPEEDS:
            strides = degrees * themes.STRIDES_PER_TURN / 360
            self.assertEqual(strides, round(strides))
            self.assertLessEqual(degrees / frames * themes.STRIDES_PER_TURN, 60.0, "six frames or more a stride")
        self.assertLess(themes.HAMSTER_MAX_STEP, 360 / themes.WHEEL_RUNGS / 2 - 3)

    def test_loops_follow_the_rotation(self):
        from space_lcd import hamstervideo
        self.assertTrue(hamstervideo.loop_name(0, hamstervideo.palette_tag({})).startswith("spacelcd_ham3_"))
        frames = {}
        original = themes.hamster_step
        themes.hamster_step = lambda temp, now: (0.0, 0.0)
        try:
            for turn in (0, 180):
                frames[turn] = render.draw_frame(Stats(cpu_temp=50), rotation=turn, theme="hamster")
        finally:
            themes.hamster_step = original
        self.assertEqual(frames[180].rotate(180).tobytes(), frames[0].tobytes())

    def test_paws_reach_the_bottom_rungs(self):
        """<summary>Fur colour appears within the rung band at the bottom of the wheel, under the hamster.</summary>"""
        original = themes.hamster_step
        themes.hamster_step = lambda temp, now: (0.0, 0.0)
        try:
            frame = render.draw_frame(Stats(cpu_temp=50), theme="hamster")
        finally:
            themes.hamster_step = original
        bottom = themes.WHEEL_CENTRE[1] + themes.WHEEL_RADIUS
        band = [frame.getpixel((x, y)) for x in range(160, 300) for y in range(bottom - 16, bottom - 4)]
        self.assertTrue(any(abs(p[0] - c) < 40 and abs(p[1] - render.HAMSTER[1]) < 40 for p in band
                            for c in (render.HAMSTER[0],)))


class NewScreenTests(unittest.TestCase):
    """<summary>The fan over a picture, the glitch slideshow and the Machine Cult.</summary>"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.pictures = root / "show"
        self.pictures.mkdir()
        for name, colour in (("a.png", (200, 30, 30)), ("b.png", (30, 30, 200))):
            Image.new("RGB", (64, 64), colour).save(self.pictures / name)
        self.behind = root / "behind.png"
        Image.new("RGB", (64, 64), (240, 240, 0)).save(self.behind)
        self.real_emblem = themes.CULT_PICTURE
        themes.CULT_PICTURE = root / "machine-cult.png"

    def tearDown(self):
        time.monotonic = REAL_MONOTONIC
        themes.CULT_PICTURE = self.real_emblem
        self.folder.cleanup()

    def test_fan_picture_shows_through_more_under_load(self):
        time.monotonic = lambda: 50.0
        idle = render.draw_frame(Stats(cpu_temp=45, cpu_load=0), theme="fan_reveal", background=self.behind)
        busy = render.draw_frame(Stats(cpu_temp=45, cpu_load=100), theme="fan_reveal", background=self.behind)
        yellow = lambda frame: sum(1 for x in range(60, 420, 6) for y in range(60, 420, 6)
                                   if frame.getpixel((x, y))[2] < 90 and frame.getpixel((x, y))[0] > 150)
        self.assertGreater(yellow(busy), yellow(idle))
        self.assertEqual(render.draw_frame(Stats(), theme="fan_reveal").size, (480, 480), "draws with no picture")

    def test_fan_picture_setting_and_screen_picture(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\nfan_picture = {}\n".format(self.behind), encoding="utf-8")
            settings = config.load(path)
        self.assertEqual(settings.fan_picture, self.behind)
        self.assertEqual(screens.picture(settings, "fan_reveal", datetime.now()), (self.behind, None))

    def test_glitch_turn_follows_the_slideshow_clock(self):
        s = config.Settings(theme="glitch", slideshow_folder=self.pictures, slideshow_seconds=10)
        when = datetime.fromtimestamp(20.5)  # 20.5 s: the third slot, picture a again, half a second in
        current, _ = screens.picture(s, "glitch", when)
        previous, age = screens.transition(s, "glitch", when)
        self.assertEqual((current.name, previous.name), ("a.png", "b.png"))
        self.assertAlmostEqual(age, 0.5)
        self.assertEqual(screens.transition(s, "slideshow", when), (None, None))

    def test_glitch_frames_mix_both_pictures_then_hold_still(self):
        a, b = self.pictures / "a.png", self.pictures / "b.png"
        middle = render.draw_frame(Stats(), theme="glitch", background=b, previous=a, age=0.6)
        later = render.draw_frame(Stats(), theme="glitch", background=b, previous=a, age=5.0)
        again = render.draw_frame(Stats(), theme="glitch", background=b, previous=a, age=0.6)
        self.assertEqual(middle.tobytes(), again.tobytes(), "the same moment gives the same pixels")
        self.assertNotEqual(middle.tobytes(), later.tobytes())
        self.assertEqual(later.getpixel((240, 240)), (30, 30, 200))

    def test_glitch_service_rests_between_glitches(self):
        s = config.Settings(theme="glitch", slideshow_folder=self.pictures, slideshow_seconds=10, interval=2.0)
        self.assertAlmostEqual(step_seconds(s, now=datetime.fromtimestamp(20.3)), 0.1)
        self.assertAlmostEqual(step_seconds(s, now=datetime.fromtimestamp(24.0)), 2.0)
        self.assertAlmostEqual(step_seconds(s, now=datetime.fromtimestamp(29.5)), 0.5, places=3)

    def test_machine_cult_moves_a_little_each_frame(self):
        time.monotonic = lambda: 10.0
        first = render.draw_frame(SAMPLE, theme="machine_cult")
        time.monotonic = lambda: 10.3
        second = render.draw_frame(SAMPLE, theme="machine_cult")
        tiles = changed_tiles(first, second)
        changed = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in tiles)
        self.assertGreater(changed, 0)
        self.assertLess(changed, render.SIZE * render.SIZE * render_full_share())
        self.assertEqual(render.draw_frame(SAMPLE, theme="machine_cult", size=240).size, (240, 240))
        self.assertEqual(render.draw_frame(Stats(), theme="machine_cult", headline="stopped").size, (480, 480))

    def test_machine_cult_draws_the_emblem_picture_when_there_is_one(self):
        """<summary>A picture at CULT_PICTURE takes the skull's place, and the screen still moves and draws small and stopped.</summary>"""
        time.monotonic = lambda: 10.0
        skull = render.draw_frame(SAMPLE, theme="machine_cult")
        Image.new("RGB", (100, 100), (0, 200, 0)).save(themes.CULT_PICTURE)
        first = render.draw_frame(SAMPLE, theme="machine_cult")
        x, y, _ = themes.CULT_EMBLEM
        self.assertGreater(first.getpixel((x, y))[1], 150)
        self.assertNotEqual(skull.tobytes(), first.tobytes())
        time.monotonic = lambda: 10.3
        tiles = changed_tiles(first, render.draw_frame(SAMPLE, theme="machine_cult"))
        self.assertGreater(sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in tiles), 0)
        self.assertEqual(render.draw_frame(SAMPLE, theme="machine_cult", size=240).size, (240, 240))
        self.assertEqual(render.draw_frame(Stats(), theme="machine_cult", headline="stopped").size, (480, 480))

    def test_machine_cult_emblem_ships_with_the_package(self):
        """<summary>The emblem picture is part of the package, so install.sh puts it on every PC, and it opens.</summary>"""
        shipped = Path(themes.__file__).resolve().parent / "assets" / "machine-cult.webp"
        self.assertEqual(self.real_emblem, shipped)
        with Image.open(shipped) as picture:
            self.assertGreater(min(picture.size), 200)

    def test_machine_cult_unreadable_picture_falls_back_to_the_skull(self):
        """<summary>A file at CULT_PICTURE that is not a picture draws the skull rather than failing the frame.</summary>"""
        time.monotonic = lambda: 10.0
        skull = render.draw_frame(SAMPLE, theme="machine_cult")
        themes.CULT_PICTURE.write_text("not a picture")
        self.assertEqual(render.draw_frame(SAMPLE, theme="machine_cult").tobytes(), skull.tobytes())

    def test_machine_cult_cog_ticks_instead_of_turning_every_frame(self):
        """<summary>Between ticks only the binary cursor and the eye change, a few small patches; the rim moves once a tick, forwards.</summary>"""
        self.assertEqual(themes.cult_angle(10.0), themes.cult_angle(11.9))
        self.assertAlmostEqual(themes.cult_angle(12.0) - themes.cult_angle(11.9), themes.CULT_STEP)
        self.assertLess(themes.CULT_STEP, 360 / themes.CULT_TEETH / 2)
        time.monotonic = lambda: 10.0
        first = render.draw_frame(SAMPLE, theme="machine_cult")
        time.monotonic = lambda: 10.3
        tiles = changed_tiles(first, render.draw_frame(SAMPLE, theme="machine_cult"))
        self.assertTrue(1 <= len(tiles) <= 3, tiles)  # the cursor across a tile edge, and the drawn skull's eye
        self.assertLess(sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in tiles), 5000)

    def test_new_screens_are_streamed_never_stored(self):
        from space_lcd import videofan
        for key in ("fan_reveal", "glitch", "machine_cult"):
            self.assertEqual(THEMES[key].interval, 0.1, key)
            self.assertNotIn(key, videofan.LOOP_SETS)


class GlitchVideoTests(unittest.TestCase):
    """<summary>The Glitch slideshow as stored clips: names, timing, making, uploads, plays and stops, against a fake screen.</summary>"""

    class Done:
        """<summary>A finished maker process, as Popen.poll() reports one.</summary>"""

        def __init__(self, code=0):
            self.code = code

        def poll(self):
            return self.code

    def setUp(self):
        from space_lcd import glitchvideo
        self.gv = glitchvideo
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.pictures = root / "show"
        self.pictures.mkdir()
        for name, colour in (("a.png", (200, 30, 30)), ("b.png", (30, 30, 200)), ("c.png", (30, 200, 30))):
            Image.new("RGB", (64, 64), colour).save(self.pictures / name)
        self.cache = root / "clips"
        self.spawned = []
        self.settings = config.Settings(theme="glitch", slideshow_folder=self.pictures, slideshow_seconds=10)
        self.storage = FanVideoTests.FakeStorage()
        self.video = glitchvideo.GlitchVideo(lambda: self.storage, cache=self.cache, spawn=self._spawn)

    def _spawn(self, pairs, rotation, cache, tune=(100, 100)):
        """<summary>Stands in for the maker process: writes every clip asked for at once.</summary>"""
        self.spawned.append(list(pairs))
        cache.mkdir(parents=True, exist_ok=True)
        for previous, current in pairs:
            (cache / self.gv.clip_name(previous, current, rotation, tune)).write_bytes(b"x" * 1000)
        return self.Done()

    def tearDown(self):
        self.folder.cleanup()

    def _at_change(self, seconds):
        """<summary>Holds the module's clock at a moment, for update()'s check that the window is still open.</summary>"""
        moment = datetime.fromtimestamp(seconds)
        real = self.gv.datetime
        self.gv.datetime = type("Clock", (), {"now": staticmethod(lambda: moment)})
        self.addCleanup(setattr, self.gv, "datetime", real)
        return moment

    def test_names_pass_the_fence_and_follow_pictures_and_rotation(self):
        from space_lcd import storage
        a, b = self.pictures / "a.png", self.pictures / "b.png"
        name = self.gv.clip_name(a, b)
        storage.checked_path(name)
        self.assertNotEqual(name, self.gv.clip_name(b, a))
        self.assertNotEqual(name, self.gv.clip_name(a, b, 90))
        self.assertEqual(name, self.gv.clip_name(a, b, 0, (100, 100)), "no boost keeps the old names")
        self.assertNotEqual(name, self.gv.clip_name(a, b, 0, (140, 100)))
        os.utime(b, (1, 1))
        self.assertNotEqual(name, self.gv.clip_name(a, b), "a changed picture gets a new clip")

    def test_changes_start_with_the_next_one(self):
        names = [(x.name, y.name) for x, y in self.gv.changes(self.settings, datetime.fromtimestamp(33.0))]
        self.assertEqual(names, [("a.png", "b.png"), ("b.png", "c.png"), ("c.png", "a.png")])

    def test_wanted_only_in_the_window_after_a_change_in_video_mode(self):
        self.assertTrue(self.video.wanted(self.settings, "glitch", False, datetime.fromtimestamp(30.5)))
        self.assertFalse(self.video.wanted(self.settings, "glitch", False, datetime.fromtimestamp(35.0)))
        self.assertFalse(self.video.wanted(self.settings, "slideshow", False, datetime.fromtimestamp(30.5)))
        self.assertFalse(self.video.wanted(self.settings, "glitch", True, datetime.fromtimestamp(30.5)))
        stream = replace(self.settings, fan_mode="stream")
        self.assertFalse(self.video.wanted(stream, "glitch", False, datetime.fromtimestamp(30.5)))

    def test_an_unmade_clip_starts_the_maker_and_leaves_the_change_to_frames(self):
        now = self._at_change(30.2)
        self.video.update(self.settings, now)
        self.assertEqual(self.storage.calls, [], "nothing sent while the clip is being made")
        self.assertEqual(len(self.spawned), 1)
        self.video.update(self.settings, now)
        name = self.gv.clip_name(self.pictures / "c.png", self.pictures / "a.png")
        self.assertEqual(self.storage.calls, ["stop", "upload", "play " + name])
        self.video.update(self.settings, now)
        self.assertEqual(len(self.storage.calls), 3, "plays once")
        self.assertTrue(self.video.leave())
        self.assertEqual(self.storage.calls[-1], "stop")
        self.assertFalse(self.video.leave(), "nothing more to stop")

    def test_a_wrong_sized_clip_on_the_screen_is_never_overwritten(self):
        now = self._at_change(30.2)
        self.video.update(self.settings, now)  # makes the clips
        name = self.gv.clip_name(self.pictures / "c.png", self.pictures / "a.png")
        self.storage.files[name] = 999
        with self.assertRaises(self.gv.StorageError):
            self.video.update(self.settings, now)
        self.assertNotIn("upload", self.storage.calls)

    def test_prefetch_uploads_the_next_clip_while_the_picture_holds(self):
        self.assertTrue(self.video.prefetch(self.settings, datetime.fromtimestamp(33.0)))
        nxt = self.gv.clip_name(self.pictures / "a.png", self.pictures / "b.png")
        self.assertIn(nxt, self.storage.files)
        self.assertFalse(self.video.prefetch(self.settings, datetime.fromtimestamp(34.0)), "only once")
        self.assertFalse(self.video.leave(), "the caller already sent the full frame")
        self.assertEqual(len(self.spawned), 1, "the maker is not started again once every clip is made")
        fresh = self.gv.GlitchVideo(lambda: self.storage, cache=self.cache, spawn=self._spawn)
        self.assertFalse(fresh.prefetch(self.settings, datetime.fromtimestamp(38.0)), "too close to the change")
        self.assertFalse(fresh.prefetch(self.settings, datetime.fromtimestamp(40.5)), "never during a change")

    def test_failures_fall_back_to_streaming(self):
        self.storage.stop_answers = False
        self.video.prefetch(self.settings, datetime.fromtimestamp(33.0))
        self.assertTrue(self.video.failed)
        self.assertFalse(self.video.wanted(self.settings, "glitch", False, datetime.fromtimestamp(40.5)))
        broken = self.gv.GlitchVideo(lambda: self.storage, cache=self.cache / "none",
                                     spawn=lambda pairs, rotation, cache, tune: self.Done(1))
        broken.prefetch(self.settings, datetime.fromtimestamp(33.0))
        broken.prefetch(self.settings, datetime.fromtimestamp(33.5))
        self.assertTrue(broken.failed, "a maker that fails ends video for the session")

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not installed")
    def test_real_clip_is_small_enough_and_three_seconds_long(self):
        from space_lcd import storage
        out = Path(self.folder.name) / "real.mp4"
        self.gv.make_clip(self.pictures / "a.png", self.pictures / "b.png", out)
        self.assertLess(out.stat().st_size, storage.MAX_UPLOAD)
        probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                                "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), str(int(self.gv.FPS * self.gv.CLIP_SECONDS)))

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not installed")
    def test_clips_are_turned_the_way_the_driver_turns_frames(self):
        """<summary>A picture blue on its right and red on its left: in the clip's held frame the blue is at the top.</summary>"""
        half = Image.new("RGB", (64, 64), (220, 20, 20))
        half.paste((20, 20, 220), (32, 0, 64, 64))
        halves = self.pictures / "halves.png"
        half.save(halves)
        out = Path(self.folder.name) / "turned.mp4"
        self.gv.make_clip(self.pictures / "a.png", halves, out)
        still = Path(self.folder.name) / "last.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-sseof", "-0.2", "-i", str(out), "-frames:v", "1",
                        str(still)], check=True)
        with Image.open(still) as frame:
            top, bottom = frame.convert("RGB").getpixel((240, 120)), frame.convert("RGB").getpixel((240, 360))
        self.assertGreater(top[2], top[0], "blue, the picture's right, is at the top")
        self.assertGreater(bottom[0], bottom[2])

    def test_screen_turn_matches_the_driver(self):
        from space_lcd import fanvideo
        frame = Image.new("RGB", (4, 4))
        frame.putpixel((3, 0), (255, 255, 255))
        self.assertEqual(fanvideo.for_screen(frame).getpixel((0, 0)), (255, 255, 255),
                         "a quarter turn anticlockwise, as upstream's rotate(90) for PORTRAIT")

    def test_maker_process_makes_clips_from_the_command_line(self):
        calls = []
        real, fonts = self.gv.make_clip, render.set_font_root
        self.gv.make_clip = lambda previous, current, out, rotation=0, tune=(100, 100): calls.append(
            (previous.name, current.name))
        render.set_font_root = lambda folder: None  # the real one would change every later test's fonts
        try:
            code = self.gv.main(["--cache", str(self.cache), str(self.pictures / "a.png"), str(self.pictures / "b.png")])
        finally:
            self.gv.make_clip, render.set_font_root = real, fonts
        self.assertEqual((code, calls), (0, [("a.png", "b.png")]))


class PictureBoostTests(unittest.TestCase):
    """<summary>Brightness and contrast for every picture screen.</summary>"""

    def tearDown(self):
        render.apply_pictures(100, 100)

    def test_settings_read_and_check_the_boost(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\npicture_brightness = 140\npicture_contrast = 120\n", encoding="utf-8")
            settings = config.load(path)
            self.assertEqual((settings.picture_brightness, settings.picture_contrast), (140, 120))
            for bad in ("picture_brightness = 20", "picture_contrast = 400"):
                path.write_text("[display]\n" + bad + "\n", encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    config.load(path)

    def test_boost_brightens_pictures_and_none_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "p.png"
            Image.new("RGB", (64, 64), (100, 100, 100)).save(picture)
            plain = render.draw_frame(SAMPLE, theme="slideshow", background=picture).getpixel((240, 240))
            render.apply_pictures(150, 100)
            lifted = render.draw_frame(SAMPLE, theme="slideshow", background=picture).getpixel((240, 240))
            self.assertGreater(lifted[0], plain[0])
            render.apply_pictures(100, 100)
            self.assertEqual(render.draw_frame(SAMPLE, theme="slideshow", background=picture).getpixel((240, 240)), plain)

    def test_service_frames_follow_the_boost(self):
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "p.png"
            Image.new("RGB", (64, 64), (100, 100, 100)).save(picture)
            plain = config.Settings(theme="image", background=picture, pixel_shift=False)
            lifted = replace(plain, picture_brightness=160)
            when = datetime.fromtimestamp(5)
            self.assertGreater(frame_for(lifted, SAMPLE, now=when).getpixel((240, 150))[0],
                               frame_for(plain, SAMPLE, now=when).getpixel((240, 150))[0])


class ZoomTests(unittest.TestCase):
    """<summary>The Zoom screen: its settings, its clock, its drawing, and its clips against a fake screen.</summary>"""

    def setUp(self):
        from space_lcd import zoomvideo
        self.zv = zoomvideo
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.a, self.b = root / "a.png", root / "b.png"
        picture = Image.new("RGB", (800, 600), (40, 40, 40))
        picture.paste((250, 20, 20), (590, 140, 610, 160))  # a red spot at 75 %, 25 %
        picture.save(self.a)
        Image.new("RGB", (600, 600), (20, 20, 250)).save(self.b)
        self.settings = config.Settings(theme="zoom", zoom=((self.a, (0.75, 0.25)), (self.b, (0.5, 0.5))),
                                        zoom_seconds=8, zoom_depth=6)
        self.cache = root / "clips"
        self.spawned = []
        self.storage = FanVideoTests.FakeStorage()
        self.video = zoomvideo.ZoomVideo(lambda: self.storage, cache=self.cache, spawn=self._spawn)

    def tearDown(self):
        self.folder.cleanup()

    def _spawn(self, missing, cache):
        """<summary>Stands in for the maker process: writes every clip asked for at once.</summary>"""
        self.spawned.append(list(missing))
        cache.mkdir(parents=True, exist_ok=True)
        for spec in missing:
            (cache / self.zv.clip_name(spec)).write_bytes(b"z" * 2000)
        return GlitchVideoTests.Done()

    def test_entries_read_write_and_check(self):
        entry = config.format_zoom_entry(self.a, (0.75, 0.25))
        self.assertEqual(config.parse_zoom_entry(entry), (self.a, (0.75, 0.25)))
        self.assertIsNone(config.parse_zoom_entry(""))
        for bad in ("0.5 | /x.png", "1.5, 0.2 | /x.png", "0.5, 0.5 |"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.parse_zoom_entry(bad)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\nzoom_2 = {}\nzoom_seconds = 5\n".format(entry), encoding="utf-8")
            self.assertEqual((config.load(path).zoom, config.load(path).zoom_seconds), (((self.a, (0.75, 0.25)),), 5))
            path.write_text("[display]\nzoom_depth = 50\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                config.load(path)

    def test_state_follows_the_clock(self):
        state = screens.zoom_state(self.settings, datetime.fromtimestamp(11.0))  # 16 s round: second slot, 3 s in
        self.assertEqual((state.current, state.previous, state.age), (self.b, self.a, 3.0))
        self.assertEqual(screens.picture(self.settings, "zoom", datetime.fromtimestamp(11.0)), (self.b, 5.0))
        self.assertEqual(screens.zoom_state(self.settings, datetime.fromtimestamp(19.0)).current, self.a, "round again")
        self.assertIsNone(screens.zoom_state(config.Settings(), datetime.fromtimestamp(19.0)))

    def test_zoom_ends_on_the_point(self):
        from space_lcd import themes
        end = themes.zoom_view(self.a, (0.75, 0.25), 6.0, 1.0)
        start = themes.zoom_view(self.a, (0.75, 0.25), 6.0, 0.0)
        self.assertGreater(end.getpixel((end.width // 2, end.height // 2))[0], 200, "the red spot fills the middle")
        self.assertLess(start.getpixel((start.width // 2, start.height // 2))[0], 100)

    def test_frames_draw_fade_and_survive_no_pictures(self):
        early = render.draw_frame(SAMPLE, theme="zoom", zoom=screens.zoom_state(self.settings, datetime.fromtimestamp(8.1)))
        later = render.draw_frame(SAMPLE, theme="zoom", zoom=screens.zoom_state(self.settings, datetime.fromtimestamp(9.0)))
        self.assertGreater(early.getpixel((240, 240))[0], later.getpixel((240, 240))[0], "the red end fades out")
        self.assertEqual(render.draw_frame(SAMPLE, theme="zoom").size, (480, 480))

    def test_names_pass_the_fence_and_follow_every_setting(self):
        from space_lcd import storage
        first = self.zv.specs(self.settings)[0]
        storage.checked_path(self.zv.clip_name(first))
        for change in ({"zoom_seconds": 9}, {"zoom_depth": 4}, {"rotation": 90}, {"picture_brightness": 130}):
            with self.subTest(change=change):
                other = self.zv.specs(replace(self.settings, **change))[0]
                self.assertNotEqual(self.zv.clip_name(first), self.zv.clip_name(other))

    def test_prepare_makes_then_uploads_all_then_plays_at_a_change(self):
        self.assertFalse(self.video.wanted(self.settings, "zoom", False, datetime.fromtimestamp(8.2)), "not uploaded yet")
        self.assertFalse(self.video.prepare(self.settings, datetime.fromtimestamp(2.0)), "first it makes them")
        self.assertEqual(len(self.spawned), 1)
        self.assertTrue(self.video.prepare(self.settings, datetime.fromtimestamp(2.5)))
        self.assertEqual(self.storage.calls, ["stop", "upload", "upload"])
        self.assertFalse(self.video.prepare(self.settings, datetime.fromtimestamp(3.0)), "only once")
        self.assertFalse(self.video.wanted(self.settings, "zoom", False, datetime.fromtimestamp(4.0)), "waits for a change")
        self.assertTrue(self.video.wanted(self.settings, "zoom", False, datetime.fromtimestamp(8.2)))
        self.video.update(self.settings, datetime.fromtimestamp(8.2))
        second = self.zv.clip_name(self.zv.specs(self.settings)[1])
        self.assertEqual(self.storage.calls[-1], "play " + second)
        self.assertTrue(self.video.wanted(self.settings, "zoom", False, datetime.fromtimestamp(12.0)), "keeps playing")
        self.assertAlmostEqual(self.video.left(self.settings, datetime.fromtimestamp(12.0)), 4.0)
        self.video.update(self.settings, datetime.fromtimestamp(16.1))
        self.assertEqual(self.storage.calls[-2:], ["stop", "play " + self.zv.clip_name(self.zv.specs(self.settings)[0])])
        self.assertTrue(self.video.leave())

    def test_old_set_plays_on_until_the_new_one_is_uploaded_at_a_change(self):
        self.video.prepare(self.settings, datetime.fromtimestamp(2.0))
        self.video.prepare(self.settings, datetime.fromtimestamp(2.5))
        self.video.update(self.settings, datetime.fromtimestamp(8.2))
        old_second = self.zv.clip_name(self.zv.specs(self.settings)[1])
        brighter = replace(self.settings, picture_brightness=140)
        self.assertTrue(self.video.wanted(brighter, "zoom", False, datetime.fromtimestamp(10.0)), "the old set plays on")
        self.assertFalse(self.video.prepare(brighter, datetime.fromtimestamp(10.0), upload=False), "only makes")
        self.assertFalse(self.video.prepare(brighter, datetime.fromtimestamp(10.5), upload=False), "made, held back")
        uploads = self.storage.calls.count("upload")
        self.video.update(brighter, datetime.fromtimestamp(11.0))
        self.assertEqual(self.storage.calls[-1], "play " + old_second, "nothing restarted mid zoom")
        self.assertFalse(self.video.at_change(brighter, datetime.fromtimestamp(12.0)))
        self.assertTrue(self.video.at_change(brighter, datetime.fromtimestamp(16.2)))
        self.assertTrue(self.video.prepare(brighter, datetime.fromtimestamp(16.2), upload=True))
        self.assertEqual(self.storage.calls.count("upload"), uploads + 2)
        self.video.update(brighter, datetime.fromtimestamp(16.3))
        self.assertEqual(self.storage.calls[-1], "play " + self.zv.clip_name(self.zv.specs(brighter)[0]))

    def test_a_clip_after_a_long_upload_waits_for_the_next_change(self):
        """<summary>An upload that runs past the change's window must not start a clip late; one that is quick plays.</summary>"""
        from space_lcd.__main__ import play_zoom
        self.video.prepare(self.settings, datetime.fromtimestamp(2.0))
        self.video.prepare(self.settings, datetime.fromtimestamp(2.5))
        self.video.update(self.settings, datetime.fromtimestamp(8.2))
        brighter = replace(self.settings, picture_brightness=140)
        self.video.prepare(brighter, datetime.fromtimestamp(10.0), upload=False)
        at_change = datetime.fromtimestamp(16.2)
        plays = len([c for c in self.storage.calls if c.startswith("play")])
        playing, sent = play_zoom(self.video, brighter, "zoom", False, at_change,
                                  clock=lambda: datetime.fromtimestamp(19.0))
        self.assertEqual((playing, sent), (False, True), "uploaded, but too late to start in step with the clock")
        self.assertEqual(len([c for c in self.storage.calls if c.startswith("play")]), plays, "nothing played late")
        playing, sent = play_zoom(self.video, brighter, "zoom", False, datetime.fromtimestamp(24.1),
                                  clock=lambda: datetime.fromtimestamp(24.2))
        self.assertEqual((playing, sent), (True, False), "the next change plays the new set on time")
        self.assertEqual(self.storage.calls[-1], "play " + self.zv.clip_name(self.zv.specs(brighter)[1]))

    def test_holding_only_when_video_can_take_over(self):
        self.assertTrue(self.video.holding(self.settings, "zoom", False))
        self.assertFalse(self.video.holding(self.settings, "dial", False))
        self.assertFalse(self.video.holding(replace(self.settings, fan_mode="stream"), "zoom", False))
        self.assertFalse(self.video.holding(replace(self.settings, zoom=()), "zoom", False))
        self.video.failed = True
        self.assertFalse(self.video.holding(self.settings, "zoom", False), "a failed session streams")

    def test_still_frame_is_the_opening_view(self):
        when = datetime.fromtimestamp(4.0)  # the picture with the red spot, half way through its zoom
        s = replace(self.settings, pixel_shift=False)
        still = frame_for(s, SAMPLE, now=when, zoom_still=True)
        opening = render.draw_frame(SAMPLE, theme="zoom", zoom=screens.zoom_state(s, when)._replace(age=0.0, previous=None))
        self.assertEqual(still.tobytes(), opening.tobytes())
        self.assertNotEqual(still.tobytes(), frame_for(s, SAMPLE, now=when).tobytes())

    def test_a_failed_maker_or_upload_streams_instead(self):
        broken = self.zv.ZoomVideo(lambda: self.storage, cache=self.cache, spawn=lambda missing, cache: GlitchVideoTests.Done(1))
        broken.prepare(self.settings, datetime.fromtimestamp(10.0))
        broken.prepare(self.settings, datetime.fromtimestamp(10.5))
        self.assertTrue(broken.failed)
        self.video.prepare(self.settings, datetime.fromtimestamp(10.0))
        self.storage.files[self.zv.clip_name(self.zv.specs(self.settings)[0])] = 5
        self.video.prepare(self.settings, datetime.fromtimestamp(10.5))
        self.assertTrue(self.video.failed, "a wrong sized file on the screen is never overwritten")
        self.assertNotIn("upload", self.storage.calls)

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not installed")
    def test_real_clip_fits_and_runs_zoom_plus_hold(self):
        from space_lcd import storage
        spec = self.zv.specs(replace(self.settings, zoom_seconds=3))[0]
        out = self.cache / "real.mp4"
        self.zv.make_clip(spec, out)
        self.assertLess(out.stat().st_size, storage.MAX_UPLOAD)
        probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                                "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), str(int(self.zv.FPS * (3 + self.zv.HOLD_SECONDS))))

    def test_streamed_never_in_the_fan_loop_sets(self):
        from space_lcd import videofan
        self.assertEqual(THEMES["zoom"].interval, 0.1)
        self.assertNotIn("zoom", videofan.LOOP_SETS)


def render_full_share() -> float:
    """<summary>The share of the screen over which the panel sends a full frame instead of tiles.</summary>"""
    from space_lcd import panel
    return panel.FULL_FRAME_SHARE


class ScannerTests(unittest.TestCase):
    """<summary>The Scanner screen: a sweep from the clock and two contacts from the temperatures.</summary>"""

    def tearDown(self):
        time.monotonic = REAL_MONOTONIC

    def _at(self, now, stats=SAMPLE, size=480, headline=None):
        """<summary>The Scanner frame at a given monotonic time.</summary>"""
        themes.time.monotonic = lambda: now
        return render.draw_frame(stats, theme="scanner", size=size, headline=headline)

    def test_sweep_follows_the_clock_and_repeats_each_turn(self):
        self.assertEqual(themes.scanner_angle(0.0), 0.0)
        self.assertAlmostEqual(themes.scanner_angle(themes.SCAN_PERIOD / 4), 90.0)
        self.assertEqual(self._at(1.0).tobytes(), self._at(1.0 + themes.SCAN_PERIOD).tobytes())
        self.assertNotEqual(self._at(1.0).tobytes(), self._at(1.1).tobytes())

    def test_each_frame_changes_only_part_of_the_screen(self):
        tiles = changed_tiles(self._at(1.0), self._at(1.1))
        changed = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in tiles)
        self.assertLess(changed, render.SIZE * render.SIZE * 0.4)

    def test_hotter_contacts_creep_closer(self):
        bearing = themes.SCAN_CONTACTS[0][1]
        cool = themes._contact_point(bearing, 30.0)
        hot = themes._contact_point(bearing, 90.0)
        distance = lambda p: math.hypot(p[0] - themes.CENTRE, p[1] - themes.CENTRE)
        self.assertLess(distance(hot), distance(cool))
        self.assertLess(distance(cool), themes.SCAN_RADIUS)

    def test_machine_cult_load_that_is_not_a_number(self):
        """<summary>A CPU load that is not a number shows as unknown, as on every other screen, instead of failing the frame.</summary>"""
        frame = render.draw_frame(Stats(cpu_temp=40.0, cpu_load=float("nan")), theme="machine_cult")
        self.assertEqual(frame.size, (render.SIZE, render.SIZE))

    def test_stopped_small_and_missing_readings_draw(self):
        self.assertEqual(self._at(1.0, headline="stopped").size, (480, 480))
        self.assertEqual(self._at(1.0, headline="stopped").tobytes(), self._at(3.0, headline="stopped").tobytes(),
                         "stopped is still")
        self.assertEqual(self._at(1.0, stats=Stats()).size, (480, 480))
        self.assertEqual(self._at(1.0, size=240).size, (240, 240))

    def test_streamed_at_the_animation_rate_never_stored(self):
        from space_lcd import videofan
        self.assertEqual(THEMES["scanner"].interval, 0.1)
        self.assertNotIn("scanner", videofan.LOOP_SETS)


class RuleTests(unittest.TestCase):
    """<summary>Phase 5: the Smart picture rules, with made up processes and a fake Steam folder.</summary>"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        art = self.root / "Steam" / "appcache" / "librarycache" / "1091500"
        art.mkdir(parents=True)
        Image.new("RGB", (1920, 620), (0, 0, 200)).save(art / "library_hero.jpg")
        Image.new("RGBA", (640, 74), (255, 255, 255, 255)).save(art / "logo.png")
        self.app_picture = self.root / "app.png"
        Image.new("RGB", (64, 64), (0, 200, 0)).save(self.app_picture)
        self.hot_picture = self.root / "hot.png"
        Image.new("RGB", (64, 64), (200, 0, 0)).save(self.hot_picture)
        self.roots = (self.root / "Steam",)

    def tearDown(self):
        self.folder.cleanup()

    def test_steam_game_from_launcher_command_line(self):
        from space_lcd import rules
        lines = [["/usr/bin/bash"], ["reaper", "SteamLaunch", "AppId=1091500", "--", "game.exe"]]
        self.assertEqual(rules.steam_game(lines), 1091500)
        self.assertIsNone(rules.steam_game([["reaper", "SteamLaunch", "AppId=0"]]))
        self.assertIsNone(rules.steam_game([["tool", "AppId=5"]]), "AppId without SteamLaunch does not count")

    def test_steam_game_from_environment(self):
        from space_lcd import rules
        self.assertEqual(rules.steam_game_from_env([{"PATH": "/usr/bin"}, {"SteamAppId": "1551360"}]), 1551360)
        self.assertEqual(rules.steam_game_from_env([{"SteamGameId": "2000000"}]), 2000000)
        self.assertIsNone(rules.steam_game_from_env([{"SteamAppId": "0"}]))
        self.assertIsNone(rules.steam_game_from_env([{"SteamGameId": str(2 ** 32 + 5)}]), "non Steam shortcut")

    def test_priority_game_then_app_then_temperature_then_fallback(self):
        from space_lcd import rules
        s = config.Settings(rules_apps=(("firefox", self.app_picture),),
                            rules_temp_pictures=(None, None, self.hot_picture), rules_fallback="clock")
        resolve = lambda temp, running, game: rules.resolve(s, temp, running, game, self.roots)[0::2]
        self.assertEqual(resolve(80, {"firefox"}, 1091500), ("game", "game 1091500"))
        self.assertEqual(resolve(80, {"firefox"}, None), ("picture", "app firefox"))
        self.assertEqual(resolve(80, {"bash"}, None), ("picture", "temperature 3"))
        self.assertEqual(resolve(40, {"bash"}, None), ("clock", "fallback"))

    def test_newer_cache_layout_with_hashed_subfolders(self):
        from space_lcd import rules
        art = self.root / "Steam" / "appcache" / "librarycache" / "2483190"
        (art / "3eeb99").mkdir(parents=True)
        (art / "b49430").mkdir()
        (art / "9f0729").mkdir()
        Image.new("RGB", (1920, 620), (200, 0, 0)).save(art / "3eeb99" / "library_hero.jpg")
        Image.new("RGBA", (640, 74), (255, 255, 255, 255)).save(art / "b49430" / "logo.png")
        Image.new("RGB", (460, 215)).save(art / "9f0729" / "library_header.jpg")
        folder = rules.artwork_folder(2483190, self.roots)
        self.assertEqual(sorted(rules.find_art(folder)), ["header", "hero", "logo"])
        frame = render.draw_frame(SAMPLE, theme="game", background=folder)
        self.assertEqual(frame.getpixel((240, 240)), (255, 255, 255))
        self.assertGreater(frame.getpixel((240, 60))[0], 150)

    def test_game_without_artwork_falls_through(self):
        from space_lcd import rules
        s = config.Settings(rules_fallback="minimal")
        self.assertEqual(rules.resolve(s, 40, set(), 999, self.roots)[0], "minimal")

    def test_steam_rule_can_be_switched_off_and_missing_pictures_skip(self):
        from space_lcd import rules
        s = config.Settings(rules_steam=False, rules_apps=(("firefox", self.root / "gone.png"),))
        self.assertEqual(rules.resolve(s, 40, {"firefox"}, 1091500, self.roots)[2], "fallback")

    def test_game_screen_draws_hero_and_logo(self):
        from space_lcd import rules
        folder = rules.artwork_folder(1091500, self.roots)
        frame = render.draw_frame(SAMPLE, theme="game", background=folder)
        self.assertEqual(frame.getpixel((240, 240)), (255, 255, 255), "logo in the middle")
        self.assertEqual(frame.getpixel((240, 60))[2] > 150, True, "hero behind it")

    def test_settings_parse_and_validate(self):
        from space_lcd import rules
        self.assertEqual(rules.parse_app_rule("Firefox | ~/a.png"), ("firefox", Path.home() / "a.png"))
        self.assertEqual(rules.parse_edges("45, 75"), (45.0, 75.0))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            for bad in ("rules_app_1 = firefox", "rules_temp_edges = 70, 50", "rules_fallback = smart",
                        "rules_fallback = game", "rules_steam = maybe"):
                path.write_text("[display]\n" + bad + "\n", encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    config.load(path)

    def test_effective_follows_the_rules_inside_the_cycle(self):
        s = config.Settings(theme="smart", rules_apps=(("firefox", self.app_picture),), pixel_shift=False)
        stats = Stats(**{**SAMPLE.__dict__, "running": frozenset({"firefox"})})
        key, picture, _, reason = screens.effective(s, stats, datetime.fromtimestamp(5))
        self.assertEqual((key, picture, reason), ("picture", self.app_picture, "app firefox"))
        frame = frame_for(s, stats, now=datetime.fromtimestamp(5))
        self.assertEqual(frame.getpixel((240, 240)), (0, 200, 0))

    def test_hidden_screens_are_not_choosable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\ntheme = game\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                config.load(path)


class PictureTests(unittest.TestCase):
    """<summary>Phase 4: the folder slideshow and the timed playlist.</summary>"""

    @staticmethod
    def _folder(names):
        """<summary>A temporary folder holding small solid colour pictures with the given names.</summary>"""
        folder = tempfile.TemporaryDirectory()
        for i, name in enumerate(names):
            if name.endswith((".png", ".jpg")):
                Image.new("RGB", (64, 64), (i * 40, 0, 0)).save(Path(folder.name) / name)
            else:
                (Path(folder.name) / name).write_text("not a picture")
        return folder

    def test_folder_lists_pictures_only_sorted_by_name(self):
        from space_lcd import pictures
        with self._folder(["b.png", "A.jpg", "notes.txt", ".hidden.png"]) as name:
            found = [p.name for p in pictures.folder_pictures(Path(name))]
        self.assertEqual(found, ["A.jpg", "b.png"])
        self.assertEqual(pictures.folder_pictures(Path("/nonexistent")), [])

    def test_playlist_uses_each_pictures_own_time(self):
        from space_lcd import pictures
        entries = ((Path("first.png"), 2), (Path("second.png"), 120))
        at = datetime.fromtimestamp
        self.assertEqual(pictures.pick(entries, at(0)), (Path("first.png"), 2))
        self.assertEqual(pictures.pick(entries, at(1.5)), (Path("first.png"), 0.5))
        self.assertEqual(pictures.pick(entries, at(2)), (Path("second.png"), 120))
        self.assertEqual(pictures.pick(entries, at(122)), (Path("first.png"), 2))
        self.assertEqual(pictures.pick((), at(5)), (None, None))

    def test_settings_parse_and_validate(self):
        self.assertEqual(config.parse_playlist_entry("2 | ~/Pictures/a b|c.png"),
                         (Path.home() / "Pictures" / "a b|c.png", 2))
        self.assertIsNone(config.parse_playlist_entry("  "))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\nplaylist_1 = 2 | /x/a.png\nplaylist_3 = 120 | /x/b.png\n"
                            "slideshow_folder = /x\nslideshow_seconds = 30\n", encoding="utf-8")
            s = config.load(path)
            self.assertEqual(s.playlist, ((Path("/x/a.png"), 2), (Path("/x/b.png"), 120)))
            self.assertEqual((s.slideshow_folder, s.slideshow_seconds), (Path("/x"), 30))
            for bad in ("playlist_1 = /x/a.png", "playlist_1 = 0 | /x/a.png", "slideshow_seconds = 9999"):
                path.write_text("[display]\n" + bad + "\n", encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    config.load(path)

    def test_service_shows_the_current_picture_and_wakes_for_the_next(self):
        with self._folder(["one.png", "two.png"]) as name:
            s = config.Settings(theme="slideshow", slideshow_folder=Path(name), slideshow_seconds=5,
                                pixel_shift=False, interval=2.0)
            moment = datetime.fromtimestamp(7)  # second picture, 3 s left
            frame = frame_for(s, SAMPLE, now=moment)
            self.assertEqual(frame.getpixel((240, 240)), (40, 0, 0))
            self.assertEqual(step_seconds(s, datetime.fromtimestamp(9.5)), 0.5)


class SystemAndNetworkTests(unittest.TestCase):
    """<summary>Phase 3: the system info and network screens and the readings behind them.</summary>"""

    def test_names_are_shortened(self):
        self.assertEqual(short_cpu_name(" AMD Ryzen 7 5800X 8-Core Processor"), "Ryzen 7 5800X")
        self.assertEqual(short_cpu_name("Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz"), "Core i7-9700K")
        self.assertEqual(short_gpu_name("NVIDIA GeForce RTX 4070 Super"), "RTX 4070 Super")

    def test_speeds_from_counters(self):
        meter = NetworkMeter.__new__(NetworkMeter)
        meter._last = None
        from collections import deque
        meter.history, meter.times = deque(maxlen=60), deque(maxlen=60)
        self.assertEqual(meter.sample(10.0, 1000, 500), (None, None))
        self.assertEqual(meter.sample(12.0, 1000 + 250000, 500 + 25000), (1e6, 1e5))
        self.assertEqual(meter.sample(14.0, 0, 0), (0, 0), "a counter going backwards reads as idle")
        self.assertEqual(meter.span(), 2.0)

    def test_formats(self):
        self.assertEqual(themes.bits(850e3), "850 kb/s")
        self.assertEqual(themes.bits(12.34e6), "12.3 Mb/s")
        self.assertEqual(themes.bits(1.05e9), "1.05 Gb/s")
        self.assertEqual(themes.bits(None), "--")
        self.assertEqual(themes.uptime(3 * 86400 + 4 * 3600 + 120), "3d 4h")
        self.assertEqual(themes.uptime(4 * 3600 + 12 * 60), "4h 12m")
        self.assertEqual(themes.span(120), "2 minutes")

    def test_screens_draw_with_and_without_readings(self):
        full = Stats(net_down=2e7, net_up=1e6, net_history=tuple((i * 1e6, i * 1e5) for i in range(30)),
                     net_span_s=58, disk_used_gb=330, disk_total_gb=1832, uptime_s=7200,
                     system=SystemInfo("Ryzen 7 5800X", 8, 16, "RTX 4070 Super", 12.0, "7.0.0"))
        for key in ("network", "system"):
            for stats in (full, Stats()):
                frame = render.draw_frame(stats, theme=key)
                self.assertEqual(frame.size, (render.SIZE, render.SIZE))


class FanTests(unittest.TestCase):
    """<summary>Phase 2: the animated fan.</summary>"""

    def setUp(self):
        themes._fan_state.update(angle=0.0, last=None)

    def test_spins_faster_with_load(self):
        themes.fan_step(0, 100.0)
        idle, _ = themes.fan_step(0, 100.1)
        themes._fan_state.update(angle=0.0, last=None)
        themes.fan_step(100, 100.0)
        busy, _ = themes.fan_step(100, 100.1)
        self.assertLess(idle, busy)

    def test_step_never_exceeds_the_aliasing_limit(self):
        themes.fan_step(100, 0.0)
        before = themes._fan_state["angle"]
        angle, blur = themes.fan_step(100, 5.0)
        self.assertLessEqual((angle - before) % 360, themes.FAN_MAX_STEP + 1e-9)
        self.assertGreater(blur, 0)

    def test_idle_has_no_blur(self):
        themes.fan_step(5, 0.0)
        self.assertEqual(themes.fan_step(5, 0.1)[1], 0.0)

    def test_colour_runs_blue_to_red_with_temperature(self):
        cold = render.draw_frame(Stats(cpu_temp=30, cpu_load=0), theme="fan", headline=None)
        hot = render.draw_frame(Stats(cpu_temp=90, cpu_load=0), theme="fan", headline=None)
        def reddest(frame):
            """<summary>The largest red minus blue of any colour in the frame.</summary>"""
            return max(red - blue for _, (red, _, blue) in frame.getcolors(render.SIZE * render.SIZE))
        self.assertLess(reddest(cold), 20)
        self.assertGreater(reddest(hot), 100)

    def test_a_fan_step_goes_as_partial_updates(self):
        from space_lcd.panel import Panel
        a = render.draw_frame(SAMPLE, theme="fan")
        themes._fan_state["last"] = themes._fan_state["last"] - 0.1
        b = render.draw_frame(Stats(**{**SAMPLE.__dict__, "cpu_temp": 70}), theme="fan")
        panel = Panel(Path("/nonexistent"), "AUTO", 50)
        panel._previous = a
        pieces = panel._pieces(changed_box(a, b), b)
        self.assertIsNotNone(pieces, "a fan step must not need a full frame")
        self.assertLess(sum((r - l) * (bt - t) for l, t, r, bt in pieces), 0.75 * render.SIZE * render.SIZE)

    def test_nothing_but_the_fan(self):
        frame = render.draw_frame(SAMPLE, theme="fan")
        self.assertEqual(frame.getpixel((240, 460)), render.BACKGROUND, "no figures under the fan")

    def test_fan_asks_for_ten_frames_a_second_and_readings_stay_on_interval(self):
        s = config.Settings(theme="fan", interval=2.0)
        self.assertEqual(step_seconds(s, datetime(2026, 10, 10, 20, 0)), 0.1)


class CycleTests(unittest.TestCase):
    """<summary>Phase 1: rotating between screens, worked out from the clock.</summary>"""

    EPOCH = datetime.fromtimestamp(0)

    def _at(self, seconds: float) -> datetime:
        """<summary>A moment a given number of seconds into the cycle, which starts at the epoch.</summary>"""
        return datetime.fromtimestamp(seconds)

    def test_single_theme_when_off_or_empty(self):
        self.assertEqual(screens.current(config.Settings(theme="clock"), self._at(5)), ("clock", None))
        on_but_empty = config.Settings(theme="clock", cycle_on=True)
        self.assertEqual(screens.current(on_but_empty, self._at(5)), ("clock", None))
        off = config.Settings(theme="clock", cycle=(("dial", 10),))
        self.assertEqual(screens.current(off, self._at(5)), ("clock", None))

    def test_cycle_order_and_time_left(self):
        s = config.Settings(cycle_on=True, cycle=(("dial", 60), ("twin", 30), ("clock", 20)))
        self.assertEqual(screens.current(s, self._at(0)), ("dial", 60))
        self.assertEqual(screens.current(s, self._at(70)), ("twin", 20))
        self.assertEqual(screens.current(s, self._at(95)), ("clock", 15))
        self.assertEqual(screens.current(s, self._at(110)), ("dial", 60))

    def test_step_is_cut_short_at_a_screen_change(self):
        s = config.Settings(cycle_on=True, cycle=(("dial", 60), ("twin", 30)), interval=2.0)
        self.assertAlmostEqual(step_seconds(s, self._at(59.5)), 0.5)
        self.assertEqual(step_seconds(s, self._at(10)), 2.0)

    def test_service_draws_the_cycled_screen(self):
        s = config.Settings(cycle_on=True, cycle=(("minimal", 30), ("clock", 30)), pixel_shift=False)
        stats = Stats(**{**SAMPLE.__dict__, "now": self._at(40)})
        self.assertIsNone(changed_box(frame_for(s, stats, now=self._at(40)),
                                      render.draw_frame(stats, theme="clock")))

    def test_cycle_settings_parse_and_validate(self):
        self.assertEqual(config.parse_cycle("dial 60, twin 30"), (("dial", 60), ("twin", 30)))
        self.assertEqual(config.format_cycle((("dial", 60), ("twin", 30))), "dial 60, twin 30")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            for bad in ("cycle = sparkles 30", "cycle = dial 2", "cycle = dial", "cycle_on = perhaps"):
                path.write_text("[display]\n" + bad + "\n", encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    config.load(path)


class TileTests(unittest.TestCase):
    """<summary>Far apart changes go as several small partial updates instead of one big one or a full frame.</summary>"""

    @staticmethod
    def _with_spots(*spots):
        """<summary>A black frame and a copy with small white squares at the given corners.</summary>"""
        before = Image.new("RGB", (render.SIZE, render.SIZE))
        after = before.copy()
        for x, y in spots:
            after.paste((255, 255, 255), (x, y, x + 10, y + 10))
        return before, after

    def test_tiles_are_tight_and_in_screen_pixels(self):
        before, after = self._with_spots((5, 5), (400, 420))
        self.assertEqual(changed_tiles(before, after), [(5, 5, 15, 15), (400, 420, 410, 430)])

    def test_panel_sends_far_apart_changes_as_pieces(self):
        before, after = self._with_spots((20, 20), (440, 440))
        sent = []

        class Driver:
            def DisplayPILImage(self, image, x=0, y=0):
                sent.append((x, y, image.size))

            def ReadData(self, size):
                return b""

        from space_lcd.panel import Panel
        panel = Panel(Path("/nonexistent"), "AUTO", 50)
        panel._lcd = Driver()
        panel._capture_replies()
        panel._previous = before
        panel.show(after)
        self.assertEqual(sent, [(20, 20, (10, 10)), (440, 440, (10, 10))])
        self.assertEqual(panel.timer.full, [])

    def test_theme_switch_still_sends_a_full_frame(self):
        from space_lcd.panel import Panel
        panel = Panel(Path("/nonexistent"), "AUTO", 50)
        panel._previous = render.draw_frame(SAMPLE, theme="dial")
        box = changed_box(panel._previous, render.draw_frame(SAMPLE, theme="clock"))
        self.assertIsNone(panel._pieces(box, render.draw_frame(SAMPLE, theme="clock")))

    def test_a_wide_box_of_small_changes_goes_as_tiles_not_a_full_frame(self):
        from space_lcd.panel import Panel
        before, after = self._with_spots((2, 2), (440, 2), (2, 440), (440, 440))
        panel = Panel(Path("/nonexistent"), "AUTO", 50)
        panel._previous = before
        pieces = panel._pieces(changed_box(before, after), after)
        self.assertIsNotNone(pieces, "four corners changed: the box is the whole screen, the tiles are tiny")
        self.assertEqual(len(pieces), 4)


class ResendTests(unittest.TestCase):
    """<summary>Acting on the screen's needReSend flag, with a fake driver instead of a screen.</summary>"""

    class FakeDriver:
        """<summary>Answers each frame with a scripted QUERY_STATUS reply through ReadData, as upstream does.</summary>"""

        def __init__(self, replies):
            self.replies = list(replies)
            self.sent = []

        def ReadData(self, size):
            return self.replies.pop(0) if self.replies else b""

        def DisplayPILImage(self, image, x=0, y=0):
            self.sent.append("full" if image.size == (render.SIZE, render.SIZE) else "partial")
            self.ReadData(1024)

    def _panel(self, replies):
        """<summary>A Panel wired to a fake driver, skipping open() and the real upstream import.</summary>"""
        from space_lcd.panel import Panel
        panel = Panel(Path("/nonexistent"), "AUTO", 50)
        panel._lcd = self.FakeDriver(replies)
        panel._capture_replies()
        return panel

    def test_sequence_restarts_after_every_full_frame(self):
        class Count:
            Start = 0

        class CountingDriver(self.FakeDriver):
            """<summary>Records the sequence number each partial would carry, then counts up as upstream does.</summary>"""

            def __init__(self):
                super().__init__([])
                self.numbers = []

            def DisplayPILImage(self, image, x=0, y=0):
                if image.size != (render.SIZE, render.SIZE):
                    self.numbers.append(Count.Start)
                    Count.Start += 1

        panel = self._panel([])
        panel._lcd = CountingDriver()
        panel._count = Count
        Count.Start = 37  # where upstream's never reset counter would be by now
        sizes = [render.SIZE, 50, 50, render.SIZE, 50]
        for size in sizes:
            frame = render.draw_frame(SAMPLE) if size == render.SIZE else None
            if frame is None:
                panel._previous = render.draw_frame(SAMPLE)
                frame = panel._previous.copy()
                frame.paste((255, 0, 0), (0, 0, 20, 20))
            else:
                panel._previous = None
            panel.show(frame)
        self.assertEqual(panel._lcd.numbers, [0, 1, 0])

    def test_full_frame_follows_a_resend_request(self):
        ok, again = b"needReSend:0|renderCnt:0|theme:x\x00\x00", b"needReSend:1|renderCnt:0|theme:x\x00"
        panel = self._panel([ok, again, ok, ok])
        base = render.draw_frame(SAMPLE)
        frames = []
        for i in range(4):
            frame = base.copy()
            frame.paste((255, 255, 255), (200 + i * 5, 200, 204 + i * 5, 204))  # one small change each time
            frames.append(frame)
        for frame in frames:
            panel.show(frame)
        self.assertEqual(panel._lcd.sent, ["full", "partial", "full", "partial"])
        self.assertEqual(panel.timer.resends, 1)


class TimingTests(unittest.TestCase):
    """<summary>Phase 0 measurement: summaries and the partial update fit.</summary>"""

    def test_fit_recovers_overhead_and_speed(self):
        timer = FrameTimer(last_summary=0)
        for area in (1000, 5000, 20000, 60000, 100000, 3000):
            timer.record("partial", area, 0.05 + area / 400000)
        overhead, speed = timer.fit()
        self.assertAlmostEqual(overhead, 0.05, places=3)
        self.assertAlmostEqual(speed, 400000, delta=1000)

    def test_no_fit_without_a_spread_of_sizes(self):
        timer = FrameTimer(last_summary=0)
        for _ in range(10):
            timer.record("partial", 5000, 0.1)
        self.assertIsNone(timer.fit())

    def test_summary_once_a_minute_then_resets(self):
        timer = FrameTimer(last_summary=0)
        timer.record("full", 480 * 480, 0.8)
        timer.record("partial", 4000, 0.05)
        self.assertIsNone(timer.due(SUMMARY_SECONDS - 1))
        line = timer.due(SUMMARY_SECONDS)
        self.assertIn("full frames 1, 0.80 s each", line)
        self.assertIn("partial updates 1,", line)
        self.assertEqual(timer.due(2 * SUMMARY_SECONDS), "timing: nothing sent")


class ReloadTests(unittest.TestCase):
    """<summary>The service applying settings saved by the control panel.</summary>"""

    class FakePanel:
        """<summary>Records brightness calls instead of talking to a screen.</summary>"""

        def __init__(self, brightness):
            self.brightness = brightness
            self.levels = []

        def set_brightness(self, level):
            self.brightness = level
            self.levels.append(level)

    NOW = datetime(2026, 10, 10, 20, 0, 0)

    def _reload(self, text: str, current: config.Settings = config.Settings()):
        """<summary>Writes text as the settings file and runs one reload.</summary>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text(text, encoding="utf-8")
            return reload_settings(current, path)

    def test_theme_change_needs_no_reconnect(self):
        settings, reopen = self._reload("[display]\ntheme = clock\n")
        self.assertEqual((settings.theme, reopen), ("clock", False))

    def test_new_brightness_is_sent_once(self):
        settings, reopen = self._reload("[display]\nbrightness = 90\n")
        panel = self.FakePanel(40)
        for _ in range(3):
            sync_brightness(panel, wanted_brightness(settings, self.NOW))
        self.assertEqual((reopen, panel.levels), (False, [90]))

    def test_refresh_brightness_applies_then_returns(self):
        settings = config.Settings(brightness=58, refresh_brightness=100,
                                   refresh_until=self.NOW + timedelta(minutes=15))
        panel = self.FakePanel(58)
        sync_brightness(panel, wanted_brightness(settings, self.NOW))
        sync_brightness(panel, wanted_brightness(settings, self.NOW + timedelta(minutes=10)))
        sync_brightness(panel, wanted_brightness(settings, self.NOW + timedelta(minutes=16)))
        self.assertEqual(panel.levels, [100, 58])
        self.assertEqual(settings.brightness, 58)

    def test_no_refresh_brightness_keeps_normal(self):
        settings = config.Settings(brightness=58, refresh_until=self.NOW + timedelta(minutes=15))
        self.assertEqual(wanted_brightness(settings, self.NOW), 58)

    def test_bad_refresh_brightness_is_refused(self):
        settings, _ = self._reload("[display]\nrefresh_brightness = 150\n", config.Settings(theme="twin"))
        self.assertEqual(settings.theme, "twin")

    def test_port_change_reconnects(self):
        _, reopen = self._reload("[display]\ncom_port = /dev/ttyACM1\n")
        self.assertTrue(reopen)

    def test_broken_file_keeps_last_good_settings(self):
        current = config.Settings(theme="twin")
        settings, reopen = self._reload("[display]\nbrightness = 400\n", current)
        self.assertEqual((settings, reopen), (current, False))


class SensorTests(unittest.TestCase):
    """<summary>Choosing and parsing readings, using fixed input rather than this machine.</summary>"""

    def test_prefers_tctl_on_ryzen(self):
        readings = {"k10temp": [Reading("Tccd1", 50.0), Reading("Tctl", 44.0)]}
        self.assertEqual(pick_cpu_temp(readings), 44.0)

    def test_intel_package(self):
        readings = {"coretemp": [Reading("Core 0", 40.0), Reading("Package id 0", 42.0)]}
        self.assertEqual(pick_cpu_temp(readings), 42.0)

    def test_no_cpu_sensor(self):
        self.assertIsNone(pick_cpu_temp({"nvme": [Reading("Composite", 35.0)]}))

    def test_nvidia_smi(self):
        self.assertEqual(parse_nvidia_smi("48, 9\n"), (48.0, 9.0))
        self.assertEqual(parse_nvidia_smi("48, [N/A]\n"), (48.0, None))
        self.assertEqual(parse_nvidia_smi(""), (None, None))


class RenderTests(unittest.TestCase):
    """<summary>Frame size, determinism and handling of missing readings.</summary>"""

    def test_frame_is_screen_sized_rgb(self):
        frame = render.draw_frame(SAMPLE)
        self.assertEqual(frame.size, (render.SIZE, render.SIZE))
        self.assertEqual(frame.mode, "RGB")

    def test_same_stats_same_pixels(self):
        self.assertIsNone(changed_box(render.draw_frame(SAMPLE), render.draw_frame(SAMPLE)))

    def test_every_theme_draws_normal_and_stopped_frames(self):
        for key in THEMES:
            with self.subTest(theme=key):
                for stats, headline in ((SAMPLE, None), (Stats(now=SAMPLE.now), "stopped")):
                    frame = render.draw_frame(stats, theme=key, headline=headline)
                    self.assertEqual((frame.size, frame.mode), ((render.SIZE, render.SIZE), "RGB"))

    def test_themes_differ(self):
        drawn = {}  # one theme per drawing function: slideshow and playlist share one by design
        for key, theme in THEMES.items():
            if key != "glitch":  # with no pictures it says what the slideshow says, by design
                drawn.setdefault(theme.draw, key)
        frames = [render.draw_frame(SAMPLE, theme=key).tobytes() for key in drawn.values()]
        self.assertEqual(len(set(frames)), len(frames))

    def test_unknown_theme_falls_back_to_default(self):
        self.assertIsNone(changed_box(render.draw_frame(SAMPLE, theme="nope"),
                                      render.draw_frame(SAMPLE, theme=render.DEFAULT_THEME)))

    def test_corners_stay_black(self):
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "white.png"
            Image.new("RGB", (800, 600), (255, 255, 255)).save(picture)
            for key in THEMES:
                frame = render.draw_frame(SAMPLE, rotation=90, theme=key, background=picture)
                for corner in ((0, 0), (479, 0), (0, 479), (479, 479)):
                    self.assertEqual(frame.getpixel(corner), render.BACKGROUND, key)

    def test_image_theme_uses_picture_and_survives_a_missing_one(self):
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "red.png"
            Image.new("RGB", (300, 900), (200, 0, 0)).save(picture)
            frame = render.draw_frame(SAMPLE, theme="image", background=picture)
            self.assertEqual(frame.getpixel((240, 120)), (200, 0, 0))
        missing = render.draw_frame(SAMPLE, theme="image", background=Path(folder) / "gone.png")
        self.assertEqual(missing.size, (render.SIZE, render.SIZE))

    def test_temperature_colours(self):
        self.assertEqual(render.temp_colour(45), render.COOL)
        self.assertEqual(render.temp_colour(70), render.WARM)
        self.assertEqual(render.temp_colour(85), render.HOT)
        self.assertEqual(render.temp_colour(None), render.MUTED)
        self.assertEqual(render.temp_colour(74, render.GPU_LIMITS), render.WARM)

    def test_fraction_clamps(self):
        self.assertEqual(render.fraction(-10, 0, 100), 0.0)
        self.assertEqual(render.fraction(150, 0, 100), 1.0)
        self.assertEqual(render.fraction(None, 0, 100), 0.0)


class ChangedBoxTests(unittest.TestCase):
    """<summary>Only the changed rectangle is sent after the first frame.</summary>"""

    def test_first_frame_is_whole(self):
        frame = render.draw_frame(SAMPLE)
        self.assertEqual(changed_box(None, frame), (0, 0, render.SIZE, render.SIZE))

    def test_temperature_change_is_local(self):
        before = render.draw_frame(SAMPLE)
        after = render.draw_frame(Stats(**{**SAMPLE.__dict__, "cpu_temp": 47.0}))
        left, top, right, bottom = changed_box(before, after)
        self.assertLess((right - left) * (bottom - top), render.SIZE * render.SIZE // 2)


class ServiceControlTests(unittest.TestCase):
    """<summary>Reading systemctl answers, with a fake runner so no real service is touched.</summary>"""

    @staticmethod
    def _fake(answers):
        """<summary>A runner that answers each systemctl verb from a table of (code, output).</summary>"""
        calls = []

        def run(command, **kwargs):
            calls.append(command[2:])
            code, output = answers.get(command[2], (0, ""))
            return subprocess.CompletedProcess(command, code, output + "\n", "")
        return run, calls

    def test_running_and_enabled(self):
        run, _ = self._fake({"is-enabled": (0, "enabled"), "is-active": (0, "active")})
        self.assertEqual(ServiceControl(run).status(), ("active", True))

    def test_installed_but_disabled(self):
        run, _ = self._fake({"is-enabled": (1, "disabled"), "is-active": (3, "inactive")})
        self.assertEqual(ServiceControl(run).status(), ("inactive", False))

    def test_not_installed(self):
        run, _ = self._fake({"is-enabled": (4, "not-found")})
        self.assertEqual(ServiceControl(run).status(), ("not installed", False))

    def test_commands_name_the_unit(self):
        run, calls = self._fake({})
        control = ServiceControl(run)
        control.start()
        control.set_enabled(False)
        self.assertEqual(calls, [["start", "space-lcd.service"], ["disable", "space-lcd.service"]])

    def test_start_at_boot_turns_lingering_on_once(self):
        """<summary>Ticking enables the unit and lingering; with lingering already on, only the unit; unticking never touches lingering.</summary>"""
        import getpass
        user = getpass.getuser()
        run, calls = self._fake({"show-user": (0, "no")})
        self.assertEqual(ServiceControl(run).set_enabled(True), (0, ""))
        self.assertEqual(calls, [["enable", "space-lcd.service"],
                                 ["show-user", user, "--property=Linger"],
                                 ["enable-linger", user]])
        run, calls = self._fake({"show-user": (0, "yes")})
        control = ServiceControl(run)
        self.assertTrue(control.lingering())
        control.set_enabled(True)
        control.set_enabled(False)
        self.assertNotIn(["enable-linger", user], calls)
        self.assertEqual(calls[-1], ["disable", "space-lcd.service"])

    def test_failed_enable_stops_before_lingering(self):
        """<summary>A failed systemctl enable is reported and lingering is not turned on.</summary>"""
        run, calls = self._fake({"enable": (1, "Failed to enable unit")})
        self.assertEqual(ServiceControl(run).set_enabled(True), (1, "Failed to enable unit"))
        self.assertEqual(calls, [["enable", "space-lcd.service"]])


class IconTests(unittest.TestCase):
    """<summary>The menu icon install.sh writes.</summary>"""

    def test_icon_is_square_with_clear_corners(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "icon.png"
            write_icon(path)
            with Image.open(path) as icon:
                self.assertEqual((icon.size, icon.mode), ((256, 256), "RGBA"))
                self.assertEqual(icon.getpixel((0, 0))[3], 0)


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 not installed")
class WindowTests(unittest.TestCase):
    """<summary>The control panel, built offscreen against a scratch settings file and a fake service.</summary>"""

    def test_clicking_a_theme_saves_it(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from space_lcd.window import ControlPanel

        class FakeReader:
            """<summary>Answers every reading with SAMPLE, so previews never touch the sensors.</summary>"""

            def read(self):
                return SAMPLE

        run, calls = ServiceControlTests._fake({"is-enabled": (1, "disabled"), "is-active": (3, "inactive")})
        app = QApplication.instance() or QApplication(["test"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text((ROOT / "config.default.ini").read_text(encoding="utf-8"), encoding="utf-8")
            before = path.read_text(encoding="utf-8")
            window = ControlPanel(path, ServiceControl(run), FakeReader())
            app.processEvents()
            self.assertEqual(path.read_text(encoding="utf-8"), before, "opening the window must not write")
            window._themes.setCurrentItem(window._theme_items["minimal"])
            self.assertEqual(path.read_text(encoding="utf-8"), before, "a click only previews")
            self.assertFalse(window._animate_timer.isActive(), "a still theme does not animate")
            window._themes.setCurrentItem(window._theme_items["scanner"])
            self.assertTrue(window._animate_timer.isActive(), "the clicked animated theme does")
            window._themes.setCurrentItem(window._theme_items["minimal"])
            self.assertFalse(window._animate_timer.isActive())
            window._send("minimal")
            window._rotation.setCurrentIndex(2)
            self.assertEqual((config.load(path).theme, config.load(path).rotation), ("minimal", 180))
            self.assertEqual(window._selected, "minimal", "saving keeps the selection")
            self.assertNotIn(["start", "space-lcd.service"], calls)
            window._shift.setChecked(False)
            self.assertFalse(config.load(path).pixel_shift)
            window._cycle_ticks["dial"].setChecked(True)
            window._cycle_times["dial"].setValue(45)
            window._cycle_ticks["clock"].setChecked(True)
            window._cycle_group.setChecked(True)
            settings = config.load(path)
            self.assertEqual((settings.cycle_on, settings.cycle), (True, (("dial", 45), ("clock", 30))))
            window._send("twin")
            self.assertEqual((config.load(path).theme, config.load(path).cycle_on), ("twin", False),
                             "sending turns the cycle off so the theme shows")
            window._cycle_group.setChecked(True)

            from PySide6.QtWidgets import QFileDialog
            original = QFileDialog.getOpenFileName
            QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("/x/second.png", ""))
            try:
                window._slots[1][1].setValue(120)
                window._choose_slot(1)
            finally:
                QFileDialog.getOpenFileName = original
            window._slots[1][1].setValue(90)
            settings = config.load(path)
            self.assertEqual((settings.theme, settings.playlist), ("playlist", ((Path("/x/second.png"), 90),)))
            self.assertEqual(config.playlist_slots(path)[1], (Path("/x/second.png"), 90))
            self.assertIsNone(config.playlist_slots(path)[0], "slot 1 stays empty, slot 2 keeps its place")
            window._clear_slot(1)
            self.assertEqual(config.load(path).playlist, ())

            QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("/x/game.png", ""))
            try:
                window._app_rules[2][0].setEditText("Firefox")
                window._choose_app_picture(2)
                window._choose_temp_picture(2)
            finally:
                QFileDialog.getOpenFileName = original
            window._edge_low.setValue(45)
            window._fallback.setCurrentIndex(window._fallback.findData("clock"))
            settings = config.load(path)
            self.assertEqual(settings.rules_apps, (("firefox", Path("/x/game.png")),))
            self.assertEqual(config.raw("rules_app_3", path), "firefox | /x/game.png")
            self.assertEqual(settings.rules_temp_pictures[2], Path("/x/game.png"))
            self.assertEqual(settings.rules_temp_edges[0], 45.0)
            self.assertEqual(settings.rules_fallback, "clock")
            self.assertNotIn("game", [window._themes.item(i).data(Qt.ItemDataRole.UserRole)
                                      for i in range(window._themes.count())])

            window._preset.setCurrentText("Neon")
            window._use_preset()
            self.assertEqual(dict(config.load(path).palette)["hot"], (255, 0, 128))
            from PySide6.QtGui import QColor
            from PySide6.QtWidgets import QColorDialog
            picker = QColorDialog.getColor
            QColorDialog.getColor = staticmethod(lambda *a, **k: QColor(1, 2, 3))
            try:
                window._pick_colour("cool")
            finally:
                QColorDialog.getColor = picker
            self.assertEqual(dict(config.load(path).palette)["cool"], (1, 2, 3))
            window._save({key: None for key, _, _ in render.PALETTE}, section="colours")
            self.assertEqual(config.load(path).palette, ())
            self.assertEqual(config.load(path).theme, settings.theme, "colours do not touch other settings")
            window.close()
            render.apply_palette({})

    def test_send_says_nothing_when_the_save_failed(self):
        """<summary>Send to device only reports a send when the settings file was written.</summary>"""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        from space_lcd.window import ControlPanel

        class FakeReader:
            """<summary>Answers every reading with SAMPLE.</summary>"""

            def read(self):
                """<summary>The fixed sample.</summary>"""
                return SAMPLE

        run, _ = ServiceControlTests._fake({"is-enabled": (1, "disabled"), "is-active": (3, "inactive")})
        app = QApplication.instance() or QApplication(["test"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text((ROOT / "config.default.ini").read_text(encoding="utf-8"), encoding="utf-8")
            window = ControlPanel(path, ServiceControl(run), FakeReader())
            app.processEvents()
            original_save, original_warning = config.save, QMessageBox.warning

            def broken(*args, **kwargs):
                """<summary>A settings file that cannot be written.</summary>"""
                raise OSError("read only")

            config.save = broken
            QMessageBox.warning = staticmethod(lambda *a, **k: None)
            try:
                window._send("minimal")
            finally:
                config.save, QMessageBox.warning = original_save, original_warning
            self.assertNotIn("Sent", window.statusBar().currentMessage())
            window._send("minimal")
            self.assertIn("Sent", window.statusBar().currentMessage())
            window.close()

    def test_choosing_a_folder_keeps_the_glitch_slideshow(self):
        """<summary>A folder switches to the Slideshow, except when the Glitch slideshow is showing or previewed.</summary>"""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QFileDialog
        from space_lcd.window import ControlPanel

        class FakeReader:
            """<summary>Answers every reading with SAMPLE.</summary>"""

            def read(self):
                return SAMPLE

        run, _ = ServiceControlTests._fake({"is-enabled": (1, "disabled"), "is-active": (3, "inactive")})
        app = QApplication.instance() or QApplication(["test"])
        original = QFileDialog.getExistingDirectory
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "/x/show")
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "config.ini"
                path.write_text((ROOT / "config.default.ini").read_text(encoding="utf-8"), encoding="utf-8")
                window = ControlPanel(path, ServiceControl(run), FakeReader())
                app.processEvents()
                window._choose_folder()
                self.assertEqual(config.load(path).theme, "slideshow")
                window._themes.setCurrentItem(window._theme_items["glitch"])
                window._choose_folder()
                self.assertEqual(config.load(path).theme, "glitch")
                window._themes.setCurrentItem(window._theme_items["dial"])
                window._choose_folder()
                self.assertEqual(config.load(path).theme, "glitch", "kept while it is on the screen")
                window.close()
        finally:
            QFileDialog.getExistingDirectory = original

    def test_boost_and_zoom_controls_save(self):
        """<summary>The boost spin boxes save; choosing a zoom picture asks for its point, saves both, previews Zoom, sends nothing.</summary>"""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QFileDialog
        from space_lcd.window import ControlPanel

        class FakeReader:
            """<summary>Answers every reading with SAMPLE.</summary>"""

            def read(self):
                return SAMPLE

        run, _ = ServiceControlTests._fake({"is-enabled": (1, "disabled"), "is-active": (3, "inactive")})
        app = QApplication.instance() or QApplication(["test"])
        original = QFileDialog.getOpenFileName
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("/x/zoom.png", ""))
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "config.ini"
                path.write_text((ROOT / "config.default.ini").read_text(encoding="utf-8"), encoding="utf-8")
                window = ControlPanel(path, ServiceControl(run), FakeReader())
                app.processEvents()
                window._picture_brightness.setValue(130)
                window._picture_contrast.setValue(115)
                settings = config.load(path)
                self.assertEqual((settings.picture_brightness, settings.picture_contrast), (130, 115))
                window._pick_point = lambda picture, point: (0.2, 0.7)
                window._choose_zoom(2)
                self.assertEqual(config.load(path).zoom, ((Path("/x/zoom.png"), (0.2, 0.7)),))
                self.assertEqual(config.raw("zoom_3", path), "0.200, 0.700 | /x/zoom.png", "slot 3 keeps its place")
                self.assertEqual(window._selected, "zoom")
                self.assertNotEqual(config.load(path).theme, "zoom", "previewed, not sent")
                window._zoom_seconds.setValue(12)
                self.assertEqual(config.load(path).zoom_seconds, 12)
                window.close()
        finally:
            QFileDialog.getOpenFileName = original
            render.apply_pictures(100, 100)

    def test_window_fits_the_chosen_cooler(self):
        """<summary>Each cooler gets its own screens, options and preview size; changing it saves and refits.</summary>"""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from space_lcd.window import ControlPanel

        class FakeReader:
            """<summary>Answers every reading with SAMPLE, so previews never touch the sensors.</summary>"""

            def read(self):
                return SAMPLE

        run, _ = ServiceControlTests._fake({"is-enabled": (1, "disabled"), "is-active": (3, "inactive")})
        app = QApplication.instance() or QApplication(["test"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text((ROOT / "config.default.ini").read_text(encoding="utf-8"), encoding="utf-8")
            config.save({"cooler": "space"}, path)
            window = ControlPanel(path, ServiceControl(run), FakeReader())
            app.processEvents()
            self.assertEqual(window._device.key, "space")
            self.assertTrue(window._theme_items["cooler"].isHidden())
            self.assertTrue(window._cycle_ticks["cooler"].isHidden())
            self.assertTrue(window._screen_form.isRowVisible(window._fan_video))
            self.assertIn("Set by hand", window._cooler_found.text())
            self.assertEqual(window._preview.pixmap().width(), 300, "the 480 frame shows at the preview size")

            window._cooler.setCurrentIndex(window._cooler.findData("kraken2023"))
            self.assertEqual(config.load(path).cooler, "kraken2023")
            self.assertEqual(window._device.key, "kraken2023")
            self.assertFalse(window._theme_items["cooler"].isHidden())
            self.assertFalse(window._cycle_ticks["cooler"].isHidden())
            self.assertFalse(window._screen_form.isRowVisible(window._fan_video))
            self.assertTrue(window._fan_loop_note.isHidden())
            self.assertIn("NZXT Kraken 2023", window.windowTitle())

            window._cycle_ticks["cooler"].setChecked(True)
            window._cycle_group.setChecked(True)
            self.assertIn(("cooler", 30), config.load(path).cycle)
            window._cooler.setCurrentIndex(window._cooler.findData("space"))
            window._cycle_ticks["dial"].setChecked(True)
            self.assertEqual(config.load(path).cycle, (("dial", 30),), "a screen the SPACE cannot show is dropped")
            window.close()

    def test_pending_brightness_survives_other_saves_and_typing_is_not_saved_per_key(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from space_lcd.window import ControlPanel

        class FakeReader:
            """<summary>Answers every reading with SAMPLE, so previews never touch the sensors.</summary>"""

            def read(self):
                return SAMPLE

        run, _ = ServiceControlTests._fake({"is-enabled": (1, "disabled"), "is-active": (3, "inactive")})
        app = QApplication.instance() or QApplication(["test"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text((ROOT / "config.default.ini").read_text(encoding="utf-8"), encoding="utf-8")
            window = ControlPanel(path, ServiceControl(run), FakeReader())
            window._brightness.setValue(90)
            self.assertTrue(window._save_timer.isActive())
            window._shift.setChecked(False)  # another save inside the slider's settle time
            self.assertEqual(config.load(path).brightness, 90, "the slider's value is written first, not reset")
            self.assertEqual(window._brightness.value(), 90)
            self.assertFalse(window._save_timer.isActive())
            window._brightness.setValue(70)
            window.close()
            self.assertEqual(config.load(path).brightness, 70, "closing the window writes a pending brightness")

            window = ControlPanel(path, ServiceControl(run), FakeReader())
            for box in (window._interval, window._folder_seconds, window._edge_low, window._refresh_level):
                self.assertFalse(box.keyboardTracking(), "a spin box must save on editing finished, not per key")
            window._app_rules[0][0].setEditText("Firefox")  # half a rule: name, no picture yet
            window._rotation.setCurrentIndex(1)
            self.assertEqual(window._app_rules[0][0].currentText(), "Firefox", "a half typed rule survives a save")
            self.assertEqual(config.raw("rules_app_1", path), "")
            window._clear_app_rule(0)
            self.assertEqual(window._app_rules[0][0].currentText(), "")
            window.close()

    def test_clear_burn_in_needs_the_service_and_saves_an_end_time(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QTime
        from PySide6.QtWidgets import QApplication, QMessageBox
        from space_lcd.window import ControlPanel

        class FakeReader:
            """<summary>Answers every reading with SAMPLE, so previews never touch the sensors.</summary>"""

            def read(self):
                return SAMPLE

        app = QApplication.instance() or QApplication(["test"])
        shown = []
        original = QMessageBox.information
        QMessageBox.information = staticmethod(lambda *args: shown.append(args))
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "config.ini"
                path.write_text((ROOT / "config.default.ini").read_text(encoding="utf-8"), encoding="utf-8")
                stopped, _ = ServiceControlTests._fake({"is-enabled": (1, "disabled"), "is-active": (3, "inactive")})
                window = ControlPanel(path, ServiceControl(stopped), FakeReader())
                window._refresh_start.click()
                self.assertIsNone(config.load(path).refresh_until)
                self.assertEqual(len(shown), 1)
                window.close()

                running, _ = ServiceControlTests._fake({"is-enabled": (0, "enabled"), "is-active": (0, "active")})
                window = ControlPanel(path, ServiceControl(running), FakeReader())
                window._refresh_length.setCurrentIndex(0)
                window._refresh_start.click()
                until = config.load(path).refresh_until
                self.assertIsNotNone(until)
                self.assertLess(abs((until - datetime.now()).total_seconds() - 15 * 60), 30)
                self.assertFalse(window._refresh_start.isEnabled())
                window._refresh_stop.click()
                self.assertIsNone(config.load(path).refresh_until)

                window._daily_at.setTime(QTime(datetime.now().hour, datetime.now().minute))
                window._daily.setChecked(True)
                settings = config.load(path)
                self.assertIsNotNone(settings.refresh_daily_at)
                self.assertTrue(window._refresh_stop.isEnabled(), "a scheduled run is in progress")
                window._refresh_stop.click()
                settings = config.load(path)
                self.assertIsNotNone(settings.refresh_skip_until)
                self.assertIsNotNone(settings.refresh_daily_at, "Stop must not cancel the schedule")
                window._daily.setChecked(False)
                self.assertIsNone(config.load(path).refresh_daily_at)

                window._refresh_bright.setChecked(True)
                window._refresh_level.setValue(95)
                self.assertEqual(config.load(path).refresh_brightness, 95)
                window._refresh_bright.setChecked(False)
                self.assertIsNone(config.load(path).refresh_brightness)
                self.assertEqual(config.load(path).brightness, config.Settings().brightness)
                window.close()
        finally:
            QMessageBox.information = original


class KrakenTests(unittest.TestCase):
    """<summary>The Kraken 2023 driver's pixel packing, checked without the cooler.</summary>"""

    def test_rgb565_matches_liquidctl(self):
        """<summary>Same bytes as liquidctl's _prepare_static_file_rgb16 loop, over every red, green and blue edge.</summary>"""
        from space_lcd import kraken
        values = (0, 7, 8, 127, 128, 248, 255)
        pixels = [(r, g, b) for r in values for g in values for b in values]
        image = Image.new("RGB", (len(pixels), 1))
        image.putdata(pixels)
        expected = bytearray()
        for red, green, blue in pixels:
            dr, dg, db = red >> 3, green >> 2, blue >> 3
            expected += bytes([(dr << 3) + (dg >> 3), ((dg & 0x7) << 5) + db])
        self.assertEqual(kraken.rgb565(image), bytes(expected))

    def test_full_frame_size(self):
        """<summary>A frame is 115,200 bytes, the length the bulk header carries.</summary>"""
        from space_lcd import kraken
        self.assertEqual(len(kraken.rgb565(Image.new("RGB", (kraken.SIZE, kraken.SIZE)))), 115200)


class SmallScreenTests(unittest.TestCase):
    """<summary>Drawing for the Kraken's 240 pixel screen, the cooler setting and the cooler readings.</summary>"""

    def test_every_listed_theme_draws_at_240(self):
        """<summary>Each screen, with and without the "stopped" headline, comes out 240 by 240.</summary>"""
        for key, theme in THEMES.items():
            for headline in (None, "stopped"):
                with self.subTest(theme=key, headline=headline):
                    frame = render.draw_frame(Stats(), theme=key, headline=headline, size=240)
                    self.assertEqual(frame.size, (240, 240))

    def test_small_layout_used_only_below_full_size(self):
        """<summary>At 480 the normal layout is drawn, so the SPACE's frames are untouched.</summary>"""
        stats = Stats(cpu_temp=50.0, cpu_load=20.0, gpu_temp=40.0)
        normal = render.draw_frame(stats, theme="dial")
        self.assertEqual(normal.tobytes(), render.draw_frame(stats, theme="dial", size=480).tobytes())
        small = render.draw_frame(stats, theme="dial", size=240)
        shrunk = normal.resize((240, 240), Image.Resampling.LANCZOS)
        self.assertNotEqual(small.tobytes(), shrunk.tobytes())

    def test_refresh_frame_at_240(self):
        """<summary>A burn-in refresh frame is sized for the cooler too.</summary>"""
        now = datetime(2026, 10, 30, 12, 0)
        settings = config.Settings(refresh_until=now + timedelta(minutes=5))
        self.assertEqual(frame_for(settings, Stats(), now=now, size=240).size, (240, 240))

    def test_cooler_setting(self):
        """<summary>cooler reads back, defaults to auto, and an unknown value is refused.</summary>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.ini"
            path.write_text("[display]\ncooler = Kraken2023\n")
            self.assertEqual(config.load(path).cooler, "kraken2023")
            path.write_text("[display]\n")
            self.assertEqual(config.load(path).cooler, "auto")
            path.write_text("[display]\ncooler = corsair\n")
            with self.assertRaises(ValueError):
                config.load(path)

    def test_detect_named_cooler(self):
        """<summary>A named cooler is used as named, without looking at USB.</summary>"""
        from space_lcd import devices
        self.assertIs(devices.detect("space"), devices.SPACE)
        self.assertIs(devices.detect("kraken2023"), devices.KRAKEN_2023)
        self.assertEqual(devices.KRAKEN_2023.size, 240)
        self.assertFalse(devices.KRAKEN_2023.stored_video)

    def test_kraken_animates_faster_and_the_space_does_not_change(self):
        """<summary>The fan and hamster use the Kraken's own frame rate; still screens and the SPACE keep theirs.</summary>"""
        from space_lcd import devices
        when = datetime(2026, 10, 10, 20, 0)
        for theme in ("fan", "hamster"):
            s = config.Settings(theme=theme, interval=2.0)
            self.assertEqual(step_seconds(s, when, device=devices.KRAKEN_2023), 0.06)
            self.assertEqual(step_seconds(s, when, device=devices.SPACE), 0.1)
            self.assertEqual(step_seconds(s, when), 0.1, "the default is the SPACE")
        s = config.Settings(theme="dial", interval=2.0)
        self.assertEqual(step_seconds(s, when, device=devices.KRAKEN_2023), 2.0)

    def test_read_cooler(self):
        """<summary>Coolant, pump and fan come from the Kraken's hwmon folder and nowhere else.</summary>"""
        from space_lcd.sensors import read_cooler
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            other = root / "hwmon0"
            other.mkdir()
            (other / "name").write_text("coretemp\n")
            (other / "temp1_input").write_text("60000\n")
            self.assertEqual(read_cooler(root), (None, None, None))
            chip = root / "hwmon1"
            chip.mkdir()
            (chip / "name").write_text("kraken2023\n")
            (chip / "temp1_input").write_text("25500\n")
            (chip / "fan1_input").write_text("1170\n")
            self.assertEqual(read_cooler(root), (25.5, 1170.0, None))


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 not installed")
class TrayTests(unittest.TestCase):
    """<summary>The tray icon: opening and closing the panel, quitting, and the single instance hand over.</summary>"""

    def setUp(self):
        """<summary>An offscreen application, a fake panel class that records closes, and a socket name no real tray uses.</summary>"""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import uuid
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QMainWindow

        self.app = QApplication.instance() or QApplication(["test"])
        self.closed = []
        self.made = []
        closed, made = self.closed, self.made

        class FakePanel(QMainWindow):
            """<summary>Stands in for the control panel, so the tray can be tested without sensors or settings.</summary>"""

            def closeEvent(self, event):
                """<summary>Records the close, as the real panel's closeEvent is where pending saves happen.</summary>"""
                closed.append(self)
                super().closeEvent(event)

        def make():
            """<summary>Builds and keeps a new fake panel, so a test can count how many the tray made.</summary>"""
            panel = FakePanel()
            made.append(panel)
            return panel

        self.make = make
        self.icon = QIcon()
        self.name = "space-lcd-tray-test-" + uuid.uuid4().hex

    def _settle(self):
        """<summary>Lets Qt deliver deferred deletes and socket traffic.</summary>"""
        from PySide6.QtCore import QCoreApplication, QEvent
        for _ in range(20):
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            time.sleep(0.01)

    def test_open_and_close_the_panel(self):
        """<summary>Open builds one panel, closing by the tray or the title bar throws it away, and the menu follows.</summary>"""
        from space_lcd.tray import Tray
        tray = Tray(self.app, self.make, self.icon, name=self.name)
        self.assertIsNone(tray.panel)
        self.assertTrue(tray._open.isEnabled())
        self.assertFalse(tray._close.isEnabled())
        tray.open_panel()
        self.assertIsNotNone(tray.panel)
        self.assertTrue(tray.panel.isVisible())
        self.assertFalse(tray._open.isEnabled())
        self.assertTrue(tray._close.isEnabled())
        tray.open_panel()
        self.assertEqual(len(self.made), 1, "opening again brings the same panel forward")
        tray.close_panel()
        self._settle()
        self.assertEqual(len(self.closed), 1, "closing goes through the panel's own close, which saves")
        self.assertIsNone(tray.panel, "a closed panel is thrown away")
        self.assertTrue(tray._open.isEnabled())
        tray.open_panel()
        self.assertEqual(len(self.made), 2, "the next open builds a fresh panel")
        tray.panel.close()
        self._settle()
        self.assertIsNone(tray.panel, "closing by the title bar is noticed too")
        tray.quit()

    def test_left_click_toggles(self):
        """<summary>A left click opens a closed panel and closes an open one; the right click menu does neither.</summary>"""
        from PySide6.QtWidgets import QSystemTrayIcon
        from space_lcd.tray import Tray
        tray = Tray(self.app, self.make, self.icon, name=self.name)
        tray._activated(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertIsNotNone(tray.panel)
        tray._activated(QSystemTrayIcon.ActivationReason.Context)
        self.assertIsNotNone(tray.panel, "the right click menu does not toggle")
        tray._activated(QSystemTrayIcon.ActivationReason.Trigger)
        self._settle()
        self.assertIsNone(tray.panel)
        tray.quit()

    def test_quit_closes_the_panel_first(self):
        """<summary>Quitting goes through the panel's own close, so a pending brightness save is not lost.</summary>"""
        from space_lcd.tray import Tray
        tray = Tray(self.app, self.make, self.icon, name=self.name)
        tray.open_panel()
        tray.quit()
        self.assertEqual(len(self.closed), 1)

    def test_second_start_asks_the_first_to_open(self):
        """<summary>A second start reaches the running tray over the local socket, and quitting frees the name.</summary>"""
        from space_lcd.tray import Tray, ask_running_tray
        self.assertFalse(ask_running_tray(self.name, wait_ms=100), "nothing is running yet")
        tray = Tray(self.app, self.make, self.icon, name=self.name)
        self.assertTrue(ask_running_tray(self.name))
        self._settle()
        self.assertIsNotNone(tray.panel, "the running tray opened its panel")
        self.assertEqual(len(self.made), 1)
        tray.quit()
        self._settle()
        self.assertFalse(ask_running_tray(self.name, wait_ms=100), "quitting frees the name")

    def test_close_and_quit_wait_for_an_open_dialog(self):
        """<summary>With one of the panel's dialogs open, Close and Quit bring it forward instead of deleting the panel under it.</summary>"""
        from PySide6.QtWidgets import QApplication
        from space_lcd import tray as tray_module
        tray = tray_module.Tray(self.app, self.make, self.icon, name=self.name)
        tray.open_panel()
        original = QApplication.activeModalWidget
        QApplication.activeModalWidget = staticmethod(lambda: tray.panel)
        try:
            tray.close_panel()
            tray.quit()
        finally:
            QApplication.activeModalWidget = original
        self._settle()
        self.assertEqual(self.closed, [], "nothing closed while a dialog was open")
        self.assertIsNotNone(tray.panel)
        tray.quit()
        self.assertEqual(len(self.closed), 1, "once the dialog is answered, Quit closes as usual")

    def test_menu_entry_and_autostart_use_the_tray(self):
        """<summary>install.sh starts the panel through the tray, and never brings back an autostart entry that was turned off.</summary>"""
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("-m space_lcd.ui --tray --show\n", text)
        self.assertIn("-m space_lcd.ui --tray\n", text)
        self.assertIn("tray-autostart-offered", text)


if __name__ == "__main__":
    unittest.main()
