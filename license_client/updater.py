#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto-Updater — يتحقق من نسخة جديدة عند التشغيل
"""
import sys, os, logging, subprocess, tempfile, json
from packaging import version as pkg_version

import requests
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                              QProgressBar, QApplication, QFrame)
from PyQt5.QtCore    import Qt, QThread, pyqtSignal
from PyQt5.QtGui     import QFont

from license_client.license_config import SERVER_URL, REQUEST_TIMEOUT

log = logging.getLogger("updater")

CURRENT_VERSION = "1.6.3"


# ── Desktop path helper (يدعم OneDrive Desktop) ───────────────────────────────
def _get_desktop_path(filename: str = "") -> str:
    """يجد مسار سطح المكتب الحقيقي حتى لو كان OneDrive Desktop."""
    try:
        import ctypes, ctypes.wintypes
        CSIDL_DESKTOPDIRECTORY = 0x0010
        buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buf)
        desktop = buf.value
    except Exception:
        # fallback: جرب OneDrive أولاً ثم المسار العادي
        onedrive = os.environ.get("OneDrive", "")
        if onedrive and os.path.isdir(os.path.join(onedrive, "Desktop")):
            desktop = os.path.join(onedrive, "Desktop")
        else:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return os.path.join(desktop, filename) if filename else desktop

# ── ملف حفظ النسخة المتجاهلة ──────────────────────────────────────────────
def _skip_file() -> str:
    base = os.environ.get("APPDATA", tempfile.gettempdir())
    return os.path.join(base, "SmartTrader", "skip_version.json")

def _get_skipped() -> str:
    try:
        with open(_skip_file(), "r") as f:
            return json.load(f).get("skip", "")
    except Exception:
        return ""

def _set_skipped(ver: str):
    try:
        os.makedirs(os.path.dirname(_skip_file()), exist_ok=True)
        with open(_skip_file(), "w") as f:
            json.dump({"skip": ver}, f)
    except Exception:
        pass

# ── حماية من فتح dialog أكثر من مرة ─────────────────────────────────────────
_dialog_open = False

DARK_BG  = "#0b1f3a"
CARD_BG  = "#0d1e35"
BORDER   = "#1a3a5c"
ACCENT   = "#00aeef"
TEXT     = "#e0e6f0"
SUBTLE   = "#8a9bb0"
SUCCESS  = "#00c853"

STYLE = f"""
QDialog {{ background: {DARK_BG}; color: {TEXT}; font-family: 'Segoe UI'; }}
QLabel  {{ color: {TEXT}; font-family: 'Segoe UI'; }}
QFrame#card {{ background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; }}
QPushButton#btnUpdate {{
    background: {ACCENT}; color: white; font-size: 14px; font-weight: bold;
    padding: 10px; border-radius: 6px; border: none;
}}
QPushButton#btnUpdate:hover {{ background: #0097d6; }}
QPushButton#btnSkip {{
    background: transparent; color: {SUBTLE}; border: none; font-size: 12px;
}}
QPushButton#btnSkip:hover {{ color: #ff1744; }}
QPushButton#btnIgnore {{
    background: transparent; color: #ff5252; border: none; font-size: 11px;
}}
QProgressBar {{
    background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 4px;
    text-align: center; color: {TEXT};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
"""

# ── Download Worker ────────────────────────────────────────────────────────────
class DownloadWorker(QThread):
    progress = pyqtSignal(int)
    done     = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            headers = {"User-Agent": "SmartTrader-Updater/1.0"}
            r = requests.get(self.url, stream=True, timeout=120, headers=headers)
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            # احفظ على سطح المكتب — يدعم OneDrive Desktop
            desktop = _get_desktop_path("SmartTrader.exe")
            with open(desktop, "wb") as f:
                downloaded = 0
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(int(downloaded * 100 / total))
            self.done.emit(desktop)
        except Exception as e:
            self.error.emit(str(e))

# ── Update Dialog ──────────────────────────────────────────────────────────────
class UpdateDialog(QDialog):
    def __init__(self, new_version: str, download_url: str,
                 release_notes: str = "", parent=None):
        super().__init__(parent)
        self.download_url = download_url
        self.new_version  = new_version
        self.setWindowTitle("تحديث متاح")
        self.setFixedSize(480, 360)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        self._worker = None
        self._build_ui(new_version, release_notes)
        self._center()

    def _center(self):
        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width()-self.width())//2, (screen.height()-self.height())//2)

    def _build_ui(self, ver, notes):
        card = QFrame(self); card.setObjectName("card")
        lay  = QVBoxLayout(card); lay.setContentsMargins(32, 28, 32, 24); lay.setSpacing(12)

        ttl = QLabel("تحديث جديد متاح!")
        ttl.setFont(QFont("Segoe UI", 16, QFont.Bold))
        ttl.setStyleSheet(f"color: {ACCENT};"); ttl.setAlignment(Qt.AlignCenter)
        lay.addWidget(ttl)

        sub = QLabel(f"النسخة الحالية: {CURRENT_VERSION}    النسخة الجديدة: {ver}")
        sub.setFont(QFont("Segoe UI", 11)); sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)

        info = QLabel("سيتم حفظ الملف على سطح المكتب باسم SmartTrader.exe\nشغّله بعد اكتمال التحميل.")
        info.setFont(QFont("Segoe UI", 9))
        info.setStyleSheet(f"color: {SUBTLE};"); info.setWordWrap(True)
        info.setAlignment(Qt.AlignCenter); lay.addWidget(info)

        if notes:
            n = QLabel(notes); n.setFont(QFont("Segoe UI", 10))
            n.setStyleSheet(f"color: {SUBTLE};"); n.setWordWrap(True)
            n.setAlignment(Qt.AlignCenter); lay.addWidget(n)

        self.lbl_status = QLabel("")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setFont(QFont("Segoe UI", 10))
        lay.addWidget(self.lbl_status)

        self.progress = QProgressBar(); self.progress.setVisible(False)
        self.progress.setMinimumHeight(18); lay.addWidget(self.progress)

        self.btn_update = QPushButton("تحديث الآن")
        self.btn_update.setObjectName("btnUpdate")
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.clicked.connect(self._start_download)
        lay.addWidget(self.btn_update)

        self.btn_skip = QPushButton("تذكيري لاحقاً")
        self.btn_skip.setObjectName("btnSkip")
        self.btn_skip.clicked.connect(self.reject)
        lay.addWidget(self.btn_skip, alignment=Qt.AlignCenter)

        self.btn_ignore = QPushButton("تجاهل هذا الإصدار")
        self.btn_ignore.setObjectName("btnIgnore")
        self.btn_ignore.clicked.connect(self._ignore_version)
        lay.addWidget(self.btn_ignore, alignment=Qt.AlignCenter)

        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.addWidget(card)

    def _start_download(self):
        self.btn_update.setEnabled(False)
        self.btn_update.setText("جاري التحميل...")
        self.btn_skip.setEnabled(False)
        self.btn_ignore.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._worker = DownloadWorker(self.download_url)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, save_path: str):
        self.progress.setValue(100)
        self.lbl_status.setText(f"[OK] تم الحفظ: {os.path.basename(save_path)}\nاغلق البرنامج وشغّل الملف الجديد.")
        self.lbl_status.setStyleSheet(f"color: {SUCCESS};")
        self.btn_update.setText("تم التحميل")
        # افتح مجلد سطح المكتب تلقائياً
        try:
            subprocess.Popen(["explorer", "/select,", save_path])
        except Exception:
            pass

    def _on_error(self, msg: str):
        self.btn_update.setEnabled(True)
        self.btn_update.setText("تحديث الآن")
        self.btn_skip.setEnabled(True)
        self.btn_ignore.setEnabled(True)
        self.lbl_status.setText(f"[ERR] فشل التحميل: {msg}")
        self.lbl_status.setStyleSheet("color: #ff1744;")

    def _ignore_version(self):
        _set_skipped(self.new_version)
        self.reject()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPos() - self._drag_pos)

    def closeEvent(self, event):
        global _dialog_open
        _dialog_open = False
        super().closeEvent(event)

    def reject(self):
        global _dialog_open
        _dialog_open = False
        super().reject()


# ── Public API ─────────────────────────────────────────────────────────────────
def check_for_update(parent=None) -> bool:
    global _dialog_open
    if _dialog_open:
        return False
    try:
        r = requests.get(f"{SERVER_URL}/version", timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return False
        data       = r.json()
        server_ver = data.get("version", "")
        dl_url     = data.get("download_url", "")
        notes      = data.get("release_notes", "")

        if not dl_url or not server_ver:
            return False

        try:
            is_newer = pkg_version.parse(server_ver) > pkg_version.parse(CURRENT_VERSION)
        except Exception:
            is_newer = server_ver != CURRENT_VERSION

        if not is_newer:
            return False

        # تجاهل إذا اختار المستخدم تجاهل هذا الإصدار
        if _get_skipped() == server_ver:
            return False

        _dialog_open = True
        dlg = UpdateDialog(server_ver, dl_url, notes, parent)
        result = dlg.exec_() == QDialog.Accepted
        _dialog_open = False
        return result

    except Exception as e:
        log.debug(f"Update check failed: {e}")
        _dialog_open = False
        return False


