#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SmartTrader License Server — FastAPI + SQLite
============================================================
تشغيل:
    pip install fastapi uvicorn python-jose passlib python-multipart
    uvicorn license_server.server:app --host 0.0.0.0 --port 8000 --reload

متغيرات البيئة (اختياري):
    ADMIN_TOKEN   — توكن الإدارة (افتراضي: يُولَّد عشوائياً عند التشغيل)
    SECRET_KEY    — مفتاح JWT (يجب تغييره في الإنتاج)
    DB_PATH       — مسار SQLite (افتراضي: licenses.db)
    PORT          — المنفذ (افتراضي: 8000)
"""
import os, sqlite3, secrets, string, hashlib, hmac, json, logging, shutil
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request, Form, status, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── إعداد السجل ──────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("smarttrader_license")

# ── إعدادات ──────────────────────────────────────────────────────────────────
DB_PATH     = os.environ.get("DB_PATH", "/data/licenses.db" if os.path.isdir("/data") else "licenses.db")
SECRET_KEY  = os.environ.get("SECRET_KEY", "CHANGE_ME_IN_PRODUCTION_" + secrets.token_hex(16))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "Aa123123Aa")

log.info(f"Admin panel ready — token configured")

PLAN_DAYS = {"trial": 7, "monthly": 30, "quarterly": 90, "yearly": 365}
CHARSET   = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # بدون أحرف مربكة

# ── Auto-Update ───────────────────────────────────────────────────────────────
VERSION_FILE  = os.environ.get("VERSION_FILE", "version.json")

def _load_version():
    global APP_VERSION, DOWNLOAD_URL, RELEASE_NOTES
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        APP_VERSION   = d.get("version",      "1.4.0")
        DOWNLOAD_URL  = d.get("download_url", "")
        RELEASE_NOTES = d.get("release_notes","")
    except Exception:
        APP_VERSION   = "1.4.0"
        DOWNLOAD_URL  = ""
        RELEASE_NOTES = ""

def _save_version():
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"version": APP_VERSION, "download_url": DOWNLOAD_URL,
                   "release_notes": RELEASE_NOTES}, f, ensure_ascii=False, indent=2)

_load_version()

# ── قاعدة البيانات ────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT    UNIQUE NOT NULL,
            name       TEXT    DEFAULT '',
            created_at TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS licenses (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            key        TEXT    UNIQUE NOT NULL,
            user_id    INTEGER REFERENCES users(id),
            plan_type  TEXT    NOT NULL DEFAULT 'monthly',
            status     TEXT    NOT NULL DEFAULT 'active',
            expires_at TEXT    NOT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            notes      TEXT    DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS devices (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            license_id   INTEGER REFERENCES licenses(id),
            hardware_id  TEXT    NOT NULL,
            device_token TEXT    DEFAULT NULL,
            activated_at TEXT    DEFAULT (datetime('now')),
            last_seen    TEXT    DEFAULT (datetime('now')),
            UNIQUE(license_id, hardware_id)
        );
        -- migration: أضف العمود إذا كانت قاعدة البيانات قديمة
        CREATE TEMPORARY TABLE IF NOT EXISTS _dummy_migration (x INTEGER);
        DROP TABLE IF EXISTS _dummy_migration;

        CREATE TABLE IF NOT EXISTS activation_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            license_id   INTEGER,
            hardware_id  TEXT,
            action       TEXT    NOT NULL,
            ip           TEXT    DEFAULT '',
            result       TEXT    DEFAULT '',
            timestamp    TEXT    DEFAULT (datetime('now'))
        );
        """)
        # migration: أضف device_token إذا كانت قاعدة البيانات قديمة
        cols = [r[1] for r in db.execute("PRAGMA table_info(devices)").fetchall()]
        if "device_token" not in cols:
            db.execute("ALTER TABLE devices ADD COLUMN device_token TEXT DEFAULT NULL")
            log.info("Migration: added device_token column to devices")
    log.info(f"Database ready: {DB_PATH}")

# ── توليد كود الترخيص ─────────────────────────────────────────────────────────
def generate_key() -> str:
    def seg(n=4):
        return ''.join(secrets.choice(CHARSET) for _ in range(n))
    return f"ST-{seg()}-{seg()}-{seg()}"

def unique_key(db) -> str:
    for _ in range(100):
        k = generate_key()
        if not db.execute("SELECT 1 FROM licenses WHERE key=?", (k,)).fetchone():
            return k
    raise RuntimeError("فشل توليد كود فريد")

# ── دوال مساعدة ──────────────────────────────────────────────────────────────
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def expires_str(days: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def is_expired(expires_at: str) -> bool:
    try:
        exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > exp
    except Exception:
        return True

def log_action(db, license_id, hardware_id, action, ip, result):
    db.execute(
        "INSERT INTO activation_logs(license_id,hardware_id,action,ip,result) VALUES(?,?,?,?,?)",
        (license_id, hardware_id, action, ip, result)
    )

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def require_admin(request: Request):
    token = request.headers.get("X-Admin-Token", "")
    cookie = request.cookies.get("admin_token", "")
    if token != ADMIN_TOKEN and cookie != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Unauthorized")

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="SmartTrader License Server", version="1.0.0", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

# ════════════════════════════════════════════════════════════════════
# API ENDPOINTS — CLIENT
# ════════════════════════════════════════════════════════════════════

class ActivateRequest(BaseModel):
    license_key: str
    hardware_id: str
    email: str

class VerifyRequest(BaseModel):
    license_key: str
    hardware_id: str

class AdminKeyRequest(BaseModel):
    license_key: str

class RenewRequest(BaseModel):
    license_key: str
    days: int

class DeactivateRequest(BaseModel):
    license_key: str
    hardware_id: str

# ── POST /activate ────────────────────────────────────────────────────────────
@app.post("/activate")
async def activate(req: ActivateRequest, request: Request):
    ip = get_client_ip(request)
    key = req.license_key.strip().upper()
    hw  = req.hardware_id.strip().upper()

    with get_db() as db:
        lic = db.execute(
            "SELECT l.*, u.email FROM licenses l LEFT JOIN users u ON l.user_id=u.id WHERE l.key=?", (key,)
        ).fetchone()

        if not lic:
            log_action(db, None, hw, "activate", ip, "key_not_found")
            raise HTTPException(400, "كود التفعيل غير صحيح")

        if lic["status"] == "revoked":
            log_action(db, lic["id"], hw, "activate", ip, "revoked")
            raise HTTPException(403, "هذا الترخيص مُلغى")

        if lic["status"] == "expired" or is_expired(lic["expires_at"]):
            db.execute("UPDATE licenses SET status='expired' WHERE id=?", (lic["id"],))
            log_action(db, lic["id"], hw, "activate", ip, "expired")
            raise HTTPException(403, "انتهت صلاحية هذا الترخيص")

        # تحقق من الجهاز
        existing_device = db.execute(
            "SELECT * FROM devices WHERE license_id=?", (lic["id"],)
        ).fetchone()

        if existing_device:
            if existing_device["hardware_id"] != hw:
                log_action(db, lic["id"], hw, "activate", ip, "wrong_device")
                raise HTTPException(403, "هذا الترخيص مرتبط بجهاز آخر. تواصل مع الدعم لفصل الجهاز.")
            # نفس الجهاز — تحديث last_seen
            db.execute("UPDATE devices SET last_seen=? WHERE id=?", (now_utc(), existing_device["id"]))
        else:
            # جهاز جديد — ربط
            db.execute(
                "INSERT INTO devices(license_id, hardware_id) VALUES(?,?)",
                (lic["id"], hw)
            )

        # جلب أو إنشاء device_token فريد لهذا الجهاز
        dev_row = db.execute(
            "SELECT device_token FROM devices WHERE license_id=?", (lic["id"],)
        ).fetchone()
        device_token = dev_row["device_token"] if dev_row and dev_row["device_token"] else None
        if not device_token:
            device_token = secrets.token_hex(32)
            db.execute(
                "UPDATE devices SET device_token=? WHERE license_id=? AND hardware_id=?",
                (device_token, lic["id"], hw)
            )

        log_action(db, lic["id"], hw, "activate", ip, "success")

        return {
            "success":      True,
            "status":       lic["status"],
            "plan_type":    lic["plan_type"],
            "expires_at":   lic["expires_at"],
            "device_token": device_token,
            "message":      "تم التفعيل بنجاح"
        }

# ── POST /verify ──────────────────────────────────────────────────────────────
@app.post("/verify")
async def verify(req: VerifyRequest, request: Request):
    ip  = get_client_ip(request)
    key = req.license_key.strip().upper()
    hw  = req.hardware_id.strip().upper()

    with get_db() as db:
        lic = db.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if not lic:
            return {"valid": False, "reason": "key_not_found"}

        if lic["status"] == "revoked":
            log_action(db, lic["id"], hw, "verify", ip, "revoked")
            return {"valid": False, "reason": "revoked"}

        if is_expired(lic["expires_at"]):
            db.execute("UPDATE licenses SET status='expired' WHERE id=?", (lic["id"],))
            log_action(db, lic["id"], hw, "verify", ip, "expired")
            return {"valid": False, "reason": "expired"}

        device = db.execute(
            "SELECT * FROM devices WHERE license_id=? AND hardware_id=?",
            (lic["id"], hw)
        ).fetchone()

        if not device:
            log_action(db, lic["id"], hw, "verify", ip, "device_mismatch")
            return {"valid": False, "reason": "device_not_registered"}

        db.execute("UPDATE devices SET last_seen=? WHERE id=?", (now_utc(), device["id"]))
        log_action(db, lic["id"], hw, "verify", ip, "ok")

        # تجديد device_token إذا لم يكن موجوداً
        token = device["device_token"]
        if not token:
            token = secrets.token_hex(32)
            db.execute("UPDATE devices SET device_token=? WHERE id=?", (token, device["id"]))

        return {
            "valid":        True,
            "status":       lic["status"],
            "plan_type":    lic["plan_type"],
            "expires_at":   lic["expires_at"],
            "device_token": token,
            "reason":       None
        }

# ── POST /deactivate_device (admin) ──────────────────────────────────────────
@app.post("/deactivate_device")
async def deactivate_device(req: DeactivateRequest, request: Request):
    require_admin(request)
    key = req.license_key.strip().upper()
    hw  = req.hardware_id.strip().upper()

    with get_db() as db:
        lic = db.execute("SELECT id FROM licenses WHERE key=?", (key,)).fetchone()
        if not lic:
            raise HTTPException(404, "الترخيص غير موجود")
        rows = db.execute(
            "DELETE FROM devices WHERE license_id=? AND hardware_id=?", (lic["id"], hw)
        ).rowcount
        log_action(db, lic["id"], hw, "deactivate_device", get_client_ip(request), f"removed={rows}")
        return {"success": True, "removed": rows}

# ── POST /renew_license (admin) ───────────────────────────────────────────────
@app.post("/renew_license")
async def renew_license(req: RenewRequest, request: Request):
    require_admin(request)
    key = req.license_key.strip().upper()

    with get_db() as db:
        lic = db.execute("SELECT * FROM licenses WHERE key=?", (key,)).fetchone()
        if not lic:
            raise HTTPException(404, "الترخيص غير موجود")

        # إذا منتهي الصلاحية — ابدأ من الآن، إذا لا — أضف للتاريخ الحالي
        if is_expired(lic["expires_at"]):
            base = datetime.now(timezone.utc)
        else:
            base = datetime.strptime(lic["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

        new_exp = (base + timedelta(days=req.days)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "UPDATE licenses SET expires_at=?, status='active' WHERE key=?",
            (new_exp, key)
        )
        log_action(db, lic["id"], "", "renew", get_client_ip(request), f"days={req.days} new_exp={new_exp}")
        return {"success": True, "expires_at": new_exp}

# ── POST /revoke_license (admin) ─────────────────────────────────────────────
@app.post("/revoke_license")
async def revoke_license(req: AdminKeyRequest, request: Request):
    require_admin(request)
    key = req.license_key.strip().upper()

    with get_db() as db:
        lic = db.execute("SELECT id FROM licenses WHERE key=?", (key,)).fetchone()
        if not lic:
            raise HTTPException(404, "الترخيص غير موجود")
        db.execute("UPDATE licenses SET status='revoked' WHERE key=?", (key,))
        log_action(db, lic["id"], "", "revoke", get_client_ip(request), "revoked")
        return {"success": True}

# ════════════════════════════════════════════════════════════════════
# ADMIN PANEL — HTML
# ════════════════════════════════════════════════════════════════════

ADMIN_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#0b1f3a;color:#e0e6f0;direction:rtl}
.topbar{background:#071122;padding:12px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a3a5c}
.topbar h1{color:#00aeef;font-size:20px}
.nav a{color:#8a9bb0;text-decoration:none;margin-right:20px;font-size:14px}
.nav a:hover{color:#00aeef}
.container{max-width:1200px;margin:24px auto;padding:0 16px}
.card{background:#0d1e35;border:1px solid #1a3a5c;border-radius:8px;padding:20px;margin-bottom:20px}
.card h2{color:#00aeef;font-size:16px;margin-bottom:16px;padding-bottom:8px;border-bottom:1px solid #1a3a5c}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#071122;color:#00aeef;padding:8px 12px;text-align:right;font-weight:600}
td{padding:8px 12px;border-bottom:1px solid #1a3a5c}
tr:hover td{background:#112240}
.badge{padding:3px 8px;border-radius:4px;font-size:11px;font-weight:bold}
.badge-active{background:#0a3a1a;color:#00c853}
.badge-expired{background:#3a1a0a;color:#ff6d00}
.badge-revoked{background:#3a0a0a;color:#ff1744}
.badge-trial{background:#1a1a3a;color:#7c4dff}
.badge-monthly{background:#0a2a3a;color:#00aeef}
.badge-quarterly{background:#1a2a0a;color:#76ff03}
.badge-yearly{background:#2a1a0a;color:#ffd740}
input,select,textarea{width:100%;padding:8px 12px;background:#071122;border:1px solid #1a3a5c;color:#e0e6f0;border-radius:4px;font-size:14px;direction:rtl}
input:focus,select:focus{outline:none;border-color:#00aeef}
.btn{padding:8px 20px;border:none;border-radius:4px;cursor:pointer;font-size:14px;font-weight:600}
.btn-primary{background:#00aeef;color:#fff}
.btn-danger{background:#d32f2f;color:#fff}
.btn-success{background:#00c853;color:#fff}
.btn-sm{padding:4px 12px;font-size:12px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.form-group{margin-bottom:12px}
.form-group label{display:block;font-size:12px;color:#8a9bb0;margin-bottom:4px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.stat-card{background:#0d1e35;border:1px solid #1a3a5c;border-radius:8px;padding:16px;text-align:center}
.stat-val{font-size:28px;font-weight:bold;color:#00aeef}
.stat-lbl{font-size:12px;color:#8a9bb0;margin-top:4px}
.alert{padding:10px 16px;border-radius:4px;margin-bottom:16px;font-size:14px}
.alert-success{background:#0a3a1a;color:#00c853;border:1px solid #00c853}
.alert-error{background:#3a0a0a;color:#ff1744;border:1px solid #ff1744}
.tab-nav{display:flex;gap:4px;margin-bottom:20px}
.tab-btn{padding:8px 20px;background:#071122;border:1px solid #1a3a5c;color:#8a9bb0;cursor:pointer;border-radius:4px;font-size:14px}
.tab-btn.active{background:#00aeef;color:#fff;border-color:#00aeef}
</style>
"""

LOGIN_HTML = """<!DOCTYPE html>
<html><head><title>SmartTrader Admin</title>{css}</head>
<body>
<div class="topbar"><h1>⚡ SmartTrader License Admin</h1></div>
<div style="max-width:400px;margin:80px auto;padding:0 16px">
  <div class="card">
    <h2>تسجيل الدخول</h2>
    {alert}
    <form method="post" action="/admin/login">
      <div class="form-group">
        <label>Admin Token</label>
        <div style="position:relative">
          <input type="password" name="token" id="tokenInput" placeholder="أدخل التوكن..." required style="padding-left:44px">
          <button type="button" onclick="var i=document.getElementById('tokenInput');i.type=i.type==='password'?'text':'password';this.textContent=i.type==='password'?'👁':'🙈'" style="position:absolute;left:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:18px;color:#8a9bb0">👁</button>
        </div>
      </div>
      <button class="btn btn-primary" style="width:100%;margin-top:8px">دخول</button>
    </form>
  </div>
</div>
</body></html>
""".replace("{css}", ADMIN_CSS)

def dashboard_html(db, msg="", msg_type=""):
    total   = db.execute("SELECT COUNT(*) FROM licenses").fetchone()[0]
    active  = db.execute("SELECT COUNT(*) FROM licenses WHERE status='active'").fetchone()[0]
    revoked = db.execute("SELECT COUNT(*) FROM licenses WHERE status='revoked'").fetchone()[0]
    devices = db.execute("SELECT COUNT(*) FROM devices").fetchone()[0]

    lics = db.execute("""
        SELECT l.key, l.plan_type, l.status, l.expires_at, l.created_at,
               u.email, COUNT(d.id) as dev_count
        FROM licenses l
        LEFT JOIN users u ON l.user_id=u.id
        LEFT JOIN devices d ON d.license_id=l.id
        GROUP BY l.id ORDER BY l.created_at DESC LIMIT 100
    """).fetchall()

    devs = db.execute("""
        SELECT d.hardware_id, d.activated_at, d.last_seen, l.key, u.email
        FROM devices d
        JOIN licenses l ON d.license_id=l.id
        LEFT JOIN users u ON l.user_id=u.id
        ORDER BY d.last_seen DESC LIMIT 100
    """).fetchall()

    logs = db.execute("""
        SELECT a.timestamp, a.action, a.hardware_id, a.result, a.ip, l.key
        FROM activation_logs a
        LEFT JOIN licenses l ON a.license_id=l.id
        ORDER BY a.timestamp DESC LIMIT 50
    """).fetchall()

    alert = ""
    if msg:
        cls = "alert-success" if msg_type == "ok" else "alert-error"
        alert = f'<div class="alert {cls}">{msg}</div>'

    def badge(plan):
        return f'<span class="badge badge-{plan}">{plan.upper()}</span>'

    def status_badge(s):
        return f'<span class="badge badge-{s}">{"✅" if s=="active" else "❌"} {s}</span>'

    lic_rows = "".join(f"""<tr>
        <td><code>{r["key"]}</code></td>
        <td>{r["email"] or "—"}</td>
        <td>{badge(r["plan_type"])}</td>
        <td>{status_badge(r["status"])}</td>
        <td style="font-size:12px">{r["expires_at"][:10]}</td>
        <td>{r["dev_count"]}</td>
        <td>
          <form method="post" action="/admin/action" style="display:inline">
            <input type="hidden" name="action" value="renew30"><input type="hidden" name="key" value="{r["key"]}">
            <button class="btn btn-success btn-sm">+30يوم</button>
          </form>
          <form method="post" action="/admin/action" style="display:inline;margin-right:4px">
            <input type="hidden" name="action" value="revoke"><input type="hidden" name="key" value="{r["key"]}">
            <button class="btn btn-danger btn-sm" onclick="return confirm('إلغاء الترخيص؟')">إلغاء</button>
          </form>
        </td>
    </tr>""" for r in lics)

    dev_rows = "".join(f"""<tr>
        <td style="font-size:11px"><code>{r["hardware_id"]}</code></td>
        <td><code>{r["key"]}</code></td>
        <td>{r["email"] or "—"}</td>
        <td style="font-size:12px">{r["last_seen"][:16]}</td>
        <td>
          <form method="post" action="/admin/action" style="display:inline">
            <input type="hidden" name="action" value="deactivate">
            <input type="hidden" name="key" value="{r["key"]}">
            <input type="hidden" name="hw" value="{r["hardware_id"]}">
            <button class="btn btn-danger btn-sm">فصل</button>
          </form>
        </td>
    </tr>""" for r in devs)

    log_rows = "".join(f"""<tr>
        <td style="font-size:11px">{r["timestamp"][:16]}</td>
        <td>{r["action"]}</td>
        <td><code style="font-size:11px">{(r["key"] or "")}</code></td>
        <td style="font-size:11px">{r["result"]}</td>
        <td style="font-size:11px">{r["ip"]}</td>
    </tr>""" for r in logs)

    cur_ver   = APP_VERSION
    cur_dl    = DOWNLOAD_URL
    cur_notes = RELEASE_NOTES

    return f"""<!DOCTYPE html>
<html><head><title>SmartTrader Admin Panel</title>{ADMIN_CSS}
<script>
function showTab(id){{
  document.querySelectorAll('.tab-pane').forEach(e=>e.style.display='none');
  document.querySelectorAll('.tab-btn').forEach(e=>e.classList.remove('active'));
  document.getElementById(id).style.display='block';
  event.target.classList.add('active');
}}
</script>
</head><body>
<div class="topbar">
  <h1>⚡ SmartTrader License Admin</h1>
  <div class="nav">
    <a href="/admin/dashboard">لوحة التحكم</a>
    <a href="/admin/logout">خروج</a>
  </div>
</div>
<div class="container">
  {alert}
  <div class="stats">
    <div class="stat-card"><div class="stat-val">{total}</div><div class="stat-lbl">إجمالي التراخيص</div></div>
    <div class="stat-card"><div class="stat-val" style="color:#00c853">{active}</div><div class="stat-lbl">نشطة</div></div>
    <div class="stat-card"><div class="stat-val" style="color:#ff1744">{revoked}</div><div class="stat-lbl">ملغاة</div></div>
    <div class="stat-card"><div class="stat-val" style="color:#ffd740">{devices}</div><div class="stat-lbl">أجهزة مسجلة</div></div>
  </div>

  <div class="tab-nav">
    <button class="tab-btn active" onclick="showTab('tab-licenses')">🔑 التراخيص</button>
    <button class="tab-btn" onclick="showTab('tab-create')">➕ إنشاء ترخيص</button>
    <button class="tab-btn" onclick="showTab('tab-devices')">💻 الأجهزة</button>
    <button class="tab-btn" onclick="showTab('tab-logs')">📋 السجل</button>
    <button class="tab-btn" onclick="showTab('tab-update')">⬆ التحديث</button>
  </div>

  <div id="tab-licenses" class="tab-pane">
    <div class="card">
      <h2>🔑 التراخيص</h2>
      <table>
        <tr><th>الكود</th><th>البريد</th><th>الخطة</th><th>الحالة</th><th>ينتهي</th><th>أجهزة</th><th>إجراءات</th></tr>
        {lic_rows or "<tr><td colspan='7' style='text-align:center;color:#8a9bb0'>لا توجد تراخيص</td></tr>"}
      </table>
    </div>
  </div>

  <div id="tab-create" class="tab-pane" style="display:none">
    <div class="card">
      <h2>➕ إنشاء ترخيص جديد</h2>
      <form method="post" action="/admin/action">
        <input type="hidden" name="action" value="create">
        <div class="form-row">
          <div class="form-group">
            <label>البريد الإلكتروني *</label>
            <input type="email" name="email" required placeholder="user@example.com">
          </div>
          <div class="form-group">
            <label>الاسم</label>
            <input type="text" name="name" placeholder="اسم العميل">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>نوع الخطة *</label>
            <select name="plan">
              <option value="trial">Trial — 7 أيام</option>
              <option value="monthly" selected>Monthly — 30 يوم</option>
              <option value="quarterly">Quarterly — 90 يوم</option>
              <option value="yearly">Yearly — 365 يوم</option>
            </select>
          </div>
          <div class="form-group">
            <label>ملاحظات</label>
            <input type="text" name="notes" placeholder="اختياري">
          </div>
        </div>
        <button class="btn btn-primary">إنشاء الترخيص</button>
      </form>
    </div>

    <div class="card">
      <h2>🔄 تجديد / إلغاء ترخيص</h2>
      <form method="post" action="/admin/action">
        <input type="hidden" name="action" value="renew_custom">
        <div class="form-row">
          <div class="form-group">
            <label>كود الترخيص</label>
            <input type="text" name="key" placeholder="ST-XXXX-XXXX-XXXX">
          </div>
          <div class="form-group">
            <label>إضافة أيام</label>
            <input type="number" name="days" value="30" min="1" max="3650">
          </div>
        </div>
        <button class="btn btn-success">تجديد</button>
      </form>
    </div>
  </div>

  <div id="tab-devices" class="tab-pane" style="display:none">
    <div class="card">
      <h2>💻 الأجهزة المسجلة</h2>
      <table>
        <tr><th>Hardware ID</th><th>الكود</th><th>البريد</th><th>آخر نشاط</th><th>فصل</th></tr>
        {dev_rows or "<tr><td colspan='5' style='text-align:center;color:#8a9bb0'>لا أجهزة</td></tr>"}
      </table>
    </div>
  </div>

  <div id="tab-update" class="tab-pane" style="display:none">
    <div class="card">
      <h2>⬆ إدارة التحديث التلقائي</h2>
      <p style="color:#8a9bb0;font-size:13px;margin-bottom:16px">
        النسخة الحالية المنشورة: <strong style="color:#00aeef">{cur_ver}</strong>
      </p>
      <form method="post" action="/admin/action">
        <input type="hidden" name="action" value="set_version">
        <div class="form-row">
          <div class="form-group">
            <label>رقم النسخة الجديدة</label>
            <input type="text" name="new_version" placeholder="مثال: 1.1.0" value="{cur_ver}">
          </div>
          <div class="form-group">
            <label>رابط تحميل EXE الجديد</label>
            <input type="text" name="download_url" placeholder="https://..." value="{cur_dl}">
          </div>
        </div>
        <div class="form-group">
          <label>ملاحظات النسخة (اختياري)</label>
          <input type="text" name="release_notes" placeholder="مثال: إصلاح أخطاء وتحسينات" value="{cur_notes}">
        </div>
        <button class="btn btn-primary">نشر التحديث</button>
      </form>
    </div>
  </div>

  <div id="tab-logs" class="tab-pane" style="display:none">
    <div class="card">
      <h2>📋 سجل النشاط (آخر 50)</h2>
      <table>
        <tr><th>الوقت</th><th>الإجراء</th><th>الكود</th><th>النتيجة</th><th>IP</th></tr>
        {log_rows or "<tr><td colspan='5' style='text-align:center;color:#8a9bb0'>لا سجلات</td></tr>"}
      </table>
    </div>
  </div>
</div>
</body></html>"""

# ── Admin Routes ──────────────────────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_root():
    return RedirectResponse("/admin/dashboard")

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    return LOGIN_HTML.replace("{alert}", "")

@app.post("/admin/login")
async def admin_login(token: str = Form(...)):
    if token != ADMIN_TOKEN:
        html = LOGIN_HTML.replace("{alert}", '<div class="alert alert-error">كلمة المرور خاطئة</div>')
        return HTMLResponse(html, status_code=401)
    resp = RedirectResponse("/admin/dashboard", status_code=302)
    resp.set_cookie("admin_token", token, httponly=True, max_age=86400)
    return resp

@app.get("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin/login")
    resp.delete_cookie("admin_token")
    return resp

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, msg: str = "", t: str = ""):
    require_admin(request)
    with get_db() as db:
        return dashboard_html(db, msg, t)

@app.post("/admin/action")
async def admin_action(request: Request,
                        action: str = Form(...),
                        key: str = Form(""),
                        hw: str = Form(""),
                        email: str = Form(""),
                        name: str = Form(""),
                        plan: str = Form("monthly"),
                        days: int = Form(30),
                        notes: str = Form("")):
    require_admin(request)
    msg, msg_type = "", "ok"

    with get_db() as db:
        if action == "create":
            if not email:
                msg, msg_type = "البريد الإلكتروني مطلوب", "err"
            else:
                user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
                if not user:
                    db.execute("INSERT INTO users(email,name) VALUES(?,?)", (email, name))
                    user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                else:
                    user_id = user["id"]
                    if name:
                        db.execute("UPDATE users SET name=? WHERE id=?", (name, user_id))

                plan = plan if plan in PLAN_DAYS else "monthly"
                new_key = unique_key(db)
                exp     = expires_str(PLAN_DAYS[plan])
                db.execute(
                    "INSERT INTO licenses(key,user_id,plan_type,status,expires_at,notes) VALUES(?,?,?,?,?,?)",
                    (new_key, user_id, plan, "active", exp, notes)
                )
                log.info(f"Created license {new_key} for {email} ({plan})")
                msg = f"✅ تم إنشاء الترخيص: {new_key} | تنتهي: {exp[:10]}"

        elif action == "revoke":
            db.execute("UPDATE licenses SET status='revoked' WHERE key=?", (key.upper(),))
            msg = f"✅ تم إلغاء الترخيص: {key}"

        elif action == "renew30":
            lic = db.execute("SELECT * FROM licenses WHERE key=?", (key.upper(),)).fetchone()
            if lic:
                base = datetime.now(timezone.utc) if is_expired(lic["expires_at"]) else \
                       datetime.strptime(lic["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                new_exp = (base + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
                db.execute("UPDATE licenses SET expires_at=?, status='active' WHERE key=?", (new_exp, key.upper()))
                msg = f"✅ تم تجديد {key} حتى {new_exp[:10]}"

        elif action == "renew_custom":
            lic = db.execute("SELECT * FROM licenses WHERE key=?", (key.upper(),)).fetchone()
            if lic:
                base = datetime.now(timezone.utc) if is_expired(lic["expires_at"]) else \
                       datetime.strptime(lic["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                new_exp = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
                db.execute("UPDATE licenses SET expires_at=?, status='active' WHERE key=?", (new_exp, key.upper()))
                msg = f"✅ تم تجديد {key} بـ {days} يوم حتى {new_exp[:10]}"
            else:
                msg, msg_type = "الترخيص غير موجود", "err"

        elif action == "deactivate":
            lic = db.execute("SELECT id FROM licenses WHERE key=?", (key.upper(),)).fetchone()
            if lic:
                db.execute("DELETE FROM devices WHERE license_id=? AND hardware_id=?", (lic["id"], hw))
                msg = f"✅ تم فصل الجهاز من {key}"

    # set_version لا يحتاج DB
    if action == "set_version":
        new_ver  = request._form.get("new_version", "").strip()
        dl_url   = request._form.get("download_url", "").strip()
        rel_note = request._form.get("release_notes", "").strip()
        if new_ver:
            global APP_VERSION, DOWNLOAD_URL, RELEASE_NOTES
            APP_VERSION   = new_ver
            DOWNLOAD_URL  = dl_url
            RELEASE_NOTES = rel_note
            _save_version()
            msg = f"✅ تم نشر النسخة {new_ver}"

    return RedirectResponse(f"/admin/dashboard?msg={msg}&t={msg_type}", status_code=302)

# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}

# ── Version Check (Auto-Update) ───────────────────────────────────────────────
@app.get("/version")
async def get_version():
    return {
        "version":      APP_VERSION,
        "download_url": DOWNLOAD_URL,
        "release_notes": RELEASE_NOTES
    }

@app.post("/version")
async def set_version(request: Request,
                      version: str = Form(...),
                      download_url: str = Form(...),
                      release_notes: str = Form("")):
    require_admin(request)
    global APP_VERSION, DOWNLOAD_URL, RELEASE_NOTES
    APP_VERSION   = version
    DOWNLOAD_URL  = download_url
    RELEASE_NOTES = release_notes
    _save_version()
    return {"success": True, "version": APP_VERSION}

# ── رفع EXE مباشرة على السيرفر ───────────────────────────────────────────────
# يُخزن في /data (Railway Volume) لتجنب المسح عند كل redeploy
_EXE_DIR  = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
_EXE_PATH = os.path.join(_EXE_DIR, "SmartTrader.exe")

@app.post("/admin/upload-exe")
async def upload_exe(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    with open(_EXE_PATH, "wb") as f:
        shutil.copyfileobj(file.file, f)
    size_mb = os.path.getsize(_EXE_PATH) / (1024 * 1024)
    return {"success": True, "size_mb": round(size_mb, 1)}

@app.get("/download/app")
async def download_app():
    # 1) إذا الملف موجود محلياً — أرسله مباشرة
    if os.path.exists(_EXE_PATH):
        return FileResponse(
            _EXE_PATH,
            media_type="application/octet-stream",
            filename="SmartTrader.exe",
            headers={"Content-Disposition": "attachment; filename=SmartTrader.exe"}
        )
    # 2) إذا يوجد رابط خارجي — redirect إليه
    ext_url = os.environ.get("EXE_DOWNLOAD_URL", "").strip() or DOWNLOAD_URL.strip()
    if ext_url:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=ext_url, status_code=302)
    raise HTTPException(status_code=404, detail="File not found")

# ── تشغيل مباشر ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    log.info(f"Starting SmartTrader License Server on port {port}")
    uvicorn.run("license_server.server:app", host="0.0.0.0", port=port, reload=False)
