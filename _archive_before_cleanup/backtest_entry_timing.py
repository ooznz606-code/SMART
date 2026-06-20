# -*- coding: utf-8 -*-
"""
backtest_entry_timing.py — SMART ICT X2: Entry Timing Diagnostic
=================================================================
Measures WHERE inside the swing move each signal fires.

Per-trade metrics:
  bars_since   — 15m bars from swing extreme to signal bar
  pct_done     — % of the eventual swing move already consumed at entry
  mfe_r        — max favorable excursion after entry (R, 40-bar window)
  mae_r        — max adverse excursion after entry  (R, 40-bar window)
  r_avail      — R from entry to the forward swing extreme (avg move available)
  bucket       — Early(<25%) / Mid(25-50%) / Late(50-75%) / VeryLate(75%+)

DO NOT modify analyzer_x2.py — read-only diagnostic only.
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import csv

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("pip install yfinance pandas")
    sys.exit(1)

try:
    from backtest_runner_x2 import (
        _flat, _to_candles, _market_state_from, _htf_direction,
        _call_detect_sweep, _call_detect_displacement,
        _detect_zone, _is_retest, _zone_bounds, _zone_mid, _event_index,
        _dir_enum,
        TP1_R, SETUP_COOLDOWN, MIN_HISTORY,
    )
except Exception as e:
    print(f"Cannot import backtest_runner_x2: {e}")
    sys.exit(1)

try:
    from analyzer_x2 import (
        Candle, Direction, SYMBOL_PROFILES, _volume_ratio,
        _fresh_zone_qualifies,
    )
except Exception as e:
    print(f"Cannot import analyzer_x2: {e}")
    sys.exit(1)


# ── Config ───────────────────────────────────────────────────────────────────
SYMBOLS        = ["QQQ", "MSFT", "META", "AMZN", "GOOGL", "SPY", "NVDA", "NFLX"]
DAYS           = 55            # yfinance 15m limit is 60 days; 55 keeps us safe
SWING_LOOKBACK = 60            # 15m bars back to find swing extreme
SWING_LOOKFWD  = 40            # 15m bars forward to find swing end / r_avail
SIM_BARS       = 40            # bars for MFE/MAE/outcome simulation


# ── Profile helper ───────────────────────────────────────────────────────────
def _get_profile(symbol: str) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "enabled": True, "timeframe": "1H",
        "min_conf": 72.0, "min_adx": 17.0, "max_adx": 68.0,
    }
    sp = SYMBOL_PROFILES.get(symbol, {})
    if isinstance(sp, dict) and sp.get("enabled", True) is not False:
        base.update({k: v for k, v in sp.items() if v is not None and k != "max_daily_signals"})
    base["enabled"] = True   # always enabled in diagnostic
    # SPY not in analyzer_x2 profiles — mirror QQQ settings
    if symbol == "SPY":
        base.update({"timeframe": "1H", "min_conf": 72.0, "min_adx": 17.0, "max_adx": 65.0})
    return base


# ── Timing helpers ───────────────────────────────────────────────────────────
def _swing_extreme(candles: List[Candle], direction: str,
                   lookback: int) -> Tuple[float, int]:
    """
    Returns (swing_price, bars_ago_from_current_bar) searching the last
    `lookback` 15m candles.  bars_ago=0 means the extreme is the current bar.
    """
    window = candles[-lookback:] if len(candles) >= lookback else candles[:]
    n = len(window)
    if not window:
        return (candles[-1].close if candles else 0.0), 0

    if direction == "LONG":
        best_price = min(c.low for c in window)
        for j in range(n - 1, -1, -1):
            if window[j].low <= best_price * 1.0005:
                return window[j].low, n - 1 - j
        return best_price, n - 1
    else:
        best_price = max(c.high for c in window)
        for j in range(n - 1, -1, -1):
            if window[j].high >= best_price * 0.9995:
                return window[j].high, n - 1 - j
        return best_price, n - 1


def _swing_end(future: List[Candle], direction: str) -> Optional[float]:
    """Forward-looking swing extreme across SWING_LOOKFWD bars."""
    if not future:
        return None
    return max(c.high for c in future) if direction == "LONG" else min(c.low for c in future)


def _bucket(pct: float) -> str:
    if pct < 25:  return "Early"
    if pct < 50:  return "Mid"
    if pct < 75:  return "Late"
    return "VeryLate"


# ── Per-symbol scan ───────────────────────────────────────────────────────────
def _run_symbol(symbol: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    counters: Dict[str, int] = {
        "candidates":      0,
        "sweep_rejected":  0,
        "zone_rejected":   0,
        "fz_freshness":    0,
        "fz_no_disp":      0,
        "fz_disp_quality": 0,
        "fz_disp_age":     0,
        "fz_wrong_side":   0,
        "dist_rejected":   0,
        "retest_rejected": 0,
        "score_rejected":  0,
        "fz_accepted":     0,
        "rt_accepted":     0,
    }

    end   = datetime.today()
    start = end - timedelta(days=DAYS + 3)   # stay within yfinance 60-day 15m window

    try:
        df15 = _flat(yf.download(symbol, start=start, end=end, interval="15m",
                                  progress=False, auto_adjust=True))
        df1h = _flat(yf.download(symbol, start=start, end=end, interval="1h",
                                  progress=False, auto_adjust=True))
    except Exception as e:
        print(f"download error: {e}")
        return [], counters

    if df15 is None or len(df15) < MIN_HISTORY:
        print(f"insufficient data ({len(df15) if df15 is not None else 0} rows)")
        return [], counters

    c15_all: List[Candle] = _to_candles(df15)
    c1h_all: List[Candle] = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []

    prof     = _get_profile(symbol)
    tf       = prof.get("timeframe", "1H")
    min_adx  = float(prof.get("min_adx", 17))
    max_adx  = float(prof.get("max_adx", 68))
    min_conf = float(prof.get("min_conf", 72))

    trades: List[Dict[str, Any]] = []
    last_bar = -999

    for i in range(MIN_HISTORY, len(c15_all) - SIM_BARS - 1):
        c15 = c15_all[:i + 1]
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15

        setup = (c15[-220:] if tf == "15m"
                 else (c1h[-300:] if c1h and len(c1h) >= 60 else c15[-220:]))
        trig  = c15[-8:]
        if len(setup) < 60:
            continue

        price  = c15_all[i].close
        market = _market_state_from(setup, price)
        atr    = market.atr_14
        adx    = market.adx

        if adx < min_adx or adx > max_adx:
            continue
        if (i - last_bar) < SETUP_COOLDOWN:
            continue

        htf  = _htf_direction(c1h[-300:] if c1h else c15[-300:], market)
        dirs = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in dirs:
            counters["candidates"] += 1

            sweep = _call_detect_sweep(setup, direction, atr)
            aidx  = _event_index(sweep) if sweep else max(0, len(setup) - 36)
            disp  = _call_detect_displacement(setup, direction, atr, aidx)

            if sweep is None and disp is None:
                counters["sweep_rejected"] += 1
                continue

            zafter = _event_index(disp) if disp else aidx
            zone   = _detect_zone(setup, direction, atr, zafter)
            if zone is None:
                counters["zone_rejected"] += 1
                continue
            zb = _zone_bounds(zone)
            if not zb:
                counters["zone_rejected"] += 1
                continue
            z_top, z_bot = zb
            z_mid = _zone_mid(zone)

            # ── Balanced Design (revised): Fresh Zone Entry -- before distance gate ──
            _fz_ok, _fz_reason = _fresh_zone_qualifies(
                sweep, len(setup), disp, zone, price, atr, _dir_enum(direction)
            )
            if not _fz_ok:
                if _fz_reason.startswith("freshness="):
                    counters["fz_freshness"] += 1
                elif _fz_reason.startswith("no_displacement"):
                    counters["fz_no_disp"] += 1
                elif _fz_reason.startswith("disp_q="):
                    counters["fz_disp_quality"] += 1
                elif _fz_reason.startswith("disp_age="):
                    counters["fz_disp_age"] += 1
                elif _fz_reason.startswith("price"):
                    counters["fz_wrong_side"] += 1

            if _fz_ok:
                entry_mode = "FRESH_ZONE_ENTRY"
            else:
                if abs(price - z_mid) / max(atr, 0.01) > 3.2:
                    counters["dist_rejected"] += 1
                    continue
                if not _is_retest(trig, zone, direction, atr):
                    counters["retest_rejected"] += 1
                    continue
                entry_mode = "RETEST_ENTRY"
            # ── end Fresh Zone / Retest branch ──────────────────────────────

            sq  = float(getattr(sweep, "quality", 0) or 0) if sweep else 0.0
            dq  = float(getattr(disp,  "quality", 0) or 0) if disp  else 0.0
            zq  = float(getattr(zone,  "quality", 0.7) or 0.7)
            fr  = float(getattr(zone,  "freshness", 0.7) or 0.7)
            try:
                vr = _volume_ratio(setup)
            except Exception:
                vr = 1.0
            score = 30 + sq*14 + dq*14 + zq*20 + fr*10
            if sweep and disp:   score += 6
            score += 5 if htf != "NEUTRAL" else 0
            score += 5 if vr >= 0.8 else 2
            if score < min_conf:
                counters["score_rejected"] += 1
                continue

            entry = c15_all[i].close
            stop  = z_bot - atr*0.22 if direction == "LONG" else z_top + atr*0.22
            risk  = abs(entry - stop)
            if risk <= 0:
                continue
            tp1 = entry + risk*TP1_R if direction == "LONG" else entry - risk*TP1_R

            if entry_mode == "FRESH_ZONE_ENTRY":
                counters["fz_accepted"] += 1
            else:
                counters["rt_accepted"] += 1

            # ── Timing metrics (always in 15m candles) ───────────────
            sw_start, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            future               = c15_all[i + 1 : i + 1 + SWING_LOOKFWD]
            sw_end               = _swing_end(future, direction)

            if direction == "LONG":
                total_mv  = (sw_end - sw_start) if sw_end else None
                at_entry  = entry - sw_start
            else:
                total_mv  = (sw_start - sw_end) if sw_end else None
                at_entry  = sw_start - entry

            pct = (round(at_entry / total_mv * 100, 1)
                   if (total_mv and total_mv > 0) else 50.0)

            r_avail = 0.0
            if sw_end and risk > 0:
                raw = (sw_end - entry) if direction == "LONG" else (entry - sw_end)
                r_avail = round(raw / risk, 2)

            # ── Trade simulation for MFE / MAE / outcome ─────────────
            outcome = "BE"; r_mult = 0.0; hold = 0; ep = entry
            mfe = 0.0; mae = 0.0

            for j, fc in enumerate(c15_all[i + 1 : i + 1 + SIM_BARS + 1], 1):
                if direction == "LONG":
                    mfe = max(mfe, (fc.high - entry) / risk)
                    mae = min(mae, (fc.low  - entry) / risk)
                    if fc.low  <= stop: outcome="LOSS"; ep=stop;  hold=j; r_mult=-1.0;  break
                    if fc.high >= tp1:  outcome="WIN";  ep=tp1;   hold=j; r_mult=TP1_R; break
                else:
                    mfe = max(mfe, (entry - fc.low)  / risk)
                    mae = min(mae, (entry - fc.high) / risk)
                    if fc.high >= stop: outcome="LOSS"; ep=stop;  hold=j; r_mult=-1.0;  break
                    if fc.low  <= tp1:  outcome="WIN";  ep=tp1;   hold=j; r_mult=TP1_R; break
            else:
                hold = SIM_BARS
                ep   = c15_all[min(i + SIM_BARS, len(c15_all) - 1)].close

            # ── Pipeline stage timings ───────────────────────────────
            # All indices are relative to setup; setup[-1] == bar i.
            # bars_ago(k) = len(setup)-1 - k  (how many bars before entry)
            _slen      = len(setup)
            _sweep_ago = (_slen - 1 - sweep.index)                    if sweep else None
            _disp_ago  = (_slen - 1 - disp.index)                     if disp  else None
            _zcix      = getattr(zone, "created_index", _slen - 1)
            _zone_ago  = _slen - 1 - _zcix
            # S1: swing extreme → sweep detection
            _s1 = max(0, bars_since - _sweep_ago) if _sweep_ago is not None else bars_since
            # S2: sweep → displacement
            _s2 = max(0, _sweep_ago - _disp_ago) if (_sweep_ago is not None and _disp_ago is not None) else 0
            # S3: displacement → zone creation
            _s3 = max(0, _disp_ago - _zone_ago) if _disp_ago is not None else 0
            # S4: zone creation → entry trigger
            _s4 = max(0, _zone_ago)

            trades.append({
                "symbol":      symbol,
                "date":        ts.strftime("%Y-%m-%d"),
                "time":        ts.strftime("%H:%M"),
                "direction":   "CALL" if direction == "LONG" else "PUT",
                "entry":       round(entry,    4),
                "stop":        round(stop,     4),
                "tp1":         round(tp1,      4),
                "exit_price":  round(ep,       4),
                "swing_start": round(sw_start, 4),
                "swing_end":   round(sw_end,   4) if sw_end else 0.0,
                "bars_since":  bars_since,
                "pct_done":    pct,
                "r_avail":     r_avail,
                "mfe_r":       round(mfe, 2),
                "mae_r":       round(mae, 2),
                "result":      outcome,
                "r":           round(r_mult, 2),
                "score":       round(score,  1),
                "bucket":      _bucket(pct),
                "entry_mode":  entry_mode,
                "disp_age":    (len(setup) - disp.index) if disp else -1,
                "zone_fresh":  round(getattr(zone, "freshness", 0.0), 3),
                "disp_qual":   round(getattr(disp, "quality",  0.0), 3) if disp else 0.0,
                "s1_swing_to_sweep": _s1,
                "s2_sweep_to_disp":  _s2,
                "s3_disp_to_zone":   _s3,
                "s4_zone_to_entry":  _s4,
            })
            last_bar = i
            break

    return trades, counters


# ── Reporting ─────────────────────────────────────────────────────────────────
def _safe_avg(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _print_table_a(all_trades: List[Dict]) -> None:
    W = 80
    print("\n" + "=" * W)
    print("  TABLE A — Standard Performance by Symbol")
    print("=" * W)
    fmt = "{:<7}  {:>7}  {:>4}  {:>4}  {:>4}  {:>6}  {:>7}  {:>9}"
    print(fmt.format("Symbol", "Trades", "W", "L", "BE", "WR%", "PF", "TotalR"))
    print("-" * W)

    tot_t = tot_w = tot_l = tot_be = 0
    tot_r = tot_gw = tot_gl = 0.0

    for sym in SYMBOLS:
        st = [t for t in all_trades if t["symbol"] == sym]
        if not st:
            print(f"{sym:<7}  {'0':>7}")
            continue
        n   = len(st)
        w   = sum(1 for t in st if t["result"] == "WIN")
        l   = sum(1 for t in st if t["result"] == "LOSS")
        be  = n - w - l
        dec = w + l
        wr  = w / dec * 100 if dec else 0.0
        tr  = sum(t["r"] for t in st)
        gw  = sum(t["r"] for t in st if t["r"] > 0)
        gl  = abs(sum(t["r"] for t in st if t["r"] < 0))
        pf  = gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0)
        print(fmt.format(sym, n, w, l, be, f"{wr:.1f}%", f"{pf:.2f}", f"{tr:+.2f}R"))
        tot_t += n; tot_w += w; tot_l += l; tot_be += be
        tot_r += tr; tot_gw += gw; tot_gl += gl

    print("-" * W)
    tot_wr = tot_w / (tot_w + tot_l) * 100 if (tot_w + tot_l) else 0.0
    tot_pf = tot_gw / tot_gl if tot_gl > 0 else (99.0 if tot_gw > 0 else 0.0)
    print(fmt.format("ALL", tot_t, tot_w, tot_l, tot_be,
                     f"{tot_wr:.1f}%", f"{tot_pf:.2f}", f"{tot_r:+.2f}R"))


def _print_table_b(all_trades: List[Dict]) -> None:
    W = 100
    print("\n" + "=" * W)
    print("  TABLE B — Entry Timing Breakdown")
    print("=" * W)

    BUCKETS = ["Early", "Mid", "Late", "VeryLate"]
    LABELS  = {
        "Early":    "Early    (<25%)",
        "Mid":      "Mid      (25-50%)",
        "Late":     "Late     (50-75%)",
        "VeryLate": "VeryLate  (75%+)",
    }

    hdr = ("{:<18}  {:>6}  {:>6}  {:>5}  {:>5}  {:>6}  {:>7}  "
           "{:>8}  {:>8}  {:>8}  {:>8}  {:>10}")
    print(hdr.format("Bucket", "Count", "%All", "W", "L", "WR%", "AvgR",
                     "AvgMFE", "AvgMAE", "AvgPct%", "AvgBars", "AvgRAvail"))
    print("-" * W)

    total = len(all_trades)
    for bk in BUCKETS:
        bt  = [t for t in all_trades if t["bucket"] == bk]
        n   = len(bt)
        if n == 0:
            print(f"{LABELS[bk]:<18}  {0:>6}")
            continue
        w   = sum(1 for t in bt if t["result"] == "WIN")
        l   = sum(1 for t in bt if t["result"] == "LOSS")
        dec = w + l
        wr  = w / dec * 100 if dec else 0.0
        pct_all  = n / total * 100 if total else 0.0
        avg_r    = _safe_avg([t["r"]          for t in bt])
        avg_mfe  = _safe_avg([t["mfe_r"]      for t in bt])
        avg_mae  = _safe_avg([t["mae_r"]      for t in bt])
        avg_pct  = _safe_avg([t["pct_done"]   for t in bt])
        avg_bars = _safe_avg([t["bars_since"] for t in bt])
        avg_rav  = _safe_avg([t["r_avail"]    for t in bt])
        print(hdr.format(
            LABELS[bk], n, f"{pct_all:.1f}%", w, l, f"{wr:.1f}%",
            f"{avg_r:+.2f}R", f"{avg_mfe:.2f}R", f"{avg_mae:.2f}R",
            f"{avg_pct:.1f}%", f"{avg_bars:.1f}", f"{avg_rav:+.2f}R",
        ))

    # Entry mode split
    n_total = len(all_trades)
    fz_trades  = [t for t in all_trades if t.get("entry_mode") == "FRESH_ZONE_ENTRY"]
    rt_trades  = [t for t in all_trades if t.get("entry_mode") == "RETEST_ENTRY"]
    def _mode_wr(lst):
        w = sum(1 for t in lst if t["result"] == "WIN")
        l = sum(1 for t in lst if t["result"] == "LOSS")
        return w/(w+l)*100 if (w+l) else 0.0
    def _mode_r(lst):
        return sum(t["r"] for t in lst)
    print()
    print("  Entry mode split:")
    print(f"  FRESH_ZONE_ENTRY : {len(fz_trades):>4} ({len(fz_trades)/n_total*100:.1f}%)  "
          f"WR={_mode_wr(fz_trades):.1f}%  TotalR={_mode_r(fz_trades):+.2f}R")
    print(f"  RETEST_ENTRY     : {len(rt_trades):>4} ({len(rt_trades)/n_total*100:.1f}%)  "
          f"WR={_mode_wr(rt_trades):.1f}%  TotalR={_mode_r(rt_trades):+.2f}R")

    # FRESH_ZONE_ENTRY detail
    if fz_trades:
        print()
        print("  FRESH_ZONE_ENTRY bucket distribution:")
        for bk in ["Early", "Mid", "Late", "VeryLate"]:
            n_bk = sum(1 for t in fz_trades if t["bucket"] == bk)
            if n_bk:
                print(f"    {bk:<12}: {n_bk} ({n_bk/len(fz_trades)*100:.0f}%)")
        avg_pct_fz   = _safe_avg([t["pct_done"]   for t in fz_trades])
        avg_da_fz    = _safe_avg([t["disp_age"]   for t in fz_trades
                                  if t.get("disp_age", -1) >= 0])
        avg_fresh_fz = _safe_avg([t["zone_fresh"] for t in fz_trades])
        avg_dq_fz    = _safe_avg([t["disp_qual"]  for t in fz_trades])
        print(f"    avg pct_done        : {avg_pct_fz:.1f}%")
        print(f"    avg displacement_age: {avg_da_fz:.1f} bars")
        print(f"    avg zone_freshness  : {avg_fresh_fz:.3f}")
        print(f"    avg disp_quality    : {avg_dq_fz:.3f}")

    # Per-symbol bucket distribution
    print()
    print("  Per-symbol distribution (% of that symbol's signals in each bucket):")
    sub = "{:<7}  {:>10}  {:>10}  {:>10}  {:>10}  {:>7}"
    print("  " + sub.format("Symbol", "Early", "Mid", "Late", "VeryLate", "Total"))
    print("  " + "-" * 60)
    for sym in SYMBOLS:
        st = [t for t in all_trades if t["symbol"] == sym]
        if not st:
            continue
        n = len(st)
        def _p(bk):
            c = sum(1 for t in st if t["bucket"] == bk)
            return f"{c} ({c/n*100:.0f}%)"
        print("  " + sub.format(sym, _p("Early"), _p("Mid"), _p("Late"), _p("VeryLate"), n))


def _print_table_c(all_trades: List[Dict]) -> None:
    W = 108
    print("\n" + "=" * W)
    print("  TABLE C — Top 20 Worst Late Entries  "
          "(Late + VeryLate, sorted by pct_done descending)")
    print("=" * W)

    late = [t for t in all_trades if t["bucket"] in ("Late", "VeryLate")]
    late.sort(key=lambda t: t["pct_done"], reverse=True)
    top20 = late[:20]

    if not top20:
        print("  No late entries found in this window.")
        return

    hdr = ("{:>3}  {:<7}  {:<12}  {:<5}  {:>9}  {:>9}  {:>9}  "
           "{:>9}  {:>6}  {:>8}  {:>7}  {:>7}  {:<7}")
    print(hdr.format(
        "#", "Symbol", "Date", "Dir", "Entry", "SwgStart", "SwgEnd",
        "MovePct%", "Bars", "RAvail", "MFE", "MAE", "Result",
    ))
    print("-" * W)

    for rank, t in enumerate(top20, 1):
        print(hdr.format(
            rank,
            t["symbol"],
            t["date"],
            t["direction"],
            f"{t['entry']:.2f}",
            f"{t['swing_start']:.2f}",
            f"{t['swing_end']:.2f}",
            f"{t['pct_done']:.1f}%",
            t["bars_since"],
            f"{t['r_avail']:+.2f}R",
            f"{t['mfe_r']:.2f}R",
            f"{t['mae_r']:.2f}R",
            t["result"],
        ))


def _print_pipeline_stages(all_trades: List[Dict]) -> None:
    W = 88
    print("\n" + "=" * W)
    print("  TABLE E — ICT Pipeline Stage Timing  (15m bars per stage)")
    print("  SwingStart -> [S1] -> Sweep -> [S2] -> Displacement -> [S3] -> Zone -> [S4] -> Entry")
    print("=" * W)

    BUCKETS = ["Early", "Mid", "Late", "VeryLate", "ALL"]
    LABELS  = {"Early": "Early (<25%)", "Mid": "Mid (25-50%)",
                "Late": "Late (50-75%)", "VeryLate": "VeryLate (75%+)", "ALL": "ALL"}

    hdr = "{:<16}  {:>5}  {:>9}  {:>9}  {:>9}  {:>9}  {:>9}"
    print(hdr.format("Bucket", "N", "S1:Swg>Swp", "S2:Swp>Dsp", "S3:Dsp>Zon", "S4:Zon>Ent", "Total"))
    print("-" * W)

    for bk in BUCKETS:
        bt = all_trades if bk == "ALL" else [t for t in all_trades if t["bucket"] == bk]
        n  = len(bt)
        if n == 0:
            print(hdr.format(LABELS[bk], 0, "-", "-", "-", "-", "-"))
            continue
        s1 = _safe_avg([t["s1_swing_to_sweep"] for t in bt])
        s2 = _safe_avg([t["s2_sweep_to_disp"]  for t in bt])
        s3 = _safe_avg([t["s3_disp_to_zone"]   for t in bt])
        s4 = _safe_avg([t["s4_zone_to_entry"]  for t in bt])
        tot = _safe_avg([t["bars_since"]        for t in bt])
        print(hdr.format(LABELS[bk], n,
                         f"{s1:.1f}b", f"{s2:.1f}b", f"{s3:.1f}b", f"{s4:.1f}b", f"{tot:.1f}b"))

    # Stage share of total time per bucket
    print()
    print("  Stage as % of total elapsed time:")
    hdr2 = "{:<16}  {:>14}  {:>14}  {:>14}  {:>14}"
    print(hdr2.format("Bucket", "S1:Swg>Swp", "S2:Swp>Dsp", "S3:Dsp>Zon", "S4:Zon>Ent"))
    print("-" * W)
    for bk in BUCKETS:
        bt = all_trades if bk == "ALL" else [t for t in all_trades if t["bucket"] == bk]
        n  = len(bt)
        if n == 0:
            continue
        s1 = _safe_avg([t["s1_swing_to_sweep"] for t in bt])
        s2 = _safe_avg([t["s2_sweep_to_disp"]  for t in bt])
        s3 = _safe_avg([t["s3_disp_to_zone"]   for t in bt])
        s4 = _safe_avg([t["s4_zone_to_entry"]  for t in bt])
        tot = s1 + s2 + s3 + s4
        if tot <= 0:
            continue
        def _pct(v: float) -> str:
            return f"{v/tot*100:.0f}% ({v:.1f}b)"
        print(hdr2.format(LABELS[bk], _pct(s1), _pct(s2), _pct(s3), _pct(s4)))

    print("=" * W)


def _print_gate_counters(c: Dict[str, int]) -> None:
    W = 72
    print("\n" + "=" * W)
    print("  TABLE D — Gate-by-Gate Rejection Funnel (all symbols combined)")
    print("=" * W)
    cand = c["candidates"]
    pct  = lambda n: f"{n/cand*100:.1f}%" if cand else "n/a"

    print(f"  Candidates scanned        : {cand:>8}")
    print()
    print(f"  [1] Sweep+Disp both None  : {c['sweep_rejected']:>8}  ({pct(c['sweep_rejected'])} of candidates)")
    print(f"  [2] Zone not found        : {c['zone_rejected']:>8}  ({pct(c['zone_rejected'])} of candidates)")
    print()
    fz_tot = (c["fz_freshness"] + c["fz_no_disp"] + c["fz_disp_quality"]
              + c["fz_disp_age"] + c["fz_wrong_side"])
    print(f"  [3] Fresh Zone failed     : {fz_tot:>8}  ({pct(fz_tot)} of candidates)")
    print(f"       zone_freshness<0.90  : {c['fz_freshness']:>8}")
    print(f"       no_displacement      : {c['fz_no_disp']:>8}")
    print(f"       disp_quality<0.65    : {c['fz_disp_quality']:>8}")
    print(f"       disp_age>5           : {c['fz_disp_age']:>8}")
    print(f"       wrong_side_of_mid    : {c['fz_wrong_side']:>8}")
    print()
    print(f"  [4] Dist >3.2ATR (retest) : {c['dist_rejected']:>8}  ({pct(c['dist_rejected'])} of candidates)")
    print(f"  [5] Retest check failed   : {c['retest_rejected']:>8}  ({pct(c['retest_rejected'])} of candidates)")
    print(f"  [6] Score < min_conf      : {c['score_rejected']:>8}  ({pct(c['score_rejected'])} of candidates)")
    print()
    print(f"  Accepted FRESH_ZONE_ENTRY : {c['fz_accepted']:>8}  ({pct(c['fz_accepted'])} of candidates)")
    print(f"  Accepted RETEST_ENTRY     : {c['rt_accepted']:>8}  ({pct(c['rt_accepted'])} of candidates)")
    print("=" * W)


def _write_csv(all_trades: List[Dict], path: str = "backtest_entry_timing.csv") -> None:
    if not all_trades:
        return
    fields = [
        "symbol", "date", "time", "direction",
        "entry", "stop", "tp1", "exit_price",
        "swing_start", "swing_end",
        "bars_since", "pct_done", "r_avail",
        "mfe_r", "mae_r",
        "result", "r", "score", "bucket", "entry_mode",
        "disp_age", "zone_fresh", "disp_qual",
        "s1_swing_to_sweep", "s2_sweep_to_disp", "s3_disp_to_zone", "s4_zone_to_entry",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in all_trades:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"\n  Full trade log saved -> {path}")


def _print_summary(all_trades: List[Dict]) -> None:
    n = len(all_trades)
    if not n:
        return
    late_n    = sum(1 for t in all_trades if t["bucket"] in ("Late", "VeryLate"))
    early_n   = sum(1 for t in all_trades if t["bucket"] == "Early")
    mid_n     = sum(1 for t in all_trades if t["bucket"] == "Mid")
    vlate_n   = sum(1 for t in all_trades if t["bucket"] == "VeryLate")
    avg_pct   = _safe_avg([t["pct_done"]  for t in all_trades])
    avg_bars  = _safe_avg([t["bars_since"] for t in all_trades])
    avg_mfe   = _safe_avg([t["mfe_r"]     for t in all_trades])
    avg_mae   = _safe_avg([t["mae_r"]     for t in all_trades])

    w  = sum(1 for t in all_trades if t["result"] == "WIN")
    l  = sum(1 for t in all_trades if t["result"] == "LOSS")
    wr = w / (w + l) * 100 if (w + l) else 0.0

    print("\n" + "=" * 80)
    print("  OVERALL TIMING SUMMARY")
    print("=" * 80)
    print(f"  Total signals       : {n}")
    print(f"  Overall WR          : {wr:.1f}%")
    print(f"  Avg move completed  : {avg_pct:.1f}%  (avg bars since swing: {avg_bars:.1f})")
    print(f"  Avg MFE             : {avg_mfe:.2f}R   |  Avg MAE: {avg_mae:.2f}R")
    print()
    print(f"  Early    (<25%)     : {early_n:>4} signals  ({early_n/n*100:.1f}%)")
    print(f"  Mid      (25-50%)   : {mid_n:>4} signals  ({mid_n/n*100:.1f}%)")
    print(f"  Late     (50-75%)   : {sum(1 for t in all_trades if t['bucket']=='Late'):>4} signals  "
          f"({sum(1 for t in all_trades if t['bucket']=='Late')/n*100:.1f}%)")
    print(f"  VeryLate (75%+)     : {vlate_n:>4} signals  ({vlate_n/n*100:.1f}%)")
    print(f"  Late+VeryLate total : {late_n:>4} signals  ({late_n/n*100:.1f}%)")
    print("=" * 80)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    print("\n" + "=" * 80)
    print(f"  SMART ICT X2 — Entry Timing Diagnostic")
    print(f"  Symbols : {', '.join(SYMBOLS)}")
    print(f"  Window  : {DAYS} days | Swing lookback: {SWING_LOOKBACK} bars | "
          f"Forward: {SWING_LOOKFWD} bars | Sim: {SIM_BARS} bars")
    print(f"  Run at  : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80 + "\n")

    all_trades:   List[Dict[str, Any]] = []
    all_counters: Dict[str, int] = {
        "candidates": 0, "sweep_rejected": 0, "zone_rejected": 0,
        "fz_freshness": 0, "fz_no_disp": 0, "fz_disp_quality": 0,
        "fz_disp_age": 0, "fz_wrong_side": 0,
        "dist_rejected": 0, "retest_rejected": 0, "score_rejected": 0,
        "fz_accepted": 0, "rt_accepted": 0,
    }

    for sym in SYMBOLS:
        print(f"  [{sym:5}] scanning ... ", end="", flush=True)
        try:
            trades, ctrs = _run_symbol(sym)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        all_trades.extend(trades)
        for k, v in ctrs.items():
            all_counters[k] = all_counters.get(k, 0) + v
        n = len(trades)
        bk = {b: sum(1 for t in trades if t["bucket"] == b)
              for b in ["Early", "Mid", "Late", "VeryLate"]}
        late_pct = (bk["Late"] + bk["VeryLate"]) / n * 100 if n else 0.0
        fz_n = sum(1 for t in trades if t.get("entry_mode") == "FRESH_ZONE_ENTRY")
        print(f"{n:>3} signals  "
              f"Early={bk['Early']:>2}  Mid={bk['Mid']:>2}  "
              f"Late={bk['Late']:>2}  VLate={bk['VeryLate']:>2}  "
              f"[late%={late_pct:.0f}%]  FZ={fz_n}")

    if not all_trades:
        print("\nNo trades generated — check ADX/score thresholds or data availability.")
        return

    _print_table_a(all_trades)
    _print_table_b(all_trades)
    _print_table_c(all_trades)
    _print_pipeline_stages(all_trades)
    _print_gate_counters(all_counters)
    _write_csv(all_trades)
    _print_summary(all_trades)


if __name__ == "__main__":
    main()
