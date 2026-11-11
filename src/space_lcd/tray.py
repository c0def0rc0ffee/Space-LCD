"""
<summary>
The system tray icon: python -m space_lcd.ui --tray. Opens the control panel from the
tray, closes it again while it is open, and quits the tray itself.
</summary>
<remarks>
The panel runs inside the tray's own process, built when it is opened and thrown away when
it is closed, so a closed panel costs no preview drawing and no sensor reads. Closing the
panel by its title bar and by the tray do the same thing, and either way any brightness
still waiting on its timer is saved first (ControlPanel.closeEvent).

Only one tray runs per user. A second start finds the first through a local socket and
asks it to open the panel, then exits; that is how the menu entry reaches a tray that is
already running instead of opening a second panel beside it.

Neither the tray nor the panel sends anything to the screen. Quitting the tray leaves the
background service exactly as it was.

On KDE Plasma the icon is a StatusNotifierItem over D-Bus, which Qt does by itself. At
login the tray may start before the panel that shows it, so the icon waits up to
TRAY_WAIT_S for a tray area rather than giving up at once.
</remarks>
"""

import getpass
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon

SHOW = b"show\n"
"""<summary>The one message a second start sends the running tray: open the panel and bring it forward.</summary>"""

TRAY_WAIT_S = 60
TRAY_POLL_MS = 2000
"""<summary>How long, and how often, the tray looks for a tray area to show its icon in before giving up.</summary>"""


def server_name() -> str:
    """
    <summary>The local socket name that marks this user's running tray.</summary>
    <remarks>Carries the user name, so two people logged in at once each get their own tray.</remarks>
    """
    return "space-lcd-tray-" + getpass.getuser()


def ask_running_tray(name: str, wait_ms: int = 500) -> bool:
    """
    <summary>Asks an already running tray to open the panel.</summary>
    <param name="name">The socket name from server_name(); tests pass their own.</param>
    <param name="wait_ms">How long to wait for the connection and for the message to go.</param>
    <returns>True when a tray answered, so this process should exit; False when none is running.</returns>
    """
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(wait_ms):
        return False
    socket.write(SHOW)
    socket.waitForBytesWritten(wait_ms)
    socket.disconnectFromServer()
    return True


class Tray(QObject):
    """
    <summary>The tray icon, its menu, and the one control panel it opens and closes.</summary>
    <remarks>
    A left click opens the panel when it is closed and closes it when it is open. The menu
    has the same two actions spelled out, only the one that makes sense enabled, and Quit.
    make_panel builds a fresh ControlPanel each time; the tray never keeps a closed one.
    </remarks>
    """

    def __init__(self, app: QApplication, make_panel: Callable[[], QMainWindow], icon: QIcon,
                 name: Optional[str] = None) -> None:
        """
        <summary>Builds the icon and menu, and starts listening for a second start.</summary>
        <param name="app">The application; the tray quits it from the menu.</param>
        <param name="make_panel">Builds a new control panel window; a fake in tests.</param>
        <param name="icon">The tray icon picture.</param>
        <param name="name">The single instance socket name; server_name() when left out.</param>
        <remarks>
        A socket left behind by a tray that crashed would block the name, so it is removed
        before listening. That is safe only because ask_running_tray() has already found
        nothing answering on it.
        </remarks>
        """
        super().__init__()
        self._app = app
        self._make_panel = make_panel
        self._panel: Optional[QMainWindow] = None

        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        name = name or server_name()
        QLocalServer.removeServer(name)
        self._server.listen(name)
        self._server.newConnection.connect(self._take_connection)

        self._menu = QMenu()
        self._open = QAction("Open control panel", self._menu)
        self._open.triggered.connect(self.open_panel)
        self._close = QAction("Close control panel", self._menu)
        self._close.triggered.connect(self.close_panel)
        quit_action = QAction("Quit tray icon", self._menu)
        quit_action.triggered.connect(self.quit)
        self._menu.addAction(self._open)
        self._menu.addAction(self._close)
        self._menu.addSeparator()
        self._menu.addAction(quit_action)

        self.icon = QSystemTrayIcon(icon, self)
        self.icon.setToolTip("Space LCD")
        self.icon.setContextMenu(self._menu)
        self.icon.activated.connect(self._activated)
        self._show_state()

        self._waited_ms = 0
        self._wait_timer = QTimer(self, interval=TRAY_POLL_MS)
        self._wait_timer.timeout.connect(self._try_show_icon)

    def start(self) -> bool:
        """
        <summary>Shows the icon now, or keeps trying for TRAY_WAIT_S if no tray area is up yet.</summary>
        <returns>True when the icon is showing straight away.</returns>
        """
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.icon.show()
            return True
        self._wait_timer.start()
        return False

    def _try_show_icon(self) -> None:
        """
        <summary>One retry while waiting for a tray area at login.</summary>
        <remarks>
        On giving up it opens the panel, so the user still has a way in and a way to quit,
        and says why on stderr, which the desktop's own log keeps.
        </remarks>
        """
        self._waited_ms += TRAY_POLL_MS
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._wait_timer.stop()
            self.icon.show()
        elif self._waited_ms >= TRAY_WAIT_S * 1000:
            self._wait_timer.stop()
            print("space_lcd.tray: no system tray found; opening the control panel instead", file=sys.stderr)
            self._app.setQuitOnLastWindowClosed(True)
            self.open_panel()

    @property
    def panel(self) -> Optional[QMainWindow]:
        """<summary>The open control panel, or None while it is closed.</summary>"""
        return self._panel

    def open_panel(self) -> None:
        """
        <summary>Opens the control panel, or brings it forward if it is already open.</summary>
        <remarks>
        Under Wayland a window cannot take the focus by itself, so an open panel that is
        behind other windows may only flash in the task bar rather than come to the front.
        </remarks>
        """
        if self._panel is None:
            panel = self._make_panel()
            panel.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            panel.destroyed.connect(self._panel_gone)
            self._panel = panel
        self._panel.show()
        self._panel.setWindowState(self._panel.windowState() & ~Qt.WindowState.WindowMinimized)
        self._panel.raise_()
        self._panel.activateWindow()
        self._show_state()

    def _busy(self) -> bool:
        """
        <summary>Whether the panel has a dialog open (a file or colour picker, the zoom point, a warning).</summary>
        <remarks>
        Closing the panel then would delete it under the dialog, and the code waiting on the
        dialog would carry on with a window that is gone. So Close and Quit bring the panel
        and its dialog forward instead, and the dialog is answered first.
        </remarks>
        """
        return self._panel is not None and QApplication.activeModalWidget() is not None

    def close_panel(self) -> None:
        """<summary>Closes the control panel, saving anything still waiting, as its own close button does.</summary>"""
        if self._busy():
            self.open_panel()
            QApplication.activeModalWidget().raise_()
            return
        if self._panel is not None:
            self._panel.close()

    def quit(self) -> None:
        """<summary>Closes the panel first, so its pending saves happen, then ends the tray process; not while a dialog is open.</summary>"""
        if self._busy():
            self.close_panel()
            return
        self.close_panel()
        self.icon.hide()
        self._server.close()
        self._app.quit()

    def _panel_gone(self) -> None:
        """<summary>Forgets the panel once Qt has deleted it after closing.</summary>"""
        self._panel = None
        self._show_state()

    def _show_state(self) -> None:
        """<summary>Enables Open or Close in the menu to match whether the panel is open.</summary>"""
        is_open = self._panel is not None
        self._open.setEnabled(not is_open)
        self._close.setEnabled(is_open)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """<summary>A left click toggles the panel: open when closed, closed when open.</summary>"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self._panel is None:
                self.open_panel()
            else:
                self.close_panel()

    def _take_connection(self) -> None:
        """
        <summary>Reads what a second start sent and opens the panel if it asked.</summary>
        <remarks>Anything else is ignored: only this user can connect (UserAccessOption).</remarks>
        """
        socket = self._server.nextPendingConnection()
        if socket is None:
            return

        def read() -> None:
            """<summary>Acts on the message once it has arrived; it can come after the connection.</summary>"""
            if bytes(socket.readAll()).startswith(SHOW.strip()):
                self.open_panel()
            socket.disconnectFromServer()

        socket.readyRead.connect(read)
        socket.disconnected.connect(socket.deleteLater)
        if socket.bytesAvailable():
            read()


def run(app: QApplication, make_panel: Callable[[], QMainWindow], icon_path: Path, show: bool) -> int:
    """
    <summary>Starts the tray, or hands over to one that is already running.</summary>
    <param name="app">The application, already made by ui.main().</param>
    <param name="make_panel">Builds a new control panel window.</param>
    <param name="icon_path">The app icon install.sh writes; a stock icon is used if it is missing.</param>
    <param name="show">Open the panel straight away, as the menu entry asks.</param>
    <returns>The process exit status.</returns>
    <remarks>
    A second start always asks the running tray to open the panel, with or without show:
    starting the tray again by hand is taken as wanting to see it.
    </remarks>
    """
    if ask_running_tray(server_name()):
        return 0
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(str(icon_path)) if icon_path.is_file() else QIcon.fromTheme("video-display")
    tray = Tray(app, make_panel, icon)
    tray.start()
    if show:
        tray.open_panel()
    return app.exec()
