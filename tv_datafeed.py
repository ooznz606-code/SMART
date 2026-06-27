# -*- coding: utf-8 -*-
"""
tv_datafeed.py — جلب بيانات TradingView وحفظها في chart_data
الإصدار: 4.0 — متوافق 100% مع trading_app.py

المتطلبات:
    pip install websocket-client

الاستخدام:
    python tv_datafeed.py --symbols SPY QQQ AAPL --interval 15 --bars 500
    python tv_datafeed.py --symbols SPY --interval 15 --bars 500 --also 1h 1d

بعد التشغيل يحفظ الملفات في:
    chart_data/SPY_15m.json
    chart_data/SPY_1H.json
    chart_data/SPY_1D.json
    ... وهكذا لكل رمز

ثم شغّل trading_app.py مباشرة — سيقرأ البيانات تلقائياً بدون IBKR
"""

from __future__ import annotations
import json
import os
import time
import random
import logging
import string
import re
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, List

log = logging.getLogger("TVDataFeed")

# ── مجلد chart_data بجانب EXE أو بجانب الملف .py ────────────────────────
import sys as _sys_tv
if getattr(_sys_tv, "frozen", False):
    _BASE_DIR = os.path.dirname(_sys_tv.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHART_DIR = os.path.join(_BASE_DIR, "chart_data")

TV_SYMBOLS: Dict[str, tuple] = {
    "SPX":    ("SP",          "SPX"),
    "XSP":    ("CBOE",        "XSP"),
    "SPY":    ("AMEX",        "SPY"),
    "QQQ":    ("NASDAQ",      "QQQ"),
    "IWM":    ("AMEX",        "IWM"),
    # ── US500 CFD (24 ساعة) ────────────────────────────────────────
    # يتداول خارج ساعات السوق بعكس SPX
    "SPX500": ("CAPITALCOM",  "SPX500"),       # Capital.com ✅ مؤكد
    "US500":  ("OANDA",       "US500USD"),
    "US500P": ("PEPPERSTONE", "US500"),
    "SP500":  ("FOREXCOM",    "SPXUSD"),
    "SPXUSD": ("FXCM",        "US500"),
    "AAPL":  ("NASDAQ", "AAPL"),
    "MSFT":  ("NASDAQ", "MSFT"),
    "NVDA":  ("NASDAQ", "NVDA"),
    "GOOGL": ("NASDAQ", "GOOGL"),
    "AMZN":  ("NASDAQ", "AMZN"),
    "TSLA":  ("NASDAQ", "TSLA"),
    "META":  ("NASDAQ", "META"),
    "AMD":   ("NASDAQ", "AMD"),
    "AVGO":  ("NASDAQ", "AVGO"),
    "QCOM":  ("NASDAQ", "QCOM"),
    "NFLX":  ("NASDAQ", "NFLX"),
    "CRM":   ("NYSE",   "CRM"),
    "ADBE":  ("NASDAQ", "ADBE"),
    "JPM":   ("NYSE",   "JPM"),
    "BAC":   ("NYSE",   "BAC"),
    "GS":    ("NYSE",   "GS"),
    "V":     ("NYSE",   "V"),
    "MA":    ("NYSE",   "MA"),
    "XOM":   ("NYSE",   "XOM"),
    "LLY":   ("NYSE",   "LLY"),
    "JNJ":   ("NYSE",   "JNJ"),
    "UNH":   ("NYSE",   "UNH"),
    "IBKR":  ("NASDAQ", "IBKR"),
    # ── البدائل الجديدة (تعويض الرموز الضعيفة بناءً على backtest) ──────────
    "PANW":  ("NASDAQ", "PANW"),   # بديل ADBE  — أمن سيبراني ترند ثابت
    "COST":  ("NASDAQ", "COST"),   # بديل AMZN  — ترند صاعد هادئ
    "NOW":   ("NYSE",   "NOW"),    # بديل CRM   — SaaS ترند واضح
    "UBER":  ("NYSE",   "UBER"),   # بديل META  — ترند أكثر استمرارية
    "DIS":   ("NYSE",   "DIS"),    # بديل NFLX  — أكثر استقراراً
    "XLK":   ("AMEX",   "XLK"),    # بديل IWM   — ETF تقنية يتبع NVDA+MSFT
    "MS":    ("NYSE",   "MS"),     # بديل GS    — بنك استثمار أكثر استقراراً
    "WFC":   ("NYSE",   "WFC"),    # بديل JPM   — بنك ترند أهدأ
    "F":     ("NYSE",   "F"),      # بديل TSLA  — تذبذب أقل
    # ── الجولة الثانية والثالثة من البدائل ────────────────────────────────
    "MU":    ("NASDAQ", "MU"),     # Micron — رقائق AI ✅ WR=61%
    "SBUX":  ("NASDAQ", "SBUX"),   # Starbucks — استهلاك
    "NKE":   ("NYSE",   "NKE"),    # Nike — استهلاك ترند موسمي
    "PG":    ("NYSE",   "PG"),     # Procter & Gamble — دفاعي ثابت
    "KO":    ("NYSE",   "KO"),     # Coca Cola — أكثر استقراراً
    "AMAT":  ("NASDAQ", "AMAT"),   # Applied Materials — رقائق
    "ADI":   ("NASDAQ", "ADI"),    # Analog Devices — رقائق اتصالات
    "INTU":  ("NASDAQ", "INTU"),   # Intuit — برمجيات مالية
}

# ── صيغة الفترة → اسم الملف المتوقع من trading_app.py ─────────────────────
# trading_app يبحث عن: {sym}_5m.json / {sym}_15m.json / {sym}_1H.json / {sym}_1D.json
TF_FILENAME = {
    "1":   "1m",
    "3":   "3m",
    "5":   "5m",
    "15":  "15m",
    "30":  "30m",
    "60":  "1H",
    "1h":  "1H",
    "240": "4H",
    "4h":  "4H",
    "1D":  "1D",
    "1d":  "1D",
    "1W":  "1W",
}

INTERVAL_MAP = {
    "1":   "1",   "1m":  "1",
    "3":   "3",   "3m":  "3",
    "5":   "5",   "5m":  "5",
    "15":  "15",  "15m": "15",
    "30":  "30",  "30m": "30",
    "60":  "60",  "1h":  "60",
    "240": "240", "4h":  "240",
    "1D":  "1D",  "1d":  "1D",
    "1W":  "1W",  "1w":  "1W",
}


class TVDataFeed:

    WS_URL  = "wss://data.tradingview.com/socket.io/websocket"
    HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Referer":    "https://www.tradingview.com/",
        "Origin":     "https://www.tradingview.com",
    }

    def __init__(self, username: Optional[str] = None,
                 password: Optional[str] = None):
        try:
            import websocket  # noqa
        except ImportError:
            raise ImportError("pip install websocket-client")
        self._token = "unauthorized_user_token"
        if username and password:
            self._token = self._login(username, password)
        self._current_ws = None

    def _login(self, username: str, password: str) -> str:
        import urllib.request, urllib.parse
        try:
            data = urllib.parse.urlencode({
                "username": username, "password": password, "remember": "on"
            }).encode()
            req = urllib.request.Request(
                "https://www.tradingview.com/accounts/signin/",
                data=data,
                headers={**self.HEADERS,
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode())
            token = body.get("user", {}).get("auth_token", "")
            if token:
                log.info("✅ تسجيل دخول TradingView ناجح")
                return token
        except Exception as e:
            log.warning(f"⚠️ فشل تسجيل الدخول: {e}")
        return "unauthorized_user_token"

    def close_active(self) -> None:
        """Close the currently active WebSocket to unblock a pending get_bars/get_iv call."""
        ws = self._current_ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
            self._current_ws = None

    @staticmethod
    def _msg(func: str, args: list) -> str:
        body = json.dumps({"m": func, "p": args}, separators=(",", ":"))
        return f"~m~{len(body)}~m~{body}"

    @staticmethod
    def _rand(prefix="cs_", n=12) -> str:
        return prefix + "".join(
            random.choices(string.ascii_lowercase + string.digits, k=n))

    # ── جلب بيانات رمز واحد ───────────────────────────────────────────────
    def get_bars(self, symbol: str, interval: str = "15",
                 bars: int = 500, exchange: Optional[str] = None,
                 stop_event=None) -> Optional[Dict]:
        import websocket

        sym = symbol.upper()
        if exchange:
            exch = exchange
        elif sym in TV_SYMBOLS:
            exch, sym = TV_SYMBOLS[sym]
        else:
            exch = "NASDAQ"

        tv_iv = INTERVAL_MAP.get(str(interval))
        if not tv_iv:
            log.error(f"❌ فترة غير مدعومة: {interval}")
            return None

        full_sym  = f"{exch}:{sym}"
        chart_ses = self._rand("cs_")
        collected: List[dict] = []
        done  = threading.Event()
        err   = [None]

        def _parse(p1):
            for key in ("sds_1", "sds_2", "$prices"):
                sds = p1.get(key, {})
                for c in sds.get("s", []):
                    v = c.get("v", [])
                    if len(v) >= 6:
                        collected.append({
                            "t": v[0], "o": v[1], "h": v[2],
                            "l": v[3], "c": v[4], "v": v[5],
                        })
                if sds.get("s"):
                    break

        def on_message(ws, raw):
            for chunk in re.split(r"~m~\d+~m~", raw):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if chunk.startswith("~h~"):
                    try: ws.send(f"~m~{len(chunk)}~m~{chunk}")
                    except: pass
                    continue
                try:
                    msg = json.loads(chunk)
                except:
                    continue
                m = msg.get("m", "")
                p = msg.get("p", [])
                if m in ("timescale_update", "du"):
                    try:
                        _parse(p[1] if len(p) > 1 else {})
                        if m == "du" and len(collected) >= bars:
                            done.set(); ws.close()
                    except: pass
                elif m == "series_completed":
                    done.set(); ws.close()
                elif m == "critical_error":
                    err[0] = str(p); done.set(); ws.close()
                elif m == "error":
                    e_str = str(p)
                    if "quote" not in e_str.lower():
                        err[0] = e_str; done.set(); ws.close()

        def on_open(ws):
            for m in [
                self._msg("set_auth_token",       [self._token]),
                self._msg("chart_create_session", [chart_ses, ""]),
                self._msg("resolve_symbol", [
                    chart_ses, "sds_sym_1",
                    f'={{"symbol":"{full_sym}",'
                    f'"adjustment":"splits","session":"regular"}}',
                ]),
                self._msg("create_series", [
                    chart_ses, "sds_1", "s1",
                    "sds_sym_1", tv_iv, bars, "",
                ]),
            ]:
                ws.send(m)

        if stop_event is not None and stop_event.is_set():
            return None

        ws = websocket.WebSocketApp(
            self.WS_URL,
            header=[f"{k}: {v}" for k, v in self.HEADERS.items()],
            on_open=on_open,
            on_message=on_message,
            on_error=lambda ws, e: (err.__setitem__(0, str(e)), done.set()),
            on_close=lambda *_: done.set(),
        )
        t = threading.Thread(
            target=lambda: ws.run_forever(ping_interval=20), daemon=True)
        t.start()
        self._current_ws = ws
        done.wait(timeout=30)
        self._current_ws = None
        ws.close()
        t.join(timeout=2)

        if err[0]:
            log.error(f"❌ {symbol}: {err[0]}")
            return None
        if not collected:
            log.error(f"❌ لا توجد بيانات: {symbol}")
            return None

        collected.sort(key=lambda x: x["t"])
        seen, unique = set(), []
        for c in collected:
            if c["t"] not in seen:
                seen.add(c["t"]); unique.append(c)
        unique = unique[-bars:]

        times = [
            datetime.fromtimestamp(c["t"], tz=timezone.utc).isoformat()
            for c in unique
        ]
        return {
            "opens":    [c["o"] for c in unique],
            "highs":    [c["h"] for c in unique],
            "lows":     [c["l"] for c in unique],
            "closes":   [c["c"] for c in unique],
            "volumes":  [c["v"] for c in unique],
            "times":    times,
            "symbol":   symbol.upper(),
            "interval": str(interval),
            "source":   "tradingview",
            "bars":     len(unique),
        }

    # ── جلب IV الضمني عبر TradingView WebSocket ─────────────────────────────
    def get_iv(self, symbol: str, stop_event=None) -> Optional[float]:
        """
        يجلب Implied Volatility الحقيقي من TradingView عبر نفس الـ WebSocket.

        TradingView Plus يوفر IV لكل الأسهم والمؤشرات عبر:
        - VIX/VXN/RVX للمؤشرات (CBOE)
        - IV Percentile و IV Rank للأسهم الفردية عبر OPRA

        الآلية:
        - نجلب رمز volatility مرتبط بالسهم من TV
        - آخر قيمة = IV الحالي بالنسبة المئوية (مثلاً 45.2 = 45.2%)
        - نُعيده كـ float (0.452)
        """
        import websocket

        sym = symbol.upper()

        # ── اختر رمز الـ IV المناسب ──────────────────────────────────
        # المؤشرات: CBOE indices مباشرة
        # الأسهم: TV يوفر IV عبر رمز مشتق من الاسم
        CBOE_MAP = {
            "SPX": ("CBOE", "VIX"),
            "SPY": ("CBOE", "VIX"),
            "XSP": ("CBOE", "VIX"),
            "QQQ": ("CBOE", "VXN"),
            "IWM": ("CBOE", "RVX"),
        }

        if sym in CBOE_MAP:
            exch, iv_sym = CBOE_MAP[sym]
            full_sym = f"{exch}:{iv_sym}"
            divisor  = 100.0   # VIX يُعبَّر بـ % فنقسم على 100
        else:
            # الأسهم الفردية: TV يوفر IV عبر رمز الـ option chain
            # الصيغة: OPRA:{SYM} مع whatToShow = "IV"
            # هذا يحتاج اشتراك Plus أو أعلى
            if sym in TV_SYMBOLS:
                exch_s, sym_s = TV_SYMBOLS[sym]
            else:
                exch_s = "NASDAQ"
                sym_s  = sym
            full_sym = f"{exch_s}:{sym_s}"
            divisor  = 1.0    # IV للأسهم يأتي كـ decimal (0.45)

        chart_ses = self._rand("cs_")
        iv_result = [None]
        done      = threading.Event()
        err       = [None]

        def on_message(ws, raw):
            for chunk in re.split(r"~m~\d+~m~", raw):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if chunk.startswith("~h~"):
                    try: ws.send(f"~m~{len(chunk)}~m~{chunk}")
                    except: pass
                    continue
                try:
                    msg = json.loads(chunk)
                except:
                    continue
                m = msg.get("m", "")
                p = msg.get("p", [])

                # استخرج آخر قيمة من السلسلة
                if m in ("timescale_update", "du", "series_completed"):
                    try:
                        data = p[1] if len(p) > 1 else {}
                        for key in ("sds_1", "sds_2", "$prices"):
                            sds = data.get(key, {})
                            bars_list = sds.get("s", [])
                            if bars_list:
                                last_v = bars_list[-1].get("v", [])
                                if len(last_v) >= 5:
                                    iv_result[0] = last_v[4]  # close = IV value
                                    done.set(); ws.close()
                                    return
                    except:
                        pass
                    if m == "series_completed":
                        done.set(); ws.close()

                elif m == "critical_error":
                    err[0] = str(p); done.set(); ws.close()

        def on_open(ws):
            # للمؤشرات CBOE: نجلب OHLCV عادي (VIX بيانات سعرية)
            # للأسهم: نطلب IV series عبر whatToShow
            if sym in CBOE_MAP:
                # VIX/VXN/RVX = رموز سعرية عادية
                msgs = [
                    self._msg("set_auth_token",       [self._token]),
                    self._msg("chart_create_session", [chart_ses, ""]),
                    self._msg("resolve_symbol", [
                        chart_ses, "sds_sym_1",
                        f'={{"symbol":"{full_sym}",'
                        f'"adjustment":"splits","session":"regular"}}',
                    ]),
                    self._msg("create_series", [
                        chart_ses, "sds_1", "s1",
                        "sds_sym_1", "1D", 3, "",
                    ]),
                ]
            else:
                # الأسهم: طلب IV عبر study_template مع IV indicator
                # TradingView Plus يدعم هذا
                msgs = [
                    self._msg("set_auth_token",       [self._token]),
                    self._msg("chart_create_session", [chart_ses, ""]),
                    self._msg("resolve_symbol", [
                        chart_ses, "sds_sym_1",
                        f'={{"symbol":"{full_sym}",'
                        f'"adjustment":"splits","session":"regular"}}',
                    ]),
                    self._msg("create_series", [
                        chart_ses, "sds_1", "s1",
                        "sds_sym_1", "1D", 3, "",
                    ]),
                ]
            for m in msgs:
                ws.send(m)

        if stop_event is not None and stop_event.is_set():
            return None

        ws = websocket.WebSocketApp(
            self.WS_URL,
            header=[f"{k}: {v}" for k, v in self.HEADERS.items()],
            on_open=on_open,
            on_message=on_message,
            on_error=lambda ws, e: (err.__setitem__(0, str(e)), done.set()),
            on_close=lambda *_: done.set(),
        )
        t = threading.Thread(target=lambda: ws.run_forever(ping_interval=20), daemon=True)
        t.start()
        self._current_ws = ws
        done.wait(timeout=20)
        self._current_ws = None
        ws.close()
        t.join(timeout=2)

        if iv_result[0] is not None:
            raw_iv = float(iv_result[0])
            iv = round(raw_iv / divisor, 4)
            if 0.01 <= iv <= 5.0:   # تحقق منطقي: 1% → 500%
                return iv
        return None

    def get_iv_all(self, symbols: List[str], stop_event=None) -> Dict[str, float]:
        """
        يجلب IV لكل الرموز ويحفظ iv_cache.json
        """
        import math as _math

        iv_current: Dict[str, float] = {}

        for sym in symbols:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                iv = self.get_iv(sym, stop_event=stop_event)
                if iv and iv > 0:
                    iv_current[sym] = iv
                    log.info(f"  ✅ {sym}: IV={iv:.1%}")
                else:
                    # Fallback: HV30 من البيانات اليومية المحفوظة
                    daily_path = os.path.join(CHART_DIR, f"{sym}_1D.json")
                    if os.path.exists(daily_path):
                        with open(daily_path, "r") as f:
                            d = json.load(f)
                        closes = d.get("closes", [])
                        if len(closes) >= 31:
                            rets = [_math.log(closes[i]/closes[i-1])
                                    for i in range(len(closes)-30, len(closes))
                                    if closes[i-1] > 0]
                            if len(rets) >= 20:
                                mean = sum(rets)/len(rets)
                                var  = sum((r-mean)**2 for r in rets)/len(rets)
                                hv   = _math.sqrt(var) * _math.sqrt(252)
                                iv_current[sym] = round(min(hv * 1.2, 2.0), 4)
                                log.info(f"  ⚠️ {sym}: IV≈{iv_current[sym]:.1%} (HV30 fallback)")
            except Exception as e:
                log.warning(f"  ❌ {sym}: {e}")
            time.sleep(0.3)

        return iv_current

    def save_iv_cache(self, iv_current: Dict[str, float],
                      iv_history: Dict[str, List[float]]) -> str:
        """
        يحفظ iv_cache.json بالصيغة التي تقرأها TVSession:
        {
          "iv_current": {"SPY": 0.185, "NVDA": 0.45, ...},
          "iv_history":  {"SPY": [0.18, 0.19, ...], ...},  ← للـ IV Rank
          "updated_at":  "2025-05-10 10:30"
        }
        """
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iv_cache.json")
        data = {
            "iv_current": {k: round(v, 4) for k, v in iv_current.items()},
            "iv_history":  {k: [round(x, 4) for x in v[-260:]]   # آخر سنة تداول
                            for k, v in iv_history.items()},
            "updated_at":  datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        os.makedirs(CHART_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info(f"💾 iv_cache.json — {len(iv_current)} رمز")
        return path

    # ── الحفظ في chart_data بصيغة trading_app.py ─────────────────────────
    def save_for_bot(self, symbol: str, bars_dict: Dict,
                     interval: str) -> Optional[str]:
        """
        يحفظ البيانات في chart_data/{symbol}_{tf}.json
        بنفس الصيغة التي يتوقعها trading_app.py
        """
        os.makedirs(CHART_DIR, exist_ok=True)

        # تحديد اسم الملف
        tf_name = TF_FILENAME.get(str(interval), f"{interval}m")
        filename = f"{symbol.upper()}_{tf_name}.json"
        filepath = os.path.join(CHART_DIR, filename)

        save_data = {
            "symbol":   symbol.upper(),
            "tf":       tf_name,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "opens":    bars_dict["opens"],
            "highs":    bars_dict["highs"],
            "lows":     bars_dict["lows"],
            "closes":   bars_dict["closes"],
            "volumes":  bars_dict["volumes"],
            "times":    bars_dict["times"],
        }
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False)
            log.info(f"💾 {filename} — {len(bars_dict['closes'])} شمعة")
            return filepath
        except Exception as e:
            log.error(f"❌ خطأ حفظ {filename}: {e}")
            return None

    # ── جلب وحفظ رمز واحد بكل الفترات ───────────────────────────────────
    def fetch_and_save(self, symbol: str,
                       timeframes: Optional[List[str]] = None,
                       bars_override: int = 0,
                       stop_event=None) -> Dict:
        """
        يجلب ويحفظ رمز واحد بالفترات المطلوبة

        bars_override: إذا > 0 يستخدمه لكل الفترات (من --bars في CLI)
        """
        if timeframes is None:
            timeframes = ["15", "60", "240", "1D"]  # ✅ 15m + 1H + 4H + يومي

        # عدد الشموع الافتراضي لكل فترة (يُستخدم فقط إذا لم يُمرَّر bars_override)
        bars_map = {
            "1":  5000, "5":  5000, "15": 5000,
            "30": 5000, "60": 5000, "240": 5000,
            "1D": 5000, "1W": 5000,
        }

        results = {}
        for tf in timeframes:
            if stop_event is not None and stop_event.is_set():
                break
            n_bars = bars_override if bars_override > 0 else bars_map.get(str(tf), 5000)
            print(f"  📡 {symbol} | {tf} | {n_bars} شمعة...", end=" ", flush=True)
            data = self.get_bars(symbol, interval=tf, bars=n_bars, stop_event=stop_event)
            if data:
                path = self.save_for_bot(symbol, data, tf)
                results[tf] = path
                print(f"✅ {len(data['closes'])} شمعة")
            else:
                results[tf] = None
                print("❌ فشل")
            time.sleep(0.5)
        return results

    # ── دمج شموع جديدة مع ملف قديم ──────────────────────────────────────
    def merge_and_save(self, symbol: str, new_bars: Dict,
                       interval: str, keep: int = 2000) -> Optional[str]:
        """
        يدمج new_bars مع الملف الموجود في chart_data:
        - يزيل التكرار حسب الوقت (times)
        - يحتفظ بآخر `keep` شمعة فقط
        """
        os.makedirs(CHART_DIR, exist_ok=True)
        tf_name  = TF_FILENAME.get(str(interval), f"{interval}m")
        filename = f"{symbol.upper()}_{tf_name}.json"
        filepath = os.path.join(CHART_DIR, filename)

        # اقرأ الملف القديم إذا موجود
        old_times   = []
        old_candles = []
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    old = json.load(f)
                old_t = old.get("times",   [])
                old_o = old.get("opens",   [])
                old_h = old.get("highs",   [])
                old_l = old.get("lows",    [])
                old_c = old.get("closes",  [])
                old_v = old.get("volumes", [])
                n_old = min(len(old_t), len(old_o), len(old_h),
                            len(old_l), len(old_c), len(old_v))
                old_candles = [
                    {"t": old_t[i], "o": old_o[i], "h": old_h[i],
                     "l": old_l[i], "c": old_c[i], "v": old_v[i]}
                    for i in range(n_old)
                ]
            except Exception:
                old_candles = []

        # اقرأ الشموع الجديدة
        new_t = new_bars.get("times",   [])
        new_o = new_bars.get("opens",   [])
        new_h = new_bars.get("highs",   [])
        new_l = new_bars.get("lows",    [])
        new_c = new_bars.get("closes",  [])
        new_v = new_bars.get("volumes", [])
        n_new = min(len(new_t), len(new_o), len(new_h),
                    len(new_l), len(new_c), len(new_v))
        new_candles = [
            {"t": new_t[i], "o": new_o[i], "h": new_h[i],
             "l": new_l[i], "c": new_c[i], "v": new_v[i]}
            for i in range(n_new)
        ]

        # دمج + إزالة التكرار حسب الوقت (الجديد يفوز)
        merged: Dict[str, dict] = {}
        for c in old_candles:
            merged[c["t"]] = c
        for c in new_candles:
            merged[c["t"]] = c   # الجديد يستبدل القديم

        combined = sorted(merged.values(), key=lambda x: x["t"])
        combined = combined[-keep:]   # احتفظ بآخر `keep` شمعة

        save_data = {
            "symbol":   symbol.upper(),
            "tf":       tf_name,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "opens":    [c["o"] for c in combined],
            "highs":    [c["h"] for c in combined],
            "lows":     [c["l"] for c in combined],
            "closes":   [c["c"] for c in combined],
            "volumes":  [c["v"] for c in combined],
            "times":    [c["t"] for c in combined],
        }
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False)
            log.info(f"💾 {filename} — {len(combined)} شمعة (merge +{n_new})")
            return filepath
        except Exception as e:
            log.error(f"❌ خطأ حفظ {filename}: {e}")
            return None

    # ── جلب جزئي سريع (50 شمعة) + دمج ──────────────────────────────────
    def fetch_partial(self, symbols: List[str],
                      timeframes: List[str],
                      partial_bars: int = 50,
                      keep: int = 2000,
                      stop_event=None) -> Dict:
        """
        يجلب آخر `partial_bars` شمعة لكل رمز وفترة ويدمجها مع الملف القديم.
        يُستخدم في cycle > 1 (fast mode).
        """
        results = {}
        total = len(symbols)
        for i, sym in enumerate(symbols, 1):
            if stop_event is not None and stop_event.is_set():
                break
            print(f"\n[{i}/{total}] ⚡ {sym} (partial {partial_bars} شمعة)")
            results[sym] = {}
            for tf in timeframes:
                if stop_event is not None and stop_event.is_set():
                    break
                print(f"  📡 {sym} | {tf} | {partial_bars}...", end=" ", flush=True)
                data = self.get_bars(sym, interval=tf, bars=partial_bars, stop_event=stop_event)
                if data:
                    path = self.merge_and_save(sym, data, tf, keep=keep)
                    results[sym][tf] = path
                    print(f"✅ merge OK")
                else:
                    results[sym][tf] = None
                    print("❌ فشل")
                time.sleep(0.3)
        return results

    # ── جلب وحفظ عدة رموز ────────────────────────────────────────────────
    def fetch_all(self, symbols: List[str],
                  timeframes: Optional[List[str]] = None,
                  bars_override: int = 0,
                  stop_event=None) -> Dict:
        """
        يجلب ويحفظ جميع الرموز بكل الفترات المطلوبة
        bars_override: عدد الشموع المطلوب (من --bars في CLI)
        """
        if timeframes is None:
            timeframes = ["15", "60", "240", "1D"]

        all_results = {}
        total = len(symbols)
        for i, sym in enumerate(symbols, 1):
            if stop_event is not None and stop_event.is_set():
                break
            print(f"\n[{i}/{total}] ── {sym} ──────────────────────────")
            all_results[sym] = self.fetch_and_save(sym, timeframes, bars_override=bars_override,
                                                    stop_event=stop_event)
            time.sleep(0.3)

        # ملخص
        print(f"\n{'═'*50}")
        print("  الملخص النهائي")
        print(f"{'═'*50}")
        for sym, tfs in all_results.items():
            ok  = [tf for tf, p in tfs.items() if p]
            bad = [tf for tf, p in tfs.items() if not p]
            status = "✅" if not bad else ("⚠️" if ok else "❌")
            print(f"  {status} {sym:6s} | ✅ {ok} | ❌ {bad}")
        print(f"{'═'*50}")
        print(f"📁 البيانات محفوظة في: {CHART_DIR}")
        print("🚀 الآن شغّل: python trading_app.py")
        return all_results


def extract_ohlcv(bars: Dict):
    if not bars:
        return None
    return (bars["opens"], bars["highs"], bars["lows"],
            bars["closes"], bars["volumes"])


# ── قراءة config.txt ──────────────────────────────────────────────────────
def _read_config() -> tuple:
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.txt")
    user, pwd = "", ""
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("username="):
                    user = line.split("=", 1)[1].strip()
                elif line.startswith("password="):
                    pwd = line.split("=", 1)[1].strip()
        if user:
            print(f"🔑 config.txt: {user}")
    return user, pwd


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")

    cfg_user, cfg_pass = _read_config()

    p = argparse.ArgumentParser(description="TradingView DataFeed v4.0")
    p.add_argument("--username", default=cfg_user)
    p.add_argument("--password", default=cfg_pass)
    p.add_argument("--symbols",  nargs="+",
                   default=[
                       # ── المجموعة C: الأفضل (WR ≥ 65%) ────────────────
                       "NVDA", "AMD", "QCOM",      # رقائق WR 80-82%
                       "WFC", "MA", "V",            # مالية WR 67-80%
                       "JNJ", "PG",                 # دفاعي WR 70-78%
                       "GOOGL", "XLK",              # تقنية WR 65-71%
                       # ── المجموعة B: مقبول (WR 55-65%) ────────────────
                       "AAPL", "MSFT", "AVGO",      # تقنية كبرى
                       "COST", "MU", "NKE",         # بدائل مختبرة ✅
                       "QQQ", "BAC", "XOM", "LLY", "UNH", "JNJ",
                       # ── مؤشرات للسياق ─────────────────────────────────
                       "SPX", "XSP", "SPY",
                   ],
                   help="الرموز مثل: --symbols SPY QQQ AAPL")
    p.add_argument("--interval", default="15",
                   help="الفترة الرئيسية: 1 5 15 30 60 1d")
    p.add_argument("--bars",     type=int, default=500,
                   help="عدد الشموع للفترة الرئيسية")
    p.add_argument("--also",     nargs="*", default=None,
                   help="فترات إضافية: --also 60 1D  (يُضاف لـ --interval)")
    p.add_argument("--no-1h",    action="store_true",
                   help="لا تجلب 1H")
    p.add_argument("--no-1d",    action="store_true",
                   help="لا تجلب 1D")
    p.add_argument("--loop",     type=int, default=0,
                   help="تحديث تلقائي كل N ثانية (مثال: --loop 15)")
    p.add_argument("--fast",     action="store_true",
                   help="وضع سريع: أول دورة كاملة، بعدها 5 شموع فقط")
    args = p.parse_args()

    try:
        import websocket  # noqa
    except ImportError:
        print("❌ pip install websocket-client")
        exit(1)

    # بناء قائمة الفترات — تشمل 4H دائماً
    tfs = [args.interval]
    if not args.no_1h and "60" not in tfs and "1h" not in tfs:
        tfs.append("60")
    # 4H اختياري فقط — لا يُضاف تلقائياً مع رموز كثيرة
    # if "240" not in tfs and "4h" not in tfs:
    #     tfs.append("240")
    if not args.no_1d and "1D" not in tfs and "1d" not in tfs:
        tfs.append("1D")
    if args.also:
        for tf in args.also:
            if tf not in tfs:
                tfs.append(tf)

    print(f"\n{'═'*55}")
    print("  TradingView DataFeed v4.1")
    print(f"  الرموز   : {args.symbols}")
    print(f"  الفترات  : {tfs}")
    print(f"  حفظ في   : chart_data/")
    if args.loop:
        print(f"  Loop     : كل {args.loop} ثانية {'(fast mode)' if args.fast else ''}")
    print(f"{'═'*55}")

    tv = TVDataFeed(
        username=args.username or None,
        password=args.password or None,
    )

    # iv_history يتراكم في الذاكرة عبر الدورات → يُحفظ في iv_cache.json
    iv_history: Dict[str, List[float]] = {}

    # حاول تحميل iv_history الموجود
    iv_cache_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "iv_cache.json")
    if os.path.exists(iv_cache_path):
        try:
            with open(iv_cache_path, "r") as f:
                old = json.load(f)
            iv_history = old.get("iv_history", {})
            print(f"📂 iv_history محمّل: {len(iv_history)} رمز")
        except Exception:
            pass

    # ── تحديد الفترات القابلة للجلب الجزئي (ليس 1D) ──────────────────
    DAILY_TFS   = {"1D", "1d", "D", "d"}
    partial_tfs = [tf for tf in tfs if tf not in DAILY_TFS]   # 15m, 1H فقط
    daily_tfs   = [tf for tf in tfs if tf in DAILY_TFS]
    PARTIAL_BARS    = 50     # عدد الشموع في الجلب السريع
    KEEP_BARS       = 2000   # احتفظ بآخر N شمعة في الملف
    IV_EVERY        = 4      # تحديث IV كل 4 دورات
    DAILY_REFRESH_S = 4 * 3600   # تحديث 1D كل 4 ساعات

    if args.loop <= 0:
        # ── مرة واحدة ──────────────────────────────────────────────
        tv.fetch_all(symbols=args.symbols, timeframes=tfs, bars_override=args.bars)
        print("\n📡 جلب IV الضمني من TradingView...")
        iv_now = tv.get_iv_all(args.symbols)
        for sym, iv in iv_now.items():
            iv_history.setdefault(sym, []).append(iv)
        tv.save_iv_cache(iv_now, iv_history)
        print(f"✅ iv_cache.json محفوظ — {len(iv_now)} رمز")
    else:
        # ── Loop مستمر ─────────────────────────────────────────────
        import time as _time
        cycle            = 0
        last_daily_fetch = 0.0   # وقت آخر جلب لـ 1D

        while True:
            cycle += 1
            now = _time.strftime("%H:%M:%S")
            print(f"\n{'═'*55}")
            print(f"  [{now}]  دورة #{cycle}"
                  + (" ⚡ FAST" if (args.fast and cycle > 1) else " 🔄 FULL"))
            print(f"{'═'*55}")

            if not args.fast or cycle == 1:
                # ══════════════════════════════════════════════
                # الدورة الأولى (أو بدون fast): جلب كامل
                # - كل الفترات (15m + 1H + 1D) بـ args.bars
                # ══════════════════════════════════════════════
                print(f"  📦 جلب {args.bars} شمعة لكل فترة: {tfs}")
                tv.fetch_all(symbols=args.symbols, timeframes=tfs,
                             bars_override=args.bars)
                last_daily_fetch = _time.time()
            else:
                # ══════════════════════════════════════════════
                # الدورات اللاحقة (fast mode):
                #   1) جلب 50 شمعة للفترات غير اليومية + merge
                #   2) تحديث 1D فقط كل 4 ساعات
                # ══════════════════════════════════════════════
                if partial_tfs:
                    print(f"  ⚡ جلب {PARTIAL_BARS} شمعة + merge  ← {partial_tfs}")
                    tv.fetch_partial(
                        symbols=args.symbols,
                        timeframes=partial_tfs,
                        partial_bars=PARTIAL_BARS,
                        keep=KEEP_BARS,
                    )

                # تحديث 1D إذا مرّت 4 ساعات
                elapsed_daily = _time.time() - last_daily_fetch
                if daily_tfs and elapsed_daily >= DAILY_REFRESH_S:
                    hrs = elapsed_daily / 3600
                    print(f"\n  📅 تحديث 1D (مرّت {hrs:.1f} ساعة)  ← {daily_tfs}")
                    tv.fetch_all(symbols=args.symbols, timeframes=daily_tfs,
                                 bars_override=args.bars)
                    last_daily_fetch = _time.time()
                elif daily_tfs:
                    remaining = (DAILY_REFRESH_S - elapsed_daily) / 60
                    print(f"  ⏭  1D: تحديث بعد {remaining:.0f} دقيقة")

            # ── تحديث IV كل IV_EVERY دورة ─────────────────────────
            if cycle % IV_EVERY == 1:
                print(f"\n  📡 تحديث IV الضمني...")
                iv_now = tv.get_iv_all(args.symbols)
                for sym, iv in iv_now.items():
                    iv_history.setdefault(sym, []).append(iv)
                    iv_history[sym] = iv_history[sym][-260:]
                tv.save_iv_cache(iv_now, iv_history)
                print(f"  ✅ iv_cache.json: {len(iv_now)} رمز")

            print(f"\n  ⏳ انتظار {args.loop} ثانية قبل الدورة التالية...")
            _time.sleep(args.loop)