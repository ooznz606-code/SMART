#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
License Manager — تحقق، تفعيل، فحص دوري
"""
import json, hmac, hashlib, time, logging, os
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

import requests
from PyQt5.QtCore import QTimer, QObject, pyqtSignal

from license_client.hardware_id  import get_hardware_id
from license_client.license_config import (
    SERVER_URL, REQUEST_TIMEOUT, VERIFY_INTERVAL_H,
    GRACE_PERIOD_H, HMAC_SECRET, LICENSE_PATH
)

log = logging.getLogger("license_manager")

# ── HMAC signing (يستخدم device_token إذا توفّر، وإلا HMAC_SECRET الاحتياطي) ─
def _get_secret(data: dict) -> bytes:
    token = data.get("device_token", "")
    return token.encode("utf-8") if token else HMAC_SECRET

def _sign(data: dict) -> str:
    secret  = _get_secret(data)
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()

def _verify_sig(data: dict, sig: str) -> bool:
    return hmac.compare_digest(_sign(data), sig)

# ── Local license file ────────────────────────────────────────────────────────
def _save_local(license_key: str, hardware_id: str, status: str,
                plan_type: str, expires_at: str,
                device_token: str = "") -> None:
    payload = {
        "license_key":  license_key,
        "hardware_id":  hardware_id,
        "status":       status,
        "plan_type":    plan_type,
        "expires_at":   expires_at,
        "device_token": device_token,
        "saved_at":     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }
    payload["sig"] = _sign({k: v for k, v in payload.items()})
    with open(LICENSE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def _load_local() -> Optional[dict]:
    try:
        with open(LICENSE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        sig = data.pop("sig", "")
        if not _verify_sig(data, sig):
            log.warning("ملف الترخيص المحلي محرَّف!")
            return None
        data["sig"] = sig
        return data
    except Exception:
        return None

def _is_expired(expires_at: str) -> bool:
    try:
        exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > exp
    except Exception:
        return True

def _within_grace(saved_at: str) -> bool:
    """True if server was reachable within GRACE_PERIOD_H"""
    try:
        sv = datetime.strptime(saved_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < sv + timedelta(hours=GRACE_PERIOD_H)
    except Exception:
        return False

# ════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════

def activate(license_key: str, email: str) -> Tuple[bool, str]:
    """
    تفعيل ترخيص جديد.
    Returns (True, "") on success or (False, error_message)
    """
    hw = get_hardware_id()
    try:
        resp = requests.post(
            f"{SERVER_URL}/activate",
            json={"license_key": license_key.strip().upper(),
                  "hardware_id": hw, "email": email.strip()},
            timeout=REQUEST_TIMEOUT
        )
        if resp.status_code == 200:
            data = resp.json()
            _save_local(license_key.upper(), hw,
                        data["status"], data["plan_type"], data["expires_at"],
                        data.get("device_token", ""))
            return True, data.get("message", "تم التفعيل بنجاح")
        else:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            return False, str(detail)
    except requests.exceptions.ConnectionError:
        return False, "لا يمكن الاتصال بخادم التراخيص. تحقق من الاتصال بالإنترنت."
    except requests.exceptions.Timeout:
        return False, "انتهت مهلة الاتصال بالخادم."
    except Exception as e:
        return False, f"خطأ: {e}"


def verify_online(license_key: str) -> Tuple[bool, str]:
    """
    فحص سريع مع الخادم.
    Returns (True, "") or (False, reason)
    """
    hw = get_hardware_id()
    try:
        resp = requests.post(
            f"{SERVER_URL}/verify",
            json={"license_key": license_key.strip().upper(), "hardware_id": hw},
            timeout=REQUEST_TIMEOUT
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("valid"):
                _save_local(license_key.upper(), hw,
                            data["status"], data["plan_type"], data["expires_at"],
                            data.get("device_token", ""))
                return True, ""
            return False, data.get("reason", "invalid")
        return False, f"server_error_{resp.status_code}"
    except Exception:
        return False, "server_unreachable"


def check_license(online: bool = False) -> Tuple[bool, str]:
    """
    فحص الترخيص — محلي فقط عند startup (online=False) لتجنب تجمد الواجهة.
    الفحص الشبكي يأتي لاحقاً من LicenseWatcher.
    """
    local = _load_local()

    if not local:
        return False, "لم يتم تفعيل البرنامج بعد"

    hw = get_hardware_id()
    if local.get("hardware_id") != hw:
        return False, "هذا الجهاز غير مرتبط بالترخيص"

    if _is_expired(local.get("expires_at", "")):
        return False, "انتهت صلاحية الترخيص"

    if not online:
        # عند بدء التشغيل: قبول محلي إذا كان ضمن فترة السماح
        if _within_grace(local.get("saved_at", "")):
            return True, ""
        # منتهية فترة السماح — نتحقق من الشبكة مرة واحدة
        online = True

    ok, reason = verify_online(local["license_key"])
    if ok:
        return True, ""

    if reason == "server_unreachable":
        if _within_grace(local.get("saved_at", "")):
            log.warning("الخادم غير متاح — قبول فترة السماح")
            return True, ""
        return False, "لا يمكن التحقق من الترخيص. يرجى الاتصال بالإنترنت."

    reasons_map = {
        "expired":               "انتهت صلاحية الترخيص",
        "revoked":               "تم إلغاء هذا الترخيص",
        "device_not_registered": "الجهاز غير مسجل — أعد التفعيل",
        "key_not_found":         "كود الترخيص غير موجود",
        "device_mismatch":       "الترخيص مرتبط بجهاز آخر",
    }
    return False, reasons_map.get(reason, f"غير صالح: {reason}")


def get_local_info() -> Optional[dict]:
    """يُعيد بيانات الترخيص المحلية أو None"""
    return _load_local()


# ════════════════════════════════════════════════════════════════════════════
# Periodic Timer (PyQt5)
# ════════════════════════════════════════════════════════════════════════════

class LicenseWatcher(QObject):
    """
    يفحص الترخيص كل VERIFY_INTERVAL_H ساعة داخل حلقة Qt.
    صل signal expired إلى slot يغلق التطبيق.
    """
    expired = pyqtSignal(str)  # يرسل رسالة الخطأ

    def __init__(self, license_key: str, parent=None):
        super().__init__(parent)
        self._key = license_key
        self._timer = QTimer(self)
        self._timer.setInterval(VERIFY_INTERVAL_H * 3600 * 1000)  # ms
        self._timer.timeout.connect(self._check)

    def start(self):
        self._timer.start()
        log.info(f"LicenseWatcher started — فحص كل {VERIFY_INTERVAL_H} ساعة")

    def stop(self):
        self._timer.stop()

    def _check(self):
        ok, msg = check_license()
        if not ok:
            log.warning(f"License check failed: {msg}")
            self._timer.stop()
            self.expired.emit(msg)
        else:
            log.info("License check OK")
