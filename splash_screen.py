# -*- coding: utf-8 -*-
"""
splash_screen.py — شاشة التحميل الاحترافية لـ SmartTrader
"""
import os, sys
from PyQt5.QtWidgets import QSplashScreen, QApplication
from PyQt5.QtGui     import QPixmap, QPainter, QColor, QFont, QLinearGradient
from PyQt5.QtCore    import Qt, QTimer, QRect


def _resource(rel: str) -> str:
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)


class SmartTraderSplash(QSplashScreen):
    """شاشة تحميل مع رسائل متسلسلة"""

    MESSAGES = [
        "Loading Trading Engine...",
        "Connecting to Market Data...",
        "Loading Analyzer...",
        "Initializing Risk Manager...",
        "Starting SmartTrader...",
    ]

    def __init__(self):
        img_path = _resource(os.path.join("assets", "splash.png"))
        if os.path.exists(img_path):
            px = self._draw_version_on(QPixmap(img_path))
        else:
            px = self._make_fallback()

        super().__init__(px, Qt.WindowStaysOnTopHint)
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.SplashScreen
        )
        self._msg_index = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._next_message)

    VERSION = "v1.4.3"

    def _make_fallback(self) -> QPixmap:
        """Splash احتياطي إذا لم يوجد ملف الصورة"""
        px = QPixmap(600, 350)
        px.fill(QColor(11, 31, 58))
        p = QPainter(px)
        p.setPen(QColor(0, 174, 239))
        f = QFont("Segoe UI", 36, QFont.Bold)
        p.setFont(f)
        p.drawText(QRect(0, 80, 600, 80), Qt.AlignCenter, "SmartTrader")
        p.setPen(QColor(192, 192, 192))
        f2 = QFont("Segoe UI", 14)
        p.setFont(f2)
        p.drawText(QRect(0, 165, 600, 40), Qt.AlignCenter,
                   "Professional Trading Platform")
        p.setPen(QColor(100, 180, 100))
        f3 = QFont("Segoe UI", 11)
        p.setFont(f3)
        p.drawText(QRect(0, 205, 600, 30), Qt.AlignCenter, self.VERSION)
        p.end()
        return px

    def _draw_version_on(self, px: QPixmap) -> QPixmap:
        """يرسم النسخة فوق صورة splash.png"""
        p = QPainter(px)
        f = QFont("Segoe UI", 11, QFont.Bold)
        p.setFont(f)
        p.setPen(QColor(100, 220, 100))
        p.drawText(QRect(0, px.height() - 45, px.width() - 10, 30),
                   Qt.AlignRight | Qt.AlignVCenter, self.VERSION)
        p.end()
        return px

    def start(self, interval_ms: int = 600):
        """يبدأ تسلسل الرسائل"""
        self.show()
        QApplication.processEvents()
        self._show_message(self.MESSAGES[0])
        self._timer.start(interval_ms)

    def _next_message(self):
        self._msg_index += 1
        if self._msg_index < len(self.MESSAGES):
            self._show_message(self.MESSAGES[self._msg_index])
        else:
            self._timer.stop()

    def _show_message(self, msg: str):
        self.showMessage(
            f"  {msg}",
            Qt.AlignBottom | Qt.AlignLeft,
            QColor(0, 174, 239)
        )
        QApplication.processEvents()

    def finish_and_close(self, main_window):
        self._timer.stop()
        self._show_message("Ready.")
        QApplication.processEvents()
        self.finish(main_window)
