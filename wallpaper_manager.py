#!/usr/bin/env python3
"""
================================================================================
Wallpaper Motor - Live Animated Stream Wallpaper Manager
================================================================================

System Dependencies:
- python-pyqt6 (PyQt6 python bindings)
- yt-dlp (YouTube stream resolution engine)
- ffmpeg (Media processing backend for mpv)
- mpv (Media player backend)
- xwinwrap (X11 desktop overlay window wrapper)

Install on openSUSE:
  sudo zypper in python3-PyQt6 yt-dlp ffmpeg mpv
  (xwinwrap needs to be compiled or installed from a community package/copr)

Description:
  A clean, modern dark-themed GUI application for openSUSE XFCE to manage and
  deploy live animated stream wallpapers. Features embedded live previews,
  system tray integration, and robust zombie process prevention.
"""

import sys
import os
import json
import subprocess
import signal
import atexit
import math
from pathlib import Path
from PyQt6.QtCore import Qt, QSize, QTimer, QPoint, QPointF, pyqtSlot, QThread, pyqtSignal
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QLineEdit,
    QComboBox, QFormLayout, QDialog, QDialogButtonBox, QCheckBox,
    QStackedWidget, QSystemTrayIcon, QMenu, QMessageBox, QFrame,
    QSplitter, QStatusBar, QSizePolicy, QFileDialog, QRadioButton,
    QTabWidget
)
from PyQt6.QtGui import QIcon, QFont, QAction, QColor, QPainter, QPen, QBrush, QPolygonF, QFontMetrics

# ==============================================================================
# Wayland Block & Elided Label Helpers
# ==============================================================================

class WaylandErrorDialog(QDialog):
    """
    Shown at startup when a Wayland session is detected.
    Halts the onboarding flow and presents an unskippable error message.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wayland Session Detected")
        self.setMinimumWidth(500)
        self.setWindowIcon(create_app_icon())
        self.setStyleSheet("""
            QDialog {
                background-color: #121214;
            }
            QFrame#we-header {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4a1e1e, stop:1 #121214);
                border-bottom: 1px solid #7a2d2d;
            }
            QLabel#we-title {
                font-size: 17px;
                font-weight: 800;
                color: #ef233c;
            }
            QLabel#we-body {
                font-size: 13px;
                color: #e2e2e9;
                line-height: 1.4;
            }
            QPushButton#we-btn-exit {
                background-color: #d90429;
                border: 1px solid #b3001e;
                border-radius: 6px;
                padding: 9px 18px;
                color: #ffffff;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton#we-btn-exit:hover {
                background-color: #ef233c;
            }
        """)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        # Header banner
        header = QFrame()
        header.setObjectName("we-header")
        header.setFixedHeight(90)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(24, 0, 24, 0)
        h_layout.setSpacing(16)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(create_app_icon().pixmap(QSize(52, 52)))
        h_layout.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_lbl = QLabel("Incompatible Environment")
        title_lbl.setObjectName("we-title")
        text_col.addWidget(title_lbl)
        h_layout.addLayout(text_col)
        root.addWidget(header)

        # Message body
        body_lbl = QLabel(
            "Wayland Session Detected. This application relies directly on X11 root windows "
            "via xwinwrap and cannot function on Wayland. Please log out and switch to an "
            "X11/XFCE session to continue."
        )
        body_lbl.setObjectName("we-body")
        body_lbl.setWordWrap(True)
        body_lbl.setContentsMargins(24, 0, 24, 0)
        root.addWidget(body_lbl)

        # Exit button row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(24, 0, 24, 24)
        btn_row.addStretch()

        btn_exit = QPushButton("Exit Application")
        btn_exit.setObjectName("we-btn-exit")
        btn_exit.clicked.connect(self.accept)
        btn_row.addWidget(btn_exit)
        root.addLayout(btn_row)

    # Disable escaping or closing the window without exiting
    def reject(self):
        self.accept()


class ElidedLabel(QLabel):
    """A QLabel that automatically truncates its text with an ellipsis when resized."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.full_text = text
        self.setToolTip(text)
        
    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        super().setText(text)
        self.update_elided_text()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_elided_text()
        
    def update_elided_text(self):
        fm = QFontMetrics(self.font())
        width = self.width() - 4
        if width <= 0:
            return
        elided = fm.elidedText(self.full_text, Qt.TextElideMode.ElideRight, width)
        super().setText(elided)

# ==============================================================================
# Dynamic Icon Generation (with $APPDIR-aware path resolution)
# ==============================================================================

ICON_RELATIVE_PATH = "usr/share/icons/hicolor/256x256/apps/wallpaper-motor.png"


def _resolve_icon_path() -> str | None:
    """
    Resolves the path to the application PNG icon at runtime.

    Priority order (for AppImage stability):
      1. $APPDIR/usr/share/icons/... — AppImage runtime (APPDIR env var is set
         by both the AppImage runtime and our AppRun script).
      2. <script_dir>/../share/icons/... — system-installed layout.
      3. <script_dir>/assets/wallpaper-motor.png — development checkout.
      4. None — fall back to the dynamically generated QPainter icon.
    """
    candidates = []

    # 1. AppImage runtime path
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(os.path.join(appdir, ICON_RELATIVE_PATH))

    # 2. Installed layout (relative to this script)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, "..", "share", "icons",
                                   "hicolor", "256x256", "apps",
                                   "wallpaper-motor.png"))

    # 3. Development / repository checkout
    candidates.append(os.path.join(script_dir, "assets", "wallpaper-motor.png"))

    for path in candidates:
        resolved = os.path.normpath(path)
        if os.path.isfile(resolved):
            return resolved

    return None


def create_app_icon():
    """
    Returns the application QIcon.

    Attempts to load a real PNG icon from the filesystem first (required for
    stable tray icon rendering inside an AppImage). Falls back to a dynamically
    generated QPainter icon when no file is found.
    """
    from PyQt6.QtGui import QPixmap

    icon_path = _resolve_icon_path()
    if icon_path:
        icon = QIcon(icon_path)
        # QIcon.isNull() returns True if the file failed to load
        if not icon.isNull():
            return icon

    # ── Fallback: generate icon dynamically via QPainter ─────────────────────
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Draw dark rounded background
    painter.setBrush(QBrush(QColor("#1e1e24")))
    painter.setPen(QPen(QColor("#2d2d34"), 2))
    painter.drawRoundedRect(4, 4, 56, 56, 12, 12)

    # Draw screen border (cyan accent)
    painter.setBrush(QBrush(QColor("#121214")))
    painter.setPen(QPen(QColor("#00b4d8"), 3))
    painter.drawRoundedRect(12, 14, 40, 28, 4, 4)

    # Draw screen stand
    painter.setPen(QPen(QColor("#00b4d8"), 3))
    painter.drawLine(32, 42, 32, 48)
    painter.drawLine(24, 48, 40, 48)

    # Draw play triangle in the center
    painter.setBrush(QBrush(QColor("#00b4d8")))
    painter.setPen(Qt.PenStyle.NoPen)
    triangle = QPolygonF([
        QPointF(28, 22),
        QPointF(28, 34),
        QPointF(38, 28)
    ])
    painter.drawPolygon(triangle)
    painter.end()
    return QIcon(pixmap)


def create_star_icon(filled=True):
    """Generates a clean vector-based star icon to represent favorite streams."""
    from PyQt6.QtGui import QPixmap
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    star_color = QColor("#ffb703") if filled else QColor("#5a5a65")
    painter.setBrush(QBrush(star_color) if filled else QBrush(Qt.GlobalColor.transparent))
    painter.setPen(QPen(star_color, 2))
    
    center = 16
    r_outer = 12
    r_inner = 5
    points = []
    for i in range(10):
        angle = i * math.pi / 5 - math.pi / 2
        r = r_outer if i % 2 == 0 else r_inner
        points.append(QPointF(center + r * math.cos(angle), center + r * math.sin(angle)))
        
    painter.drawPolygon(QPolygonF(points))
    painter.end()
    return QIcon(pixmap)


# ==============================================================================
# Database / Storage Module
# ==============================================================================

class DatabaseManager:
    """Manages CRUD operations on the local stream configuration JSON file."""
    def __init__(self):
        self.config_dir = Path.home() / ".config" / "stream-wallpaper-manager"
        self.config_file = self.config_dir / "streams.json"
        self.streams = []
        self.load_streams()
        
    def load_streams(self):
        if not self.config_dir.exists():
            self.config_dir.mkdir(parents=True, exist_ok=True)
            
        if not self.config_file.exists():
            self.streams = []
            self.save_streams()
        else:
            try:
                with open(self.config_file, "r") as f:
                    self.streams = json.load(f)
            except Exception as e:
                print(f"Error loading stream database: {e}")
                self.streams = []
                
    def save_streams(self):
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.streams, f, indent=4)
        except Exception as e:
            print(f"Error saving stream database: {e}")


# ==============================================================================
# Background Process Manager
# ==============================================================================

class ProcessManager:
    """Spawns, monitors, and cleanly terminates background preview and wallpaper players."""
    def __init__(self):
        self.preview_process = None
        self.wallpaper_process = None
        self.is_paused = False
        
    def start_preview(self, url, win_id):
        """Starts a small embedded mpv preview window inside the QWidget."""
        self.stop_preview()
        
        # We limit the width/height to save network bandwidth and loading time
        cmd = [
            "mpv",
            f"--wid={win_id}",
            "--no-osc",
            "--osd-level=0",
            "--input-default-bindings=no",
            "--input-vo-keyboard=no",
            "--no-audio",
            "--loop-file=inf",
            "--ytdl-format=bestvideo[height<=480]/bestvideo[height<=720]/best",
            url
        ]
        
        try:
            self.preview_process = subprocess.Popen(
                cmd,
                preexec_fn=os.setsid,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f"Failed to start mpv preview process: {e}")
            
    def stop_preview(self):
        """Kills the active preview player process."""
        if self.preview_process:
            self.terminate_process_group(self.preview_process)
            self.preview_process = None
            
    def start_wallpaper(self, url, resolution):
        """Deploys a live stream wallpaper in the background using xwinwrap and mpv."""
        self.stop_wallpaper()
        
        # Deploy wallpaper using the user specified structure
        cmd = [
            "xwinwrap",
            "-ov",
            "-g", resolution,
            "--",
            "mpv",
            "-wid", "WID",
            "--no-osc",
            "--osd-level=0",
            "--input-default-bindings=no",
            "--input-vo-keyboard=no",
            "--no-audio",
            "--keep-open=yes",
            "--loop-file=inf",
            "--ytdl-format=bestvideo",
            url
        ]
        
        try:
            self.wallpaper_process = subprocess.Popen(
                cmd,
                preexec_fn=os.setsid,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.is_paused = False
        except Exception as e:
            print(f"Failed to deploy live wallpaper: {e}")
            
    def stop_wallpaper(self):
        """Kills the background xwinwrap and mpv wallpaper process group."""
        if self.wallpaper_process:
            self.terminate_process_group(self.wallpaper_process)
            self.wallpaper_process = None
            self.is_paused = False
            
    def pause_wallpaper(self):
        """Sends SIGSTOP to pause playback of the wallpaper process group (saving CPU)."""
        if not self.wallpaper_process:
            return False
        try:
            pgid = os.getpgid(self.wallpaper_process.pid)
            os.killpg(pgid, signal.SIGSTOP)
            self.is_paused = True
            return True
        except Exception as e:
            print(f"Failed to pause wallpaper process group: {e}")
            return False
            
    def resume_wallpaper(self):
        """Sends SIGCONT to resume playback of the wallpaper process group."""
        if not self.wallpaper_process:
            return False
        try:
            pgid = os.getpgid(self.wallpaper_process.pid)
            os.killpg(pgid, signal.SIGCONT)
            self.is_paused = False
            return True
        except Exception as e:
            print(f"Failed to resume wallpaper process group: {e}")
            return False
            
    def terminate_process_group(self, proc):
        """Robustly terminates a subprocess and all its children via pgid."""
        if proc is None:
            return
        try:
            pid = proc.pid
            pgid = os.getpgid(pid)
            # Try soft SIGTERM first
            os.killpg(pgid, signal.SIGTERM)
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            # Force kill if still lingering
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass
        except Exception:
            # Fallback to direct process termination
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                    
    def clean_up_all(self):
        """Kills all processes launched by the application (prevents zombie processes)."""
        self.stop_preview()
        self.stop_wallpaper()


# ==============================================================================
# Startup Onboarding — Distro-Aware Dependency Checker
# ==============================================================================

import shutil

# Tools that must be on the HOST system.
# xwinwrap is intentionally absent here: when running from the AppImage it is
# bundled in $APPDIR/usr/bin (already on PATH via AppRun). When running from
# source it will still be found by shutil.which if the user has it installed.
HOST_DEPS = [
    {
        "binary": "mpv",
        "label": "mpv",
        "description": "Video player engine that renders the stream onto the desktop.",
        "pkg": "mpv",
    },
    {
        "binary": "ffmpeg",
        "label": "ffmpeg",
        "description": "Media processing library used by mpv for decoding and muxing.",
        "pkg": "ffmpeg",
    },
]

# When running from source (not AppImage) xwinwrap must also be present.
_XWINWRAP_DEP = {
    "binary": "xwinwrap",
    "label": "xwinwrap",
    "description": "X11 window overlay wrapper — places the video behind all desktop icons.",
    "pkg": "xwinwrap",
}


# ---------------------------------------------------------------------------
# Distro detection
# ---------------------------------------------------------------------------

# Per-distro install commands: (distro_id_fragment, friendly_name, install_template)
# Checked against /etc/os-release ID and ID_LIKE fields.
_DISTRO_TABLE = [
    ("opensuse",   "openSUSE",         "sudo zypper install {pkg}"),
    ("suse",       "SUSE",              "sudo zypper install {pkg}"),
    ("ubuntu",     "Ubuntu",            "sudo apt install {pkg}"),
    ("debian",     "Debian",            "sudo apt install {pkg}"),
    ("linuxmint",  "Linux Mint",        "sudo apt install {pkg}"),
    ("pop",        "Pop!_OS",           "sudo apt install {pkg}"),
    ("elementary", "elementary OS",     "sudo apt install {pkg}"),
    ("kali",       "Kali Linux",        "sudo apt install {pkg}"),
    ("fedora",     "Fedora",            "sudo dnf install {pkg}"),
    ("centos",     "CentOS",            "sudo dnf install {pkg}"),
    ("rhel",       "Red Hat",           "sudo dnf install {pkg}"),
    ("almalinux",  "AlmaLinux",         "sudo dnf install {pkg}"),
    ("rocky",      "Rocky Linux",       "sudo dnf install {pkg}"),
    ("arch",       "Arch Linux",        "sudo pacman -S {pkg}"),
    ("manjaro",    "Manjaro",           "sudo pacman -S {pkg}"),
    ("endeavouros","EndeavourOS",       "sudo pacman -S {pkg}"),
    ("garuda",     "Garuda Linux",      "sudo pacman -S {pkg}"),
    ("void",       "Void Linux",        "sudo xbps-install {pkg}"),
    ("alpine",     "Alpine Linux",      "sudo apk add {pkg}"),
    ("gentoo",     "Gentoo",            "sudo emerge {pkg}"),
    ("nixos",      "NixOS",             "nix-env -iA nixpkgs.{pkg}"),
    ("solus",      "Solus",             "sudo eopkg install {pkg}"),
    ("mageia",     "Mageia",            "sudo urpmi {pkg}"),
    ("slackware",  "Slackware",         "sudo slackpkg install {pkg}"),
]


def _read_os_release() -> dict:
    """Parse /etc/os-release into a dict."""
    info = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        info[k.strip()] = v.strip().strip('"')
            break
        except OSError:
            continue
    return info


def _detect_distro() -> tuple[str, str]:
    """
    Returns (friendly_name, install_template) for the running distribution.
    Falls back to probing the available package manager binary.
    """
    os_info = _read_os_release()
    distro_id   = os_info.get("ID", "").lower()
    id_like     = os_info.get("ID_LIKE", "").lower()
    pretty_name = os_info.get("PRETTY_NAME", os_info.get("NAME", "Linux"))

    search_ids = [distro_id] + id_like.split()

    for search_id in search_ids:
        for fragment, name, template in _DISTRO_TABLE:
            if fragment in search_id:
                return name, template

    # Fallback: probe which package manager is available
    for binary, template in [
        ("zypper",       "sudo zypper install {pkg}"),
        ("apt",          "sudo apt install {pkg}"),
        ("apt-get",      "sudo apt-get install {pkg}"),
        ("dnf",          "sudo dnf install {pkg}"),
        ("yum",          "sudo yum install {pkg}"),
        ("pacman",       "sudo pacman -S {pkg}"),
        ("xbps-install", "sudo xbps-install {pkg}"),
        ("apk",          "sudo apk add {pkg}"),
        ("emerge",       "sudo emerge {pkg}"),
        ("eopkg",        "sudo eopkg install {pkg}"),
    ]:
        if shutil.which(binary):
            return pretty_name, template

    return pretty_name, "# Install {pkg} with your system package manager"


def _is_present(binary: str) -> bool:
    return shutil.which(binary) is not None


def get_missing_deps() -> list[dict]:
    """Returns a list of dep dicts that are currently missing from PATH."""
    deps = list(HOST_DEPS)
    # Add xwinwrap check only when NOT running from an AppImage
    if not os.environ.get("APPDIR"):
        deps.append(_XWINWRAP_DEP)
    return [d for d in deps if not _is_present(d["binary"])]


# ---------------------------------------------------------------------------
# OnboardingDialog
# ---------------------------------------------------------------------------

class OnboardingDialog(QDialog):
    """
    Shown at startup when host dependencies (mpv, ffmpeg) are missing.
    Detects the user's Linux distribution and shows the exact install command
    for their package manager. Features copy-to-clipboard and re-check.
    """

    def __init__(self, missing_deps: list[dict], parent=None):
        super().__init__(parent)
        self.missing_deps = missing_deps
        self.distro_name, self.install_template = _detect_distro()
        self._dep_rows: dict[str, dict] = {}  # binary -> {row widgets}
        self.setWindowTitle("Setup Required — Missing Dependencies")
        self.setMinimumWidth(580)
        self.setWindowIcon(create_app_icon())
        self.setStyleSheet(self._qss())
        self._build_ui()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header banner ────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("ob-header")
        header.setFixedHeight(90)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(24, 0, 24, 0)
        h_layout.setSpacing(16)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(create_app_icon().pixmap(QSize(52, 52)))
        h_layout.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_lbl = QLabel("Wallpaper Motor")
        title_lbl.setObjectName("ob-title")
        sub_lbl = QLabel("A few host tools need to be installed before you can deploy wallpapers.")
        sub_lbl.setObjectName("ob-sub")
        sub_lbl.setWordWrap(True)
        text_col.addWidget(title_lbl)
        text_col.addWidget(sub_lbl)
        h_layout.addLayout(text_col)
        root.addWidget(header)

        # ── Distro badge ─────────────────────────────────────────────────────
        distro_row = QHBoxLayout()
        distro_row.setContentsMargins(24, 12, 24, 4)
        distro_lbl = QLabel(f"Detected distribution:")
        distro_lbl.setObjectName("ob-label")
        distro_val = QLabel(self.distro_name)
        distro_val.setObjectName("ob-distro-val")
        distro_row.addWidget(distro_lbl)
        distro_row.addWidget(distro_val)
        distro_row.addStretch()
        root.addLayout(distro_row)

        # ── Dependency cards ─────────────────────────────────────────────────
        cards_widget = QWidget()
        cards_widget.setObjectName("ob-cards")
        cards_layout = QVBoxLayout(cards_widget)
        cards_layout.setContentsMargins(20, 8, 20, 8)
        cards_layout.setSpacing(10)

        for dep in self.missing_deps:
            card, row_data = self._make_dep_card(dep)
            cards_layout.addWidget(card)
            self._dep_rows[dep["binary"]] = row_data

        root.addWidget(cards_widget)

        # ── Note about xwinwrap (AppImage only) ──────────────────────────────
        if not os.environ.get("APPDIR"):
            note = QLabel(
                "ℹ  <b>xwinwrap</b> must also be installed when running from source — "
                "it is bundled automatically inside the AppImage."
            )
            note.setObjectName("ob-note")
            note.setWordWrap(True)
            note.setTextFormat(Qt.TextFormat.RichText)
            note.setContentsMargins(24, 0, 24, 8)
            root.addWidget(note)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(20, 8, 20, 20)
        btn_row.setSpacing(10)

        self.btn_recheck = QPushButton("↻  Re-check Dependencies")
        self.btn_recheck.setObjectName("ob-btn-recheck")
        self.btn_recheck.clicked.connect(self._recheck)

        btn_continue = QPushButton("Continue Anyway")
        btn_continue.setObjectName("ob-btn-continue")
        btn_continue.clicked.connect(self.accept)

        btn_row.addWidget(self.btn_recheck)
        btn_row.addStretch()
        btn_row.addWidget(btn_continue)
        root.addLayout(btn_row)

    def _make_dep_card(self, dep: dict) -> tuple[QFrame, dict]:
        """Build a single dependency card widget. Returns (frame, row_data)."""
        card = QFrame()
        card.setObjectName("ob-card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # Title row: status dot + name + description
        title_row = QHBoxLayout()
        title_row.setSpacing(10)

        status_dot = QLabel("✗")
        status_dot.setObjectName("ob-dot-missing")
        status_dot.setFixedWidth(20)

        name_lbl = QLabel(dep["label"])
        name_lbl.setObjectName("ob-dep-name")

        desc_lbl = QLabel(dep["description"])
        desc_lbl.setObjectName("ob-dep-desc")
        desc_lbl.setWordWrap(True)

        title_row.addWidget(status_dot)
        title_row.addWidget(name_lbl)
        title_row.addWidget(desc_lbl, stretch=1)
        layout.addLayout(title_row)

        # Command row: monospace box + copy button
        cmd_text = self.install_template.format(pkg=dep["pkg"])
        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(8)

        cmd_box = QLineEdit(cmd_text)
        cmd_box.setObjectName("ob-cmd")
        cmd_box.setReadOnly(True)
        cmd_box.setFont(QFont("Monospace", 10))

        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("ob-copy-btn")
        copy_btn.setFixedWidth(64)
        copy_btn.clicked.connect(lambda _, t=cmd_text: self._copy(t, copy_btn))

        cmd_row.addWidget(cmd_box)
        cmd_row.addWidget(copy_btn)
        layout.addLayout(cmd_row)

        return card, {
            "card": card,
            "status_dot": status_dot,
            "binary": dep["binary"],
        }

    # --------------------------------------------------------------- slots --
    def _copy(self, text: str, btn: QPushButton):
        QApplication.clipboard().setText(text)
        original = btn.text()
        btn.setText("✓ Copied")
        btn.setObjectName("ob-copy-btn-done")
        btn.setStyleSheet("background-color: #1e4a2e; border-color: #2d7a47; color: #4ade80;")
        QTimer.singleShot(1800, lambda: self._reset_copy_btn(btn, original))

    @staticmethod
    def _reset_copy_btn(btn: QPushButton, original: str):
        btn.setText(original)
        btn.setStyleSheet("")

    def _recheck(self):
        """Re-run PATH checks and update the status dots live."""
        all_ok = True
        for binary, row in self._dep_rows.items():
            found = _is_present(binary)
            dot: QLabel = row["status_dot"]
            if found:
                dot.setText("✓")
                dot.setObjectName("ob-dot-ok")
                row["card"].setObjectName("ob-card-ok")
            else:
                dot.setText("✗")
                dot.setObjectName("ob-dot-missing")
                row["card"].setObjectName("ob-card")
                all_ok = False
            # Force style refresh
            dot.style().unpolish(dot)
            dot.style().polish(dot)
            row["card"].style().unpolish(row["card"])
            row["card"].style().polish(row["card"])

        if all_ok:
            self.btn_recheck.setText("✓ All dependencies found!")
            QTimer.singleShot(1200, self.accept)

    # ----------------------------------------------------------------- QSS --
    @staticmethod
    def _qss() -> str:
        return """
        QDialog {
            background-color: #121214;
        }
        QFrame#ob-header {
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:0,
                stop:0 #0e2a35, stop:1 #121214);
            border-bottom: 1px solid #1e3a4a;
        }
        QLabel#ob-title {
            font-size: 17px;
            font-weight: 800;
            color: #00b4d8;
        }
        QLabel#ob-sub {
            font-size: 11px;
            color: #8a8a98;
        }
        QLabel#ob-label {
            font-size: 12px;
            color: #7a7a85;
        }
        QLabel#ob-distro-val {
            font-size: 12px;
            font-weight: bold;
            color: #00b4d8;
            background: #0e2a35;
            border: 1px solid #1e3a4a;
            border-radius: 4px;
            padding: 2px 8px;
        }
        QFrame#ob-card {
            background-color: #1a1a1e;
            border: 1px solid #2d2d34;
            border-radius: 8px;
        }
        QFrame#ob-card-ok {
            background-color: #1a2a1e;
            border: 1px solid #2d7a47;
            border-radius: 8px;
        }
        QLabel#ob-dot-missing {
            font-size: 16px;
            font-weight: bold;
            color: #ef233c;
        }
        QLabel#ob-dot-ok {
            font-size: 16px;
            font-weight: bold;
            color: #4ade80;
        }
        QLabel#ob-dep-name {
            font-size: 14px;
            font-weight: 800;
            color: #00b4d8;
        }
        QLabel#ob-dep-desc {
            font-size: 11px;
            color: #8a8a98;
        }
        QLabel#ob-note {
            font-size: 11px;
            color: #7a7a85;
            padding: 0px 4px;
        }
        QLineEdit#ob-cmd {
            background-color: #0d0d10;
            border: 1px solid #2d2d34;
            border-radius: 5px;
            padding: 6px 10px;
            color: #a0e4f1;
            font-size: 12px;
        }
        QPushButton#ob-copy-btn {
            background-color: #25252b;
            border: 1px solid #3a3a42;
            border-radius: 5px;
            padding: 6px 8px;
            color: #e2e2e9;
            font-size: 11px;
            font-weight: bold;
        }
        QPushButton#ob-copy-btn:hover {
            background-color: #2d2d34;
        }
        QPushButton#ob-btn-recheck {
            background-color: #0e2a35;
            border: 1px solid #00b4d8;
            border-radius: 6px;
            padding: 9px 18px;
            color: #00b4d8;
            font-weight: bold;
            font-size: 12px;
        }
        QPushButton#ob-btn-recheck:hover {
            background-color: #1a3d4f;
        }
        QPushButton#ob-btn-continue {
            background-color: #25252b;
            border: 1px solid #3a3a42;
            border-radius: 6px;
            padding: 9px 18px;
            color: #8a8a98;
            font-weight: bold;
            font-size: 12px;
        }
        QPushButton#ob-btn-continue:hover {
            background-color: #2d2d34;
            color: #e2e2e9;
        }
        """


# ==============================================================================
# UI Component: Custom List Item Widget
# ==============================================================================

class StreamItemWidget(QWidget):
    """Custom item display layout for the stream entries in the list."""
    def __init__(self, stream, on_favorite_toggled, parent=None):
        super().__init__(parent)
        self.stream = stream
        self.on_favorite_toggled = on_favorite_toggled
        self.init_ui()
        
    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        
        self.lbl_name = ElidedLabel(self.stream.get("name", "Unnamed Stream"))
        self.lbl_name.setStyleSheet("font-weight: bold; font-size: 13px; color: #e2e2e9;")
        self.lbl_name.setMinimumWidth(10)
        self.lbl_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        
        self.lbl_category = QLabel(self.stream.get("category", "General").upper())
        self.lbl_category.setStyleSheet("""
            font-size: 9px;
            font-weight: 800;
            color: #8a8a98;
            background-color: #24242d;
            border: 1px solid #34343d;
            border-radius: 4px;
            padding: 1px 6px;
        """)
        self.lbl_category.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed
        )
        
        info_layout.addWidget(self.lbl_name)
        info_layout.addWidget(self.lbl_category)
        
        layout.addLayout(info_layout, 1)
        
        # Star toggle button
        self.btn_fav = QPushButton()
        self.btn_fav.setFixedSize(28, 28)
        self.btn_fav.setFlat(True)
        self.btn_fav.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.05);
                border-radius: 4px;
            }
        """)
        self.update_favorite_icon()
        self.btn_fav.clicked.connect(self.toggle_favorite)
        layout.addWidget(self.btn_fav)
        
    def update_favorite_icon(self):
        is_fav = self.stream.get("favorite", False)
        self.btn_fav.setIcon(create_star_icon(is_fav))
        
    def toggle_favorite(self):
        self.stream["favorite"] = not self.stream.get("favorite", False)
        self.update_favorite_icon()
        self.on_favorite_toggled(self.stream)


# ==============================================================================
# UI Component: Add / Edit Stream Dialog
# ==============================================================================

class MetadataFetcher(QThread):
    metadata_fetched = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        
    def run(self):
        try:
            import yt_dlp
            ydl_opts = {
                'simulate': True,
                'quiet': True,
                'skip_download': True,
                'extract_flat': False,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                res = {
                    "title": info.get("title", "Unnamed Stream"),
                    "is_live": bool(info.get("is_live")),
                }
                self.metadata_fetched.emit(res)
        except Exception as e:
            self.error_occurred.emit(str(e))

class StreamDialog(QDialog):
    """Configuration dialog for adding and editing live streams and local video files."""
    def __init__(self, categories=None, stream=None, parent=None):
        super().__init__(parent)
        self.stream = stream or {}
        self.categories = categories or ["General"]
        self.fetcher_thread = None
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("Edit Source" if self.stream else "Add Source")
        self.setMinimumWidth(500)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1e;
            }
            QLabel {
                font-weight: bold;
                color: #e2e2e9;
            }
            QLineEdit, QComboBox {
                background-color: #121214;
                border: 1px solid #2d2d34;
                border-radius: 6px;
                padding: 8px 12px;
                color: #e2e2e9;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #00b4d8;
            }
            QCheckBox {
                color: #e2e2e9;
                font-weight: bold;
            }
            QPushButton#btn-browse {
                background-color: #25252b;
                border: 1px solid #2d2d34;
                color: #e2e2e9;
                border-radius: 6px;
                padding: 8px 12px;
                font-weight: bold;
            }
            QPushButton#btn-browse:hover {
                background-color: #2d2d34;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        form_layout = QFormLayout()
        form_layout.setSpacing(12)
        
        # Source Type
        self.combo_type = QComboBox()
        self.combo_type.addItem("Live Stream / URL", "stream")
        self.combo_type.addItem("Local Video File", "local")
        is_local = self.stream.get("is_local", False)
        url_val = self.stream.get("url", "")
        if url_val and not (url_val.startswith("http://") or url_val.startswith("https://")):
            is_local = True
        self.combo_type.setCurrentIndex(1 if is_local else 0)
        self.combo_type.currentIndexChanged.connect(self.on_type_changed)
        
        # Name
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("e.g. Lofi Girl (Study Beats) / Cyberpunk Train")
        self.txt_name.setText(self.stream.get("name", ""))
        
        # Name row layout to include a detection status label
        name_container = QWidget()
        name_layout = QHBoxLayout(name_container)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(8)
        name_layout.addWidget(self.txt_name)
        
        self.lbl_detect_status = QLabel("")
        self.lbl_detect_status.setStyleSheet("color: #00b4d8; font-size: 11px; font-weight: normal;")
        name_layout.addWidget(self.lbl_detect_status)
        
        # Category (Editable combo box)
        self.combo_category = QComboBox()
        self.combo_category.setEditable(True)
        self.combo_category.addItems(self.categories)
        self.combo_category.setEditText(self.stream.get("category", "General"))
        
        # URL/Path Input Layout
        path_container = QWidget()
        path_layout = QHBoxLayout(path_container)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(8)
        
        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.txt_url.setText(self.stream.get("url", ""))
        self.txt_url.editingFinished.connect(self.on_url_editing_finished)
        path_layout.addWidget(self.txt_url)
        
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.setObjectName("btn-browse")
        self.btn_browse.clicked.connect(self.browse_local_file)
        path_layout.addWidget(self.btn_browse)
        
        # Set up form rows
        form_layout.addRow("Source Type:", self.combo_type)
        self.lbl_path_title = QLabel("YouTube/Stream URL:")
        form_layout.addRow(self.lbl_path_title, path_container)
        form_layout.addRow("Name:", name_container)
        form_layout.addRow("Category/Tag:", self.combo_category)
        
        layout.addLayout(form_layout)
        
        self.chk_fav = QCheckBox("Add to Favorites")
        self.chk_fav.setChecked(self.stream.get("favorite", False))
        layout.addWidget(self.chk_fav)
        
        # Action Buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Save")
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet("""
            QPushButton {
                background-color: #00b4d8;
                border: 1px solid #0096b4;
                color: #ffffff;
                font-weight: bold;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #00c4ec;
            }
        """)
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet("""
            QPushButton {
                background-color: #25252c;
                border: 1px solid #3a3a42;
                color: #e2e2e9;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #2d2d34;
            }
        """)
        
        self.button_box.accepted.connect(self.validate_and_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        
        # Update visibility states based on initial selection
        self.on_type_changed()
        
    def on_type_changed(self):
        source_type = self.combo_type.currentData()
        if source_type == "local":
            self.lbl_path_title.setText("Video File Path:")
            self.txt_url.setPlaceholderText("e.g. /home/user/Videos/wallpaper.mp4")
            self.btn_browse.setVisible(True)
        else:
            self.lbl_path_title.setText("YouTube/Stream URL:")
            self.txt_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
            self.btn_browse.setVisible(False)
            
    def browse_local_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video File",
            "",
            "Video Files (*.mp4 *.mkv *.webm *.avi *.mov *.m4v);;All Files (*)"
        )
        if file_path:
            self.txt_url.setText(file_path)
            current_name = self.txt_name.text().strip()
            if not current_name or current_name == "Unnamed Stream":
                self.txt_name.setText(Path(file_path).stem)
                
    def on_url_editing_finished(self):
        source_type = self.combo_type.currentData()
        if source_type != "stream":
            return
            
        url = self.txt_url.text().strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return
            
        if self.fetcher_thread and self.fetcher_thread.isRunning():
            self.fetcher_thread.terminate()
            self.fetcher_thread.wait()
            
        self.lbl_detect_status.setText("Detecting source...")
        self.lbl_detect_status.setStyleSheet("color: #00b4d8; font-size: 11px; font-weight: normal;")
        self.fetcher_thread = MetadataFetcher(url)
        self.fetcher_thread.metadata_fetched.connect(self.on_metadata_fetched)
        self.fetcher_thread.error_occurred.connect(self.on_metadata_error)
        self.fetcher_thread.finished.connect(self.on_fetch_finished)
        self.fetcher_thread.start()
        
    def on_metadata_fetched(self, metadata):
        current_name = self.txt_name.text().strip()
        if not current_name or current_name == "Unnamed Stream":
            self.txt_name.setText(metadata["title"])
        self.stream["is_live"] = metadata["is_live"]
        type_str = "Live Stream" if metadata["is_live"] else "VOD/Video"
        self.lbl_detect_status.setText(f"Detected: {type_str}")
        self.lbl_detect_status.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: bold;")
        
    def on_metadata_error(self, err):
        self.lbl_detect_status.setText("Detection failed")
        self.lbl_detect_status.setStyleSheet("color: #ef233c; font-size: 11px; font-weight: bold;")
        
    def on_fetch_finished(self):
        if "Detected" not in self.lbl_detect_status.text() and "failed" not in self.lbl_detect_status.text():
            self.lbl_detect_status.setText("")
            
    def validate_and_accept(self):
        name = self.txt_name.text().strip()
        category = self.combo_category.currentText().strip()
        url = self.txt_url.text().strip()
        
        if not name or not url:
            QMessageBox.warning(self, "Validation Error", "Name and URL/Path are required.")
            return
            
        self.stream["name"] = name
        self.stream["category"] = category if category else "General"
        self.stream["url"] = url
        self.stream["favorite"] = self.chk_fav.isChecked()
        self.stream["is_local"] = (self.combo_type.currentData() == "local")
        if "is_live" not in self.stream:
            self.stream["is_live"] = False
        self.accept()
        
    def closeEvent(self, event):
        if self.fetcher_thread and self.fetcher_thread.isRunning():
            self.fetcher_thread.terminate()
            self.fetcher_thread.wait()
        event.accept()


class DownloadThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str) # success, message or final path
    
    def __init__(self, url, duration, output_dir, is_live):
        super().__init__()
        self.url = url
        self.duration = duration
        self.output_dir = output_dir
        self.is_live = is_live
        
    def run(self):
        try:
            import hashlib
            url_hash = hashlib.md5(self.url.encode('utf-8')).hexdigest()
            os.makedirs(self.output_dir, exist_ok=True)
            final_path = os.path.join(self.output_dir, f"{url_hash}.mp4")
            
            if os.path.exists(final_path):
                try:
                    os.remove(final_path)
                except Exception:
                    pass
            
            self.progress.emit("Starting download...")
            
            if self.duration != 'whole':
                cmd = [
                    "yt-dlp",
                    "--downloader", "ffmpeg",
                    "--downloader-args", f"ffmpeg:-t {self.duration}",
                    "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "--merge-output-format", "mp4",
                    "-o", final_path,
                    self.url
                ]
            else:
                cmd = [
                    "yt-dlp",
                    "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "--merge-output-format", "mp4",
                    "-o", final_path,
                    self.url
                ]
                
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                if "[download]" in line and "%" in line:
                    parts = line.split()
                    for p in parts:
                        if "%" in p:
                            self.progress.emit(f"Downloading: {p}")
                            break
                elif "ffmpeg" in line.lower() or "downloading" in line.lower():
                    self.progress.emit(line)
                    
            process.wait()
            
            if process.returncode == 0 and os.path.exists(final_path):
                self.finished.emit(True, final_path)
            else:
                self.finished.emit(False, "yt-dlp exited with non-zero code or output file missing.")
        except Exception as e:
            self.finished.emit(False, str(e))


# ==============================================================================
# UI Component: Main Application Window
# ==============================================================================

class MainWindow(QMainWindow):
    """The central dark-themed desktop frontend manager for live stream wallpapers."""
    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.proc_manager = ProcessManager()
        self.active_wallpaper_name = None
        self.force_close_requested = False
        
        # Sleep/wake variables
        self.restart_wallpaper_on_wake = False
        self.last_wallpaper_url = None
        self.last_wallpaper_resolution = None
        
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("Wallpaper Motor")
        self.setMinimumSize(950, 650)
        self.setWindowIcon(create_app_icon())
        
        # Load stylesheet
        self.setStyleSheet(self.get_qss())
        
        # Main Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)
        
        # ----------------------------------------------------------------------
        # Sidebar Left Panel
        # ----------------------------------------------------------------------
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 16, 16, 16)
        sidebar_layout.setSpacing(12)
        
        # Header title
        lbl_title = QLabel("WALLPAPER MOTOR")
        lbl_title.setObjectName("lbl-title")
        lbl_subtitle = QLabel("Live Stream Wallpaper Manager")
        lbl_subtitle.setObjectName("lbl-subtitle")
        
        sidebar_layout.addWidget(lbl_title)
        sidebar_layout.addWidget(lbl_subtitle)
        sidebar_layout.addSpacing(5)
        
        # Search Box
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search by name or category...")
        self.txt_search.textChanged.connect(self.filter_streams)
        sidebar_layout.addWidget(self.txt_search)
        
        # Stream List Widget
        self.lst_streams = QListWidget()
        self.lst_streams.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lst_streams.itemSelectionChanged.connect(self.on_stream_selection_changed)
        sidebar_layout.addWidget(self.lst_streams)
        
        # CRUD Buttons Layout
        crud_layout = QHBoxLayout()
        crud_layout.setSpacing(8)
        
        self.btn_add = QPushButton("Add")
        self.btn_add.clicked.connect(self.add_stream)
        
        self.btn_edit = QPushButton("Edit")
        self.btn_edit.clicked.connect(self.edit_stream)
        self.btn_edit.setEnabled(False)
        
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self.delete_stream)
        self.btn_delete.setEnabled(False)
        
        crud_layout.addWidget(self.btn_add)
        crud_layout.addWidget(self.btn_edit)
        crud_layout.addWidget(self.btn_delete)
        sidebar_layout.addLayout(crud_layout)
        
        # ----------------------------------------------------------------------
        # Main Right Panel (QTabWidget)
        # ----------------------------------------------------------------------
        self.right_tabs = QTabWidget()
        self.right_tabs.setObjectName("right-tabs")
        
        # --- TAB 1: Wallpaper Dashboard ---
        dashboard_tab = QWidget()
        main_panel_layout = QVBoxLayout(dashboard_tab)
        main_panel_layout.setContentsMargins(20, 20, 20, 20)
        main_panel_layout.setSpacing(16)
        
        # Video Preview Section (StackedWidget)
        self.preview_stack = QStackedWidget()
        self.preview_stack.setStyleSheet("background-color: #000000; border-radius: 8px; border: 1px solid #28282f;")
        
        # Page 0: Placeholder
        placeholder = QWidget()
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_layout.setSpacing(10)
        
        lbl_placeholder_icon = QLabel("📺")
        lbl_placeholder_icon.setStyleSheet("font-size: 56px; color: #2e2e36;")
        lbl_placeholder_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        lbl_placeholder_text = QLabel("Select a stream and click 'Preview Stream'")
        lbl_placeholder_text.setStyleSheet("color: #7a7a85; font-size: 13px; font-weight: 500;")
        lbl_placeholder_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        placeholder_layout.addWidget(lbl_placeholder_icon)
        placeholder_layout.addWidget(lbl_placeholder_text)
        self.preview_stack.addWidget(placeholder)
        
        # Page 1: Video container
        self.preview_container = QWidget()
        self.preview_container.setStyleSheet("background-color: #000000;")
        self.preview_stack.addWidget(self.preview_container)
        
        main_panel_layout.addWidget(self.preview_stack, stretch=1)
        
        # Preview Controls Layout
        preview_ctrls = QHBoxLayout()
        preview_ctrls.setSpacing(10)
        
        self.btn_prev_play = QPushButton("Preview Stream")
        self.btn_prev_play.clicked.connect(self.play_selected_preview)
        self.btn_prev_play.setEnabled(False)
        
        self.btn_prev_stop = QPushButton("Stop Preview")
        self.btn_prev_stop.clicked.connect(self.stop_selected_preview)
        
        self.lbl_prev_status = QLabel("Preview Status: Idle")
        self.lbl_prev_status.setStyleSheet("color: #7a7a85; font-size: 12px;")
        
        preview_ctrls.addWidget(self.btn_prev_play)
        preview_ctrls.addWidget(self.btn_prev_stop)
        preview_ctrls.addWidget(self.lbl_prev_status)
        preview_ctrls.addStretch()
        
        main_panel_layout.addLayout(preview_ctrls)
        
        # ----------------------------------------------------------------------
        # Download Panel / Frame
        # ----------------------------------------------------------------------
        self.download_frame = QFrame()
        self.download_frame.setObjectName("download-frame")
        self.download_frame.setStyleSheet("""
            QFrame#download-frame {
                background-color: #1a1a1e;
                border: 1px solid #28282f;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        download_layout = QVBoxLayout(self.download_frame)
        download_layout.setSpacing(10)
        
        lbl_download_header = QLabel("Local Video Downloader (yt-dlp)")
        lbl_download_header.setStyleSheet("font-weight: bold; color: #e2e2e9; font-size: 13px;")
        download_layout.addWidget(lbl_download_header)
        
        # Duration selection row
        duration_row = QHBoxLayout()
        duration_row.setSpacing(10)
        
        lbl_dur = QLabel("Download Duration:")
        lbl_dur.setStyleSheet("font-weight: 500; color: #a0a0ab;")
        duration_row.addWidget(lbl_dur)
        
        self.combo_duration = QComboBox()
        self.combo_duration.addItem("30 Seconds", 30)
        self.combo_duration.addItem("60 Seconds", 60)
        self.combo_duration.addItem("120 Seconds", 120)
        self.combo_duration.addItem("Whole Video", "whole")
        duration_row.addWidget(self.combo_duration)
        
        self.btn_download = QPushButton("Download Local Loop")
        self.btn_download.clicked.connect(self.start_download)
        duration_row.addWidget(self.btn_download)
        
        duration_row.addStretch()
        download_layout.addLayout(duration_row)
        
        # Download status/progress label
        self.lbl_download_status = QLabel("No local download found.")
        self.lbl_download_status.setStyleSheet("color: #7a7a85; font-size: 12px;")
        download_layout.addWidget(self.lbl_download_status)
        
        # Playback Mode Toggle Row
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(15)
        
        lbl_mode = QLabel("Playback Mode:")
        lbl_mode.setStyleSheet("font-weight: 500; color: #a0a0ab;")
        toggle_row.addWidget(lbl_mode)
        
        self.radio_run_live = QRadioButton("Run Live Stream (URL)")
        self.radio_run_live.setStyleSheet("color: #e2e2e9; font-weight: bold;")
        self.radio_run_live.toggled.connect(self.on_playback_mode_toggled)
        toggle_row.addWidget(self.radio_run_live)
        
        self.radio_run_local = QRadioButton("Run Local Loop")
        self.radio_run_local.setStyleSheet("color: #e2e2e9; font-weight: bold;")
        self.radio_run_local.toggled.connect(self.on_playback_mode_toggled)
        toggle_row.addWidget(self.radio_run_local)
        
        toggle_row.addStretch()
        download_layout.addLayout(toggle_row)
        
        main_panel_layout.addWidget(self.download_frame)
        self.download_frame.setVisible(False)
        
        # Horizontal Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        divider.setStyleSheet("background-color: #28282f; max-height: 1px; border: none;")
        main_panel_layout.addWidget(divider)
        
        # Resolution Settings Layout
        settings_frame = QFrame()
        settings_frame.setStyleSheet("""
            QFrame {
                background-color: #1a1a1e;
                border: 1px solid #28282f;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        settings_layout = QVBoxLayout(settings_frame)
        settings_layout.setSpacing(10)
        
        lbl_settings_header = QLabel("Wallpaper Deployment Options")
        lbl_settings_header.setStyleSheet("font-weight: bold; color: #e2e2e9; font-size: 13px;")
        settings_layout.addWidget(lbl_settings_header)
        
        res_form = QHBoxLayout()
        res_form.setSpacing(10)
        
        lbl_res = QLabel("Target Resolution:")
        lbl_res.setStyleSheet("font-weight: 500; color: #a0a0ab;")
        res_form.addWidget(lbl_res)
        
        self.combo_resolution = QComboBox()
        res_form.addWidget(self.combo_resolution)
        
        self.txt_custom_res = QLineEdit()
        self.txt_custom_res.setPlaceholderText("e.g. 2560x1440+0+0")
        self.txt_custom_res.setVisible(False)
        res_form.addWidget(self.txt_custom_res)
        
        res_form.addStretch()
        settings_layout.addLayout(res_form)
        main_panel_layout.addWidget(settings_frame)
        
        # Wallpaper Action Controls (Apply / Stop)
        action_layout = QHBoxLayout()
        action_layout.setSpacing(12)
        
        self.btn_apply = QPushButton("Apply Wallpaper")
        self.btn_apply.setObjectName("btn-apply")
        self.btn_apply.setMinimumHeight(44)
        self.btn_apply.clicked.connect(self.apply_wallpaper)
        
        self.btn_stop = QPushButton("Stop Wallpaper")
        self.btn_stop.setObjectName("btn-stop")
        self.btn_stop.setMinimumHeight(44)
        self.btn_stop.clicked.connect(self.stop_wallpaper)
        
        self.lbl_status = QLabel("Status: Inactive")
        self.lbl_status.setStyleSheet("color: #a0a0ab; font-size: 13px;")
        
        action_layout.addWidget(self.btn_apply)
        action_layout.addWidget(self.btn_stop)
        action_layout.addWidget(self.lbl_status)
        action_layout.addStretch()
        
        main_panel_layout.addLayout(action_layout)
        
        self.right_tabs.addTab(dashboard_tab, "Wallpaper Dashboard")
        
        # --- TAB 2: Help & Compatibility ---
        help_tab = QWidget()
        help_layout = QVBoxLayout(help_tab)
        help_layout.setContentsMargins(24, 24, 24, 24)
        help_layout.setSpacing(16)
        help_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        lbl_help_title = QLabel("Help & Compatibility")
        lbl_help_title.setStyleSheet("font-size: 18px; font-weight: 800; color: #00b4d8;")
        help_layout.addWidget(lbl_help_title)
        
        # Compatibility banner
        banner = QFrame()
        banner.setStyleSheet("""
            QFrame {
                background-color: #1a1a1e;
                border: 1px solid #00b4d8;
                border-radius: 8px;
                padding: 16px;
            }
        """)
        banner_layout = QVBoxLayout(banner)
        banner_layout.setSpacing(8)
        
        lbl_banner_title = QLabel("ℹ  Protocol & URL Compatibility Information")
        lbl_banner_title.setStyleSheet("font-weight: bold; color: #00b4d8; font-size: 14px;")
        banner_layout.addWidget(lbl_banner_title)
        
        lbl_banner_desc = QLabel(
            "This application utilizes the powerful <b>yt-dlp</b> engine as its backend to stream "
            "and download video content. This guarantees native compatibility with a vast range "
            "of media protocols and hosting platforms."
        )
        lbl_banner_desc.setWordWrap(True)
        lbl_banner_desc.setTextFormat(Qt.TextFormat.RichText)
        banner_layout.addWidget(lbl_banner_desc)
        
        help_layout.addWidget(banner)
        
        # Details list
        lbl_details = QLabel(
            "<h3>Supported Input Protocols & Platforms:</h3>"
            "<ul>"
            "<li><b>YouTube:</b> Live streams, videos, playlists.</li>"
            "<li><b>Twitch:</b> Live channels and broadcast VODs.</li>"
            "<li><b>Vimeo:</b> Standard videos and live broadcasts.</li>"
            "<li><b>IPTV / Custom Streams:</b> Direct HLS (<code>.m3u8</code>), RTMP, RTSP, or custom feeds.</li>"
            "<li><b>Direct Web Links:</b> Direct URLs pointing to video files (<code>.mp4</code>, <code>.mkv</code>, <code>.webm</code>, etc.).</li>"
            "</ul>"
            "<p>Any streaming source compatible with the <b>yt-dlp</b> CLI is natively supported "
            "and can be added directly to your wallpaper library.</p>"
        )
        lbl_details.setWordWrap(True)
        lbl_details.setTextFormat(Qt.TextFormat.RichText)
        lbl_details.setStyleSheet("color: #e2e2e9; line-height: 1.4;")
        help_layout.addWidget(lbl_details)
        
        self.right_tabs.addTab(help_tab, "Help & Compatibility")
        
        # Add sidebars to splitter
        splitter.addWidget(sidebar)
        splitter.addWidget(self.right_tabs)
        splitter.setSizes([320, 630])
        
        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        
        # Populate GUI contents
        self.populate_stream_list()
        self.init_resolution_selector()
        self.init_tray_icon()
        
        # Active Monitoring Timer
        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(2000)
        self.monitor_timer.timeout.connect(self.monitor_background_processes)
        self.monitor_timer.start()
        
        # Refresh UI elements based on state
        self.update_ui_state()
        
        # Connect to system sleep/wake notifications via systemd DBus
        bus = QDBusConnection.systemBus()
        if bus.isConnected():
            bus.connect(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "PrepareForSleep",
                self.handle_prepare_for_sleep
            )
        
    # ----------------------------------------------------------------------
    # System Tray Integration
    # ----------------------------------------------------------------------
    def init_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(create_app_icon(), self)
        self.tray_menu = QMenu()
        
        self.action_open = QAction("Open", self)
        self.action_open.triggered.connect(self.show_and_raise)
        self.tray_menu.addAction(self.action_open)
        
        self.action_pause = QAction("Pause", self)
        self.action_pause.triggered.connect(self.toggle_pause_wallpaper)
        self.action_pause.setEnabled(False)
        self.tray_menu.addAction(self.action_pause)
        
        self.action_stop = QAction("Stop", self)
        self.action_stop.triggered.connect(self.stop_wallpaper)
        self.action_stop.setEnabled(False)
        self.tray_menu.addAction(self.action_stop)
        
        self.tray_menu.addSeparator()
        
        self.action_exit = QAction("Exit", self)
        self.action_exit.triggered.connect(self.force_exit)
        self.tray_menu.addAction(self.action_exit)
        
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        
    def on_tray_icon_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            if self.isVisible():
                self.hide()
            else:
                self.show_and_raise()
                
    def show_and_raise(self):
        self.show()
        self.activateWindow()
        self.raise_()
        
    def force_exit(self):
        """Kills background tasks and exits the app completely."""
        self.force_close_requested = True
        self.proc_manager.clean_up_all()
        QApplication.quit()
        
    def closeEvent(self, event):
        """Intercepts main window close events and redirects them to the system tray."""
        if self.force_close_requested:
            self.proc_manager.clean_up_all()
            event.accept()
        else:
            self.hide()
            # Stop preview playback to save network resources while window is hidden
            self.stop_selected_preview()
            if not hasattr(self, 'tray_notified'):
                self.tray_icon.showMessage(
                    "Wallpaper Motor",
                    "Application is minimized to the system tray. Select Exit from the tray menu to close completely.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000
                )
                self.tray_notified = True
            event.ignore()

    # ----------------------------------------------------------------------
    # Stream Item Management & CRUD
    # ----------------------------------------------------------------------
    def populate_stream_list(self):
        self.lst_streams.clear()
        
        # Sort favorites to top, then alphabetical by name
        sorted_streams = sorted(
            self.db.streams,
            key=lambda s: (not s.get("favorite", False), s.get("name", "").lower())
        )
        
        for stream in sorted_streams:
            item = QListWidgetItem(self.lst_streams)
            item.setData(Qt.ItemDataRole.UserRole, stream)
            
            widget = StreamItemWidget(stream, self.on_favorite_toggled, self.lst_streams)
            item.setSizeHint(widget.sizeHint())
            
            self.lst_streams.addItem(item)
            self.lst_streams.setItemWidget(item, widget)
            
        self.on_stream_selection_changed()
        
    def on_favorite_toggled(self, stream):
        self.db.save_streams()
        
        # Re-populate and maintain item selection
        selected_name = None
        current_item = self.lst_streams.currentItem()
        if current_item:
            selected_name = current_item.data(Qt.ItemDataRole.UserRole).get("name")
            
        self.populate_stream_list()
        
        if selected_name:
            for i in range(self.lst_streams.count()):
                item = self.lst_streams.item(i)
                s = item.data(Qt.ItemDataRole.UserRole)
                if s.get("name") == selected_name:
                    self.lst_streams.setCurrentItem(item)
                    break
                    
    def on_stream_selection_changed(self):
        current_item = self.lst_streams.currentItem()
        has_selection = current_item is not None
        
        self.btn_prev_play.setEnabled(has_selection)
        self.btn_edit.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)
        
        if not has_selection:
            self.download_frame.setVisible(False)
            return
            
        stream = current_item.data(Qt.ItemDataRole.UserRole)
        
        if stream.get("is_local", False):
            self.download_frame.setVisible(False)
        else:
            self.download_frame.setVisible(True)
            
            # Enable/disable 'Whole Video' if stream is live
            is_live = stream.get("is_live", False)
            item = self.combo_duration.model().item(3)
            if item:
                item.setEnabled(not is_live)
            if is_live and self.combo_duration.currentIndex() == 3:
                self.combo_duration.setCurrentIndex(0)
                
            # Check if local downloaded file exists
            import hashlib
            url_hash = hashlib.md5(stream["url"].encode('utf-8')).hexdigest()
            local_dir = os.path.join(Path.home(), ".local", "share", "wallpaper-motor", "videos")
            local_file = os.path.join(local_dir, f"{url_hash}.mp4")
            
            local_exists = os.path.exists(local_file)
            self.radio_run_local.setEnabled(local_exists)
            
            current_mode = stream.get("playback_mode", "live")
            if current_mode == "local" and not local_exists:
                current_mode = "live"
                stream["playback_mode"] = "live"
                self.db.save_streams()
                
            self.radio_run_live.blockSignals(True)
            self.radio_run_local.blockSignals(True)
            if current_mode == "local":
                self.radio_run_local.setChecked(True)
            else:
                self.radio_run_live.setChecked(True)
            self.radio_run_live.blockSignals(False)
            self.radio_run_local.blockSignals(False)
            
            if local_exists:
                size_mb = os.path.getsize(local_file) / (1024 * 1024)
                self.lbl_download_status.setText(f"Local copy exists ({size_mb:.1f} MB). Ready to loop.")
                self.lbl_download_status.setStyleSheet("color: #4ade80; font-size: 12px;")
            else:
                self.lbl_download_status.setText("No local copy downloaded yet.")
                self.lbl_download_status.setStyleSheet("color: #7a7a85; font-size: 12px;")
        
        # Seamlessly update preview on selection swap if it's already active
        if has_selection and self.preview_stack.currentIndex() == 1:
            self.play_selected_preview()
            
    def start_download(self):
        current_item = self.lst_streams.currentItem()
        if not current_item:
            return
        stream = current_item.data(Qt.ItemDataRole.UserRole)
        
        self.btn_download.setEnabled(False)
        self.combo_duration.setEnabled(False)
        self.lbl_download_status.setText("Preparing download...")
        self.lbl_download_status.setStyleSheet("color: #00b4d8; font-size: 12px;")
        
        url = stream["url"]
        duration = self.combo_duration.currentData()
        local_dir = os.path.join(Path.home(), ".local", "share", "wallpaper-motor", "videos")
        is_live = stream.get("is_live", False)
        
        self.dl_thread = DownloadThread(url, duration, local_dir, is_live)
        self.dl_thread.progress.connect(self.on_download_progress)
        self.dl_thread.finished.connect(self.on_download_finished)
        self.dl_thread.start()
        
    def on_download_progress(self, msg):
        self.lbl_download_status.setText(msg)
        
    def on_download_finished(self, success, result):
        self.btn_download.setEnabled(True)
        self.combo_duration.setEnabled(True)
        
        current_item = self.lst_streams.currentItem()
        if not current_item:
            return
        stream = current_item.data(Qt.ItemDataRole.UserRole)
        
        if success:
            size_mb = os.path.getsize(result) / (1024 * 1024)
            self.lbl_download_status.setText(f"Download complete! ({size_mb:.1f} MB). Ready to loop.")
            self.lbl_download_status.setStyleSheet("color: #4ade80; font-size: 12px;")
            self.radio_run_local.setEnabled(True)
            self.radio_run_local.setChecked(True)
        else:
            self.lbl_download_status.setText(f"Download failed: {result}")
            self.lbl_download_status.setStyleSheet("color: #ef233c; font-size: 12px;")
            
    def on_playback_mode_toggled(self):
        current_item = self.lst_streams.currentItem()
        if not current_item:
            return
        stream = current_item.data(Qt.ItemDataRole.UserRole)
        
        mode = "local" if self.radio_run_local.isChecked() else "live"
        stream["playback_mode"] = mode
        self.db.save_streams()
        
        # Stop preview or wallpaper if they are active to allow applying new mode
        self.stop_selected_preview()
            
    def filter_streams(self):
        query = self.txt_search.text().lower().strip()
        for i in range(self.lst_streams.count()):
            item = self.lst_streams.item(i)
            stream = item.data(Qt.ItemDataRole.UserRole)
            name = stream.get("name", "").lower()
            category = stream.get("category", "").lower()
            
            match = not query or query in name or query in category
            item.setHidden(not match)
            
    def get_existing_categories(self):
        categories = set()
        for stream in self.db.streams:
            cat = stream.get("category", "General").strip()
            if cat:
                categories.add(cat)
        if not categories:
            categories.add("General")
        return sorted(list(categories))

    def add_stream(self):
        categories = self.get_existing_categories()
        dialog = StreamDialog(categories=categories, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.db.streams.append(dialog.stream)
            self.db.save_streams()
            self.populate_stream_list()
            self.status_bar.showMessage(f"Added source: {dialog.stream.get('name')}", 3000)
            
    def edit_stream(self):
        current_item = self.lst_streams.currentItem()
        if not current_item:
            return
            
        stream = current_item.data(Qt.ItemDataRole.UserRole)
        categories = self.get_existing_categories()
        dialog = StreamDialog(categories=categories, stream=stream.copy(), parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for idx, s in enumerate(self.db.streams):
                if s.get("name") == stream.get("name") and s.get("url") == stream.get("url"):
                    self.db.streams[idx] = dialog.stream
                    break
            self.db.save_streams()
            self.populate_stream_list()
            self.status_bar.showMessage(f"Updated source: {dialog.stream.get('name')}", 3000)
            
    def delete_stream(self):
        current_item = self.lst_streams.currentItem()
        if not current_item:
            return
            
        stream = current_item.data(Qt.ItemDataRole.UserRole)
        confirm = QMessageBox.question(
            self,
            "Delete Stream",
            f"Are you sure you want to permanently delete '{stream.get('name')}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.db.streams = [
                s for s in self.db.streams
                if not (s.get("name") == stream.get("name") and s.get("url") == stream.get("url"))
            ]
            self.db.save_streams()
            self.populate_stream_list()
            self.status_bar.showMessage("Deleted stream entry.", 3000)

    # ----------------------------------------------------------------------
    # Screen Geometry & Resolutions
    # ----------------------------------------------------------------------
    def init_resolution_selector(self):
        self.combo_resolution.clear()
        
        screens = QApplication.screens()
        for s in screens:
            g = s.geometry()
            res_str = f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"
            self.combo_resolution.addItem(f"Monitor: {s.name()} ({res_str})", res_str)
            
        if len(screens) > 1:
            min_x = min(s.geometry().x() for s in screens)
            min_y = min(s.geometry().y() for s in screens)
            max_x = max(s.geometry().x() + s.geometry().width() for s in screens)
            max_y = max(s.geometry().y() + s.geometry().height() for s in screens)
            combined_str = f"{max_x - min_x}x{max_y - min_y}+{min_x}+{min_y}"
            self.combo_resolution.addItem(f"Combined Monitors ({combined_str})", combined_str)
            
        self.combo_resolution.addItem("Custom Resolution...", "custom")
        self.combo_resolution.currentIndexChanged.connect(self.on_resolution_selection_changed)
        
        if screens:
            self.combo_resolution.setCurrentIndex(0)
        self.on_resolution_selection_changed()
        
    def on_resolution_selection_changed(self):
        selected_data = self.combo_resolution.currentData()
        if selected_data == "custom":
            self.txt_custom_res.setVisible(True)
            self.txt_custom_res.setEnabled(True)
            if not self.txt_custom_res.text().strip():
                screens = QApplication.screens()
                if screens:
                    g = screens[0].geometry()
                    self.txt_custom_res.setText(f"{g.width()}x{g.height()}+0+0")
        else:
            self.txt_custom_res.setVisible(False)
            self.txt_custom_res.setEnabled(False)
            
    def get_selected_resolution(self):
        selected_data = self.combo_resolution.currentData()
        if selected_data == "custom":
            return self.txt_custom_res.text().strip()
        return selected_data

    # ----------------------------------------------------------------------
    # Control Actions: Preview & Wallpaper
    # ----------------------------------------------------------------------
    def play_selected_preview(self):
        current_item = self.lst_streams.currentItem()
        if not current_item:
            return
            
        stream = current_item.data(Qt.ItemDataRole.UserRole)
        url = stream.get("url")
        if not url:
            return
            
        if not stream.get("is_local", False) and stream.get("playback_mode", "live") == "local":
            import hashlib
            url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
            local_dir = os.path.join(Path.home(), ".local", "share", "wallpaper-motor", "videos")
            local_file = os.path.join(local_dir, f"{url_hash}.mp4")
            if os.path.exists(local_file):
                url = local_file
                
        self.preview_stack.setCurrentIndex(1)
        self.lbl_prev_status.setText("Preview Status: Buffering/Playing...")
        
        wid = int(self.preview_container.winId())
        self.proc_manager.start_preview(url, wid)
        
    def stop_selected_preview(self):
        self.proc_manager.stop_preview()
        self.preview_stack.setCurrentIndex(0)
        self.lbl_prev_status.setText("Preview Status: Idle")
        
    def apply_wallpaper(self):
        current_item = self.lst_streams.currentItem()
        if not current_item:
            QMessageBox.warning(self, "No Stream Selected", "Please select a stream to apply as background.")
            return
            
        stream = current_item.data(Qt.ItemDataRole.UserRole)
        url = stream.get("url")
        if not url:
            return
            
        if not stream.get("is_local", False) and stream.get("playback_mode", "live") == "local":
            import hashlib
            url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
            local_dir = os.path.join(Path.home(), ".local", "share", "wallpaper-motor", "videos")
            local_file = os.path.join(local_dir, f"{url_hash}.mp4")
            if os.path.exists(local_file):
                url = local_file
                
        resolution = self.get_selected_resolution()
        if not resolution:
            QMessageBox.warning(self, "Invalid Geometry", "Please specify a valid screen geometry.")
            return
            
        # Stop preview player before applying background to preserve hardware resources
        self.stop_selected_preview()
        
        self.active_wallpaper_name = stream.get("name", "Live Wallpaper")
        self.proc_manager.start_wallpaper(url, resolution)
        self.update_ui_state()
        self.status_bar.showMessage("Wallpaper deployed successfully.", 4000)
        
    def stop_wallpaper(self):
        self.proc_manager.stop_wallpaper()
        self.active_wallpaper_name = None
        self.update_ui_state()
        self.status_bar.showMessage("Wallpaper playback stopped.", 4000)
        
    def toggle_pause_wallpaper(self):
        if not self.proc_manager.wallpaper_process:
            return
            
        if self.proc_manager.is_paused:
            self.proc_manager.resume_wallpaper()
            self.status_bar.showMessage("Wallpaper playback resumed.", 3000)
        else:
            self.proc_manager.pause_wallpaper()
            self.status_bar.showMessage("Wallpaper playback paused (CPU usage reduced).", 3000)
        self.update_ui_state()
        
    def monitor_background_processes(self):
        """Active poll loop checking if the background wallpaper process has crashed/exited."""
        if self.proc_manager.wallpaper_process:
            if self.proc_manager.wallpaper_process.poll() is not None:
                self.proc_manager.wallpaper_process = None
                self.active_wallpaper_name = None
                self.update_ui_state()
                self.status_bar.showMessage("Wallpaper background engine stopped unexpectedly.", 5000)

    def update_ui_state(self):
        is_running = self.proc_manager.wallpaper_process is not None
        is_paused = self.proc_manager.is_paused
        
        self.action_stop.setEnabled(is_running)
        self.action_pause.setEnabled(is_running)
        
        if is_paused:
            self.action_pause.setText("Resume")
        else:
            self.action_pause.setText("Pause")
            
        self.btn_apply.setEnabled(not is_running)
        self.btn_stop.setEnabled(is_running)
        
        if is_running:
            state = "Paused" if is_paused else "Running"
            self.lbl_status.setText(f"Status: {state} - {self.active_wallpaper_name}")
            self.lbl_status.setStyleSheet("color: #00b4d8; font-weight: bold; font-size: 13px;")
            self.tray_icon.setToolTip(f"Wallpaper Motor - {state}")
        else:
            self.lbl_status.setText("Status: Inactive")
            self.lbl_status.setStyleSheet("color: #7a7a85; font-size: 13px;")
            self.tray_icon.setToolTip("Wallpaper Motor - Idle")

    @pyqtSlot(bool)
    def handle_prepare_for_sleep(self, entering):
        """Cleanly stops wallpaper before suspend and schedules restore upon wake."""
        if entering:
            # System is going to sleep
            is_running = self.proc_manager.wallpaper_process is not None
            if is_running:
                current_item = self.lst_streams.currentItem()
                if current_item:
                    stream = current_item.data(Qt.ItemDataRole.UserRole)
                    self.last_wallpaper_url = stream.get("url")
                    self.last_wallpaper_resolution = self.get_selected_resolution()
                    self.restart_wallpaper_on_wake = True
                
                # Turn off player processes to prevent buffer hang issues and save resources
                self.stop_wallpaper()
                self.stop_selected_preview()
        else:
            # System is waking up from sleep
            if self.restart_wallpaper_on_wake and self.last_wallpaper_url:
                self.lbl_status.setText("Status: Reconnecting stream after system wake...")
                self.lbl_status.setStyleSheet("color: #ffb703; font-weight: bold; font-size: 13px;")
                
                # Give network cards 10 seconds to fully reconnect to the network
                QTimer.singleShot(10000, self.auto_resume_after_wake)

    def auto_resume_after_wake(self):
        """Restores the wallpaper once the network has had time to reconnect."""
        if self.restart_wallpaper_on_wake and self.last_wallpaper_url:
            self.status_bar.showMessage("Auto-resuming wallpaper after system wake...", 4000)
            
            # Identify stream name from the saved URL
            self.active_wallpaper_name = "Restored Stream"
            for stream in self.db.streams:
                if stream.get("url") == self.last_wallpaper_url:
                    self.active_wallpaper_name = stream.get("name")
                    break
            
            self.proc_manager.start_wallpaper(self.last_wallpaper_url, self.last_wallpaper_resolution)
            self.update_ui_state()
            
            # Reset sleep-handling state
            self.restart_wallpaper_on_wake = False
            self.last_wallpaper_url = None
            self.last_wallpaper_resolution = None

    # ==============================================================================
    # Stylesheet (Modern Sterile Dark Theme)
    # ==============================================================================
    def get_qss(self):
        return """
        QMainWindow {
            background-color: #121214;
        }

        QFrame#sidebar {
            background-color: #1a1a1e;
            border-right: 1px solid #28282f;
        }

        QFrame#main-panel {
            background-color: #121214;
        }

        QLabel {
            color: #e2e2e9;
            font-size: 13px;
        }

        QLabel#lbl-title {
            font-size: 18px;
            font-weight: 800;
            color: #00b4d8;
            letter-spacing: 1px;
        }

        QLabel#lbl-subtitle {
            font-size: 11px;
            font-weight: 600;
            color: #5a5a65;
        }

        QLineEdit {
            background-color: #25252b;
            border: 1px solid #2d2d34;
            border-radius: 6px;
            padding: 8px 12px;
            color: #e2e2e9;
            font-size: 13px;
        }

        QLineEdit:focus {
            border: 1px solid #00b4d8;
            background-color: #2a2a32;
        }

        QListWidget {
            background-color: #121214;
            border: 1px solid #28282f;
            border-radius: 8px;
            padding: 4px;
        }

        QListWidget::item {
            background-color: #1a1a1e;
            border: 1px solid #28282f;
            border-radius: 6px;
            margin-bottom: 5px;
        }

        QListWidget::item:hover {
            background-color: #23232a;
            border-color: #34343d;
        }

        QListWidget::item:selected {
            background-color: #282830;
            border-color: #00b4d8;
        }

        QPushButton {
            background-color: #25252b;
            border: 1px solid #2d2d34;
            border-radius: 6px;
            padding: 8px 16px;
            color: #e2e2e9;
            font-weight: bold;
            font-size: 12px;
        }

        QPushButton:hover {
            background-color: #2d2d34;
            border-color: #3e3e48;
        }

        QPushButton:pressed {
            background-color: #1d1d22;
        }

        QPushButton:disabled {
            background-color: #16161a;
            color: #5a5a62;
            border-color: #202024;
        }

        QPushButton#btn-apply {
            background-color: #00b4d8;
            border: 1px solid #0096b4;
            color: #ffffff;
            font-size: 13px;
        }

        QPushButton#btn-apply:hover {
            background-color: #00c4ec;
        }

        QPushButton#btn-apply:pressed {
            background-color: #008eb0;
        }

        QPushButton#btn-stop {
            background-color: #d90429;
            border: 1px solid #b3001e;
            color: #ffffff;
            font-size: 13px;
        }

        QPushButton#btn-stop:hover {
            background-color: #ef233c;
        }

        QPushButton#btn-stop:pressed {
            background-color: #b3001e;
        }

        QComboBox {
            background-color: #25252b;
            border: 1px solid #2d2d34;
            border-radius: 6px;
            padding: 8px 12px;
            color: #e2e2e9;
            min-width: 220px;
        }

        QComboBox:focus {
            border-color: #00b4d8;
        }

        QComboBox::drop-down {
            border: none;
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 25px;
        }

        QComboBox::down-arrow {
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 5px solid #e2e2e9;
            margin-right: 10px;
        }

        QSplitter::handle {
            background-color: #28282f;
        }

        QStatusBar {
            background-color: #121214;
            border-top: 1px solid #28282f;
            color: #7a7a85;
            font-size: 11px;
        }

        QTabWidget::pane {
            border: 1px solid #28282f;
            background-color: #121214;
            border-top: none;
        }

        QTabBar::tab {
            background-color: #1a1a1e;
            color: #8a8a98;
            border: 1px solid #28282f;
            border-bottom: none;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 8px 16px;
            font-weight: bold;
            font-size: 12px;
        }

        QTabBar::tab:selected {
            background-color: #121214;
            color: #00b4d8;
            border-bottom: 2px solid #00b4d8;
        }

        QTabBar::tab:hover {
            background-color: #23232a;
            color: #e2e2e9;
        }
        """

# ==============================================================================
# Application Cleanup on Exit
# ==============================================================================

def register_exit_handler(proc_manager):
    def exit_cleanup():
        print("Cleaning up background players on application exit.")
        proc_manager.clean_up_all()
    atexit.register(exit_cleanup)


# ==============================================================================
# Main Application Entrance
# ==============================================================================

if __name__ == "__main__":
    # Handle X11 integration and PyQt initialization
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Wayland Safety Detector
    session_type = os.environ.get("XDG_SESSION_TYPE", "")
    if session_type.lower() == "wayland":
        dlg = WaylandErrorDialog()
        dlg.exec()
        sys.exit(0)

    # Initialize Main GUI
    main_win = MainWindow()

    # Register global exit hook to terminate background processes
    register_exit_handler(main_win.proc_manager)

    # --- Runtime dependency check ---
    # Run AFTER MainWindow is created so tray icon is visible and the dialog
    # has a proper parent to center on.
    missing = get_missing_deps()
    if missing:
        dlg = OnboardingDialog(missing, parent=main_win)
        dlg.exec()

        # Re-check after dialog closes (user may have installed in terminal)
        still_missing = get_missing_deps()
        if still_missing:
            names = ', '.join(d['binary'] for d in still_missing)
            main_win.btn_apply.setEnabled(False)
            main_win.btn_apply.setToolTip(
                f"Disabled — missing: {names}\n"
                "Install them and restart Wallpaper Motor."
            )
            main_win.status_bar.showMessage(
                f"⚠  Missing: {names} — restart after installing.", 0
            )

    # Show window
    main_win.show()

    sys.exit(app.exec())
