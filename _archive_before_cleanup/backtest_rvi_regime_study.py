# -*- coding: utf-8 -*-
"""
backtest_rvi_regime_study.py  --  Session RVI & Market Regime Diagnostic
=========================================================================
Approved 2026-06-20.

Goal:
  Validate whether Session Relative Volatility Index (RVI) and Market Regime
  are true predictors of trade quality.

Definitions:
  volatility_ratio = ATR_at_entry / ATR_20day_average
                     (where ATR_20day_average = mean True Range over ~400 bars)
  volume_ratio     = session_volume_at_entry / avg_session_volume_20d
                     (cumulative from session open to entry bar, same intraday offset)
  RVI              = volatility_ratio * volume_ratio

Regime:
  Trend  = ADX >= 25
  Range  = ADX <  25

RVI buckets: data-driven terciles (no threshold optimization):
  Low    = RVI in 0..33rd percentile of the study pool
  Medium = RVI in 33..67th percentile
  High   = RVI in 67..100th percentile

Trade sets:
  1. X2 trades       (adapted from backtest_runner_x2.backtest_symbol, chart_data)
  2. B+C trades      (same engine as validated backtest studies, chart_data)
  3. B+C+ATR<=0.52   (same as #2 with ATR filter applied)

Data: chart_data/ JSON files (~8 months, ~5000 bars per symbol)

Reports per trade set:
  - RVI bucket table: N, WR, PF, TotalR, AvgMFE, AvgMAE, LV%
  - Regime table: same fields
  - RVI decile analysis (10 equal bins by RVI, monotonicity check)
  - Pearson & Spearman correlation: RVI vs outcome, ADX vs outcome
  - Leave-one-symbol-out

Do not modify analyzer_x2.py, execution.py, or any existing backtest.
Research only.  No optimization.  No threshold search.
"""
from __future__ import annotations

import json
import math
import os
import sys
import warnings
from collections import defaultdict
from datetime import date as date_type, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

warnings.filterwarnings("ignore")

try:
    from scipy.stats import pearsonr, spearmanr
    SCIPY_OK = True
except ImportError:
    print("scipy not found -- correlations will be skipped")
    SCIPY_OK = False

# ── Core infrastructure (read-only imports) ───────────────────────────────────
try:
    from backtest_runner_x2 import (
        _market_state_from, _detect_zone, _zone_bounds, _htf_direction,
        _call_detect_sweep, _call_detect_displacement,
        _is_retest, _event_index,
        MIN_HISTORY, TP1_R,
        _volume_ratio as _x2_volume_ratio,
    )
    X2_INFRA_OK = True
except Exception as e:
    print(f"backtest_runner_x2 partial import: {e}")
    X2_INFRA_OK = False

try:
    from analyzer_x2 import Candle, SYMBOL_PROFILES
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


# ── Constants ─────────────────────────────────────────────────────────────────

SYMBOLS: List[str] = [
    "AAPL", "AMD",  "TSLA", "AVGO", "COST", "LLY",  "PANW", "CRM",
    "QQQ",  "SPY",  "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
]

CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")

# X2 engine constants (from backtest_runner_x2)
MAX_HOLD       = 26
SETUP_COOLDOWN = 16

# B+C engine constants (unchanged from research)
CONF_START         = 2
CONF_END           = 6
PRE_B_QUAL_MIN     = 0.55
PRE_DISP_QUAL_MIN  = 0.35
PRE_BIRTH_AGE_MAX  = 15
HTF_MIN_STRENGTH   = 0.30
SESSION_CUTOFF     = 525     # UTC session_min -- last 75 min = ET 14:15
STOP_BUFFER        = 0.22
DISPLACE_BODY_MIN  = 0.50
DISPLACE_CLOSE_MIN = 0.65
ATR_THRESHOLD      = 0.52

# RVI regime threshold
ADX_TREND_MIN = 25.0
# RVI tercile thresholds computed from data (not hardcoded -- see _assign_rvi_buckets)
N_RVI_DECILES = 10

W = 94


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_json(symbol: str, tf: str) -> Optional[List[Candle]]:
    path = os.path.join(CHART_DIR, f"{symbol}_{tf}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
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


# ── Session volume precomputation ─────────────────────────────────────────────

def _precompute_session_vols(c15_all: List[Candle]) -> Dict[date_type, Dict[int, float]]:
    """
    For each trading date and each intraday offset (bars from UTC 13:30 open),
    compute cumulative session volume from open to that offset.

    Returns: {date: {offset: cumulative_volume}}
    offset 0 = first bar at UTC 13:30, offset 1 = 13:45, etc.
    """
    result: Dict[date_type, Dict[int, float]] = defaultdict(dict)
    for c in c15_all:
        sm = (c.timestamp.hour - 9) * 60 + c.timestamp.minute - 30
        if sm < 240 or sm >= 525:   # outside ET session (in UTC terms)
            continue
        offset = (sm - 240) // 15
        d      = c.timestamp.date()
        prev   = result[d].get(offset - 1, 0.0) if offset > 0 else 0.0
        result[d][offset] = prev + c.volume
    return dict(result)


def _session_vol_at(
    session_cum: Dict[date_type, Dict[int, float]],
    entry_date:  date_type,
    entry_sm:    int,
) -> Tuple[float, float]:
    """
    Return (session_vol_now, avg_session_vol_20d) at a given entry_date and session_min.
    Uses the cumulative volume from open to entry bar, and averages the same
    intraday offset over the last 20 prior sessions.
    """
    if entry_sm < 240:
        return 0.0, 0.0
    offset   = (entry_sm - 240) // 15
    vol_now  = session_cum.get(entry_date, {}).get(offset, 0.0)

    # Find last 20 sessions that have data at this offset
    prior = sorted(d for d in session_cum if d < entry_date and offset in session_cum[d])
    past20 = prior[-20:]
    if not past20:
        return vol_now, vol_now
    avg_vol = sum(session_cum[d][offset] for d in past20) / len(past20)
    return vol_now, avg_vol


def _atr_20d_avg(c15_all: List[Candle], global_idx: int, lookback: int = 400) -> float:
    """Mean True Range over last ~lookback bars (proxy for 20-day ATR average)."""
    start = max(1, global_idx - lookback)
    trs: List[float] = []
    for j in range(start, global_idx):
        hi = c15_all[j].high
        lo = c15_all[j].low
        pc = c15_all[j - 1].close
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _compute_rvi(
    c15_all:     List[Candle],
    entry_idx:   int,
    entry_sm:    int,
    atr:         float,
    session_cum: Dict[date_type, Dict[int, float]],
) -> Tuple[float, float, float]:
    """
    Returns (rvi, vol_ratio, atr_ratio).
    rvi = atr_ratio * vol_ratio
    """
    # Volatility ratio
    atr_20d    = _atr_20d_avg(c15_all, entry_idx, 400)
    atr_ratio  = atr / atr_20d if atr_20d > 1e-9 else 1.0

    # Volume ratio
    entry_date = c15_all[entry_idx].timestamp.date()
    vol_now, avg_vol = _session_vol_at(session_cum, entry_date, entry_sm)
    vol_ratio  = vol_now / avg_vol if avg_vol > 1e-9 else 1.0

    rvi = atr_ratio * vol_ratio
    return round(rvi, 4), round(vol_ratio, 4), round(atr_ratio, 4)


# ── RVI bucket assignment (terciles, no optimization) ─────────────────────────

def _assign_rvi_buckets(trades: List[Dict]) -> List[Dict]:
    """Add 'rvi_bucket' field: Low / Medium / High by data-driven terciles."""
    rvi_vals = [t["rvi"] for t in trades]
    if not rvi_vals:
        return trades
    p33 = float(np.percentile(rvi_vals, 33.33))
    p67 = float(np.percentile(rvi_vals, 66.67))
    for t in trades:
        v = t["rvi"]
        if v <= p33:
            t["rvi_bucket"] = "Low"
        elif v <= p67:
            t["rvi_bucket"] = "Medium"
        else:
            t["rvi_bucket"] = "High"
    return trades


# ── X2 scanner (adapted from backtest_runner_x2.backtest_symbol) ──────────────

def _scan_x2(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:
    """
    Replicate X2 trade logic on pre-loaded candles.
    Adds RVI, regime, mfe_r, mae_r to each trade dict.
    Does NOT download data (read from chart_data).
    Does NOT modify backtest_runner_x2 or analyzer_x2.
    """
    if not X2_INFRA_OK:
        return []

    default_profile = {
        "enabled": True,
        "timeframe": "15m" if symbol in {"NVDA", "SQQQ"} else "1H",
        "min_conf": 72, "min_adx": 17, "max_adx": 68,
    }
    profile = dict(default_profile)
    sp      = SYMBOL_PROFILES.get(symbol, {})
    if isinstance(sp, dict):
        profile.update(sp)
    tf       = profile.get("timeframe", "1H")
    min_adx  = float(profile.get("min_adx", 17))
    max_adx  = float(profile.get("max_adx", 68))
    min_conf = float(profile.get("min_conf", profile.get("min_score", 70)))

    session_cum = _precompute_session_vols(c15_all)
    trades: List[Dict] = []
    last_trade_bar = -999

    for i in range(MIN_HISTORY, len(c15_all) - MAX_HOLD - 1):
        c15 = c15_all[: i + 1]
        ts  = c15_all[i].timestamp

        setup_candles = (c15[-220:] if tf == "15m" else
                         ([c for c in c1h_all if c.timestamp <= ts][-300:]
                          if c1h_all and len([c for c in c1h_all if c.timestamp <= ts]) >= 60
                          else c15[-220:]))
        trigger_candles = c15[-8:]
        if len(setup_candles) < 60:
            continue

        market = _market_state_from(setup_candles, c15_all[i].close)
        atr    = market.atr_14
        adx    = market.adx
        if adx < min_adx or adx > max_adx:
            continue
        if (i - last_trade_bar) < SETUP_COOLDOWN:
            continue

        c1h_snap = [c for c in c1h_all if c.timestamp <= ts] or c15
        htf      = _htf_direction(c1h_snap[-300:] if len(c1h_snap) >= 60 else c1h_snap, market)
        dirs     = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in dirs:
            sweep        = _call_detect_sweep(setup_candles, direction, atr)
            after_idx    = _event_index(sweep) if sweep is not None else max(0, len(setup_candles) - 36)
            displacement = _call_detect_displacement(setup_candles, direction, atr, after_idx)
            if sweep is None and displacement is None:
                continue
            zone_after = _event_index(displacement) if displacement is not None else after_idx
            zone       = _detect_zone(setup_candles, direction, atr, zone_after)
            if zone is None:
                continue
            zb = _zone_bounds(zone)
            if not zb:
                continue
            z_top, z_bot = zb

            z_mid = (z_top + z_bot) / 2
            if abs(c15_all[i].close - z_mid) / max(atr, 0.01) > 3.2:
                continue
            if not _is_retest(trigger_candles, zone, direction, atr):
                continue

            sweep_q = float(getattr(sweep,        "quality", 0.0) or 0.0) if sweep        else 0.0
            disp_q  = float(getattr(displacement, "quality", 0.0) or 0.0) if displacement else 0.0
            zone_q  = float(getattr(zone,         "quality", 0.7) or 0.7)
            fresh   = float(getattr(zone,        "freshness",0.7) or 0.7)
            vr_x2   = _x2_volume_ratio(setup_candles) if _x2_volume_ratio else 1.0
            score   = 30 + sweep_q*14 + disp_q*14 + zone_q*20 + fresh*10
            if sweep and displacement:
                score += 6
            score += 5 if htf != "NEUTRAL" else 0
            score += 5 if vr_x2 >= 0.8 else 2
            if score < min_conf:
                continue

            entry    = c15_all[i].close
            stop     = (z_bot - atr * STOP_BUFFER if direction == "LONG"
                        else z_top + atr * STOP_BUFFER)
            risk     = abs(entry - stop)
            if risk <= 0:
                continue
            tp1 = entry + risk * TP1_R if direction == "LONG" else entry - risk * TP1_R

            sm_i = (ts.hour - 9) * 60 + ts.minute - 30
            rvi, vol_ratio, atr_ratio = _compute_rvi(c15_all, i, sm_i, atr, session_cum)

            # Simulate
            outcome = "BE"; r_mult = 0.0; mfe_r = 0.0; mae_r = 0.0
            for fc in c15_all[i + 1 : i + 1 + MAX_HOLD]:
                if direction == "LONG":
                    mfe_r = max(mfe_r, (fc.high - entry) / risk)
                    mae_r = min(mae_r, (fc.low  - entry) / risk)
                    if fc.low  <= stop: outcome = "LOSS"; r_mult = -1.0; break
                    if fc.high >= tp1:  outcome = "WIN";  r_mult = TP1_R; break
                else:
                    mfe_r = max(mfe_r, (entry - fc.low)  / risk)
                    mae_r = min(mae_r, (entry - fc.high) / risk)
                    if fc.high >= stop: outcome = "LOSS"; r_mult = -1.0; break
                    if fc.low  <= tp1:  outcome = "WIN";  r_mult = TP1_R; break

            trades.append({
                "symbol":     symbol,
                "direction":  "LONG" if direction == "LONG" else "SHORT",
                "date":       ts.strftime("%Y-%m-%d"),
                "result":     outcome,
                "r":          round(r_mult, 2),
                "mfe_r":      round(mfe_r,  2),
                "mae_r":      round(mae_r,  2),
                "score":      round(score,  1),
                "adx":        round(adx,    1),
                "atr":        round(atr,    4),
                "rvi":        rvi,
                "vol_ratio":  vol_ratio,
                "atr_ratio":  atr_ratio,
                "regime":     "Trend" if adx >= ADX_TREND_MIN else "Range",
                "atr_pct":    None,
                "pct_done":   None,
                "bucket":     None,
            })
            last_trade_bar = i
            break

    return trades


# ── B+C scanner ───────────────────────────────────────────────────────────────

def _scan_bc(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:
    """B+C scanner with RVI and regime added. Returns all B+C trades (no ATR filter)."""
    prof    = _get_profile(symbol)
    min_adx = float(prof.get("min_adx", 17))
    max_adx = float(prof.get("max_adx", 68))

    session_cum = _precompute_session_vols(c15_all)
    trades: List[Dict] = []
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
            if bars_after_birth != 0 or b_any > PRE_BIRTH_AGE_MAX:
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
            b1_fire = b_exp <= near; b2_fire = b_vol <= near; b3_fire = b_str <= near
            if not (b1_fire or b2_fire or b3_fire):
                continue

            last_birth[direction] = birth_global

            birth_area = c15_all[swing_global : birth_global + 1]
            if not birth_area:
                continue
            h_struct = (max(c.high for c in birth_area) if direction == "LONG"
                        else min(c.low  for c in birth_area))

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

            b_offset: Optional[int] = None
            c_offset: Optional[int] = None

            for offset in range(CONF_START, CONF_END + 1):
                conf_idx = birth_global + offset
                if conf_idx >= safe_max + CONF_END:
                    break
                cb2 = c15_all[conf_idx]
                rng = max(cb2.high - cb2.low, 1e-9)
                if direction == "LONG":
                    body2 = cb2.close - cb2.open
                    dc    = (cb2.close - cb2.low) / rng
                    if b_offset is None and cb2.close > cb2.open and body2 >= DISPLACE_BODY_MIN*atr and dc >= DISPLACE_CLOSE_MIN:
                        b_offset = offset
                    if c_offset is None and cb2.close > h_struct:
                        c_offset = offset
                else:
                    body2 = cb2.open - cb2.close
                    dc    = (cb2.high - cb2.close) / rng
                    if b_offset is None and cb2.close < cb2.open and body2 >= DISPLACE_BODY_MIN*atr and dc >= DISPLACE_CLOSE_MIN:
                        b_offset = offset
                    if c_offset is None and cb2.close < h_struct:
                        c_offset = offset

                if b_offset is not None and c_offset is not None:
                    entry_offset = max(b_offset, c_offset)
                    entry_idx    = birth_global + entry_offset
                    entry_px     = c15_all[entry_idx].close
                    entry_ts     = c15_all[entry_idx].timestamp

                    atr_pct      = atr / max(entry_px, 1e-9) * 100.0
                    entry_sm     = (entry_ts.hour - 9) * 60 + entry_ts.minute - 30
                    rvi, vol_ratio, atr_ratio = _compute_rvi(
                        c15_all, entry_idx, entry_sm, atr, session_cum)

                    # pct_done (diagnostic)
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

                    risk = abs(entry_px - stop_price)
                    if risk <= 1e-9:
                        break
                    tp1    = (entry_px + risk * TP1_R if direction == "LONG"
                              else entry_px - risk * TP1_R)
                    outcome = "BE"; r_mult = 0.0; mfe_r = 0.0; mae_r = 0.0
                    for fc in c15_all[entry_idx + 1 : entry_idx + SIM_BARS + 1]:
                        if direction == "LONG":
                            mfe_r = max(mfe_r, (fc.high - entry_px) / risk)
                            mae_r = min(mae_r, (fc.low  - entry_px) / risk)
                            if fc.low  <= stop_price: outcome="LOSS"; r_mult=-1.0; break
                            if fc.high >= tp1:        outcome="WIN";  r_mult=TP1_R; break
                        else:
                            mfe_r = max(mfe_r, (entry_px - fc.low)  / risk)
                            mae_r = min(mae_r, (entry_px - fc.high) / risk)
                            if fc.high >= stop_price: outcome="LOSS"; r_mult=-1.0; break
                            if fc.low  <= tp1:        outcome="WIN";  r_mult=TP1_R; break

                    trades.append({
                        "symbol":    symbol,
                        "direction": direction,
                        "date":      ts.strftime("%Y-%m-%d"),
                        "result":    outcome,
                        "r":         round(r_mult, 2),
                        "mfe_r":     round(mfe_r,  2),
                        "mae_r":     round(mae_r,  2),
                        "adx":       round(adx,    1),
                        "atr":       round(atr,    4),
                        "rvi":       rvi,
                        "vol_ratio": vol_ratio,
                        "atr_ratio": atr_ratio,
                        "regime":    "Trend" if adx >= ADX_TREND_MIN else "Range",
                        "atr_pct":   round(atr_pct, 4),
                        "pct_done":  pct,
                        "bucket":    _bucket(pct),
                    })
                    break

    return trades


# ── Metrics ───────────────────────────────────────────────────────────────────

def _m(trades: List[Dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n":0,"wr":0.0,"pf":0.0,"totalr":0.0,"mfe":0.0,"mae":0.0,"lv":0.0}
    wins = sum(1 for t in trades if t["result"] == "WIN")
    loss = sum(1 for t in trades if t["result"] == "LOSS")
    gw   = sum(float(t["r"]) for t in trades if float(t["r"]) > 0)
    gl   = abs(sum(float(t["r"]) for t in trades if float(t["r"]) < 0))
    dec  = wins + loss
    lv_trades = [t for t in trades if t.get("bucket") in ("Late","VeryLate")]
    return {
        "n":      n,
        "wr":     wins / dec * 100 if dec else 0.0,
        "pf":     gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr": sum(float(t["r"]) for t in trades),
        "mfe":    float(np.mean([float(t["mfe_r"]) for t in trades])) if trades else 0.0,
        "mae":    float(np.mean([float(t["mae_r"]) for t in trades])) if trades else 0.0,
        "lv":     len(lv_trades) / n * 100 if n > 0 else 0.0,
    }

def _hline(c: str = "-") -> str:
    return "  " + c * (W - 2)


# ── Report: RVI buckets ───────────────────────────────────────────────────────

def _print_rvi_buckets(label: str, trades: List[Dict]) -> None:
    if not trades:
        return
    rvi_vals   = [t["rvi"] for t in trades]
    p33        = float(np.percentile(rvi_vals, 33.33))
    p67        = float(np.percentile(rvi_vals, 66.67))

    print(f"\n  RVI BUCKETS  [{label}]")
    print(f"  Low = RVI <= {p33:.3f}  |  Medium = {p33:.3f}-{p67:.3f}  |  High = RVI > {p67:.3f}")
    hdr = "  {:<10}  {:>4}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}  {:>6}"
    print(hdr.format("Bucket","N","WR%","PF","TotalR","AvgMFE","AvgMAE","LV%"))
    print(_hline())
    for bname, bfn in [
        ("Low",    lambda r: r <= p33),
        ("Medium", lambda r: p33 < r <= p67),
        ("High",   lambda r: r > p67),
    ]:
        grp = [t for t in trades if bfn(t["rvi"])]
        m   = _m(grp)
        lv  = "--" if m["lv"] == 0.0 and all(t.get("bucket") is None for t in grp) else f"{m['lv']:.1f}%"
        print(hdr.format(bname, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                         f"{m['totalr']:+.1f}R", f"{m['mfe']:+.2f}R", f"{m['mae']:+.2f}R", lv))
    print(_hline())
    mt = _m(trades)
    lv_t = "--" if all(t.get("bucket") is None for t in trades) else f"{mt['lv']:.1f}%"
    print(hdr.format("TOTAL", mt["n"], f"{mt['wr']:.1f}%", f"{mt['pf']:.2f}",
                     f"{mt['totalr']:+.1f}R", f"{mt['mfe']:+.2f}R", f"{mt['mae']:+.2f}R", lv_t))

    # Separation check
    m_lo = _m([t for t in trades if t["rvi"] <= p33])
    m_hi = _m([t for t in trades if t["rvi"] >  p67])
    d_pf = m_hi["pf"] - m_lo["pf"]
    print(f"\n  Low->High PF change: {m_lo['pf']:.2f} -> {m_hi['pf']:.2f}  dPF={d_pf:+.2f}  "
          f"{'SEPARATION' if abs(d_pf) >= 0.30 else 'no meaningful separation'}")


# ── Report: Regime ────────────────────────────────────────────────────────────

def _print_regime(label: str, trades: List[Dict]) -> None:
    if not trades:
        return
    print(f"\n  REGIME  [{label}]  (Trend=ADX>={ADX_TREND_MIN:.0f}, Range=ADX<{ADX_TREND_MIN:.0f})")
    hdr = "  {:<10}  {:>4}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}  {:>6}  {:>7}"
    print(hdr.format("Regime","N","WR%","PF","TotalR","AvgMFE","AvgMAE","LV%","AvgADX"))
    print(_hline())
    for rname in ["Trend", "Range"]:
        grp = [t for t in trades if t.get("regime") == rname]
        m   = _m(grp)
        avg_adx = float(np.mean([t["adx"] for t in grp])) if grp else 0.0
        lv  = "--" if all(t.get("bucket") is None for t in grp) else f"{m['lv']:.1f}%"
        print(hdr.format(rname, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                         f"{m['totalr']:+.1f}R", f"{m['mfe']:+.2f}R", f"{m['mae']:+.2f}R",
                         lv, f"{avg_adx:.1f}"))
    print(_hline())
    mt = _m(trades)
    lv_t = "--" if all(t.get("bucket") is None for t in trades) else f"{mt['lv']:.1f}%"
    avg_adx_t = float(np.mean([t["adx"] for t in trades])) if trades else 0.0
    print(hdr.format("TOTAL", mt["n"], f"{mt['wr']:.1f}%", f"{mt['pf']:.2f}",
                     f"{mt['totalr']:+.1f}R", f"{mt['mfe']:+.2f}R", f"{mt['mae']:+.2f}R",
                     lv_t, f"{avg_adx_t:.1f}"))


# ── Report: Decile analysis ───────────────────────────────────────────────────

def _print_deciles(label: str, trades: List[Dict], field: str = "rvi") -> None:
    if len(trades) < N_RVI_DECILES * 2:
        print(f"\n  [Decile analysis skipped for {label} -- N={len(trades)} too small]")
        return
    sp  = sorted(trades, key=lambda t: t[field])
    n   = len(sp)
    sz  = max(1, n // N_RVI_DECILES)

    print(f"\n  DECILE ANALYSIS  [{label}]  (by {field}, D1=lowest, D10=highest)")
    print(f"  If {field} predicts quality: PF should trend monotonically with decile.")
    hdr = "  {:>4}  {:>12}  {:>4}  {:>6}  {:>5}  {:>8}"
    print(hdr.format("Decile", f"{field[:10]}Range", "N", "WR%", "PF", "TotalR"))
    print(_hline())
    pf_vals = []
    for d in range(N_RVI_DECILES):
        lo  = d * sz
        hi  = (d + 1) * sz if d < N_RVI_DECILES - 1 else n
        grp = sp[lo:hi]
        if not grp:
            continue
        m    = _m(grp)
        f_lo = grp[0][field]; f_hi = grp[-1][field]
        pf_vals.append(m["pf"])
        print(hdr.format(f"D{d+1:02d}", f"{f_lo:.2f}-{f_hi:.2f}",
                         m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                         f"{m['totalr']:+.1f}R"))
    print(_hline())
    rises = sum(1 for i in range(1, len(pf_vals)) if pf_vals[i] > pf_vals[i-1])
    total = len(pf_vals) - 1
    print(f"  Monotone rising: {rises}/{total} transitions  "
          f"({'SIGNAL >60%' if total > 0 and rises/total > 0.6 else 'no directional signal'})")


# ── Report: Correlations ──────────────────────────────────────────────────────

def _print_correlations(label: str, trades: List[Dict]) -> None:
    elig  = [t for t in trades if t["result"] in ("WIN","LOSS")]
    if len(elig) < 10:
        print(f"\n  [Correlations skipped for {label} -- N={len(elig)}]")
        return
    y   = np.array([1.0 if t["result"] == "WIN" else 0.0 for t in elig])
    print(f"\n  CORRELATIONS  [{label}]  (outcome WIN=1, LOSS=0)")
    hdr = "  {:<20}  {:>9}  {:>9}  {:>10}  {:>10}  {}"
    print(hdr.format("Feature", "Pearson r", "P-value", "Spearman r", "P-value", "Signal"))
    print(_hline())
    for feat, label2 in [
        ("rvi",       "RVI"),
        ("atr_ratio", "ATR ratio"),
        ("vol_ratio", "Vol ratio"),
        ("adx",       "ADX"),
    ]:
        xv = np.array([t.get(feat, 0.0) for t in elig])
        if len(set(xv)) < 2:
            print(hdr.format(label2, "0.000", "--", "0.000", "--", "constant"))
            continue
        if SCIPY_OK:
            pr, pp = pearsonr(xv, y)
            sr, sp = spearmanr(xv, y)
            sig = ("***" if min(pp,sp)<0.001 else ("**" if min(pp,sp)<0.01
                   else ("*" if min(pp,sp)<0.05 else "")))
            note = ("moderate" if max(abs(pr),abs(sr))>=0.30 else
                    ("weak" if max(abs(pr),abs(sr))>=0.10 else "negligible"))
            print(hdr.format(label2, f"{pr:+.3f}", f"{pp:.4f}",
                             f"{sr:+.3f}", f"{sp:.4f}", f"{sig} {note}"))
        else:
            print(hdr.format(label2, "scipy N/A", "--", "scipy N/A", "--", "--"))


# ── Report: Leave-one-symbol-out ─────────────────────────────────────────────

def _print_loo(label: str, trades: List[Dict]) -> None:
    full = _m(trades)
    if full["n"] == 0:
        return
    print(f"\n  LEAVE-ONE-SYMBOL-OUT  [{label}]")
    print(f"  Full: N={full['n']}  WR={full['wr']:.1f}%  PF={full['pf']:.2f}  "
          f"TotalR={full['totalr']:+.1f}R")
    hdr = "  {:<7}  {:>4}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}"
    print(hdr.format("Removed", "N", "WR%", "PF", "TotalR", "dPF", "dWR"))
    print(_hline())
    rows = []
    for sym in SYMBOLS:
        sub = [t for t in trades if t["symbol"] != sym]
        sm  = _m(sub)
        sym_n = sum(1 for t in trades if t["symbol"] == sym)
        if sym_n == 0:
            continue
        rows.append((sym, sm, sm["pf"] - full["pf"]))
    rows.sort(key=lambda x: x[2])
    for sym, sm, dpf in rows:
        flag = ("  <-- carrying" if dpf <= -0.35 else
                ("  <-- dragging" if dpf >= +0.35 else ""))
        print(hdr.format(sym, sm["n"], f"{sm['wr']:.1f}%", f"{sm['pf']:.2f}",
                         f"{sm['totalr']:+.1f}R",
                         f"{dpf:+.2f}", f"{sm['wr']-full['wr']:+.1f}pp") + flag)
    print(_hline())
    top_r = max((_m([t for t in trades if t["symbol"]==s])["totalr"] for s in SYMBOLS), default=0)
    if full["totalr"] > 0:
        conc = top_r / full["totalr"] * 100
        flag = "CONCENTRATED" if conc > 60 else ("MODERATE" if conc > 40 else "DISTRIBUTED")
        print(f"  Concentration: {flag} (top symbol = {conc:.0f}% of TotalR)")


# ── Full section for one trade set ────────────────────────────────────────────

def _report_set(name: str, trades: List[Dict]) -> None:
    print("\n\n" + "=" * W)
    print(f"  TRADE SET: {name}")
    m = _m(trades)
    print(f"  N={m['n']}  WR={m['wr']:.1f}%  PF={m['pf']:.2f}  TotalR={m['totalr']:+.1f}R  "
          f"AvgMFE={m['mfe']:+.2f}R  AvgMAE={m['mae']:+.2f}R")
    if m["n"] == 0:
        print("  No trades for this set."); return

    rvi_all = [t["rvi"] for t in trades]
    print(f"  RVI  mean={np.mean(rvi_all):.3f}  median={np.median(rvi_all):.3f}  "
          f"p33={np.percentile(rvi_all,33.33):.3f}  p67={np.percentile(rvi_all,66.67):.3f}")
    print("=" * W)

    _print_rvi_buckets(name, trades)
    _print_regime(name, trades)
    _print_deciles(name, trades, "rvi")
    _print_deciles(name, trades, "adx")
    _print_correlations(name, trades)
    _print_loo(name, trades)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  RVI & REGIME DIAGNOSTIC STUDY")
    print(f"  RVI = (ATR_today/ATR_20d_avg) * (session_vol/avg_session_vol_20d)")
    print(f"  Regime: ADX >= {ADX_TREND_MIN:.0f} = Trend, < {ADX_TREND_MIN:.0f} = Range")
    print(f"  Data: chart_data/ (~8 months)  |  Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Symbols: {len(SYMBOLS)}")
    print("=" * W + "\n")

    x2_all:  List[Dict] = []
    bc_all:  List[Dict] = []

    for sym in SYMBOLS:
        print(f"  [{sym:5}] loading ... ", end="", flush=True)
        data = _load_symbol(sym)
        if data is None:
            print("NO DATA"); continue
        c15, c1h = data

        x2 = _scan_x2(sym, c15, c1h)
        bc = _scan_bc(sym, c15, c1h)
        x2_all.extend(x2)
        bc_all.extend(bc)

        bc_filt = [t for t in bc if t["atr_pct"] is not None and t["atr_pct"] <= ATR_THRESHOLD]
        x2w = sum(1 for t in x2 if t["result"]=="WIN")
        x2l = sum(1 for t in x2 if t["result"]=="LOSS")
        bcw = sum(1 for t in bc if t["result"]=="WIN")
        bcl = sum(1 for t in bc if t["result"]=="LOSS")
        print(f"OK  X2={len(x2)}(WR={x2w/max(x2w+x2l,1)*100:.0f}%)  "
              f"BC={len(bc)}(WR={bcw/max(bcw+bcl,1)*100:.0f}%)  "
              f"BC-ATR={len(bc_filt)}")

    bc_atr_all = [t for t in bc_all
                  if t["atr_pct"] is not None and t["atr_pct"] <= ATR_THRESHOLD]

    print(f"\n  Summary:")
    print(f"    X2 trades      : {len(x2_all)}")
    print(f"    B+C trades     : {len(bc_all)}")
    print(f"    B+C+ATR trades : {len(bc_atr_all)}")

    # RVI is comparable across sets
    if x2_all:
        all_rvi = [t["rvi"] for t in x2_all + bc_atr_all]
        print(f"\n  Cross-set RVI stats:")
        print(f"    X2:    mean={np.mean([t['rvi'] for t in x2_all]):.3f}")
        print(f"    BC:    mean={np.mean([t['rvi'] for t in bc_all]):.3f}")
        print(f"    BC+ATR:mean={np.mean([t['rvi'] for t in bc_atr_all]):.3f}")

    # ── Reports ───────────────────────────────────────────────────────────────
    if x2_all:
        _report_set("1. All X2 trades", x2_all)
    else:
        print("\n  [X2 trades: scanner unavailable or no trades found]")

    _report_set("2. B+C trades (all)", bc_all)
    _report_set("3. B+C + ATR<=0.52",  bc_atr_all)

    # ── Cross-set summary ─────────────────────────────────────────────────────
    print("\n\n" + "=" * W)
    print("  VERDICT  --  Does RVI or Regime predict trade quality?")
    print("=" * W)
    for name, pool in [
        ("X2 trades", x2_all),
        ("B+C all",   bc_all),
        ("B+C+ATR",   bc_atr_all),
    ]:
        if not pool:
            continue
        rvis = [t["rvi"] for t in pool]
        p33  = float(np.percentile(rvis, 33.33))
        p67  = float(np.percentile(rvis, 66.67))
        m_lo = _m([t for t in pool if t["rvi"] <= p33])
        m_hi = _m([t for t in pool if t["rvi"] >  p67])
        m_tr = _m([t for t in pool if t["regime"] == "Trend"])
        m_rg = _m([t for t in pool if t["regime"] == "Range"])
        dpf_rvi    = m_hi["pf"] - m_lo["pf"]
        dpf_regime = m_tr["pf"] - m_rg["pf"]

        elig = [t for t in pool if t["result"] in ("WIN","LOSS")]
        if SCIPY_OK and len(elig) >= 10:
            y    = np.array([1.0 if t["result"]=="WIN" else 0.0 for t in elig])
            xrvi = np.array([t["rvi"] for t in elig])
            pr, pp = pearsonr(xrvi, y)
        else:
            pr, pp = 0.0, 1.0

        sig = "YES" if (abs(dpf_rvi) >= 0.30 or abs(pr) >= 0.10) else "NO"
        print(f"\n  {name}:")
        print(f"    RVI Low->High PF: {m_lo['pf']:.2f} -> {m_hi['pf']:.2f}  dPF={dpf_rvi:+.2f}")
        print(f"    RVI Pearson r={pr:+.3f}  p={pp:.4f}")
        print(f"    Trend PF={m_tr['pf']:.2f}  Range PF={m_rg['pf']:.2f}  dPF={dpf_regime:+.2f}")
        print(f"    RVI predictive? {sig}  |  Regime predictive? "
              f"{'YES' if abs(dpf_regime) >= 0.30 else 'NO'}")
    print("=" * W)


if __name__ == "__main__":
    main()
