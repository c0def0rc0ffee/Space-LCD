# Plan: smooth video stored on the screen (not started)

Written 10th October 2026 from read only research. Nothing in this file has been sent to
the screen. Each stage needs Rob's explicit yes, given after he has read that stage.

## Why

Frames sent from the PC top out at about 5 a second for a full screen fan (JOURNAL.md).
The Windows app gets smooth video by uploading a video file into the screen's own
storage and telling the screen to play it; the PC then sends almost nothing.

## What is known, and how well

Main source: the bezel project's reverse engineering of the vendor app
(`docs/reverse-engineering/protocol-turing-rev-c.md`, sections 2, 13, 14, 15 and 19).

| Fact | Confidence |
|---|---|
| Commands: storage info 0x64, list folder 0x65 (creates it if missing), file size 0x6E, upload 0x6F plus a data phase, delete 0x66, play 0x78, stop 0x79 and 0x96 | Vendor app decompiled; uploads proven on an 8.8 inch |
| Small screens keep files in `/root/img/` and `/root/video/`; a card, if any, at `/mnt/SDCARD/` | Seen in a 5 inch capture; one person claims it on a 2.1 inch |
| Video: MP4, H.264, exactly 480 by 480, no audio, 24 or 30 frames a second | Vendor encoder settings; 2.1 inch claim |
| Switching between two stored videos: about 0.55 s on the 8.8 inch; over 2 s on small screens if the vendor's unexplained 0x82 step is needed | 8.8 inch measured; small screens unknown |
| Upload, play with loop, stop and delete proven on this 2.1 inch, ROM 1.88, without 0x82 | Stages 2 and 3, 10/10/2026 |
| Storage on this screen: 2.6 MB flash, 120 MB card storage with 92.5 MB free (stage 1) | Measured 10/10/2026 |
| Its largest safe upload | Unknown |

## Known ways it goes wrong

| Trigger | Result | Recovery | Case open? |
|---|---|---|---|
| Upload stopped part way | Leftover bytes land in the next upload, or the screen stops answering for about 10 s | Reconnect, check the file size, delete the partial file | No |
| Padding sent to finish a stopped upload | 8.8 inch hung for over 20 minutes | USB replug | **Yes** |
| Upload over about 28 MB (8.8 inch; the 2.1 inch limit is unknown and may be lower) | Hang | 8.8 inch: a microcontroller restart over the second serial port; untested on 2.1 inch | Possibly |
| RESTART 0x84 on a 2.1 inch | Stuck on the stock message | Power off | **Yes** |
| A file named `/update.app` then a reboot | Firmware update from that file | | Risk of a dead screen |

On this cooler a replug means opening the case, so the plan avoids every trigger above.

## Rules for any of this, fixed before it starts

- Files only ever go into `/root/img/` or `/root/video/` (or the card's `img` and `video`
  folders, if a card exists), with lowercase names of letters, digits, `_`, `.` and `-`.
  Never any other path, never a name containing `update`.
- Never send 0x84 (restart), 0x82 (unknown) or 0x81.
- Uploads are never interrupted and never padded. Each is checked with the file size
  command afterwards.
- First uploads are tiny; videos stay under 5 MB until the screen's limit is known.
- Storage is checked before each upload; nothing is ever deleted automatically.
- The background service is stopped first, so only one program talks to the screen.

## Stages

1. **DONE 10/10/2026.** Replies: HELLO `chs_5inch.dev1_rom1.88`; storage
   `2688-184-2504-122880-28182-94698` (KiB): internal flash 2,688 total, 2,504 free; card
   storage 122,880 total (120 MB), 28,182 used, 94,698 free (92.5 MB); `/root/img/t.png`
   size `0`. So videos go to `/mnt/SDCARD/video/`. The 27.5 MB in use is very likely the
   stock Windows picture and animation. Original wording of the stage:
   **Look, don't touch.** Three queries only: HELLO (already sent at every start),
   storage info 0x64, and file size 0x6E for `/root/img/t.png` (expected `0`). Writes
   nothing. Answers: is there storage, how much, is there a card. Script:
   `tools/probe_storage.py`, which prints every byte it sends and receives.
2. **DONE 10/10/2026.** `tools/probe_upload.py --send`: HELLO, STOP_VIDEO, STOP_MEDIA
   (`media_stop`), LIST_DIR `/mnt/SDCARD/img/` (`result:nodir-createdone`: the folder did
   not exist and now does, empty), LIST_DIR `/mnt/SDCARD/video/` (six stock videos:
   `SPACE_01.mp4`, `SPACE_03.mp4`, `SPACE_03_270.mp4`, a `Hamster_CPU` video and two
   converted GIFs, all left alone), size 0, `create_success`,
   `file_rev_done` + `img_show_`, size 67, delete (no reply), size 0. Uploads work on
   ROM 1.88. Original wording: **One tiny picture.** Upload a 67 byte, 1 by 1 pixel PNG to `/root/img/t.png` in a
   single block, check its size reads 67, delete it, check it reads 0.
3. **DONE 10/10/2026.** A 223,588 byte, 3 second, 480 by 480 H.264 fan loop at 24 frames a
   second. Rob's own first run was stopped after the upload, leaving the file; the next run
   found it by its size and stopped at the guard. `--play-existing` then: `media_stop`, size
   223,588, PLAY_VIDEO with loop on answered `play_video_success` without the vendor's 0x82,
   Rob: "plays nicely"; STOP gave `media_stop`; deleted, size 0. Original wording:
   **One short video.** A 2 second, 480 by 480 test clip under 1 MB to `/root/video/`;
   play it; stop it; check the service's normal frames still work afterwards; delete it.
   Switching, measured 10/10/2026 (`tools/probe_switch.py`): PLAY of a second video while
   one plays answers success but the screen keeps showing the first; STOP (STOP_VIDEO,
   200 ms, STOP_MEDIA) then PLAY switches cleanly (Rob saw red, blue, red). Play answers
   in about 0.02 s, stop in about 0.3 s, a 240 KB upload in about 0.2 s once the reader
   stops at "file_rev_done" instead of waiting for 1,024 bytes.
4. **BUILT 1.0.16 and running (10/10/2026): nine loops uploaded by the service on its
   first start, playing; switching under load still to be seen.** Rob's choices: nine
   loops, stop and show the stopped frame on exit. **Fan loops.** A few short pre-rendered fan loops (slow and blue to fast and red),
   switched as CPU load and temperature change. Only if stages 1 to 3 are clean and the
   switching delay measured in stage 3 is acceptable.

Each stage is its own decision. Stage 1 first, and only on a yes.
