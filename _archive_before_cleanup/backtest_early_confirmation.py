# -*- coding: utf-8 -*-
"""
backtest_early_confirmation.py  --  Early Confirmation Engine Diagnostic
========================================================================
Design approved 2026-06-20.  Two modifications applied:
  1. Confirmation window: bars +2 through +6 after birth bar (not +1).
  2. Zone: diagnostic field only -- no rejection, no distance gate.

Signal types tested:
  A  Birth-only   Enter at birth bar close.  Baseline.
  B  Displacement  Candle in window with body >= 0.50 ATR and close in top/
                   bottom 35% of range.
  C  Structure     Close through max(high) or min(low) of swing-to-birth range.
  D  Pullback hold Wick reaches birth midpoint, bar closes back through it.
  E  Combined      Any 2 of B/C/D fire within window.  Entry on the bar that
                   makes the count reach 2.

Pre-conditions (checked at birth bar -- hard gates):
  b_qual    >= 0.55
  disp_qual >= 0.35
  htf_str   >  0.30   (proven predictor, applied as hard gate not a score)
  session_min < 525   (last 75 min excluded, 24% WR in study)
  b_any     <= 15

Zone: detected and recorded as diagnostic fields (zone_detected, zone_type,
zone_fresh, zone_dist_atr).  No rejection based on zone.  If no zone is
detected, stop falls back to birth-bar extreme - buffer.

Birth mandatory: at least one of B1/B2/B3 must fire within b_any+2 bars.

Do not modify analyzer_x2.  Research only.
"""
from __future__ import annotations

import csv
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
        SYMBOLS, DAYS, SWING_LOOKBACK, SWING_LOOKFWD, SIM_BARS,
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

PRE_B_QUAL_MIN     = 0.55   # birth bar body / ATR
PRE_DISP_QUAL_MIN  = 0.35   # displacement quality gate
PRE_BIRTH_AGE_MAX  = 15     # max bars from swing to birth
HTF_MIN_STRENGTH   = 0.30   # direction-adjusted EMA20/EMA50 gap * 100
SESSION_CUTOFF     = 525    # session_min >= 525 = last 75 min, excluded
STOP_BUFFER        = 0.22   # ATR buffer below/above zone for stop
POOL_COOLDOWN      = 16     # bars between births per symbol per direction

CONF_START         = 2      # earliest confirmation bar (offset from birth)
CONF_END           = 6      # latest  confirmation bar (offset from birth)

DISPLACE_BODY_MIN  = 0.50   # min body as fraction of ATR
DISPLACE_CLOSE_MIN = 0.65   # min directional close fraction


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# ── Data download ─────────────────────────────────────────────────────────────

def _download(symbol: str) -> Optional[Tuple[List[Candle], List[Candle]]]:
    end   = datetime.today()
    start = end - timedelta(days=DAYS + 3)
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


# ── Core scanner ──────────────────────────────────────────────────────────────

def _scan_symbol(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:
    """
    For each birth event that passes pre-conditions, simulate:
      A  (always)       entry = birth bar close
      B  (if fires)     entry = first displacement bar in window
      C  (if fires)     entry = first structure-break bar in window
      D  (if fires)     entry = first pullback-hold bar in window
      E  (if fires)     entry = bar where 2nd of B/C/D fires

    Returns one record per (birth_event, signal_type).
    Zone fields are included as diagnostics but never used for rejection.
    """
    prof    = _get_profile(symbol)
    min_adx = float(prof.get("min_adx", 17))
    max_adx = float(prof.get("max_adx", 68))

    results: List[Dict[str, Any]]   = []
    last_birth: Dict[str, int]      = {"LONG": -999, "SHORT": -999}
    # Extra margin: birth + CONF_END + SIM_BARS + 2
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

            # ── Swing detection ───────────────────────────────────────
            sw_price, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            swing_idx_local  = len(c15) - 1 - bars_since   # within c15
            swing_idx_global = i - bars_since               # within c15_all

            # ── Birth detection ───────────────────────────────────────
            b_exp = _cap(_birth_expand(c15, swing_idx_local, direction, atr), bars_since)
            b_vol = _cap(_birth_volume(c15, swing_idx_local),                 bars_since)
            b_str = _cap(_birth_structure(c15, swing_idx_local, direction),   bars_since)
            b_any = min(b_exp, b_vol, b_str)

            birth_idx_global = swing_idx_global + b_any
            bars_after_birth = i - birth_idx_global

            # Only process at the birth bar itself
            if bars_after_birth != 0:
                continue
            if b_any > PRE_BIRTH_AGE_MAX:
                continue
            # Dedup: same birth bar already processed
            if birth_idx_global <= last_birth[direction]:
                continue
            if not (0 <= birth_idx_global < len(c15_all)):
                continue

            # ── Pre-conditions (hard gates at birth bar) ──────────────
            bc      = c15_all[birth_idx_global]   # birth candle
            b_body  = abs(bc.close - bc.open)
            b_qual  = min(1.0, b_body / max(atr, 1e-9))
            if b_qual < PRE_B_QUAL_MIN:
                continue

            b_bar_local = swing_idx_local + b_any
            try:
                disp_qual = _disp_quality(c15, b_bar_local, direction, atr)
            except Exception:
                disp_qual = 0.0
            if disp_qual < PRE_DISP_QUAL_MIN:
                continue

            # HTF strength (direction-adjusted EMA20/EMA50 gap)
            h_src    = c1h if len(c1h) >= 50 else c15[-100:]
            h_prices = [c.close for c in h_src]
            h_ema20  = _ema(h_prices, 20)
            h_ema50  = _ema(h_prices, 50)
            raw_htf  = (h_ema20 - h_ema50) / max(abs(h_ema50), 1e-9) * 100.0
            htf_str  = raw_htf if direction == "LONG" else -raw_htf
            if htf_str <= HTF_MIN_STRENGTH:
                continue

            # Session gate
            h_u, m_u    = ts.hour, ts.minute
            session_min = (h_u - 9) * 60 + m_u - 30
            if session_min >= SESSION_CUTOFF:
                continue

            # Birth must have at least one confirmed type
            near    = b_any + 2
            b1_fire = b_exp <= near
            b2_fire = b_vol <= near
            b3_fire = b_str <= near
            if not (b1_fire or b2_fire or b3_fire):
                continue

            # ── Mark birth processed ──────────────────────────────────
            last_birth[direction] = birth_idx_global

            # ── Zone (diagnostic only, no rejection) ──────────────────
            swing_in_setup = max(0, len(zone_setup) - 1 - bars_since)
            zone           = _detect_zone(zone_setup, direction, atr, swing_in_setup)
            zone_detected  = zone is not None

            if zone_detected:
                zb = _zone_bounds(zone)
                if zb:
                    z_top, z_bot = zb
                else:
                    z_top = z_bot = bc.close
                z_mid      = _zone_mid(zone)
                zone_fresh = float(getattr(zone, "freshness", 0.5) or 0.5)
                zone_type  = str(getattr(zone, "zone_type", "?"))
                zone_dist  = abs(bc.close - z_mid) / max(atr, 1e-9)
                stop_price = (z_bot - STOP_BUFFER * atr if direction == "LONG"
                              else z_top + STOP_BUFFER * atr)
            else:
                zone_fresh = 0.0; zone_type = "NONE"; zone_dist = 0.0
                z_top = z_bot = bc.close
                stop_price = (bc.low  - STOP_BUFFER * atr if direction == "LONG"
                              else bc.high + STOP_BUFFER * atr)

            # ── Structural level for Signal C ─────────────────────────
            swing_to_birth = c15_all[swing_idx_global : birth_idx_global + 1]
            if not swing_to_birth:
                continue
            if direction == "LONG":
                h_struct = max(c.high for c in swing_to_birth)
            else:
                l_struct = min(c.low  for c in swing_to_birth)

            # Birth midpoint for Signal D
            birth_mid = (bc.high + bc.low) / 2.0

            # ── Inner simulation function (closure) ───────────────────
            def _sim(entry_px: float, entry_bar: int) -> Optional[Dict]:
                risk = abs(entry_px - stop_price)
                if risk <= 1e-9:
                    return None
                tp1 = (entry_px + risk * TP1_R if direction == "LONG"
                       else entry_px - risk * TP1_R)
                result = "BE"; r_mult = 0.0
                mfe = 0.0; mae = 0.0
                for fc in c15_all[entry_bar + 1 : entry_bar + SIM_BARS + 1]:
                    if direction == "LONG":
                        mfe = max(mfe, (fc.high - entry_px) / risk)
                        mae = min(mae, (fc.low  - entry_px) / risk)
                        if fc.low  <= stop_price: result = "LOSS"; r_mult = -1.0; break
                        if fc.high >= tp1:        result = "WIN";  r_mult = TP1_R; break
                    else:
                        mfe = max(mfe, (entry_px - fc.low)  / risk)
                        mae = min(mae, (entry_px - fc.high) / risk)
                        if fc.high >= stop_price: result = "LOSS"; r_mult = -1.0; break
                        if fc.low  <= tp1:        result = "WIN";  r_mult = TP1_R; break

                bars_sw = entry_bar - swing_idx_global
                future  = c15_all[entry_bar + 1 : entry_bar + 1 + SWING_LOOKFWD]
                sw_end  = _swing_end(future, direction)
                if sw_end is not None:
                    total_mv = ((sw_end - sw_price) if direction == "LONG"
                                else (sw_price - sw_end))
                    at_entry = ((entry_px - sw_price) if direction == "LONG"
                                else (sw_price - entry_px))
                    pct = round(at_entry / total_mv * 100, 1) if total_mv > 0 else 50.0
                else:
                    pct = 50.0

                return {
                    "result":          result,
                    "r":               round(r_mult, 2),
                    "mfe_r":           round(mfe,    2),
                    "mae_r":           round(mae,    2),
                    "entry":           round(entry_px, 4),
                    "risk_r":          round(risk, 4),
                    "bars_since_swing": bars_sw,
                    "pct_done":        pct,
                    "bucket":          _bucket(pct),
                }

            # ── Common record fields ──────────────────────────────────
            common: Dict[str, Any] = {
                "symbol":       symbol,
                "direction":    "CALL" if direction == "LONG" else "PUT",
                "date":         ts.strftime("%Y-%m-%d"),
                "time":         ts.strftime("%H:%M"),
                "b_any":        b_any,
                "b_qual":       round(b_qual,    3),
                "disp_qual":    round(disp_qual, 3),
                "htf_strength": round(htf_str,   3),
                "session_min":  session_min,
                "confluence":   int(b1_fire) + int(b2_fire) + int(b3_fire),
                # Zone diagnostics
                "zone_detected": int(zone_detected),
                "zone_type":     zone_type,
                "zone_fresh":    round(zone_fresh, 3),
                "zone_dist_atr": round(zone_dist,  3),
            }

            # ── Signal A: birth-only ──────────────────────────────────
            out_a = _sim(bc.close, birth_idx_global)
            if out_a:
                results.append({**common, "signal": "A",
                                 "conf_offset": 0, **out_a})

            # ── Confirmation window: +2 through +6 ────────────────────
            b_result: Optional[Tuple[int, int]] = None   # (offset, bar_global)
            c_result: Optional[Tuple[int, int]] = None
            d_result: Optional[Tuple[int, int]] = None
            e_result: Optional[Tuple[int, int]] = None
            b_done = c_done = d_done = e_done = False

            for offset in range(CONF_START, CONF_END + 1):
                conf_bar = birth_idx_global + offset
                if conf_bar >= safe_max + CONF_END:
                    break
                cb2 = c15_all[conf_bar]
                rng = max(cb2.high - cb2.low, 1e-9)

                if direction == "LONG":
                    body2     = cb2.close - cb2.open
                    dir_close = (cb2.close - cb2.low) / rng
                    sig_b = (not b_done
                             and cb2.close > cb2.open
                             and body2     >= DISPLACE_BODY_MIN  * atr
                             and dir_close >= DISPLACE_CLOSE_MIN)
                    sig_c = (not c_done and cb2.close > h_struct)
                    sig_d = (not d_done and cb2.low <= birth_mid
                             and cb2.close > birth_mid)
                else:  # SHORT
                    body2     = cb2.open - cb2.close
                    dir_close = (cb2.high - cb2.close) / rng
                    sig_b = (not b_done
                             and cb2.close < cb2.open
                             and body2     >= DISPLACE_BODY_MIN  * atr
                             and dir_close >= DISPLACE_CLOSE_MIN)
                    sig_c = (not c_done and cb2.close < l_struct)
                    sig_d = (not d_done and cb2.high >= birth_mid
                             and cb2.close < birth_mid)

                if sig_b and not b_done:
                    b_result = (offset, conf_bar); b_done = True
                if sig_c and not c_done:
                    c_result = (offset, conf_bar); c_done = True
                if sig_d and not d_done:
                    d_result = (offset, conf_bar); d_done = True

                # E fires when total confirmed signals reaches 2
                if not e_done and sum([b_done, c_done, d_done]) >= 2:
                    e_result = (offset, conf_bar); e_done = True

            # ── Emit confirmation records ─────────────────────────────
            for sig, res in [("B", b_result), ("C", c_result),
                              ("D", d_result), ("E", e_result)]:
                if res is None:
                    continue
                off, bar_g = res
                cb2  = c15_all[bar_g]
                out  = _sim(cb2.close, bar_g)
                if out:
                    results.append({**common, "signal": sig,
                                     "conf_offset": off, **out})

    return results


# ── Metrics ───────────────────────────────────────────────────────────────────

def _metrics(trades: List[Dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0,
                "mfe": 0.0, "mae": 0.0, "lv": 0.0, "avg_bars": 0.0}
    wins = sum(1 for t in trades if t["result"] == "WIN")
    loss = sum(1 for t in trades if t["result"] == "LOSS")
    gw   = sum(float(t["r"]) for t in trades if float(t["r"]) > 0)
    gl   = abs(sum(float(t["r"]) for t in trades if float(t["r"]) < 0))
    dec  = wins + loss
    lv   = sum(1 for t in trades if t.get("bucket") in ("Late", "VeryLate"))
    return {
        "n":        n,
        "wr":       wins / dec * 100 if dec else 0.0,
        "pf":       gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr":   sum(float(t["r"]) for t in trades),
        "mfe":      _safe_avg([float(t["mfe_r"]) for t in trades]),
        "mae":      _safe_avg([float(t["mae_r"]) for t in trades]),
        "lv":       lv / n * 100,
        "avg_bars": _safe_avg([float(t["bars_since_swing"]) for t in trades]),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

W = 94   # line width

def _hline(c: str = "-") -> str:
    return "  " + c * (W - 2)

def _print_comparison(all_results: List[Dict], n_births: int) -> None:
    sigs = ["A", "B", "C", "D", "E"]
    labels = {
        "A": "A) Birth-only          (enter at birth close)",
        "B": "B) Displacement        (body>=0.50 ATR, close in dir 65%+)",
        "C": "C) Structure break     (close through swing-to-birth range)",
        "D": "D) Pullback hold       (wick<=midpoint, close back above)",
        "E": "E) Combined 2-of-3     (any 2 of B/C/D in window)",
    }

    print("\n" + "=" * W)
    print("  EARLY CONFIRMATION ENGINE  --  SIGNAL COMPARISON")
    print(f"  Birth events after pre-screen: {n_births}")
    print(f"  Confirmation window: bars +{CONF_START} to +{CONF_END} after birth")
    print(f"  HTF gate: htf_strength > {HTF_MIN_STRENGTH}  |  Session gate: session_min < {SESSION_CUTOFF}")
    print("=" * W)

    hdr = "  {:<48}  {:>6}  {:>6}  {:>6}  {:>5}  {:>8}  {:>7}  {:>6}"
    print(hdr.format("Variant", "Sigs", "Conf%", "WR%", "PF", "TotalR",
                     "AvgMAE", "LV%"))
    print(_hline())

    for sig in sigs:
        grp = [t for t in all_results if t["signal"] == sig]
        m   = _metrics(grp)
        conf_rate = m["n"] / max(n_births, 1) * 100
        print(hdr.format(
            labels[sig],
            m["n"], f"{conf_rate:.0f}%",
            f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
            f"{m['totalr']:+.1f}R", f"{m['mae']:+.2f}R",
            f"{m['lv']:.1f}%",
        ))

    print(_hline("="))
    print(hdr.format(
        "Reference: X2 baseline (all 207 trades)",
        207, "---", "39.5%", "1.18", "+19.0R", "N/A", "75.8%",
    ))
    print(_hline("="))

    # Lift summary
    grp_a = [t for t in all_results if t["signal"] == "A"]
    ma    = _metrics(grp_a)
    print(f"\n  WR lift vs Signal A:  ", end="")
    for sig in ["B", "C", "D", "E"]:
        grp = [t for t in all_results if t["signal"] == sig]
        m   = _metrics(grp)
        lift = m["wr"] - ma["wr"]
        print(f"  {sig}={lift:+.1f}pp", end="")
    print()
    print(f"  PF lift vs Signal A:  ", end="")
    for sig in ["B", "C", "D", "E"]:
        grp = [t for t in all_results if t["signal"] == sig]
        m   = _metrics(grp)
        lift = m["pf"] - ma["pf"]
        print(f"  {sig}={lift:+.2f}", end="")
    print()
    print(f"  LV lift vs Signal A:  ", end="")
    for sig in ["B", "C", "D", "E"]:
        grp = [t for t in all_results if t["signal"] == sig]
        m   = _metrics(grp)
        lift = m["lv"] - ma["lv"]
        print(f"  {sig}={lift:+.1f}pp", end="")
    print()


def _print_bucket_table(all_results: List[Dict]) -> None:
    sigs = ["A", "B", "C", "D", "E"]
    print("\n" + "=" * W)
    print("  BUCKET BREAKDOWN  (pct_done -- diagnostic, not used in engine)")
    print("  Goal: confirmation delays entry into Mid bucket vs birth-only")
    print("=" * W)

    hdr = "  {:<14}  " + "  ".join(["{:>14}"] * 5)
    print(hdr.format("Bucket", *[f"Signal {s}" for s in sigs]))
    print(_hline())

    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        row = [bk]
        for sig in sigs:
            grp = [t for t in all_results if t["signal"] == sig
                   and t.get("bucket") == bk]
            total_sig = [t for t in all_results if t["signal"] == sig]
            pct = len(grp) / max(len(total_sig), 1) * 100
            wins = sum(1 for t in grp if t["result"] == "WIN")
            loss = sum(1 for t in grp if t["result"] == "LOSS")
            wr = wins / max(wins + loss, 1) * 100
            row.append(f"N={len(grp):>2} WR={wr:.0f}%")
        print(hdr.format(*row))

    print(_hline())
    hdr2 = "  {:<14}  " + "  ".join(["{:>14}"] * 5)
    lv_row = ["LV total"]
    for sig in sigs:
        grp = [t for t in all_results if t["signal"] == sig]
        lv  = [t for t in grp if t.get("bucket") in ("Late", "VeryLate")]
        pct = len(lv) / max(len(grp), 1) * 100
        lv_row.append(f"{pct:.0f}% ({len(lv)}/{len(grp)})")
    print(hdr2.format(*lv_row))

    avg_row = ["AvgBars"]
    for sig in sigs:
        grp = [t for t in all_results if t["signal"] == sig]
        avg = _safe_avg([float(t["bars_since_swing"]) for t in grp])
        avg_row.append(f"{avg:.1f} bars")
    print(hdr2.format(*avg_row))
    print("=" * W)


def _print_window_timing(all_results: List[Dict]) -> None:
    """When in the window (+2..+6) does each signal fire?"""
    print("\n" + "=" * W)
    print("  CONFIRMATION TIMING  -- distribution of conf_offset within window")
    print("=" * W)
    hdr = "  {:<8}" + "  {:>8}" * 5
    print(hdr.format("Offset", "Signal B", "Signal C", "Signal D", "Signal E", "(all)"))
    print(_hline())
    for off in range(CONF_START, CONF_END + 1):
        row = [f"+{off}"]
        for sig in ["B", "C", "D", "E"]:
            cnt = sum(1 for t in all_results if t["signal"] == sig
                      and t.get("conf_offset") == off)
            row.append(str(cnt) if cnt else "-")
        total = sum(1 for t in all_results if t.get("conf_offset") == off
                    and t["signal"] != "A")
        row.append(str(total) if total else "-")
        print(hdr.format(*row))
    print("=" * W)


def _print_signal_overlap(all_results: List[Dict]) -> None:
    """For birth events where multiple signals fire, compare quality."""
    # Group by (symbol, date, time, direction) = one birth event
    from collections import defaultdict
    births: Dict[str, List[str]] = defaultdict(list)
    for t in all_results:
        key = f"{t['symbol']}|{t['date']}|{t['time']}|{t['direction']}"
        births[key].append(t["signal"])

    combos = {
        "B only":    ({"B"},       set()),
        "C only":    ({"C"},       set()),
        "D only":    ({"D"},       set()),
        "B + C":     ({"B", "C"}, set()),
        "B + D":     ({"B", "D"}, set()),
        "C + D":     ({"C", "D"}, set()),
        "B + C + D": ({"B","C","D"}, set()),
    }

    print("\n" + "=" * W)
    print("  SIGNAL OVERLAP  --  quality when signals co-occur on same birth event")
    print(_hline())
    hdr = "  {:<18}  {:>5}  {:>6}  {:>5}  {:>8}"
    print(hdr.format("Co-occurrence", "N", "WR%", "PF", "TotalR"))
    print(_hline())

    for label, (require, exclude) in combos.items():
        # Find birth events where all required signals fired (and check excludes if any)
        matching_keys = {k for k, sigs in births.items()
                         if require.issubset(set(sigs))}
        # Take records for Signal E or the "primary" signal
        # For comparison, take the combined/E signal outcome when available,
        # else take the first required signal
        recs = []
        for t in all_results:
            key = f"{t['symbol']}|{t['date']}|{t['time']}|{t['direction']}"
            if key in matching_keys and t["signal"] == "E":
                recs.append(t)
        if not recs:
            # Fallback: take first required signal outcome
            first_req = sorted(require)[0]
            recs = [t for t in all_results
                    if f"{t['symbol']}|{t['date']}|{t['time']}|{t['direction']}"
                    in matching_keys and t["signal"] == first_req]

        m = _metrics(recs)
        if m["n"] == 0:
            continue
        print(hdr.format(label, m["n"], f"{m['wr']:.1f}%",
                          f"{m['pf']:.2f}", f"{m['totalr']:+.1f}R"))
    print("=" * W)


def _print_per_symbol(all_results: List[Dict], best_sig: str) -> None:
    grp = [t for t in all_results if t["signal"] == best_sig]
    print("\n" + "=" * W)
    print(f"  PER-SYMBOL  --  Signal {best_sig}  (best by PF)")
    print(_hline())
    hdr = "  {:<7}  {:>5}  {:>6}  {:>5}  {:>8}  {:>7}"
    print(hdr.format("Symbol", "N", "WR%", "PF", "TotalR", "AvgBars"))
    print(_hline())
    for sym in SYMBOLS:
        st = [t for t in grp if t["symbol"] == sym]
        m  = _metrics(st)
        print(hdr.format(sym, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                          f"{m['totalr']:+.1f}R", f"{m['avg_bars']:.1f}"))
    print(_hline())
    mt = _metrics(grp)
    print(hdr.format("TOTAL", mt["n"], f"{mt['wr']:.1f}%", f"{mt['pf']:.2f}",
                      f"{mt['totalr']:+.1f}R", f"{mt['avg_bars']:.1f}"))
    print("=" * W)


def _print_zone_diagnostic(all_results: List[Dict]) -> None:
    """Show whether zone detection correlates with outcome -- pure research."""
    grp_a = [t for t in all_results if t["signal"] == "A"]
    if not grp_a:
        return

    def pearson(x, y):
        n = len(x)
        if n < 3: return 0.0
        mx = sum(x)/n; my = sum(y)/n
        cov = sum((xi-mx)*(yi-my) for xi,yi in zip(x,y))
        vx  = sum((xi-mx)**2 for xi in x)
        vy  = sum((yi-my)**2 for yi in y)
        d   = (vx*vy)**0.5
        return round(cov/d, 4) if d else 0.0

    win_bin = [1.0 if t["result"] == "WIN" else 0.0 for t in grp_a]
    r_vals  = [float(t["r"]) for t in grp_a]
    sig_thr = 2.0 / max(len(grp_a), 1) ** 0.5

    print("\n" + "=" * W)
    print("  ZONE DIAGNOSTIC  (Signal A pool -- zone excluded from entry logic)")
    print(f"  Pearson r, threshold |r| >= {sig_thr:.3f}")
    print(_hline())

    for field, label in [
        ("zone_detected",  "Zone detected (0/1)"),
        ("zone_fresh",     "Zone freshness"),
        ("zone_dist_atr",  "Zone dist (ATR)"),
    ]:
        x = [float(t.get(field, 0)) for t in grp_a]
        rw = pearson(x, win_bin)
        rr = pearson(x, r_vals)
        star = "*" if abs(rw) >= sig_thr or abs(rr) >= sig_thr else " "
        print(f"  {label:<30}  r(win)={rw:+.3f}  r(R)={rr:+.3f}  {star}")

    # With vs without zone
    with_z    = [t for t in grp_a if t.get("zone_detected") == 1]
    without_z = [t for t in grp_a if t.get("zone_detected") == 0]
    mw = _metrics(with_z); mwo = _metrics(without_z)
    print(_hline())
    print(f"  Zone detected   : N={mw['n']:>3}  WR={mw['wr']:.1f}%  PF={mw['pf']:.2f}"
          f"  TotalR={mw['totalr']:+.1f}R")
    print(f"  No zone         : N={mwo['n']:>3}  WR={mwo['wr']:.1f}%  PF={mwo['pf']:.2f}"
          f"  TotalR={mwo['totalr']:+.1f}R")
    print("=" * W)


def _write_csv(all_results: List[Dict]) -> None:
    if not all_results:
        return
    fields = [
        "symbol", "direction", "date", "time", "signal", "conf_offset",
        "b_any", "b_qual", "disp_qual", "htf_strength", "session_min",
        "confluence",
        "zone_detected", "zone_type", "zone_fresh", "zone_dist_atr",
        "entry", "risk_r", "bars_since_swing",
        "pct_done", "bucket", "result", "r", "mfe_r", "mae_r",
    ]
    path = "backtest_early_confirmation.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in all_results:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"\n  CSV -> {path}  ({len(all_results)} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  EARLY CONFIRMATION ENGINE -- DIAGNOSTIC BACKTEST")
    print(f"  Symbols   : {', '.join(SYMBOLS)}")
    print(f"  Window    : {DAYS} days")
    print(f"  Conf win  : bars +{CONF_START} to +{CONF_END} after birth")
    print(f"  Pre-gates : bq>={PRE_B_QUAL_MIN}  dq>={PRE_DISP_QUAL_MIN}"
          f"  birth_age<={PRE_BIRTH_AGE_MAX}  htf>{HTF_MIN_STRENGTH}"
          f"  session<{SESSION_CUTOFF}")
    print(f"  Zone      : diagnostic only -- no rejection")
    print(f"  Run       : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W + "\n")

    all_results: List[Dict] = []
    total_births = 0

    for sym in SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download(sym)
        if dl is None:
            print("SKIP"); continue
        c15_all, c1h_all = dl

        recs = _scan_symbol(sym, c15_all, c1h_all)
        all_results.extend(recs)

        births = [t for t in recs if t["signal"] == "A"]
        total_births += len(births)
        b_rate = [t for t in recs if t["signal"] == "B"]
        c_rate = [t for t in recs if t["signal"] == "C"]
        d_rate = [t for t in recs if t["signal"] == "D"]
        wins_a = sum(1 for t in births if t["result"] == "WIN")
        print(f"OK  {len(births):>3} births  "
              f"A_WR={wins_a/max(len(births),1)*100:.0f}%  "
              f"B={len(b_rate)}  C={len(c_rate)}  D={len(d_rate)}")

    if not all_results:
        print("\nNo results."); return

    print(f"\n  Total births (Signal A): {total_births}")
    print(f"  Total records all signals: {len(all_results)}")

    _print_comparison(all_results, total_births)
    _print_bucket_table(all_results)
    _print_window_timing(all_results)
    _print_signal_overlap(all_results)
    _print_zone_diagnostic(all_results)

    # Best signal by PF (minimum 15 signals)
    best_sig = max(
        ["B", "C", "D", "E"],
        key=lambda s: _metrics(
            [t for t in all_results if t["signal"] == s]
        )["pf"] if len([t for t in all_results if t["signal"] == s]) >= 15 else 0
    )
    _print_per_symbol(all_results, best_sig)
    _write_csv(all_results)

    # Final summary line
    print("\n" + _hline("="))
    hdr = "  {:<8}  {:>5}  {:>6}  {:>5}  {:>8}  {:>7}  {:>6}"
    print(hdr.format("Signal", "N", "WR%", "PF", "TotalR", "AvgMAE", "LV%"))
    print(_hline())
    for sig in ["A", "B", "C", "D", "E"]:
        grp = [t for t in all_results if t["signal"] == sig]
        m   = _metrics(grp)
        print(hdr.format(sig, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                          f"{m['totalr']:+.1f}R", f"{m['mae']:+.2f}R",
                          f"{m['lv']:.1f}%"))
    print(_hline("="))


if __name__ == "__main__":
    main()
