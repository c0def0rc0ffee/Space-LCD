# Journal

Measurements and verdicts, newest first. Numbers, not impressions.

## 07/11/2026: the zoom "a bit jerky"

- Rob, 1.0.30 on the SPACE: "bit jerky". He changed zoom_seconds, the boost and the depth
  from 15:27; each change needs a new clip set, and from 15:27 to 15:29 the zoom was streamed
  meanwhile: 149 and 147 full frames a minute at 0.35 s each (2.8 a second). The new clip
  (651,647 bytes, uploaded in 0.4 s) played from 15:29:20.
- The clips themselves: a 20 s, 11 times clip of a made up 4000 by 3000 picture shows no
  jumps between frames (none over 2.2 times the median change), and for Rob's point the view
  centre's speed changes by at most 0.01 px a frame. An edge clamp was suspected and ruled
  out: the clamp is on for the whole zoom or never, so it cannot jerk.
- 1.0.31: the old set plays on while a new one is made, and with none on the screen the
  picture holds still. Not yet confirmed on the screen.

## 07/11/2026: stored videos play a quarter turn off; the glitch as stored clips

- Streamed glitch on the SPACE: every glitch frame changes nearly the whole screen, so each
  goes as a full frame at 0.34 s (service timing lines, 14:34 to 14:36): three or four
  frames per 1.2 s glitch. Rob asked for video; 1.0.28 plays each change as a stored clip.
- Clips of two of Rob's photographs: 374,006 and 509,752 bytes; the maker process made two
  in 6.3 s (one alone took 6.0 s before the held frame was drawn once and copied).
- Fan loop uploads from the service log, 14:18: nine loops of 160,710 to 225,194 bytes, one
  about every 2 s, making included.
- Verdict, from Rob on the SPACE with 1.0.28: the glitch played sideways, then the held
  frame came up the right way. Cause in upstream lcd_comm_rev_c.py: in PORTRAIT every frame
  is turned by rotate(90) before sending; a stored video is not. The 1.0.21 to 1.0.28
  hamster loops were on their side too ("never goes to the bottom"). 1.0.29 turns glitch and
  hamster video frames to match; not yet confirmed on the screen.
- 1.0.29 on the SPACE, 15:00 to 15:02: all 17 gl2 clips made in the background (365,228 to
  1,004,222 bytes), each uploaded during the hold in 0.3 to 0.7 s (about 1.4 MB a second),
  then played at its change, one every 10 s, with no warnings. Uploads are much faster than
  the 5 s PREFETCH_MARGIN allows for; left as it is.

## 30/10/2026: Kraken 2023 Phase 4, the fan and hamster at the Kraken's own rate

Offline first, on this PC: drawing a frame takes about 0.018 s at 240 (fan 18.3 ms, hamster
18.0 ms, dial 20.7 ms; RGB565 conversion 0.3 ms). With the 0.041 s send, the Kraken's
animation interval was set to 0.06 s (`devices.KRAKEN_2023.animation_interval`); the SPACE
keeps the theme's 0.1 s.

Then the service for 70 s with Rob's yes, cycling fan, hamster, a picture and the Cooler
screen at 15 s each, brightness already 100 (no brightness command sent). The first minute's
timing line: 475 full frames, 0.04 s each to send. About 15 of those are the still screens
(one every 2 s), so the fan and hamster ran at about 15 frames a second against 16 asked.
No warnings or reconnects. Rob: "all looks great".

## 30/10/2026: Kraken 2023 Phase 2, the service on the small screen

`python -m space_lcd` from the test venv for 60 s (`timeout -s INT 60`), with Rob's yes, cycling
Cooler, Dial, Clock and Fan at 15 s each. `cooler = auto` picked the NZXT Kraken 2023 by
itself. A first run at brightness 40 was too dim to read; Rob asked for it brighter, so the
second run had brightness 100. The screen info reply read brightness 40 and orientation 0, so
`30 02 01 64 00 00 01 00` was sent. The cooler stays at 100 afterwards. The log showed no
warnings or reconnects. Rob: all four screens looked right ("looks awesome"). Brightness 40
is too dim on this screen; 100 is readable.

## 30/10/2026: Kraken 2023 Phase 1, first pictures and frame rate

`tools/probe_kraken_frame.py --send` with Rob's yes. The screen info reply read brightness
18 and orientation 0, so `30 02 01 28 00 00 01 00` set brightness 40 (it stays at 40 on the
cooler afterwards). Every command had a reply; no warnings.

| Send | Measured |
|---|---|
| Test picture, sent twice (first after open) | 0.082 s for both |
| 20 numbered full frames, 240 by 240 RGB565, 115,200 B each | 0.816 s: 0.041 s a frame, 24.5 frames a second |

Against the SPACE (0.33 s a full frame, JOURNAL 10/10/2026) that is 8 times faster per frame,
about 2.8 MB a second on the wire, so the whole frame only design costs nothing here.
Kill criterion (over a second a frame) is far off. Rob: the dial appeared, the numbers
counted up to 20 the right way up, and 20 stayed on screen after the tool exited.

## 30/10/2026: Kraken 2023 Phase 0, firmware version

Second PC (Linux Mint 22.3). `tools/probe_kraken_fw.py --send` with Rob's yes, after the
udev rule. Sent `10 01` and 62 zero bytes to `/dev/hidraw4`. The first report back was the
answer, no status reports skipped:
`11 01 b6 83 f3 fb 0b 58 ee 5c ae 0f a0 1f 0e 30 01 02 00 00 ...` (zeros to 64 bytes).
Bytes 0x11 to 0x13: `02 00 00`, firmware **2.0.0**, matching bcdDevice 2.00. The agreed
firmware 2.x picture path applies. Bytes 2 to 13 are the start of the USB serial number.

## 11/10/2026: Smart picture and Forza Horizon 6

Rob ran Forza Horizon 6 through Steam (Proton Experimental, Steam Linux Runtime 4). The
launcher line `reaper SteamLaunch AppId=2483190` was there, so the game was found; the
miss was the artwork. Steam keeps this game's files in hashed subfolders of
`appcache/librarycache/2483190/` and names two of them `library_header.jpg` and
`library_capsule.jpg`. 1.0.19 searches one level of subfolders and both names. The
environment check added in 1.0.18 (SteamAppId) stays as a second way to find the id.
Process scan: about 50 ms every 2 s with environments read.
The Steam option had been switched off in the Rules tab (rules_steam = no, written by the
panel at 13:58:15); once ticked at 13:59:28 the service showed Forza's artwork. Rob: working.

## 10/10/2026: stored video fan, first service runs (1.0.16)

- 21:50:11 start: all nine loops uploaded (176,459 to 242,815 B, 1.9 MB) between 21:50:12
  and 21:50:19, then `spacelcd_fan1_slow_blue.mp4` played. Later starts found the loops
  by size and played within about a second, with no uploads.
- QUERY_STATUS at shutdown read `renderCnt:979`, then `1072`: the screen counting the
  video frames it played itself.
- 22:00 a scheduled burn-in refresh took over: the video stopped and full frames ran at
  27 a minute. No warnings, no fall back to streaming.
- Loop switching under real load not yet seen: the CPU stayed in the slow, blue band.

## 10/10/2026: device video switching

`tools/probe_switch.py`, two loops (slow blue 223,588 B, fast red 242,815 B).
- Run 1: play A, then play B with no stop: B answered `play_video_success` in 0.00 s but
  Rob saw "just blue" throughout. Uploads took 30.1 s each only because the reader waited
  for 1,024 bytes after the 23 byte `file_rev_done.img_show_`.
- Run 2 (reader fixed): uploads 0.18 and 0.19 s.
- Run 3: play B; stop (0.29 s); play A; stop; play B. Rob: "red blue red". So a stop
  between videos is needed and is enough; play answers in 0.02 s.

## 10/10/2026: device video stage 3, a short video plays smoothly

223,588 byte fan loop (3 s, 24 fps, 480 by 480, H.264 High, yuv420p, no audio). Upload
via `probe_video.py --send` (Rob's run, stopped by him after the upload). Then
`--play-existing`: PLAY_VIDEO `78 ef 69 00 00 00 1d 01 00 00` + path answered
`play_video_success` at once, no 0x82 sent. Rob: "plays nicely". STOP_VIDEO then
STOP_MEDIA gave `media_stop` on the first poll. Deleted; size 0. Switching time between
two stored videos not yet measured.

## 10/10/2026: device video stage 2, one tiny upload

`tools/probe_upload.py --send`: every step answered as expected. `create_success` came at
once, `file_rev_done` plus `img_show_` after the single 250 byte block, size 67, deleted,
size 0. LIST_DIR created the missing `/mnt/SDCARD/img/`. The card's video folder holds
the six stock videos (27.5 MB). Delete gives no reply; verify by size, as planned.

## 10/10/2026: device video stage 1, read only storage probe

`tools/probe_storage.py`, service stopped, screen awake on the first ttyACM node.
HELLO `chs_5inch.dev1_rom1.88`. GET_STORAGE_INFO `2688-184-2504-122880-28182-94698` KiB:
flash 2,688 total, 184 used, 2,504 free; card 122,880 total, 28,182 used, 94,698 free.
GET_FILE_SIZE `/root/img/t.png` gave `0`. All three replied at once; nothing was written.

## 10/10/2026: the fan judders; the screen was rejecting partial updates

- 1.0.10, fan theme: about 6 to 8 partial updates a second sent (62,000 px, 0.07 to 0.11 s
  each) plus 13 to 25 full frames a minute, yet the screen moved about once every 1 to 2 s.
- 1.0.11 read the QUERY_STATUS replies: `needReSend:0|renderCnt:0` at start, then the
  screen asked for a full frame 114 times in a minute out of 120 partial updates.
- Cause, from the bezel reverse engineering (section 9.2): the partial update sequence
  number must restart at 0 after each full frame; upstream never resets it.
- 1.0.12 resets it: the screen asked for a full frame 12 times in a minute out of 349
  partial updates (was 114 of 120).
- 1.0.13, big fan, 14:55 to 15:06: 304 to 313 partial updates a minute (5.2 frames a
  second), each about 158,000 px and 0.174 s; no full frame requests at all in eleven
  minutes; one full frame every five minutes (the pixel shift). Service CPU 8.5 per cent.

## 10/10/2026: Phase 0, how fast the screen can be fed

From the service's own `timing:` lines, 14:17 to 14:30, version 1.0.8, normal themes then
a Clear burn-in run.

| Send | Measured |
|---|---|
| Full frame (480 by 480, 4 bytes a pixel) | 0.33 s each, every one of about 110 samples; 3.0 frames a second |
| Partial update | about 0.001 s fixed cost plus 918,000 to 932,000 pixels a second (fit over 500 updates of 2,000 to 39,000 px) |
| Typical stats tick | 6,000 to 38,000 px, 0.007 to 0.043 s |

Both work out at about 2.8 MB a second on the wire, so the link (or the screen's
handling of it), not the pixel format, sets the pace.

Drawing on this PC, measured with the same code and no screen: 14 to 15 ms a frame for
the drawn themes, 22 ms for the picture theme; working out the changed rectangle 0.4 ms.

What it means for the plan:

- Full screen animation: about 3 frames a second at best. Not video.
- A 240 by 240 animated area: about 0.06 s to send plus about 0.015 s to draw, so 10 to 13
  frames a second is realistic. The kill criterion (over 0.5 s for 240 by 240) is nowhere
  near; the spinning fan goes ahead.
- A one off "Display returned invalid or unsupported ID" warning at 14:09:31 was followed
  by a normal start a second later. Upstream retries on its own; no action.
