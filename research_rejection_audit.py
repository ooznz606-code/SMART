# -*- coding: utf-8 -*-
"""
research_rejection_audit.py
===========================

Research only. No live orders. No code changes.

ضع الملف داخل مجلد SMART ثم شغله:
    python research_rejection_audit.py
أو:
    python research_rejection_audit.py 5

الهدف:
- يفسر لماذا ORB و B+C لا يتداولان خلال آخر N أيام.
- لا يختبر الربح فقط، بل يحسب أسباب الرفض.
- يطبع أكثر فلتر يمنع الصفقات.
- يعطيك هل المشكلة في فلتر واحد أو في تصميم المحركات.

لا يعدل أي ملف.
لا يرسل أي أمر.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple
import sys
import os


# ─────────────────────────────────────────────────────────────────────────────
# Project imports
# ─────────────────────────────────────────────────────────────────────────────

try:
    from analyzer_bc_core import (
        CHART_DIR,
        SYMBOLS,
        TOP_N_DAILY,
        load_symbol_candles,
        scan_symbol,
        select_daily,
        ATR_THRESHOLD,
    )
except Exception as e:
    raise SystemExit(f"❌ cannot import analyzer_bc_core: {e}")

try:
    from smart_analyzer_bridge_bc import _enrich, passes_exec_gate
except Exception as e:
    raise SystemExit(f"❌ cannot import smart_analyzer_bridge_bc: {e}")

try:
    import smart_analyzer_bridge_orb as orb
except Exception as e:
    orb = None
    print(f"⚠️ cannot import smart_analyzer_bridge_orb: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def sep(title: str = "", ch: str = "=", w: int = 120) -> None:
    print("\n" + ch * w)
    if title:
        print(title)
        print(ch * w)


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def load_all() -> Tuple[Dict[str, List[Any]], Dict[str, List[Any]]]:
    c15_map, c1h_map = {}, {}
    for sym in SYMBOLS:
        try:
            try:
                data = load_symbol_candles(sym, CHART_DIR, lookback_days=None)
            except TypeError:
                data = load_symbol_candles(sym, CHART_DIR)
        except Exception as e:
            print(f"⚠️ load {sym}: {e}")
            continue

        if not data:
            continue
        c15, c1h = data
        if c15:
            c15_map[sym] = c15
        if c1h:
            c1h_map[sym] = c1h

    return c15_map, c1h_map


def cutoff_date(c15_map: Dict[str, List[Any]], days: int) -> str:
    dates = sorted({str(b.timestamp.date()) for bars in c15_map.values() for b in bars})
    if not dates:
        return "0000-00-00"
    return dates[-days] if len(dates) >= days else dates[0]


def first_bc_reject_reason(sig: Dict[str, Any]) -> str:
    """Same order as live gate; returns first blocking reason."""
    sym = sig.get("symbol", "")
    if sym == "AAPL":
        return "blocked_symbol_AAPL"

    rank = safe_float(sig.get("rank_score"), 0.0)
    if rank < 75.0:
        return "rank_score<75"

    if sig.get("rvi_bucket") != "High":
        return "rvi_not_high"

    if sig.get("regime") != "Trend":
        return "regime_not_trend"

    adx = safe_float(sig.get("adx"), 0.0)
    if adx < 40.0:
        return "adx<40"

    off = safe_int(sig.get("entry_offset"), 999)
    if off > 4:
        return "entry_offset>4"

    atr = safe_float(sig.get("atr_pct"), 999.0)
    if atr > ATR_THRESHOLD:
        return f"atr_pct>{ATR_THRESHOLD}"

    sm = safe_int(sig.get("session_min"), 999)
    if sm >= 525:
        return "after_14:15_ET"

    return "PASS"


def print_counter(title: str, counter: Counter, total: int) -> None:
    sep(title, "-")
    if total <= 0:
        print("No items.")
        return
    print(f"{'Reason':30} {'Count':>7} {'Pct':>8}")
    print("-" * 52)
    for reason, count in counter.most_common():
        print(f"{reason:30} {count:7d} {count/total*100:7.1f}%")


def print_sample_bc(title: str, sigs: List[Dict[str, Any]], limit: int = 30) -> None:
    sep(title, "-")
    if not sigs:
        print("No samples.")
        return
    print(f"{'Date':10} {'Time':5} {'Sym':5} {'Dir':6} {'Score':>6} {'ADX':>6} {'RVI':>6} {'Bucket':>7} {'ATR%':>7} {'Off':>4} {'Reason':>18}")
    print("-" * 110)
    for s in sigs[:limit]:
        print(
            f"{str(s.get('date','')):10} {str(s.get('birth_time','')):5} {str(s.get('symbol','')):5} {str(s.get('direction','')):6} "
            f"{safe_float(s.get('rank_score')):6.1f} {safe_float(s.get('adx')):6.1f} {safe_float(s.get('rvi')):6.3f} "
            f"{str(s.get('rvi_bucket','')):>7} {safe_float(s.get('atr_pct')):7.3f} {safe_int(s.get('entry_offset')):4d} {first_bc_reject_reason(s):>18}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B+C audit
# ─────────────────────────────────────────────────────────────────────────────

def audit_bc(c15_map: Dict[str, List[Any]], c1h_map: Dict[str, List[Any]], cutoff: str) -> None:
    sep("B+C REJECTION AUDIT", "=")

    all_sigs: List[Dict[str, Any]] = []
    per_symbol_raw = Counter()

    for sym, c15 in c15_map.items():
        try:
            sigs = scan_symbol(sym, c15, c1h_map.get(sym, []))
            _enrich(sigs, sym, c15)
        except Exception as e:
            print(f"⚠️ B+C scan {sym}: {e}")
            continue

        sigs = [s for s in sigs if str(s.get("date", "")) >= cutoff]
        per_symbol_raw[sym] += len(sigs)
        all_sigs.extend(sigs)

    selected = select_daily(all_sigs, TOP_N_DAILY)

    print(f"Raw B+C signals in window: {len(all_sigs)}")
    print(f"Selected Top-{TOP_N_DAILY}/day: {len(selected)}")
    print(f"Active B+C days: {len(set(str(s.get('date','')) for s in selected))}")

    reasons_all = Counter(first_bc_reject_reason(s) for s in all_sigs)
    reasons_selected = Counter(first_bc_reject_reason(s) for s in selected)

    print_counter("B+C all raw rejection reasons", reasons_all, len(all_sigs))
    print_counter("B+C selected Top/day rejection reasons", reasons_selected, len(selected))

    passed = [s for s in selected if first_bc_reject_reason(s) == "PASS"]
    rejected = [s for s in selected if first_bc_reject_reason(s) != "PASS"]

    sep("B+C PASS candidates", "-")
    if not passed:
        print("No live-eligible B+C signals.")
    else:
        print_sample_bc("B+C passed samples", passed, limit=20)

    # focus near misses
    near_score = [
        s for s in selected
        if first_bc_reject_reason(s) == "rank_score<75" and 70 <= safe_float(s.get("rank_score")) < 75
    ]
    print_sample_bc("B+C near-miss score 70-74.99", near_score, limit=40)

    # symbols
    sep("B+C raw signals by symbol", "-")
    for sym, count in per_symbol_raw.most_common():
        print(f"{sym:6}: {count}")

    # quick what-if counters, no PnL simulation here
    sep("B+C what-if live count only", "-")
    scenarios = [
        ("current", 75, 40, {"High"}),
        ("score>=72", 72, 40, {"High"}),
        ("score>=70", 70, 40, {"High"}),
        ("score>=72 adx>=35", 72, 35, {"High"}),
        ("score>=70 adx>=35", 70, 35, {"High"}),
        ("score>=72 High/Medium", 72, 40, {"High", "Medium"}),
    ]
    for name, score_min, adx_min, rvi_set in scenarios:
        n = 0
        by_day = Counter()
        for s in selected:
            if s.get("symbol") == "AAPL":
                continue
            if safe_float(s.get("rank_score")) < score_min:
                continue
            if s.get("rvi_bucket") not in rvi_set:
                continue
            if s.get("regime") != "Trend":
                continue
            if safe_float(s.get("adx")) < adx_min:
                continue
            if safe_int(s.get("entry_offset"), 999) > 4:
                continue
            if safe_float(s.get("atr_pct"), 999) > ATR_THRESHOLD:
                continue
            if safe_int(s.get("session_min"), 999) >= 525:
                continue
            n += 1
            by_day[str(s.get("date", ""))] += 1
        active_days = len(by_day)
        print(f"{name:24}: {n:3d} signals | active_days={active_days}")


# ─────────────────────────────────────────────────────────────────────────────
# ORB rejection audit
# ─────────────────────────────────────────────────────────────────────────────

def orb_manual_reason(sym: str, bars: List[Any], bias_map: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
    """
    Attempts to reproduce the first ORB rejection reason for latest session.
    It uses ORB module internals when available.
    """
    if orb is None:
        return "orb_module_missing", {}

    if not bars:
        return "no_bars", {}

    try:
        # Build session bars for latest date
        latest_date = max(b.timestamp.date() for b in bars)
        day = [b for b in bars if b.timestamp.date() == latest_date]
        if len(day) < 8:
            return "not_enough_intraday_bars", {"date": str(latest_date), "bars": len(day)}

        # ORB window usually first 30 minutes after open; use module constants if available
        sess_open = getattr(orb, "SESS_OPEN", 570)
        orb_done = getattr(orb, "SESS_ORB_DONE", 600)
        brk_end = getattr(orb, "SESS_BRK_END", 690)

        def sm(b):
            return (b.timestamp.hour * 60 + b.timestamp.minute)

        orb_bars = [b for b in day if sess_open <= sm(b) < orb_done]
        post = [b for b in day if orb_done <= sm(b) <= brk_end]
        if not orb_bars:
            return "no_orb_opening_range", {"date": str(latest_date)}
        if not post:
            return "no_post_orb_bars", {"date": str(latest_date)}

        orb_high = max(b.high for b in orb_bars)
        orb_low = min(b.low for b in orb_bars)

        # Check latest/any breakout
        breakout = None
        for b in post:
            if b.close > orb_high:
                breakout = ("LONG", b)
                break
            if b.close < orb_low:
                breakout = ("SHORT", b)
                break
        if not breakout:
            return "no_breakout", {"orb_high": orb_high, "orb_low": orb_low, "date": str(latest_date)}

        direction, b = breakout

        idx = bars.index(b)
        candles_to_idx = bars[:idx+1]
        atr = orb._atr(candles_to_idx, 14) if hasattr(orb, "_atr") else 0
        adx = orb._adx(candles_to_idx, 14) if hasattr(orb, "_adx") else 0
        rvol = orb._rvol(candles_to_idx, idx) if hasattr(orb, "_rvol") else 0

        if adx < getattr(orb, "ORB_ADX_MIN", 30):
            return "adx_weak", {"adx": adx, "need": getattr(orb, "ORB_ADX_MIN", 30), "symbol": sym}
        if rvol < getattr(orb, "ORB_RVOL_MIN", 1.5):
            return "rvol_low", {"rvol": rvol, "need": getattr(orb, "ORB_RVOL_MIN", 1.5), "symbol": sym}

        # If module scan returns nothing despite these passing, use its detail
        sigs = orb.scan_orb_live(sym, bars, bias_map)
        if not sigs:
            return "blocked_by_later_orb_filters", {"adx": adx, "rvol": rvol, "direction": direction}
        return "PASS", {"signals": len(sigs), "adx": adx, "rvol": rvol}

    except Exception as e:
        return f"audit_error:{type(e).__name__}", {"error": str(e)}


def audit_orb(c15_map: Dict[str, List[Any]], cutoff: str) -> None:
    sep("ORB REJECTION AUDIT", "=")

    if orb is None:
        print("ORB module missing.")
        return

    try:
        scan_syms = [s for s in SYMBOLS if s not in getattr(orb, "ORB_EXCLUDED", set())]
        print("ORB scan symbols:", scan_syms)
        print(
            f"Rules: ADX>={getattr(orb,'ORB_ADX_MIN',None)}, RVOL>={getattr(orb,'ORB_RVOL_MIN',None)}, "
            f"ORBrng>={getattr(orb,'ORB_RANGE_ATR_MIN',None)}ATR, EMA20dist>={getattr(orb,'ORB_EMA20_DIST_MIN',None)}ATR, "
            f"break>={getattr(orb,'ORB_BREAK_DIST_MIN',None)}ATR"
        )
    except Exception:
        scan_syms = []

    try:
        bias_map = orb._build_bias(c15_map)
    except Exception as e:
        print(f"❌ ORB bias error: {e}")
        return

    # Real scan candidates in window
    all_sigs = []
    per_symbol = Counter()
    for sym in scan_syms:
        bars = c15_map.get(sym)
        if not bars:
            continue
        try:
            sigs = orb.scan_orb_live(sym, bars, bias_map)
            sigs = [s for s in sigs if str(s.get("date", "")) >= cutoff]
            all_sigs.extend(sigs)
            per_symbol[sym] += len(sigs)
        except Exception as e:
            print(f"⚠️ ORB scan {sym}: {e}")

    sep("ORB qualifying signals by symbol", "-")
    if not all_sigs:
        print("No qualifying ORB signals in window.")
    else:
        for sym, count in per_symbol.most_common():
            print(f"{sym:6}: {count}")

    # Current latest-session rejection reason
    reasons = Counter()
    details = {}
    for sym in scan_syms:
        bars = c15_map.get(sym)
        if not bars:
            reasons["no_data"] += 1
            continue
        reason, info = orb_manual_reason(sym, bars, bias_map)
        reasons[reason] += 1
        details[sym] = (reason, info)

    print_counter("ORB latest-session first rejection reasons", reasons, sum(reasons.values()))

    sep("ORB latest-session per symbol", "-")
    print(f"{'Sym':6} {'Reason':28} Details")
    print("-" * 100)
    for sym in scan_syms:
        reason, info = details.get(sym, ("missing", {}))
        print(f"{sym:6} {reason:28} {info}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    days = 5
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except Exception:
            pass

    sep("REJECTION AUDIT — WHY NO TRADES?", "=")
    print("Research only. No files modified. No orders sent.")
    print("CHART_DIR:", CHART_DIR)

    c15_map, c1h_map = load_all()
    print(f"Loaded: {len(c15_map)}/{len(SYMBOLS)} symbols")
    cutoff = cutoff_date(c15_map, days)
    print(f"Window cutoff: {cutoff}  (last {days} available dates)")

    audit_bc(c15_map, c1h_map, cutoff)
    audit_orb(c15_map, cutoff)

    sep("FINAL READ", "=")
    print("If B+C rejection is mostly rank_score<75, the engine sees setups but the live gate is too strict.")
    print("If lowering to 72/70 still adds few signals, B+C is not the daily engine yet.")
    print("If ORB rejection is mostly no_breakout / adx_weak / rvol_low, ORB is healthy but market conditions do not match it.")
    print("If both engines are healthy but quiet, the next solution is adding a third independent Daily Engine, not random filter loosening.")


if __name__ == "__main__":
    main()
