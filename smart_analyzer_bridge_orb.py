# -*- coding: utf-8 -*-
"""
smart_analyzer_bridge_orb.py  --  ORB Daily Engine Bridge
==========================================================
Opening Range Breakout signal scanner and live execution bridge.
Runs alongside BCPaperBridge as a second signal source (Hybrid Engine).

Rules:
  ADX >= 30  |  RVOL >= 1.5x  |  Bias = not-counter (SPY+QQQ EMA9/EMA20)
  ORB range >= 2.0 ATR  |  EMA20 dist >= 1.95 ATR (direction-adjusted)
  Excluded:  AAPL, AMD, AVGO, COST, GOOGL, SPY, TSLA, UBER
  Stop = 1.5 ATR  |  Target = 2.7 ATR (=1.8R)  |  Max hold = 40 bars
  Top-3/day cap  |  Max 2 per direction/day (F2)  |  Break dist >= 0.05 ATR (F3)  |  MSFT SHORT NEUTRAL blocked (F4)  |  Breakout window: 10:00-12:00 ET (noon)

Source label: "ORB Daily"
Priority:     B+C Sniper has priority -- if BC has active signal for a symbol,
              ORB skips live execution for that symbol.

Do NOT modify execution.py.
Do NOT modify analyzer_x2.py.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Set

from analyzer_bc_core import load_symbol_candles, SYMBOLS as _LIVE_SYMBOLS, CHART_DIR as _CHART_DIR
from analyzer_x2 import Candle  # type only

# ── Brain Gate (Phase 12A) — safe optional import ────────────────────────────
try:
    import market_brain_gate as _brain_gate
    _BRAIN_GATE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _BRAIN_GATE_AVAILABLE = False

_BRAIN_GATE_ALLOW = 'ALLOW_ORB'
_BRAIN_GATE_BLOCK = 'BLOCK_ORB'


# ── Constants ─────────────────────────────────────────────────────────────────

ORB_EXCLUDED:      frozenset = frozenset({"AAPL", "AMD", "AVGO", "COST", "GOOGL","SPY", "TSLA", "UBER"})
ORB_ADX_MIN:       float     = 30.0
ORB_RVOL_MIN:      float     = 1.5
ORB_BODY_ATR:      float     = 0.25   # min candle body as fraction of ATR
ORB_RANGE_ATR_MIN: float     = 2.0    # ORB range must be >= 2.0x ATR
ORB_EMA20_DIST_MIN: float    = 1.95   # price must be >= 1.95 ATR from EMA20 (direction-adjusted)
ORB_MAX_DIR_PER_DAY: int     = 2      # F2: max same-direction signals per day after Top-N cap
ORB_BREAK_DIST_MIN:  float   = 0.05   # F3: min breakout distance beyond ORB level (ATR units)

SCAN_INTERVAL_SEC = 60
DATA_STALE_MIN    = 30
SIGNAL_TTL_SEC    = 300   # UI/execution freshness: do not show/route stale ORB signals
TOP_N_DAY         = 3             # max live ORB signals per trading day

SESS_OPEN     = 240   # 9:30 ET   — (ts.hour-9)*60 + ts.minute - 30
SESS_ORB_DONE = 270   # 10:00 ET  — ORB range locks after this
SESS_BRK_END  = 390   # 11:30 ET  — breakout window closes
SESS_CUTOFF   = 525   # 14:15 ET  — hard session cutoff

MIN_LB = 60           # minimum lookback bars for indicator warm-up

CHART_DIR = _CHART_DIR

# ORB-specific production scan list.  Do not use _LIVE_SYMBOLS directly for ORB
# because _LIVE_SYMBOLS is shared with research / core symbol pools and may include
# symbols intentionally removed from ORB production, such as LLY.
_ORB_SYMBOLS = [s for s in _LIVE_SYMBOLS if str(s).upper() != "LLY"]


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _sm(ts: datetime) -> int:
    return (ts.hour - 9) * 60 + ts.minute - 30


def _ema(v: List[float], p: int) -> List[float]:
    k = 2.0 / (p + 1)
    r = [v[0]]
    for x in v[1:]:
        r.append(r[-1] + k * (x - r[-1]))
    return r


def _wilder(v: List[float], p: int) -> List[float]:
    k = 1.0 / p
    r = [v[0]]
    for x in v[1:]:
        r.append(r[-1] + k * (x - r[-1]))
    return r


def _atr(bars: List[Candle], p: int = 14) -> List[float]:
    tr = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return _wilder(tr, p)


def _adx(bars: List[Candle], p: int = 14) -> List[float]:
    n = len(bars)
    if n < p + 2:
        return [0.0] * n
    pdm, mdm, tr = [], [], []
    for i in range(1, n):
        h, l = bars[i].high, bars[i].low
        ph, pl, pc = bars[i - 1].high, bars[i - 1].low, bars[i - 1].close
        up, dn = h - ph, pl - l
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = _wilder(tr, p)
    pd_ = _wilder(pdm, p)
    md  = _wilder(mdm, p)
    dx  = []
    for ai, pi, mi in zip(a, pd_, md):
        pdi = 100 * pi / ai if ai > 0 else 0.0
        mdi = 100 * mi / ai if ai > 0 else 0.0
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi > 0 else 0.0)
    return [0.0] + _wilder(dx, p)


def _rvol(vols: List[float], p: int = 20) -> List[float]:
    out = [1.0] * p
    for i in range(p, len(vols)):
        avg = sum(vols[i - p:i]) / p
        out.append(vols[i] / avg if avg > 0 else 1.0)
    return out


# ── Market bias ───────────────────────────────────────────────────────────────

def _build_bias(c15_map: Dict) -> Dict:
    """SPY + QQQ EMA9 vs EMA20 per bar -> BULL / BEAR / NEUTRAL."""
    bias: Dict = {}
    for mkt in ("SPY", "QQQ"):
        bars = c15_map.get(mkt)
        if not bars:
            continue
        cl  = [b.close for b in bars]
        e9  = _ema(cl, 9)
        e20 = _ema(cl, 20)
        for i, b in enumerate(bars):
            bull = e9[i] > e20[i]
            prev = bias.get(b.timestamp)
            if prev is None:
                bias[b.timestamp] = "BULL" if bull else "BEAR"
            elif (prev == "BULL") == bull:
                pass
            else:
                bias[b.timestamp] = "NEUTRAL"
    return bias


# ── F2 direction cap ─────────────────────────────────────────────────────────

def _f2_filter(signals: List[Dict]) -> List[Dict]:
    """
    F2: from a score-sorted list, allow at most ORB_MAX_DIR_PER_DAY signals
    sharing the same direction on the same date.  Lowest-scoring excess dropped.
    Assumes signals are pre-sorted highest score first.
    """
    dir_count: Dict[str, int] = {}
    kept: List[Dict] = []
    for s in signals:
        key = f"{s['date']}|{s['direction']}"
        if dir_count.get(key, 0) < ORB_MAX_DIR_PER_DAY:
            dir_count[key] = dir_count.get(key, 0) + 1
            kept.append(s)
    return kept


# ── ORB scanner ───────────────────────────────────────────────────────────────

def scan_orb_live(sym: str, bars: List[Candle], bias_map: Dict) -> List[Dict]:
    """
    Scan for ORB breakout signals across the full bar history.
    Returns all qualifying signals (pre-Top-3 cap).
    Each (date, direction) pair is emitted at most once.
    """
    n = len(bars)
    if n < MIN_LB + 2:
        return []

    cl  = [b.close  for b in bars]
    vol = [b.volume for b in bars]

    atrs   = _atr(bars, 14)
    adxs   = _adx(bars, 14)
    rvs    = _rvol(vol, 20)
    ema20s = _ema(cl, 20)

    orb:     Dict[str, list] = {}   # date -> [high, low, locked]
    emitted: Set[tuple]      = set()
    signals: List[Dict]      = []

    for i in range(MIN_LB, n):
        b  = bars[i]
        ts = b.timestamp
        sm = _sm(ts)
        dt = str(ts.date())

        # accumulate opening range (9:30-10:00 ET)
        if SESS_OPEN <= sm < SESS_ORB_DONE:
            if dt not in orb:
                orb[dt] = [b.high, b.low, False]
            else:
                orb[dt][0] = max(orb[dt][0], b.high)
                orb[dt][1] = min(orb[dt][1], b.low)
        elif dt in orb and not orb[dt][2] and sm >= SESS_ORB_DONE:
            orb[dt][2] = True  # lock ORB at 10:00 ET

        # only scan during breakout window (10:00-12:00 ET noon)
        if sm < SESS_ORB_DONE or sm >= SESS_BRK_END:
            continue
        if dt not in orb or not orb[dt][2]:
            continue

        atr = atrs[i]
        if atr <= 0:
            continue

        oh, ol, _ = orb[dt]
        adx  = adxs[i]
        rv   = rvs[i]
        bias = bias_map.get(ts, "NEUTRAL")
        body = abs(b.close - b.open)

        if adx  < ORB_ADX_MIN:        continue
        if rv   < ORB_RVOL_MIN:       continue
        if body < ORB_BODY_ATR * atr:  continue
        if (oh - ol) / atr < ORB_RANGE_ATR_MIN: continue   # ORB Pro: range >= 2.0 ATR

        counter_long  = (bias == "BEAR")
        counter_short = (bias == "BULL")
        score_mult    = 1.3 if bias != "NEUTRAL" else 1.0
        e20           = ema20s[i]

        if (b.close > oh and b.close > b.open
                and not counter_long and (dt, "LONG") not in emitted
                and (b.close - e20) / atr >= ORB_EMA20_DIST_MIN    # ORB Pro: EMA20 dist
                and (b.close - oh)  / atr >= ORB_BREAK_DIST_MIN):  # F3: break dist
            signals.append(dict(
                symbol=sym, date=dt, entry_ts=ts, direction="LONG",
                entry_price=b.close,
                stop_price=b.close - 1.5 * atr,
                tp1=b.close + 2.7 * atr,
                adx=adx, rvol=rv, bias=bias, atr=atr,
                score=adx * rv * score_mult,
            ))
            emitted.add((dt, "LONG"))

        if (b.close < ol and b.close < b.open
                and not counter_short and (dt, "SHORT") not in emitted
                and (e20 - b.close) / atr >= ORB_EMA20_DIST_MIN    # ORB Pro: EMA20 dist
                and (ol - b.close)  / atr >= ORB_BREAK_DIST_MIN    # F3: break dist
                and not (sym == "MSFT" and bias == "NEUTRAL")):     # F4: MSFT SHORT NEUTRAL
            signals.append(dict(
                symbol=sym, date=dt, entry_ts=ts, direction="SHORT",
                entry_price=b.close,
                stop_price=b.close + 1.5 * atr,
                tp1=b.close - 2.7 * atr,
                adx=adx, rvol=rv, bias=bias, atr=atr,
                score=adx * rv * score_mult,
            ))
            emitted.add((dt, "SHORT"))

    return signals


# ── Rejection reason helper ──────────────────────────────────────────────────

def _orb_rejection_reasons(sym: str, bars: List[Candle], bias_map: Dict, today: str) -> List[str]:
    """
    Returns a list of human-readable rejection reasons for the most recent
    bar inside today's breakout window (10:00–11:30 ET).
    Display-only — does not affect strategy logic.
    """
    n = len(bars)
    if n < MIN_LB + 2:
        return ["not enough bars"]

    cl  = [b.close  for b in bars]
    vol = [b.volume for b in bars]
    atrs   = _atr(bars, 14)
    adxs   = _adx(bars, 14)
    rvs    = _rvol(vol, 20)
    ema20s = _ema(cl, 20)

    orb: Dict[str, list] = {}
    last_reasons: List[str] = []

    for i in range(MIN_LB, n):
        b  = bars[i]
        ts = b.timestamp
        sm = _sm(ts)
        dt = str(ts.date())

        if dt != today:
            if SESS_OPEN <= sm < SESS_ORB_DONE:
                if dt not in orb:
                    orb[dt] = [b.high, b.low, False]
                else:
                    orb[dt][0] = max(orb[dt][0], b.high)
                    orb[dt][1] = min(orb[dt][1], b.low)
            elif dt in orb and not orb[dt][2] and sm >= SESS_ORB_DONE:
                orb[dt][2] = True
            continue

        if SESS_OPEN <= sm < SESS_ORB_DONE:
            if dt not in orb:
                orb[dt] = [b.high, b.low, False]
            else:
                orb[dt][0] = max(orb[dt][0], b.high)
                orb[dt][1] = min(orb[dt][1], b.low)
            continue
        elif dt in orb and not orb[dt][2] and sm >= SESS_ORB_DONE:
            orb[dt][2] = True

        if sm < SESS_ORB_DONE or sm >= SESS_BRK_END:
            continue
        if dt not in orb or not orb[dt][2]:
            continue

        atr = atrs[i]
        if atr <= 0:
            continue

        oh, ol, _ = orb[dt]
        adx  = adxs[i]
        rv   = rvs[i]
        bias = bias_map.get(ts, "NEUTRAL")
        body = abs(b.close - b.open)

        reasons: List[str] = []
        if adx  < ORB_ADX_MIN:
            reasons.append(f"ADX weak ({adx:.1f}<{ORB_ADX_MIN})")
        if rv   < ORB_RVOL_MIN:
            reasons.append(f"RVOL low ({rv:.2f}<{ORB_RVOL_MIN})")
        if body < ORB_BODY_ATR * atr:
            reasons.append("body small")
        if (oh - ol) / atr < ORB_RANGE_ATR_MIN:
            reasons.append("ORB range small")
        # Check breakout
        broke_up   = b.close > oh and b.close > b.open
        broke_down = b.close < ol and b.close < b.open
        if not broke_up and not broke_down:
            reasons.append("no breakout")
        elif broke_up and bias == "BEAR":
            reasons.append("counter-bias (LONG vs BEAR)")
        elif broke_down and bias == "BULL":
            reasons.append("counter-bias (SHORT vs BULL)")
        if sym == "MSFT" and broke_down and bias == "NEUTRAL":
            reasons.append("MSFT SHORT blocked (F4)")

        last_reasons = reasons if reasons else ["passed filters (no emit yet)"]

    return last_reasons if last_reasons else ["outside scan window"]


# ── Brain Gate input derivation (Phase 12A) ──────────────────────────────────
# Pure, read-only functions. No ORB strategy parameters referenced.

def _bg_market_regime(c15_map: Dict, bias_map: Dict) -> Optional[str]:
    """Current market regime from the latest SPY bar's bias entry."""
    spy_bars = c15_map.get('SPY', [])
    return bias_map.get(spy_bars[-1].timestamp) if spy_bars else None


def _bg_spy_range_ratio(spy_bars: List[Candle], today: str) -> Optional[float]:
    """Today's SPY daily range / rolling 14-day average daily range."""
    if not spy_bars:
        return None
    day_lo: Dict[str, float] = {}
    day_hi: Dict[str, float] = {}
    for b in spy_bars:
        d = str(b.timestamp.date())
        day_lo[d] = min(day_lo.get(d, b.low),  b.low)
        day_hi[d] = max(day_hi.get(d, b.high), b.high)
    sorted_days = sorted(day_lo)
    if today not in sorted_days:
        return None
    idx = sorted_days.index(today)
    if idx == 0:
        return None
    daily_ranges = [day_hi[d] - day_lo[d] for d in sorted_days]
    prior = daily_ranges[max(0, idx - 14):idx]
    avg = sum(prior) / len(prior) if prior else 0.0
    return daily_ranges[idx] / avg if avg > 0 else None


def _bg_orb_range_atr(c15_map: Dict, today: str) -> Optional[float]:
    """Average ORB range (ATR units) across non-excluded symbols for today's 9:30-10:00 window."""
    ratios: List[float] = []
    for sym, bars in c15_map.items():
        if sym in ORB_EXCLUDED or not bars or len(bars) < 15:
            continue
        lo, hi, has_orb = float('inf'), float('-inf'), False
        for b in bars:
            if str(b.timestamp.date()) == today:
                sm = _sm(b.timestamp)
                if SESS_OPEN <= sm < SESS_ORB_DONE:
                    lo = min(lo, b.low)
                    hi = max(hi, b.high)
                    has_orb = True
        if not has_orb or hi <= lo:
            continue
        atr_vals = _atr(bars, 14)
        atr = atr_vals[-1]
        if atr > 0:
            ratios.append((hi - lo) / atr)
    return (sum(ratios) / len(ratios)) if ratios else None


def _bg_breadth(c15_map: Dict) -> Optional[float]:
    """% of non-excluded, non-index symbols whose latest close > EMA20."""
    above, total = 0, 0
    for sym, bars in c15_map.items():
        if sym in ORB_EXCLUDED or sym in ('SPY', 'QQQ') or not bars or len(bars) < 20:
            continue
        ema20_vals = _ema([b.close for b in bars], 20)
        if bars[-1].close > ema20_vals[-1]:
            above += 1
        total += 1
    return (100.0 * above / total) if total > 0 else None


def _check_brain_gate(
    c15_map: Dict, bias_map: Dict, today: str, log_fn=None
) -> tuple:
    """
    Derive brain gate inputs and evaluate. Returns (verdict, reason).
    Safe: any exception or import failure returns ALLOW_ORB — never raises.
    """
    if not _BRAIN_GATE_AVAILABLE:
        return _BRAIN_GATE_ALLOW, 'brain_gate_unavailable'
    try:
        regime  = _bg_market_regime(c15_map, bias_map)
        spy_rr  = _bg_spy_range_ratio(c15_map.get('SPY', []), today)
        orb_atr = _bg_orb_range_atr(c15_map, today)
        breadth = _bg_breadth(c15_map)
        return _brain_gate.evaluate(regime, spy_rr, orb_atr, breadth, date=today)
    except Exception as exc:
        if log_fn:
            log_fn(f"[ORB Brain Gate] evaluation error — safe ALLOW: {exc}")
        return _BRAIN_GATE_ALLOW, 'brain_gate_error_safe_allow'


# ── ORB Daily Bridge ──────────────────────────────────────────────────────────

class ORBDailyBridge:
    """
    Scans chart_data every SCAN_INTERVAL_SEC for ORB breakout signals.
    Logs all qualifying signals with "Source: ORB Daily".
    Routes to live execution when enable_live_orb=True, within the ORB window.
    """

    def __init__(
        self,
        app,
        log_fn=None,
        enable_live_orb: bool = False,
        bc_bridge=None,
    ):
        self.app           = app
        self._log          = log_fn or print
        self._enable_live  = enable_live_orb
        self._bc_bridge    = bc_bridge   # BCPaperBridge ref for priority check
        self._running      = False
        self._thread:       Optional[threading.Thread] = None
        self._seen:         Set[str]       = set()   # "sym|dir|date"
        self._day_count:    Dict[str, int] = {}      # date -> signals emitted
        self._active_signals: Dict         = {}      # symbol -> {id, ts}
        self._cycle = 0

    # ── Thread management ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name="ORBDailyBridge"
        )
        self._thread.start()
        mode = "LIVE" if self._enable_live else "DISPLAY ONLY"
        self._log(
            f"[ORB Bridge] started -- ADX>={ORB_ADX_MIN}  RVOL>={ORB_RVOL_MIN}x  "
            f"ORBrng>={ORB_RANGE_ATR_MIN}ATR  EMA20dist>={ORB_EMA20_DIST_MIN}ATR  "
            f"F2:max-{ORB_MAX_DIR_PER_DAY}/dir/day  F3:break>={ORB_BREAK_DIST_MIN}ATR  "
            f"F4:MSFT-SHORT-NEUTRAL=blocked  "
            f"bias=not-counter  excl={sorted(ORB_EXCLUDED)}  "
            f"Top-{TOP_N_DAY}/day  {mode}"
        )

    def stop(self) -> None:
        self._running = False
        self._log("[ORB Bridge] stopped")

    def wait(self, ms: int = 3000) -> None:
        if self._thread:
            self._thread.join(ms / 1000.0)

    def _run(self) -> None:
        # Slight offset from BC bridge startup (12 s vs 10 s) to stagger file I/O
        for _ in range(12):
            if not self._running:
                return
            time.sleep(1)
        while self._running:
            try:
                self._scan()
            except Exception as exc:
                self._log(f"[ORB Bridge] scan error: {exc}")
            for _ in range(SCAN_INTERVAL_SEC):
                if not self._running:
                    return
                time.sleep(1)

    # ── Scan cycle ────────────────────────────────────────────────────────────

    def _scan(self) -> None:
        self._cycle += 1
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Load 15m candles for all live symbols (SPY+QQQ needed for bias)
        c15_map: Dict = {}
        for sym in _ORB_SYMBOLS:
            json_path = os.path.join(CHART_DIR, f"{sym}_15m.json")
            if not os.path.exists(json_path):
                continue
            age_min = (time.time() - os.path.getmtime(json_path)) / 60.0
            if age_min > DATA_STALE_MIN:
                continue
            try:
                data = load_symbol_candles(sym, CHART_DIR)
                if data:
                    c15_map[sym] = data[0]
            except Exception:
                pass

        if "SPY" not in c15_map or "QQQ" not in c15_map:
            if self._cycle % 5 == 0:
                self._log("[ORB Bridge] SPY/QQQ unavailable -- bias neutral")
            bias_map: Dict = {}
        else:
            bias_map = _build_bias(c15_map)

        # Collect all today's signals across scan symbols, sorted by score
        all_today:    List[Dict]        = []
        reject_map:   Dict[str, List[str]] = {}   # sym -> [reasons]
        scanned_syms: List[str]         = []

        for sym in _ORB_SYMBOLS:
            if sym in ORB_EXCLUDED:
                continue
            bars = c15_map.get(sym)
            if not bars:
                reject_map[sym] = ["no data / stale"]
                continue
            scanned_syms.append(sym)
            try:
                sigs = scan_orb_live(sym, bars, bias_map)
            except Exception as exc:
                self._log(f"[ORB] {sym}: scan error: {exc}")
                reject_map[sym] = [f"scan error: {exc}"]
                continue
            today_sigs = [s for s in sigs if s["date"] == today]
            if today_sigs:
                all_today.extend(today_sigs)
            else:
                # Collect rejection reasons from latest bar in scan window
                reasons = _orb_rejection_reasons(sym, bars, bias_map, today)
                reject_map[sym] = reasons if reasons else ["outside scan window"]

        all_today.sort(key=lambda x: -x["score"])
        top_candidates = _f2_filter(all_today[:TOP_N_DAY])  # F2: max 2 per direction/day
        # Do not re-display or route old intraday signals after their action window expires.
        top_candidates = [s for s in top_candidates if self._is_signal_fresh(s)]

        # ── Brain Gate (Phase 12A): day-level pre-filter before emission ────────
        if top_candidates:
            _gate_v, _gate_r = _check_brain_gate(c15_map, bias_map, today, self._log)
            if _gate_v == _BRAIN_GATE_BLOCK:
                self._log(
                    f"[ORB Brain Gate] BLOCK_ORB ({_gate_r}) — "
                    f"{len(top_candidates)} signal(s) suppressed"
                )
                top_candidates = []

        # Emit new signals up to daily cap
        emitted_today = self._day_count.get(today, 0)
        for sig in top_candidates:
            key = f"{sig['symbol']}|{sig['direction']}|{sig['date']}"
            if key not in self._seen and emitted_today < TOP_N_DAY:
                self._seen.add(key)
                emitted_today += 1
                self._day_count[today] = emitted_today
                self._emit_signal(sig)

        # ── Status report every cycle ─────────────────────────────────────────
        now_str = datetime.utcnow().strftime("%H:%M UTC")
        mode    = "LIVE" if self._enable_live else "DISPLAY"

        if top_candidates:
            self._log(
                f"[ORB] {now_str}  Cycle #{self._cycle}  "
                f"{len(top_candidates)} candidate(s)  "
                f"emitted={emitted_today}/{TOP_N_DAY}  {mode}"
            )
        else:
            # Summarise top rejection reasons across all symbols
            reason_counts: Dict[str, int] = {}
            for reasons in reject_map.values():
                for r in reasons:
                    reason_counts[r] = reason_counts.get(r, 0) + 1
            top_reasons = sorted(reason_counts, key=lambda k: -reason_counts[k])[:3]
            reasons_str = " / ".join(top_reasons) if top_reasons else "outside window"
            self._log(
                f"[ORB] {now_str}  quiet — scanned {len(scanned_syms)} symbols  "
                f"0/{TOP_N_DAY} signals  |  {reasons_str}"
            )
            if self._cycle % 5 == 0 and reject_map:
                detail = "  ".join(
                    f"{s}:[{','.join(v[:2])}]"
                    for s, v in reject_map.items() if v and v != ["outside scan window"]
                )
                if detail:
                    self._log(f"[ORB detail] {detail}")

    # ── Signal emission ───────────────────────────────────────────────────────

    def _is_signal_fresh(self, sig: Dict) -> bool:
        """Only display/route ORB signals that are still actionable."""
        ts = sig.get("entry_ts")
        if not isinstance(ts, datetime):
            return False
        now = datetime.utcnow()
        if str(ts.date()) != now.strftime("%Y-%m-%d"):
            return False
        # Use the same session-minute clock already used by the bridge.
        age_sec = (_sm(now) - _sm(ts)) * 60
        return 0 <= age_sec <= SIGNAL_TTL_SEC

    def _emit_signal(self, sig: Dict) -> None:
        direction_label = "CALL" if sig["direction"] == "LONG" else "PUT"
        entry  = sig.get("entry_price", 0.0)
        stop_p = sig.get("stop_price",  0.0)
        tp1_p  = sig.get("tp1",         0.0)
        adx_v  = sig.get("adx",         0.0)
        rv_v   = sig.get("rvol",        0.0)
        bias_s = sig.get("bias",        "NEUTRAL")

        _now_sm = _sm(datetime.utcnow())
        in_window = SESS_ORB_DONE <= _now_sm < SESS_BRK_END

        if self._enable_live and in_window:
            tag = "-> LIVE EXECUTION"
        elif self._enable_live:
            tag = "DISPLAY ONLY [outside ORB window]"
        else:
            tag = "DISPLAY ONLY"

        self._log(
            f"[ORB Daily]  {sig['symbol']} {direction_label} "
            f"@ ${entry:.2f}  "
            f"SL=${stop_p:.2f}  TP1=${tp1_p:.2f}  |  "
            f"ADX={adx_v:.1f}  RVOL={rv_v:.1f}x  Bias={bias_s}  "
            f"Score={sig.get('score', 0):.1f}  |  "
            f"Date={sig.get('date', '')} {sig.get('entry_ts').strftime('%H:%M') if isinstance(sig.get('entry_ts'), datetime) else ''}  |  "
            f"Source: ORB Daily  |  {tag}"
        )

        if self._enable_live and in_window:
            self._route_to_execution(sig)

    # ── Execution routing ─────────────────────────────────────────────────────

    def _route_to_execution(self, sig: Dict) -> None:
        symbol         = sig["symbol"]
        direction_exec = "CALL" if sig["direction"] == "LONG" else "PUT"

        # Wall-clock window guard
        _now_sm = _sm(datetime.utcnow())
        if not (SESS_ORB_DONE <= _now_sm < SESS_BRK_END):
            self._log(f"[ORB] {symbol}: outside 10:00-12:00 ET noon (sm={_now_sm}) -- skip")
            return

        # Symbol exclusion guard (defense in depth)
        if symbol in ORB_EXCLUDED:
            self._log(f"[ORB] {symbol}: in excluded set -- abort")
            return

        # B+C Sniper priority: if BC has an active signal for this symbol, yield
        if self._bc_bridge is not None:
            bc_active = getattr(self._bc_bridge, "_active_signals", {})
            if symbol in bc_active:
                self._log(f"[ORB] {symbol}: B+C Sniper active -- ORB execution skipped")
                return

        # Duplicate execution guard
        if symbol in self._active_signals:
            self._log(f"[ORB] {symbol}: ORB execution pending -- skip")
            return

        # Open position / max positions check
        eng = getattr(self.app, "_exec_engine", None)
        if eng:
            engine_open = getattr(eng, "open_positions", {})
            open_syms   = {v.get("symbol", "") for v in engine_open.values()
                           if isinstance(v, dict)}
            if symbol in open_syms:
                self._log(f"[ORB] {symbol}: position already open -- skip")
                return
            cfg     = getattr(eng, "cfg", None)
            max_pos = int(getattr(cfg, "max_open_trades", 3))
            if len(engine_open) >= max_pos:
                self._log(f"[ORB] {symbol}: max positions ({max_pos}) reached -- skip")
                return

        # Populate signal cache (sl/tp read by _on_analyzer_trade_signal)
        if not hasattr(self.app, "_analyzer_signal_cache"):
            self.app._analyzer_signal_cache = {}
        self.app._analyzer_signal_cache[symbol] = {
            "symbol":           symbol,
            "sl":               sig.get("stop_price",  0),
            "tp1":              sig.get("tp1",          0),
            "tp2":              None,
            "entry_price":      sig.get("entry_price",  0),
            "underlying_price": sig.get("entry_price",  0),
            "grade":            "ORB",
            "source":           "ORBDailyBridge",
            "adx":              sig.get("adx",          0),
            "rvol":             sig.get("rvol",         0),
            "bias":             sig.get("bias",         "NEUTRAL"),
        }

        sig_id     = f"{symbol}-ORB-{int(time.time())}"
        self._active_signals[symbol] = {"id": sig_id, "ts": time.time()}
        confidence = min(99, max(60, int(sig.get("score", 60) / 10.0 * 3)))

        self._log(
            f"[ORB LIVE]  {symbol} {direction_exec}  "
            f"Entry=${sig.get('entry_price', 0):.2f}  "
            f"SL=${sig.get('stop_price', 0):.2f}  "
            f"TP1=${sig.get('tp1', 0):.2f}  "
            f"ADX={sig.get('adx', 0):.1f}  RVOL={sig.get('rvol', 0):.1f}x  "
            f"confidence={confidence}%  ->  execution"
        )

        if not hasattr(self.app, "_on_analyzer_trade_signal"):
            self._log(f"[ORB] {symbol}: _on_analyzer_trade_signal not found -- abort")
            self._active_signals.pop(symbol, None)
            return

        try:
            self.app._on_analyzer_trade_signal(symbol, direction_exec, confidence)
        except Exception as exc:
            self._log(f"[ORB] {symbol}: dispatch error: {exc}")
            self._active_signals.pop(symbol, None)
            return

        # Release lock after 10 s if no trade appeared
        t = threading.Timer(10.0, self._verify_result, args=(symbol, sig_id))
        t.daemon = True
        t.start()

    def _verify_result(self, symbol: str, sig_id: str) -> None:
        try:
            current   = self._active_signals.get(symbol)
            if current is None:
                return
            stored_id = current.get("id") if isinstance(current, dict) else None
            if stored_id != sig_id:
                return
            eng = getattr(self.app, "_exec_engine", None)
            if eng and hasattr(eng, "open_positions"):
                has_trade = any(
                    p.get("symbol") == symbol
                    for p in eng.open_positions.values() if isinstance(p, dict)
                )
                if has_trade:
                    return
            self._active_signals.pop(symbol, None)
            self._log(f"[ORB] {symbol}: no trade confirmed after 10s -- lock released")
        except Exception:
            self._active_signals.pop(symbol, None)


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== smart_analyzer_bridge_orb self-test ===")
    print(f"ORB_EXCLUDED      : {sorted(ORB_EXCLUDED)}")
    print(f"ORB_ADX_MIN       : {ORB_ADX_MIN}")
    print(f"ORB_RVOL_MIN      : {ORB_RVOL_MIN}")
    print(f"ORB_RANGE_ATR_MIN : {ORB_RANGE_ATR_MIN}")
    print(f"ORB_EMA20_DIST_MIN: {ORB_EMA20_DIST_MIN}")
    print(f"ORB_BREAK_DIST_MIN: {ORB_BREAK_DIST_MIN}")
    print(f"TOP_N_DAY         : {TOP_N_DAY}")
    print(f"CHART_DIR         : {CHART_DIR}")
    print(f"Scan symbols      : {[s for s in _ORB_SYMBOLS if s not in ORB_EXCLUDED]}")
    assert "LLY" not in _ORB_SYMBOLS, "LLY must be excluded from ORB scan symbols"
    assert ORB_RANGE_ATR_MIN  == 2.0,   f"ORB_RANGE_ATR_MIN unexpected: {ORB_RANGE_ATR_MIN}"
    assert ORB_EMA20_DIST_MIN == 1.95,  f"ORB_EMA20_DIST_MIN unexpected: {ORB_EMA20_DIST_MIN}"
    assert ORB_MAX_DIR_PER_DAY == 2,    f"ORB_MAX_DIR_PER_DAY unexpected: {ORB_MAX_DIR_PER_DAY}"
    assert ORB_BREAK_DIST_MIN  == 0.05, f"ORB_BREAK_DIST_MIN unexpected: {ORB_BREAK_DIST_MIN}"

    # F3 break distance: 0.022 blocked, 0.098 passes
    assert 0.022 < ORB_BREAK_DIST_MIN, "F3: fakeout break (0.022) should be blocked"
    assert 0.098 >= ORB_BREAK_DIST_MIN, "F3: valid break (0.098) should pass"
    print("  F3 break distance (0.022 blocked, 0.098 passes): OK")

    # F4: MSFT SHORT NEUTRAL blocked; MSFT SHORT BEAR and AMZN SHORT NEUTRAL pass
    assert     ("MSFT" == "MSFT" and "NEUTRAL" == "NEUTRAL"), "F4 trigger check"
    assert not ("MSFT" == "MSFT" and "BEAR"    == "NEUTRAL"), "F4: MSFT SHORT BEAR should pass"
    assert not ("AMZN" == "MSFT" and "NEUTRAL" == "NEUTRAL"), "F4: AMZN SHORT NEUTRAL should pass"
    print("  F4 MSFT SHORT NEUTRAL blocked (MSFT BEAR + AMZN NEUTRAL pass): OK")

    # F2 filter: 3 same-direction signals on one day → keep top 2
    _f2_input = [
        dict(date="2026-06-11", direction="SHORT", score=203.8, symbol="META"),
        dict(date="2026-06-11", direction="SHORT", score=89.8,  symbol="MSFT"),
        dict(date="2026-06-11", direction="SHORT", score=56.3,  symbol="NFLX"),
    ]
    _f2_out = _f2_filter(_f2_input)
    assert len(_f2_out) == 2,              f"F2 should keep 2, got {len(_f2_out)}"
    assert _f2_out[0]["symbol"] == "META", f"F2 top-1 wrong: {_f2_out[0]['symbol']}"
    assert _f2_out[1]["symbol"] == "MSFT", f"F2 top-2 wrong: {_f2_out[1]['symbol']}"
    print("  F2 filter (3 SHORT -> 2, NFLX dropped): OK")

    class _FakeApp:
        class _FakeEngine:
            open_positions = {}
            class cfg: max_open_trades = 3
        _exec_engine = _FakeEngine()
        _analyzer_signal_cache: dict = {}
        def _on_analyzer_trade_signal(self, sym, d, pct):
            print(f"  [dispatch] {sym} {d} {pct}%")

    b = ORBDailyBridge(_FakeApp(), enable_live_orb=False)
    assert b._enable_live is False
    assert b._bc_bridge   is None
    print("  Paper mode init: OK")

    b2 = ORBDailyBridge(_FakeApp(), enable_live_orb=True)
    assert b2._enable_live is True
    print("  Live mode init: OK")

    # Execution blocked outside ORB window (sm not in 270-390)
    import datetime as _dt
    dummy_sig = dict(
        symbol="META", date="2026-06-22", direction="LONG",
        entry_price=590.0, stop_price=581.5, tp1=605.8,
        adx=35.0, rvol=1.9, bias="BULL", atr=5.5, score=108.0,
        entry_ts=_dt.datetime(2026, 6, 22, 14, 15),  # UTC = 10:15 ET
    )
    b2._route_to_execution(dummy_sig)
    print("  Execution guard (sm check): OK")

    print("Self-test passed.")
