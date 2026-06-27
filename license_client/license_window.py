#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LicenseWindow — نافذة تفعيل الترخيص (PyQt5)
تُعرض قبل تحميل TradingApp إذا لم يكن الترخيص صالحاً.
"""
import sys, os, logging
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QApplication, QMessageBox
)
from PyQt5.QtCore    import Qt, QThread, pyqtSignal
from PyQt5.QtGui     import QFont, QColor, QPalette, QIcon, QPixmap

from license_client.license_manager import activate, check_license, get_local_info
from license_client.hardware_id     import get_hardware_id

log = logging.getLogger("license_window")

# ── Worker thread للتفعيل ─────────────────────────────────────────────────────
class ActivateWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, key: str, email: str):
        super().__init__()
        self.key   = key
        self.email = email

    def run(self):
        ok, msg = activate(self.key, self.email)
        self.done.emit(ok, msg)

# ── Worker thread للفحص ───────────────────────────────────────────────────────
class VerifyWorker(QThread):
    done = pyqtSignal(bool, str)

    def run(self):
        ok, msg = check_license()
        self.done.emit(ok, msg)

# ── اللون الرئيسي ─────────────────────────────────────────────────────────────
DARK_BG   = "#0b1f3a"
CARD_BG   = "#0d1e35"
BORDER    = "#1a3a5c"
ACCENT    = "#00aeef"
TEXT      = "#e0e6f0"
SUBTLE    = "#8a9bb0"
SUCCESS   = "#00c853"
DANGER    = "#ff1744"

STYLE = f"""
QDialog {{
    background: {DARK_BG};
    color: {TEXT};
    font-family: 'Segoe UI';
}}
QLabel {{
    color: {TEXT};
    font-family: 'Segoe UI';
}}
QLineEdit {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    color: {TEXT};
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 14px;
    font-family: 'Segoe UI';
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QPushButton#btnActivate {{
    background: {ACCENT};
    color: white;
    font-size: 15px;
    font-weight: bold;
    padding: 12px;
    border-radius: 6px;
    border: none;
    font-family: 'Segoe UI';
}}
QPushButton#btnActivate:hover {{ background: #0097d6; }}
QPushButton#btnActivate:disabled {{ background: #1a3a5c; color: {SUBTLE}; }}
QPushButton#btnExit {{
    background: transparent;
    color: {SUBTLE};
    border: none;
    font-size: 12px;
    font-family: 'Segoe UI';
}}
QPushButton#btnExit:hover {{ color: {DANGER}; }}
QFrame#card {{
    background: {CARD_BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
"""

class LicenseWindow(QDialog):
    """
    نافذة تفعيل الاشتراك.
    .exec_() → يُعيد QDialog.Accepted إذا تم التفعيل بنجاح.
    """

    def __init__(self, parent=None, prefill_key: str = ""):
        super().__init__(parent)
        self.setWindowTitle("SmartTrader — تفعيل البرنامج")
        self.setFixedSize(520, 560)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setStyleSheet(STYLE)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._worker = None
        self._build_ui(prefill_key)
        self._center()

    def _center(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width()  - self.width())  // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

    def _build_ui(self, prefill_key: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # الإطار الرئيسي
        card = QFrame(self)
        card.setObjectName("card")
        card.setContentsMargins(0, 0, 0, 0)
        root.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(40, 36, 40, 30)
        layout.setSpacing(18)

        # شعار + عنوان
        title = QLabel("⚡ SmartTrader")
        title.setFont(QFont("Segoe UI", 22, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("أدخل كود التفعيل للاستمرار")
        sub.setFont(QFont("Segoe UI", 12))
        sub.setStyleSheet(f"color: {SUBTLE};")
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub)

        # خط فاصل
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        layout.addWidget(sep)

        # حقل الكود
        lbl_key = QLabel("كود التفعيل")
        lbl_key.setFont(QFont("Segoe UI", 11, QFont.Bold))
        layout.addWidget(lbl_key)

        self.inp_key = QLineEdit(prefill_key)
        self.inp_key.setPlaceholderText("ST-XXXX-XXXX-XXXX")
        self.inp_key.setAlignment(Qt.AlignCenter)
        self.inp_key.setFont(QFont("Consolas", 15, QFont.Bold))
        self.inp_key.textChanged.connect(self._format_key)
        layout.addWidget(self.inp_key)

        # حقل البريد
        lbl_email = QLabel("البريد الإلكتروني")
        lbl_email.setFont(QFont("Segoe UI", 11, QFont.Bold))
        layout.addWidget(lbl_email)

        self.inp_email = QLineEdit()
        self.inp_email.setPlaceholderText("your@email.com")
        layout.addWidget(self.inp_email)

        # Hardware ID (للعرض)
        hw = get_hardware_id()
        hw_lbl = QLabel(f"معرّف الجهاز: {hw[:16]}...")
        hw_lbl.setFont(QFont("Segoe UI", 9))
        hw_lbl.setStyleSheet(f"color: {SUBTLE};")
        hw_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(hw_lbl)

        # رسالة الحالة
        self.lbl_status = QLabel("")
        self.lbl_status.setFont(QFont("Segoe UI", 11))
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setMinimumHeight(36)
        layout.addWidget(self.lbl_status)

        # زر التفعيل
        self.btn_activate = QPushButton("تفعيل البرنامج")
        self.btn_activate.setObjectName("btnActivate")
        self.btn_activate.setCursor(Qt.PointingHandCursor)
        self.btn_activate.clicked.connect(self._do_activate)
        layout.addWidget(self.btn_activate)

        # زر الخروج
        self.btn_exit = QPushButton("إغلاق")
        self.btn_exit.setObjectName("btnExit")
        self.btn_exit.clicked.connect(self.reject)
        layout.addWidget(self.btn_exit, alignment=Qt.AlignCenter)

        # نقل النافذة بالسحب
        self._drag_pos = None

    # ── تنسيق الكود تلقائياً ─────────────────────────────────────────────────
    def _format_key(self, text: str):
        clean = text.upper().replace("-", "").replace(" ", "")
        # احذف بادئة ST إن وُجدت حتى لا تتكرر
        if clean.startswith("ST"):
            clean = clean[2:]
        parts = [clean[i:i+4] for i in range(0, min(len(clean), 12), 4)]
        formatted = ("ST-" + "-".join(parts)) if parts else ""
        self.inp_key.blockSignals(True)
        self.inp_key.setText(formatted)
        self.inp_key.setCursorPosition(len(formatted))
        self.inp_key.blockSignals(False)

    # ── منطق التفعيل ──────────────────────────────────────────────────────────
    def _do_activate(self):
        key   = self.inp_key.text().strip().upper()
        email = self.inp_email.text().strip()

        if not key or len(key) < 14:
            self._set_status("أدخل كود التفعيل الكامل", DANGER)
            return
        if not email or "@" not in email:
            self._set_status("أدخل بريد إلكتروني صحيح", DANGER)
            return

        self.btn_activate.setEnabled(False)
        self.btn_activate.setText("جاري التفعيل...")
        self._set_status("جاري الاتصال بالخادم...", SUBTLE)

        self._worker = ActivateWorker(key, email)
        self._worker.done.connect(self._on_activate_done)
        self._worker.start()

    def _on_activate_done(self, ok: bool, msg: str):
        self.btn_activate.setEnabled(True)
        self.btn_activate.setText("تفعيل البرنامج")
        if ok:
            self._set_status(f"✅ {msg}", SUCCESS)
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(1200, self.accept)
        else:
            self._set_status(f"❌ {msg}", DANGER)

    def _set_status(self, text: str, color: str = TEXT):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"color: {color};")

    # ── سحب النافذة ───────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)


# ════════════════════════════════════════════════════════════════════════════
# نافذة انتهاء الاشتراك (تُعرض أثناء تشغيل التطبيق)
# ════════════════════════════════════════════════════════════════════════════

class LicenseExpiredDialog(QDialog):
    def __init__(self, reason: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("انتهى الترخيص")
        self.setFixedSize(400, 260)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        self.setAttribute(Qt.WA_TranslucentBackground)

        card = QFrame(self)
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(16)

        ic  = QLabel("⛔")
        ic.setFont(QFont("Segoe UI", 36))
        ic.setAlignment(Qt.AlignCenter)
        layout.addWidget(ic)

        ttl = QLabel("انتهت صلاحية الترخيص")
        ttl.setFont(QFont("Segoe UI", 14, QFont.Bold))
        ttl.setStyleSheet(f"color: {DANGER};")
        ttl.setAlignment(Qt.AlignCenter)
        layout.addWidget(ttl)

        msg = QLabel(reason)
        msg.setFont(QFont("Segoe UI", 11))
        msg.setStyleSheet(f"color: {SUBTLE};")
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        btn = QPushButton("إغلاق البرنامج")
        btn.setObjectName("btnActivate")
        btn.setStyleSheet(f"QPushButton#btnActivate {{ background: {DANGER}; }}")
        btn.clicked.connect(self._close_app)
        layout.addWidget(btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(card)

        # center
        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width()-self.width())//2, (screen.height()-self.height())//2)

    def _close_app(self):
        QApplication.quit()


# ════════════════════════════════════════════════════════════════════════════
# تشغيل مستقل للاختبار
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = LicenseWindow()
    result = w.exec_()
    print("Result:", result)
    sys.exit(0)
