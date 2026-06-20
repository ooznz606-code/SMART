# -*- coding: utf-8 -*-
"""
analyzer_bc_atr_daily.py  --  Experimental B+C Signal Analyzer
==============================================================
PAPER / ANALYZER MODE ONLY.
NOT integrated with execution.py.  NOT connected to live orders.
Do NOT modify analyzer_x2.py.  Do NOT modify execution.py.

Strategy  : B + C + atr_pct <= 0.52 + Top-3 per day
Data      : chart_data/ JSON files (TradingView WebSocket feed)

Rules implemented:
  1. Birth event mandatory  (b_exp, b_vol, or b_str within 2-bar window)
  2. B = displacement bar   (body >= 0.50 ATR, dir_close >= 0.65)
  3. C = structure break    (close > max-high of swing-to-birth range)
  4. Entry only when B and C both confirm within CONF_START..CONF_END
  5. atr_pct = ATR / entry_price * 100 must be <= 0.52
  6. Exclude last 75 minutes (session_min >= 525 = UTC 18:15 = ET 14:15)
  7. Max TOP_N_DAILY trades per calendar day across all symbols
     (ranked by rank_score from backtest_daily_selection, unchanged)
  8. PAPER MODE: outputs signal list only, no execution

Public API:
  scan_symbol(symbol, c15_all, c1h_all) -> List[Signal]
  scan_all(chart_dir, symbols, lookback_days=None) -> List[Signal]
  select_daily(signals, top_n=TOP_N_DAILY) -> List[Signal]
  run(chart_dir, symbols, top_n, lookback_days, verbose) -> List[Signal]

Signal keys (all available at fire point, before outcome is known):
  symbol, direction, date, birth_time, entry_time, entry_price,
  stop_price, risk_pts, tp1, atr, atr_pct, rank_score,
  htf_strength, b_qual, birth_age, entry_offset, confluence,
  disp_qual, dist_birth, session_min, price_ext_atr
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

# ── Research infrastructure imports (read-only) ───────────────────────────────
try:
    from backtest_runner_x2 import (
        _market_state_from, _detect_zone, _zone_bounds, _htf_direction,
        MIN_HISTORY, TP1_R,
    )
except Exception as e:
    print(f"[analyzer_bc_atr_daily] Cannot import backtest_runner_x2: {e}")
    sys.exit(1)

try:
    from analyzer_x2 import Candle
except Exception as e:
    print(f"[analyzer_bc_atr_daily] Cannot import analyzer_x2: {e}")
    sys.exit(1)

try:
    from backtest_entry_timing import (
        _get_profile, _swing_extreme, SWING_LOOKBACK, SIM_BARS,
    )
except Exception as e:
    print(f"[analyzer_bc_atr_daily] Cannot import backtest_entry_timing: {e}")
    sys.exit(1)

try:
    from backtest_move_birth import _birth_expand, _birth_volume, _birth_structure, _cap
except Exception as e:
    print(f"[analyzer_bc_atr_daily] Cannot import backtest_move_birth: {e}")
    sys.exit(1)

try:
    from backtest_x3_birth_ict import _disp_quality
except Exception as e:
    print(f"[analyzer_bc_atr_daily] Cannot import backtest_x3_birth_ict: {e}")
    sys.exit(1)

try:
    from backtest_daily_selection import _rank_score, _session_pts
except Exception as e:
    print(f"[analyzer_bc_atr_daily] Cannot import backtest_daily_selection: {e}")
    sys.exit(1)


# ── Strategy constants (DO NOT CHANGE -- validated in backtest studies) ───────

CONF_START         = 2
CONF_END           = 6
PRE_B_QUAL_MIN     = 0.55
PRE_DISP_QUAL_MIN  = 0.35
PRE_BIRTH_AGE_MAX  = 15
HTF_MIN_STRENGTH   = 0.30
SESSION_CUTOFF     = 525      # UTC session_min >= 525 = ET 14:15 = last 75 min
STOP_BUFFER        = 0.22
DISPLACE_BODY_MIN  = 0.50
DISPLACE_CLOSE_MIN = 0.65
ATR_THRESHOLD      = 0.52     # atr_pct <= this to qualify
TOP_N_DAILY        = 3        # max signals emitted per calendar day

SYMBOLS: List[str] = [
    "AAPL", "AMD",  "TSLA", "AVGO", "COST", "LLY",  "PANW", "CRM",
    "QQQ",  "SPY",  "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
]

CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")

# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# ── Data loader ───────────────────────────────────────────────────────────────

def load_json(symbol: str, tf: str, chart_dir: str) -> Optional[List[Candle]]:
    """
    Load chart_data/{symbol}_{tf}.json and return List[Candle].
    Timestamps kept as naive UTC datetimes (calibrated to SESSION_CUTOFF=525).
    """
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
                timestamp=dt, open=float(opens[i]), high=float(highs[i]),
                low=float(lows[i]), close=float(closes[i]), volume=vol,
            ))
        except Exception:
            continue
    return candles if len(candles) >= 100 else None


def load_symbol_candles(
    symbol:     str,
    chart_dir:  str,
    lookback_days: Optional[int] = None,
) -> Optional[Tuple[List[Candle], List[Candle]]]:
    c15 = load_json(symbol, "15m", chart_dir)
    c1h = load_json(symbol, "1H",  chart_dir) or []
    if c15 is None:
        return None
    if lookback_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        c15 = [c for c in c15 if c.timestamp >= cutoff]
        c1h = [c for c in c1h if c.timestamp >= cutoff]
        if len(c15) < MIN_HISTORY:
            return None
    return c15, c1h


# ── Core scanner (no simulation -- paper signals only) ────────────────────────

def scan_symbol(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:
    """
    Scan c15_all for B+C fire events.  Does NOT simulate outcomes.
    Returns all signals that pass pre-filter gates and ATR_THRESHOLD.
    Daily selection is applied separately by select_daily().
    """
    prof    = _get_profile(symbol)
    min_adx = float(prof.get("min_adx", 17))
    max_adx = float(prof.get("max_adx", 68))

    signals: List[Dict] = []
    last_birth: Dict[str, int] = {"LONG": -999, "SHORT": -999}
    safe_max = len(c15_all) - SIM_BARS - CONF_END - 2

    for i in range(MIN_HISTORY, safe_max):
        c15 = c15_all[: i + 1]
        ts  = c15_all[i].timestamp          # birth bar UTC timestamp

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

            # Structure level for C signal
            birth_area = c15_all[swing_global : birth_global + 1]
            if not birth_area:
                continue
            if direction == "LONG":
                h_struct = max(c.high for c in birth_area)
            else:
                l_struct = min(c.low  for c in birth_area)

            # Stop reference
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

            # ── Confirmation window: wait for both B and C ────────────────
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

                    # ── ATR filter ────────────────────────────────────────
                    atr_pct = atr / max(entry_px, 1e-9) * 100.0
                    if atr_pct > ATR_THRESHOLD:
                        break   # fails filter -- skip this birth

                    # ── Risk / TP1 ────────────────────────────────────────
                    stop_price = stop_ref
                    risk_pts   = abs(entry_px - stop_price)
                    if risk_pts < 1e-9:
                        break
                    tp1 = (entry_px + risk_pts * TP1_R if direction == "LONG"
                           else entry_px - risk_pts * TP1_R)

                    # ── Rank score ────────────────────────────────────────
                    if direction == "LONG":
                        price_ext_atr = (entry_px - bc.close) / max(atr, 1e-9)
                    else:
                        price_ext_atr = (bc.close - entry_px) / max(atr, 1e-9)
                    price_ext_atr = max(0.0, price_ext_atr)

                    rank, rank_comps = _rank_score(
                        htf_strength  = htf_str,
                        birth_age     = b_any,
                        ts            = ts,        # birth bar timestamp (UTC)
                        price_ext_atr = price_ext_atr,
                        confluence    = confluence,
                        b_qual        = b_qual,
                    )

                    # ── Distance for display ──────────────────────────────
                    if direction == "LONG":
                        dist_birth = (entry_px - bc.close) / max(atr, 1e-9)
                    else:
                        dist_birth = (bc.close - entry_px) / max(atr, 1e-9)
                    dist_birth = max(0.0, dist_birth)

                    signals.append({
                        # Identification
                        "symbol":       symbol,
                        "direction":    direction,
                        "date":         ts.strftime("%Y-%m-%d"),
                        "birth_time":   ts.strftime("%H:%M"),
                        "entry_time":   entry_ts.strftime("%H:%M"),
                        "birth_ts":     ts,
                        "entry_ts":     entry_ts,
                        # Trade parameters
                        "entry_price":  round(entry_px,    4),
                        "stop_price":   round(stop_price,  4),
                        "risk_pts":     round(risk_pts,    4),
                        "tp1":          round(tp1,         4),
                        # ATR filter field
                        "atr":          round(atr,         4),
                        "atr_pct":      round(atr_pct,     4),
                        # Score
                        "rank_score":   rank,
                        "score_comps":  rank_comps,
                        # Signal quality
                        "htf_strength": round(htf_str,        3),
                        "b_qual":       round(b_qual,          3),
                        "birth_age":    b_any,
                        "entry_offset": entry_offset,
                        "confluence":   confluence,
                        "disp_qual":    round(disp_qual,       3),
                        "dist_birth":   round(dist_birth,      3),
                        "session_min":  session_min,
                        "price_ext_atr":round(price_ext_atr,   3),
                    })
                    break   # one signal per birth event

    return signals


# ── Daily selection ───────────────────────────────────────────────────────────

def select_daily(
    signals: List[Dict[str, Any]],
    top_n:   int = TOP_N_DAILY,
) -> List[Dict[str, Any]]:
    """
    From all signals, keep top_n per calendar day ranked by rank_score DESC.
    Calendar day is determined by birth bar date.
    """
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for sig in signals:
        by_date[sig["date"]].append(sig)
    selected: List[Dict] = []
    for date in sorted(by_date):
        ranked = sorted(by_date[date], key=lambda s: -s["rank_score"])
        selected.extend(ranked[:top_n])
    return selected


# ── Full scan ─────────────────────────────────────────────────────────────────

def scan_all(
    chart_dir:     str              = CHART_DIR,
    symbols:       List[str]        = SYMBOLS,
    lookback_days: Optional[int]    = None,
    verbose:       bool             = False,
) -> List[Dict[str, Any]]:
    """
    Load chart_data for each symbol and run scan_symbol.
    Returns ALL signals passing ATR filter (before daily selection).
    """
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
    """
    Full pipeline: scan -> ATR filter (already applied in scan) -> daily selection.
    Returns the top_n selected signals per day.
    """
    raw      = scan_all(chart_dir, symbols, lookback_days, verbose)
    selected = select_daily(raw, top_n)
    return selected


# ── Display ───────────────────────────────────────────────────────────────────

def print_signals(
    signals: List[Dict[str, Any]],
    label:   str = "PAPER SIGNALS",
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
            s["date"],
            s["birth_time"],
            s["symbol"],
            s["direction"][:4],
            f"{s['entry_price']:.2f}",
            f"{s['stop_price']:.2f}",
            f"{s['tp1']:.2f}",
            f"{s['atr_pct']:.3f}",
            f"{s['rank_score']:.1f}",
            f"{s['risk_pts']:.2f}",
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


# ── Standalone main ───────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 96)
    print("  analyzer_bc_atr_daily.py  --  PAPER MODE")
    print(f"  Run : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  ATR filter : atr_pct <= {ATR_THRESHOLD}")
    print(f"  Daily limit: Top-{TOP_N_DAILY} per day by rank_score")
    print(f"  PAPER ONLY : No execution.  No live orders.")
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
