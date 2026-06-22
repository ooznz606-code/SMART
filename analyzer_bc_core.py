# -*- coding: utf-8 -*-
"""
analyzer_bc_core.py  --  B+C Signal Analyzer (self-contained production core)
==============================================================================
Clean production build.  Single dependency: analyzer_x2.py.
No backtest_*.py imports.  No yfinance.  No pandas.

All helpers previously imported from backtest_runner_x2, backtest_entry_timing,
backtest_move_birth, backtest_x3_birth_ict, and backtest_daily_selection are
inlined verbatim so output is byte-for-byte identical to analyzer_bc_atr_daily.

Public API (unchanged):
  scan_symbol(symbol, c15_all, c1h_all) -> List[Signal]
  scan_all(chart_dir, symbols, lookback_days) -> List[Signal]
  select_daily(signals, top_n) -> List[Signal]
  run(chart_dir, symbols, top_n, lookback_days, verbose) -> List[Signal]
  load_json(symbol, tf, chart_dir) -> Optional[List[Candle]]
  load_symbol_candles(symbol, chart_dir, lookback_days) -> Optional[Tuple[...]]

Signal keys: identical to analyzer_bc_atr_daily (no new keys, none removed).
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

# -- Single external dependency -----------------------------------------------
try:
    from analyzer_x2 import (
        Candle,
        MarketState,
        Direction,
        SYMBOL_PROFILES,
        _atr,
        _adx_calc,
    )
except Exception as _e:
    print(f"[analyzer_bc_core] Cannot import analyzer_x2: {_e}")
    sys.exit(1)

import analyzer_x2 as _x2

# Resolve optional function names that changed between analyzer_x2 versions
_direction_from_htf = getattr(_x2, "_direction_from_htf", None)
_get_1h_trend       = getattr(_x2, "_get_1h_trend",       None)
_resolve_zone       = getattr(_x2, "_resolve_zone",        None)
_detect_fvg         = getattr(_x2, "_detect_fvg",          None)
_detect_order_block = getattr(_x2, "_detect_order_block",  None)


# -- Strategy constants (validated in backtest studies -- do not change) -------

CONF_START         = 2
CONF_END           = 6
PRE_B_QUAL_MIN     = 0.55
PRE_DISP_QUAL_MIN  = 0.35
PRE_BIRTH_AGE_MAX  = 15
HTF_MIN_STRENGTH   = 0.30
SESSION_CUTOFF     = 525       # UTC session_min >= 525 = ET 14:15
STOP_BUFFER        = 0.22
DISPLACE_BODY_MIN  = 0.50
DISPLACE_CLOSE_MIN = 0.65
ATR_THRESHOLD      = 0.52
TOP_N_DAILY        = 3

# Inlined from backtest_runner_x2 (unchanged values)
MIN_HISTORY  = 160
TP1_R        = 1.8

# Inlined from backtest_entry_timing (unchanged values)
SWING_LOOKBACK = 60
SIM_BARS       = 40

SYMBOLS: List[str] = [
    "AAPL", "AMD",  "TSLA", "AVGO", "COST", "LLY",  "PANW", "CRM",
    "QQQ",  "SPY",  "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
    "UBER",
]

CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")


# -- EMA (inlined from analyzer_bc_atr_daily / backtest_daily_selection) ------

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# -- Inlined from backtest_runner_x2 ------------------------------------------

def _as_dir_name(d: Any) -> str:
    v = getattr(d, "value", d)
    s = str(v).upper()
    if s in ("LONG", "BULL", "CALL", "BUY"):
        return "LONG"
    if s in ("SHORT", "BEAR", "PUT", "SELL"):
        return "SHORT"
    return "NEUTRAL"


def _dir_enum(name: str):
    if name == "LONG":
        return Direction.LONG
    if name == "SHORT":
        return Direction.SHORT
    return Direction.NEUTRAL


def _market_state_from(candles: List[Candle], price: float) -> MarketState:
    closes  = [c.close for c in candles]
    vols    = [c.volume for c in candles[-21:]]
    atr     = _atr(candles, 14)    if len(candles) >= 20 else max(price * 0.01, 0.01)
    adx     = _adx_calc(candles, 14) if len(candles) >= 40 else 20.0
    ema50   = _ema(closes, 50)     if len(closes) >= 5  else price
    ema200  = _ema(closes, 200)    if len(closes) >= 20 else _ema(closes, len(closes))
    avg_vol = (sum(vols[:-1]) / max(1, len(vols[:-1]))
               if len(vols) > 1 else (vols[-1] if vols else 1.0))
    return MarketState(
        vix          = 18.0,
        adx          = float(adx  or 0),
        volume       = float(vols[-1] if vols else 0),
        avg_volume_20= float(avg_vol or 1),
        ema_50       = float(ema50  or price),
        ema_200      = float(ema200 or price),
        price        = float(price),
        atr_14       = float(atr   or max(price * 0.01, 0.01)),
        news_risk    = "LOW",
    )


def _htf_direction(c1h: List[Candle], market: MarketState) -> str:
    if _direction_from_htf:
        try:
            out = _direction_from_htf(c1h, market)
            if isinstance(out, tuple):
                return _as_dir_name(out[0])
            return _as_dir_name(out)
        except TypeError:
            pass
        except Exception:
            return "NEUTRAL"
    if _get_1h_trend:
        try:
            return _as_dir_name(_get_1h_trend(c1h))
        except Exception:
            return "NEUTRAL"
    return "NEUTRAL"


def _zone_bounds(zone: Any) -> Optional[Tuple[float, float]]:
    if zone is None:
        return None
    if isinstance(zone, tuple) and len(zone) >= 2:
        return max(float(zone[0]), float(zone[1])), min(float(zone[0]), float(zone[1]))
    top    = getattr(zone, "top",    None)
    bottom = getattr(zone, "bottom", None)
    if top is not None and bottom is not None:
        return max(float(top), float(bottom)), min(float(top), float(bottom))
    return None


def _detect_zone(
    candles:        List[Candle],
    direction_name: str,
    atr:            float,
    after_index:    int = 0,
) -> Optional[Any]:
    direction = _dir_enum(direction_name)
    if _resolve_zone:
        try:
            z, _debug = _resolve_zone(candles, direction, atr, after_index)
            if z is not None:
                return z
        except Exception:
            pass
    for fn in (_detect_fvg, _detect_order_block):
        if fn is None:
            continue
        try:
            try:
                z = fn(candles, direction, atr, after_index)
            except TypeError:
                z = fn(candles, direction_name, atr)
            if z is not None:
                return z
        except Exception:
            continue
    return None


# -- Inlined from backtest_entry_timing ---------------------------------------

def _get_profile(symbol: str) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "enabled": True, "timeframe": "1H",
        "min_conf": 72.0, "min_adx": 17.0, "max_adx": 68.0,
    }
    sp = SYMBOL_PROFILES.get(symbol, {})
    if isinstance(sp, dict) and sp.get("enabled", True) is not False:
        base.update({k: v for k, v in sp.items()
                     if v is not None and k != "max_daily_signals"})
    base["enabled"] = True
    if symbol == "SPY":
        base.update({"timeframe": "1H", "min_conf": 72.0,
                     "min_adx": 17.0, "max_adx": 65.0})
    return base


def _swing_extreme(
    candles:  List[Candle],
    direction: str,
    lookback: int,
) -> Tuple[float, int]:
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


# -- Inlined from backtest_move_birth -----------------------------------------

def _birth_expand(
    candles:   List[Candle],
    swing_idx: int,
    direction: str,
    atr:       float,
) -> Optional[int]:
    if atr <= 0 or swing_idx >= len(candles) - 1:
        return None
    for j in range(swing_idx + 1, len(candles)):
        c    = candles[j]
        body = abs(c.close - c.open)
        if direction == "LONG"  and c.close > c.open and body >= atr * 0.8:
            return j - swing_idx
        if direction == "SHORT" and c.close < c.open and body >= atr * 0.8:
            return j - swing_idx
    return None


def _birth_volume(candles: List[Candle], swing_idx: int) -> Optional[int]:
    pre     = candles[max(0, swing_idx - 20): swing_idx]
    vols    = [c.volume for c in pre if c.volume and c.volume > 0]
    if len(vols) < 5:
        return None
    avg_vol = sum(vols) / len(vols)
    if avg_vol <= 0:
        return None
    for j in range(swing_idx + 1, len(candles)):
        c = candles[j]
        if c.volume and c.volume > 0 and c.volume >= avg_vol * 1.3:
            return j - swing_idx
    return None


def _birth_structure(
    candles:   List[Candle],
    swing_idx: int,
    direction: str,
    lookback:  int = 5,
) -> Optional[int]:
    pre = candles[max(0, swing_idx - lookback + 1): swing_idx + 1]
    if not pre:
        return None
    if direction == "LONG":
        level = max(c.high for c in pre)
        for j in range(swing_idx + 1, len(candles)):
            if candles[j].close > level:
                return j - swing_idx
    else:
        level = min(c.low for c in pre)
        for j in range(swing_idx + 1, len(candles)):
            if candles[j].close < level:
                return j - swing_idx
    return None


def _cap(val: Optional[int], ceiling: int) -> int:
    if val is None or val > ceiling:
        return ceiling
    return val


# -- Inlined from backtest_x3_birth_ict ---------------------------------------

def _disp_quality(
    c15:       List[Candle],
    birth_idx: int,
    direction: str,
    atr:       float,
) -> float:
    if birth_idx < 0 or birth_idx >= len(c15):
        return 0.0
    bc       = c15[birth_idx]
    body     = abs(bc.close - bc.open)
    body_r   = min(1.0, body / max(atr, 1e-9))
    crange   = max(bc.high - bc.low, 1e-9)
    if direction == "LONG":
        dir_ok = (bc.close - bc.low) / crange >= 0.5
    else:
        dir_ok = (bc.high - bc.close) / crange >= 0.5
    if birth_idx > 0:
        prev_body = abs(c15[birth_idx - 1].close - c15[birth_idx - 1].open)
        accel_ok  = body > prev_body
    else:
        accel_ok = True
    return min(1.0, body_r * (1.0 if dir_ok else 0.6) * (1.0 if accel_ok else 0.8))


# -- Inlined from backtest_daily_selection ------------------------------------

def _session_pts(ts: datetime) -> float:
    h, m = ts.hour, ts.minute
    sm   = (h - 9) * 60 + m - 30
    if   sm <  270: return 20.0
    elif sm <  330: return 15.0
    elif sm <  525: return  8.0
    else:           return  0.0


def _rank_score(
    htf_strength:  float,
    birth_age:     int,
    ts:            datetime,
    price_ext_atr: float,
    confluence:    int,
    b_qual:        float,
) -> Tuple[float, Dict[str, float]]:
    htf_pts   = max(0.0, min(float(htf_strength), 3.0)) * 10.0
    birth_pts = max(0.0, 4.0 - float(birth_age))        * 7.5
    sess_pts  = _session_pts(ts)
    ext_pts   = max(0.0, 3.0 - float(price_ext_atr))    * 3.0
    conf_pts  = max(0.0, float(confluence) - 1.0)        * 5.0
    bq_pts    = float(b_qual) * 5.0
    total     = htf_pts + birth_pts + sess_pts + ext_pts + conf_pts + bq_pts
    comps     = {
        "htf":   htf_pts,  "birth": birth_pts, "sess": sess_pts,
        "ext":   ext_pts,  "conf":  conf_pts,  "bq":   bq_pts,
    }
    return round(total, 2), comps


# -- Data loader ---------------------------------------------------------------

def load_json(symbol: str, tf: str, chart_dir: str) -> Optional[List[Candle]]:
    path = os.path.join(chart_dir, f"{symbol}_{tf}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        print(f"  [load_json] {symbol}/{tf}: {e}")
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
                timestamp=dt, open=float(opens[i]),  high=float(highs[i]),
                low=float(lows[i]),  close=float(closes[i]), volume=vol,
            ))
        except Exception:
            continue
    return candles if len(candles) >= 100 else None


def load_symbol_candles(
    symbol:        str,
    chart_dir:     str,
    lookback_days: Optional[int] = None,
) -> Optional[Tuple[List[Candle], List[Candle]]]:
    c15 = load_json(symbol, "15m", chart_dir)
    c1h = load_json(symbol, "1H",  chart_dir) or []
    if c15 is None:
        return None
    if lookback_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        c15    = [c for c in c15 if c.timestamp >= cutoff]
        c1h    = [c for c in c1h if c.timestamp >= cutoff]
        if len(c15) < MIN_HISTORY:
            return None
    return c15, c1h


# -- Core scanner (identical logic to analyzer_bc_atr_daily.scan_symbol) ------

def scan_symbol(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:
    prof    = _get_profile(symbol)
    min_adx = float(prof.get("min_adx", 17))
    max_adx = float(prof.get("max_adx", 68))

    signals:     List[Dict]       = []
    last_birth:  Dict[str, int]   = {"LONG": -999, "SHORT": -999}
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

            birth_area = c15_all[swing_global: birth_global + 1]
            if not birth_area:
                continue
            if direction == "LONG":
                h_struct = max(c.high for c in birth_area)
            else:
                l_struct = min(c.low  for c in birth_area)

            zone = _detect_zone(zone_setup, direction, atr,
                                max(0, len(zone_setup) - 1 - bars_since))
            if zone:
                zb = _zone_bounds(zone)
                if zb:
                    z_top, z_bot = zb
                    stop_ref = (z_bot - STOP_BUFFER * atr if direction == "LONG"
                                else z_top + STOP_BUFFER * atr)
                else:
                    stop_ref = (bc.low  - STOP_BUFFER * atr if direction == "LONG"
                                else bc.high + STOP_BUFFER * atr)
            else:
                stop_ref = (bc.low  - STOP_BUFFER * atr if direction == "LONG"
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
                    entry_bar    = c15_all[entry_idx]
                    entry_px     = entry_bar.close
                    entry_ts     = entry_bar.timestamp

                    atr_pct = atr / max(entry_px, 1e-9) * 100.0
                    if atr_pct > ATR_THRESHOLD:
                        break

                    stop_price = stop_ref
                    risk_pts   = abs(entry_px - stop_price)
                    if risk_pts < 1e-9:
                        break
                    tp1 = (entry_px + risk_pts * TP1_R if direction == "LONG"
                           else entry_px - risk_pts * TP1_R)

                    if direction == "LONG":
                        price_ext_atr = (entry_px - bc.close) / max(atr, 1e-9)
                    else:
                        price_ext_atr = (bc.close - entry_px) / max(atr, 1e-9)
                    price_ext_atr = max(0.0, price_ext_atr)

                    rank, rank_comps = _rank_score(
                        htf_strength  = htf_str,
                        birth_age     = b_any,
                        ts            = ts,
                        price_ext_atr = price_ext_atr,
                        confluence    = confluence,
                        b_qual        = b_qual,
                    )

                    if direction == "LONG":
                        dist_birth = (entry_px - bc.close) / max(atr, 1e-9)
                    else:
                        dist_birth = (bc.close - entry_px) / max(atr, 1e-9)
                    dist_birth = max(0.0, dist_birth)

                    signals.append({
                        "symbol":        symbol,
                        "direction":     direction,
                        "date":          ts.strftime("%Y-%m-%d"),
                        "birth_time":    ts.strftime("%H:%M"),
                        "entry_time":    entry_ts.strftime("%H:%M"),
                        "birth_ts":      ts,
                        "entry_ts":      entry_ts,
                        "entry_price":   round(entry_px,     4),
                        "stop_price":    round(stop_price,   4),
                        "risk_pts":      round(risk_pts,     4),
                        "tp1":           round(tp1,          4),
                        "atr":           round(atr,          4),
                        "atr_pct":       round(atr_pct,      4),
                        "rank_score":    rank,
                        "score_comps":   rank_comps,
                        "htf_strength":  round(htf_str,      3),
                        "b_qual":        round(b_qual,        3),
                        "birth_age":     b_any,
                        "entry_offset":  entry_offset,
                        "confluence":    confluence,
                        "disp_qual":     round(disp_qual,    3),
                        "dist_birth":    round(dist_birth,   3),
                        "session_min":   session_min,
                        "price_ext_atr": round(price_ext_atr, 3),
                    })
                    break

    return signals


# -- Daily selection -----------------------------------------------------------

def select_daily(
    signals: List[Dict[str, Any]],
    top_n:   int = TOP_N_DAILY,
) -> List[Dict[str, Any]]:
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for sig in signals:
        by_date[sig["date"]].append(sig)
    selected: List[Dict] = []
    for date in sorted(by_date):
        ranked = sorted(by_date[date], key=lambda s: -s["rank_score"])
        selected.extend(ranked[:top_n])
    return selected


# -- Full scan pipeline --------------------------------------------------------

def scan_all(
    chart_dir:     str           = CHART_DIR,
    symbols:       List[str]     = SYMBOLS,
    lookback_days: Optional[int] = None,
    verbose:       bool          = False,
) -> List[Dict[str, Any]]:
    all_signals: List[Dict] = []
    for sym in symbols:
        if verbose:
            print(f"  [{sym:5}] scanning ... ", end="", flush=True)
        data = load_symbol_candles(sym, chart_dir, lookback_days)
        if data is None:
            if verbose:
                print("NO DATA")
            continue
        c15_all, c1h_all = data
        sigs = scan_symbol(sym, c15_all, c1h_all)
        all_signals.extend(sigs)
        if verbose:
            print(f"OK  {len(sigs):>3} signals")
    return sorted(all_signals, key=lambda s: (s["date"], s["birth_time"], s["symbol"]))


def run(
    chart_dir:     str           = CHART_DIR,
    symbols:       List[str]     = SYMBOLS,
    top_n:         int           = TOP_N_DAILY,
    lookback_days: Optional[int] = None,
    verbose:       bool          = True,
) -> List[Dict[str, Any]]:
    raw      = scan_all(chart_dir, symbols, lookback_days, verbose)
    selected = select_daily(raw, top_n)
    return selected


# -- Display -------------------------------------------------------------------

def print_signals(
    signals:          List[Dict[str, Any]],
    label:            str  = "PAPER SIGNALS",
    show_score_comps: bool = False,
) -> None:
    W = 96
    print("\n" + "=" * W)
    print(f"  {label}  --  PAPER MODE  --  NOT EXECUTABLE")
    print(f"  Engine : B + C + atr_pct <= {ATR_THRESHOLD} + Top-{TOP_N_DAILY}/day")
    print(f"  N      : {len(signals)}")
    print("=" * W)
    if not signals:
        print("  No signals."); return
    hdr = "  {:<10}  {:<5}  {:<6}  {:<5}  {:>8}  {:>8}  {:>8}  {:>6}  {:>6}  {:>6}"
    print(hdr.format("Date", "Time", "Symbol", "Dir", "Entry", "Stop", "TP1",
                     "ATR%", "Score", "R-pts"))
    print("  " + "-" * (W - 2))
    for s in signals:
        print(hdr.format(
            s["date"], s["birth_time"], s["symbol"], s["direction"][:4],
            f"{s['entry_price']:.2f}", f"{s['stop_price']:.2f}", f"{s['tp1']:.2f}",
            f"{s['atr_pct']:.3f}", f"{s['rank_score']:.1f}", f"{s['risk_pts']:.2f}",
        ))
        if show_score_comps:
            c = s.get("score_comps", {})
            print(f"           HTF={c.get('htf',0):.0f} "
                  f"Birth={c.get('birth',0):.0f} "
                  f"Sess={c.get('sess',0):.0f} "
                  f"Ext={c.get('ext',0):.1f} "
                  f"Conf={c.get('conf',0):.0f} "
                  f"BQ={c.get('bq',0):.1f}")
    print("=" * W)


# -- Standalone main ----------------------------------------------------------

def main() -> None:
    print("\n" + "=" * 96)
    print("  analyzer_bc_core.py  --  PAPER MODE  (self-contained, no backtest deps)")
    print(f"  Run : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  ATR filter : atr_pct <= {ATR_THRESHOLD}")
    print(f"  Daily limit: Top-{TOP_N_DAILY} per day by rank_score")
    print("=" * 96 + "\n")
    raw      = scan_all(verbose=True)
    selected = select_daily(raw, TOP_N_DAILY)
    print(f"\n  Total raw signals (ATR-filtered) : {len(raw)}")
    print(f"  After Top-{TOP_N_DAILY}/day selection    : {len(selected)}")
    print_signals(raw,      label=f"ALL FILTERED SIGNALS (N={len(raw)})")
    print_signals(selected, label=f"SELECTED: Top-{TOP_N_DAILY}/day (N={len(selected)})",
                  show_score_comps=True)


if __name__ == "__main__":
    main()
