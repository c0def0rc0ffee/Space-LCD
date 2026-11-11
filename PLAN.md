# Plan: a second cooler, the NZXT Kraken 2023

Written 30/10/2026. Branch `feature/kraken-2023`.

**Agreed by Rob 30/10/2026, all four decisions as written below (do not relitigate). Phase 0 started.**

## The cooler

Found on the second PC by its USB IDs only (sysfs, nothing sent): NZXT Kraken 2023, the
standard model, USB `1e71:300e`, product string "NZXT Kraken Base", bcdDevice 2.00. Its
screen is round, 1.54 inch, **240 by 240** (the SPACE is 2.1 inch, 480 by 480). The kernel
driver `nzxt-kraken3` already reads it as hwmon `kraken2023`: pump speed, fan speed and
coolant temperature, which the SPACE has never been able to give us.

The protocol is the published one in liquidctl (`liquidctl/driver/kraken3.py`, `KrakenZ3`,
read at commit 292dc56, 16/06/2026). Two USB interfaces: HID (interface 1, commands and
replies) and a vendor bulk endpoint 0x02 (interface 0, picture data). Picture upload differs
by firmware:

| Firmware | How a picture goes up | Writes to the cooler's storage |
|---|---|---|
| 2.x | HID `36 01 00 01 06`, bulk header (12 fixed bytes, `06 00 00 00`, length as 4 bytes little endian), 115,200 bytes of RGB565 big endian, HID `36 02` | No |
| 1.x | Query 16 storage buckets, delete one, set it up, upload into it, switch to it | Yes: deletes and writes |

bcdDevice 2.00 suggests firmware 2.x but is not proof. **Only the 2.x path is in this
plan.** If the firmware reads 1.x, work stops and the plan comes back to Rob; the bucket
path is never built without its own yes.

## What is uncertain

- The firmware version, until Phase 0 reads it.
- How fast frames go. 115 KB per frame over USB 2.0 high speed should be well under the
  SPACE's 0.33 s, but nobody has measured it here, and the Kraken takes whole frames only
  (no changed rectangles). Animated screens are rated after Phase 1 measures it.
- liquidctl sends each picture twice after start, with a comment that nobody knows why
  ("some soft of framebuffer swapping?"). Copied as is, and recorded.
- Brightness on the Kraken is one HID command that also carries the orientation, so the
  orientation is read first and sent back unchanged. Rotation stays in software, as now.

## Decisions to make (Rob)

1. One program for both coolers, chosen at start by USB ID, with `cooler = auto` in the
   settings to override. Name and settings file stay Space LCD (the key rule: shipped names
   never change).
2. Small screen layouts: each stats screen gets its own 240 pixel layout with fewer, larger
   figures, rather than the 480 layout shrunk (shrunk, the 20 pixel labels become 10 pixels
   on a 1.54 inch screen, which cannot be read).
3. A new **Cooler** screen for coolers that report it: coolant temperature, pump and fan
   speed. The SPACE never shows it.
4. On the Kraken, hide what it cannot do: the stored video option and its notes. The fan and
   hamster stay as streamed frames if Phase 1 shows the rate is good enough.

## Phases

| Phase | What | Touches the cooler how |
|---|---|---|
| 0 | DONE 30/10/2026: firmware 2.0.0, see JOURNAL.md. udev rule so Rob's user can open the Kraken's HID node (one sudo command for Rob). Read the firmware version: HID `10 01`, wait for the `11 01` reply. Shown to Rob byte for byte first | One read command |
| 1 | DONE 30/10/2026: 0.041 s a full frame, 24.5 a second; Rob confirmed the pictures, the right way up, last frame stays. `kraken.py`: open, show a frame, brightness, close, with the same shape as `panel.Panel`. One test picture (`--once`), timed. Recovery page in `docs/recovery.md` before it runs | Picture upload (fw 2.x path only), brightness |
| 2 | DONE 30/10/2026, Rob confirmed on the Kraken at brightness 100: all 13 screens drawn at 240, 480 frames proven unchanged. Device profiles: size, partial updates, stored video, cooler sensors. Rendering at 240 for the Kraken; small layouts for dial, twin, clock, minimal, image; the Cooler screen; network and system cut down to what fits | As Phase 1, every update |
| 3 | DONE 30/10/2026, Rob confirmed the real window. Control panel: a Cooler choice on the Screen tab (auto or by hand, saying what was found), shows the cooler found, previews at its real size, hides SPACE only options, screen list per cooler | None (the panel never talks to the cooler) |
| 4 | DONE 30/10/2026, Rob confirmed on the Kraken: about 15 frames a second. Fan and hamster streamed at the measured rate; picture screens and Smart picture at 240 | As Phase 1 |

Each phase ships as its own build and is tried on the Kraken before the next starts. The
SPACE must behave exactly as in 1.0.22; its tests stay green and a test pins its 480 frames
byte for byte.

## Kill criteria

- Firmware 1.x: stop, back to Rob.
- Any picture upload leaving the screen blank or stuck: stop, record it in
  `docs/recovery.md`. Recovery is NZXT's own "liquid" screen (HID `38 01 02 00`, proven in
  liquidctl), or unplugging the cooler's USB lead.
- A full frame taking over a second: animated screens are not offered on the Kraken.

## Not in this plan

- Pump and fan speed control (the kernel driver owns that; liquidctl or CoolerControl do it).
- The 1.x bucket upload, GIFs, the Elite (640 by 640) and other Kraken models.

---

# Earlier plan: more screens, animation and picture rules

Written 10/10/2026. No code for this milestone until this plan is agreed (type 08 plan gate).

## The hard limit: how fast the screen can be fed

The screen takes pictures over a USB serial link. A full 480 by 480 frame is about 920 KB
in the upstream format; the time it takes on this cooler has never been measured. Only
the changed rectangle is sent after the first frame, so a small animation in the middle of
the screen can run far faster than a full screen one.

The official Windows app gets smooth video by uploading the file into the screen's own
storage and letting the screen play it. That means writing to the device's flash, which
upstream has not implemented and which is the riskiest thing this project could do to the
hardware. It is in the backlog, not this plan.

So "video" here means animation drawn by this program and sent frame by frame, at
whatever rate the link allows. Phase 0 measures that rate before anything is designed
around a guess.

## Decisions (10/10/2026, do not relitigate)

- Plan agreed; start with Phase 0.
- Rotation: yes. Several chosen screens cycle, each for its own time; one screen stays possible.
- Picture rules priority: running Steam game, then running application, then temperature band.

## Phases

| Phase | What | Touches the screen how |
|---|---|---|
| 0 | DONE 10/10/2026 (1.0.6): full frame 0.33 s, partial updates about 920,000 px a second; see JOURNAL.md | No new commands; timing only |
| 1 | DONE 1.0.9, Rob confirmed cycling works. Engine: each screen sets its own update rate; optional rotation between several chosen screens, each for a set time | Same commands, sent more often |
| 2 | DONE 1.0.13, Rob confirmed it spins at about 5 frames a second. Fan: nothing but a big fan that spins faster with CPU load and shifts from blue to red with temperature | Partial updates of the fan area |
| 3 | DONE 1.0.14, Rob confirmed both look good. System info (CPU and GPU model, cores, memory, disk use, uptime, kernel) and Network (download and upload speed with a short history graph) | As now |
| 4 | BUILT 1.0.15, awaiting Rob's try. Pictures: a folder of images on a loop with one duration for all; a playlist of up to 5 images, each with its own duration (2 seconds, 2 minutes and so on) | Full frames on each change |
| 5 | DONE 1.0.19: Steam artwork confirmed on screen with Forza Horizon 6 (11/10/2026); app and temperature rules built, not yet tried by Rob. Picture rules: a picture per temperature band (for example under 50, 50 to 70, over 70); a picture per running application; the logo of the Steam game being played | Full frames on each change |

Each phase ships as its own build, is tried on the screen by Rob, and only then does the
next one start.

## How the Steam logo would work

Steam keeps artwork for every game in the library on this PC already
(`~/.local/share/Steam/appcache/librarycache/<app id>/`, which holds `logo.png`,
`header.jpg` and `library_600x900.jpg`). The running game is found from the Steam launch
process, which carries the game's app id. So no download is needed: nothing leaves the
machine and no Steam account access is involved. When no game is running the screen falls
back to a chosen theme.

## How animation fits the burn-in work

Animated screens help with burn-in by nature. The pixel shift still applies to everything
that is not animated. The Clear burn-in refresh takes priority over every screen.

## Backlog, not to be built without asking

- Playing real video files (MP4 and similar) by decoding them here with ffmpeg and sending
  frames. Possible, but at the rate Phase 0 finds, which may be a few frames a second.
- Stored video on the screen: stages 1 to 3 proven 10/10/2026, stage 4 (the fan) built
  in 1.0.16 with Rob's yes; see docs/device-video-plan.md. Playing other video files is
  still not built.
- The 1.1.0 question and the old install leftovers are separate open questions, not part
  of this plan. (Everything up to 1.0.21 was committed on 11/10/2026.)

## Kill criteria

- If Phase 0 shows a partial update of a 240 by 240 area takes over half a second, the fan
  becomes a slow, smooth "breathing" animation rather than a spinning one.
- If any phase makes the screen misbehave, it is turned off by default and recorded in
  `docs/recovery.md` before anything else is done.
