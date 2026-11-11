<!--
<summary>Space LCD usage guide: the control panel tabs and the features the README only summarises.</summary>
<remarks>Moved from the README with light edits; the README links each section here.</remarks>
-->

# Using Space LCD

Back to the [README](../README.md).

- [The control panel](#the-control-panel)
- [Pictures](#pictures)
- [Zoom](#zoom)
- [Smooth video on the SPACE](#smooth-video-on-the-space)
- [Smart picture](#smart-picture)
- [Cycling between screens](#cycling-between-screens)
- [Colours](#colours)
- [Burn-in](#burn-in)

## The control panel

Open **Space LCD** from the application menu. Click a screen to preview it; an animated one
plays in the preview, and only that one moves. Right click a screen and choose **Send to
device** to show it on the cooler (this turns the screen cycle off if it was on). Every
change saves at once and reaches the screen within one update while the service is
running, with no restart. The panel itself never talks to the screen, so it is safe to
open with the service stopped.

The panel lives in the system tray. The menu entry starts the tray icon and opens the
panel. Click the icon to open the panel, and click it again to close it; its menu has
**Open control panel**, **Close control panel** and **Quit tray icon**. Closing the panel
or quitting the tray leaves the background service as it was. To stop the tray starting
at login, remove **Space LCD tray icon** in System Settings, Autostart; installing again
will not bring it back.

**Start when the computer starts** starts the service at boot, before anyone logs in
(systemd lingering for your user, turned on by the same tick; your desktop may ask for
your password once). On the SPACE this also keeps short the time its own stock picture
shows, since that plays whenever this service is not driving the screen.

## Pictures

The **Pictures** tab sets:

- the picture for **Your picture**;
- a folder and a time for the **Slideshow** and the **Glitch slideshow**;
- up to five pictures for the **Playlist**, each for its own time (2 seconds, 2 minutes
  and so on);
- **Behind the fan**, the picture for Fan over a picture. Choosing it previews that screen
  without sending it.

Choosing any of the others switches to its screen. Each change of picture takes about a
third of a second to reach the screen.

**Picture boost**, at the top of the tab, sets brightness and contrast for every picture a
screen shows: your picture, the fan's, both slideshows, the playlist, the zoom, the Smart
picture's rules and Steam artwork. Over 100 % lifts them for a dim screen. Changing it
makes new glitch and zoom clips.

## Zoom

The **Zoom** tab holds up to five pictures. Choosing one opens it so you can click the
point to zoom into, and **Point** moves it later. Each zoom takes 3 to 20 seconds and
magnifies 2 to 12 times; the zoom feels steady because the magnification grows evenly. The
round screen starts on the middle square of each picture, and the zoom ends centred on the
point.

## Smooth video on the SPACE

The serial link carries about five frames a second. So on the SPACE, the screens that need
more play short clips stored on the screen itself, as the Windows app does with its videos.
The program only ever adds its own clips to the screen's storage and never deletes
anything there. Details and safety rules: [recovery.md](recovery.md).

**Fan and hamster.** The fan has nine loops, three speeds by CPU load and three colours by
temperature, about 2 MB in all; the hamster has three by temperature, under 1 MB.
`install.sh` draws them once with ffmpeg, the service uploads them once (a couple of
seconds), and they switch at most every 10 seconds. Changing the fan's colours makes a new
set, uploaded the next time the fan shows; earlier sets stay on the screen. Untick **Play
the fan and hamster as smooth video** in the Screen tab to go back to streamed frames.

**Glitch slideshow.** Each change is a 3 second clip at 24 frames a second: just over a
second of glitch, then the new picture, which then holds as a normal frame. A separate low
priority process makes the clips (400 to 500 KB for photographs), and the service uploads
each one while a picture holds. Until a change's clip is on the screen that change is
streamed, so the first round of a new folder is partly streamed. Uploads need at least 5
seconds left on a picture, so give each picture 7 seconds or more. Clips stay on the
screen, one per pair of pictures.

**Zoom.** One clip a picture (about 1 to 2 MB): the last picture fading into this one, the
zoom into its point, then a few seconds of the last frame so a late switch never shows the
zoom restarting. The clips are made in the background and uploaded together, and play from
the next change of picture. After a change on the Zoom tab or to the picture boost, the old
clips play on while new ones are made, and the new ones take over at a change of picture.
With none on the screen yet, it holds the picture still rather than stream a jerky zoom.

The Scanner, Machine Cult and Fan over a picture are always streamed, at about ten frames
a second, and store nothing on the screen.

The Kraken plays no stored video, so on it the animated screens are always streamed.

## Smart picture

The **Smart picture** screen chooses for itself, first match wins:

1. a Steam game being played: its artwork (background and logo) from Steam's own cache on
   this PC; nothing is downloaded;
2. one of up to five programs you name in the **Rules** tab: the picture you gave it;
3. the CPU temperature band (two edges you set, three pictures);
4. otherwise a screen you choose, such as the dial.

The Rules tab says which rule applies right now. Running programs are checked once per
update; their names are only compared, never stored or logged.

## Cycling between screens

Tick **Cycle through screens**, tick the screens you want and give each a time (5 seconds
to an hour). They show in the listed order, round and round. Untick the box to go back to
the single screen picked above it. A burn-in refresh still takes over from the cycle while
it runs.

## Colours

The **Colours** tab sets every colour the screens use: background, rings, text, labels,
the cool, warm and hot temperature colours, CPU load, GPU, download, upload and the fan,
and the Machine Cult's cog, bone and eye. Pick a ready made palette (Default, Ocean,
Sunset, Neon, Mono, Amber Console, Wrist Console) or click any swatch to choose your own.
Amber Console and Wrist Console are each one colour of phosphor on a dark tube.

## Burn-in

The **Burn-in** section has two tools:

- **Clear burn-in** cycles full white, black, red, green, blue and random noise across the
  whole screen for 15 minutes to 8 hours, then returns to your screen. Run it at high
  brightness; repeated long runs (overnight) work best on stubborn retention, which usually
  fades but can be partly permanent. **Brightness during burn-in** gives the refresh its
  own brightness; your normal brightness comes back the moment it ends.
- **Move the picture slightly** (on by default) shifts everything by two pixels every few
  minutes so no edge stays on the same pixels for hours.

**Every day at** runs the same refresh at a set time for a set length, then returns the
screen to exactly what it was showing; a run can cross midnight. Stop during a scheduled
run ends tonight's run and keeps the schedule.
