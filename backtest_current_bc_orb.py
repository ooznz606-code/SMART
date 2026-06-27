# -*- coding: utf-8 -*-
"""
backtest_current_bc_orb.py
==========================

ضعه داخل مجلد SMART ثم شغله:

    python backtest_current_bc_orb.py

ماذا يفعل:
- Research فقط.
- لا يعدّل أي ملف.
- لا يرسل أوامر.
- يراجع مسار التحليل والتنفيذ بشكل سريع.
- يعمل Backtest للـ ORB الحالي والـ B+C الحالي من ملفات chart_data.
- يطبق قواعد الإنتاج الحالية قدر الإمكان:
  ORB: scan_orb_live + Top-N/day + F2 + F3 + F4
  B+C: scan_symbol + select_daily + _enrich + passes_exec_gate

مخرجاته:
- فحص Syntax/import أساسي.
- مسار الإشارة: BC/ORB → _route_to_execution → _on_analyzer_trade_signal → execute_signal.
- إحصائيات:
  N, W, L, BE, WR%, PF, TotalR, MaxDD, MaxLS, trades/day, trades/week, trades/month.
- جدول آخر الصفقات.
"""

from __future__ import annotations

import os
import sys
import math
import py_compile
import traceback
from collections import defaultdict, Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Brain Gate (Phase 12A) — safe optional import for backtest
try:
    import market_brain_gate as _bt_brain_gate
    _BT_BG_AVAILABLE = True
except ImportError:
    _bt_brain_gate = None
    _BT_BG_AVAILABLE = False

# ── Cost simulation constants ──────────────────────────────────────────────────
# Production (execution.py) does NOT deduct these from P&L.
# Values derived from production constants: CONTRACT_COST $70-$160, stop_loss_pct=0.50.
_COST_COMMISSION_PER_TRADE  = 1.30    # IBKR $0.65/contract/leg × 2 legs (round-trip)
_COST_AVG_PREMIUM_DOLLARS   = 115.0   # midpoint of $70-$160 contract cost ÷ 100 shares
_COST_ONE_R_DOLLARS         = _COST_AVG_PREMIUM_DOLLARS * 0.50  # stop_loss_pct=0.50 → 1R=$57.50
_COST_COMMISSION_R          = _COST_COMMISSION_PER_TRADE / _COST_ONE_R_DOLLARS   # ≈ 0.023R
_COST_SPREAD_PCT            = 0.05    # conservative 5% avg (production max: 15%)
_COST_SPREAD_R              = (_COST_AVG_PREMIUM_DOLLARS * _COST_SPREAD_PCT) / _COST_ONE_R_DOLLARS  # ≈ 0.100R
_COST_SLIPPAGE_PCT          = 0.015   # 1.5% on entry (market order, liquid options)
_COST_SLIPPAGE_R            = (_COST_AVG_PREMIUM_DOLLARS * _COST_SLIPPAGE_PCT) / _COST_ONE_R_DOLLARS  # ≈ 0.030R
_COST_TOTAL_R               = _COST_COMMISSION_R + _COST_SPREAD_R + _COST_SLIPPAGE_R  # ≈ 0.153R


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def sep(title: str = "", ch: str = "=", w: int = 118) -> None:
    print("\n" + ch * w)
    if title:
        print(title)
        print(ch * w)

def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default

def fmt(v: Any, n: int = 2) -> str:
    try:
        return f"{float(v):.{n}f}"
    except Exception:
        return str(v)

def max_drawdown(seq: List[float]) -> float:
    peak = 0.0
    eq = 0.0
    dd = 0.0
    for r in seq:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd

def max_losing_streak(trades: List[Dict]) -> int:
    cur = mx = 0
    for t in trades:
        if t.get("outcome") == "LOSS":
            cur += 1
            mx = max(mx, cur)
        elif t.get("outcome") == "WIN":
            cur = 0
    return mx

def stats(name: str, trades: List[Dict], r_key: str = "R") -> Dict:
    n = len(trades)
    w = sum(1 for t in trades if t.get("outcome") == "WIN")
    l = sum(1 for t in trades if t.get("outcome") == "LOSS")
    be = sum(1 for t in trades if t.get("outcome") == "BE")
    rs = [safe_float(t.get(r_key, t.get("R"))) for t in trades]
    wins = sum(r for r in rs if r > 0)
    losses = abs(sum(r for r in rs if r < 0))
    dates = sorted({str(t.get("date", "")) for t in trades if t.get("date")})
    active_days = len(dates)
    weeks = len({d[:7] + "-W" + str(datetime.strptime(d, "%Y-%m-%d").isocalendar().week) for d in dates}) if dates else 0
    months = len({d[:7] for d in dates}) if dates else 0
    return {
        "name": name,
        "N": n,
        "W": w,
        "L": l,
        "BE": be,
        "WR": (w / max(1, w + l) * 100),
        "PF": (wins / losses) if losses > 0 else (999.0 if wins > 0 else 0.0),
        "TotalR": sum(rs),
        "MaxDD": max_drawdown(rs),
        "MaxLS": max_losing_streak(trades),
        "TradesPerDay": n / max(1, active_days),
        "TradesPerWeek": n / max(1, weeks),
        "TradesPerMonth": n / max(1, months),
        "ActiveDays": active_days,
    }

def print_stats(rows: List[Dict]) -> None:
    print(f"{'Scenario':34} {'N':>5} {'W':>4} {'L':>4} {'BE':>4} {'WR%':>8} {'PF':>8} {'TotalR':>9} {'MaxDD':>8} {'MaxLS':>6} {'/Day':>7} {'/Week':>7} {'/Month':>8}")
    print("-" * 126)
    for r in rows:
        print(
            f"{r['name'][:34]:34} {r['N']:5d} {r['W']:4d} {r['L']:4d} {r['BE']:4d} "
            f"{r['WR']:8.1f} {r['PF']:8.2f} {r['TotalR']:9.2f} {r['MaxDD']:8.2f} {r['MaxLS']:6d} "
            f"{r['TradesPerDay']:7.2f} {r['TradesPerWeek']:7.2f} {r['TradesPerMonth']:8.2f}"
        )

def print_trades(title: str, trades: List[Dict], limit: int = 80) -> None:
    sep(title, "-")
    if not trades:
        print("No trades.")
        return
    print(f"{'Date':10} {'Time':5} {'Sym':6} {'Dir':6} "
          f"{'Entry':>9} {'Stop':>9} {'TP1':>9} {'Out':>5} "
          f"{'R raw':>7} {'R mgd':>7} {'R adj':>7} {'Exit':>10}")
    print("-" * 108)
    for t in trades[-limit:]:
        r_raw = safe_float(t.get('R'))
        r_mgd = safe_float(t.get('R_managed', t.get('R')))
        r_adj = safe_float(t.get('R_adj',     t.get('R')))
        print(
            f"{str(t.get('date','')):10} {str(t.get('time','')):5} "
            f"{str(t.get('symbol','')):6} {str(t.get('direction','')):6} "
            f"{safe_float(t.get('entry')):9.2f} {safe_float(t.get('stop')):9.2f} {safe_float(t.get('tp1')):9.2f} "
            f"{str(t.get('outcome','')):>5} "
            f"{r_raw:7.3f} {r_mgd:7.3f} {r_adj:7.3f} {str(t.get('exit_reason','')):>10}"
        )

def find_bar_index(bars: List[Any], ts: Any) -> Optional[int]:
    for i, b in enumerate(bars):
        if getattr(b, "timestamp", None) == ts:
            return i
    return None

def simulate_stock_trade(
    bars: List[Any],
    start_idx: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    max_hold_bars: int = 40,
    win_r: float = 1.8,
) -> Tuple[str, float]:
    """
    Conservative stock-level simulation.
    LONG: stop if low<=stop, win if high>=tp1.
    SHORT: stop if high>=stop, win if low<=tp1.
    If same bar hits both, stop is counted first.
    """
    direction = direction.upper()
    end = min(len(bars), start_idx + max_hold_bars + 1)
    for b in bars[start_idx + 1:end]:
        if direction in ("LONG", "CALL"):
            if getattr(b, "low") <= stop:
                return "LOSS", -1.0
            if getattr(b, "high") >= tp1:
                return "WIN", float(win_r)
        else:
            if getattr(b, "high") >= stop:
                return "LOSS", -1.0
            if getattr(b, "low") <= tp1:
                return "WIN", float(win_r)
    return "BE", 0.0


def _outcome(r: float) -> str:
    if r >  0.05: return 'WIN'
    if r < -0.05: return 'LOSS'
    return 'BE'


def simulate_trade_managed(
    bars: List[Any],
    start_idx: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    max_hold_bars: int = 40,
    win_r: float = 1.8,
) -> Tuple[str, float, str]:
    """
    Approximates managed exit logic using underlying stock bars:
      BREAKEVEN : raises SL to entry when stock reaches 50% of entry-to-TP1 distance.
      TRAIL     : raises SL to +0.5R when stock reaches 80% of entry-to-TP1 distance.
      MAX_HOLD  : exits at close price of the last bar; R computed from price movement.
    NOTE: approximated from underlying stock bars, not historical option bid/ask.
    Returns (outcome, managed_R, exit_reason).
    """
    direction = direction.upper()
    is_long   = direction in ('LONG', 'CALL')
    risk      = abs(entry - stop)
    if risk <= 0:
        return 'BE', 0.0, 'MAX_HOLD'
    tp_dist = abs(tp1 - entry)

    managed_stop = stop
    be_active    = False
    trail_active = False
    last_close   = entry
    end = min(len(bars), start_idx + max_hold_bars + 1)

    for b in bars[start_idx + 1:end]:
        h          = getattr(b, 'high',  entry)
        lo         = getattr(b, 'low',   entry)
        last_close = getattr(b, 'close', entry)

        if is_long:
            # 1. Stop check first (conservative)
            if lo <= managed_stop:
                mr = round((managed_stop - entry) / risk, 4)
                if be_active or trail_active:
                    reason = 'BREAKEVEN' if abs(mr) < 0.05 else 'TRAIL'
                    if reason == 'BREAKEVEN': mr = 0.0
                else:
                    reason = 'STOP'
                return _outcome(mr), mr, reason
            # 2. TP1
            if h >= tp1:
                return 'WIN', win_r, 'TP1'
            # 3. Update managed stop thresholds (after TP1 to avoid same-bar conflict)
            if not be_active and h >= entry + 0.50 * tp_dist:
                be_active    = True
                managed_stop = max(managed_stop, entry)
            if not trail_active and h >= entry + 0.80 * tp_dist:
                trail_active = True
                managed_stop = max(managed_stop, entry + 0.50 * risk)
        else:  # SHORT / PUT
            # 1. Stop check first
            if h >= managed_stop:
                mr = round((entry - managed_stop) / risk, 4)
                if be_active or trail_active:
                    reason = 'BREAKEVEN' if abs(mr) < 0.05 else 'TRAIL'
                    if reason == 'BREAKEVEN': mr = 0.0
                else:
                    reason = 'STOP'
                return _outcome(mr), mr, reason
            # 2. TP1
            if lo <= tp1:
                return 'WIN', win_r, 'TP1'
            # 3. Update managed stop thresholds
            if not be_active and lo <= entry - 0.50 * tp_dist:
                be_active    = True
                managed_stop = min(managed_stop, entry)
            if not trail_active and lo <= entry - 0.80 * tp_dist:
                trail_active = True
                managed_stop = min(managed_stop, entry - 0.50 * risk)

    # MAX_HOLD: exit at close price of the final bar
    mr = round(((last_close - entry) if is_long else (entry - last_close)) / risk, 4)
    return _outcome(mr), mr, 'MAX_HOLD'


def simulate_trade_with_costs(
    bars: List[Any],
    start_idx: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    max_hold_bars: int = 40,
    win_r: float = 1.8,
) -> Tuple[str, float, float, str, float]:
    """
    Returns (outcome, raw_R, managed_R, exit_reason, cost_adj_R).
      raw_R     : basic TP1=+win_r / STOP=-1.0 / MAX_HOLD=0.0  (no trail management)
      managed_R : TRAIL / BREAKEVEN / close-price MAX_HOLD applied
      cost_adj_R: managed_R minus commission + spread + slippage
    Contract cost rejection ($70-$160) is NOT SIMULATED (requires live option chain).
    NOTE: approximated from underlying stock bars, not historical option bid/ask.
    """
    _, raw_r = simulate_stock_trade(bars, start_idx, direction, entry, stop, tp1, max_hold_bars, win_r)
    outcome, managed_r, exit_reason = simulate_trade_managed(bars, start_idx, direction, entry, stop, tp1, max_hold_bars, win_r)
    cost_adj_r = round(managed_r - _COST_TOTAL_R, 4)
    return outcome, raw_r, managed_r, exit_reason, cost_adj_r


# ─────────────────────────────────────────────────────────────────────────────
# static audit
# ─────────────────────────────────────────────────────────────────────────────

def static_audit() -> None:
    sep("STATIC / ROUTE AUDIT")
    files = [
        "trading_app.py",
        "smart_analyzer_bridge_bc.py",
        "smart_analyzer_bridge_orb.py",
        "execution.py",
        "news_analyzer.py",
    ]
    for f in files:
        if not os.path.exists(f):
            print(f"❌ missing: {f}")
            continue
        try:
            py_compile.compile(f, doraise=True)
            print(f"✅ py_compile: {f}")
        except Exception as e:
            print(f"❌ py_compile: {f}: {e}")

    def has(f: str, s: str) -> bool:
        try:
            return s in open(f, "r", encoding="utf-8", errors="replace").read()
        except Exception:
            return False

    checks = [
        ("BC imports ORB bridge", "smart_analyzer_bridge_bc.py", "from smart_analyzer_bridge_orb import ORBDailyBridge"),
        ("BC has TTL", "smart_analyzer_bridge_bc.py", "SIGNAL_TTL_SEC"),
        ("BC freshness gate", "smart_analyzer_bridge_bc.py", "def _is_signal_fresh"),
        ("BC routes to app pipeline", "smart_analyzer_bridge_bc.py", "_on_analyzer_trade_signal"),
        ("ORB has TTL", "smart_analyzer_bridge_orb.py", "SIGNAL_TTL_SEC"),
        ("ORB freshness gate", "smart_analyzer_bridge_orb.py", "def _is_signal_fresh"),
        ("ORB routes to app pipeline", "smart_analyzer_bridge_orb.py", "_on_analyzer_trade_signal"),
        ("App signal UI state functions", "trading_app.py", "def _signal_log_key"),
        ("App execution callback", "trading_app.py", "def _on_analyzer_trade_signal"),
        ("Execution main entry", "execution.py", "def execute_signal"),
        ("Execution contract selector", "execution.py", "def select_best_option_contract"),
        ("Execution cost floor 70", "execution.py", "CONTRACT_COST_MIN = 70.0"),
        ("Execution cost cap 160", "execution.py", "CONTRACT_COST_MAX = 160.0"),
    ]

    sep("Route checks", "-")
    for label, f, s in checks:
        print(f"{'✅' if has(f, s) else '❌'} {label}")


# ─────────────────────────────────────────────────────────────────────────────
# Brain Gate per-date helpers (backtest only — mirrors production derivation)
# ─────────────────────────────────────────────────────────────────────────────

def _bt_bg_regime_for_date(c15_map: Dict, bias_map: Dict, date: str) -> Optional[str]:
    """Regime as of a specific date: last SPY bar on that date looked up in bias_map."""
    ts = None
    for b in reversed(c15_map.get('SPY', [])):
        if str(b.timestamp.date()) == date:
            ts = b.timestamp
            break
    return bias_map.get(ts) if ts is not None else None


def _bt_bg_breadth_for_date(
    c15_map: Dict, date: str, orb_excluded: frozenset, ema_fn: Any
) -> Optional[float]:
    """% non-excluded, non-index symbols with last close on `date` > EMA20 (as-of-date)."""
    above = total = 0
    for sym, bars in c15_map.items():
        if sym in orb_excluded or sym in ('SPY', 'QQQ') or not bars or len(bars) < 20:
            continue
        bars_to_date = [b for b in bars if str(b.timestamp.date()) <= date]
        if len(bars_to_date) < 20:
            continue
        ema20 = ema_fn([b.close for b in bars_to_date], 20)
        if bars_to_date[-1].close > ema20[-1]:
            above += 1
        total += 1
    return (100.0 * above / total) if total > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# Backtests
# ─────────────────────────────────────────────────────────────────────────────

def backtest_orb() -> Tuple[List[Dict], Dict]:
    import smart_analyzer_bridge_orb as orb
    from analyzer_bc_core import load_symbol_candles, SYMBOLS, CHART_DIR

    c15_map: Dict[str, List[Any]] = {}
    for sym in SYMBOLS:
        try:
            data = load_symbol_candles(sym, CHART_DIR)
            if data and data[0]:
                c15_map[sym] = data[0]
        except Exception:
            pass

    if "SPY" in c15_map and "QQQ" in c15_map:
        bias_map = orb._build_bias(c15_map)
    else:
        bias_map = {}

    # Use production ORB scan list — LLY excluded via _ORB_SYMBOLS (mirrors live _scan())
    orb_scan_syms = [s for s in orb._ORB_SYMBOLS if s not in orb.ORB_EXCLUDED]
    assert "LLY" not in orb_scan_syms, "LLY must not appear in ORB scan symbols"
    print(f"ORB scan symbols ({len(orb_scan_syms)}): {sorted(orb_scan_syms)}")

    by_date: Dict[str, List[Dict]] = defaultdict(list)
    bars_by_sym = {}

    for sym in orb_scan_syms:
        bars = c15_map.get(sym)
        if not bars:
            continue
        bars_by_sym[sym] = bars
        try:
            sigs = orb.scan_orb_live(sym, bars, bias_map)
        except Exception as exc:
            print(f"⚠ ORB scan {sym}: {exc}")
            continue
        for s in sigs:
            by_date[str(s.get("date", ""))].append(s)

    selected: List[Dict] = []
    for d, sigs in by_date.items():
        sigs.sort(key=lambda x: -safe_float(x.get("score")))
        selected.extend(orb._f2_filter(sigs[:orb.TOP_N_DAY]))

    selected.sort(key=lambda s: (str(s.get("date","")), str(s.get("entry_ts",""))))

    # ── Brain Gate (Phase 12A): per-date pre-filter ───────────────────────────
    gate_stats: Dict = {
        'available':       _BT_BG_AVAILABLE,
        'dates_evaluated': 0,
        'dates_allowed':   0,
        'dates_blocked':   0,
        'trades_blocked':  0,
        'reasons':         Counter(),
    }
    if _BT_BG_AVAILABLE:
        by_date_sel: Dict[str, List] = defaultdict(list)
        for s in selected:
            by_date_sel[str(s.get('date', ''))].append(s)
        gated: List[Dict] = []
        for date in sorted(by_date_sel):
            sigs = by_date_sel[date]
            try:
                regime  = _bt_bg_regime_for_date(c15_map, bias_map, date)
                spy_rr  = orb._bg_spy_range_ratio(c15_map.get('SPY', []), date)
                orb_atr = orb._bg_orb_range_atr(c15_map, date)
                breadth = _bt_bg_breadth_for_date(c15_map, date, orb.ORB_EXCLUDED, orb._ema)
                verdict, reason = _bt_brain_gate.evaluate(regime, spy_rr, orb_atr, breadth, date=date)
            except Exception:
                verdict, reason = 'ALLOW_ORB', 'error_safe_allow'
            gate_stats['dates_evaluated'] += 1
            gate_stats['reasons'][reason] += 1
            if verdict == 'BLOCK_ORB':
                gate_stats['dates_blocked'] += 1
                gate_stats['trades_blocked'] += len(sigs)
            else:
                gate_stats['dates_allowed'] += 1
                gated.extend(sigs)
        selected = gated
    # ─────────────────────────────────────────────────────────────────────────

    trades = []
    for s in selected:
        sym = s["symbol"]
        bars = bars_by_sym.get(sym, [])
        idx = find_bar_index(bars, s.get("entry_ts"))
        if idx is None:
            continue
        out, r_raw, r_managed, exit_reason, r_adj = simulate_trade_with_costs(
            bars=bars,
            start_idx=idx,
            direction=s["direction"],
            entry=s["entry_price"],
            stop=s["stop_price"],
            tp1=s["tp1"],
            max_hold_bars=40,
            win_r=1.8,
        )
        ts = s.get("entry_ts")
        trades.append({
            "engine":      "ORB",
            "symbol":      sym,
            "direction":   "CALL" if s["direction"] == "LONG" else "PUT",
            "date":        str(s.get("date","")),
            "time":        ts.strftime("%H:%M") if hasattr(ts, "strftime") else "",
            "entry":       s.get("entry_price", 0),
            "stop":        s.get("stop_price", 0),
            "tp1":         s.get("tp1", 0),
            "score":       s.get("score", 0),
            "outcome":     out,
            "R":           r_raw,
            "R_managed":   r_managed,
            "R_adj":       r_adj,
            "exit_reason": exit_reason,
        })
    return trades, gate_stats

def backtest_bc() -> Tuple[List[Dict], List[Dict]]:
    import smart_analyzer_bridge_bc as bc
    from analyzer_bc_core import scan_symbol, select_daily, load_symbol_candles, SYMBOLS, TOP_N_DAILY, CHART_DIR

    all_sigs: List[Dict] = []
    bars_by_sym: Dict[str, List[Any]] = {}

    for sym in SYMBOLS:
        try:
            data = load_symbol_candles(sym, CHART_DIR)
        except Exception as exc:
            print(f"⚠ load {sym}: {exc}")
            continue
        if not data:
            continue
        c15, c1h = data
        bars_by_sym[sym] = c15
        try:
            sigs = scan_symbol(sym, c15, c1h)
            bc._enrich(sigs, sym, c15)
            all_sigs.extend(sigs)
        except Exception as exc:
            print(f"⚠ BC scan {sym}: {exc}")

    selected = select_daily(all_sigs, TOP_N_DAILY)
    selected.sort(key=lambda s: (str(s.get("date","")), str(s.get("birth_ts",""))))

    all_selected_trades: List[Dict] = []
    live_gate_trades: List[Dict] = []

    for s in selected:
        sym = s.get("symbol")
        bars = bars_by_sym.get(sym, [])
        ts = s.get("birth_ts") or s.get("entry_ts")
        idx = find_bar_index(bars, ts)
        if idx is None:
            continue

        direction_raw = s.get("direction", "")
        entry = safe_float(s.get("entry_price"))
        stop = safe_float(s.get("stop_price"))
        tp1 = safe_float(s.get("tp1"))
        if entry <= 0 or stop <= 0 or tp1 <= 0:
            continue

        out, r_raw, r_managed, exit_reason, r_adj = simulate_trade_with_costs(
            bars=bars,
            start_idx=idx,
            direction=direction_raw,
            entry=entry,
            stop=stop,
            tp1=tp1,
            max_hold_bars=40,
            win_r=1.8,
        )

        row = {
            "engine":      "BC",
            "symbol":      sym,
            "direction":   "CALL" if direction_raw == "LONG" else "PUT",
            "date":        str(s.get("date","")),
            "time":        ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(s.get("birth_time","")),
            "entry":       entry,
            "stop":        stop,
            "tp1":         tp1,
            "score":       s.get("rank_score", 0),
            "outcome":     out,
            "R":           r_raw,
            "R_managed":   r_managed,
            "R_adj":       r_adj,
            "exit_reason": exit_reason,
            "grade":       s.get("grade"),
            "gate":        bc.passes_exec_gate(s)[0],
            "gate_reason": bc.passes_exec_gate(s)[1],
        }
        all_selected_trades.append(row)
        if row["gate"]:
            live_gate_trades.append(row)

    return all_selected_trades, live_gate_trades


def print_cost_report(
    orb_trades: List[Dict],
    bc_all:     List[Dict],
    bc_live:    List[Dict],
) -> None:
    sep("COST REALISM REPORT", "-")
    print("  NOTE: Breakeven/Trailing are approximated from underlying stock bars, not historical option bid/ask.")
    print()

    # ── 1. Simulation status ──────────────────────────────────────────────────
    W = 27
    print(f"  {'Cost item':<30} {'Backtest (this run)':<22} Production (execution.py)")
    print("  " + "-" * 82)
    print(f"  {'Commissions':<30} {'SIMULATED':<22} NOT SIMULATED")
    print(f"  {'Bid/ask spread':<30} {'SIMULATED':<22} NOT SIMULATED (filtered <=15%, cost not deducted)")
    print(f"  {'Slippage':<30} {'SIMULATED':<22} NOT SIMULATED (tracked diagnostically only)")
    print(f"  {'Contract cost filter $70-$160':<30} {'NOT SIMULATED':<22} SIMULATED  (requires live option chain)")
    print(f"  {'TRAIL exit (stock approx.)':<30} {'SIMULATED':<22} SIMULATED  (8% ratchet trailing stop)")
    print(f"  {'BREAKEVEN exit (stock approx.)':<30} {'SIMULATED':<22} SIMULATED  (trail raised to entry)")
    print(f"  {'MAX_HOLD at close price':<30} {'SIMULATED':<22} NOT SIMULATED")
    print()

    # ── 2. Cost assumptions ───────────────────────────────────────────────────
    print("  Assumptions (derived from production execution.py constants):")
    print(f"    Avg option premium     : ${_COST_AVG_PREMIUM_DOLLARS:.2f}   (midpoint of CONTRACT_COST $70-$160)")
    print(f"    1R in dollars          : ${_COST_ONE_R_DOLLARS:.2f}   (stop_loss_pct = 0.50 → 1R = 50% of premium)")
    print(f"    Commission / trade     : ${_COST_COMMISSION_PER_TRADE:.2f}   = {_COST_COMMISSION_R:.4f}R  (IBKR $0.65/contract/leg x 2)")
    print(f"    Bid/ask spread cost    : {_COST_SPREAD_PCT*100:.0f}% of premium    = {_COST_SPREAD_R:.4f}R  (conservative avg; prod max = 15%)")
    print(f"    Slippage (entry)       : {_COST_SLIPPAGE_PCT*100:.1f}% of premium  = {_COST_SLIPPAGE_R:.4f}R  (market order on liquid options)")
    print(f"    Total cost / trade     : ${_COST_TOTAL_R * _COST_ONE_R_DOLLARS:.2f}   = {_COST_TOTAL_R:.4f}R")
    print()

    # ── 3. Old vs adjusted comparison table ───────────────────────────────────
    combined = sorted(
        orb_trades + bc_live,
        key=lambda t: (t.get("date", ""), t.get("time", ""))
    )
    scenarios = [
        ("ORB production rules",      orb_trades),
        ("B+C selected display",      bc_all),
        ("B+C live gate only",        bc_live),
        ("Combined ORB + BC-gate",    combined),
    ]
    print(f"  {'Scenario':<34} {'N':>4}  {'R raw':>8}  {'R mgd':>8}  {'R adj':>8}  {'Mgd-Raw':>8}  {'Adj-Raw':>8}")
    print("  " + "-" * 96)
    for name, trades in scenarios:
        n      = len(trades)
        old_r  = sum(safe_float(t.get("R"))                      for t in trades)
        mgd_r  = sum(safe_float(t.get("R_managed", t.get("R")))  for t in trades)
        adj_r  = sum(safe_float(t.get("R_adj",     t.get("R")))  for t in trades)
        print(f"  {name:<34} {n:4d}  {old_r:8.3f}  {mgd_r:8.3f}  {adj_r:8.3f}  {mgd_r-old_r:8.3f}  {adj_r-old_r:8.3f}")
    print()

    # ── 4. Per-trade R diff (ORB) ─────────────────────────────────────────────
    if orb_trades:
        print(f"  ORB per-trade detail:")
        print(f"  {'Sym':6} {'Date':10} {'Dir':5} {'Out':5} {'Exit':10} {'R raw':>7} {'R mgd':>7} {'R adj':>7}")
        print("  " + "-" * 68)
        for t in orb_trades:
            r_raw = safe_float(t.get("R"))
            r_mgd = safe_float(t.get("R_managed", t.get("R")))
            r_adj = safe_float(t.get("R_adj",     t.get("R")))
            print(
                f"  {str(t.get('symbol','')):6} {str(t.get('date','')):10} "
                f"{str(t.get('direction','')):5} {str(t.get('outcome','')):5} "
                f"{str(t.get('exit_reason','')):10} {r_raw:7.3f} {r_mgd:7.3f} {r_adj:7.3f}"
            )
        print()

    # ── 5. Exit reason breakdown ──────────────────────────────────────────────
    all_trades = orb_trades + bc_all
    exit_counts = Counter(t.get("exit_reason", "UNKNOWN") for t in all_trades)
    print(f"  Exit by reason (ORB + BC all,  N={len(all_trades)}):")
    for reason in ["TP1", "STOP", "BREAKEVEN", "TRAIL", "MAX_HOLD"]:
        cnt = exit_counts.get(reason, 0)
        print(f"    {reason:<12} : {cnt:4d}   (SIMULATED -- approximated from stock bars)")
    print()
    print("  NOTE: Breakeven/Trailing are approximated from underlying stock bars, not historical option bid/ask.")
    print()

    # ── 6. Contract cost rejection ────────────────────────────────────────────
    print("  Contract cost rejected trades  : NOT SIMULATED")
    print("    Production filter : $70 <= premium*100 <= $160  (execution.py CONTRACT_COST_MIN/MAX)")
    print("    Cannot determine option premium from stock bars alone.")
    print("    Trades above reflect stock-level TP/SL simulation only.")


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sep("CURRENT BOT AUDIT + BACKTEST")
    print("Research only. No code changes. No orders.")
    print("CWD:", os.getcwd())

    static_audit()

    sep("BACKTEST")
    try:
        orb_trades, orb_gate_stats = backtest_orb()
    except Exception:
        print("❌ ORB backtest failed:")
        traceback.print_exc()
        orb_trades, orb_gate_stats = [], {'available': False}

    try:
        bc_all, bc_live = backtest_bc()
    except Exception:
        print("❌ B+C backtest failed:")
        traceback.print_exc()
        bc_all, bc_live = [], []

    combined_live = sorted(orb_trades + bc_live, key=lambda t: (t.get("date",""), t.get("time","")))

    sep("OLD R RESULT (no management, no costs)", "-")
    rows_raw = [
        stats("ORB production rules",        orb_trades),
        stats("B+C selected display",        bc_all),
        stats("B+C live gate only",          bc_live),
        stats("Combined live ORB + BC-gate", combined_live),
    ]
    print_stats(rows_raw)

    sep("MANAGED R RESULT (trail/BE/close-price MAX_HOLD -- no cost deduction)", "-")
    rows_mgd = [
        stats("ORB production rules",        orb_trades,    r_key="R_managed"),
        stats("B+C selected display",        bc_all,        r_key="R_managed"),
        stats("B+C live gate only",          bc_live,       r_key="R_managed"),
        stats("Combined live ORB + BC-gate", combined_live, r_key="R_managed"),
    ]
    print_stats(rows_mgd)

    sep("PRODUCTION-LIKE RESULT (managed + commission + spread + slippage)", "-")
    rows_adj = [
        stats("ORB production rules",        orb_trades,    r_key="R_adj"),
        stats("B+C selected display",        bc_all,        r_key="R_adj"),
        stats("B+C live gate only",          bc_live,       r_key="R_adj"),
        stats("Combined live ORB + BC-gate", combined_live, r_key="R_adj"),
    ]
    print_stats(rows_adj)

    print_cost_report(orb_trades, bc_all, bc_live)

    sep("ORB BRAIN GATE SUMMARY", "-")
    if orb_gate_stats.get('available'):
        print(f"  Brain Gate       : ON  (market_brain_gate loaded)")
        print(f"  Dates evaluated  : {orb_gate_stats['dates_evaluated']}")
        print(f"  Dates ALLOWED    : {orb_gate_stats['dates_allowed']}")
        print(f"  Dates BLOCKED    : {orb_gate_stats['dates_blocked']}")
        print(f"  Trades blocked   : {orb_gate_stats['trades_blocked']}")
        if orb_gate_stats.get('reasons'):
            print(f"  Decision reasons:")
            for reason, cnt in orb_gate_stats['reasons'].most_common():
                print(f"    {reason:42} : {cnt}")
    else:
        print("  Brain Gate       : OFF (market_brain_gate not available)")

    print_trades("ORB last trades", orb_trades, limit=80)
    print_trades("B+C live-gate last trades", bc_live, limit=80)

    sep("REJECTION / GATE SUMMARY", "-")
    if bc_all:
        c = Counter(t.get("gate_reason", "PASS") if not t.get("gate") else "PASS" for t in bc_all)
        for k, v in c.most_common():
            print(f"{k:45} : {v}")
    else:
        print("No B+C selected trades found.")

    sep("FINAL NOTE", "=")
    print("إذا كانت النتائج ممتازة هنا لكن البوت لا ينفذ في السوق الحقيقي، فالمشكلة تكون في execution.py/IBKR/اختيار العقد.")
    print("إذا النتائج ضعيفة هنا، فالمشكلة من الاستراتيجية أو اختيار الرموز قبل التنفيذ.")


if __name__ == "__main__":
    main()
