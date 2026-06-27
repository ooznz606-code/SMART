# -*- coding: utf-8 -*-
"""
utils/paths.py — إدارة مسارات SmartTrader
==========================================
وضع dev (script)  → كل الملفات بجانب trading_app.py
وضع EXE مثبّت    → ملفات قابلة للكتابة في %APPDATA%\SmartTrader
                    ملفات مضمّنة في sys._MEIPASS (read-only)
"""
from __future__ import annotations
import os, sys, json

APP_NAME    = "SmartTrader"
APP_VERSION = "1.3.1"


# ── مسارات أساسية ────────────────────────────────────────────────────────────

def exe_dir() -> str:
    """مجلد ملف EXE (Program Files) أو مجلد السكريبت في وضع dev."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def appdata_dir() -> str:
    """%APPDATA%\\SmartTrader — مجلد البيانات القابلة للكتابة."""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, APP_NAME)
    # وضع dev: استخدم نفس مجلد السكريبت
    return exe_dir()


def resource_path(*parts: str) -> str:
    """مسار لملف مضمّن داخل EXE (read-only: assets, icons, etc.)."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = exe_dir()
    return os.path.join(base, *parts)


def data_path(*parts: str) -> str:
    """مسار لملف بيانات قابل للكتابة في AppData."""
    return os.path.join(appdata_dir(), *parts)


# ── Bootstrap: أنشئ المجلدات والملفات الافتراضية ────────────────────────────

DEFAULT_SETTINGS = {
    "app_name":    APP_NAME,
    "version":     APP_VERSION,
    "ib_host":     "127.0.0.1",
    "ib_port":     7497,
    "ib_client_id": 1,
    "trading_mode": "analyzer_only",
    "max_open_trades": 3,
    "risk_per_trade":  2.0,
    "tv_username": "",
    "tv_password": "",
    "theme": "dark",
    "auto_start_datafeed": True,
}

DEFAULT_PROFILES = {
    "QQQ":  {"enabled": True,  "min_confidence": 80.3, "max_confidence": 83.9, "min_adx": 28, "max_adx": 999, "block_short": False, "block_long": False},
    "LLY":  {"enabled": True,  "min_confidence": 74.0, "max_confidence": 79.3, "min_adx": 38, "max_adx": 50,  "block_short": False, "block_long": False},
    "AAPL": {"enabled": True,  "min_confidence": 75.4, "max_confidence": 79.3, "min_adx": 30, "max_adx": 59,  "block_short": False, "block_long": False},
    "MSFT": {"enabled": True,  "min_confidence": 71.0, "max_confidence": 83.0, "min_adx": 38, "max_adx": 50,  "block_short": False, "block_long": False},
    "NVDA": {"enabled": True,  "min_confidence": 74.0, "max_confidence": 81.8, "min_adx": 34, "max_adx": 55,  "block_short": False, "block_long": False},
    "AMZN": {"enabled": True,  "min_confidence": 76.4, "max_confidence": 79.4, "min_adx": 25, "max_adx": 45,  "block_short": False, "block_long": False},
    "GOOGL":{"enabled": True,  "min_confidence": 72.0, "max_confidence": 84.0, "min_adx": 25, "max_adx": 48,  "block_short": False, "block_long": False},
    "NFLX": {"enabled": True,  "min_confidence": 70.0, "max_confidence": 73.5, "min_adx": 20, "max_adx": 60,  "block_short": False, "block_long": False},
    "CRM":  {"enabled": True,  "min_confidence": 70.4, "max_confidence": 73.9, "min_adx": 20, "max_adx": 999, "block_short": False, "block_long": False},
    "SHOP": {"enabled": True,  "min_confidence": 70.0, "max_confidence": 76.4, "min_adx": 20, "max_adx": 48,  "block_short": False, "block_long": False},
    "ADBE": {"enabled": True,  "min_confidence": 72.1, "max_confidence": 75.3, "min_adx": 20, "max_adx": 51,  "block_short": False, "block_long": False},
    "AVGO": {"enabled": True,  "min_confidence": 75.4, "max_confidence": 90.0, "min_adx": 35, "max_adx": 999, "block_short": False, "block_long": False},
    "COST": {"enabled": True,  "min_confidence": 81.4, "max_confidence": 82.4, "min_adx": 28, "max_adx": 60,  "block_short": False, "block_long": False},
    "SPY":  {"enabled": True,  "min_confidence": 77.5, "max_confidence": 84.0, "min_adx": 39, "max_adx": 999, "block_short": False, "block_long": False},
    "META": {"enabled": False}, "PG": {"enabled": False}, "TSLA": {"enabled": False},
    "AMD":  {"enabled": False}, "JPM": {"enabled": False}, "V":    {"enabled": False},
}


def bootstrap() -> None:
    """يُستدعى مرة واحدة عند بدء التطبيق — ينشئ كل ما يلزم."""
    ad = appdata_dir()
    for folder in ["logs", "data", "chart_data"]:
        os.makedirs(os.path.join(ad, folder), exist_ok=True)

    # settings.json
    s_path = data_path("settings.json")
    if not os.path.exists(s_path):
        with open(s_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2, ensure_ascii=False)

    # symbol_profiles.json
    p_path = data_path("symbol_profiles.json")
    if not os.path.exists(p_path):
        with open(p_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_PROFILES, f, indent=2, ensure_ascii=False)

    # config.txt (للتوافق مع tv_datafeed)
    c_path = data_path("config.txt")
    if not os.path.exists(c_path):
        with open(c_path, "w", encoding="utf-8") as f:
            f.write("username=\npassword=\n")

    # trade_history.json
    th_path = data_path("trade_history.json")
    if not os.path.exists(th_path):
        with open(th_path, "w", encoding="utf-8") as f:
            f.write("[]")

    # risk_memory.json
    rm_path = data_path("risk_memory.json")
    if not os.path.exists(rm_path):
        with open(rm_path, "w", encoding="utf-8") as f:
            f.write("{}")


def load_settings() -> dict:
    try:
        with open(data_path("settings.json"), encoding="utf-8") as f:
            d = json.load(f)
        # دمج مع الافتراضي للمفاتيح الجديدة
        merged = {**DEFAULT_SETTINGS, **d}
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    with open(data_path("settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
