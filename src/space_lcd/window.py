"""
<summary>
The control panel window (Qt). Kept apart from ui.py so that importing ui.py, for the icon
writer and the tests, does not need Qt.
</summary>
<remarks>
Every control saves straight to the settings file; there is no Apply button. The
brightness slider waits until it has been still for a moment before saving, so dragging it
does not send the screen a burst of brightness commands; any other save in that moment,
and closing the window, write the slider's value first so it is never lost. Clicking a
theme only previews it; "Send to device" on its right click menu is what saves it.
Picking a picture saves at once. Spin boxes save when editing finishes (Enter, focus
leaving, the arrows), not on every keystroke, so "1.5" can be typed. The previewed
theme refreshes on a timer, ten times a second when it is animated; the other thumbnails
are drawn once and again only after a setting changes, since drawing them all every two
seconds made the window stutter (Rob, 07/11/2026). Service status refreshes on a timer.
</remarks>
"""

import configparser
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import Image, ImageOps
from PySide6.QtCore import QPoint, QSize, Qt, QTime, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QSpinBox, QTabWidget, QTimeEdit,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from . import burnin, config, devices, pictures, render, rules, screens
from .sensors import SensorReader, Stats
from .themes import THEMES
from .ui import ServiceControl

PREVIEW_SIZE = 300
THUMB_SIZE = 132
"""<summary>Sizes, in window pixels, of the large preview and of each theme thumbnail.</summary>"""

PREVIEW_MS = 2000
ANIMATE_MS = 100
STATUS_MS = 3000
SAVE_DELAY_MS = 400
"""
<summary>Preview refresh for a still theme, frame time for an animated one, service status
refresh, and the brightness slider's settle time.</summary>
"""

PICTURE_FILTER = "Pictures (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"

STATE_TEXT = {
    "active": "Running: the screen is live",
    "activating": "Starting",
    "deactivating": "Stopping",
    "inactive": "Stopped",
    "failed": "Stopped after an error (see the journal)",
    "not installed": "Not installed: run install.sh",
}
"""<summary>Plain English for each systemd state shown in the status line.</summary>"""


def to_pixmap(image: Image.Image, size: int) -> QPixmap:
    """
    <summary>Converts a frame to a Qt pixmap, scaled, with the corners outside the circle transparent.</summary>
    <param name="image">A square RGB frame from render.draw_frame(), at the cooler's own size.</param>
    <param name="size">Width and height of the result in device independent pixels.</param>
    <remarks>A 240 frame is scaled up rather than redrawn larger, so the preview shows the small screen's real detail.</remarks>
    """
    rgba = image.convert("RGBA")
    rgba.putalpha(render._round_mask(rgba.width))
    rgba = rgba.resize((size * 2, size * 2), Image.Resampling.LANCZOS)
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
    pixmap = QPixmap.fromImage(qimage)
    pixmap.setDevicePixelRatio(2.0)
    return pixmap


def to_icon(image: Image.Image, size: int) -> QIcon:
    """
    <summary>A thumbnail icon that looks the same selected as not.</summary>
    <remarks>Qt tints a selected icon with the highlight colour, which hid the clicked theme's own colours while it animated.</remarks>
    """
    pixmap = to_pixmap(image, size)
    icon = QIcon(pixmap)
    icon.addPixmap(pixmap, QIcon.Mode.Selected)
    return icon


class _PointLabel(QLabel):
    """
    <summary>A picture that remembers where it was last clicked and marks the spot.</summary>
    <remarks>The point is kept as fractions of the picture's width and height, as the settings store it.</remarks>
    """

    def __init__(self, pixmap: QPixmap, point: Tuple[float, float]) -> None:
        """<summary>Shows pixmap at its own size with the point marked.</summary>"""
        super().__init__()
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.point = point

    def mousePressEvent(self, event) -> None:
        """<summary>Moves the point to where the click landed.</summary>"""
        spot = event.position()
        self.point = (max(0.0, min(1.0, spot.x() / self.width())), max(0.0, min(1.0, spot.y() / self.height())))
        self.update()

    def paintEvent(self, event) -> None:
        """<summary>Draws the picture, then a ring and cross on the point in red and white so it shows on any picture.</summary>"""
        super().paintEvent(event)
        painter = QPainter(self)
        x, y = self.point[0] * self.width(), self.point[1] * self.height()
        for colour, width in ((QColor(255, 255, 255), 5), (QColor(230, 30, 30), 2)):
            painter.setPen(QPen(colour, width))
            painter.drawEllipse(QPoint(round(x), round(y)), 14, 14)
            painter.drawLine(round(x) - 22, round(y), round(x) + 22, round(y))
            painter.drawLine(round(x), round(y) - 22, round(x), round(y) + 22)
        painter.end()


class PointPicker(QDialog):
    """
    <summary>Shows a picture and lets the user click the point the Zoom screen should zoom into.</summary>
    <remarks>
    The picture is turned upright from its camera tag first, as the Zoom screen turns it, so the
    point lands on what the user sees. Only a preview size copy is shown.
    </remarks>
    """

    PREVIEW = 640
    """<summary>The longest side of the picture in the dialog, in window pixels.</summary>"""

    def __init__(self, parent: QWidget, path: Path, point: Tuple[float, float]) -> None:
        """
        <summary>Builds the dialog for one picture.</summary>
        <exception cref="OSError">The picture cannot be read.</exception>
        """
        super().__init__(parent)
        self.setWindowTitle("Click the point to zoom into")
        with Image.open(path) as source:
            picture = ImageOps.exif_transpose(source).convert("RGB")
        picture.thumbnail((self.PREVIEW, self.PREVIEW))
        data = picture.tobytes("raw", "RGB")
        image = QImage(data, picture.width, picture.height, picture.width * 3, QImage.Format.Format_RGB888).copy()
        self._label = _PointLabel(QPixmap.fromImage(image), point)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("The zoom ends centred on this point. Only the middle square of the picture "
                                "fills the round screen at the start.", wordWrap=True))
        layout.addWidget(self._label, alignment=Qt.AlignmentFlag.AlignCenter)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def point(self) -> Tuple[float, float]:
        """<summary>The point chosen, as fractions of the picture's width and height.</summary>"""
        return self._label.point


class ControlPanel(QMainWindow):
    """
    <summary>The main window: theme picker on the left, settings and service on the right.</summary>
    <remarks>
    Settings are read once at start and after each save; if the file is edited by hand
    while the window is open, the window does not notice until it next saves. The cooler
    is detected again on each of those (sysfs only, nothing is sent to it), and the window
    then hides what that cooler cannot use: screens needing pump and fan readings on the
    SPACE, stored video on the Kraken.
    </remarks>
    """

    def __init__(self, config_path: Path, service: ServiceControl, reader: SensorReader) -> None:
        """
        <summary>Builds the window and starts the preview and status timers.</summary>
        <param name="config_path">The settings file to read and write.</param>
        <param name="service">Controls the background service; a fake in tests.</param>
        <param name="reader">Live sensor readings for the previews.</param>
        """
        super().__init__()
        self._path = config_path
        self._service = service
        self._reader = reader
        self._settings = self._load()
        self._device = devices.detect(self._settings.cooler)
        self._stats: Stats = reader.read()
        self._loading = True
        self._selected: Optional[str] = None
        """<summary>The theme clicked in the list: previewed, and animated if it moves; not necessarily the one on the screen.</summary>"""

        self.setWindowTitle("Space LCD")
        central = QWidget()
        self.setCentralWidget(central)
        columns = QHBoxLayout(central)
        columns.addLayout(self._build_themes(), 3)
        columns.addLayout(self._build_controls(), 2)

        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS)
        self._save_timer.timeout.connect(lambda: self._save({}))
        for box in self.findChildren(QAbstractSpinBox):
            box.setKeyboardTracking(False)
        self._preview_timer = QTimer(self, interval=PREVIEW_MS)
        self._preview_timer.timeout.connect(self._tick_preview)
        self._animate_timer = QTimer(self, interval=ANIMATE_MS)
        self._animate_timer.timeout.connect(self._draw_selected)
        self._status_timer = QTimer(self, interval=STATUS_MS)
        self._status_timer.timeout.connect(self._refresh_status)

        self._show_settings()
        self._loading = False
        self._refresh_previews(read=False)
        self._refresh_status()
        self._preview_timer.start()
        self._status_timer.start()

    # Layout ----------------------------------------------------------------

    def _build_themes(self) -> QVBoxLayout:
        """<summary>The large preview, its description, and the clickable theme thumbnails.</summary>"""
        layout = QVBoxLayout()
        self._preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(PREVIEW_SIZE + 20, PREVIEW_SIZE + 20)
        layout.addWidget(self._preview)

        self._description = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        layout.addWidget(self._description)
        self._on_screen = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self._on_screen.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self._on_screen)

        self._themes = QListWidget()
        self._themes.setViewMode(QListWidget.ViewMode.IconMode)
        self._themes.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self._themes.setGridSize(QSize(THUMB_SIZE + 24, THUMB_SIZE + 40))
        self._themes.setMovement(QListWidget.Movement.Static)
        self._themes.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._themes.setWrapping(True)
        self._themes.setMinimumHeight(2 * (THUMB_SIZE + 44))
        self._theme_items: Dict[str, QListWidgetItem] = {}
        for key, theme in THEMES.items():
            if not theme.listed:
                continue
            item = QListWidgetItem(theme.name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(theme.description)
            self._themes.addItem(item)
            self._theme_items[key] = item
        self._themes.currentItemChanged.connect(self._theme_chosen)
        self._themes.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._themes.customContextMenuRequested.connect(self._theme_menu)
        self._themes.setToolTip("")
        layout.addWidget(self._themes)
        layout.addWidget(self._build_cycle())
        return layout

    def _build_cycle(self) -> QGroupBox:
        """
        <summary>The screen cycle: a tick and a time for each theme, in a box that turns cycling on.</summary>
        <remarks>
        Only ticked screens are saved, in theme list order. A screen's time is kept in the
        control while unticked but is not remembered once the panel closes.
        </remarks>
        """
        group = QGroupBox("Cycle through screens", checkable=True)
        grid = QGridLayout(group)
        self._cycle_ticks: Dict[str, QCheckBox] = {}
        self._cycle_times: Dict[str, QSpinBox] = {}
        for row, (key, theme) in enumerate((k, th) for k, th in THEMES.items() if th.listed):
            tick = QCheckBox(theme.name)
            seconds = QSpinBox(minimum=config.CYCLE_SECONDS[0], maximum=config.CYCLE_SECONDS[1],
                               value=30, suffix=" s")
            tick.toggled.connect(lambda _: self._save_cycle())
            seconds.valueChanged.connect(lambda _: self._save_cycle())
            grid.addWidget(tick, row // 2, (row % 2) * 2)
            grid.addWidget(seconds, row // 2, (row % 2) * 2 + 1)
            self._cycle_ticks[key] = tick
            self._cycle_times[key] = seconds
        self._cycle_state = QLabel(wordWrap=True)
        grid.addWidget(self._cycle_state, (len(self._cycle_ticks) + 1) // 2, 0, 1, 4)
        group.toggled.connect(lambda _: self._save_cycle())
        self._cycle_group = group
        return group

    def _save_cycle(self) -> None:
        """
        <summary>Saves whether cycling is on and the ticked screens with their times.</summary>
        <remarks>A screen this cooler cannot show is left out, so a hidden tick is dropped at the next cycle change.</remarks>
        """
        entries = [(key, self._cycle_times[key].value()) for key in self._cycle_ticks
                   if self._cycle_ticks[key].isChecked() and self._available(key)]
        self._save({"cycle_on": "yes" if self._cycle_group.isChecked() else "no",
                    "cycle": config.format_cycle(entries)})

    def _show_cycle_state(self) -> None:
        """<summary>Says which screen is showing now and for how much longer, when cycling.</summary>"""
        s = self._settings
        if not s.cycle_on:
            self._cycle_state.setText("Off: the screen shows the theme last sent to it.")
        elif not s.cycle:
            self._cycle_state.setText("Tick at least one screen. Until then the theme last sent shows.")
        else:
            key, left = screens.current(s, datetime.now())
            self._cycle_state.setText("Showing {} now, {:.0f} s left.".format(THEMES[key].name, left or 0))

    def _build_controls(self) -> QVBoxLayout:
        """
        <summary>The right hand column: the service, always visible, over tabs for Screen, Pictures, Zoom, Rules, Colours and Burn-in.</summary>
        """
        layout = QVBoxLayout()
        layout.addWidget(self._build_service())
        tabs = QTabWidget()
        tabs.addTab(self._build_screen(), "Screen")
        tabs.addTab(self._build_pictures(), "Pictures")
        tabs.addTab(self._build_zoom(), "Zoom")
        tabs.addTab(self._build_rules(), "Rules")
        tabs.addTab(self._build_colours(), "Colours")
        tabs.addTab(self._build_burnin(), "Burn-in")
        layout.addWidget(tabs, 1)
        return layout

    def _build_screen(self) -> QWidget:
        """<summary>The Screen tab: which cooler, brightness, rotation, update interval and stored video.</summary>"""
        page = QWidget()
        form = QFormLayout(page)
        self._screen_form = form

        cooler_row = QVBoxLayout()
        self._cooler = QComboBox()
        self._cooler.addItem("Find it automatically", "auto")
        for device in devices.DEVICES.values():
            self._cooler.addItem(device.name, device.key)
        self._cooler.currentIndexChanged.connect(lambda _: self._save({"cooler": self._cooler.currentData()}))
        self._cooler_found = QLabel(wordWrap=True)
        self._cooler_found.setStyleSheet("color: palette(placeholder-text);")
        cooler_row.addWidget(self._cooler)
        cooler_row.addWidget(self._cooler_found)
        form.addRow("Cooler", cooler_row)

        brightness_row = QHBoxLayout()
        self._brightness = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100, pageStep=10)
        self._brightness_value = QLabel()
        self._brightness_value.setMinimumWidth(40)
        self._brightness.valueChanged.connect(self._brightness_moved)
        brightness_row.addWidget(self._brightness)
        brightness_row.addWidget(self._brightness_value)
        form.addRow("Brightness", brightness_row)

        self._rotation = QComboBox()
        for degrees in config.ROTATIONS:
            self._rotation.addItem("{}\u00b0".format(degrees), degrees)
        self._rotation.currentIndexChanged.connect(
            lambda _: self._save({"rotation": self._rotation.currentData()}))
        form.addRow("Rotation", self._rotation)

        self._interval = QDoubleSpinBox(minimum=0.5, maximum=30.0, singleStep=0.5, decimals=1, suffix=" s")
        self._interval.valueChanged.connect(lambda value: self._save({"interval": value}))
        form.addRow("Update every", self._interval)

        self._fan_video = QCheckBox("Play the fan, hamster and glitch changes as smooth video stored on the screen")
        self._fan_video.setToolTip("Uploads short loops (about 2 MB for the fan, under 1 MB for the hamster) to the "
                                   "screen's own storage once, then plays them there. Unticked, they are drawn here "
                                   "and sent at about 5 frames a second.")
        self._fan_video.toggled.connect(lambda on: self._save({"fan_mode": "video" if on else "stream"}))
        form.addRow("Animations", self._fan_video)
        return page

    def _build_service(self) -> QGroupBox:
        """<summary>The background service group: state, Start, Stop, Restart and start when the computer starts.</summary>"""
        service = QGroupBox("Background service")
        service_layout = QVBoxLayout(service)
        self._state = QLabel(wordWrap=True)
        service_layout.addWidget(self._state)
        buttons = QHBoxLayout()
        self._start = QPushButton("Start")
        self._stop = QPushButton("Stop")
        self._restart = QPushButton("Restart")
        self._start.clicked.connect(lambda: self._service_action(self._service.start))
        self._stop.clicked.connect(lambda: self._service_action(self._service.stop))
        self._restart.clicked.connect(lambda: self._service_action(self._service.restart))
        for button in (self._start, self._stop, self._restart):
            buttons.addWidget(button)
        service_layout.addLayout(buttons)
        self._at_boot = QCheckBox("Start when the computer starts")
        self._at_boot.setToolTip("Starts the service at boot, before anyone logs in, and keeps it running after "
                                 "logging out. Ticked only when both are set up; ticking sets up whichever is missing.")
        self._at_boot.clicked.connect(lambda checked: self._service_action(
            lambda: self._service.set_enabled(checked)))
        service_layout.addWidget(self._at_boot)
        note = QLabel("Changes reach the screen within one update while the service is running. "
                      "Nothing is sent to the screen while it is stopped.", wordWrap=True)
        note.setStyleSheet("color: palette(placeholder-text);")
        service_layout.addWidget(note)
        return service

    def _build_pictures(self) -> QWidget:
        """
        <summary>The Pictures tab: the picture for "Your picture", the one behind the fan, the slideshow folder, and the five playlist slots.</summary>
        <remarks>
        Choosing a picture, a folder or a playlist slot also switches to the matching
        screen, since that is almost always why it was chosen; the cycle, if on, is untouched.
        The picture behind the fan is the exception: it only previews the Fan over a picture,
        in keeping with the theme list, and is sent from there.
        </remarks>
        """
        page = QWidget()
        layout = QVBoxLayout(page)

        boost = QGroupBox("Picture boost, for every picture screen")
        form = QFormLayout(boost)
        self._picture_brightness = QSpinBox(minimum=render.PICTURE_RANGE[0], maximum=render.PICTURE_RANGE[1],
                                            singleStep=5, suffix=" %")
        self._picture_brightness.valueChanged.connect(lambda value: self._save({"picture_brightness": value}))
        self._picture_contrast = QSpinBox(minimum=render.PICTURE_RANGE[0], maximum=render.PICTURE_RANGE[1],
                                          singleStep=5, suffix=" %")
        self._picture_contrast.valueChanged.connect(lambda value: self._save({"picture_contrast": value}))
        reset = QPushButton("As taken")
        reset.clicked.connect(lambda: self._save({"picture_brightness": 100, "picture_contrast": 100}))
        boost_row = QHBoxLayout()
        boost_row.addWidget(self._picture_contrast, 1)
        boost_row.addWidget(reset)
        form.addRow("Brightness", self._picture_brightness)
        form.addRow("Contrast", boost_row)
        boost_note = QLabel("Over 100 % lifts pictures for the cooler's dim screen: your picture, the fan's, "
                            "the slideshows, the playlist, the zoom and Steam artwork. Changing it makes new "
                            "glitch and zoom clips.", wordWrap=True)
        boost_note.setStyleSheet("color: palette(placeholder-text);")
        form.addRow(boost_note)
        layout.addWidget(boost)

        single = QGroupBox("Your picture")
        row = QHBoxLayout(single)
        self._picture = QLineEdit(readOnly=True, placeholderText="None chosen")
        choose = QPushButton("Choose")
        choose.clicked.connect(self._choose_picture)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self._save({"background": None}))
        row.addWidget(self._picture, 1)
        row.addWidget(choose)
        row.addWidget(clear)
        layout.addWidget(single)

        behind = QGroupBox("Behind the fan")
        row = QHBoxLayout(behind)
        self._fan_picture = QLineEdit(readOnly=True, placeholderText="None chosen")
        choose_fan = QPushButton("Choose")
        choose_fan.clicked.connect(self._choose_fan_picture)
        clear_fan = QPushButton("Clear")
        clear_fan.clicked.connect(lambda: self._save({"fan_picture": None}))
        row.addWidget(self._fan_picture, 1)
        row.addWidget(choose_fan)
        row.addWidget(clear_fan)
        layout.addWidget(behind)

        show = QGroupBox("Slideshow and Glitch slideshow")
        form = QFormLayout(show)
        folder_row = QHBoxLayout()
        self._folder = QLineEdit(readOnly=True, placeholderText="No folder chosen")
        pick_folder = QPushButton("Choose")
        pick_folder.clicked.connect(self._choose_folder)
        folder_row.addWidget(self._folder, 1)
        folder_row.addWidget(pick_folder)
        form.addRow("Folder", folder_row)
        self._folder_seconds = QSpinBox(minimum=pictures.PICTURE_SECONDS[0], maximum=pictures.PICTURE_SECONDS[1],
                                        suffix=" s")
        self._folder_seconds.valueChanged.connect(lambda value: self._save({"slideshow_seconds": value}))
        form.addRow("Each picture", self._folder_seconds)
        self._folder_count = QLabel()
        form.addRow("", self._folder_count)
        layout.addWidget(show)

        playlist = QGroupBox("Playlist")
        grid = QGridLayout(playlist)
        self._slots = []
        for n in range(pictures.PLAYLIST_SIZE):
            name = QLineEdit(readOnly=True, placeholderText="Empty")
            seconds = QSpinBox(minimum=pictures.PICTURE_SECONDS[0], maximum=pictures.PICTURE_SECONDS[1],
                               value=10, suffix=" s")
            choose_slot = QPushButton("Choose")
            clear_slot = QPushButton("Clear")
            choose_slot.clicked.connect(lambda _=False, n=n: self._choose_slot(n))
            clear_slot.clicked.connect(lambda _=False, n=n: self._clear_slot(n))
            seconds.valueChanged.connect(lambda _=0, n=n: self._save_slot(n))
            grid.addWidget(QLabel(str(n + 1)), n, 0)
            grid.addWidget(name, n, 1)
            grid.addWidget(seconds, n, 2)
            grid.addWidget(choose_slot, n, 3)
            grid.addWidget(clear_slot, n, 4)
            self._slots.append((name, seconds))
        layout.addWidget(playlist)
        layout.addStretch(1)
        return page

    def _build_zoom(self) -> QWidget:
        """
        <summary>The Zoom tab: five pictures, each with the point to zoom into, how long a zoom takes and how deep it goes.</summary>
        <remarks>
        Choosing a picture opens it to click the point, then previews the Zoom screen without
        sending it, in keeping with the theme list. Slots keep their places, like the playlist.
        </remarks>
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Pictures to zoom into, in order")
        grid = QGridLayout(group)
        self._zoom_rows = []
        for n in range(config.ZOOM_SLOTS):
            name = QLineEdit(readOnly=True, placeholderText="Empty")
            where = QLabel()
            choose = QPushButton("Choose")
            point = QPushButton("Point")
            clear = QPushButton("Clear")
            choose.clicked.connect(lambda _=False, n=n: self._choose_zoom(n))
            point.clicked.connect(lambda _=False, n=n: self._point_zoom(n))
            clear.clicked.connect(lambda _=False, n=n: self._save({"zoom_{}".format(n + 1): None}))
            grid.addWidget(QLabel(str(n + 1)), n, 0)
            grid.addWidget(name, n, 1)
            grid.addWidget(where, n, 2)
            grid.addWidget(choose, n, 3)
            grid.addWidget(point, n, 4)
            grid.addWidget(clear, n, 5)
            self._zoom_rows.append((name, where, point))
        layout.addWidget(group)

        form = QFormLayout()
        self._zoom_seconds = QSpinBox(minimum=config.ZOOM_SECONDS[0], maximum=config.ZOOM_SECONDS[1], suffix=" s")
        self._zoom_seconds.valueChanged.connect(lambda value: self._save({"zoom_seconds": value}))
        self._zoom_depth = QSpinBox(minimum=config.ZOOM_DEPTH[0], maximum=config.ZOOM_DEPTH[1], suffix=" times")
        self._zoom_depth.valueChanged.connect(lambda value: self._save({"zoom_depth": value}))
        form.addRow("Each zoom takes", self._zoom_seconds)
        form.addRow("Zooms in", self._zoom_depth)
        layout.addLayout(form)
        note = QLabel("On the SPACE the zoom plays as smooth video stored on the screen: one clip a picture, "
                      "about 1 to 2 MB each, made on this PC and uploaded once. Until then, and whenever a "
                      "setting here changes, it is streamed for a while. Right click Zoom in the screen "
                      "list to send it.", wordWrap=True)
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _zoom_slot(self, n: int) -> Optional[Tuple[Path, Tuple[float, float]]]:
        """<summary>Zoom slot n, counting from 0, as written in the file; None when empty or unreadable.</summary>"""
        try:
            return config.parse_zoom_entry(config.raw("zoom_{}".format(n + 1), self._path))
        except ValueError:
            return None

    def _pick_point(self, path: Path, point: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        """<summary>Opens the point picker on a picture; the point chosen, or None when cancelled or unreadable.</summary>"""
        try:
            dialog = PointPicker(self, path, point)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            QMessageBox.warning(self, "Zoom", "That picture could not be opened.\n\n{}".format(error))
            return None
        return dialog.point() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _choose_zoom(self, n: int) -> None:
        """<summary>Asks for a picture for zoom slot n, then its point; saves both and previews the Zoom screen.</summary>"""
        slot = self._zoom_slot(n)
        start = str(slot[0].parent) if slot else str(Path.home() / "Pictures")
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose a picture to zoom into", start, PICTURE_FILTER)
        if not chosen:
            return
        point = self._pick_point(Path(chosen), (0.5, 0.5))
        if point is None:
            return
        self._save({"zoom_{}".format(n + 1): config.format_zoom_entry(Path(chosen), point)})
        item = self._theme_items.get("zoom")
        if item is not None:
            self._themes.setCurrentItem(item)

    def _point_zoom(self, n: int) -> None:
        """<summary>Opens slot n's picture to move its point.</summary>"""
        slot = self._zoom_slot(n)
        if slot is None:
            return
        point = self._pick_point(*slot)
        if point is not None:
            self._save({"zoom_{}".format(n + 1): config.format_zoom_entry(slot[0], point)})

    def _show_zoom(self) -> None:
        """<summary>Fills the Zoom tab from the file: each slot's picture and point, the speed and the depth.</summary>"""
        for n, (name, where, point) in enumerate(self._zoom_rows):
            slot = self._zoom_slot(n)
            name.setText(str(slot[0]) if slot else "")
            where.setText("{:.0f} %, {:.0f} %".format(slot[1][0] * 100, slot[1][1] * 100) if slot else "")
            point.setEnabled(slot is not None)
        self._zoom_seconds.setValue(self._settings.zoom_seconds)
        self._zoom_depth.setValue(self._settings.zoom_depth)

    def _build_rules(self) -> QWidget:
        """
        <summary>The Rules tab, for the Smart picture screen: Steam artwork, five app rules, three temperature pictures, fallback.</summary>
        <remarks>
        The app name boxes offer the names of programs running now, read once when the
        window opens, and accept any typed name. Matching is on the exact process name,
        case ignored, which is what the drop down shows.
        </remarks>
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        self._rule_state = QLabel(wordWrap=True)
        layout.addWidget(self._rule_state)

        self._steam = QCheckBox("Show the artwork of the Steam game being played")
        self._steam.toggled.connect(lambda on: self._save({"rules_steam": "yes" if on else "no"}))
        layout.addWidget(self._steam)

        apps = QGroupBox("Apps: a picture while a program is running")
        grid = QGridLayout(apps)
        try:
            from .sensors import read_processes
            running = sorted(read_processes()[0])
        except Exception:  # a panel without a process list still works; names can be typed
            running = []
        self._app_rules = []
        for n in range(rules.APP_RULES):
            process = QComboBox(editable=True)
            process.addItems([""] + running)
            # Without this the box grows to the longest running program's name.
            process.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            process.setMinimumContentsLength(12)
            process.lineEdit().setPlaceholderText("program name")
            picture = QLineEdit(readOnly=True, placeholderText="No picture")
            picture.setMinimumWidth(140)
            choose = QPushButton("Choose")
            clear = QPushButton("Clear")
            for button in (choose, clear):
                button.setMaximumWidth(80)
            process.lineEdit().editingFinished.connect(lambda n=n: self._save_app_rule(n))
            process.activated.connect(lambda _=0, n=n: self._save_app_rule(n))
            choose.clicked.connect(lambda _=False, n=n: self._choose_app_picture(n))
            clear.clicked.connect(lambda _=False, n=n: self._clear_app_rule(n))
            grid.addWidget(process, n, 0)
            grid.addWidget(picture, n, 1)
            grid.addWidget(choose, n, 2)
            grid.addWidget(clear, n, 3)
            self._app_rules.append((process, picture))
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        layout.addWidget(apps)

        temps = QGroupBox("Temperature: a picture per CPU temperature band")
        form = QGridLayout(temps)
        self._edge_low = QSpinBox(minimum=20, maximum=100, suffix="\u00b0")
        self._edge_high = QSpinBox(minimum=21, maximum=110, suffix="\u00b0")
        self._edge_low.valueChanged.connect(lambda _: self._save_edges())
        self._edge_high.valueChanged.connect(lambda _: self._save_edges())
        edges = QHBoxLayout()
        edges.addWidget(QLabel("Bands change at"))
        edges.addWidget(self._edge_low)
        edges.addWidget(QLabel("and"))
        edges.addWidget(self._edge_high)
        edges.addStretch(1)
        form.addLayout(edges, 0, 0, 1, 4)
        self._temp_rules = []
        for n in range(rules.TEMP_BANDS):
            label = QLabel()
            picture = QLineEdit(readOnly=True, placeholderText="No picture")
            choose = QPushButton("Choose")
            clear = QPushButton("Clear")
            choose.clicked.connect(lambda _=False, n=n: self._choose_temp_picture(n))
            clear.clicked.connect(lambda _=False, n=n: self._save({"rules_temp_{}".format(n + 1): None}))
            form.addWidget(label, n + 1, 0)
            form.addWidget(picture, n + 1, 1)
            form.addWidget(choose, n + 1, 2)
            form.addWidget(clear, n + 1, 3)
            self._temp_rules.append((label, picture))
        layout.addWidget(temps)

        fallback_row = QHBoxLayout()
        fallback_row.addWidget(QLabel("When nothing matches, show"))
        self._fallback = QComboBox()
        for key, theme in THEMES.items():
            if theme.listed and key != "smart":
                self._fallback.addItem(theme.name, key)
        self._fallback.currentIndexChanged.connect(
            lambda _: self._save({"rules_fallback": self._fallback.currentData()}))
        fallback_row.addWidget(self._fallback, 1)
        layout.addLayout(fallback_row)
        layout.addStretch(1)
        return page

    def _save_app_rule(self, n: int) -> None:
        """
        <summary>Saves app rule n when both its program name and picture are set; a rule with only one half is kept in the window.</summary>
        <remarks>Nothing is written when the file already holds the same rule: choosing from the drop down fires two signals.</remarks>
        """
        process, picture = self._app_rules[n]
        name = process.currentText().strip()
        if name and picture.text():
            rule = rules.format_app_rule(name, Path(picture.text()))
            if rule != config.raw("rules_app_{}".format(n + 1), self._path):
                self._save({"rules_app_{}".format(n + 1): rule})

    def _clear_app_rule(self, n: int) -> None:
        """<summary>Empties app rule n in the window and in the file.</summary>"""
        process, picture = self._app_rules[n]
        process.setEditText("")
        picture.setText("")
        self._save({"rules_app_{}".format(n + 1): ""})

    def _choose_app_picture(self, n: int) -> None:
        """<summary>Asks for the picture of app rule n, then saves the rule if it has a program name.</summary>"""
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose a picture", str(Path.home() / "Pictures"), PICTURE_FILTER)
        if chosen:
            self._app_rules[n][1].setText(chosen)
            self._save_app_rule(n)

    def _choose_temp_picture(self, n: int) -> None:
        """<summary>Asks for the picture of temperature band n and saves it.</summary>"""
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose a picture", str(Path.home() / "Pictures"), PICTURE_FILTER)
        if chosen:
            self._save({"rules_temp_{}".format(n + 1): chosen})

    def _save_edges(self) -> None:
        """<summary>Saves the two band edges, nudging the upper one so it always stays above the lower.</summary>"""
        if self._edge_high.value() <= self._edge_low.value():
            self._edge_high.setValue(self._edge_low.value() + 1)
            return
        self._save({"rules_temp_edges": "{}, {}".format(self._edge_low.value(), self._edge_high.value())})

    def _show_rules(self) -> None:
        """<summary>Puts the rule settings into the Rules tab and says which rule applies right now.</summary>"""
        s = self._settings
        self._steam.setChecked(s.rules_steam)
        for n, (process, picture) in enumerate(self._app_rules):
            try:
                rule = rules.parse_app_rule(config.raw("rules_app_{}".format(n + 1), self._path))
            except ValueError:
                rule = None
            # An empty rule in the file leaves the boxes alone: a half typed rule (name
            # without a picture yet) must survive every other save in the meantime.
            if rule is not None:
                process.setEditText(rule[0])
                picture.setText(str(rule[1]))
        low, high = (int(edge) for edge in s.rules_temp_edges)
        self._edge_low.setValue(low)
        self._edge_high.setValue(high)
        bands = ("Below {}\u00b0".format(low), "{}\u00b0 to {}\u00b0".format(low, high), "Above {}\u00b0".format(high))
        for (label, picture), band, path in zip(self._temp_rules, bands, s.rules_temp_pictures):
            label.setText(band)
            picture.setText(str(path) if path else "")
        index = self._fallback.findData(s.rules_fallback)
        if index >= 0:
            self._fallback.setCurrentIndex(index)
        self._show_rule_state()

    def _show_rule_state(self) -> None:
        """<summary>One line saying what the Smart picture screen would show now, and why.</summary>"""
        key, _, reason = rules.resolve(self._settings, self._stats.cpu_temp, self._stats.running,
                                       self._stats.steam_app)
        if reason == "fallback":
            text = "Nothing matches now, so it shows {}.".format(THEMES[key].name)
        elif reason.startswith("game"):
            text = "Showing the artwork of the Steam game being played."
        elif reason.startswith("app"):
            text = "Showing the picture for {}, which is running.".format(reason[4:])
        else:
            text = "Showing the picture for temperature band {}.".format(reason.split()[-1])
        self._rule_state.setText("Smart picture: " + text)

    def _build_colours(self) -> QWidget:
        """
        <summary>The Colours tab: a ready made palette, a swatch per colour to change it, and a reset.</summary>
        <remarks>
        Colours are saved in the settings' [colours] section, only those that differ from
        the default. Changing the fan's colours makes the service draw and upload a new set
        of stored fan loops (about 2 MB) the next time the fan shows.
        </remarks>
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        presets = QHBoxLayout()
        presets.addWidget(QLabel("Ready made"))
        self._preset = QComboBox()
        self._preset.addItems(list(render.PRESETS))
        use = QPushButton("Use")
        use.clicked.connect(self._use_preset)
        presets.addWidget(self._preset, 1)
        presets.addWidget(use)
        layout.addLayout(presets)

        grid = QGridLayout()
        self._swatches: Dict[str, QPushButton] = {}
        for row, (key, label, _) in enumerate(render.PALETTE):
            swatch = QPushButton()
            swatch.setFixedSize(64, 24)
            swatch.clicked.connect(lambda _=False, key=key: self._pick_colour(key))
            grid.addWidget(QLabel(label), row // 2, (row % 2) * 2)
            grid.addWidget(swatch, row // 2, (row % 2) * 2 + 1)
            self._swatches[key] = swatch
        layout.addLayout(grid)

        reset = QPushButton("Back to the default colours")
        reset.clicked.connect(lambda: self._save({key: None for key, _, _ in render.PALETTE}, section="colours"))
        layout.addWidget(reset)
        note = QLabel("Changing the fan colours makes a new set of smooth fan loops, uploaded to the screen "
                      "the next time the fan shows (about 2 MB). Earlier sets stay on the screen's storage.",
                      wordWrap=True)
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)
        self._fan_loop_note = note
        layout.addStretch(1)
        return page

    def _colours(self) -> Dict[str, tuple]:
        """<summary>Every palette colour in use now: the settings' changes over the defaults.</summary>"""
        current = {key: colour for key, _, colour in render.PALETTE}
        current.update(dict(self._settings.palette))
        return current

    def _pick_colour(self, key: str) -> None:
        """<summary>Opens a colour picker for one palette colour and saves the choice; a default colour is stored as blank.</summary>"""
        start = QColor(*self._colours()[key])
        chosen = QColorDialog.getColor(start, self, dict((k, label) for k, label, _ in render.PALETTE)[key])
        if chosen.isValid():
            colour = (chosen.red(), chosen.green(), chosen.blue())
            default = dict((k, c) for k, _, c in render.PALETTE)[key]
            self._save({key: None if colour == default else config.format_colour(colour)}, section="colours")

    def _use_preset(self) -> None:
        """<summary>Replaces every colour with a ready made palette's, defaults for the ones it does not set.</summary>"""
        chosen = render.PRESETS[self._preset.currentText()]
        self._save({key: config.format_colour(chosen[key]) if key in chosen else None for key, _, _ in render.PALETTE},
                   section="colours")

    def _show_colours(self) -> None:
        """<summary>Paints each swatch in its current colour.</summary>"""
        for key, colour in self._colours().items():
            self._swatches[key].setStyleSheet("background-color: {}; border: 1px solid palette(mid);".format(
                config.format_colour(colour)))
            self._swatches[key].setToolTip(config.format_colour(colour))

    def _choose_folder(self) -> None:
        """
        <summary>Asks for the slideshow folder, saves it and switches to the slideshow, or keeps the Glitch slideshow.</summary>
        <remarks>
        Both slideshows use this folder. When the Glitch slideshow is on the screen or being
        previewed, it is kept: switching to the plain Slideshow here is what hid the glitch
        on 07/11/2026.
        </remarks>
        """
        start = str(self._settings.slideshow_folder or Path.home() / "Pictures")
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder of pictures", start)
        if chosen:
            glitch = "glitch" in (self._settings.theme, self._selected)
            self._save({"slideshow_folder": chosen, "theme": "glitch" if glitch else "slideshow"})

    def _slot_key(self, n: int) -> str:
        """<summary>The settings key for playlist slot n, counting from 0.</summary>"""
        return "playlist_{}".format(n + 1)

    def _slot_path(self, n: int) -> Optional[Path]:
        """<summary>The picture in playlist slot n as shown in the window, or None when empty.</summary>"""
        text = self._slots[n][0].text()
        return Path(text) if text else None

    def _choose_slot(self, n: int) -> None:
        """<summary>Asks for a picture for one playlist slot, saves it and switches to the playlist.</summary>"""
        start = str(self._slot_path(n).parent if self._slot_path(n) else Path.home() / "Pictures")
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose a picture", start, PICTURE_FILTER)
        if chosen:
            self._save({self._slot_key(n): config.format_playlist_entry(Path(chosen), self._slots[n][1].value()),
                        "theme": "playlist"})

    def _clear_slot(self, n: int) -> None:
        """<summary>Empties one playlist slot; the others keep their places.</summary>"""
        self._save({self._slot_key(n): ""})

    def _save_slot(self, n: int) -> None:
        """<summary>Saves a changed time for a filled slot; an empty slot has nothing to save.</summary>"""
        path = self._slot_path(n)
        if path is not None:
            self._save({self._slot_key(n): config.format_playlist_entry(path, self._slots[n][1].value())})

    def _build_burnin(self) -> QGroupBox:
        """
        <summary>The anti burn-in group: the pixel shift switch and the refresh controls.</summary>
        <remarks>
        The refresh only shows while the service is running, so the Clear button refuses
        to start one with the service stopped rather than saving an end time nobody sees.
        </remarks>
        """
        group = QGroupBox("Burn-in")
        layout = QVBoxLayout(group)

        self._shift = QCheckBox("Move the picture slightly every few minutes")
        self._shift.setToolTip("Shifts everything by two pixels every {} minutes so no edge stays "
                               "on the same pixels for hours.".format(burnin.SHIFT_MINUTES))
        self._shift.toggled.connect(lambda on: self._save({"pixel_shift": "yes" if on else "no"}))
        layout.addWidget(self._shift)

        row = QHBoxLayout()
        self._refresh_length = QComboBox()
        for label, minutes in burnin.REFRESH_CHOICES:
            self._refresh_length.addItem(label, minutes)
        self._refresh_length.setCurrentIndex(1)
        self._refresh_start = QPushButton("Clear burn-in")
        self._refresh_start.clicked.connect(self._start_refresh)
        self._refresh_stop = QPushButton("Stop")
        self._refresh_stop.clicked.connect(self._stop_refresh)
        row.addWidget(self._refresh_length, 1)
        row.addWidget(self._refresh_start)
        row.addWidget(self._refresh_stop)
        layout.addLayout(row)

        schedule = QHBoxLayout()
        self._daily = QCheckBox("Every day at")
        self._daily_at = QTimeEdit(displayFormat="HH:mm")
        self._daily_at.setTime(QTime(3, 0))
        self._daily_length = QComboBox()
        for label, minutes in burnin.REFRESH_CHOICES:
            self._daily_length.addItem("for " + label, minutes)
        self._daily.toggled.connect(lambda _: self._save_schedule())
        self._daily_at.timeChanged.connect(lambda _: self._save_schedule())
        self._daily_length.currentIndexChanged.connect(lambda _: self._save_schedule())
        schedule.addWidget(self._daily)
        schedule.addWidget(self._daily_at)
        schedule.addWidget(self._daily_length, 1)
        layout.addLayout(schedule)

        level = QHBoxLayout()
        self._refresh_bright = QCheckBox("Brightness during burn-in")
        self._refresh_bright.setToolTip("Used only while a refresh runs. Your normal brightness "
                                        "comes back when it ends.")
        self._refresh_level = QSpinBox(minimum=0, maximum=100, suffix=" %", value=100)
        self._refresh_bright.toggled.connect(lambda _: self._save_refresh_brightness())
        self._refresh_level.valueChanged.connect(lambda _: self._save_refresh_brightness())
        level.addWidget(self._refresh_bright)
        level.addWidget(self._refresh_level)
        level.addStretch(1)
        layout.addLayout(level)

        self._refresh_state = QLabel(wordWrap=True)
        layout.addWidget(self._refresh_state)
        note = QLabel("Cycles white, colours and random noise across the whole screen, then "
                      "returns to your screen as it was. A schedule needs the service running "
                      "at that time. Higher brightness works better. Long standing burn-in may "
                      "only fade, and some may be permanent.", wordWrap=True)
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)
        return group

    def _start_refresh(self) -> None:
        """<summary>Saves an end time for the refresh, if the service is running to show it.</summary>"""
        state, _ = self._service.status()
        if state not in ("active", "activating"):
            QMessageBox.information(self, "Clear burn-in", "Start the background service first: "
                                    "the refresh is drawn by the service.")
            return
        until = datetime.now() + timedelta(minutes=self._refresh_length.currentData())
        self._save({"refresh_until": until.isoformat(timespec="seconds")})

    def _save_refresh_brightness(self) -> None:
        """<summary>Saves the burn-in brightness, or a blank value when the box is unticked.</summary>"""
        self._refresh_level.setEnabled(self._refresh_bright.isChecked())
        level = self._refresh_level.value() if self._refresh_bright.isChecked() else None
        self._save({"refresh_brightness": level})

    def _save_schedule(self) -> None:
        """<summary>Saves the daily schedule: a blank time when the box is unticked.</summary>"""
        at = self._daily_at.time().toString("HH:mm") if self._daily.isChecked() else None
        self._save({"refresh_daily_at": at, "refresh_daily_minutes": self._daily_length.currentData()})

    def _scheduled_end(self, now: datetime) -> Optional[datetime]:
        """<summary>The end of the scheduled run in progress, ignoring one already stopped, or None.</summary>"""
        s = self._settings
        if s.refresh_skip_until is not None and now < s.refresh_skip_until:
            return None
        return burnin.scheduled_until(s.refresh_daily_at, s.refresh_daily_minutes, now)

    def _stop_refresh(self) -> None:
        """
        <summary>Ends the refresh on screen: clears one started by hand, or skips the rest of tonight's scheduled one.</summary>
        """
        now = datetime.now()
        if burnin.refreshing(self._settings.refresh_until, now):
            self._save({"refresh_until": None})
        else:
            end = self._scheduled_end(now)
            if end is not None:
                self._save({"refresh_skip_until": end.isoformat(timespec="seconds")})

    def _show_refresh_state(self) -> None:
        """<summary>Says what the refresh is doing and when the next scheduled one is, and enables the right buttons.</summary>"""
        s = self._settings
        now = datetime.now()
        manual = burnin.refreshing(s.refresh_until, now)
        scheduled = self._scheduled_end(now)
        if manual:
            text = "Clearing until {} on {}".format(s.refresh_until.strftime("%H:%M"),
                                                     s.refresh_until.strftime("%d/%m/%Y"))
        elif scheduled is not None:
            text = "Scheduled clearing until {}".format(scheduled.strftime("%H:%M"))
        else:
            text = "Not running"
        upcoming = burnin.next_scheduled(s.refresh_daily_at, now)
        if upcoming is not None and scheduled is None:
            text += ". Next scheduled: {} on {}".format(upcoming.strftime("%H:%M"), upcoming.strftime("%d/%m/%Y"))
        self._refresh_state.setText(text)
        self._refresh_start.setEnabled(not manual)
        self._refresh_stop.setEnabled(manual or scheduled is not None)

    # Settings --------------------------------------------------------------

    def _load(self) -> config.Settings:
        """<summary>Reads the settings, falling back to defaults and saying so if the file is broken.</summary>"""
        try:
            return config.load(self._path)
        except (ValueError, OSError, configparser.Error) as error:
            QMessageBox.warning(self, "Settings", "The settings file could not be read, so defaults "
                                "are shown.\n\n{}".format(error))
            return config.Settings()

    def _show_settings(self) -> None:
        """
        <summary>Puts the current settings into the controls without saving anything back.</summary>
        <remarks>The loading flag is restored however this ends, or one bad value would leave every later change unsaved.</remarks>
        """
        was_loading, self._loading = self._loading, True
        try:
            self._fill_controls()
        finally:
            self._loading = was_loading

    def _fill_controls(self) -> None:
        """<summary>The body of _show_settings(): every control set from self._settings.</summary>"""
        s = self._settings
        self._show_device()
        self._brightness.setValue(s.brightness)
        self._brightness_value.setText("{}%".format(s.brightness))
        self._rotation.setCurrentIndex(config.ROTATIONS.index(s.rotation))
        self._interval.setValue(s.interval)
        self._fan_video.setChecked(s.fan_mode == "video")
        self._picture.setText(str(s.background) if s.background else "")
        self._fan_picture.setText(str(s.fan_picture) if s.fan_picture else "")
        self._picture_brightness.setValue(s.picture_brightness)
        self._picture_contrast.setValue(s.picture_contrast)
        self._show_zoom()
        self._folder.setText(str(s.slideshow_folder) if s.slideshow_folder else "")
        self._folder_seconds.setValue(s.slideshow_seconds)
        count = len(pictures.folder_pictures(s.slideshow_folder))
        self._folder_count.setText("" if s.slideshow_folder is None else
                                   "{} picture{} found".format(count, "" if count == 1 else "s"))
        for (name, seconds), slot in zip(self._slots, config.playlist_slots(self._path)):
            name.setText(str(slot[0]) if slot else "")
            if slot:
                seconds.setValue(slot[1])
        self._shift.setChecked(s.pixel_shift)
        self._show_rules()
        self._show_colours()
        self._cycle_group.setChecked(s.cycle_on)
        chosen = dict(s.cycle)
        for key in self._cycle_ticks:
            self._cycle_ticks[key].setChecked(key in chosen)
            if key in chosen:
                self._cycle_times[key].setValue(chosen[key])
        self._show_cycle_state()
        self._refresh_bright.setChecked(s.refresh_brightness is not None)
        if s.refresh_brightness is not None:
            self._refresh_level.setValue(s.refresh_brightness)
        self._refresh_level.setEnabled(s.refresh_brightness is not None)
        self._daily.setChecked(s.refresh_daily_at is not None)
        if s.refresh_daily_at is not None:
            self._daily_at.setTime(QTime(s.refresh_daily_at.hour, s.refresh_daily_at.minute))
        index = self._daily_length.findData(s.refresh_daily_minutes)
        if index < 0:  # a length typed into the file by hand: offer it rather than lose it
            self._daily_length.addItem("for {} minutes".format(s.refresh_daily_minutes), s.refresh_daily_minutes)
            index = self._daily_length.count() - 1
        self._daily_length.setCurrentIndex(index)
        self._show_refresh_state()
        if self._selected is None:  # first fill only: after that the selection is the user's
            item = self._theme_items.get(s.theme)
            if item is not None:
                self._themes.setCurrentItem(item)

    def _available(self, key: str) -> bool:
        """
        <summary>Whether the detected cooler can show a theme.</summary>
        <param name="key">A key of THEMES.</param>
        <returns>False only for a screen that needs pump and fan readings on a cooler without them.</returns>
        """
        return self._device.cooler_sensors or not THEMES[key].needs_cooler

    def _show_device(self) -> None:
        """
        <summary>Detects the cooler again and fits the window to it.</summary>
        <remarks>
        Hides screens it cannot show (thumbnails, cycle ticks, the Smart picture fallback)
        and, without stored video, the stored video option and the fan loop note. Hidden,
        not removed: a hidden screen already saved as the theme or fallback stays saved
        until another is picked. The cycle ticks are laid out again so no gap is left.
        </remarks>
        """
        s = self._settings
        self._device = devices.detect(s.cooler)
        index = self._cooler.findData(s.cooler)
        self._cooler.setCurrentIndex(max(index, 0))
        found = "{}, {} by {} pixels".format(self._device.name, self._device.size, self._device.size)
        self._cooler_found.setText(("Found: " if s.cooler == "auto" else "Set by hand: ") + found)
        self.setWindowTitle("Space LCD: {}".format(self._device.name))

        for key, item in self._theme_items.items():
            item.setHidden(not self._available(key))
        grid = self._cycle_ticks["dial"].parentWidget().layout()
        shown = [key for key in self._cycle_ticks if self._available(key)]
        for key in self._cycle_ticks:
            self._cycle_ticks[key].setHidden(key not in shown)
            self._cycle_times[key].setHidden(key not in shown)
        for row, key in enumerate(shown):
            grid.addWidget(self._cycle_ticks[key], row // 2, (row % 2) * 2)
            grid.addWidget(self._cycle_times[key], row // 2, (row % 2) * 2 + 1)
        grid.addWidget(self._cycle_state, (len(shown) + 1) // 2, 0, 1, 4)
        for row in range(self._fallback.count()):
            self._fallback.view().setRowHidden(row, not self._available(self._fallback.itemData(row)))

        self._screen_form.setRowVisible(self._fan_video, self._device.stored_video)
        self._fan_loop_note.setHidden(not self._device.stored_video)

    def _save(self, values: Dict[str, object], section: str = "display") -> bool:
        """
        <summary>Writes changed values to the settings file and reloads them.</summary>
        <returns>False when nothing was written: while loading, or after a failure the user has been told about.</returns>
        <remarks>
        Ignored while the controls are being filled in, so loading never writes. A
        brightness still waiting on its timer is written first (into the display section,
        whatever section this save is for), since refilling the controls would otherwise
        put the slider back to the file's value before the timer fired.
        </remarks>
        """
        if self._loading:
            return False
        try:
            if self._save_timer.isActive() or not values:
                self._save_timer.stop()
                if self._brightness.value() != self._settings.brightness:
                    config.save({"brightness": self._brightness.value()}, self._path)
            if values:
                config.save(values, self._path, section=section)
            self._settings = config.load(self._path)
        except (ValueError, OSError, configparser.Error) as error:
            QMessageBox.warning(self, "Settings", "Could not save the setting.\n\n{}".format(error))
            return False
        self._show_settings()
        self._refresh_previews(read=False)
        self.statusBar().showMessage("Saved", 2000)
        return True

    def _brightness_moved(self, value: int) -> None:
        """<summary>Updates the figure at once and saves once the slider has settled.</summary>"""
        self._brightness_value.setText("{}%".format(value))
        if not self._loading:
            self._save_timer.start()

    def _theme_chosen(self, current: Optional[QListWidgetItem], previous) -> None:
        """
        <summary>Previews the theme clicked in the list, animating it if it moves; nothing is saved or sent.</summary>
        <remarks>The thumbnail it leaves is put back to a still frame, so only one ever moves.</remarks>
        """
        if current is None:
            return
        left = self._selected
        self._selected = current.data(Qt.ItemDataRole.UserRole)
        self._description.setText(THEMES[self._selected].description)
        if left is not None and left != self._selected and left in self._theme_items:
            self._draw_thumbnail(left)
        if THEMES[self._selected].interval is not None:
            self._animate_timer.start()
        else:
            self._animate_timer.stop()
        self._draw_selected()

    def _theme_menu(self, point: QPoint) -> None:
        """<summary>The right click menu on a theme: Send to device.</summary>"""
        item = self._themes.itemAt(point)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        send = QAction("Send to device", menu)
        send.triggered.connect(lambda: self._send(key))
        menu.addAction(send)
        menu.exec(self._themes.viewport().mapToGlobal(point))

    def _send(self, key: str) -> None:
        """
        <summary>Saves a theme as the one the screen shows; the service picks it up within an update.</summary>
        <remarks>
        Turns the cycle off as well when it is on, since otherwise the cycle would go on
        choosing and the theme sent would not appear; the status bar says so.
        </remarks>
        """
        values = {"theme": key}
        cycling = self._settings.cycle_on
        if cycling:
            values["cycle_on"] = "no"
        if not self._save(values):
            return
        self.statusBar().showMessage("Sent {} to the screen{}".format(
            THEMES[key].name, " and turned the cycle off" if cycling else ""), 4000)

    def _choose_fan_picture(self) -> None:
        """<summary>Asks for the picture behind the fan, saves it, and previews the Fan over a picture without sending it.</summary>"""
        current = self._settings.fan_picture
        start = str(current.parent) if current else str(Path.home() / "Pictures")
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose a picture for behind the fan", start, PICTURE_FILTER)
        if chosen:
            self._save({"fan_picture": chosen})
            item = self._theme_items.get("fan_reveal")
            if item is not None:
                self._themes.setCurrentItem(item)

    def _choose_picture(self) -> None:
        """<summary>Asks for a picture, saves it, and switches to the picture theme.</summary>"""
        start = str(self._settings.background.parent) if self._settings.background else str(Path.home() / "Pictures")
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose a picture", start, PICTURE_FILTER)
        if chosen:
            self._save({"background": chosen, "theme": "image"})

    # Refresh ---------------------------------------------------------------

    def _frame(self, key: str, rotation: int = 0) -> Image.Image:
        """
        <summary>One frame of a theme as the detected cooler would show it now, with its picture and, for the glitch slideshow, its change.</summary>
        <remarks>The Smart picture is drawn as whatever its rules choose now. The palette must already be applied.</remarks>
        """
        s = self._settings
        now = datetime.now()
        if key == "smart":
            key, background, _, _ = screens.effective(replace(s, cycle_on=False, theme="smart"), self._stats, now)
        else:
            background = screens.picture(s, key, now)[0]
        previous, age = screens.transition(s, key, now)
        zoom = screens.zoom_state(s, now) if key == "zoom" else None
        return render.draw_frame(self._stats, rotation, theme=key, background=background, size=self._device.size,
                                 previous=previous, age=age, zoom=zoom)

    def _draw_thumbnail(self, key: str) -> None:
        """<summary>Draws one thumbnail as a still frame.</summary>"""
        render.apply_palette(dict(self._settings.palette))
        render.apply_pictures(self._settings.picture_brightness, self._settings.picture_contrast)
        self._theme_items[key].setIcon(to_icon(self._frame(key), THUMB_SIZE))

    def _draw_selected(self) -> None:
        """
        <summary>Draws the selected theme into the large preview and its own thumbnail: one frame, used for both.</summary>
        <remarks>The animation timer calls this ten times a second while an animated theme is selected; nothing else moves.</remarks>
        """
        key = self._selected
        if key is None or key not in self._theme_items:
            return
        render.apply_palette(dict(self._settings.palette))
        render.apply_pictures(self._settings.picture_brightness, self._settings.picture_contrast)
        frame = self._frame(key)
        self._theme_items[key].setIcon(to_icon(frame, THUMB_SIZE))
        rotation = self._settings.rotation
        self._preview.setPixmap(to_pixmap(frame.rotate(-rotation) if rotation else frame, PREVIEW_SIZE))

    def _tick_preview(self) -> None:
        """<summary>Every PREVIEW_MS: fresh readings, the rule and screen state, and the selected theme when it is still.</summary>"""
        self._stats = self._reader.read()
        self._show_rule_state()
        self._show_on_screen()
        if not self._animate_timer.isActive():
            self._draw_selected()

    def _show_on_screen(self) -> None:
        """<summary>Says which screen the cooler is showing now, which may differ from the one previewed.</summary>"""
        showing = screens.effective(self._settings, self._stats, datetime.now())[0]
        name = THEMES[showing].name if showing in THEMES else showing
        self._on_screen.setText("On the screen now: {}. Right click a theme to send it.".format(name))

    def _refresh_previews(self, read: bool = True) -> None:
        """
        <summary>Redraws every shown thumbnail, still, then the selected theme.</summary>
        <param name="read">Take fresh readings first. False when only a setting changed.</param>
        <remarks>
        Drawn at the detected cooler's own size, with its small layouts, so what is seen here
        is what that screen gets. Called on opening and after each save, not on a timer: it
        is the one costly redraw.
        </remarks>
        """
        if read:
            self._stats = self._reader.read()
            self._show_rule_state()
        for key, item in self._theme_items.items():
            if not item.isHidden() and key != self._selected:
                self._draw_thumbnail(key)
        self._draw_selected()
        self._show_on_screen()

    def _refresh_status(self) -> None:
        """
        <summary>Shows whether the service is running, and enables the buttons that make sense.</summary>
        <remarks>
        The start box is ticked only when the unit is enabled and lingering is on: enabled
        alone (as 1.0.22 left it) starts only at login, so it shows unticked, and ticking it
        turns lingering on.
        </remarks>
        """
        state, enabled = self._service.status()
        enabled = enabled and self._service.lingering()
        self._state.setText(STATE_TEXT.get(state, state.capitalize()))
        installed = state != "not installed"
        running = state in ("active", "activating")
        self._start.setEnabled(installed and not running)
        self._stop.setEnabled(installed and running)
        self._restart.setEnabled(installed and running)
        self._at_boot.setEnabled(installed)
        self._at_boot.setChecked(enabled)
        self._show_refresh_state()
        self._show_cycle_state()

    def _service_action(self, action) -> None:
        """<summary>Runs one service command, reports a failure, then refreshes the status.</summary>"""
        code, message = action()
        if code != 0:
            QMessageBox.warning(self, "Background service", message or "systemctl reported an error.")
        self._refresh_status()

    def closeEvent(self, event) -> None:
        """<summary>Writes a brightness still waiting on its timer before the window goes.</summary>"""
        if self._save_timer.isActive():
            self._save({})
        super().closeEvent(event)
