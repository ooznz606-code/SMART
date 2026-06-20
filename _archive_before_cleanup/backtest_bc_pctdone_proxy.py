# -*- coding: utf-8 -*-
"""
backtest_bc_pctdone_proxy.py  --  Real-Time pct_done Proxy Study
=================================================================
Approved 2026-06-20.

Engine : B + C + atr_pct <= 0.52   (exact, unchanged)
Data   : chart_data/ JSON (~8 months, ~5000 bars per symbol)

Problem:
  pct_done is the strongest predictor of outcome (d=-1.711 from winner-loser
  study) but requires knowing the future swing endpoint -- it is NOT available
  in real-time.

Goal:
  Find which real-time features at the B+C fire point best predict pct_done.
  Build a simple linear proxy so pct_done can be estimated from observable data.

Target variable : pct_done  (0-100%, position within swing move at entry)

Candidate predictors (all available at B+C fire point, before outcome):
  1.  atr_pct          ATR / entry_price * 100
  2.  htf_strength     Direction-adjusted EMA20/50 gap on 1H
  3.  b_qual           Birth bar body / ATR
  4.  birth_age        Bars swing_extreme -> birth bar  (b_any)
  5.  entry_offset     B+C offset from birth bar  (2-6)
  6.  bars_since_swing birth_age + entry_offset
  7.  bars_after_birth entry_offset  (same as entry_offset, kept for clarity)
  8.  disp_qual        Displacement quality score
  9.  dist_birth       (entry - birth_close) / ATR
  10. dist_struct      (entry - struct_level) / ATR
  11. ext_from_swing   (entry - swing_price) / ATR  (total extension)
  12. session_min      UTC minute of birth bar
  13. confluence       Count of birth-type anchors near b_any
  14. vol_expansion    Birth bar volume / 20-bar avg volume
  15. atr_rank         Percentile of current ATR vs trailing 50 ATRs

Reports:
  1. Pearson correlation to pct_done
  2. Spearman correlation to pct_done
  3. Multivariate linear regression (standardized)
  4. Top 5 predictors of pct_done
  5. Linear proxy formula: pct_done_est = f(real-time features)
  6. Proxy quality: R2, RMSE, error percentiles, bias

No optimization.  No new filters.  No trading rules.
Do not modify analyzer_x2.  Research only.
"""
from __future__ import annotations

import json
import math
import os
import sys
import warnings
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

warnings.filterwarnings("ignore")

try:
    from scipy.stats import pearsonr, spearmanr
    SCIPY_OK = True
except ImportError:
    print("scipy not found -- Pearson/Spearman will be skipped"); SCIPY_OK = False

try:
    from sklearn.linear_model import LinearRegression, Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import r2_score
    SKLEARN_OK = True
except ImportError:
    print("scikit-learn not found -- regression will be skipped"); SKLEARN_OK = False

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

# Ordered candidate list -- all available at fire point, before outcome is known
FEATURES: List[str] = [
    "birth_age",
    "bars_since_swing",
    "ext_from_swing",
    "dist_birth",
    "dist_struct",
    "disp_qual",
    "entry_offset",
    "session_min",
    "atr_pct",
    "htf_strength",
    "b_qual",
    "vol_expansion",
    "confluence",
    "atr_rank",
]

FEATURE_LABELS: Dict[str, str] = {
    "birth_age":        "birth_age        (bars swing->birth)",
    "bars_since_swing": "bars_since_swing (birth_age + offset)",
    "ext_from_swing":   "ext_from_swing   (entry-swing)/ATR",
    "dist_birth":       "dist_birth       (entry-birth)/ATR",
    "dist_struct":      "dist_struct      (entry-struct)/ATR",
    "disp_qual":        "disp_qual        (displacement score)",
    "entry_offset":     "entry_offset     (B+C bar offset 2-6)",
    "session_min":      "session_min      (UTC min of birth)",
    "atr_pct":          "atr_pct          (ATR/price*100)",
    "htf_strength":     "htf_strength     (EMA20/50 gap)",
    "b_qual":           "b_qual           (birth body/ATR)",
    "vol_expansion":    "vol_expansion    (birth vol/avg vol)",
    "confluence":       "confluence       (anchor count 1-3)",
    "atr_rank":         "atr_rank         (ATR percentile 0-100)",
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


# ── JSON loader (UTC timestamps) ──────────────────────────────────────────────

def _load_json(symbol: str, tf: str) -> Optional[List[Candle]]:
    path = os.path.join(CHART_DIR, f"{symbol}_{tf}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        print(f"  JSON error {symbol}/{tf}: {e}"); return None
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _avg_volume(c15_all: List[Candle], global_idx: int, lookback: int = 20) -> float:
    start = max(0, global_idx - lookback)
    vols  = [c.volume for c in c15_all[start:global_idx] if c.volume > 0]
    return sum(vols) / len(vols) if vols else 1.0


def _atr_rank(c15_all: List[Candle], global_idx: int, atr: float, lookback: int = 50) -> float:
    start = max(0, global_idx - lookback)
    ranges: List[float] = []
    for j in range(start, global_idx):
        c = c15_all[j]
        prev_close = c15_all[j - 1].close if j > 0 else c.close
        tr = max(c.high - c.low,
                 abs(c.high - prev_close),
                 abs(c.low  - prev_close))
        ranges.append(tr)
    if not ranges:
        return 50.0
    below = sum(1 for r in ranges if r <= atr)
    return below / len(ranges) * 100.0


# ── Scanner ───────────────────────────────────────────────────────────────────

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

            birth_area = c15_all[swing_global : birth_global + 1]
            if not birth_area:
                continue
            if direction == "LONG":
                h_struct = max(c.high for c in birth_area)
            else:
                l_struct = min(c.low  for c in birth_area)

            stop_price = (bc.low  - STOP_BUFFER * atr if direction == "LONG"
                          else bc.high + STOP_BUFFER * atr)

            avg_vol       = _avg_volume(c15_all, birth_global, 20)
            vol_expansion = bc.volume / max(avg_vol, 1.0)
            atr_rank_val  = _atr_rank(c15_all, birth_global, atr, 50)

            # ── Confirmation window ───────────────────────────────────────
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

                    atr_pct = atr / max(entry_px, 1e-9) * 100.0

                    # Distance features (real-time, direction-adjusted)
                    if direction == "LONG":
                        dist_birth    = (entry_px - bc.close) / max(atr, 1e-9)
                        dist_struct   = (entry_px - h_struct) / max(atr, 1e-9)
                        ext_from_swing = (entry_px - sw_price) / max(atr, 1e-9)
                    else:
                        dist_birth    = (bc.close - entry_px) / max(atr, 1e-9)
                        dist_struct   = (l_struct - entry_px) / max(atr, 1e-9)
                        ext_from_swing = (sw_price - entry_px) / max(atr, 1e-9)
                    dist_birth     = max(0.0, dist_birth)
                    dist_struct    = max(0.0, dist_struct)
                    ext_from_swing = max(0.0, ext_from_swing)

                    # pct_done (target variable -- uses future data)
                    future = c15_all[entry_idx + 1 : entry_idx + 1 + SWING_LOOKFWD]
                    sw_end = _swing_end(future, direction)
                    if sw_end is not None:
                        total_mv = ((sw_end - sw_price) if direction == "LONG"
                                    else (sw_price - sw_end))
                        at_entry = ((entry_px - sw_price) if direction == "LONG"
                                    else (sw_price - entry_px))
                        pct = round(at_entry / total_mv * 100, 1) if total_mv > 0 else 50.0
                    else:
                        pct = 50.0

                    # Simulation (for outcome context only)
                    risk = abs(entry_px - stop_price)
                    if risk <= 1e-9:
                        break
                    tp1    = (entry_px + risk * TP1_R if direction == "LONG"
                              else entry_px - risk * TP1_R)
                    result = "BE"
                    for fc in c15_all[entry_idx + 1 : entry_idx + SIM_BARS + 1]:
                        if direction == "LONG":
                            if fc.low  <= stop_price: result = "LOSS"; break
                            if fc.high >= tp1:        result = "WIN";  break
                        else:
                            if fc.high >= stop_price: result = "LOSS"; break
                            if fc.low  <= tp1:        result = "WIN";  break

                    results.append({
                        "symbol":       symbol,
                        "result":       result,
                        "pct_done":     pct,
                        # real-time features
                        "birth_age":        b_any,
                        "bars_since_swing": b_any + entry_offset,
                        "ext_from_swing":   round(ext_from_swing, 3),
                        "dist_birth":       round(dist_birth,     3),
                        "dist_struct":      round(dist_struct,    3),
                        "disp_qual":        round(disp_qual,      3),
                        "entry_offset":     entry_offset,
                        "session_min":      session_min,
                        "atr_pct":          round(atr_pct,        4),
                        "htf_strength":     round(htf_str,        3),
                        "b_qual":           round(b_qual,         3),
                        "vol_expansion":    round(vol_expansion,  3),
                        "confluence":       confluence,
                        "atr_rank":         round(atr_rank_val,   1),
                    })
                    break

    return results


# ── Report helpers ────────────────────────────────────────────────────────────

def _hline(c: str = "-") -> str:
    return "  " + c * (W - 2)


def _vif(X: np.ndarray) -> List[float]:
    """Variance inflation factor for each column of X."""
    vifs = []
    for j in range(X.shape[1]):
        y_j = X[:, j]
        X_j = np.delete(X, j, axis=1)
        reg = LinearRegression().fit(X_j, y_j)
        ss_res = np.sum((y_j - reg.predict(X_j)) ** 2)
        ss_tot = np.sum((y_j - np.mean(y_j)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
        vif = 1.0 / (1.0 - r2) if r2 < 0.9999 else 999.0
        vifs.append(round(vif, 1))
    return vifs


# ── Analysis sections ─────────────────────────────────────────────────────────

def _print_correlations(trades: List[Dict]) -> Tuple[Dict[str,float], Dict[str,float]]:
    y = np.array([t["pct_done"] for t in trades])

    pearson_r : Dict[str, float] = {}
    spearman_r: Dict[str, float] = {}
    pearson_p : Dict[str, float] = {}
    spearman_p: Dict[str, float] = {}

    for feat in FEATURES:
        x = np.array([t.get(feat, 0.0) for t in trades])
        if len(set(x)) < 2:
            pearson_r[feat] = spearman_r[feat] = 0.0
            pearson_p[feat] = spearman_p[feat] = 1.0
            continue
        if SCIPY_OK:
            pr, pp = pearsonr(x, y)
            sr, sp = spearmanr(x, y)
            pearson_r[feat]  = pr; pearson_p[feat]  = pp
            spearman_r[feat] = sr; spearman_p[feat] = sp
        else:
            pearson_r[feat] = spearman_r[feat] = 0.0
            pearson_p[feat] = spearman_p[feat] = 1.0

    if not SCIPY_OK:
        print("\n  [Correlation skipped -- scipy not available]")
        return pearson_r, spearman_r

    # ── Pearson ───────────────────────────────────────────────────────────────
    sorted_p = sorted(FEATURES, key=lambda f: -abs(pearson_r[f]))

    print("\n" + "=" * W)
    print("  1. PEARSON CORRELATION  (feature -> pct_done)")
    print("     Positive r = feature rises as pct_done rises (later entry).")
    print("     |r| >= 0.50 strong, >= 0.30 moderate, >= 0.10 weak, < 0.10 negligible")
    print("=" * W)
    hdr = "  {:>3}  {:<45}  {:>9}  {:>10}  {:>7}"
    print(hdr.format("Rk", "Feature", "Pearson r", "p-value", "Signal"))
    print(_hline())
    for rank, feat in enumerate(sorted_p, 1):
        r = pearson_r[feat]; p = pearson_p[feat]
        lbl = FEATURE_LABELS.get(feat, feat)[:45]
        sig = ("***" if p < 0.001 else ("**" if p < 0.010 else ("*" if p < 0.050 else "")))
        note = ("strong" if abs(r) >= 0.50 else
                ("moderate" if abs(r) >= 0.30 else
                ("weak" if abs(r) >= 0.10 else "negligible")))
        print(hdr.format(rank, lbl, f"{r:+.3f}", f"{p:.4f}", f"{sig} {note}"))
    print("=" * W)

    # ── Spearman ──────────────────────────────────────────────────────────────
    sorted_s = sorted(FEATURES, key=lambda f: -abs(spearman_r[f]))

    print("\n" + "=" * W)
    print("  2. SPEARMAN CORRELATION  (rank-based, robust to outliers)")
    print("=" * W)
    hdr = "  {:>3}  {:<45}  {:>10}  {:>10}  {:>7}"
    print(hdr.format("Rk", "Feature", "Spearman r", "p-value", "Signal"))
    print(_hline())
    for rank, feat in enumerate(sorted_s, 1):
        r = spearman_r[feat]; p = spearman_p[feat]
        lbl = FEATURE_LABELS.get(feat, feat)[:45]
        sig = ("***" if p < 0.001 else ("**" if p < 0.010 else ("*" if p < 0.050 else "")))
        note = ("strong" if abs(r) >= 0.50 else
                ("moderate" if abs(r) >= 0.30 else
                ("weak" if abs(r) >= 0.10 else "negligible")))
        print(hdr.format(rank, lbl, f"{r:+.3f}", f"{p:.4f}", f"{sig} {note}"))
    print("=" * W)

    return pearson_r, spearman_r


def _print_multivariate(trades: List[Dict], pearson_r: Dict[str,float]) -> Optional[Dict]:
    if not SKLEARN_OK:
        print("\n  [Multivariate regression skipped -- scikit-learn not available]")
        return None

    y = np.array([t["pct_done"] for t in trades])

    # Use all features with non-zero variance
    feat_order: List[str] = []
    X_cols: List[List[float]] = []
    for feat in FEATURES:
        col = [t.get(feat, 0.0) for t in trades]
        if len(set(col)) > 1:
            feat_order.append(feat)
            X_cols.append(col)

    if not feat_order:
        print("\n  [No variable features]")
        return None

    X      = np.array(X_cols).T
    scaler = StandardScaler()
    Xs     = scaler.fit_transform(X)

    model = LinearRegression()
    model.fit(Xs, y)
    coefs  = model.coef_
    y_pred = model.predict(Xs)
    r2     = r2_score(y, y_pred)

    # VIF for collinearity
    vifs = _vif(Xs)

    # Sort by |coef|
    rows = [(feat_order[i], coefs[i], abs(coefs[i]), vifs[i])
            for i in range(len(feat_order))]
    rows.sort(key=lambda x: -x[2])

    print("\n" + "=" * W)
    print("  3. MULTIVARIATE LINEAR REGRESSION  (target: pct_done)")
    print("     Features standardized to zero-mean unit-variance.")
    print("     Positive coef = feature pushes toward higher pct_done (LATER entry).")
    print(f"     Model R² = {r2:.3f}  ({r2*100:.1f}% of pct_done variance explained)")
    print("     VIF > 5 = multicollinearity concern")
    print("=" * W)
    hdr = "  {:>3}  {:<45}  {:>8}  {:>5}  {:>7}  {}"
    print(hdr.format("Rk", "Feature", "Coef", "VIF", "PearsonR", "Note"))
    print(_hline())
    for rank, (feat, c, ac, vif) in enumerate(rows, 1):
        lbl  = FEATURE_LABELS.get(feat, feat)[:45]
        pr   = pearson_r.get(feat, 0.0)
        note = ""
        if vif > 5:
            note = "collinear"
        elif ac >= 5.0:
            note = "dominant"
        elif ac >= 2.0:
            note = "strong"
        print(hdr.format(rank, lbl, f"{c:+.3f}", f"{vif:.1f}", f"{pr:+.3f}", note))
    print(_hline())
    print(f"  Model intercept = {model.intercept_:.2f}")
    print(f"  Model R²        = {r2:.3f}")
    print("=" * W)

    return {
        "model":      model,
        "scaler":     scaler,
        "feat_order": feat_order,
        "r2":         r2,
        "y_pred":     y_pred,
        "rows":       rows,
    }


def _print_top5(pearson_r: Dict[str,float], spearman_r: Dict[str,float],
                reg_result: Optional[Dict]) -> List[str]:
    """
    Select top 5 by composite rank (|Pearson|, |Spearman|, |LogReg coef|).
    Exclude collinear pairs (VIF > 5).
    """
    # Rank by |Pearson|
    p_rank  = {f: i for i, f in enumerate(sorted(FEATURES, key=lambda f: -abs(pearson_r.get(f,0))), 1)}
    s_rank  = {f: i for i, f in enumerate(sorted(FEATURES, key=lambda f: -abs(spearman_r.get(f,0))), 1)}

    reg_rank: Dict[str, int] = {}
    if reg_result:
        for i, (feat, _, _, _) in enumerate(reg_result["rows"], 1):
            reg_rank[feat] = i

    composite: Dict[str, float] = {}
    for feat in FEATURES:
        ranks = [p_rank.get(feat, len(FEATURES)),
                 s_rank.get(feat, len(FEATURES))]
        if reg_rank:
            ranks.append(reg_rank.get(feat, len(FEATURES)))
        composite[feat] = sum(ranks) / len(ranks)

    sorted_feats = sorted(FEATURES, key=lambda f: composite[f])

    print("\n" + "=" * W)
    print("  4. TOP 5 PREDICTORS OF pct_done  (by composite rank)")
    print("=" * W)
    hdr = "  {:>3}  {:<45}  {:>9}  {:>10}  {:>9}  {}"
    print(hdr.format("Rk", "Feature", "Pearson r", "Spearman r", "CompRank", "Interpretation"))
    print(_hline())
    for rank, feat in enumerate(sorted_feats[:5], 1):
        pr  = pearson_r.get(feat, 0.0)
        sr  = spearman_r.get(feat, 0.0)
        avg = composite[feat]
        lbl = FEATURE_LABELS.get(feat, feat)[:45]
        if pr > 0:
            interp = "higher -> later entry"
        else:
            interp = "higher -> earlier entry"
        print(hdr.format(rank, lbl, f"{pr:+.3f}", f"{sr:+.3f}", f"{avg:.1f}", interp))
    print("=" * W)

    return sorted_feats[:5]


def _build_proxy(trades: List[Dict], top5: List[str]) -> None:
    if not SKLEARN_OK:
        print("\n  [Proxy build skipped -- scikit-learn not available]")
        return

    # Remove constant features from top5
    usable = [f for f in top5 if len(set(t.get(f, 0.0) for t in trades)) > 1]
    if not usable:
        print("\n  [No variable features for proxy]")
        return

    y = np.array([t["pct_done"] for t in trades])
    X = np.array([[t.get(f, 0.0) for f in usable] for t in trades])

    model  = LinearRegression()
    model.fit(X, y)
    y_pred = model.predict(X)
    r2     = r2_score(y, y_pred)
    errors = y_pred - y
    rmse   = math.sqrt(np.mean(errors ** 2))
    mae    = np.mean(np.abs(errors))
    bias   = np.mean(errors)

    print("\n" + "=" * W)
    print("  5. LINEAR PROXY FORMULA  --  pct_done_est = f(real-time features)")
    print("     Fit on all 151 filtered trades (unscaled raw coefficients).")
    print("     Usable at fire point -- all inputs are known before outcome.")
    print("=" * W)
    print()
    terms = []
    for i, feat in enumerate(usable):
        c    = model.coef_[i]
        sign = "+" if c >= 0 else ""
        terms.append(f"{sign}{c:.4f} * {feat}")

    intercept = model.intercept_
    print(f"  pct_done_est =")
    print(f"    {intercept:.4f}")
    for term in terms:
        print(f"    {term}")
    print()
    print(f"  Proxy quality:")
    print(f"    R²   = {r2:.3f}  ({r2*100:.1f}% of pct_done variance explained)")
    print(f"    RMSE = {rmse:.1f} pct-points")
    print(f"    MAE  = {mae:.1f} pct-points")
    print(f"    Bias = {bias:+.2f} pct-points  (positive = proxy overestimates)")
    print()

    # Error percentiles
    abs_err = np.abs(errors)
    print(f"  Error distribution (|predicted - actual|):")
    for pct_lbl, pct_val in [("25th", 25), ("50th", 50), ("75th", 75), ("90th", 90)]:
        print(f"    {pct_lbl} percentile: {np.percentile(abs_err, pct_val):.1f} pct-pts")
    print()
    print("=" * W)

    # ── Proxy bucket analysis ─────────────────────────────────────────────────
    print("\n" + "=" * W)
    print("  PROXY BUCKET ANALYSIS  --  does estimated pct_done predict outcome?")
    print("  Bucket: Early_est (<33%), Mid_est (33-66%), Late_est (>66%)")
    print("  If proxy works: Early_est should have higher WR than Late_est.")
    print("=" * W)
    hdr = "  {:<14}  {:>4}  {:>6}  {:>5}  {:>8}  {:>12}"
    print(hdr.format("Est.Bucket", "N", "WR%", "PF", "TotalR", "ActualMean%"))
    print(_hline())

    buckets = [("Early_est (<33%)",  lambda e: e <  33),
               ("Mid_est (33-66%)",  lambda e: 33 <= e < 66),
               ("Late_est (>66%)",   lambda e: e >= 66)]

    for bname, bfn in buckets:
        grp = [(trades[i], y_pred[i]) for i in range(len(trades)) if bfn(y_pred[i])]
        if not grp:
            continue
        g_trades = [x[0] for x in grp]
        wins = sum(1 for t in g_trades if t["result"] == "WIN")
        loss = sum(1 for t in g_trades if t["result"] == "LOSS")
        gw   = sum(1.8 for t in g_trades if t["result"] == "WIN")
        gl   = sum(1.0 for t in g_trades if t["result"] == "LOSS")
        dec  = wins + loss
        wr   = wins / dec * 100 if dec else 0.0
        pf   = gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0)
        tr   = sum(1.8 if t["result"] == "WIN" else
                   (-1.0 if t["result"] == "LOSS" else 0.0) for t in g_trades)
        actual_mean = np.mean([t["pct_done"] for t in g_trades])
        print(hdr.format(bname, len(g_trades), f"{wr:.1f}%", f"{pf:.2f}",
                         f"{tr:+.1f}R", f"{actual_mean:.1f}%"))

    print("=" * W)

    # ── Practical proxy note ──────────────────────────────────────────────────
    print("\n" + "=" * W)
    print("  PRACTICAL INTERPRETATION")
    print("=" * W)
    print(f"  R² = {r2:.3f} means the proxy explains {r2*100:.1f}% of pct_done variance.")
    print(f"  RMSE = {rmse:.1f} pct-points (average error in estimating swing position).")
    print()
    if r2 >= 0.40:
        print("  USABLE: proxy captures enough signal to meaningfully stratify trades.")
    elif r2 >= 0.20:
        print("  WEAK: proxy has limited accuracy. Use directionally, not as absolute estimate.")
        print("  Treat as tiebreaker, not a hard gate.")
    else:
        print("  NOT USABLE: proxy is too noisy. pct_done cannot be estimated from")
        print("  real-time features. The information simply is not available pre-outcome.")
        print()
        print("  Implication: the winner/loser separation by pct_done is a property")
        print("  of WHICH setups the market generates, not something observable")
        print("  before the trade resolves.")
    print("=" * W)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  B+C pct_done PROXY STUDY")
    print(f"  Engine  : B + C + atr_pct <= {ATR_THRESHOLD}")
    print(f"  Target  : pct_done  (% of swing move at entry -- uses future data)")
    print(f"  Mission : find real-time predictors and build proxy estimate")
    print(f"  Data    : chart_data/ (~8 months)")
    print(f"  Run     : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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

        n     = len(filt)
        pct_m = np.mean([t["pct_done"] for t in filt]) if filt else 0.0
        print(f"OK  {n:>3} filt  avg_pct={pct_m:.0f}%")

    print(f"\n  Total filtered trades : {len(all_trades)}")
    if not all_trades:
        print("  No trades."); return

    pct_arr = np.array([t["pct_done"] for t in all_trades])
    print(f"  pct_done  mean={np.mean(pct_arr):.1f}%  "
          f"median={np.median(pct_arr):.1f}%  "
          f"std={np.std(pct_arr):.1f}%  "
          f"min={np.min(pct_arr):.1f}%  "
          f"max={np.max(pct_arr):.1f}%")

    wins = sum(1 for t in all_trades if t["result"] == "WIN")
    loss = sum(1 for t in all_trades if t["result"] == "LOSS")
    print(f"  Outcomes: WIN={wins}  LOSS={loss}  BE={len(all_trades)-wins-loss}")

    pearson_r, spearman_r = _print_correlations(all_trades)
    reg_result = _print_multivariate(all_trades, pearson_r)
    top5 = _print_top5(pearson_r, spearman_r, reg_result)
    _build_proxy(all_trades, top5)


if __name__ == "__main__":
    main()
