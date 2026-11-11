# Recovery: ID-COOLING SPACE LCD screen (Turing 2.1 inch, revision C)

How to get the screen back to a known state. Read this before sending it anything new.

## What this program sends

Only these upstream commands, all from `library/lcd/lcd_comm_rev_c.py` at the pinned commit:

| Step | Command | First bytes |
|---|---|---|
| Wake | opens the sleeping port (`1a86:ca21`) at 115200 baud, no data | none |
| Hello | HELLO, reads a 23 byte ID | `01 ef 69 00 00 00 01 00 00 00 c5 d3` |
| Screen on | STOP_VIDEO, then STOP_MEDIA, reads 1024 bytes | `79 ef 69 ...`, `96 ef 69 ...` |
| Options | OPTIONS: default start mode, no flip, sleep timer off | `7d ef 69 00 00 00 05 00 00 00 2d` |
| Brightness | SET_BRIGHTNESS with one byte, 0 to 255, sent after OPTIONS (from 1.0.5) | `7b ef 69 00 00 00 01 00 00 00` |
| First frame | PRE_UPDATE_BITMAP, START_DISPLAY_BITMAP, DISPLAY_BITMAP (2 inch), pixel data, QUERY_STATUS | `86 ef 69`, `2c`, `c8 ef 69 00 0e 10`, `cf ef 69` |
| Later frames | UPDATE_BITMAP for the changed rectangle, QUERY_STATUS. Header bytes 10 to 13 are a sequence number that restarts at 0 after every full frame (from 1.0.12, as the vendor app does; upstream never reset it) | `cc ef 69 00`, `cf ef 69` |
| Theme change | when over three quarters of the screen changed, even after splitting it into 96 pixel tiles: the full frame sequence above | as first frame |
| Scattered changes | from 1.0.13, changes far apart go as several UPDATE_BITMAP partials, one per changed tile, instead of one large rectangle or a full frame | `cc ef 69 00`, `cf ef 69` |
| Resend request | when a QUERY_STATUS reply says `needReSend:1`, the next frame is sent whole (from 1.0.11; upstream ignored the reply) | as first frame |
| Fan theme | partial updates of the fan, which fills the screen: about 130,000 pixels a frame in tiles | `cc ef 69 00`, `cf ef 69` |
| Burn-in refresh | a full frame every 2 seconds: solid colours and random noise, until the end time | as first frame |
| Pixel shift | one full frame every 5 minutes when the picture moves two pixels | as first frame |
| Brightness change | SET_BRIGHTNESS while running, only when the wanted level differs from the last one sent: a new setting, or a refresh with its own brightness starting or ending (and before the "stopped" frame) | `7b ef 69 00 00 00 01 00 00 00` |

It never sends RESTART (`84 ef 69`) or TURNOFF (`83 ef 69 00 00 00 01`). Every command is
padded to a multiple of 250 bytes, as upstream does.

Nothing is written to the screen's own storage. The GIF or video uploaded with the Windows
software is stopped by STOP_VIDEO and STOP_MEDIA, not deleted.

## Stored fan and hamster videos (from 1.0.16; hamster from 1.0.21)

With the fan screen on and "Play the fan as smooth video" ticked (the default), the
service also uses the screen's own storage, through `src/space_lcd/storage.py` only:

| Step | Command | First bytes |
|---|---|---|
| Stop anything playing | STOP_VIDEO, 200 ms, STOP_MEDIA until `media_stop` | `79 ef 69 ...`, `96 ef 69 ...` |
| Check a loop | GET_FILE_SIZE of `/mnt/SDCARD/video/spacelcd_fan2_<colours>_<speed>_<step>.mp4` (from 1.0.20; `<colours>` is six hex characters naming the fan colours; 1.0.16 to 1.0.19 used `spacelcd_fan1_<speed>_<colour>.mp4`, which stay on the screen) | `6e ef 69 00 00 00 <len> 00 00 00` + path |
| Upload a missing loop, once | UPLOAD_FILE header (path, then size little endian), the whole file as 249 byte pieces each followed by `00`, then GET_FILE_SIZE must match | `6f ef 69 ...` |
| Hamster loops (from 1.0.21) | the same steps for `spacelcd_ham1_<colours>_<speed>.mp4`, three files; from 1.0.27 `spacelcd_ham2_...` and from 1.0.29 `spacelcd_ham3_...` (turned upright, see below); earlier files stay on the screen | as above |
| Glitch clips (from 1.0.28) | the same steps for `spacelcd_gl1_<12 hex>.mp4` (from 1.0.29 `spacelcd_gl2_...`, turned upright), one per pair of pictures in the slideshow folder, each uploaded once while a picture holds; played with loop on and stopped after 1.7 s, inside the clip's 3 s | as above |
| Zoom clips (from 1.0.30) | the same steps for `spacelcd_zm1_<12 hex>.mp4`, one per picture on the Zoom tab, all uploaded together after one STOP once every one is made; each at most 95 per cent of the 2 MB fence; played with loop on, switched with STOP then PLAY at each change | as above |
| Play a loop | PLAY_VIDEO with loop on; a switch is always STOP first, at most every 10 s | `78 ef 69 00 00 00 <len> 01 00 00` + path |
| Leave the fan screen or stop | STOP_VIDEO, STOP_MEDIA, then a full frame (also after a failed upload, since the STOP before it changed the screen) | as above |

The hand run probe tools in `tools/` (never the service) also send these, each only on
Rob's yes, with the service stopped and the port opened exclusively:

| Tool | Command | First bytes |
|---|---|---|
| probe_storage | GET_STORAGE_INFO: flash and card sizes, free space | `64 ef 69 00 00 00 01` |
| probe_upload | LIST_DIR of `/mnt/SDCARD/img/` and `/mnt/SDCARD/video/` (creates the folder if missing) | `65 ef 69 00 00 00 <len> 00 00 00` + path |
| probe_upload, probe_video, probe_switch | DELETE_FILE of the probe's own test file only, then GET_FILE_SIZE must read 0 | `66 ef 69 00 00 00 <len> 00 00 00` + path |

Fences: only `spacelcd_` names in `/mnt/SDCARD/video/`, never a name containing
`update`, 2 MB per file, never overwriting (a loop on the screen with the wrong size stops the video mode and is left for
a human), never deleting automatically, never 0x84, 0x82 or 0x81. Any failure puts the fan
back to streamed frames for the rest of the session; the journal says why.

The nine loops use about 2 MB of the card's 92 MB free. The six stock videos in the same
folder are never touched. To remove our loops by hand later, delete only names starting
`spacelcd_`, using the storage module's `delete()`, with the service stopped.

## Getting back to a known state

1. Stop the service, with the Stop button in the control panel or
   `systemctl --user stop space-lcd`. The screen keeps the last frame, which reads
   "stopped".
2. If the screen is frozen or blank, power cycle it. It takes its power from the pump's
   USB header, so shut the PC down fully, not restart, and wait ten seconds.
3. After a cold start the screen sleeps as `1a86:ca21` and shows its stored animation.

Unverified: whether the stored animation comes back without a power cycle once this program
stops. Not tested yet.

## Timing log

From 1.0.6 the service writes a `timing:` line to the journal once a minute: full frames
and partial updates sent, their average time, and a fitted overhead plus pixels per second
for partial updates. It sends nothing extra; it times what is sent anyway.

## Brightness

Until 1.0.4 the screen looked dim even at 100%, dimmer than the Windows app. A reverse
engineering write up of the vendor software
([bezel, section 6.2](https://github.com/slipalison/bezel/blob/main/docs/reverse-engineering/protocol-turing-rev-c.md))
reads byte 10 of OPTIONS as a brightness value; upstream always sends `2d` (45 of 255)
there, and sent OPTIONS after SET_BRIGHTNESS. From 1.0.5 OPTIONS goes first and
SET_BRIGHTNESS last. Confirmed on this cooler on 10th October 2026: with 1.0.5 the screen
is clearly brighter. Do not move SET_BRIGHTNESS back in front of OPTIONS.

## Known upstream issue

A 5 inch revision C screen has been reported freezing on Linux after one frame, because the
`cdc_acm` serial path jams
([upstream issue 1075](https://github.com/mathoudebine/turing-smart-screen-python/issues/1075)).
Tested on this cooler on 1st October 2026: a 60 second run with partial updates every
2 seconds kept updating correctly, so the 2.1 inch is not affected on this machine. If it
happens here the symptom is one frame followed by a
screen error in the journal every ten seconds. The fix proposed there is a libusb transport;
do not try it without reading that issue first.

# Recovery: NZXT Kraken 2023 screen (1.54 inch, 240 by 240, USB 1e71:300e)

Added 30/10/2026 for the second cooler. Firmware read 2.0.0 on 30/10/2026
(`tools/probe_kraken_fw.py`). The protocol is liquidctl's (`liquidctl/driver/kraken3.py`,
class `KrakenZ3`, commit 292dc56); only its firmware 2.x picture path is used.

## What this program sends

HID reports go to the cooler's hidraw node, 64 bytes each, padded with zeros, first byte
the report number. Picture data goes to USB bulk endpoint 0x02 on interface 0.

| Step | Where | Bytes |
|---|---|---|
| Firmware version (Phase 0 tool only) | HID | `10 01`, reply `11 01`, version in bytes 0x11 to 0x13 |
| Screen info | HID | `30 01`, reply `31 01`: brightness at byte 0x18, orientation at 0x1a |
| Brightness, only when it differs | HID | `30 02 01 <level 0 to 100> 00 00 01 <orientation as read>` |
| Start picture | HID | `36 01 00 01 06` |
| Picture header | bulk | `12 fa 01 e8 ab cd ef 98 76 54 32 10 06 00 00 00 00 c2 01 00` |
| Picture | bulk | 115,200 bytes, RGB565 high byte first, turned to the orientation read |
| End picture | HID | `36 02` |
| Recovery only, `--liquid` | HID | `38 01 02 00`: NZXT's own coolant temperature screen |

The first picture after opening is sent twice, as liquidctl does on this firmware. Status
reports the cooler sends for the kernel driver (`75 ...`) are read and thrown away.

It never sends the firmware 1.x storage commands: no bucket query (`30 04`), delete
(`32 02`), setup (`32 01`) or switch to a bucket (`38 01 04`). Nothing is written to the
cooler's storage. It never detaches the `nzxt-kraken3` kernel driver and never sets a USB
configuration; if a driver ever holds interface 0, it refuses to start.

## Getting back to a known state

1. Stop whatever is sending (the probe tool, or later the service).
2. `python3 tools/probe_kraken_frame.py --liquid` puts back NZXT's coolant screen.
3. If the cooler does not answer at all, unplug its USB lead from the motherboard header,
   or power the PC off at the wall for 10 seconds. The pump runs from SATA power and keeps
   cooling whatever the screen does.

Confirmed 30/10/2026: the last picture stays on screen once this program stops; the
cooler does not go back to its own screen by itself. Use --liquid for that.
