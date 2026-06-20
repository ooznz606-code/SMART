# -*- coding: utf-8 -*-
"""
backtest_bc_winner_loser.py  --  Winner vs Loser Feature Study
==============================================================
Approved 2026-06-20.

Engine : B + C + atr_pct <= 0.52   (exact, unchanged)
Data   : chart_data/ JSON files (8 months, ~5000 bars per symbol)
Goal   : Find which real-time features statistically separate
         winners from losers.

No new filters.  No optimization.  No score changes.
Do not modify analyzer_x2.  Research only.

Features captured at B+C fire point (before outcome is known):
  1.  atr_pct          ATR / entry_price * 100  (filter var)
  2.  htf_strength     Direction-adjusted EMA20/50 gap on 1H (>= 0.30)
  3.  b_qual           Birth bar body / ATR  (0-1 capped)
  4.  birth_age        Bars swing_extreme -> birth bar  (b_any)
  5.  entry_offset     B+C offset from birth  (2-6)
  6.  bars_since_swing birth_age + entry_offset
  7.  disp_qual        Displacement quality score  (0.35-1.0)
  8.  dist_birth       (entry - birth_close) / ATR  for LONG  (extension)
  9.  dist_struct      (entry - struct_level) / ATR  for LONG  (past structure)
  10. pct_done         % of swing move completed at entry
  11. session_min      UTC minute of birth bar (240 = open, 525 = cutoff)
  12. confluence       Count of birth-type anchors near b_any  (1-3)
  13. vol_expansion    Birth bar volume / 20-bar avg volume
  14. atr_rank         Percentile of current ATR vs trailing 50 ATRs  (0-100)
  15. birth_wick_bull  Bullish wick of birth bar / ATR  (lower for LONG)
  16. birth_wick_bear  Bearish wick of birth bar / ATR  (upper for LONG)

Statistics reported:
  - Mean and median by outcome group
  - Cohen's d effect size
  - Pearson correlation with outcome
  - Spearman correlation with outcome
  - Logistic regression coefficient (standardized)
  - Composite ranking: features consistently separating winners from losers
"""
from __future__ import annotations

import json
import math
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

warnings.filterwarnings("ignore")

try:
    from scipy.stats import pearsonr, spearmanr
    SCIPY_OK = True
except ImportError:
    print("scipy not found -- Pearson/Spearman will be skipped")
    SCIPY_OK = False

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    SKLEARN_OK = True
except ImportError:
    print("scikit-learn not found -- logistic regression will be skipped")
    SKLEARN_OK = False

try:
    from backtest_runner_x2 import (
        _flat, _to_candles, _market_state_from,
        _detect_zone, _zone_bounds, _htf_direction,
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


# ── Constants (identical to backtest_bc_research.py) ─────────────────────────

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
ATR_THRESHOLD      = 0.52

SYMBOLS: List[str] = [
    "AAPL", "AMD",  "TSLA", "AVGO", "COST", "LLY",  "PANW", "CRM",
    "QQQ",  "SPY",  "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
]

CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")
W = 92

FEATURE_NAMES: List[str] = [
    "atr_pct", "htf_strength", "b_qual", "birth_age", "entry_offset",
    "bars_since_swing", "disp_qual", "dist_birth", "dist_struct",
    "pct_done", "session_min", "confluence", "vol_expansion",
    "atr_rank", "birth_wick_bull", "birth_wick_bear",
]

FEATURE_LABELS: Dict[str, str] = {
    "atr_pct":          "ATR%          (ATR/price*100)",
    "htf_strength":     "HTF strength  (EMA20/50 gap)",
    "b_qual":           "Birth quality (body/ATR)",
    "birth_age":        "Birth age     (bars swing->birth)",
    "entry_offset":     "Entry offset  (B+C offset 2-6)",
    "bars_since_swing": "Bars/swing    (birth_age+offset)",
    "disp_qual":        "Disp quality  (displacement score)",
    "dist_birth":       "Dist birth    (entry-birth)/ATR",
    "dist_struct":      "Dist struct   (entry-struct)/ATR",
    "pct_done":         "Pct done      (swing % complete)",
    "session_min":      "Session min   (UTC min of birth)",
    "confluence":       "Confluence    (anchor count 1-3)",
    "vol_expansion":    "Vol expansion (birth vol/avg vol)",
    "atr_rank":         "ATR rank      (ATR percentile 0-100)",
    "birth_wick_bull":  "Wick bull     (bullish tail/ATR)",
    "birth_wick_bear":  "Wick bear     (bearish wick/ATR)",
}


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# ── JSON loader (UTC timestamps -- session filter calibrated to UTC) ──────────

def _load_json(symbol: str, tf: str) -> Optional[List[Candle]]:
    path = os.path.join(CHART_DIR, f"{symbol}_{tf}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        print(f"  JSON error {path}: {e}"); return None
    times   = d.get("times",   [])
    opens   = d.get("opens",   [])
    highs   = d.get("highs",   [])
    lows    = d.get("lows",    [])
    closes  = d.get("closes",  [])
    volumes = d.get("volumes", [])
    n = min(len(times), len(opens), len(highs), len(lows), len(closes))
    if n == 0:
        return None
    candles: List[Candle] = []
    for i in range(n):
        try:
            dt  = datetime.strptime(times[i][:16].replace("T", " "), "%Y-%m-%d %H:%M")
            vol = float(volumes[i]) if i < len(volumes) else 0.0
            candles.append(Candle(
                timestamp=dt, open=float(opens[i]), high=float(highs[i]),
                low=float(lows[i]), close=float(closes[i]), volume=vol,
            ))
        except Exception:
            continue
    return candles if len(candles) >= 100 else None


def _load_symbol(symbol: str) -> Optional[Tuple[List[Candle], List[Candle]]]:
    c15 = _load_json(symbol, "15m")
    if c15 is None:
        return None
    c1h = _load_json(symbol, "1H") or []
    return c15, c1h


# ── Feature-capturing B+C scanner ────────────────────────────────────────────

def _atr_rank(c15_all: List[Candle], global_idx: int, atr: float, lookback: int = 50) -> float:
    """Percentile rank of current ATR vs trailing lookback ATRs (0-100)."""
    start = max(0, global_idx - lookback)
    # Approximate ATR as 14-bar rolling range proxy for speed
    recent_ranges: List[float] = []
    for j in range(start, global_idx):
        c = c15_all[j]
        prev_close = c15_all[j - 1].close if j > 0 else c.close
        tr = max(c.high - c.low,
                 abs(c.high - prev_close),
                 abs(c.low  - prev_close))
        recent_ranges.append(tr)
    if not recent_ranges:
        return 50.0
    below = sum(1 for r in recent_ranges if r <= atr)
    return below / len(recent_ranges) * 100.0


def _avg_volume(c15_all: List[Candle], global_idx: int, lookback: int = 20) -> float:
    start = max(0, global_idx - lookback)
    vols  = [c.volume for c in c15_all[start:global_idx] if c.volume > 0]
    return sum(vols) / len(vols) if vols else 1.0


def _scan_symbol(
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
            session_min = (h_u - 9) * 60 + m_u - 30
            if session_min >= SESSION_CUTOFF:
                continue

            near    = b_any + 2
            b1_fire = b_exp <= near
            b2_fire = b_vol <= near
            b3_fire = b_str <= near
            if not (b1_fire or b2_fire or b3_fire):
                continue
            confluence = int(b1_fire) + int(b2_fire) + int(b3_fire)

            last_birth[direction] = birth_global

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

            birth_area = c15_all[swing_global : birth_global + 1]
            if not birth_area:
                continue
            if direction == "LONG":
                h_struct = max(c.high for c in birth_area)
            else:
                l_struct = min(c.low  for c in birth_area)

            # Pre-compute volume expansion and ATR rank at birth bar
            avg_vol      = _avg_volume(c15_all, birth_global, 20)
            vol_expansion = bc.volume / max(avg_vol, 1.0)
            atr_rank_val  = _atr_rank(c15_all, birth_global, atr, 50)

            # Bullish / bearish wicks of birth bar
            body_hi = max(bc.open, bc.close)
            body_lo = min(bc.open, bc.close)
            if direction == "LONG":
                birth_wick_bull = (body_lo - bc.low)  / max(atr, 1e-9)
                birth_wick_bear = (bc.high - body_hi) / max(atr, 1e-9)
            else:
                birth_wick_bull = (bc.high - body_hi) / max(atr, 1e-9)
                birth_wick_bear = (body_lo - bc.low)  / max(atr, 1e-9)

            # ── Confirmation window +2..+6 ────────────────────────────────
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

                    # atr_pct filter
                    atr_pct = atr / max(entry_px, 1e-9) * 100.0

                    # Distance features
                    if direction == "LONG":
                        dist_birth  = (entry_px - bc.close)  / max(atr, 1e-9)
                        dist_struct = (entry_px - h_struct)  / max(atr, 1e-9)
                    else:
                        dist_birth  = (bc.close - entry_px)  / max(atr, 1e-9)
                        dist_struct = (l_struct - entry_px)  / max(atr, 1e-9)
                    dist_birth  = max(0.0, dist_birth)
                    dist_struct = max(0.0, dist_struct)

                    # Swing completion %
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

                    # Simulation
                    risk = abs(entry_px - stop_price)
                    if risk <= 1e-9:
                        break
                    tp1    = (entry_px + risk * TP1_R if direction == "LONG"
                              else entry_px - risk * TP1_R)
                    result = "BE"; r_mult = 0.0
                    mfe_r  = 0.0; mae_r  = 0.0
                    for fc in c15_all[entry_idx + 1 : entry_idx + SIM_BARS + 1]:
                        if direction == "LONG":
                            mfe_r = max(mfe_r, (fc.high - entry_px) / risk)
                            mae_r = min(mae_r, (fc.low  - entry_px) / risk)
                            if fc.low  <= stop_price: result="LOSS"; r_mult=-1.0; break
                            if fc.high >= tp1:        result="WIN";  r_mult=TP1_R; break
                        else:
                            mfe_r = max(mfe_r, (entry_px - fc.low)  / risk)
                            mae_r = min(mae_r, (entry_px - fc.high) / risk)
                            if fc.high >= stop_price: result="LOSS"; r_mult=-1.0; break
                            if fc.low  <= tp1:        result="WIN";  r_mult=TP1_R; break

                    results.append({
                        "symbol":       symbol,
                        "direction":    direction,
                        "date":         ts.strftime("%Y-%m-%d"),
                        "result":       result,
                        "r":            round(r_mult, 2),
                        "mfe_r":        round(mfe_r,  2),
                        "mae_r":        round(mae_r,  2),
                        "bucket":       _bucket(pct),
                        # ── features ──
                        "atr_pct":          round(atr_pct,         4),
                        "htf_strength":     round(htf_str,         3),
                        "b_qual":           round(b_qual,          3),
                        "birth_age":        b_any,
                        "entry_offset":     entry_offset,
                        "bars_since_swing": b_any + entry_offset,
                        "disp_qual":        round(disp_qual,       3),
                        "dist_birth":       round(dist_birth,      3),
                        "dist_struct":      round(dist_struct,     3),
                        "pct_done":         pct,
                        "session_min":      session_min,
                        "confluence":       confluence,
                        "vol_expansion":    round(vol_expansion,   3),
                        "atr_rank":         round(atr_rank_val,    1),
                        "birth_wick_bull":  round(birth_wick_bull, 3),
                        "birth_wick_bear":  round(birth_wick_bear, 3),
                    })
                    break

    return results


# ── Statistical helpers ───────────────────────────────────────────────────────

def _cohens_d(a: List[float], b: List[float]) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    na, nb = len(a), len(b)
    ma, mb = np.mean(a), np.mean(b)
    sa, sb = np.std(a, ddof=1), np.std(b, ddof=1)
    pooled = math.sqrt(((na - 1) * sa**2 + (nb - 1) * sb**2) / (na + nb - 2))
    return (ma - mb) / pooled if pooled > 1e-9 else 0.0


def _effect_label(d: float) -> str:
    ad = abs(d)
    if ad >= 0.80: return "large"
    if ad >= 0.50: return "medium"
    if ad >= 0.20: return "small"
    return "trivial"


def _safe_median(vals: List[float]) -> float:
    return float(np.median(vals)) if vals else 0.0


def _hline(c: str = "-") -> str:
    return "  " + c * (W - 2)


# ── Report functions ──────────────────────────────────────────────────────────

def _print_mean_median(wins: List[Dict], loss: List[Dict]) -> None:
    print("\n" + "=" * W)
    print("  1. FEATURE STATISTICS  --  WINNERS vs LOSERS")
    print(f"     Winners: N={len(wins)}    Losers: N={len(loss)}")
    print("     Direction column shows which side has higher mean for winners.")
    print("=" * W)
    hdr = "  {:<38}  {:>6} {:>6}  {:>6} {:>6}  {:>6}  {:>6}  {}"
    print(hdr.format("Feature", "W-mean", "W-med", "L-mean", "L-med",
                     "diff", "Cohen d", "size"))
    print(_hline())
    for feat in FEATURE_NAMES:
        wv = [t[feat] for t in wins if feat in t]
        lv = [t[feat] for t in loss if feat in t]
        if not wv or not lv:
            continue
        wm  = np.mean(wv);  wmed = _safe_median(wv)
        lm  = np.mean(lv);  lmed = _safe_median(lv)
        d   = _cohens_d(wv, lv)
        lbl = FEATURE_LABELS.get(feat, feat)[:38]
        diff = wm - lm
        dir_sym = "W>" if diff > 0 else ("W<" if diff < 0 else "==")
        print(hdr.format(
            lbl,
            f"{wm:6.2f}", f"{wmed:6.2f}",
            f"{lm:6.2f}", f"{lmed:6.2f}",
            f"{diff:+6.2f}",
            f"{d:+6.3f}",
            f"{_effect_label(d)} {dir_sym}",
        ))
    print("=" * W)


def _print_cohen_ranking(wins: List[Dict], loss: List[Dict]) -> None:
    rows = []
    for feat in FEATURE_NAMES:
        wv = [t[feat] for t in wins if feat in t]
        lv = [t[feat] for t in loss if feat in t]
        if not wv or not lv:
            continue
        d = _cohens_d(wv, lv)
        rows.append((feat, d, abs(d)))
    rows.sort(key=lambda x: -x[2])

    print("\n" + "=" * W)
    print("  2. FEATURE RANKING BY COHEN'S d  (|d|, higher = stronger separation)")
    print("     d >= 0.80 large, >= 0.50 medium, >= 0.20 small, < 0.20 trivial")
    print("=" * W)
    hdr = "  {:>3}  {:<38}  {:>7}  {:>8}"
    print(hdr.format("Rk", "Feature", "Cohen d", "Size"))
    print(_hline())
    for rank, (feat, d, ad) in enumerate(rows, 1):
        lbl = FEATURE_LABELS.get(feat, feat)[:38]
        print(hdr.format(rank, lbl, f"{d:+.3f}", _effect_label(d)))
    print("=" * W)
    return {feat: rank for rank, (feat, _, _) in enumerate(rows, 1)}


def _print_pearson(trades: List[Dict]) -> Optional[Dict[str, int]]:
    if not SCIPY_OK:
        print("\n  [Pearson skipped -- scipy not available]")
        return None

    y = np.array([1.0 if t["result"] == "WIN" else 0.0
                  for t in trades if t["result"] in ("WIN", "LOSS")])
    rows = []
    for feat in FEATURE_NAMES:
        xvals = [t[feat] for t in trades
                 if t["result"] in ("WIN","LOSS") and feat in t]
        if len(xvals) != len(y) or len(xvals) < 5:
            continue
        x = np.array(xvals)
        try:
            r, p = pearsonr(x, y)
        except Exception:
            r, p = 0.0, 1.0
        rows.append((feat, r, abs(r), p))
    rows.sort(key=lambda x: -x[2])

    print("\n" + "=" * W)
    print("  3. PEARSON CORRELATION  (outcome: WIN=1, LOSS=0)")
    print("     Positive r = feature higher in winners.")
    print("     |r| >= 0.30 moderate, >= 0.10 weak, < 0.10 negligible")
    print("=" * W)
    hdr = "  {:>3}  {:<38}  {:>8}  {:>10}  {}"
    print(hdr.format("Rk", "Feature", "Pearson r", "p-value", "Signal"))
    print(_hline())
    for rank, (feat, r, ar, p) in enumerate(rows, 1):
        lbl  = FEATURE_LABELS.get(feat, feat)[:38]
        sig  = ("***" if p < 0.001 else
                ("**"  if p < 0.010 else
                ("*"   if p < 0.050 else "")))
        note = ("moderate" if ar >= 0.30 else
                ("weak"    if ar >= 0.10 else "negligible"))
        print(hdr.format(rank, lbl, f"{r:+.3f}", f"{p:.4f}", f"{sig} {note}"))
    print("=" * W)
    return {feat: rank for rank, (feat, _, _, _) in enumerate(rows, 1)}


def _print_spearman(trades: List[Dict]) -> Optional[Dict[str, int]]:
    if not SCIPY_OK:
        print("\n  [Spearman skipped -- scipy not available]")
        return None

    y = np.array([1.0 if t["result"] == "WIN" else 0.0
                  for t in trades if t["result"] in ("WIN", "LOSS")])
    rows = []
    for feat in FEATURE_NAMES:
        xvals = [t[feat] for t in trades
                 if t["result"] in ("WIN","LOSS") and feat in t]
        if len(xvals) != len(y) or len(xvals) < 5:
            continue
        x = np.array(xvals)
        try:
            rho, p = spearmanr(x, y)
        except Exception:
            rho, p = 0.0, 1.0
        rows.append((feat, rho, abs(rho), p))
    rows.sort(key=lambda x: -x[2])

    print("\n" + "=" * W)
    print("  4. SPEARMAN CORRELATION  (rank-based, robust to outliers)")
    print("=" * W)
    hdr = "  {:>3}  {:<38}  {:>10}  {:>10}  {}"
    print(hdr.format("Rk", "Feature", "Spearman r", "p-value", "Signal"))
    print(_hline())
    for rank, (feat, rho, arho, p) in enumerate(rows, 1):
        lbl = FEATURE_LABELS.get(feat, feat)[:38]
        sig = ("***" if p < 0.001 else
               ("**"  if p < 0.010 else
               ("*"   if p < 0.050 else "")))
        note = ("moderate" if arho >= 0.30 else
                ("weak"    if arho >= 0.10 else "negligible"))
        print(hdr.format(rank, lbl, f"{rho:+.3f}", f"{p:.4f}", f"{sig} {note}"))
    print("=" * W)
    return {feat: rank for rank, (feat, _, _, _) in enumerate(rows, 1)}


def _print_logreg(trades: List[Dict]) -> Optional[Dict[str, int]]:
    if not SKLEARN_OK:
        print("\n  [Logistic regression skipped -- scikit-learn not available]")
        return None

    eligible = [t for t in trades if t["result"] in ("WIN", "LOSS")]
    y        = np.array([1 if t["result"] == "WIN" else 0 for t in eligible])

    feat_order = []
    X_cols     = []
    for feat in FEATURE_NAMES:
        col = [t.get(feat, 0.0) for t in eligible]
        if len(set(col)) > 1:
            feat_order.append(feat)
            X_cols.append(col)

    if not feat_order:
        print("\n  [Logistic regression: no variable features]")
        return None

    X = np.array(X_cols).T
    scaler = StandardScaler()
    Xs     = scaler.fit_transform(X)

    try:
        model = LogisticRegression(max_iter=1000, solver="lbfgs", C=1.0)
        model.fit(Xs, y)
        coefs = model.coef_[0]
    except Exception as e:
        print(f"\n  [Logistic regression failed: {e}]")
        return None

    rows = [(feat_order[i], coefs[i], abs(coefs[i]))
            for i in range(len(feat_order))]
    rows.sort(key=lambda x: -x[2])

    print("\n" + "=" * W)
    print("  5. LOGISTIC REGRESSION  (standardized coefficients)")
    print("     Features scaled to zero-mean unit-variance before fitting.")
    print("     Positive coef = feature pushes toward WIN.")
    print("     Magnitude reflects contribution after controlling for other features.")
    print("=" * W)
    hdr = "  {:>3}  {:<38}  {:>9}  {}"
    print(hdr.format("Rk", "Feature", "Coef", "Direction"))
    print(_hline())
    for rank, (feat, c, ac) in enumerate(rows, 1):
        lbl = FEATURE_LABELS.get(feat, feat)[:38]
        dir_s = "-> WIN" if c > 0 else "-> LOSS"
        print(hdr.format(rank, lbl, f"{c:+.3f}", dir_s))
    print("=" * W)
    return {feat: rank for rank, (feat, _, _) in enumerate(rows, 1)}


def _print_composite(
    cohen_ranks:   Optional[Dict[str, int]],
    pearson_ranks: Optional[Dict[str, int]],
    spear_ranks:   Optional[Dict[str, int]],
    logreg_ranks:  Optional[Dict[str, int]],
    wins:          List[Dict],
    loss:          List[Dict],
) -> None:
    methods  = [r for r in [cohen_ranks, pearson_ranks, spear_ranks, logreg_ranks]
                if r is not None]
    n_methods = len(methods)
    if n_methods == 0:
        return

    composite: Dict[str, float] = {}
    for feat in FEATURE_NAMES:
        ranks_for_feat = [m[feat] for m in methods if feat in m]
        if ranks_for_feat:
            composite[feat] = sum(ranks_for_feat) / len(ranks_for_feat)

    ranked = sorted(composite.items(), key=lambda x: x[1])

    print("\n" + "=" * W)
    print(f"  6. COMPOSITE RANKING  --  average rank across {n_methods} methods")
    print("     Lower avg rank = more consistently predictive across all methods.")
    print("=" * W)
    hdr = "  {:>3}  {:<38}  {:>9}  {:>8}  {}"
    print(hdr.format("Rk", "Feature", "AvgRank", "Cohen d", "Consistency"))
    print(_hline())

    for composite_rank, (feat, avg) in enumerate(ranked, 1):
        wv  = [t[feat] for t in wins if feat in t]
        lv  = [t[feat] for t in loss if feat in t]
        d   = _cohens_d(wv, lv) if wv and lv else 0.0
        lbl = FEATURE_LABELS.get(feat, feat)[:38]
        # Count how many methods put this feature in top half
        n_feats = len(FEATURE_NAMES)
        top_half = sum(1 for m in methods if feat in m and m[feat] <= n_feats // 2)
        cons = f"top-half in {top_half}/{n_methods} methods"
        print(hdr.format(composite_rank, lbl, f"{avg:.1f}", f"{d:+.3f}", cons))

    # ── Key findings ──────────────────────────────────────────────────────────
    print()
    print("  KEY FINDINGS:")
    top3_feats = [feat for feat, _ in ranked[:3]]
    for feat in top3_feats:
        wv   = [t[feat] for t in wins if feat in t]
        lv   = [t[feat] for t in loss if feat in t]
        d    = _cohens_d(wv, lv) if wv and lv else 0.0
        wm   = np.mean(wv) if wv else 0
        lm   = np.mean(lv) if lv else 0
        lbl  = FEATURE_LABELS.get(feat, feat)
        dir_ = "higher" if wm > lm else "lower"
        print(f"  - {lbl}")
        print(f"    Winners {dir_}: {wm:.2f} vs Losers: {lm:.2f}  |  d={d:+.3f} ({_effect_label(d)})")

    # ── Statistically significant separators ─────────────────────────────────
    if SCIPY_OK:
        print()
        print("  STATISTICALLY SIGNIFICANT SEPARATORS (p < 0.05, |r| > 0.10):")
        eligible = [t for t in wins + loss if t["result"] in ("WIN","LOSS")]
        y = np.array([1.0 if t["result"] == "WIN" else 0.0 for t in eligible])
        found_any = False
        for feat, _ in ranked:
            xv = [t.get(feat, 0.0) for t in eligible]
            if len(xv) < 5:
                continue
            x = np.array(xv)
            try:
                r, p = pearsonr(x, y)
            except Exception:
                continue
            if p < 0.05 and abs(r) > 0.10:
                wv = [t[feat] for t in wins if feat in t]
                lv = [t[feat] for t in loss if feat in t]
                d  = _cohens_d(wv, lv) if wv and lv else 0.0
                lbl = FEATURE_LABELS.get(feat, feat)
                print(f"  * {lbl}")
                print(f"    r={r:+.3f}  p={p:.4f}  d={d:+.3f} ({_effect_label(d)})")
                found_any = True
        if not found_any:
            print("  None meet both p<0.05 and |r|>0.10 thresholds.")
            print("  NOTE: with N<200, power is limited. Medium effects (d=0.4-0.7)")
            print("  may not reach significance. Use effect sizes as primary guide.")
    print("=" * W)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  B+C WINNER vs LOSER FEATURE STUDY")
    print(f"  Engine : B + C + atr_pct <= {ATR_THRESHOLD}  (exact, unchanged)")
    print(f"  Data   : chart_data/ JSON  (~8 months)")
    print(f"  Goal   : Find real-time features that predict outcome")
    print(f"  Run    : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W + "\n")

    all_trades: List[Dict] = []

    for sym in SYMBOLS:
        print(f"  [{sym:5}] loading ... ", end="", flush=True)
        dl = _load_symbol(sym)
        if dl is None:
            print("SKIP"); continue
        c15_all, c1h_all = dl

        trades = _scan_symbol(sym, c15_all, c1h_all)
        filt   = [t for t in trades if t["atr_pct"] <= ATR_THRESHOLD]
        all_trades.extend(filt)

        wins = sum(1 for t in filt if t["result"] == "WIN")
        loss = sum(1 for t in filt if t["result"] == "LOSS")
        print(f"OK  {len(c15_all)} bars  {len(filt):>3} filt  "
              f"WR={wins/max(wins+loss,1)*100:.0f}%")

    eligible = [t for t in all_trades if t["result"] in ("WIN", "LOSS")]
    wins_all = [t for t in eligible if t["result"] == "WIN"]
    loss_all = [t for t in eligible if t["result"] == "LOSS"]

    print(f"\n  Total filtered  : {len(all_trades)}")
    print(f"  Eligible (W+L)  : {len(eligible)}")
    print(f"  Winners         : {len(wins_all)}")
    print(f"  Losers          : {len(loss_all)}")
    print(f"  Break-evens     : {len(all_trades) - len(eligible)}")
    if len(eligible) < 20:
        print("  WARNING: N < 20 -- statistical power is very low.")

    if not wins_all or not loss_all:
        print("  Cannot run study: need at least one winner and one loser."); return

    _print_mean_median(wins_all, loss_all)
    d_ranks = _print_cohen_ranking(wins_all, loss_all)
    p_ranks = _print_pearson(eligible)
    s_ranks = _print_spearman(eligible)
    l_ranks = _print_logreg(eligible)
    _print_composite(d_ranks, p_ranks, s_ranks, l_ranks, wins_all, loss_all)


if __name__ == "__main__":
    main()
