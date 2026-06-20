# -*- coding: utf-8 -*-
"""
backtest_bc_research.py  --  B+C Signal Dedicated Research Backtest
====================================================================
Approved 2026-06-20.

Trades only when BOTH signals fire on the same birth event within
the confirmation window (+2 to +6 bars after birth).

  B  Early Displacement:
       - Body >= 0.50 x ATR in trade direction
       - Close in top/bottom 35% of bar range (dir_close >= 0.65)
       - Must be a directional candle (close > open for LONG)

  C  Structure Break:
       - Close through max(high) of [swing_start -> birth_bar] for LONG
       - Close through min(low)  of [swing_start -> birth_bar] for SHORT

Entry: close of the later-firing bar (max(B_offset, C_offset)).

Removed vs early_confirmation.py:
  - Signal A, D, E
  - All scoring and ranking logic
  - Signal overlap table

Kept hard gates:
  - Birth mandatory (>=1 of B1/B2/B3)
  - b_qual >= 0.55, disp_qual >= 0.35, birth_age <= 15
  - HTF strength > 0.30 (direction-adjusted EMA20/EMA50 on 1H)
  - Session_min < 525 (last 75 min excluded)

Zone: stop fallback only, not a gate.  Not reported.

Robustness: reports 30d, 55d, 90d, max-available windows.
yfinance 15m data limit is ~60 days; 90d and max windows will
show actual available span.

Do not modify analyzer_x2.  Research only.
"""
from __future__ import annotations

import csv
import sys
import warnings
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance"); sys.exit(1)

try:
    from backtest_runner_x2 import (
        _flat, _to_candles, _market_state_from,
        _detect_zone, _zone_bounds, _zone_mid, _htf_direction,
        MIN_HISTORY, TP1_R,
    )
except Exception as e:
    print(f"Cannot import backtest_runner_x2: {e}"); sys.exit(1)

try:
    from analyzer_x2 import Candle
except Exception as e:
    print(f"Cannot import analyzer_x2: {e}"); sys.exit(1)

try:
    from backtest_entry_timing import (
        _get_profile, _swing_extreme, _swing_end,
        _bucket, _safe_avg,
        SYMBOLS, SWING_LOOKBACK, SWING_LOOKFWD, SIM_BARS,
    )
except Exception as e:
    print(f"Cannot import backtest_entry_timing: {e}"); sys.exit(1)

try:
    from backtest_move_birth import _birth_expand, _birth_volume, _birth_structure, _cap
except Exception as e:
    print(f"Cannot import backtest_move_birth: {e}"); sys.exit(1)

try:
    from backtest_x3_birth_ict import _disp_quality
except Exception as e:
    print(f"Cannot import backtest_x3_birth_ict: {e}"); sys.exit(1)


# ── Constants ─────────────────────────────────────────────────────────────────

DOWNLOAD_DAYS      = 55     # 55+3 buffer = 58 days, within yfinance 15m 60-day limit
CONF_START         = 2      # first allowed confirmation offset from birth
CONF_END           = 6      # last  allowed confirmation offset from birth
POOL_COOLDOWN      = 16     # bars between births per symbol per direction

PRE_B_QUAL_MIN     = 0.55
PRE_DISP_QUAL_MIN  = 0.35
PRE_BIRTH_AGE_MAX  = 15
HTF_MIN_STRENGTH   = 0.30   # direction-adjusted EMA20/EMA50 gap * 100
SESSION_CUTOFF     = 525    # session_min >= 525 excluded (last ~75 min)
STOP_BUFFER        = 0.22   # ATR buffer for stop placement

DISPLACE_BODY_MIN  = 0.50   # Signal B: min body as fraction of ATR
DISPLACE_CLOSE_MIN = 0.65   # Signal B: min directional close fraction

ROBUSTNESS_WINDOWS: List[Tuple[str, int]] = [
    ("30 days",    30),
    ("55 days",    55),
    ("90 days",    90),
    ("Max avail",  9999),
]

W = 88   # report line width


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# ── Download ──────────────────────────────────────────────────────────────────

def _download(symbol: str) -> Optional[Tuple[List[Candle], List[Candle]]]:
    end   = datetime.today()
    start = end - timedelta(days=DOWNLOAD_DAYS + 3)
    try:
        df15 = _flat(yf.download(symbol, start=start, end=end, interval="15m",
                                  progress=False, auto_adjust=True))
        df1h = _flat(yf.download(symbol, start=start, end=end, interval="1h",
                                  progress=False, auto_adjust=True))
    except Exception as e:
        print(f"  download error {symbol}: {e}"); return None
    if df15 is None or len(df15) < MIN_HISTORY:
        return None
    c15 = _to_candles(df15)
    c1h = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []
    return c15, c1h


# ── Core B+C scanner ──────────────────────────────────────────────────────────

def _scan_symbol(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:
    """
    Scan for birth events that pass pre-conditions, then look for
    both B (displacement) AND C (structure break) within bars +2..+6.
    Entry = close of whichever fires second.
    Returns one record per qualifying B+C event.
    """
    prof    = _get_profile(symbol)
    min_adx = float(prof.get("min_adx", 17))
    max_adx = float(prof.get("max_adx", 68))

    results: List[Dict] = []
    last_birth: Dict[str, int] = {"LONG": -999, "SHORT": -999}
    safe_max = len(c15_all) - SIM_BARS - CONF_END - 2

    for i in range(MIN_HISTORY, safe_max):
        c15 = c15_all[: i + 1]
        ts  = c15_all[i].timestamp

        zone_setup = c15[-220:]
        if len(zone_setup) < 60:
            continue

        market = _market_state_from(zone_setup, c15_all[i].close)
        atr    = market.atr_14
        adx    = market.adx
        if adx < min_adx or adx > max_adx:
            continue

        c1h      = [c for c in c1h_all if c.timestamp <= ts] or c15[-40:]
        htf_feed = c1h[-300:] if len(c1h) >= 60 else c15[-220:]
        htf      = _htf_direction(htf_feed, market)
        dirs     = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in dirs:

            # ── Swing & birth ─────────────────────────────────────────
            sw_price, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            swing_local  = len(c15) - 1 - bars_since
            swing_global = i - bars_since

            b_exp = _cap(_birth_expand(c15, swing_local, direction, atr), bars_since)
            b_vol = _cap(_birth_volume(c15, swing_local),                  bars_since)
            b_str = _cap(_birth_structure(c15, swing_local, direction),    bars_since)
            b_any = min(b_exp, b_vol, b_str)

            birth_global     = swing_global + b_any
            bars_after_birth = i - birth_global

            # Trigger only AT birth bar
            if bars_after_birth != 0:
                continue
            if b_any > PRE_BIRTH_AGE_MAX:
                continue
            if birth_global <= last_birth[direction]:
                continue
            if not (0 <= birth_global < len(c15_all)):
                continue

            # ── Pre-conditions (hard gates) ───────────────────────────
            bc     = c15_all[birth_global]
            b_body = abs(bc.close - bc.open)
            b_qual = min(1.0, b_body / max(atr, 1e-9))
            if b_qual < PRE_B_QUAL_MIN:
                continue

            b_local = swing_local + b_any
            try:
                disp_qual = _disp_quality(c15, b_local, direction, atr)
            except Exception:
                disp_qual = 0.0
            if disp_qual < PRE_DISP_QUAL_MIN:
                continue

            # HTF strength
            h_src    = c1h if len(c1h) >= 50 else c15[-100:]
            h_prices = [c.close for c in h_src]
            raw_htf  = (((_ema(h_prices, 20) - _ema(h_prices, 50))
                         / max(abs(_ema(h_prices, 50)), 1e-9)) * 100.0)
            htf_str  = raw_htf if direction == "LONG" else -raw_htf
            if htf_str <= HTF_MIN_STRENGTH:
                continue

            # Session
            h_u, m_u    = ts.hour, ts.minute
            session_min = (h_u - 9) * 60 + m_u - 30
            if session_min >= SESSION_CUTOFF:
                continue

            # At least one birth type
            near    = b_any + 2
            b1_fire = b_exp <= near
            b2_fire = b_vol <= near
            b3_fire = b_str <= near
            if not (b1_fire or b2_fire or b3_fire):
                continue

            last_birth[direction] = birth_global

            # ── Stop placement (zone if available, else birth extreme) ─
            swing_in_setup = max(0, len(zone_setup) - 1 - bars_since)
            zone           = _detect_zone(zone_setup, direction, atr, swing_in_setup)
            if zone:
                zb = _zone_bounds(zone)
                if zb:
                    z_top, z_bot = zb
                    stop_price = (z_bot - STOP_BUFFER * atr if direction == "LONG"
                                  else z_top + STOP_BUFFER * atr)
                else:
                    stop_price = (bc.low  - STOP_BUFFER * atr if direction == "LONG"
                                  else bc.high + STOP_BUFFER * atr)
            else:
                stop_price = (bc.low  - STOP_BUFFER * atr if direction == "LONG"
                              else bc.high + STOP_BUFFER * atr)

            # ── Structural level for Signal C ─────────────────────────
            birth_area = c15_all[swing_global : birth_global + 1]
            if not birth_area:
                continue
            if direction == "LONG":
                h_struct = max(c.high for c in birth_area)
            else:
                l_struct = min(c.low  for c in birth_area)

            birth_mid = (bc.high + bc.low) / 2.0

            # ── Simulation closure ────────────────────────────────────
            def _sim(entry_px: float, entry_bar: int,
                     _stop=stop_price, _dir=direction,
                     _sw=sw_price, _sg=swing_global) -> Optional[Dict]:
                risk = abs(entry_px - _stop)
                if risk <= 1e-9:
                    return None
                tp1 = (entry_px + risk * TP1_R if _dir == "LONG"
                       else entry_px - risk * TP1_R)
                result = "BE"; r_mult = 0.0
                mfe = 0.0; mae = 0.0
                for fc in c15_all[entry_bar + 1 : entry_bar + SIM_BARS + 1]:
                    if _dir == "LONG":
                        mfe = max(mfe, (fc.high - entry_px) / risk)
                        mae = min(mae, (fc.low  - entry_px) / risk)
                        if fc.low  <= _stop: result = "LOSS"; r_mult = -1.0; break
                        if fc.high >= tp1:   result = "WIN";  r_mult = TP1_R; break
                    else:
                        mfe = max(mfe, (entry_px - fc.low)  / risk)
                        mae = min(mae, (entry_px - fc.high) / risk)
                        if fc.high >= _stop: result = "LOSS"; r_mult = -1.0; break
                        if fc.low  <= tp1:   result = "WIN";  r_mult = TP1_R; break

                bars_sw = entry_bar - _sg
                future  = c15_all[entry_bar + 1 : entry_bar + 1 + SWING_LOOKFWD]
                sw_end  = _swing_end(future, _dir)
                if sw_end is not None:
                    total_mv = ((sw_end - _sw) if _dir == "LONG" else (_sw - sw_end))
                    at_entry = ((entry_px - _sw) if _dir == "LONG" else (_sw - entry_px))
                    pct = round(at_entry / total_mv * 100, 1) if total_mv > 0 else 50.0
                else:
                    pct = 50.0
                return {
                    "result":          result,
                    "r":               round(r_mult, 2),
                    "mfe_r":           round(mfe, 2),
                    "mae_r":           round(mae, 2),
                    "bars_since_swing": bars_sw,
                    "pct_done":        pct,
                    "bucket":          _bucket(pct),
                }

            # ── Confirmation window: search +2 to +6 ─────────────────
            b_offset: Optional[int] = None
            c_offset: Optional[int] = None

            for offset in range(CONF_START, CONF_END + 1):
                conf_idx = birth_global + offset
                if conf_idx >= safe_max + CONF_END:
                    break
                cb2 = c15_all[conf_idx]
                rng = max(cb2.high - cb2.low, 1e-9)

                if direction == "LONG":
                    body2     = cb2.close - cb2.open
                    dir_close = (cb2.close - cb2.low) / rng
                    if b_offset is None:
                        if (cb2.close > cb2.open
                                and body2     >= DISPLACE_BODY_MIN  * atr
                                and dir_close >= DISPLACE_CLOSE_MIN):
                            b_offset = offset
                    if c_offset is None and cb2.close > h_struct:
                        c_offset = offset
                else:  # SHORT
                    body2     = cb2.open - cb2.close
                    dir_close = (cb2.high - cb2.close) / rng
                    if b_offset is None:
                        if (cb2.close < cb2.open
                                and body2     >= DISPLACE_BODY_MIN  * atr
                                and dir_close >= DISPLACE_CLOSE_MIN):
                            b_offset = offset
                    if c_offset is None and cb2.close < l_struct:
                        c_offset = offset

                # Both fired -- enter on the later bar
                if b_offset is not None and c_offset is not None:
                    entry_offset = max(b_offset, c_offset)
                    entry_idx    = birth_global + entry_offset
                    entry_px     = c15_all[entry_idx].close
                    out          = _sim(entry_px, entry_idx)
                    if out:
                        seq = ("B_then_C" if b_offset < c_offset
                               else ("C_then_B" if c_offset < b_offset
                                     else "same_bar"))
                        results.append({
                            "symbol":       symbol,
                            "direction":    "CALL" if direction == "LONG" else "PUT",
                            "date":         ts.strftime("%Y-%m-%d"),
                            "time":         ts.strftime("%H:%M"),
                            "b_offset":     b_offset,
                            "c_offset":     c_offset,
                            "entry_offset": entry_offset,
                            "sequence":     seq,
                            "b_any":        b_any,
                            "b_qual":       round(b_qual,    3),
                            "disp_qual":    round(disp_qual, 3),
                            "htf_strength": round(htf_str,   3),
                            "session_min":  session_min,
                            "confluence":   int(b1_fire) + int(b2_fire) + int(b3_fire),
                            **out,
                        })
                    break   # only one B+C trade per birth event

    return results


# ── Metrics ───────────────────────────────────────────────────────────────────

def _m(trades: List[Dict]) -> Dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0,
                "mfe": 0.0, "mae": 0.0, "lv": 0.0}
    wins = sum(1 for t in trades if t["result"] == "WIN")
    loss = sum(1 for t in trades if t["result"] == "LOSS")
    gw   = sum(float(t["r"]) for t in trades if float(t["r"]) > 0)
    gl   = abs(sum(float(t["r"]) for t in trades if float(t["r"]) < 0))
    dec  = wins + loss
    lv   = sum(1 for t in trades if t.get("bucket") in ("Late", "VeryLate"))
    return {
        "n":      n,
        "wr":     wins / dec * 100 if dec else 0.0,
        "pf":     gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr": sum(float(t["r"]) for t in trades),
        "mfe":    _safe_avg([float(t["mfe_r"]) for t in trades]),
        "mae":    _safe_avg([float(t["mae_r"]) for t in trades]),
        "lv":     lv / n * 100,
    }


def _hline(c: str = "-", w: int = W) -> str:
    return "  " + c * (w - 2)


# ── Section A: Overall ────────────────────────────────────────────────────────

def _print_overall(trades: List[Dict], label: str = "Max available") -> None:
    m = _m(trades)
    if not trades:
        print(f"  {label}: no trades"); return
    dates = sorted({t["date"] for t in trades})
    span  = f"{dates[0]} to {dates[-1]}  ({len(dates)} trading days)"

    print("\n" + "=" * W)
    print(f"  B+C SIGNAL  --  OVERALL  [{label}]")
    print(f"  Period   : {span}")
    print(f"  Symbols  : {', '.join(SYMBOLS)}")
    print("=" * W)
    hdr = "  {:>6}  {:>6}  {:>5}  {:>8}  {:>8}  {:>8}  {:>6}"
    print(hdr.format("N", "WR%", "PF", "TotalR", "AvgMFE", "AvgMAE", "LV%"))
    print(_hline())
    print(hdr.format(
        m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
        f"{m['totalr']:+.1f}R", f"{m['mfe']:+.2f}R",
        f"{m['mae']:+.2f}R", f"{m['lv']:.1f}%",
    ))
    print(_hline("="))

    # Reference
    ref = "  Ref X2 baseline: N=207  WR=39.5%  PF=1.18  TotalR=+19.0R  LV=75.8%"
    print(ref)
    print(_hline("="))


# ── Section B: By symbol ──────────────────────────────────────────────────────

def _print_by_symbol(trades: List[Dict]) -> None:
    print("\n" + "=" * W)
    print("  B) BY SYMBOL")
    print("=" * W)
    hdr = "  {:<7}  {:>5}  {:>6}  {:>5}  {:>8}"
    print(hdr.format("Symbol", "N", "WR%", "PF", "TotalR"))
    print(_hline())
    totals = []
    for sym in SYMBOLS:
        st = [t for t in trades if t["symbol"] == sym]
        m  = _m(st)
        totals.append(m)
        flag = "  *** low N" if m["n"] < 5 else ""
        print(hdr.format(sym, m["n"], f"{m['wr']:.1f}%",
                          f"{m['pf']:.2f}", f"{m['totalr']:+.1f}R") + flag)
    print(_hline())
    mt = _m(trades)
    print(hdr.format("TOTAL", mt["n"], f"{mt['wr']:.1f}%",
                      f"{mt['pf']:.2f}", f"{mt['totalr']:+.1f}R"))
    # Consistency: how many symbols positive PF
    pos = sum(1 for m in totals if m["n"] > 0 and m["pf"] >= 1.0)
    print(f"\n  Positive PF symbols: {pos}/{len([m for m in totals if m['n'] > 0])}")
    print("=" * W)


# ── Section C: By bucket ──────────────────────────────────────────────────────

def _print_by_bucket(trades: List[Dict]) -> None:
    print("\n" + "=" * W)
    print("  C) BY BUCKET  (pct_done -- where in the move is entry)")
    print("=" * W)
    hdr = "  {:<12}  {:>5}  {:>6}  {:>5}  {:>8}  {:>8}"
    print(hdr.format("Bucket", "N", "WR%", "PF", "TotalR", "AvgBars"))
    print(_hline())
    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        grp = [t for t in trades if t.get("bucket") == bk]
        m   = _m(grp)
        avg_bars = _safe_avg([t["bars_since_swing"] for t in grp])
        if m["n"] == 0:
            continue
        print(hdr.format(bk, m["n"], f"{m['wr']:.1f}%",
                          f"{m['pf']:.2f}", f"{m['totalr']:+.1f}R",
                          f"{avg_bars:.1f}"))
    print(_hline())
    mt  = _m(trades)
    avg = _safe_avg([t["bars_since_swing"] for t in trades])
    print(hdr.format("TOTAL", mt["n"], f"{mt['wr']:.1f}%",
                      f"{mt['pf']:.2f}", f"{mt['totalr']:+.1f}R",
                      f"{avg:.1f}"))
    print(_hline("="))
    lv = [t for t in trades if t.get("bucket") in ("Late", "VeryLate")]
    print(f"  Late+VeryLate: {len(lv)}/{len(trades)} = {len(lv)/max(len(trades),1)*100:.1f}%")
    print("=" * W)


# ── Section D: Robustness ─────────────────────────────────────────────────────

def _print_robustness(all_trades: List[Dict]) -> None:
    if not all_trades:
        return
    all_dates = sorted({t["date"] for t in all_trades})
    max_date  = datetime.strptime(all_dates[-1], "%Y-%m-%d").date()
    avail_days = (max_date - datetime.strptime(all_dates[0], "%Y-%m-%d").date()).days

    print("\n" + "=" * W)
    print("  D) ROBUSTNESS  --  Does B+C hold across different sample windows?")
    print(f"     Available data span: {all_dates[0]} to {all_dates[-1]}"
          f"  ({avail_days} calendar days)")
    print(f"     Note: yfinance 15m limit is ~60 days; windows > 60d = same as max")
    print("=" * W)
    hdr = "  {:<14}  {:>12}  {:>5}  {:>6}  {:>5}  {:>8}  {:>6}  {:>6}"
    print(hdr.format("Window", "Date range", "N", "WR%", "PF",
                      "TotalR", "LV%", "N/day"))
    print(_hline())

    for label, n_days in ROBUSTNESS_WINDOWS:
        if n_days >= 9999:
            subset = all_trades
            cutoff = all_dates[0]
        else:
            cutoff_dt = max_date - timedelta(days=n_days)
            cutoff    = cutoff_dt.strftime("%Y-%m-%d")
            subset    = [t for t in all_trades if t["date"] >= cutoff]

        m    = _m(subset)
        if not subset:
            print(hdr.format(label, "no data", 0, "-", "-", "-", "-", "-"))
            continue

        sub_dates = sorted({t["date"] for t in subset})
        date_rng  = f"{sub_dates[0][5:]} to {sub_dates[-1][5:]}"
        n_td      = len(sub_dates)
        tpd       = m["n"] / max(n_td, 1)
        note      = ""
        if n_days > avail_days:
            note = " *"

        print(hdr.format(
            label + note, date_rng,
            m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
            f"{m['totalr']:+.1f}R", f"{m['lv']:.1f}%", f"{tpd:.2f}",
        ))

    print(_hline())
    print("  * Window exceeds available 15m history -- result = max available")
    print("=" * W)

    # Verdict
    subsets = {}
    for label, n_days in ROBUSTNESS_WINDOWS:
        if n_days >= 9999:
            subsets[label] = all_trades
        else:
            cutoff_dt = max_date - timedelta(days=n_days)
            cutoff    = cutoff_dt.strftime("%Y-%m-%d")
            subsets[label] = [t for t in all_trades if t["date"] >= cutoff]

    pfs = [_m(s)["pf"] for s in subsets.values() if len(s) >= 5]
    wrs = [_m(s)["wr"] for s in subsets.values() if len(s) >= 5]
    if pfs:
        all_positive = all(p >= 1.0 for p in pfs)
        pf_range     = f"{min(pfs):.2f} - {max(pfs):.2f}"
        wr_range     = f"{min(wrs):.1f}% - {max(wrs):.1f}%"
        verdict      = "ROBUST" if all_positive else "NOT ROBUST"
        print(f"\n  Robustness verdict : {verdict}")
        print(f"  PF range across windows  : {pf_range}")
        print(f"  WR range across windows  : {wr_range}")


# ── Bonus: B+C timing breakdown ───────────────────────────────────────────────

def _print_bc_timing(trades: List[Dict]) -> None:
    """When in window do B and C fire, and which fires first?"""
    print("\n" + "=" * W)
    print("  SIGNAL TIMING  --  B+C firing offsets and sequence")
    print("=" * W)

    # Sequence split
    for seq in ["B_then_C", "C_then_B", "same_bar"]:
        grp = [t for t in trades if t.get("sequence") == seq]
        m   = _m(grp)
        if m["n"] == 0:
            continue
        label = {"B_then_C": "B fires first, C confirms",
                 "C_then_B": "C fires first, B confirms",
                 "same_bar": "B and C same bar"}.get(seq, seq)
        print(f"  {label:<36}  N={m['n']:>3}  WR={m['wr']:.1f}%"
              f"  PF={m['pf']:.2f}  TotalR={m['totalr']:+.1f}R")

    print(_hline())

    # Entry offset distribution
    hdr = "  {:<14}" + "  {:>8}" * 5
    print(hdr.format("Entry offset", "+2", "+3", "+4", "+5", "+6"))
    print(_hline())
    row_b  = ["B fires at"]
    row_c  = ["C fires at"]
    row_e  = ["Entry at"]
    for off in range(CONF_START, CONF_END + 1):
        row_b.append(str(sum(1 for t in trades if t.get("b_offset") == off)))
        row_c.append(str(sum(1 for t in trades if t.get("c_offset") == off)))
        row_e.append(str(sum(1 for t in trades if t.get("entry_offset") == off)))
    print(hdr.format(*row_b))
    print(hdr.format(*row_c))
    print(_hline())
    print(hdr.format(*row_e))

    # WR by entry offset
    print(_hline())
    print("  WR by entry offset:")
    for off in range(CONF_START, CONF_END + 1):
        grp = [t for t in trades if t.get("entry_offset") == off]
        m   = _m(grp)
        if m["n"] == 0:
            continue
        print(f"    +{off}:  N={m['n']:>3}  WR={m['wr']:.1f}%"
              f"  PF={m['pf']:.2f}  TotalR={m['totalr']:+.1f}R")
    print("=" * W)


# ── CSV export ────────────────────────────────────────────────────────────────

def _write_csv(trades: List[Dict]) -> None:
    if not trades:
        return
    fields = [
        "symbol", "direction", "date", "time",
        "b_offset", "c_offset", "entry_offset", "sequence",
        "b_any", "b_qual", "disp_qual", "htf_strength",
        "session_min", "confluence",
        "bars_since_swing", "pct_done", "bucket",
        "result", "r", "mfe_r", "mae_r",
    ]
    path = "backtest_bc_research.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in trades:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"\n  CSV -> {path}  ({len(trades)} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  B+C RESEARCH BACKTEST  --  Displacement + Structure Break")
    print(f"  Symbols  : {', '.join(SYMBOLS)}")
    print(f"  Download : {DOWNLOAD_DAYS} days (yfinance 15m hard limit ~60 days)")
    print(f"  Window   : +{CONF_START} to +{CONF_END} bars after birth")
    print(f"  Gates    : htf>{HTF_MIN_STRENGTH}  bq>={PRE_B_QUAL_MIN}"
          f"  dq>={PRE_DISP_QUAL_MIN}  age<={PRE_BIRTH_AGE_MAX}"
          f"  session<{SESSION_CUTOFF}")
    print(f"  TP1={TP1_R}R  Stop=zone or birth-extreme - {STOP_BUFFER}xATR")
    print(f"  Run : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W + "\n")

    all_trades: List[Dict] = []

    for sym in SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download(sym)
        if dl is None:
            print("SKIP"); continue
        c15_all, c1h_all = dl

        trades = _scan_symbol(sym, c15_all, c1h_all)
        all_trades.extend(trades)

        wins = sum(1 for t in trades if t["result"] == "WIN")
        loss = sum(1 for t in trades if t["result"] == "LOSS")
        print(f"OK  {len(trades):>3} BC trades  "
              f"WR={wins/max(wins+loss,1)*100:.0f}%")

    if not all_trades:
        print("\nNo B+C trades found."); return

    print(f"\n  Total B+C trades: {len(all_trades)}")

    _print_overall(all_trades)
    _print_by_symbol(all_trades)
    _print_by_bucket(all_trades)
    _print_robustness(all_trades)
    _print_bc_timing(all_trades)
    _write_csv(all_trades)

    # Final one-liner
    m = _m(all_trades)
    print(f"\n  {'-' * (W-2)}")
    print(f"  B+C RESULT:  N={m['n']}  WR={m['wr']:.1f}%  PF={m['pf']:.2f}"
          f"  TotalR={m['totalr']:+.1f}R  LV={m['lv']:.1f}%")
    print(f"  {'-' * (W-2)}")


if __name__ == "__main__":
    main()
