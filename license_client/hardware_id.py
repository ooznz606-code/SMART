#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hardware ID — fingerprint فريد للجهاز
يجمع: CPU ID + Disk Serial + Motherboard Serial → SHA256
"""
import subprocess, hashlib, re, sys, logging

log = logging.getLogger("hardware_id")

def _wmic(query: str) -> str:
    try:
        out = subprocess.check_output(
            ["wmic"] + query.split() + ["get", "/value"],
            stderr=subprocess.DEVNULL,
            timeout=8,
            creationflags=0x08000000  # CREATE_NO_WINDOW
        ).decode("utf-8", errors="ignore")
        for line in out.splitlines():
            line = line.strip()
            if "=" in line:
                val = line.split("=", 1)[1].strip()
                if val and val.lower() not in ("", "none", "to be filled by o.e.m.", "o.e.m.", "default string"):
                    return val
    except Exception as e:
        log.debug(f"wmic error ({query}): {e}")
    return ""

def get_hardware_id() -> str:
    """
    يُعيد SHA256 مبني على معرّفات الجهاز الفيزيائية.
    يعمل على Windows فقط.
    """
    cpu    = _wmic("cpu ProcessorId")
    disk   = _wmic("diskdrive SerialNumber")
    mobo   = _wmic("baseboard SerialNumber")

    # fallback إذا تعذّر الحصول على أي قيمة
    cpu    = cpu    or _wmic("cpu Name")
    disk   = disk   or _wmic("diskdrive Caption")
    mobo   = mobo   or _wmic("baseboard Product")

    raw = f"ST|{cpu}|{disk}|{mobo}".upper()
    raw = re.sub(r"\s+", " ", raw).strip()

    hw_id = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    log.debug(f"HW raw='{raw}' → id={hw_id[:8]}...")
    return hw_id

if __name__ == "__main__":
    print("Hardware ID:", get_hardware_id())
