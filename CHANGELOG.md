# Changelog

## 1.0.35

- Machine Cult: the emblem picture now ships with the app, so every install shows it without copying a file by hand.

## 1.0.34

- Machine Cult no longer jerks. The outer cog now ticks round like clockwork, a third of a tooth every 2 seconds, instead of turning every frame. Turning, it changed about 16 small patches round the rim each frame and the screen kept asking for whole frames; it now asks about 7 times a minute instead of 69.

## 1.0.33

- Machine Cult shows the emblem picture in the middle, inside the binary ring, with the temperatures and load underneath. Without the picture it draws its own skull as before.

## 1.0.32

- Tray icon. The control panel now lives in the system tray: click the icon to open the panel and again to close it, or use its menu (Open control panel, Close control panel, Quit tray icon). The Space LCD menu entry starts the tray and opens the panel, so there is only ever one panel, and the tray starts at login. Closing the panel or quitting the tray leaves the background service as it was.
- Zoom: when new clips finish uploading too late to start in step with the clock, the picture now holds still until the next change instead of playing a clip that is cut short.
- Machine Cult: a CPU load that is not a number shows as unknown instead of stopping the frame.
- A burn-in end time or daily time written by hand with a time zone is now refused when the settings load, rather than failing every frame.
- Control panel: Send to device no longer says a theme was sent when the settings could not be saved.

## 1.0.31

- Zoom no longer goes jerky while its clips are remade. After a change on the Zoom tab or to the picture boost, the clips already on the screen keep playing while the new ones are made, and the new ones are uploaded and take over at a change of picture. With no clips on the screen yet, the picture holds still instead of being streamed at under three frames a second.

## 1.0.30

- Picture boost: brightness and contrast for every picture screen (your picture, the fan's, both slideshows, the playlist, the Smart picture's rules, Steam artwork and the zoom), on the Pictures tab. At 100 % nothing changes.
- Zoom screen: up to five pictures on the new Zoom tab, each with a point you click on the picture. The screen zooms steadily into the point, then the next picture fades in and the zoom starts again. Speed and depth are adjustable. On the SPACE it plays as stored video, one clip a picture, made in the background and uploaded together once; until then it is streamed.

## 1.0.29

- Stored videos the right way up. The driver turns every frame a quarter turn before sending it, but the screen plays a stored video unturned, so the glitch clips played sideways and the hamster ran on its side (which is why it never reached the bottom). Glitch clips and hamster loops are now turned to match, as new files (gl2, ham3) uploaded once. The fan loops are left as they were: the fan looks the same at any angle.

## 1.0.28

- Glitch slideshow as stored video: each change of picture is a 3 second clip played from the screen's storage at 24 frames a second, then the picture holds as a normal frame. Streamed, a glitch only managed three or four frames. Clips are made by a separate low priority process on this PC and uploaded once each while a picture holds; a change whose clip is not on the screen yet is streamed. Follows the "smooth video" box in the Screen tab.
- Choosing a slideshow folder keeps the Glitch slideshow when it is on the screen or being previewed, instead of switching to the plain Slideshow.

## 1.0.27

- The phosphor tube finish is gone: it did not work as intended. The Amber Console and Wrist Console palettes stay. An old `finish` line in a settings file is ignored.
- Control panel: clicking a theme previews it and no longer sends it to the screen. Only the clicked theme animates; the other thumbnails are drawn once and redrawn only after a setting changes, which ends the stutter. Right click a theme for **Send to device**, which also turns the cycle off when it is on. A line under the preview says what the screen is showing now.
- Hamster: the paws now reach the rungs at the bottom of the wheel; three darker spokes over a darker stand, so the still lines cannot be mistaken for turning ones; six strides a turn instead of nine, so the legs no longer strobe at speed; the most the wheel turns between streamed frames drops from 14 to 10 degrees, well clear of the point where the rungs seem to run backwards. Stored hamster loops are drawn again (ham2) and uploaded once.
- Stored fan and hamster loops now follow the rotation setting. At rotation 0 nothing changes.
- Fan over a picture: the big fan over a picture you choose (Pictures tab, Behind the fan). The harder the CPU works, the faster it spins and the more the picture shows through.
- Glitch slideshow: the Slideshow folder's pictures, each breaking up into the next with torn bands, split colour and broken blocks, then holding still.
- Machine Cult: a bone skull with a pulsing bionic eye inside a cog, the great rim cog slowly turning, binary round the rim with a running cursor, temperatures and the CPU load in binary. Red and brass by default; three new palette colours (cog, bone, eye).
- The three new screens are streamed only and write nothing to the screen's storage.

## 1.0.26

- Deckplate looks. Two ready made palettes, Amber Console and Wrist Console, take the phosphor consoles of the Deckplate icon themes. A new Finish choice on the Colours tab puts every frame through a phosphor tube: a glow round everything lit, scan lines and edges that fall into the dark. Picture screens stay plain. With the finish left at Plain every frame is exactly as before. A finish makes its own set of stored fan and hamster loops, as new colours do.
- Scanner screen: a motion tracker sweep over range rings, with the CPU and GPU as contacts that flare and ping as the sweep passes and creep closer to the centre as they heat up. Streamed at about ten frames a second on both coolers; it has no stored loops, so it writes nothing to the screen's storage.

## 1.0.25

- Burn-in: 12 hour and 24 hour runs added to the Clear burn-in and daily schedule choices.

## 1.0.24

- Start when the computer starts: the control panel's "Start at login" box is now "Start when the computer starts". Ticking it enables the service and turns on systemd lingering for your user, so the service starts at boot, before anyone logs in, with either cooler. Unticking disables the service and leaves lingering alone. A service enabled by an earlier version (login only) shows unticked until ticked again.

## 1.0.23

- A second cooler: the NZXT Kraken 2023 (240 by 240 screen, firmware 2.x). The new `cooler` setting finds it by its USB IDs (`auto`), or names either cooler by hand; a PC with only the SPACE behaves exactly as before. Every screen has a small layout for the Kraken, and a new Cooler screen shows coolant temperature, pump and fan speed. The fan and hamster stream at about 15 frames a second on the Kraken. The control panel has a Cooler choice, previews at the cooler's own size, and shows only the screens and options that cooler can use. `install.sh` checks the Kraken's access and points to `tools/setup-kraken-udev.sh` (one sudo run) when it is missing.

## 1.0.22

- Review fixes. Stored video: a failed upload after a stop, or a stop that gets no answer, no longer leaves a stale frame or a loop the service thinks it replaced; a serial error during video reconnects instead of giving up on stored video for the session; `--once` never uses stored video. Settings: a file that will not load at start is reported and the defaults used, instead of a restart loop; the panel survives a hand edited file with a repeated key or section, and never adds a second copy of a key written in another case; a `%` in a picture path reads correctly; hidden themes cannot be put in the cycle; a `nan` interval is refused. Panel: a brightness still settling is written before any other save and on close; spin boxes save when editing finishes, so decimals can be typed; a half typed app rule survives other saves; pictures and game logos are decoded once, not every two seconds; status queries time out after two seconds. Loops are encoded to a temporary name so an interrupted encode is never uploaded. Probe tools refuse to run while the service is active and open the port exclusively; Ctrl-C sends nothing further. The storage name fence refuses anything containing `update`. Changes that are wide but small go as tiles rather than a full frame. Build: every gate step fails the build, the version is stamped into the staged copies, and the build checks the Git zip against `git ls-files` and the App folder against the Dist zip. Doc comments on the gate, build and deploy scripts, the unit, the settings template and the test helpers.

## 1.0.21

- Hamster screen: a hamster running in a wheel, faster as the CPU heats up, streamed or as three smooth stored loops. Hamster fur and wheel colours in the palette.

## 1.0.20

- Colours tab: every colour the screens use can be changed, with ready made palettes (Default, Ocean, Sunset, Neon, Mono). Stored fan loops are named after their colours, so new fan colours get a new set.

## 1.0.19

- Steam artwork is found in Steam's newer cache layout too (files in hashed subfolders, library_header and library_capsule names), so newer games such as Forza Horizon 6 show their artwork.

## 1.0.18

- Steam games are also recognised from the SteamAppId Steam sets on every game, for the newer containerised Steam runtime. `python -m space_lcd.rules` shows what the Smart picture sees.

## 1.0.17

- Smart picture screen: the Steam game's artwork while one is played, else a picture per running program, else a picture per CPU temperature band, else a chosen screen. New Rules tab in the control panel.

## 1.0.16

- The fan screen plays smooth video stored on the screen: nine loops by CPU load and temperature, uploaded once and switched as the CPU changes. A Screen tab option goes back to streamed frames.

## 1.0.15

- Slideshow screen (every picture in a folder in turn) and Playlist screen (up to five pictures, each for its own time). The control panel's settings are now in Screen, Pictures and Burn-in tabs.

## 1.0.14

- Network screen (download and upload speed with a graph) and System screen (CPU and GPU model, memory, disk, uptime, kernel).

## 1.0.13

- Changes in different parts of the screen are sent as several small updates instead of one large one, so a temperature change no longer forces a slow full frame.
- The fan theme is now nothing but a big fan filling the screen, with rounded blade tips.

## 1.0.12

- Partial updates now number themselves from 0 after every full frame, as the official app does, so the screen stops rejecting them.

## 1.0.11

- The screen's status replies are now read: when it asks for a full frame it gets one, as the official app does. The timing log counts these requests.

## 1.0.10

- Fan theme: an animated fan that spins faster with CPU load and turns from blue to red with temperature, at up to 10 frames a second. Sensors are now read on the update interval while animations run faster.

## 1.0.9

- Cycle through screens: pick several themes and a time for each, and the screen rotates through them. Themes can now set their own update speed, ready for animation.

## 1.0.8

- Optional brightness for burn-in refreshes only; the normal brightness returns when the refresh ends.

## 1.0.7

- Daily burn-in schedule: a time of day and a length; it takes over the screen for that time and then returns it as it was.

## 1.0.6

- The service logs how long screen updates take, once a minute, to size the coming animations.

## 1.0.5

- Brightness is now sent after the screen options at start up, so the options command can no longer turn it back down.

## 1.0.4

- Anti burn-in: a Clear burn-in refresh (white, colours and random noise for 15 minutes to 8 hours) and a slow two pixel picture shift, both in the control panel.

## 1.0.3

- Renamed from CPU LCD Display to Space LCD, after the cooler: menu entry, service (`space-lcd`), settings and install folders. The installer copies existing settings across and lists the old install's leftovers without removing them.

## 1.0.2

- Five themes (dial, twin gauges, big clock, minimal, your picture) and a control panel app to choose them with live previews, set brightness, rotation, interval and picture, and start or stop the service. Settings now apply live without a restart.

## 1.0.1

- First version: live CPU, GPU and memory stats on the cooler's round screen, systemd user service, per user installer.
