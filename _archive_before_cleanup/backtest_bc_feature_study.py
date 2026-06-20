# -*- coding: utf-8 -*-
"""
backtest_bc_feature_study.py  --  B+C Feature Separation Study
===============================================================
Approved 2026-06-20.

Goal: find which real-time features distinguish Early-bucket B+C
trades (WR=81.8%) from Mid-bucket trades (WR=35.3%).

All B+C scan logic is identical to backtest_bc_research.py.
Only addition: at the moment B+C fires, 15 features are captured
at the ENTRY BAR (no future data).

Features measured at entry bar:
  1  birth_age          bars from swing to birth bar
  2  bars_after_birth   offset from birth to entry  (+2 to +6)
  3  bars_since_swing   bars from swing to entry
  4  birth_quality      birth bar body / ATR
  5  disp_quality       displacement quality at birth bar
  6  htf_strength       direction-adjusted EMA20/EMA50 gap on 1H
  7  atr_pct            ATR / entry_price * 100 (relative volatility)
  8  vol_ratio          entry bar volume / 20-bar avg volume
  9  session_min        minutes from 9:30 ET at entry bar
 10  dist_birth         (entry - birth_close) / ATR  (direction-adj)
 11  dist_struct        (entry - h_struct)   / ATR  (direction-adj)
 12  entry_body_atr     entry bar body / ATR
 13  entry_close_loc    (close - low) / range of entry bar
 14  trend_slope        price change last 5 bars / ATR / 5  (dir-adj)
 15  vol_regime         avg_range_5bar / avg_range_20bar

Analysis:
  A) Feature table: Early mean vs Mid mean, effect size (Cohen's d)
  B) Ranked by |d| (separation power)
  C) Rule tests: for top features, test single threshold rules
     Threshold = midpoint of Early and Mid means (not tuned on outcome)

WARNING: N_Early=11, N_Mid=25 -- effect sizes are noisy at this sample
size.  All findings are directional signals only, not conclusions.

Do not modify analyzer_x2.  Research only.
"""
from __future__ import annotations

import csv
import sys
import warnings
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
        SWING_LOOKBACK, SWING_LOOKFWD, SIM_BARS,
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


# ── Symbol universe (same as validation study) ────────────────────────────────

SYMBOLS: List[str] = [
    "AAPL", "AMD", "TSLA", "AVGO", "COST", "LLY", "PANW", "CRM",
    "QQQ",  "SPY", "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
]

# ── Constants (identical to backtest_bc_research.py -- do not change) ─────────

DOWNLOAD_DAYS      = 55
CONF_START         = 2
CONF_END           = 6
POOL_COOLDOWN      = 16
PRE_B_QUAL_MIN     = 0.55
PRE_DISP_QUAL_MIN  = 0.35
PRE_BIRTH_AGE_MAX  = 15
HTF_MIN_STRENGTH   = 0.30
SESSION_CUTOFF     = 525
STOP_BUFFER        = 0.22
DISPLACE_BODY_MIN  = 0.50
DISPLACE_CLOSE_MIN = 0.65

W = 90


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
        print(f"  {e}"); return None
    if df15 is None or len(df15) < MIN_HISTORY:
        return None
    c15 = _to_candles(df15)
    c1h = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []
    return c15, c1h


# ── Feature capture ───────────────────────────────────────────────────────────

def _features_at_entry(
    c15_all:      List[Candle],
    c1h_all:      List[Candle],
    direction:    str,
    swing_global: int,
    birth_global: int,
    entry_global: int,
    entry_px:     float,
    atr:          float,
    b_any:        int,
    b_qual:       float,
    disp_qual:    float,
    htf_str:      float,
    h_or_l_struct:float,    # h_struct for LONG, l_struct for SHORT
) -> Dict[str, float]:

    eb = c15_all[entry_global]    # entry bar candle
    bc = c15_all[birth_global]    # birth bar candle
    ts = eb.timestamp

    # -- 1: birth_age -------------------------------------------------------
    birth_age = float(b_any)

    # -- 2: bars_after_birth ------------------------------------------------
    bars_after_birth = float(entry_global - birth_global)

    # -- 3: bars_since_swing ------------------------------------------------
    bars_since_swing = float(entry_global - swing_global)

    # -- 4,5,6: already provided -------------------------------------------
    # b_qual, disp_qual, htf_str passed in

    # -- 7: atr_pct ---------------------------------------------------------
    atr_pct = atr / max(entry_px, 1e-9) * 100.0

    # -- 8: vol_ratio (entry bar volume / 20-bar avg) ----------------------
    prev = c15_all[max(0, entry_global - 20) : entry_global]
    vols = [getattr(c, "volume", 0) or 0 for c in prev]
    avg_vol = sum(vols) / max(len(vols), 1)
    entry_vol = getattr(eb, "volume", 0) or 0
    vol_ratio = float(entry_vol) / max(float(avg_vol), 1e-9)

    # -- 9: session_min at entry bar ----------------------------------------
    session_min = float((ts.hour - 9) * 60 + ts.minute - 30)

    # -- 10: dist_birth (direction-adjusted) --------------------------------
    if direction == "LONG":
        dist_birth = (entry_px - bc.close) / max(atr, 1e-9)
    else:
        dist_birth = (bc.close - entry_px) / max(atr, 1e-9)

    # -- 11: dist_struct (direction-adjusted) --------------------------------
    # For LONG: entry above h_struct = positive
    # For SHORT: entry below l_struct = positive
    if direction == "LONG":
        dist_struct = (entry_px - h_or_l_struct) / max(atr, 1e-9)
    else:
        dist_struct = (h_or_l_struct - entry_px) / max(atr, 1e-9)

    # -- 12: entry_body_atr -------------------------------------------------
    entry_body_atr = abs(eb.close - eb.open) / max(atr, 1e-9)

    # -- 13: entry_close_loc ------------------------------------------------
    rng = eb.high - eb.low
    entry_close_loc = (eb.close - eb.low) / max(rng, 1e-9)
    if direction == "SHORT":
        entry_close_loc = 1.0 - entry_close_loc   # flip: 1 = strong down close

    # -- 14: trend_slope (5-bar price change / ATR / 5, direction-adj) ------
    lo = max(0, entry_global - 5)
    price_5_ago = c15_all[lo].close
    slope = (eb.close - price_5_ago) / max(atr, 1e-9) / max(entry_global - lo, 1)
    if direction == "SHORT":
        slope = -slope

    # -- 15: vol_regime (avg_range_5 / avg_range_20) -------------------------
    ranges20 = [c.high - c.low for c in c15_all[max(0, entry_global - 20) : entry_global]]
    ranges5  = [c.high - c.low for c in c15_all[max(0, entry_global - 5)  : entry_global]]
    avg20 = sum(ranges20) / max(len(ranges20), 1)
    avg5  = sum(ranges5)  / max(len(ranges5),  1)
    vol_regime = avg5 / max(avg20, 1e-9)

    return {
        "birth_age":       birth_age,
        "bars_after_birth":bars_after_birth,
        "bars_since_swing":bars_since_swing,
        "birth_quality":   b_qual,
        "disp_quality":    disp_qual,
        "htf_strength":    htf_str,
        "atr_pct":         round(atr_pct, 4),
        "vol_ratio":       round(vol_ratio, 4),
        "session_min":     session_min,
        "dist_birth":      round(dist_birth, 4),
        "dist_struct":     round(dist_struct, 4),
        "entry_body_atr":  round(entry_body_atr, 4),
        "entry_close_loc": round(entry_close_loc, 4),
        "trend_slope":     round(slope, 4),
        "vol_regime":      round(vol_regime, 4),
    }


# ── B+C scanner with feature capture ─────────────────────────────────────────
# Logic is IDENTICAL to backtest_bc_research._scan_symbol.
# ONLY addition: _features_at_entry() call at the B+C fire point.

def _scan_with_features(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:

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

            sw_price, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            swing_local  = len(c15) - 1 - bars_since
            swing_global = i - bars_since

            b_exp = _cap(_birth_expand(c15, swing_local, direction, atr), bars_since)
            b_vol = _cap(_birth_volume(c15, swing_local),                  bars_since)
            b_str = _cap(_birth_structure(c15, swing_local, direction),    bars_since)
            b_any = min(b_exp, b_vol, b_str)

            birth_global     = swing_global + b_any
            bars_after_birth = i - birth_global

            if bars_after_birth != 0:
                continue
            if b_any > PRE_BIRTH_AGE_MAX:
                continue
            if birth_global <= last_birth[direction]:
                continue
            if not (0 <= birth_global < len(c15_all)):
                continue

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

            h_src    = c1h if len(c1h) >= 50 else c15[-100:]
            h_prices = [c.close for c in h_src]
            raw_htf  = (((_ema(h_prices, 20) - _ema(h_prices, 50))
                         / max(abs(_ema(h_prices, 50)), 1e-9)) * 100.0)
            htf_str  = raw_htf if direction == "LONG" else -raw_htf
            if htf_str <= HTF_MIN_STRENGTH:
                continue

            h_u, m_u    = ts.hour, ts.minute
            session_min_birth = (h_u - 9) * 60 + m_u - 30
            if session_min_birth >= SESSION_CUTOFF:
                continue

            near    = b_any + 2
            b1_fire = b_exp <= near
            b2_fire = b_vol <= near
            b3_fire = b_str <= near
            if not (b1_fire or b2_fire or b3_fire):
                continue

            last_birth[direction] = birth_global

            # Stop placement (identical to research file)
            zone = _detect_zone(zone_setup, direction, atr,
                                max(0, len(zone_setup) - 1 - bars_since))
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

            # Structural level (Signal C threshold)
            birth_area = c15_all[swing_global : birth_global + 1]
            if not birth_area:
                continue
            if direction == "LONG":
                h_struct = max(c.high for c in birth_area)
                struct_level = h_struct
            else:
                l_struct = min(c.low for c in birth_area)
                struct_level = l_struct

            # Confirmation window +2..+6
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
                else:
                    body2     = cb2.open - cb2.close
                    dir_close = (cb2.high - cb2.close) / rng
                    if b_offset is None:
                        if (cb2.close < cb2.open
                                and body2     >= DISPLACE_BODY_MIN  * atr
                                and dir_close >= DISPLACE_CLOSE_MIN):
                            b_offset = offset
                    if c_offset is None and cb2.close < l_struct:
                        c_offset = offset

                if b_offset is not None and c_offset is not None:
                    entry_offset = max(b_offset, c_offset)
                    entry_idx    = birth_global + entry_offset
                    entry_px     = c15_all[entry_idx].close

                    # Simulation (identical to research file)
                    risk = abs(entry_px - stop_price)
                    if risk <= 1e-9:
                        break
                    tp1    = (entry_px + risk * TP1_R if direction == "LONG"
                              else entry_px - risk * TP1_R)
                    result = "BE"; r_mult = 0.0
                    mfe = 0.0; mae = 0.0
                    for fc in c15_all[entry_idx + 1 : entry_idx + SIM_BARS + 1]:
                        if direction == "LONG":
                            mfe = max(mfe, (fc.high - entry_px) / risk)
                            mae = min(mae, (fc.low  - entry_px) / risk)
                            if fc.low  <= stop_price: result="LOSS"; r_mult=-1.0; break
                            if fc.high >= tp1:        result="WIN";  r_mult=TP1_R; break
                        else:
                            mfe = max(mfe, (entry_px - fc.low)  / risk)
                            mae = min(mae, (entry_px - fc.high) / risk)
                            if fc.high >= stop_price: result="LOSS"; r_mult=-1.0; break
                            if fc.low  <= tp1:        result="WIN";  r_mult=TP1_R; break

                    bars_sw = entry_idx - swing_global
                    future  = c15_all[entry_idx + 1 : entry_idx + 1 + SWING_LOOKFWD]
                    sw_end  = _swing_end(future, direction)
                    if sw_end is not None:
                        total_mv = ((sw_end - sw_price) if direction == "LONG"
                                    else (sw_price - sw_end))
                        at_entry = ((entry_px - sw_price) if direction == "LONG"
                                    else (sw_price - entry_px))
                        pct = round(at_entry / total_mv * 100, 1) if total_mv > 0 else 50.0
                    else:
                        pct = 50.0

                    bkt = _bucket(pct)

                    # ── Feature capture (addition) ─────────────────────
                    feats = _features_at_entry(
                        c15_all, c1h_all, direction,
                        swing_global, birth_global, entry_idx,
                        entry_px, atr,
                        b_any, b_qual, disp_qual, htf_str,
                        struct_level,
                    )

                    seq = ("B_then_C" if b_offset < c_offset else
                           ("C_then_B" if c_offset < b_offset else "same_bar"))

                    results.append({
                        "symbol":    symbol,
                        "direction": "CALL" if direction == "LONG" else "PUT",
                        "date":      ts.strftime("%Y-%m-%d"),
                        "time":      ts.strftime("%H:%M"),
                        "b_offset":  b_offset,
                        "c_offset":  c_offset,
                        "entry_offset": entry_offset,
                        "sequence":  seq,
                        "bars_since_swing": bars_sw,
                        "pct_done":  pct,
                        "bucket":    bkt,
                        "result":    result,
                        "r":         round(r_mult, 2),
                        "mfe_r":     round(mfe, 2),
                        "mae_r":     round(mae, 2),
                        **feats,
                    })
                    break

    return results


# ── Statistics ────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    "birth_age", "bars_after_birth", "bars_since_swing",
    "birth_quality", "disp_quality", "htf_strength",
    "atr_pct", "vol_ratio", "session_min",
    "dist_birth", "dist_struct",
    "entry_body_atr", "entry_close_loc",
    "trend_slope", "vol_regime",
]

FEATURE_LABELS = {
    "birth_age":        "Birth age (bars, swing to birth)",
    "bars_after_birth": "Bars after birth to entry (+2..+6)",
    "bars_since_swing": "Bars since swing to entry",
    "birth_quality":    "Birth quality (body/ATR)",
    "disp_quality":     "Displacement quality",
    "htf_strength":     "HTF EMA strength (dir-adj)",
    "atr_pct":          "ATR as % of price",
    "vol_ratio":        "Entry bar vol / 20-bar avg",
    "session_min":      "Session min from 9:30 ET (entry bar)",
    "dist_birth":       "Dist from birth close / ATR (dir-adj)",
    "dist_struct":      "Dist above struct break / ATR",
    "entry_body_atr":   "Entry bar body / ATR",
    "entry_close_loc":  "Entry bar close location (dir-adj)",
    "trend_slope":      "5-bar trend slope / ATR (dir-adj)",
    "vol_regime":       "Vol regime (avg5bar_range / avg20bar_range)",
}


def _mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0

def _std(vals: List[float]) -> float:
    if len(vals) < 2: return 0.0
    m = _mean(vals)
    return (sum((x-m)**2 for x in vals) / (len(vals)-1)) ** 0.5

def _cohens_d(g1: List[float], g2: List[float]) -> float:
    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2: return 0.0
    m1, m2 = _mean(g1), _mean(g2)
    v1 = sum((x-m1)**2 for x in g1) / (n1-1)
    v2 = sum((x-m2)**2 for x in g2) / (n2-1)
    pooled = ((v1*(n1-1) + v2*(n2-1)) / (n1+n2-2)) ** 0.5
    return (m1 - m2) / pooled if pooled > 0 else 0.0

def _metrics(trades: List[Dict]) -> Dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0}
    wins = sum(1 for t in trades if t["result"] == "WIN")
    loss = sum(1 for t in trades if t["result"] == "LOSS")
    gw   = sum(float(t["r"]) for t in trades if float(t["r"]) > 0)
    gl   = abs(sum(float(t["r"]) for t in trades if float(t["r"]) < 0))
    dec  = wins + loss
    return {
        "n":      n,
        "wr":     wins / dec * 100 if dec else 0.0,
        "pf":     gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr": sum(float(t["r"]) for t in trades),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def _hline(c: str = "-") -> str:
    return "  " + c * (W - 2)


def _print_feature_table(trades: List[Dict]) -> List[Tuple[str, float, float, float]]:
    """Print Early vs Mid feature comparison. Returns [(feat, d, early_mean, mid_mean)]."""

    early = [t for t in trades if t.get("bucket") == "Early"]
    mid   = [t for t in trades if t.get("bucket") == "Mid"]

    print("\n" + "=" * W)
    print(f"  A) FEATURE COMPARISON  --  Early (N={len(early)}) vs Mid (N={len(mid)})")
    print(f"     Cohen's d: |d|>=0.50 = medium, |d|>=0.80 = large effect")
    print(f"     WARNING: N_Early={len(early)} -- effect sizes are noisy at this sample size")
    print("=" * W)

    hdr = "  {:<34}  {:>8}  {:>8}  {:>8}  {:>7}"
    print(hdr.format("Feature", "Early", "Mid", "Sep.Ratio", "Cohen.d"))
    print(_hline())

    rows = []
    for feat in FEATURE_COLS:
        e_vals = [float(t.get(feat, 0)) for t in early]
        m_vals = [float(t.get(feat, 0)) for t in mid]
        if not e_vals or not m_vals:
            continue
        e_mean = _mean(e_vals)
        m_mean = _mean(m_vals)
        sep    = e_mean / m_mean if m_mean != 0 else 999.0
        d      = _cohens_d(e_vals, m_vals)
        rows.append((feat, d, e_mean, m_mean))

    # Print sorted by |d| descending
    for feat, d, e_mean, m_mean in sorted(rows, key=lambda x: -abs(x[1])):
        label = FEATURE_LABELS.get(feat, feat)
        sep   = e_mean / m_mean if m_mean != 0 else 999.0
        star  = "***" if abs(d) >= 0.80 else ("** " if abs(d) >= 0.50 else "   ")
        print(hdr.format(
            label[:34], f"{e_mean:.3f}", f"{m_mean:.3f}",
            f"{sep:.2f}x", f"{d:+.2f} {star}",
        ))

    print("=" * W)
    return sorted(rows, key=lambda x: -abs(x[1]))


def _print_rule_tests(
    trades:      List[Dict],
    ranked_feats: List[Tuple[str, float, float, float]],
) -> None:
    """
    For each top feature, set threshold = midpoint(early_mean, mid_mean).
    Direction determined by which side Early trades fall on.
    Test the rule on ALL trades. Report Approved N, WR, PF, TotalR.
    Threshold is NOT tuned on outcome -- it is purely descriptive.
    """
    early = [t for t in trades if t.get("bucket") == "Early"]
    mid   = [t for t in trades if t.get("bucket") == "Mid"]

    print("\n" + "=" * W)
    print("  B) RULE TESTS  --  single-feature threshold rules")
    print(f"     Threshold = midpoint of Early-mean and Mid-mean (NOT optimized on outcome)")
    print(f"     Full pool N={len(trades)}  baseline: "
          f"WR={_metrics(trades)['wr']:.1f}%  PF={_metrics(trades)['pf']:.2f}"
          f"  TotalR={_metrics(trades)['totalr']:+.1f}R")
    print("=" * W)

    hdr = "  {:<42}  {:>5}  {:>6}  {:>5}  {:>8}"
    print(hdr.format("Rule", "N", "WR%", "PF", "TotalR"))
    print(_hline())

    tested = 0
    for feat, d, e_mean, m_mean in ranked_feats:
        if abs(d) < 0.25:
            break   # skip weak features
        if tested >= 10:
            break

        threshold = (e_mean + m_mean) / 2.0

        # Direction: if Early_mean < Mid_mean, rule is feat <= threshold
        if e_mean <= m_mean:
            rule_str = f"{feat} <= {threshold:.2f}"
            approved = [t for t in trades if float(t.get(feat, 0)) <= threshold]
        else:
            rule_str = f"{feat} >= {threshold:.2f}"
            approved = [t for t in trades if float(t.get(feat, 0)) >= threshold]

        m = _metrics(approved)
        flag = ""
        if m["pf"] >= 2.0 and m["n"] >= 8:
            flag = "  *** strong"
        elif m["pf"] >= 1.50 and m["n"] >= 8:
            flag = "  ** notable"
        elif m["pf"] >= 1.20 and m["n"] >= 8:
            flag = "  * above baseline"
        print(hdr.format(rule_str[:42], m["n"],
                          f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                          f"{m['totalr']:+.1f}R") + flag)
        tested += 1

    # Also test Early-bucket-specific thresholds for top features
    print(_hline())
    print("  Early-cluster thresholds (= Early-mean rounded to nearest 0.5 or integer):")
    print(_hline())

    for feat, d, e_mean, m_mean in ranked_feats[:6]:
        if abs(d) < 0.25:
            continue
        # Round to a clean number
        if feat in ("birth_age", "bars_after_birth", "bars_since_swing", "session_min"):
            thresh = round(e_mean)
        else:
            thresh = round(e_mean * 2) / 2

        if e_mean <= m_mean:
            rule_str = f"{feat} <= {thresh}"
            approved = [t for t in trades if float(t.get(feat, 0)) <= thresh]
        else:
            rule_str = f"{feat} >= {thresh}"
            approved = [t for t in trades if float(t.get(feat, 0)) >= thresh]

        m = _metrics(approved)
        print(hdr.format(rule_str[:42], m["n"],
                          f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                          f"{m['totalr']:+.1f}R"))

    print("=" * W)


def _print_combination_test(
    trades:       List[Dict],
    ranked_feats: List[Tuple[str, float, float, float]],
) -> None:
    """Test top-2 and top-3 feature combinations using midpoint thresholds."""
    print("\n" + "=" * W)
    print("  C) COMBINATION RULES  --  top features combined (AND logic)")
    print(f"     Same midpoint thresholds as above -- NOT optimized")
    print("=" * W)

    hdr = "  {:<50}  {:>5}  {:>6}  {:>5}  {:>8}"
    print(hdr.format("Rule combination", "N", "WR%", "PF", "TotalR"))
    print(_hline())

    # Build threshold rules for top-5 features
    rules = []
    for feat, d, e_mean, m_mean in ranked_feats:
        if abs(d) < 0.25 or len(rules) >= 5:
            break
        threshold = (e_mean + m_mean) / 2.0
        direction = "<=" if e_mean <= m_mean else ">="
        rules.append((feat, direction, threshold))

    from itertools import combinations
    for r in [2, 3, 4]:
        if r > len(rules):
            break
        best_pf = 0.0
        best_combo = None
        best_m = None
        for combo in combinations(rules, r):
            def passes(t, c=combo):
                for feat, op, thr in c:
                    v = float(t.get(feat, 0))
                    if op == "<=" and v > thr: return False
                    if op == ">=" and v < thr: return False
                return True
            approved = [t for t in trades if passes(t)]
            m = _metrics(approved)
            if m["n"] >= 5 and m["pf"] > best_pf:
                best_pf = m["pf"]
                best_combo = combo
                best_m = m

        if best_combo and best_m:
            rule_str = " AND ".join(f"{f}{op}{thr:.2f}" for f,op,thr in best_combo)
            flag = ""
            if best_m["pf"] >= 2.0:
                flag = "  *** strong"
            elif best_m["pf"] >= 1.5:
                flag = "  ** notable"
            print(hdr.format(
                rule_str[:50], best_m["n"],
                f"{best_m['wr']:.1f}%", f"{best_m['pf']:.2f}",
                f"{best_m['totalr']:+.1f}R",
            ) + flag)

    print(_hline("="))
    print("  Note: showing BEST N-feature combo by PF, with N >= 5 trades.")
    print("  These are hypotheses for further research, not production rules.")
    print("=" * W)


def _print_bucket_feature_summary(trades: List[Dict]) -> None:
    """Per-bucket mean for top features -- shows the full gradient."""
    ranked_cols = FEATURE_COLS[:8]   # first 8 for readability

    print("\n" + "=" * W)
    print("  D) BUCKET-BY-BUCKET FEATURE MEANS  (gradient view)")
    print("=" * W)

    buckets = ["Early", "Mid", "Late", "VeryLate"]
    grps    = {bk: [t for t in trades if t.get("bucket") == bk] for bk in buckets}

    hdr = "  {:<26}" + "  {:>11}" * 4
    print(hdr.format("Feature", *[f"{bk} N={len(grps[bk])}" for bk in buckets]))
    print(_hline())

    for feat in FEATURE_COLS:
        label = FEATURE_LABELS.get(feat, feat)[:26]
        row = [label]
        for bk in buckets:
            vals = [float(t.get(feat, 0)) for t in grps[bk]]
            row.append(f"{_mean(vals):.3f}" if vals else "-")
        print(hdr.format(*row))

    print("=" * W)


def _write_csv(trades: List[Dict]) -> None:
    if not trades:
        return
    base_fields = ["symbol", "direction", "date", "time",
                   "b_offset", "c_offset", "entry_offset", "sequence",
                   "bars_since_swing", "pct_done", "bucket",
                   "result", "r", "mfe_r", "mae_r"]
    all_fields = base_fields + FEATURE_COLS
    path = "backtest_bc_features.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        for row in trades:
            w.writerow({k: row.get(k, "") for k in all_fields})
    print(f"\n  CSV -> {path}  ({len(trades)} rows x {len(all_fields)} columns)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  B+C FEATURE SEPARATION STUDY")
    print(f"  Symbols  : {len(SYMBOLS)} (same as validation)")
    print(f"  Goal     : distinguish Early-bucket from Mid-bucket B+C trades")
    print(f"  Run      : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W + "\n")

    all_trades: List[Dict] = []

    for sym in SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download(sym)
        if dl is None:
            print("SKIP"); continue
        c15_all, c1h_all = dl

        trades = _scan_with_features(sym, c15_all, c1h_all)
        all_trades.extend(trades)

        wins = sum(1 for t in trades if t["result"] == "WIN")
        loss = sum(1 for t in trades if t["result"] == "LOSS")
        bkts = {bk: sum(1 for t in trades if t.get("bucket") == bk)
                for bk in ["Early", "Mid", "Late", "VeryLate"]}
        print(f"OK  {len(trades):>3} BC  WR={wins/max(wins+loss,1)*100:.0f}%  "
              f"E={bkts['Early']} M={bkts['Mid']} L={bkts['Late']} VL={bkts['VeryLate']}")

    if not all_trades:
        print("\nNo B+C trades."); return

    n_e  = sum(1 for t in all_trades if t.get("bucket") == "Early")
    n_m  = sum(1 for t in all_trades if t.get("bucket") == "Mid")
    n_l  = sum(1 for t in all_trades if t.get("bucket") == "Late")
    n_vl = sum(1 for t in all_trades if t.get("bucket") == "VeryLate")
    print(f"\n  Total: {len(all_trades)} trades  "
          f"Early={n_e}  Mid={n_m}  Late={n_l}  VeryLate={n_vl}")

    ranked = _print_feature_table(all_trades)
    _print_rule_tests(all_trades, ranked)
    _print_combination_test(all_trades, ranked)
    _print_bucket_feature_summary(all_trades)
    _write_csv(all_trades)

    # Final summary
    m_all = _metrics(all_trades)
    m_e   = _metrics([t for t in all_trades if t.get("bucket") == "Early"])
    print(f"\n  {'-'*(W-2)}")
    print(f"  All B+C:     N={m_all['n']}  WR={m_all['wr']:.1f}%  PF={m_all['pf']:.2f}"
          f"  TotalR={m_all['totalr']:+.1f}R")
    print(f"  Early only:  N={m_e['n']}   WR={m_e['wr']:.1f}%  PF={m_e['pf']:.2f}"
          f"  TotalR={m_e['totalr']:+.1f}R")
    print(f"  {'-'*(W-2)}")


if __name__ == "__main__":
    main()
