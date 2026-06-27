# -*- coding: utf-8 -*-
"""
smart_analyzer_bridge_x1.py — X1 Decision Bridge
═══════════════════════════════════════════════════════════════════
نسخة نظيفة لإزالة طبقات V12/Adapter القديمة نهائياً.

الهدف:
- تشغيل محلل واحد فقط من analyzer.SmartDayTradingAnalyzer بدون V12 wrappers.
- منع التضارب بين محللين أو طبقات compatibility قديمة.
- بناء شموع 1H بطريقة صحيحة من شموع 15m بدل أخذ كل رابع شمعة.
- تقليل الثقة الوهمية: لا تنفيذ إذا بيانات الأوبشن الحقيقية غير متاحة إلا إذا سمحت بذلك صراحة.
- الحفاظ على فلتر تكلفة العقد $50-$150.
- Trailing stop واضح ومركزي.

مهم:
نسبة 70% لا تُضمن بالكود وحده. هذه النسخة تنظف الربط وتمنع الترقيع، لكن الوصول إلى 70% يحتاج backtest/forward test على نفس الرموز ونفس قواعد التنفيذ.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

from analyzer_x2 import (
    SmartICTAnalyzer as SmartDayTradingAnalyzer,
    MarketState,
    Candle,
    TradeSignal,
    RejectedSignal,
)
try:
    from news_analyzer import get_news_analyzer as _get_news_analyzer
    _NEWS_AVAILABLE = True
except Exception:
    _NEWS_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════
# إعدادات عامة
# ══════════════════════════════════════════════════════════════════

CONTRACT_MIN_COST = 70.0
CONTRACT_MAX_COST = 160.0

def _dynamic_cost_range(stock_price: float):
    """
    نطاق التكلفة المناسب بحسب سعر السهم.
    الأسهم الغالية (COST $990, QQQ $480) لها عقود أغلى طبيعياً.
    """
    p = stock_price or 0
    if   p < 50:    return  15.0,   200.0
    elif p < 150:   return  30.0,   400.0
    elif p < 300:   return  50.0,   800.0
    elif p < 500:   return  80.0,  1500.0
    elif p < 800:   return 100.0,  2500.0
    else:           return 150.0,  4000.0

def _estimate_option_cost(stock_price: float, strike: float, dte: int,
                          is_call: bool = True, assumed_iv: float = 0.30) -> float:
    """
    تقدير سريع لتكلفة العقد (بالدولار) قبل الجلب من IBKR.
    يُستخدم لتخطي الـ strikes البعيدة جداً عن النطاق مبكراً.
    """
    import math as _math
    if stock_price <= 0 or dte <= 0:
        return 0.0
    t = max(dte, 1) / 365.0
    sigma_t = stock_price * assumed_iv * _math.sqrt(t)
    intrinsic = max(0.0, (stock_price - strike) if is_call else (strike - stock_price))
    if sigma_t > 0:
        moneyness = (stock_price - strike) / sigma_t
        atm_factor = _math.exp(-0.5 * moneyness ** 2)
        atm_prem   = stock_price * assumed_iv * _math.sqrt(t) * 0.40
        estimated  = intrinsic + atm_prem * atm_factor
    else:
        estimated = intrinsic
    return round(estimated * 100, 2)   # cost per contract ($)

TRAIL_STEP_PCT  = 0.08   # كل 8% ربح → يتحرك الـstop
TRAIL_LOCK_PCT  = 0.00   # عند أول 8% → stop يصبح سعر الدخول (0% ربح مضمون)
# المنطق: كل خطوة تُثبت الخطوة السابقة
# +8%  → stop = entry (+0%)
# +16% → stop = entry + 8%
# +24% → stop = entry + 16%
# الوقف يتحرك بمقدار خطوة واحدة (8%) خلف أعلى سعر
SCAN_INTERVAL_SEC = 30  # Phase1: reduced from 60s to cut signal detection latency
MAX_BARS_AGE_MIN = 30

# لا نسمح بتقدير Greeks افتراضي للتنفيذ الحقيقي لأنه يضخم الثقة.
# اجعله True فقط للاختبار الورقي أو التشخيص.
ALLOW_ESTIMATED_OPTION_DATA = False  # محجوز للتوافق — غير مستخدم


# ══════════════════════════════════════════════════════════════════
# Diagnostic latency tracker — logging only, zero behavior change
# ══════════════════════════════════════════════════════════════════

class _LatencyTracker:
    """Rolling per-category timing accumulator. Thread-safe. Diagnostic only."""
    _KEEP = 200   # samples retained per category

    def __init__(self):
        self._data: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def record(self, category: str, ms: float) -> None:
        with self._lock:
            if category not in self._data:
                self._data[category] = []
            self._data[category].append(ms)
            if len(self._data[category]) > self._KEEP:
                self._data[category].pop(0)

    def report_lines(self) -> List[str]:
        from datetime import datetime as _dt
        hdr = f"[LATENCY REPORT] {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}"
        bar = "=" * 72
        lines = [
            bar, hdr, bar,
            f"  {'Category':<34}  {'n':>4}  {'best':>8}  {'avg':>8}  {'worst':>8}",
            "-" * 72,
        ]
        def _f(v: float) -> str:
            return f"{v:6.0f}ms" if v < 10_000 else f"{v/1000:5.1f}s  "
        with self._lock:
            for cat, samples in sorted(self._data.items()):
                if not samples:
                    continue
                n = len(samples)
                lines.append(
                    f"  {cat:<34}  {n:>4}  {_f(min(samples))}  "
                    f"{_f(sum(samples)/n)}  {_f(max(samples))}"
                )
        lines.append(bar)
        return lines


_LAT = _LatencyTracker()   # module-level singleton


# ══════════════════════════════════════════════════════════════════
# أدوات حساب نظيفة لا تعتمد على trading_app قدر الإمكان
# ══════════════════════════════════════════════════════════════════

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def calc_ema(values: List[float], period: int) -> Optional[float]:
    values = [_safe_float(v) for v in values]
    if not values:
        return None
    if len(values) < period:
        period = max(1, len(values))
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def calc_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        h = _safe_float(highs[i])
        l = _safe_float(lows[i])
        pc = _safe_float(closes[i - 1])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def calc_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    if len(highs) < period * 2 or len(lows) < period * 2 or len(closes) < period * 2:
        return None
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    dxs = []
    for i in range(period, len(tr) + 1):
        tr_sum = sum(tr[i - period:i])
        if tr_sum <= 0:
            continue
        pdi = 100.0 * sum(plus_dm[i - period:i]) / tr_sum
        mdi = 100.0 * sum(minus_dm[i - period:i]) / tr_sum
        den = pdi + mdi
        if den > 0:
            dxs.append(100.0 * abs(pdi - mdi) / den)
    if len(dxs) < period:
        return None
    return sum(dxs[-period:]) / period


def normalize_bars(raw: dict) -> dict:
    closes = [_safe_float(x) for x in raw.get("closes", [])]
    opens = [_safe_float(x, closes[i] if i < len(closes) else 0.0) for i, x in enumerate(raw.get("opens", closes))]
    highs = [_safe_float(x, closes[i] if i < len(closes) else 0.0) for i, x in enumerate(raw.get("highs", closes))]
    lows = [_safe_float(x, closes[i] if i < len(closes) else 0.0) for i, x in enumerate(raw.get("lows", closes))]
    volumes = [_safe_float(x) for x in raw.get("volumes", [0.0] * len(closes))]
    n = min(len(opens), len(highs), len(lows), len(closes), len(volumes))
    return {
        "opens": opens[-n:],
        "highs": highs[-n:],
        "lows": lows[-n:],
        "closes": closes[-n:],
        "volumes": volumes[-n:],
        "times": raw.get("times", [])[-n:] if isinstance(raw.get("times", []), list) else [],
    }


def build_15m_candles(bars: dict) -> List[Candle]:
    bars = normalize_bars(bars)
    opens, highs, lows, closes, volumes = (bars[k] for k in ("opens", "highs", "lows", "closes", "volumes"))
    n = len(closes)
    now = datetime.now().replace(second=0, microsecond=0)
    candles: List[Candle] = []
    for i in range(n):
        candles.append(Candle(
            open=opens[i],
            high=highs[i],
            low=lows[i],
            close=closes[i],
            volume=volumes[i],
            timestamp=now - timedelta(minutes=(n - i) * 15),
        ))
    return candles



def build_tf_candles(bars: dict, minutes: int) -> List[Candle]:
    bars = normalize_bars(bars)
    opens, highs, lows, closes, volumes = (bars[k] for k in ("opens", "highs", "lows", "closes", "volumes"))
    n = len(closes)
    now = datetime.now().replace(second=0, microsecond=0)
    candles: List[Candle] = []
    for i in range(n):
        candles.append(Candle(
            open=opens[i], high=highs[i], low=lows[i], close=closes[i], volume=volumes[i],
            timestamp=now - timedelta(minutes=(n - i) * minutes),
        ))
    return candles

def resample_15m_to_1h(candles_15m: List[Candle]) -> List[Candle]:
    """تجميع صحيح: open أول شمعة، high أعلى، low أدنى، close آخر، volume مجموع."""
    out: List[Candle] = []
    usable = len(candles_15m) - (len(candles_15m) % 4)
    for i in range(0, usable, 4):
        chunk = candles_15m[i:i + 4]
        if len(chunk) < 4:
            continue
        out.append(Candle(
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
            volume=sum(c.volume for c in chunk),
            timestamp=chunk[-1].timestamp,
        ))
    return out


def _direction_to_app(direction_value: str) -> str:
    d = str(direction_value).upper()
    if d in {"LONG", "BUY", "CALL"}:
        return "CALL"
    if d in {"SHORT", "SELL", "PUT"}:
        return "PUT"
    return d


# patch_execution_engine و ContractCostFilter حُذفا —
# اختيار العقد والتكلفة تتم حصراً في execution.py

def patch_execution_engine(engine) -> None:
    """stub للتوافق — لا يغيّر أي إعدادات."""
    pass


class ContractCostFilter:
    """stub للتوافق — لا يُرشّح شيئاً."""
    @staticmethod
    def is_valid(premium: float) -> bool:
        return True

    @staticmethod
    def check(premium: float) -> tuple:
        return True, "ok"

    @staticmethod
    def filter_candidates(candidates: list) -> list:
        return list(candidates or [])


class TrailingStopManager:
    def __init__(self):
        self._positions: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def register(self, trade_id: str, entry_premium: float, initial_sl: float, initial_tp1: float, initial_tp2: float):
        entry = _safe_float(entry_premium)
        sl = _safe_float(initial_sl)
        tp1 = _safe_float(initial_tp1)
        tp2 = _safe_float(initial_tp2)
        if entry <= 0 or sl <= 0:
            return
        with self._lock:
            self._positions[trade_id] = {
                "entry": entry,
                "current_sl": sl,
                "highest": entry,
                "last_trail_at": entry,
                "tp1": tp1,
                "tp2": tp2,
                "trail_count": 0,
                "phase": 0,
            }
        print(f"[Trail] 📝 {trade_id}: entry=${entry:.2f} SL=${sl:.2f}")

    def update(self, trade_id: str, current_price: float) -> dict:
        current = _safe_float(current_price)
        if current <= 0:
            return {"action": "HOLD"}
        with self._lock:
            pos = self._positions.get(trade_id)
            if not pos:
                return {"action": "HOLD"}
            entry = pos["entry"]
            sl = pos["current_sl"]
            highest = max(pos["highest"], current)
            pos["highest"] = highest
            tp1, tp2 = pos["tp1"], pos["tp2"]

            if tp2 > 0 and current >= tp2:
                return {"action": "CLOSE", "reason": "TP2", "price": current}

            # ── TP1 → SL إلى Breakeven (يُفحص قبل SL) ─────────────
            if pos["phase"] == 0 and tp1 > 0 and current >= tp1:
                new_sl = max(sl, round(entry, 2))
                pos["current_sl"] = new_sl
                pos["phase"] = 1
                pos["last_trail_at"] = current
                return {"action": "MOVE_STOP", "new_sl": new_sl, "reason": "TP1_BREAKEVEN", "count": pos["trail_count"]}

            # ── فحص SL الثابت ──────────────────────────────────────
            if current <= sl:
                reason = "TRAILING_STOP" if pos["trail_count"] > 0 else "STOP_LOSS"
                return {"action": "CLOSE", "reason": reason, "price": current}

            # ── 8% Ratchet — يعمل فقط بعد TP1 (phase >= 1) ─────────
            if pos["phase"] >= 1:
                last_at = pos["last_trail_at"]
                gain_from_last = (highest - last_at) / last_at if last_at > 0 else 0.0
                if gain_from_last >= TRAIL_STEP_PCT:
                    steps_done = pos["trail_count"]
                    new_sl = round(entry * (1 + TRAIL_STEP_PCT * (steps_done + 1)), 2)
                    new_sl = max(new_sl, sl)   # لا يتراجع أبداً
                    if new_sl > sl:
                        pos["current_sl"] = new_sl
                        pos["last_trail_at"] = highest
                        pos["trail_count"] += 1
                        return {
                            "action": "MOVE_STOP",
                            "new_sl": new_sl,
                            "reason": f"TRAIL_8PCT_#{pos['trail_count']}",
                            "highest": highest,
                            "count": pos["trail_count"],
                        }

            return {"action": "HOLD", "current_sl": pos["current_sl"], "highest": highest}

    def remove(self, trade_id: str):
        with self._lock:
            self._positions.pop(trade_id, None)

    def get_sl(self, trade_id: str) -> Optional[float]:
        with self._lock:
            pos = self._positions.get(trade_id)
            return pos["current_sl"] if pos else None


# اسم عام للتوافق مع بقية الملفات — ليس V12.
TrailingStopEngine = TrailingStopManager


class SmartAnalyzerBridge:
    """الجسر النظيف: لا يحتوي على V12 Adapter ولا يشغل أكثر من محلل."""

    def __init__(self, app, exec_engine=None, log_fn=None, allow_estimated_option_data: bool = False):
        self.app = app
        self.engine = exec_engine
        self._log = log_fn or print
        self.analyzer = SmartDayTradingAnalyzer()
        self.trail_mgr = TrailingStopManager()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._prefetch_thread: Optional[threading.Thread] = None
        self._active_signals: Dict[str, str] = {}
        # بيانات الشارت المُجلبة مسبقاً {symbol: dict}
        self._bars_cache: Dict[str, dict] = {}
        self._bars_cache_time: Dict[str, float] = {}
        # Diagnostic latency counters
        self._scan_cycle: int = 0
        self._last_analysis_ts: float = 0.0
        # Scan overlap protection — only one _scan_all() may run at a time
        self._scan_lock = threading.Lock()

    def start(self):
        if self._running:
            return
        self._running = True
        # thread للجلب المسبق للبيانات (bars) — كل 4 دقائق
        self._prefetch_thread = threading.Thread(target=self._prefetch_loop, daemon=True)
        self._prefetch_thread.start()
        # thread المسح الرئيسي — يبدأ بعد 90 ثانية ليعطي الـprefetch وقتاً
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._log("[Bridge] ✅ SmartAnalyzerBridge بدأ (bars-prefetch + scan)")

    def stop(self):
        self._running = False
        self._log("[Bridge] ⏹ SmartAnalyzerBridge توقف")

    def quit(self):
        self.stop()

    def wait(self, ms: int = 3000):
        if self._thread:
            self._thread.join(ms / 1000)

    def _prefetch_loop(self):
        """يجلب بيانات الشارت فقط لجميع رموز المسح في الخلفية."""
        # انتظار أولي حتى يكتمل الاتصال
        for _ in range(30):
            if not self._running:
                return
            if getattr(self.app, "connected", False):
                break
            time.sleep(2)

        while self._running:
            try:
                symbols = list(getattr(self.app, "auto_bot_scan_symbols", []) or [])
                if symbols and getattr(self.app, "connected", False):
                    self._prefetch_bars_for(symbols)
            except Exception as e:
                self._log(f"[Prefetch] ❌ {e}")
            # تجديد كل 4 دقائق
            for _ in range(240):
                if not self._running:
                    return
                time.sleep(1)

    def _options_refresh_loop(self):
        """ملغاة عمداً: الجسر لا يجلب ولا يجهّز عقود أوبشن.

        اختيار العقد وتسعيره وإرساله إلى IBKR يتم حصراً داخل ExecutionEngine.
        بقيت الدالة فقط لتوافق أي استدعاء قديم، ولا تعمل أي شيء.
        """
        return

    def _merge_bars(self, old: dict, new: dict, keep: int = 2000) -> dict:
        """
        يدمج شموع جديدة (new) مع قديمة (old).
        يُزيل التكرار حسب الوقت، يحتفظ بآخر `keep` شمعة.
        الشمعة الجديدة تفوز عند التكرار.
        """
        def _to_candles(d: dict) -> list:
            t = d.get("times",   [])
            o = d.get("opens",   [])
            h = d.get("highs",   [])
            l = d.get("lows",    [])
            c = d.get("closes",  [])
            v = d.get("volumes", [])
            n = min(len(t), len(o), len(h), len(l), len(c), len(v))
            return [{"t": t[i], "o": o[i], "h": h[i],
                     "l": l[i], "c": c[i], "v": v[i]} for i in range(n)]

        merged: dict = {}
        for candle in _to_candles(old):
            merged[candle["t"]] = candle
        for candle in _to_candles(new):
            merged[candle["t"]] = candle   # الجديد يفوز

        combined = sorted(merged.values(), key=lambda x: x["t"])[-keep:]
        return {
            "opens":   [c["o"] for c in combined],
            "highs":   [c["h"] for c in combined],
            "lows":    [c["l"] for c in combined],
            "closes":  [c["c"] for c in combined],
            "volumes": [c["v"] for c in combined],
            "times":   [c["t"] for c in combined],
        }

    def _prefetch_bars_for(self, symbols: List[str]):
        """يجلب bars لكل الرموز واحداً تلو الآخر مع فاصل زمني لتجنب pacing."""
        chart_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")
        for symbol in symbols:
            if not self._running:
                break
            # تخطّ إذا البيانات حديثة (أقل من 4 دقائق)
            last = self._bars_cache_time.get(symbol, 0)
            if time.time() - last < 240:
                continue
            bars = self._fetch_bars_ibkr(symbol, chart_dir)
            if bars:
                # ── ادمج مع JSON التاريخي لنحتفظ بعمق البيانات ──────
                for tf in ("15m", "5m"):
                    json_path = os.path.join(chart_dir, f"{symbol}_{tf}.json")
                    if os.path.exists(json_path):
                        try:
                            with open(json_path, "r", encoding="utf-8") as f:
                                old_raw = json.load(f)
                            old_bars = normalize_bars(old_raw)
                            if old_bars and len(old_bars.get("closes", [])) > len(bars.get("closes", [])):
                                bars = self._merge_bars(old_bars, bars)
                                self._log(
                                    f"  🔀 {symbol}: دمج IBKR ({len(bars.get('closes',[]))} بار) "
                                    f"مع JSON ({len(old_raw.get('closes', old_raw.get('times',[])))} بار)"
                                )
                        except Exception:
                            pass
                        break
                self._bars_cache[symbol] = bars
                self._bars_cache_time[symbol] = time.time()
            # جلب شموع 5m للدخول الدقيق (بعد فاصل لتجنب pacing)
            time.sleep(12)
            self._fetch_5m_bars_ibkr(symbol, chart_dir)
            # فاصل 15 ثانية بين الرموز — IBKR pacing rule: max 6 req/2s
            time.sleep(15)

    def _prefetch_options_for(self, symbol: str, price: float):
        """ملغاة عمداً: لا يوجد أي prefetch للأوبشن داخل الجسر."""
        return

    def _run(self):
        # انتظار 15 ثانية فقط — الـprefetch يعمل بالتوازي
        for _ in range(15):
            if not self._running:
                return
            time.sleep(1)

        while self._running:
            try:
                self._scan_all()
                self._monitor_open_positions()
            except Exception as e:
                self._log(f"[Bridge] ❌ {e}")
            time.sleep(SCAN_INTERVAL_SEC)

    def _scan_all(self):
        # ── Scan Overlap Protection ─────────────────────────────────────
        # Non-blocking: if a previous scan is still running, skip this cycle.
        if not self._scan_lock.acquire(blocking=False):
            self._log("[Bridge] scan skipped — previous scan still running")
            return

        self._scan_cycle += 1
        _cycle_id = self._scan_cycle
        _t_scan_start = time.time()
        self._log(f"[SCAN #{_cycle_id}] start")

        try:
            # Phase1: expire any signal locks older than 300 seconds (5 minutes).
            _now = time.time()
            _stale = [
                s for s, v in list(self._active_signals.items())
                if isinstance(v, dict) and _now - v.get("ts", _now) > 300
            ]
            for _s in _stale:
                self._log(f"[Bridge] {_s}: signal lock >5min — force-expired")
                self._active_signals.pop(_s, None)

            symbols = getattr(self.app, "auto_bot_scan_symbols", []) or getattr(self.app, "scan_symbols", [])
            if not symbols:
                return
            self._log(f"[Bridge] مسح {len(symbols)} رمز...")
            for symbol in list(symbols):
                if not self._running:
                    break
                _t_sym = time.time()   # [DIAG] per-symbol start
                try:
                    self._analyze_symbol(str(symbol).upper().strip())
                except Exception as e:
                    self._log(f"[Bridge] {symbol}: {e}")
                _LAT.record("1b_per_symbol_ms", (time.time() - _t_sym) * 1000)

            # [DIAG] record and report scan totals
            _scan_ms = (time.time() - _t_scan_start) * 1000
            _LAT.record("1a_scan_total_ms", _scan_ms)
            self._log(
                f"[LATENCY] scan_pass #{_cycle_id}: "
                f"total={_scan_ms:.0f}ms | {len(symbols)} symbols"
            )
            if _cycle_id % 10 == 0:
                self._print_latency_report()

        finally:
            _duration_ms = (time.time() - _t_scan_start) * 1000
            self._log(f"[SCAN #{_cycle_id}] end duration={_duration_ms:.0f}ms")
            self._scan_lock.release()

    def _analyze_symbol(self, symbol: str):
        self._last_analysis_ts = time.time()   # [DIAG] anchor for end-to-end latency

        # ── Measurement 2a: market data load ──────────────────────────
        _t_bars = time.time()
        bars = self._load_bars(symbol)
        _bars_ms = (time.time() - _t_bars) * 1000
        _LAT.record("2a_load_bars_ms", _bars_ms)
        if not bars:
            return

        # ── Measurement 2b: market state + candle preparation ─────────
        _t_prep = time.time()
        market = self._build_market_state(symbol, bars)
        candles_15m = build_15m_candles(bars)
        candles_1h  = resample_15m_to_1h(candles_15m)
        candles_5m  = None
        try:
            chart_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")
            p5 = os.path.join(chart_dir, f"{symbol}_5m.json")
            if os.path.exists(p5):
                with open(p5, "r", encoding="utf-8") as f:
                    candles_5m = build_tf_candles(json.load(f), 5)
        except Exception:
            candles_5m = None
        _prep_ms = (time.time() - _t_prep) * 1000
        _LAT.record("2b_market_prep_ms", _prep_ms)
        if not market:
            self._log(f"  ⏭ {symbol}: تعذّر حساب ADX — بيانات غير كافية أو السوق مغلق")
            return
        if len(candles_15m) < 20 or len(candles_1h) < 8:
            self._log(f"  ⏭ {symbol}: شموع غير كافية ({len(candles_15m)} 15m / {len(candles_1h)} 1h)")
            return

        cooldown = self.analyzer.get_cooldown_status(symbol)
        if cooldown.get("active"):
            self._log(f"  ⏭ {symbol}: cooldown ({cooldown.get('remaining_minutes', 0)} دقيقة)")
            return

        # ── Measurement 3: analyzer_x2 analysis time ─────────────────
        _t_analyze = time.time()
        result = self.analyzer.analyze(
            symbol      = symbol,
            market      = market,
            candles_1h  = candles_1h,
            candles_15m = candles_15m,
            option_data = None,
            candles_5m  = candles_5m,
        )
        _analyze_ms = (time.time() - _t_analyze) * 1000
        _LAT.record("3_analyzer_x2_ms", _analyze_ms)
        self._log(
            f"[LATENCY] {symbol}: bars={_bars_ms:.0f}ms "
            f"prep={_prep_ms:.0f}ms analyze={_analyze_ms:.0f}ms"
        )

        if isinstance(result, RejectedSignal):
            critical = [r for r in result.rejection_reasons if r.severity == "CRITICAL"]
            warnings = [r for r in result.rejection_reasons if r.severity == "WARNING"]
            if critical:
                main = critical[0]
                self._log(f"  ↩ {symbol}: [{main.step}] {main.reason} — {main.detail}")
                for r in critical[1:]:
                    self._log(f"       + [{r.step}] {r.reason} — {r.detail}")
            elif warnings:
                main = warnings[0]
                self._log(f"  ↩ {symbol}: (تحذير) {main.reason} — {main.detail}")
            else:
                self._log(f"  ↩ {symbol}: رُفض")
            p = result.partial_analysis
            mq = p.get("market_quality"); mq_d = p.get("market_quality_detail", "")
            htf = p.get("htf_bias"); struct = p.get("structure", {})
            if mq is not None:
                self._log(f"       MQ={mq}/{p.get('min_market_quality')} | {mq_d}")
            if htf:
                self._log(f"       HTF={htf} | {struct.get('method','')} | {str(struct.get('reason',''))[:60]}")
            return

        if not isinstance(result, TradeSignal):
            return

        direction = _direction_to_app(getattr(result.direction, "value", result.direction))

        # ── فلتر الأخبار ────────────────────────────────────────────────
        if _NEWS_AVAILABLE:
            try:
                _news = _get_news_analyzer()
                _allow, _reason = _news.news_filter(symbol, direction.lower())
                if not _allow:
                    self._log(f"  ↩ {symbol}: رُفض بسبب الأخبار — {_reason}")
                    return
                if _reason and _reason != "لا أخبار مؤثرة":
                    self._log(f"  📰 {symbol}: {_reason}")
            except Exception:
                pass

        # ── إرسال الإشارة إلى ExecutionEngine ─────────────────────────
        self._log(
            f"  📊 {symbol}: إشارة {direction} {result.confidence_score:.0f}% "
            f"price=${market.price:.2f} — إرسال للتنفيذ"
        )
        self._execute_signal(result, underlying_price=market.price)

    def _execute_signal(self, signal: TradeSignal, underlying_price: float = 0.0):
        """
        الجسر يُرسل فقط: symbol, direction, confidence, underlying_price
        لا اختيار عقد هنا — ExecutionEngine هو المسؤول الوحيد.
        """
        symbol    = signal.symbol
        direction = _direction_to_app(getattr(signal.direction, "value", signal.direction))

        if symbol in self._active_signals:
            self._log(f"  ⏭ {symbol}: إشارة نشطة بالفعل")
            return

        pct = int(max(0, min(100, getattr(signal, "confidence_score", 0))))

        # خزّن SL/TP للـ execution engine
        if not hasattr(self.app, "_analyzer_signal_cache"):
            self.app._analyzer_signal_cache = {}
        self.app._analyzer_signal_cache[symbol] = {
            "symbol":            symbol,
            "sl":                signal.stop_loss,
            "tp1":               signal.target_1,
            "tp2":               signal.target_2,
            "entry_price":       signal.entry_price,
            "underlying_price":  underlying_price,
            "grade":             getattr(signal.grade, "value", signal.grade),
            "source":            "SmartAnalyzerBridge",
        }

        # منع التكرار — Phase1: store dict with timestamp for expiry tracking
        sig_id = getattr(signal, "signal_id", f"{symbol}-{int(time.time())}")
        self._active_signals[symbol] = {"id": sig_id, "ts": time.time()}

        self._log(
            f"  🚀 {symbol} {direction} {pct}% price=${underlying_price:.2f} "
            f"→ إرسال لـ IBKR"
        )

        if not hasattr(self.app, "_on_analyzer_trade_signal"):
            self._log(f"  ❌ {symbol}: app._on_analyzer_trade_signal غير موجود")
            self._active_signals.pop(symbol, None)
            return

        # [DIAG] Register signal-origin timestamp so execution.py can compute end-to-end latency
        try:
            import execution as _exec_lat_mod
            _exec_lat_mod.register_signal_ts(symbol, self._last_analysis_ts or time.time())
        except Exception:
            pass

        _t_dispatch = time.time()   # [DIAG] how long bridge blocks on execute_signal
        try:
            self.app._on_analyzer_trade_signal(symbol, direction, pct)
            _dispatch_ms = (time.time() - _t_dispatch) * 1000
            _LAT.record("6_bridge_to_exec_return_ms", _dispatch_ms)
            self._log(
                f"[LATENCY] {symbol}: bridge_dispatch+execution={_dispatch_ms:.0f}ms"
            )
            # Phase1: if execution fails silently (no exception here but no trade opened),
            # auto-release the lock after 10 seconds so the symbol can signal again.
            _chk = threading.Timer(10.0, self._verify_signal_result, args=(symbol, sig_id))
            _chk.daemon = True
            _chk.start()
        except Exception as _send_err:
            self._log(f"  ❌ {symbol}: خطأ في الإرسال: {_send_err}")
            self._active_signals.pop(symbol, None)

    def _print_latency_report(self) -> None:
        """Print accumulated worst/avg/best latency stats to the existing log channel."""
        for line in _LAT.report_lines():
            self._log(line)

    def _verify_signal_result(self, symbol: str, sig_id: str):
        """Phase1: called 10s after signal dispatch — releases lock if no position opened."""
        try:
            eng = getattr(self.app, "_exec_engine", None)
            if eng and hasattr(eng, "open_positions"):
                has_trade = any(
                    p.get("symbol") == symbol
                    for p in eng.open_positions.values()
                )
                if not has_trade:
                    current = self._active_signals.get(symbol)
                    stored_id = current.get("id") if isinstance(current, dict) else current
                    if stored_id == sig_id:
                        self._active_signals.pop(symbol, None)
                        self._log(
                            f"[Bridge] {symbol}: no position confirmed after 10s "
                            f"— signal lock auto-released"
                        )
        except Exception as e:
            self._log(f"[Bridge] _verify_signal_result {symbol}: {e}")

    def _monitor_open_positions(self):
        # ① استدع manage_positions من exec_engine أولاً لتحديث current_price من IBKR
        eng = getattr(self.app, "_exec_engine", None)
        if eng and hasattr(eng, "manage_positions"):
            try:
                closed = eng.manage_positions()
                for r in (closed or []):
                    sym = r.get("symbol", "?")
                    outcome = r.get("outcome", "")
                    price = r.get("exit_price", 0)
                    pnl = r.get("pnl_usd", 0)
                    sign = "+" if pnl >= 0 else ""
                    self._log(f"  {'✅' if outcome == 'WIN' else '❌'} {sym} أُغلق [{outcome}] @ ${price:.2f} | PnL={sign}{pnl:.2f}$")
                    self._active_signals.pop(sym, None)
            except Exception as e:
                self._log(f"[Bridge] manage_positions error: {e}")

        # ② طباعة حالة الصفقات المفتوحة
        pm = getattr(self.app, "position_manager", None)
        if not pm or not hasattr(pm, "get_all"):
            return
        positions = pm.get_all() or []
        if positions:
            self._log(f"[Monitor] {len(positions)} صفقة مفتوحة:")
        for pos in positions:
            tid = pos.get("id", "")
            # اقرأ السعر المحدَّث من exec_engine إن أمكن
            current = 0.0
            if eng and hasattr(eng, "open_positions"):
                eng_pos = eng.open_positions.get(tid, {})
                current = _safe_float(eng_pos.get("current_price", 0))
            if current <= 0:
                current = _safe_float(pos.get("current_price", 0))
            if not tid or current <= 0:
                continue
            entry = _safe_float(pos.get("entry_premium", 0))
            sl = _safe_float(pos.get("stop_loss", 0))
            tp1 = _safe_float(pos.get("take_profit", 0))
            contracts = int(pos.get("contracts", 1) or 1)
            pnl_usd = round((current - entry) * contracts * 100, 2) if entry else 0
            pnl_pct = round((current - entry) / entry * 100, 1) if entry else 0
            sign = "+" if pnl_usd >= 0 else ""
            self._log(
                f"    {pos.get('symbol')} | now=${current:.2f} entry=${entry:.2f} "
                f"SL=${sl:.2f} TP=${tp1:.2f} | PnL={sign}{pnl_usd:.2f}$ ({sign}{pnl_pct}%)"
            )
            if self.trail_mgr.get_sl(tid) is None:
                self.trail_mgr.register(tid, entry, sl, tp1, pos.get("take_profit_2", 0))
            action = self.trail_mgr.update(tid, current)
            if action["action"] == "MOVE_STOP":
                new_sl = action["new_sl"]
                if hasattr(pm, "get"):
                    pm_pos = pm.get(tid)
                    if pm_pos:
                        pm_pos["stop_loss"] = new_sl
                self._send_sl_update(tid, new_sl, pos, current)
                self._log(f"  🔺 Trail {pos.get('symbol')}: SL جديد=${new_sl:.2f} ({action.get('reason','')})")
            elif action["action"] == "CLOSE":
                self.trail_mgr.remove(tid)
                self._active_signals.pop(pos.get("symbol", ""), None)
                self._log(f"  🛑 Trail {pos.get('symbol')}: إغلاق @ ${action.get('price', current):.2f} ({action.get('reason','TRAIL')})")

    def _send_sl_update(self, trade_id: str, new_sl: float, pos: dict, current: float):
        try:
            entry = _safe_float(pos.get("entry_premium", 0))
            contracts = int(pos.get("contracts", 1) or 1)
            pnl_usd = round((current - entry) * contracts * 100, 2) if entry else 0
            pnl_pct = round((current - entry) / entry * 100, 1) if entry else 0
            if hasattr(self.app, "ui_updater"):
                self.app.ui_updater.update_trade.emit({
                    "id": trade_id,
                    "current": current,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "stop_loss": new_sl,
                    "tp1": pos.get("take_profit", 0),
                    "phase_lbl": "Trail🔄",
                    "contracts": contracts,
                    "pnl_abs": abs(pnl_usd),
                    "pnl_sign": "+" if pnl_usd >= 0 else "-",
                })
        except Exception as e:
            self._log(f"[Bridge] UI update error: {e}")

    def _load_bars(self, symbol: str) -> Optional[dict]:
        # حدّد وضع التشغيل: Live = متصل + dry_run=False
        _connected = bool(getattr(self.app, "connected", False))
        _dry_run   = True   # افتراضي آمن
        try:
            _eng = getattr(self.app, "_exec_engine", None)
            if _eng is not None:
                _dry_run = bool(_eng.cfg.dry_run)
        except Exception:
            pass
        _live_mode = _connected and not _dry_run

        # حد عمر كاش IBKR: 3 دقائق في Live، 30 دقيقة في Backtest/dry_run
        _max_age_cache = 3.0 if _live_mode else MAX_BARS_AGE_MIN
        # Phase1: tighten JSON staleness in live mode.
        # Live:    15m/5m max 10min  (was 30min — signals on 25-min-old data are invalid)
        # Backtest: unchanged at 30min to preserve historical analysis behaviour.
        _max_age_json  = 10.0 if _live_mode else 30.0
        # Phase1: 1H JSON cap — 60min in live mode, unlimited in backtest.
        _max_age_1h    = 60.0 if _live_mode else float("inf")

        chart_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")

        # ── أولوية 0: bars_cache (IBKR مدموج مع JSON) ─────────────────
        cached = self._bars_cache.get(symbol)
        cached_age = time.time() - self._bars_cache_time.get(symbol, 0)
        if cached and cached_age < (_max_age_cache * 60):
            return cached

        # ── أولوية 1: JSON من tv_datafeed (يقبل حتى 30 دقيقة) ─────────
        for tf in ("15m", "5m"):
            path = os.path.join(chart_dir, f"{symbol}_{tf}.json")
            if os.path.exists(path):
                age_min = (time.time() - os.path.getmtime(path)) / 60.0
                if age_min <= _max_age_json:
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = normalize_bars(json.load(f))
                        if data and len(data.get("closes", [])) >= 50:
                            return data
                    except Exception:
                        pass

        # ── أولوية 1b: 1H JSON — احتياطي مع حد عمر في Live ─────────────
        # Phase1: live mode caps 1H JSON at 60 minutes; backtest has no cap.
        for tf in ("1H", "1h", "60m"):
            path = os.path.join(chart_dir, f"{symbol}_{tf}.json")
            if os.path.exists(path):
                try:
                    age_1h_min = (time.time() - os.path.getmtime(path)) / 60.0
                    if age_1h_min > _max_age_1h:
                        self._log(
                            f"  ⏭ {symbol}: 1H JSON عمره {age_1h_min:.0f}دق "
                            f"(حد={_max_age_1h:.0f}دق) — متجاهَل"
                        )
                        continue
                    with open(path, "r", encoding="utf-8") as f:
                        data = normalize_bars(json.load(f))
                    if data and len(data.get("closes", [])) >= 80:
                        return data
                except Exception:
                    pass

        # ── أولوية 2: _pro_chart الحي (رمز الشارت الحالي فقط) ──────────
        chart = getattr(self.app, "_pro_chart", None)
        if chart and getattr(chart, "_sym", None) == symbol and getattr(chart, "_closes", None):
            return normalize_bars({
                "opens":   getattr(chart, "_opens",   []),
                "highs":   getattr(chart, "_highs",   []),
                "lows":    getattr(chart, "_lows",    []),
                "closes":  getattr(chart, "_closes",  []),
                "volumes": getattr(chart, "_volumes", []),
            })

        # ── لا بيانات متاحة — الـprefetch_loop سيجلبها قريباً ──────────
        if _live_mode:
            self._log(f"  ⏳ {symbol}: بيانات الشارت ستُجلب في الدورة القادمة")
        return None

    def _fetch_5m_bars_ibkr(self, symbol: str, chart_dir: str) -> None:
        """يجلب شموع 5m من IBKR ويحفظها في chart_data/{symbol}_5m.json."""
        try:
            from trading_app import run_in_ib_thread_long
            from ib_insync import Stock, Index

            ib = getattr(self.app, "ib", None)
            if not ib:
                return

            _TRUE_INDICES = {"SPX", "XSP", "NDX", "VIX", "RUT"}
            _ARCA        = {"SPY", "IWM", "GLD", "SLV", "TLT", "XLF", "XLE",
                            "EEM", "EFA", "VXX", "UVXY"}
            _NASDAQ_ETF  = {"QQQ", "TQQQ", "SQQQ", "QID", "PSQ"}
            _NYSE        = {"LLY", "JPM", "BAC", "GS", "MS", "V", "MA", "XOM",
                            "JNJ", "UNH", "WMT", "PG", "KO", "DIS", "F", "UBER",
                            "COST", "SBUX", "IBM", "GE", "BA", "CVX", "MRK"}
            _ISLAND      = {"META", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL",
                            "TSLA", "AMD", "AVGO", "QCOM", "NFLX", "CRM", "ADBE",
                            "INTU", "PANW", "NOW", "MU", "AMAT", "ADI", "PYPL", "IBKR"}

            if symbol in _TRUE_INDICES:
                contract = Index(symbol, "CBOE", "USD")
            else:
                contract = Stock(symbol, "SMART", "USD")
                if symbol in _ARCA:         contract.primaryExch = "ARCA"
                elif symbol in _NASDAQ_ETF: contract.primaryExch = "NASDAQ"
                elif symbol in _NYSE:       contract.primaryExch = "NYSE"
                elif symbol in _ISLAND:     contract.primaryExch = "ISLAND"

            _what = "MIDPOINT" if symbol in _TRUE_INDICES else "TRADES"

            bars_raw = run_in_ib_thread_long(
                ib.reqHistoricalData, contract,
                endDateTime="", durationStr="2 D",
                barSizeSetting="5 mins", whatToShow=_what,
                useRTH=True, formatDate=1, keepUpToDate=False, timeout=60,
            )
            if not bars_raw or len(bars_raw) < 30:
                return

            data = {
                "opens":   [float(b.open)               for b in bars_raw],
                "highs":   [float(b.high)               for b in bars_raw],
                "lows":    [float(b.low)                for b in bars_raw],
                "closes":  [float(b.close)              for b in bars_raw],
                "volumes": [max(0.0, float(b.volume))   for b in bars_raw],
                "times":   [str(b.date)                 for b in bars_raw],
            }
            path = os.path.join(chart_dir, f"{symbol}_5m.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            self._log(f"  ✅ {symbol}: 5m — {len(bars_raw)} شمعة")
        except Exception as e:
            self._log(f"  ⚠ {symbol}: 5m خطأ: {e}")

    def _fetch_bars_ibkr(self, symbol: str, chart_dir: str) -> Optional[dict]:
        """
        يجلب شموع 15m من IBKR مباشرة.
        يُستدعى من _prefetch_bars_for فقط — لا يُستدعى من scan loop.
        """
        try:
            from trading_app import run_in_ib_thread_long
            from ib_insync import Stock, Index

            ib = getattr(self.app, "ib", None)
            if not ib:
                return None

            self._log(f"  📡 {symbol}: جلب بيانات الشارت من IBKR...")

            # بناء contract — مع primaryExch لرفع غموض SMART
            _TRUE_INDICES = {"SPX", "XSP", "NDX", "VIX", "RUT"}
            # ETFs على ARCA (SPY/IWM/GLD...)، QQQ على NASDAQ (ISLAND)
            # أسهم NASDAQ على ISLAND، NYSE على NYSE
            _ARCA   = {"SPY", "IWM", "GLD", "SLV", "TLT", "XLF", "XLE",
                       "EEM", "EFA", "VXX", "UVXY"}
            _NASDAQ_ETF = {"QQQ", "TQQQ", "SQQQ", "QID", "PSQ"}  # NASDAQ ETFs
            _NYSE   = {"LLY", "JPM", "BAC", "GS", "MS", "V", "MA", "XOM",
                       "JNJ", "UNH", "WMT", "PG", "KO", "DIS", "F", "UBER",
                       "COST", "SBUX", "IBM", "GE", "BA", "CVX", "MRK"}
            _ISLAND = {"META", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL",
                       "TSLA", "AMD", "AVGO", "QCOM", "NFLX", "CRM", "ADBE",
                       "INTU", "PANW", "NOW", "MU", "AMAT", "ADI", "PYPL", "IBKR"}

            if symbol in _TRUE_INDICES:
                from ib_insync import Index as _Idx
                contract = _Idx(symbol, "CBOE", "USD")
            else:
                contract = Stock(symbol, "SMART", "USD")
                if symbol in _ARCA:
                    contract.primaryExch = "ARCA"
                elif symbol in _NASDAQ_ETF:
                    contract.primaryExch = "NASDAQ"
                elif symbol in _NYSE:
                    contract.primaryExch = "NYSE"
                elif symbol in _ISLAND:
                    contract.primaryExch = "ISLAND"
                # إذا لم يُطابق أي قائمة — SMART يحدد تلقائياً

            _what = "MIDPOINT" if symbol in _TRUE_INDICES else "TRADES"

            def _req_hist(dur, rth):
                return run_in_ib_thread_long(
                    ib.reqHistoricalData,
                    contract,
                    endDateTime="",
                    durationStr=dur,
                    barSizeSetting="15 mins",
                    whatToShow=_what,
                    useRTH=rth,
                    formatDate=1,
                    keepUpToDate=False,
                    timeout=60,
                )

            # محاولة 1: 5 أيام RTH=True → ~130 شمعة
            bars_raw = _req_hist("5 D", True)

            if not bars_raw or len(bars_raw) < 80:
                # محاولة 2: 7 أيام RTH=False (تشمل pre/post market)
                self._log(f"  🔄 {symbol}: محاولة ثانية 7D RTH=False...")
                time.sleep(20)   # زيادة من 12s إلى 20s لتجنب pacing
                bars_raw = _req_hist("7 D", False)

            if not bars_raw or len(bars_raw) < 80:
                # محاولة 3: 10 أيام RTH=False
                self._log(f"  🔄 {symbol}: محاولة ثالثة 10D RTH=False...")
                time.sleep(20)
                bars_raw = _req_hist("10 D", False)

            if not bars_raw or len(bars_raw) < 80:
                # محاولة 4: 14 أيام RTH=False — آخر محاولة
                self._log(f"  🔄 {symbol}: محاولة رابعة 14D RTH=False...")
                time.sleep(25)
                bars_raw = _req_hist("14 D", False)

            if not bars_raw or len(bars_raw) < 80:
                n = len(bars_raw) if bars_raw else 0
                self._log(f"  ⚠ {symbol}: IBKR أرجع {n} شمعة — غير كافٍ (قد يكون pacing violation)")
                return None

            opens   = [float(b.open)   for b in bars_raw]
            highs   = [float(b.high)   for b in bars_raw]
            lows    = [float(b.low)    for b in bars_raw]
            closes  = [float(b.close)  for b in bars_raw]
            volumes = [max(0.0, float(b.volume)) for b in bars_raw]
            times   = [str(b.date)     for b in bars_raw]

            data = {"opens": opens, "highs": highs, "lows": lows,
                    "closes": closes, "volumes": volumes, "times": times}

            # حفظ في JSON
            try:
                path = os.path.join(chart_dir, f"{symbol}_15m.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                self._log(f"  ✅ {symbol}: حُفظت {len(closes)} شمعة")
            except Exception as e:
                self._log(f"  ⚠ {symbol}: لم يُحفظ: {e}")

            return normalize_bars(data)

        except Exception as e:
            self._log(f"  ❌ {symbol}: خطأ IBKR bars: {e}")
            return None

    def _build_market_state(self, symbol: str, bars: dict) -> Optional[MarketState]:
        try:
            b = normalize_bars(bars)
            closes, highs, lows, volumes = b["closes"], b["highs"], b["lows"], b["volumes"]
            if len(closes) < 50:
                return None
            price = closes[-1]
            ema50 = calc_ema(closes, 50) or price
            ema200 = calc_ema(closes, 200) or price
            atr14 = calc_atr(highs, lows, closes, 14) or price * 0.01
            adx = calc_adx(highs, lows, closes, 14)
            if adx is None:
                self._log(f"  ⏭ {symbol}: تعذر حساب ADX الحقيقي")
                return None
            avg_vol = sum(volumes[-20:]) / 20.0 if len(volumes) >= 20 else max(1.0, volumes[-1] if volumes else 1.0)
            cur_vol = volumes[-1] if volumes else avg_vol
            returns = []
            for i in range(max(1, len(closes) - 20), len(closes)):
                if closes[i - 1] > 0 and closes[i] > 0:
                    returns.append(abs(math.log(closes[i] / closes[i - 1])))
            vix_est = (sum(returns) / len(returns) * math.sqrt(252) * 100.0) if returns else 20.0
            vix = max(10.0, min(45.0, vix_est))
            return MarketState(
                vix=round(vix, 1),
                adx=round(adx, 1),
                volume=cur_vol,
                avg_volume_20=avg_vol,
                ema_50=round(ema50, 2),
                ema_200=round(ema200, 2),
                price=round(price, 2),
                atr_14=round(atr14, 4),
                news_risk="LOW",
            )
        except Exception as e:
            self._log(f"[Bridge] _build_market_state {symbol}: {e}")
            return None

    # _build_option_data حُذفت — الجسر لا يجلب option data من IBKR

    # ─────────────────────────────────────────────────────────────────────
    # _build_from_ui_cache حُذفت — الجسر لا يبني OptionData من cache الواجهة
    def _build_from_ui_cache(self, symbol: str, stock_price: float):
        """stub - removed."""
        return None

class MarketAnalyzerEngine:
    """
    واجهة توافق نظيفة مع trading_app.py.
    الاسم محفوظ حتى لا تحتاج تغييرات كبيرة في الاستيراد - لكن لا يوجد V12 داخله.
    """

    class _Signal:
        def __init__(self):
            self._cbs = []
        def connect(self, fn):
            self._cbs.append(fn)
        def emit(self, *args):
            for cb in list(self._cbs):
                try:
                    cb(*args)
                except Exception:
                    pass

    def __init__(self, ib=None, parent=None):
        self._ib = ib
        self._parent = parent
        self._app = None
        self._exec = None
        self._bridge: Optional[SmartAnalyzerBridge] = None
        self.log_msg = self._Signal()
        self.trade_signal = self._Signal()
        self.profile_updated = self._Signal()

    def set_app(self, app, exec_engine=None):
        self._app = app
        self._exec = exec_engine
        patch_execution_engine(exec_engine)
        self._bridge = SmartAnalyzerBridge(app, exec_engine, log_fn=self.log_msg.emit)

    def start(self):
        if self._bridge:
            self._bridge.start()
        else:
            self.log_msg.emit("⚠ MarketAnalyzerEngine لم يتم ربطه بـ app بعد")

    def stop(self):
        if self._bridge:
            self._bridge.stop()

    def quit(self):
        self.stop()

    def wait(self, ms=3000):
        if self._bridge:
            self._bridge.wait(ms)

    def register_trade(self, trade_id, entry_premium, stop_loss, tp1, tp2):
        if self._bridge:
            self._bridge.trail_mgr.register(trade_id, entry_premium, stop_loss, tp1, tp2)

    def remove_trade(self, trade_id):
        if self._bridge:
            self._bridge.trail_mgr.remove(trade_id)


# أسماء التوافق المسموحة بدون V12
CleanAnalyzerBridge = SmartAnalyzerBridge
CleanMarketAnalyzerEngine = MarketAnalyzerEngine


def integrate_with_trading_app(app, exec_engine=None, log_fn=None) -> SmartAnalyzerBridge:
    patch_execution_engine(exec_engine)
    bridge = SmartAnalyzerBridge(app=app, exec_engine=exec_engine, log_fn=log_fn)
    bridge.start()
    return bridge


def patch_position_manager_trailing(position_manager, trail_mgr: TrailingStopManager):
    if not position_manager or not hasattr(position_manager, "check_exits"):
        return
    original_check = position_manager.check_exits

    def patched_check_exits(trade_id: str, current_premium: float):
        action = trail_mgr.update(trade_id, current_premium)
        if action["action"] == "CLOSE":
            return action.get("reason", "TRAILING_STOP")
        if action["action"] == "MOVE_STOP":
            pos = position_manager.get(trade_id) if hasattr(position_manager, "get") else None
            if pos:
                pos["stop_loss"] = action["new_sl"]
        return original_check(trade_id, current_premium)

    position_manager.check_exits = patched_check_exits
    print("[Bridge] ✅ PositionManager مُحدّث بـ Clean Trailing Stop")


if __name__ == "__main__":
    print("=== SmartAnalyzerBridge self-test ===")
    mgr = TrailingStopManager()
    mgr.register("TEST", 1.00, 0.50, 1.80, 3.00)
    for p in [1.00, 1.08, 1.17, 1.80, 1.95, 0.95]:
        print(p, mgr.update("TEST", p))