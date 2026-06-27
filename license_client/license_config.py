#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
إعدادات خادم الترخيص
"""

# ── عنوان الخادم ─────────────────────────────────────────────────────────────
# غيّر هذا إلى عنوان الخادم الفعلي عند النشر
# مثال: https://license.smarttrader.io
SERVER_URL = "https://lucid-reflection-production-6b54.up.railway.app"

# ── إعدادات المهلة ────────────────────────────────────────────────────────────
REQUEST_TIMEOUT   = 10   # ثوان — مهلة طلب API
VERIFY_INTERVAL_H = 6    # ساعات — الفترة بين كل فحص دوري
GRACE_PERIOD_H    = 3    # ساعات — فترة السماح عند انقطاع الخادم (مقلّصة لمنع التحايل)

# ── مفتاح HMAC الاحتياطي فقط (يُستبدل بـ device_token من السيرفر) ──────────
HMAC_SECRET = b"ST_HMAC_2025_FALLBACK_ONLY"

# ── مسار حفظ الترخيص المحلي ──────────────────────────────────────────────────
import os, sys

def get_license_path() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~")
    folder = os.path.join(base, ".smarttrader")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "license.dat")

LICENSE_PATH = get_license_path()
