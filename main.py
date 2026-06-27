# -*- coding: utf-8 -*-
"""
main.py — نقطة دخول SmartTrader
يُشغَّل هذا الملف من PyInstaller ويظهر Splash ثم يفتح الواجهة الرئيسية.
"""
import sys, os

# ── إغلاق أي نسخة أخرى من SmartTrader فوراً (Windows API فقط) ────────────
def _kill_other_instances():
    """يقتل أي عملية SmartTrader.exe أخرى بدون psutil."""
    if sys.platform != "win32":
        return
    try:
        import ctypes, ctypes.wintypes, subprocess
        current = os.getpid()
        me = os.path.basename(sys.argv[0]).lower()
        # استخدم taskkill لقتل النسخ الأخرى
        subprocess.run(
            ["taskkill", "/F", "/FI", f"IMAGENAME eq SmartTrader.exe",
             "/FI", f"PID ne {current}"],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            capture_output=True
        )
    except Exception:
        pass

_kill_other_instances()

# ── منع تشغيل أكثر من نسخة واحدة (Mutex فقط) ────────────────────────────
def _ensure_single_instance():
    """يستخدم Windows Named Mutex — بدون psutil."""
    if sys.platform != "win32":
        return None
    import ctypes
    ERROR_ALREADY_EXISTS = 183

    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "SmartTrader_SI_Mutex_v2")
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        # ابحث عن نافذة SmartTrader وأبرزها
        try:
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.user32.FindWindowW(None, "SmartTrader"), 9)
        except Exception:
            pass
        sys.exit(0)
    return mutex

_MUTEX = _ensure_single_instance()

# ── إعداد المسارات أولاً (قبل أي import آخر) ─────────────────────────────
from utils.paths import bootstrap, appdata_dir, data_path, resource_path
bootstrap()

# ── إعادة توجيه stdout/stderr للـ log (عند التشغيل كـ EXE) ──────────────
if getattr(sys, "frozen", False):
    import logging
    log_file = data_path("logs", "smarttrader.log")
    logging.basicConfig(
        filename=log_file,
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        encoding="utf-8",
    )

# ── إخفاء تحذيرات ib_insync ──────────────────────────────────────────────
import logging as _lg
_lg.getLogger("ib_insync").setLevel(_lg.CRITICAL)
_lg.getLogger("asyncio").setLevel(_lg.CRITICAL)

# ── تشغيل التطبيق ─────────────────────────────────────────────────────────
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui     import QIcon
from PyQt5.QtCore    import Qt

def main():
    # ── High DPI ─────────────────────────────────────────────────────────
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)

    app = QApplication(sys.argv)
    app.setApplicationName("SmartTrader")
    app.setApplicationVersion("1.4.3")
    app.setOrganizationName("SmartTrader Technologies")
    app.setOrganizationDomain("smarttrader.app")

    # أيقونة التطبيق
    icon_path = resource_path("assets", "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # ══════════════════════════════════════════════════════════════════
    # الخطوة 0 — Splash فوراً (بدون انتظار شبكة)
    # ══════════════════════════════════════════════════════════════════
    from splash_screen import SmartTraderSplash
    splash = SmartTraderSplash()
    splash.start(interval_ms=2000)
    app.processEvents()

    # ══════════════════════════════════════════════════════════════════
    # الخطوة 1 — فحص التحديث (background للتحقق، main thread للعرض)
    # ══════════════════════════════════════════════════════════════════
    import threading
    _update_data = {}

    def _bg_check():
        try:
            import requests
            from license_client.license_config import SERVER_URL, REQUEST_TIMEOUT
            from packaging import version as pkg_version
            from license_client.updater import CURRENT_VERSION, _get_skipped
            r = requests.get(f"{SERVER_URL}/version", timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                d  = r.json()
                sv = d.get("version", "")
                dl = d.get("download_url", "")
                notes = d.get("release_notes", "")
                if (dl and sv
                        and _get_skipped() != sv
                        and pkg_version.parse(sv) > pkg_version.parse(CURRENT_VERSION)):
                    _update_data["version"] = sv
                    _update_data["url"]     = dl
                    _update_data["notes"]   = notes
        except Exception:
            pass

    t = threading.Thread(target=_bg_check, daemon=True)
    t.start()
    t.join(timeout=5)

    if _update_data:
        from license_client.updater import UpdateDialog, _dialog_open
        import license_client.updater as _upd_mod
        if not _upd_mod._dialog_open:          # حماية من التكرار
            _upd_mod._dialog_open = True
            splash.close()
            dlg = UpdateDialog(
                _update_data["version"],
                _update_data["url"],
                _update_data["notes"],
            )
            dlg.exec_()
            _upd_mod._dialog_open = False
            splash = SmartTraderSplash()
            splash.start(interval_ms=2000)
            app.processEvents()

    # ══════════════════════════════════════════════════════════════════
    # الخطوة 2 — فحص الترخيص (محلي أولاً — سريع بدون شبكة)
    # ══════════════════════════════════════════════════════════════════
    from license_client.license_manager import check_license
    from license_client.license_window  import LicenseWindow
    from PyQt5.QtWidgets import QDialog

    ok, reason = check_license()
    if not ok:
        splash.close()
        license_win = LicenseWindow(prefill_key="")
        if license_win.exec_() != QDialog.Accepted:
            sys.exit(0)
        # أعد فتح splash بعد التفعيل
        splash = SmartTraderSplash()
        splash.start(interval_ms=2000)
        app.processEvents()

    # ══════════════════════════════════════════════════════════════════
    # الخطوة 3 — النافذة الرئيسية (الشارت يُنشأ بعد ظهور النافذة)
    # ══════════════════════════════════════════════════════════════════
    from trading_app import TradingApp
    window = TradingApp()

    # أيقونة النافذة
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))

    window.setWindowTitle("SmartTrader v1.4 — Professional Trading Platform")

    # إغلاق Splash وفتح النافذة
    splash.finish_and_close(window)
    window.showMaximized()
    window.raise_()
    window.activateWindow()

    # ══════════════════════════════════════════════════════════════════
    # الخطوة 4 — مراقب الترخيص الدوري (كل 6 ساعات)
    # ══════════════════════════════════════════════════════════════════
    from license_client.license_manager import get_local_info
    from license_client.license_window  import LicenseExpiredDialog

    local = get_local_info()
    if local:
        from license_client.license_manager import LicenseWatcher
        watcher = LicenseWatcher(local["license_key"], parent=app)

        def _on_license_expired(msg: str):
            dlg = LicenseExpiredDialog(msg)
            dlg.exec_()

        watcher.expired.connect(_on_license_expired)
        watcher.start()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
