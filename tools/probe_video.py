#!/usr/bin/env python3
"""
<summary>
Stage 3 of docs/device-video-plan.md: one short fan video uploaded to the screen's card
storage, played on the screen, stopped, and deleted again.
</summary>
<remarks>
  --make FILE   draws the test video on this PC with ffmpeg: the fan theme, blue, one full
                turn in 3 seconds at 24 frames a second, 480 by 480, H.264, no audio.
                Touches no hardware.
  (no flag)     prints every packet --send would use; sends nothing.
  --play-existing FILE
                when an earlier --send was stopped after uploading: checks the file on the
                screen has FILE's size, then plays, stops and deletes it (steps 7 to 9).
  --send FILE   on the screen, stopping at the first unexpected answer:
                1 HELLO; 2 STOP_VIDEO, STOP_MEDIA; 3 size of /mnt/SDCARD/video/fantest.mp4,
                expect 0 (never overwrite); 4 UPLOAD_FILE header, expect create_success;
                5 the data phase in 249 byte pieces each followed by 00, sent whole in one
                write and never cut short, expect file_rev_done; 6 size must match;
                7 PLAY_VIDEO with loop on, expect play_video_success; watch for 15 s;
                8 STOP_VIDEO, STOP_MEDIA, expect media_stop; 9 delete, size 0.
The vendor app sends 0x82 before playing on small screens; what it does is unknown, so
this does not send it, nor 0x84, 0x81, or any OPTIONS write. Files over 1 MB are refused.

Run --send only on Rob's yes, with the Space LCD service stopped (it refuses to run
otherwise and opens the port exclusively; see probe_common.py) and the screen awake.
</remarks>
"""

import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TARGET = "/mnt/SDCARD/video/fantest.mp4"
"""<summary>The only path this script writes. Lowercase, in the video folder, nothing like "update".</summary>"""

MAX_BYTES = 1024 * 1024
"""<summary>Stage 3 refuses anything bigger: the screen's upload limit is unknown.</summary>"""

FPS, SECONDS = 24, 3


def make(out: Path) -> None:
    """
    <summary>Draws the fan video with the app's own fan renderer and encodes it with ffmpeg.</summary>
    <remarks>
    One full turn over the loop, so it repeats without a jump: 120 degrees a second, 5
    degrees a frame. The fan's step function is replaced here only, to set each angle exactly.
    </remarks>
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from space_lcd import render, themes
    from space_lcd.sensors import Stats
    render.set_font_root(Path.home() / ".local/share/space-lcd/upstream")
    frames = FPS * SECONDS
    with tempfile.TemporaryDirectory() as folder:
        for i in range(frames):
            angle = 360.0 * i / frames
            themes.fan_step = lambda load, now, a=angle: (a, 0.0)
            render.draw_frame(Stats(cpu_temp=40, cpu_load=20), theme="fan").save(Path(folder) / "f{:03d}.png".format(i))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", str(Path(folder) / "f%03d.png"),
                        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
                        "-f", "mp4", str(out)], check=True)
    print("made {} ({} bytes)".format(out, out.stat().st_size))


def packet(op: int, path: str = "", tail: bytes = b"", flag: int = 0) -> bytes:
    """<summary>One 250 byte command: op ef 69, BE32 path length, flag, 00 00, path, tail, zero padding.</summary>"""
    encoded = path.encode("ascii")
    body = bytes((op, 0xEF, 0x69)) + struct.pack(">I", len(encoded) if encoded else 1) + bytes((flag, 0, 0))
    body += encoded + tail
    assert len(body) <= 250
    return body + bytes(250 - len(body))


def data_phase(content: bytes) -> bytes:
    """<summary>The file cut into 249 byte pieces, each followed by one 00; the last block padded to 250.</summary>"""
    out = bytearray()
    for start in range(0, len(content), 249):
        piece = content[start:start + 249]
        out += piece + b"\x00" if len(piece) == 249 else piece + bytes(250 - len(piece))
    return bytes(out)


HELLO = bytes.fromhex("01 ef 69 00 00 00 01 00 00 00 c5 d3") + bytes(238)
STOP_VIDEO = packet(0x79)
STOP_MEDIA = packet(0x96)


def show(label: str, data: bytes) -> None:
    """<summary>Prints bytes as hex (trailing zeros trimmed, first 150 characters) and as text.</summary>"""
    trimmed = data.rstrip(b"\x00")
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in trimmed)
    print("  {:<5} {:>7} B: {}".format(label, len(data), trimmed.hex(" ")[:150]))
    if text:
        print("        text: {}".format(text[:120]))


def ask(link, name: str, data: bytes, size: int, wait: float = 1.0) -> bytes:
    """<summary>Sends one packet (or a whole data phase), reads up to size bytes within wait seconds.</summary>"""
    print(name)
    link.reset_input_buffer()
    link.write(data)
    link.flush()
    show("sent", data)
    link.timeout = wait
    reply = link.read(size)
    show("reply", reply)
    return reply


def stop(reason: str) -> int:
    """<summary>Prints why the run stopped. Nothing further is sent.</summary>"""
    print("\nSTOPPED: {}. Nothing more was sent.".format(reason))
    return 1


def stop_media(link) -> bool:
    """<summary>STOP_VIDEO, 200 ms, then STOP_MEDIA polled up to five times for media_stop.</summary>"""
    ask(link, "STOP_VIDEO", STOP_VIDEO, 0, 0.2)
    time.sleep(0.2)
    for attempt in range(5):
        if b"media_stop" in ask(link, "STOP_MEDIA ({})".format(attempt + 1), STOP_MEDIA, 1024):
            return True
        time.sleep(0.4)
    return False


def play_existing(link, size: int) -> int:
    """
    <summary>Plays, stops and deletes a test video already on the screen, without uploading.</summary>
    <param name="size">The size the file must have, so nothing but our own test video is played or deleted.</param>
    <remarks>For when an earlier run was stopped after the upload and left the file behind.</remarks>
    """
    if b"chs_" not in ask(link, "1 HELLO", HELLO, 23):
        return stop("no HELLO answer")
    if not stop_media(link):
        return stop("no media_stop")
    found = ask(link, "3 GET_FILE_SIZE (expect {})".format(size), packet(0x6E, TARGET), 1024, 2.0).strip(b"\x00 ")
    if found != str(size).encode():
        return stop("the file there is not our test video")
    played = ask(link, "7 PLAY_VIDEO (loop)", packet(0x78, TARGET, flag=1), 1024, 6.0)
    if b"play_video_success" in played:
        print("\n  >>> playing: watch the screen for 15 seconds <<<\n")
        time.sleep(15)
    else:
        print("  (no play_video_success)")
    stop_media(link)
    ask(link, "9a DELETE_FILE", packet(0x66, TARGET), 1024, 2.0)
    after = ask(link, "9b GET_FILE_SIZE (expect 0)", packet(0x6E, TARGET), 1024, 2.0).strip(b"\x00 ")
    print("\nRESULT: play reply {!r}, after delete {}".format(played.strip(b"\x00")[:40], after.decode("ascii", "replace")))
    return 0 if after == b"0" else 1


def run(link, content: bytes) -> int:
    """<summary>The nine steps, stopping at the first unexpected answer.</summary>"""
    if b"chs_" not in ask(link, "1 HELLO", HELLO, 23):
        return stop("no HELLO answer")
    if not stop_media(link):
        return stop("no media_stop")
    if ask(link, "3 GET_FILE_SIZE (expect 0)", packet(0x6E, TARGET), 1024, 2.0).strip(b"\x00 ") != b"0":
        return stop("a file already exists at " + TARGET)
    if b"create_success" not in ask(link, "4 UPLOAD_FILE header", packet(0x6F, TARGET, struct.pack("<I", len(content))), 1024, 3.0):
        return stop("no create_success; no data was sent")
    started = time.monotonic()
    done = ask(link, "5 data phase", data_phase(content), 1024, 60.0)
    print("  data phase took {:.2f} s".format(time.monotonic() - started))
    if b"file_rev_done" not in done:
        print("  (no file_rev_done; checking the size anyway)")
    size = ask(link, "6 GET_FILE_SIZE (expect {})".format(len(content)), packet(0x6E, TARGET), 1024, 2.0).strip(b"\x00 ")
    if size != str(len(content)).encode():
        print("  size does not match; not playing it")
    else:
        played = ask(link, "7 PLAY_VIDEO (loop)", packet(0x78, TARGET, flag=1), 1024, 6.0)
        if b"play_video_success" in played:
            print("\n  >>> playing: watch the screen for 15 seconds <<<\n")
            time.sleep(15)
        else:
            print("  (no play_video_success)")
        stop_media(link)
    ask(link, "9a DELETE_FILE", packet(0x66, TARGET), 1024, 2.0)
    after = ask(link, "9b GET_FILE_SIZE (expect 0)", packet(0x6E, TARGET), 1024, 2.0).strip(b"\x00 ")
    print("\nRESULT: uploaded size {}, after delete {}".format(size.decode("ascii", "replace"), after.decode("ascii", "replace")))
    return 0 if after == b"0" else 1


def main(argv) -> int:
    """<summary>--make draws the video; --send runs on the screen; otherwise prints the packets.</summary>"""
    if argv[:1] == ["--make"] and len(argv) == 2:
        make(Path(argv[1]))
        return 0
    if argv[:1] in (["--send"], ["--play-existing"]) and len(argv) == 2:
        content = Path(argv[1]).read_bytes()
        if len(content) > MAX_BYTES:
            return stop("video is {} bytes, over the 1 MB stage 3 limit".format(len(content)))
        from probe_common import find_screen, open_screen
        device = find_screen()
        if device is None:
            return 1
        print("Screen: {}\nVideo: {} bytes\n".format(device, len(content)))
        with open_screen(device) as link:
            if argv[0] == "--play-existing":
                return play_existing(link, len(content))
            return run(link, content)
    print("Dry run: packets --send would use (file size shown as 0x00012345). Nothing is sent.\n")
    for name, data in (("GET_FILE_SIZE", packet(0x6E, TARGET)), ("UPLOAD_FILE", packet(0x6F, TARGET, struct.pack("<I", 0x12345))),
                       ("PLAY_VIDEO loop", packet(0x78, TARGET, flag=1)), ("STOP_VIDEO", STOP_VIDEO),
                       ("STOP_MEDIA", STOP_MEDIA), ("DELETE_FILE", packet(0x66, TARGET))):
        print(name)
        show("send", data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
