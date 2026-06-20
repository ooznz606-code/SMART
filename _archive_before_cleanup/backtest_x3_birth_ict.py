# -*- coding: utf-8 -*-
"""
backtest_x3_birth_ict.py — X3 Birth ICT Experimental Backtest
==============================================================
Entry logic:
  Swing extreme → Birth anchor (B1/B2/B3) → Displacement quality →
  Zone (15m, from swing) → Confidence score → Entry

All five hard gates are computed from bars UP TO AND INCLUDING the
entry bar — zero look-forward in the entry decision:
  1. birth_age        <= 10  bars from swing extreme
  2. bars_after_birth <= 5   bars from birth event to entry
  3. birth_quality    >= 0.85  (birth candle body / ATR)
  4. displacement_quality >= 0.55  (body strength × directional close × acceleration)
  5. zone_freshness   >= 0.80

pct_done is calculated AFTER the entry decision using future bars.
It is a diagnostic metric only and has no effect on whether a trade fires.

Comparison output: Current X2  vs  X3 Birth ICT
No changes to analyzer_x2.py.  Experimental file only.
"""
from __future__ import annotations

import sys
import csv
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance")
    sys.exit(1)

try:
    from backtest_runner_x2 import (
        _flat, _to_candles, _market_state_from, _htf_direction,
        _detect_zone, _zone_bounds, _zone_mid,
        TP1_R, SETUP_COOLDOWN, MIN_HISTORY,
    )
except Exception as e:
    print(f"Cannot import backtest_runner_x2: {e}")
    sys.exit(1)

try:
    from analyzer_x2 import Candle, _volume_ratio
except Exception as e:
    print(f"Cannot import analyzer_x2: {e}")
    sys.exit(1)

try:
    from backtest_entry_timing import (
        _get_profile, _swing_extreme, _swing_end, _bucket, _safe_avg,
        SYMBOLS, DAYS, SWING_LOOKBACK, SWING_LOOKFWD, SIM_BARS,
        _run_symbol as _run_current_raw,
    )
except Exception as e:
    print(f"Cannot import backtest_entry_timing: {e}")
    sys.exit(1)

try:
    from backtest_move_birth import (
        _birth_expand, _birth_volume, _birth_structure, _cap,
    )
except Exception as e:
    print(f"Cannot import backtest_move_birth: {e}")
    sys.exit(1)


# ── X3 parameters — real-time gates only ─────────────────────────────────────
MAX_BIRTH_AGE   = 10    # birth must fire within N bars of swing extreme
BIRTH_RECENCY   = 5     # entry bar must be within N bars of birth event
MIN_BIRTH_QUAL  = 0.85  # birth candle body / ATR
MIN_DISP_QUAL   = 0.55  # composite: body_r × dir_bonus × accel_bonus
MIN_ZONE_FRESH  = 0.80  # zone freshness
MAX_ZONE_DIST   = 3.2   # ATR distance from zone.mid to price
STOP_BUFFER     = 0.22  # zone boundary padding (mirrors analyzer_x2)


# ── Displacement quality (pure, no look-forward) ──────────────────────────────

def _disp_quality(c15: List[Candle], birth_idx: int,
                  direction: str, atr: float) -> float:
    """
    Composite quality score for the birth candle as an ICT displacement.

      body_r     = min(1.0, body / ATR)          — raw candle strength
      dir_bonus  = 1.0 if close in upper half (LONG) / lower half (SHORT)
                   0.6 otherwise  — rejects wicks that close against direction
      accel_bonus= 1.0 if body > prior candle body
                   0.8 otherwise  — rewards acceleration

    Returns body_r × dir_bonus × accel_bonus, capped at 1.0.
    All inputs are from bars <= birth_idx (no look-forward).
    """
    if birth_idx < 0 or birth_idx >= len(c15):
        return 0.0

    bc         = c15[birth_idx]
    body       = abs(bc.close - bc.open)
    body_r     = min(1.0, body / max(atr, 1e-9))
    crange     = max(bc.high - bc.low, 1e-9)

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


def _x3_score(disp_qual: float, zq: float, fr: float,
               htf: str, vr: float) -> float:
    """
    X3 confidence score.  All inputs available at entry time.
    Max possible: 20 + 25 + 20 + 10 + 5 + 5 = 85.
    """
    s  = 20.0
    s += disp_qual * 25.0
    s += zq        * 20.0
    s += fr        * 10.0
    s += 5.0 if htf != "NEUTRAL" else 0.0
    s += 5.0 if vr  >= 0.8       else 2.0
    return s


# ── X3 scan ───────────────────────────────────────────────────────────────────

def _download_symbol(symbol: str) -> Optional[Tuple[List[Candle], List[Candle]]]:
    """
    Download 15m + 1h data once per symbol.
    Returns (c15_all, c1h_all) or None on failure.
    Separated from the scan so the parameter sweep can reuse the same
    download across all 18 gate combinations.
    """
    end   = datetime.today()
    start = end - timedelta(days=DAYS + 3)
    try:
        df15 = _flat(yf.download(symbol, start=start, end=end, interval="15m",
                                  progress=False, auto_adjust=True))
        df1h = _flat(yf.download(symbol, start=start, end=end, interval="1h",
                                  progress=False, auto_adjust=True))
    except Exception as e:
        print(f"  download error: {e}")
        return None
    if df15 is None or len(df15) < MIN_HISTORY:
        return None
    c15_all = _to_candles(df15)
    c1h_all = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []
    return c15_all, c1h_all


def _scan_x3(
    symbol:         str,
    c15_all:        List[Candle],
    c1h_all:        List[Candle],
    birth_age_max:  int,
    birth_recency:  int,
    birth_qual_min: float,
    zone_fresh_min: float,
) -> List[Dict[str, Any]]:
    """
    Core X3 scan — operates on pre-loaded candle data with explicit gate values.
    MIN_DISP_QUAL stays as a module constant (not included in the sweep).
    pct_done is computed after entry is locked; it is diagnostic only.
    """
    prof     = _get_profile(symbol)
    min_adx  = float(prof.get("min_adx", 17))
    max_adx  = float(prof.get("max_adx", 68))
    min_conf = float(prof.get("min_conf", 72))

    trades: List[Dict[str, Any]] = []
    last_bar = -999

    for i in range(MIN_HISTORY, len(c15_all) - SIM_BARS - 1):
        c15 = c15_all[:i + 1]
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15

        zone_setup = c15[-220:]
        if len(zone_setup) < 60:
            continue

        htf_feed = c1h[-300:] if c1h and len(c1h) >= 60 else c15[-220:]
        market   = _market_state_from(zone_setup, c15_all[i].close)
        atr      = market.atr_14
        adx      = market.adx

        if adx < min_adx or adx > max_adx:
            continue
        if (i - last_bar) < SETUP_COOLDOWN:
            continue

        htf  = _htf_direction(htf_feed, market)
        dirs = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in dirs:
            price     = c15_all[i].close
            sw_start, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            swing_idx = len(c15) - 1 - bars_since

            b_expand = _cap(_birth_expand(c15, swing_idx, direction, atr), bars_since)
            b_vol    = _cap(_birth_volume(c15, swing_idx),                  bars_since)
            b_struct = _cap(_birth_structure(c15, swing_idx, direction),    bars_since)
            b_any    = min(b_expand, b_vol, b_struct)

            if b_any > birth_age_max:
                continue

            bars_after_birth = bars_since - b_any
            if bars_after_birth > birth_recency:
                continue

            b_bar_idx = swing_idx + b_any
            if not (0 <= b_bar_idx < len(c15)):
                continue
            bc     = c15[b_bar_idx]
            b_body = abs(bc.close - bc.open)
            b_qual = min(1.0, b_body / max(atr, 1e-9))

            if b_qual < birth_qual_min:
                continue

            disp_qual = _disp_quality(c15, b_bar_idx, direction, atr)
            if disp_qual < MIN_DISP_QUAL:
                continue

            swing_in_setup = max(0, len(zone_setup) - 1 - bars_since)
            zone = _detect_zone(zone_setup, direction, atr, swing_in_setup)
            if zone is None:
                continue
            zb = _zone_bounds(zone)
            if not zb:
                continue
            z_top, z_bot = zb
            z_mid        = _zone_mid(zone)

            zq = float(getattr(zone, "quality",   0.7) or 0.7)
            fr = float(getattr(zone, "freshness", 0.7) or 0.7)

            if fr < zone_fresh_min:
                continue
            if abs(price - z_mid) / max(atr, 0.01) > MAX_ZONE_DIST:
                continue

            try:
                vr = _volume_ratio(zone_setup)
            except Exception:
                vr = 1.0

            score = _x3_score(disp_qual, zq, fr, htf, vr)
            if score < min_conf:
                continue

            # ── ALL GATES PASSED — ENTRY DECISION LOCKED ──────────────────────
            entry = price
            stop  = (z_bot - atr * STOP_BUFFER) if direction == "LONG" \
                    else (z_top + atr * STOP_BUFFER)
            risk  = abs(entry - stop)
            if risk <= 0:
                continue
            tp1 = entry + risk * TP1_R if direction == "LONG" else entry - risk * TP1_R

            birth_close = bc.close
            if direction == "LONG":
                price_ext = max(0.0, (entry - birth_close) / max(atr, 1e-9))
            else:
                price_ext = max(0.0, (birth_close - entry) / max(atr, 1e-9))

            # DIAGNOSTIC ONLY — not an entry gate
            future   = c15_all[i + 1 : i + 1 + SWING_LOOKFWD]
            sw_end   = _swing_end(future, direction)
            if direction == "LONG":
                total_mv = (sw_end - sw_start) if sw_end else None
                at_entry = entry - sw_start
            else:
                total_mv = (sw_start - sw_end) if sw_end else None
                at_entry = sw_start - entry
            pct = (round(at_entry / total_mv * 100, 1)
                   if (total_mv and total_mv > 0) else 50.0)

            outcome = "BE"; r_mult = 0.0; ep = entry; mfe = 0.0; mae = 0.0
            for fc in c15_all[i + 1 : i + 1 + SIM_BARS + 1]:
                if direction == "LONG":
                    mfe = max(mfe, (fc.high - entry) / risk)
                    mae = min(mae, (fc.low  - entry) / risk)
                    if fc.low  <= stop: outcome = "LOSS"; ep = stop; r_mult = -1.0; break
                    if fc.high >= tp1:  outcome = "WIN";  ep = tp1;  r_mult = TP1_R; break
                else:
                    mfe = max(mfe, (entry - fc.low)  / risk)
                    mae = min(mae, (entry - fc.high) / risk)
                    if fc.high >= stop: outcome = "LOSS"; ep = stop; r_mult = -1.0; break
                    if fc.low  <= tp1:  outcome = "WIN";  ep = tp1;  r_mult = TP1_R; break
            else:
                ep = c15_all[min(i + SIM_BARS, len(c15_all) - 1)].close

            trades.append({
                "symbol":           symbol,
                "date":             ts.strftime("%Y-%m-%d"),
                "time":             ts.strftime("%H:%M"),
                "direction":        "CALL" if direction == "LONG" else "PUT",
                "entry":            round(entry,    4),
                "stop":             round(stop,     4),
                "tp1":              round(tp1,      4),
                "exit_price":       round(ep,       4),
                "bars_since":       bars_since,
                "b_any":            b_any,
                "bars_after_birth": bars_after_birth,
                "price_ext_atr":    round(price_ext, 3),
                "b_qual":           round(b_qual,    3),
                "disp_qual":        round(disp_qual, 3),
                "zone_fresh":       round(fr,        3),
                "zone_qual":        round(zq,        3),
                "score":            round(score,     1),
                "pct_done":         pct,
                "bucket":           _bucket(pct),
                "mfe_r":            round(mfe,   2),
                "mae_r":            round(mae,   2),
                "result":           outcome,
                "r":                round(r_mult, 2),
            })
            last_bar = i
            break

    return trades


def _run_x3(symbol: str) -> List[Dict[str, Any]]:
    """Thin wrapper: download once, scan with module-level default parameters."""
    result = _download_symbol(symbol)
    if result is None:
        return []
    c15_all, c1h_all = result
    return _scan_x3(symbol, c15_all, c1h_all,
                    MAX_BIRTH_AGE, BIRTH_RECENCY, MIN_BIRTH_QUAL, MIN_ZONE_FRESH)


# ── Metrics ───────────────────────────────────────────────────────────────────

def _metrics(trades: List[Dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0,
                "mfe": 0.0, "mae": 0.0,
                "early": 0.0, "mid": 0.0, "late": 0.0, "vlate": 0.0,
                "avg_bars": 0.0, "avg_pct": 0.0}
    w   = sum(1 for t in trades if t["result"] == "WIN")
    l   = sum(1 for t in trades if t["result"] == "LOSS")
    gw  = sum(t["r"] for t in trades if t["r"] > 0)
    gl  = abs(sum(t["r"] for t in trades if t["r"] < 0))
    dec = w + l
    return {
        "n":       n,
        "wr":      w / dec * 100 if dec else 0.0,
        "pf":      gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr":  sum(t["r"] for t in trades),
        "mfe":     _safe_avg([t["mfe_r"]    for t in trades]),
        "mae":     _safe_avg([t["mae_r"]    for t in trades]),
        "early":   sum(1 for t in trades if t["bucket"] == "Early")    / n * 100,
        "mid":     sum(1 for t in trades if t["bucket"] == "Mid")      / n * 100,
        "late":    sum(1 for t in trades if t["bucket"] == "Late")     / n * 100,
        "vlate":   sum(1 for t in trades if t["bucket"] == "VeryLate") / n * 100,
        "avg_bars": _safe_avg([t["bars_since"] for t in trades]),
        "avg_pct":  _safe_avg([t["pct_done"]   for t in trades]),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def _print_comparison(cm: Dict, xm: Dict) -> None:
    W = 72
    print("\n" + "=" * W)
    print("  COMPARISON: Current X2  vs  X3 Birth ICT")
    print(f"  Gates (real-time): birth_age<={MAX_BIRTH_AGE}  recency<={BIRTH_RECENCY}"
          f"  bqual>={MIN_BIRTH_QUAL}  dqual>={MIN_DISP_QUAL}  zfresh>={MIN_ZONE_FRESH}")
    print(f"  pct_done = diagnostic only (no future-leakage in entry logic)")
    print("=" * W)

    hdr = "{:<22}  {:>22}  {:>22}"
    print(hdr.format("Metric", "Current X2", "X3 Birth ICT"))
    print("-" * W)

    def row(label: str, cv: Any, xv: Any, fmt: str = "") -> None:
        if   fmt == "pct": fv = lambda v: f"{v:.1f}%"
        elif fmt == "r":   fv = lambda v: f"{v:+.2f}R"
        elif fmt == "f2":  fv = lambda v: f"{v:.2f}"
        elif fmt == "f1":  fv = lambda v: f"{v:.1f}"
        else:              fv = str
        print(hdr.format(label, fv(cv), fv(xv)))

    row("Signals (N)",          cm["n"],       xm["n"])
    row("Win Rate",             cm["wr"],       xm["wr"],      "pct")
    row("Profit Factor",        cm["pf"],       xm["pf"],      "f2")
    row("Total R",              cm["totalr"],   xm["totalr"],  "r")
    row("Avg MFE",              cm["mfe"],      xm["mfe"],     "f2")
    row("Avg MAE",              cm["mae"],      xm["mae"],     "f2")
    row("Avg bars since swing", cm["avg_bars"], xm["avg_bars"], "f1")
    row("Avg pct done (diag)",  cm["avg_pct"],  xm["avg_pct"],  "pct")
    print("-" * W)
    row("Early   (<25%)",       cm["early"],    xm["early"],   "pct")
    row("Mid     (25-50%)",     cm["mid"],      xm["mid"],     "pct")
    row("Late    (50-75%)",     cm["late"],     xm["late"],    "pct")
    row("VeryLate (75%+)",      cm["vlate"],    xm["vlate"],   "pct")
    lv_c = cm["late"] + cm["vlate"]
    lv_x = xm["late"] + xm["vlate"]
    print("-" * W)
    print(hdr.format("Late+VeryLate", f"{lv_c:.1f}%", f"{lv_x:.1f}%"))
    print("=" * W)


def _print_per_symbol(cur_all: List[Dict], x3_all: List[Dict]) -> None:
    W = 92
    print("\n" + "=" * W)
    print("  PER-SYMBOL  (C = Current X2  |  X3 = X3 Birth ICT)")
    print("=" * W)
    hdr = "{:<7}  {:>6}  {:>5}  {:>5}  {:>7}  {:>6}  |  {:>6}  {:>5}  {:>5}  {:>7}  {:>6}"
    print(hdr.format("Symbol",
                     "C:Sig", "C:WR", "C:PF", "C:TotR", "C:LV%",
                     "X:Sig", "X:WR", "X:PF", "X:TotR", "X:LV%"))
    print("-" * W)

    for sym in SYMBOLS:
        ct = [t for t in cur_all if t["symbol"] == sym]
        xt = [t for t in x3_all  if t["symbol"] == sym]
        cm = _metrics(ct)
        xm = _metrics(xt)
        print(hdr.format(
            sym,
            cm["n"], f"{cm['wr']:.0f}%", f"{cm['pf']:.2f}",
            f"{cm['totalr']:+.1f}R", f"{cm['late']+cm['vlate']:.0f}%",
            xm["n"], f"{xm['wr']:.0f}%", f"{xm['pf']:.2f}",
            f"{xm['totalr']:+.1f}R", f"{xm['late']+xm['vlate']:.0f}%",
        ))
    print("=" * W)


def _print_bucket_wr(cur_all: List[Dict], x3_all: List[Dict]) -> None:
    W = 82
    print("\n" + "=" * W)
    print("  WR BY BUCKET  (diagnostic — pct_done computed after entry)")
    print("=" * W)
    hdr = "{:<16}  {:>9}  {:>7}  {:>8}  |  {:>9}  {:>7}  {:>8}"
    print(hdr.format("Bucket",
                     "C:Count", "C:WR%", "C:AvgR",
                     "X:Count", "X:WR%", "X:AvgR"))
    print("-" * W)

    tot_c = max(len(cur_all), 1)
    tot_x = max(len(x3_all), 1)

    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        ct = [t for t in cur_all if t["bucket"] == bk]
        xt = [t for t in x3_all  if t["bucket"] == bk]
        cm = _metrics(ct); xm = _metrics(xt)
        c_avgr = _safe_avg([t["r"] for t in ct])
        x_avgr = _safe_avg([t["r"] for t in xt])
        print(hdr.format(
            bk,
            f"{len(ct)} ({len(ct)/tot_c*100:.0f}%)", f"{cm['wr']:.1f}%", f"{c_avgr:+.2f}R",
            f"{len(xt)} ({len(xt)/tot_x*100:.0f}%)", f"{xm['wr']:.1f}%", f"{x_avgr:+.2f}R",
        ))
    print("=" * W)


def _print_x3_internals(x3_all: List[Dict]) -> None:
    if not x3_all:
        return
    W = 70
    print("\n" + "=" * W)
    print("  X3 INTERNAL STATS (real-time gate values at entry)")
    print("=" * W)

    avg_b    = _safe_avg([t["b_any"]           for t in x3_all])
    avg_bab  = _safe_avg([t["bars_after_birth"] for t in x3_all])
    avg_bq   = _safe_avg([t["b_qual"]           for t in x3_all])
    avg_dq   = _safe_avg([t["disp_qual"]        for t in x3_all])
    avg_fr   = _safe_avg([t["zone_fresh"]       for t in x3_all])
    avg_sc   = _safe_avg([t["score"]            for t in x3_all])
    avg_mfe  = _safe_avg([t["mfe_r"]            for t in x3_all])
    avg_mae  = _safe_avg([t["mae_r"]            for t in x3_all])

    print(f"  Avg birth_age          : {avg_b:.1f} bars  (gate: <={MAX_BIRTH_AGE})")
    print(f"  Avg bars_after_birth   : {avg_bab:.1f} bars  (gate: <={BIRTH_RECENCY})")
    print(f"  Avg birth_quality      : {avg_bq:.3f}       (gate: >={MIN_BIRTH_QUAL})")
    print(f"  Avg disp_quality       : {avg_dq:.3f}       (gate: >={MIN_DISP_QUAL})")
    print(f"  Avg zone_freshness     : {avg_fr:.3f}       (gate: >={MIN_ZONE_FRESH})")
    print(f"  Avg confidence score   : {avg_sc:.1f}        (gate: >=72, max=85)")
    print(f"  Avg MFE                : {avg_mfe:.2f}R")
    print(f"  Avg MAE                : {avg_mae:.2f}R")

    # Per-symbol avg pct_done for context
    print()
    print("  Avg pct_done by symbol (diagnostic — not an entry gate):")
    for sym in SYMBOLS:
        st = [t for t in x3_all if t["symbol"] == sym]
        if st:
            avg_pct = _safe_avg([t["pct_done"] for t in st])
            print(f"    {sym:<7}: {avg_pct:.1f}%  (N={len(st)})")
    print("=" * W)


def _write_csv(x3_all: List[Dict]) -> None:
    if not x3_all:
        return
    path   = "backtest_x3_birth_ict.csv"
    rt_fields = ["symbol", "date", "time", "direction", "entry", "stop", "tp1",
                 "bars_since", "b_any", "bars_after_birth", "price_ext_atr",
                 "b_qual", "disp_qual", "zone_fresh", "zone_qual", "score"]
    diag_fields = ["pct_done", "bucket", "result", "r", "mfe_r", "mae_r", "exit_price"]
    fields = rt_fields + diag_fields
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in x3_all:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"  Saved -> {path}  ({len(x3_all)} rows)")


# ── Parameter sweep ──────────────────────────────────────────────────────────

def _param_sweep() -> None:
    """
    Download each symbol ONCE, then run _scan_x3 for all 18 gate combinations.
    Total downloads = len(SYMBOLS), not 18 * len(SYMBOLS).

    Swept parameters:
      MIN_BIRTH_QUAL  : 0.85, 0.75, 0.70
      MIN_ZONE_FRESH  : 0.80, 0.70, 0.60
      BIRTH_RECENCY   : 5, 8
      MAX_BIRTH_AGE   : fixed at 10

    Selection criteria (in order):
      1. PF     >= 1.5
      2. N      >= 30
      3. L+VL   <= 40%
      4. Max TotalR among qualifying combos
    """
    BQ_VALS:  List[float] = [0.85, 0.75, 0.70]
    ZF_VALS:  List[float] = [0.80, 0.70, 0.60]
    REC_VALS: List[int]   = [5, 8]
    AGE = MAX_BIRTH_AGE  # fixed

    combos: List[Tuple[float, float, int]] = [
        (bq, zf, rec)
        for bq  in BQ_VALS
        for zf  in ZF_VALS
        for rec in REC_VALS
    ]

    W = 96
    print("\n" + "=" * W)
    print(f"  PARAMETER SWEEP — X3 Birth ICT  (MAX_BIRTH_AGE={AGE} fixed)")
    print(f"  {len(combos)} combos: bq={BQ_VALS}  zf={ZF_VALS}  rec={REC_VALS}")
    print(f"  Download: 1x per symbol.  Scan: {len(combos)} passes per symbol.")
    print(f"  Criteria: PF>=1.5  N>=30  L+VL<=40%  then max TotalR")
    print("=" * W)

    # Accumulate per-combo trade lists across all symbols
    combo_trades: Dict[Tuple, List[Dict]] = {c: [] for c in combos}

    for sym in SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download_symbol(sym)
        if dl is None:
            print("SKIP")
            continue
        c15_all, c1h_all = dl
        counts = []
        for (bq, zf, rec) in combos:
            t = _scan_x3(sym, c15_all, c1h_all, AGE, rec, bq, zf)
            combo_trades[(bq, zf, rec)].extend(t)
            counts.append(len(t))
        print(f"OK  {sum(counts)} trade-events ({len(combos)} combos, "
              f"range {min(counts)}-{max(counts)} per combo)")

    # Build result rows
    rows: List[Dict] = []
    for (bq, zf, rec) in combos:
        tt  = combo_trades[(bq, zf, rec)]
        m   = _metrics(tt)
        lv  = m["late"] + m["vlate"]
        ap  = _safe_avg([t["pct_done"] for t in tt]) if tt else 0.0
        rows.append({"bq": bq, "zf": zf, "rec": rec,
                     "n": m["n"], "wr": m["wr"], "pf": m["pf"],
                     "totalr": m["totalr"], "lv": lv, "avgpct": ap})

    # Print table, grouped by bq block
    hdr = "  {:<5} {:<5} {:>3}  | {:>5}  {:>5}  {:>5}  {:>8}  {:>6}  {:>7}  {}"
    print("\n" + hdr.format("bq", "zf", "rec", "N", "WR%", "PF", "TotalR",
                            "LV%", "AvgPct", "FLAGS"))
    prev_bq: Optional[float] = None
    for r in rows:
        if r["bq"] != prev_bq:
            print("  " + "-" * (W - 2))
            prev_bq = r["bq"]
        f_pf = r["pf"]     >= 1.5
        f_n  = r["n"]      >= 30
        f_lv = r["lv"]     <= 40.0
        flags = ("PF " if f_pf else "   ") + ("N " if f_n else "  ") + ("LV" if f_lv else "  ")
        star  = "*** " if (f_pf and f_n and f_lv) else "    "
        print(hdr.format(
            f"{r['bq']:.2f}", f"{r['zf']:.2f}", r["rec"],
            r["n"], f"{r['wr']:.1f}", f"{r['pf']:.2f}",
            f"{r['totalr']:+.1f}R", f"{r['lv']:.0f}%", f"{r['avgpct']:.1f}%",
            star + flags.strip(),
        ))
    print("  " + "=" * (W - 2))

    # Select best
    cands = [r for r in rows if r["pf"] >= 1.5 and r["n"] >= 30 and r["lv"] <= 40.0]
    if cands:
        best = max(cands, key=lambda r: r["totalr"])
        print(f"\n  BEST COMBO (PF>=1.5, N>=30, LV<=40%, highest TotalR):")
        print(f"    MIN_BIRTH_QUAL={best['bq']:.2f}  MIN_ZONE_FRESH={best['zf']:.2f}"
              f"  BIRTH_RECENCY={best['rec']}")
        print(f"    N={best['n']}  WR={best['wr']:.1f}%  PF={best['pf']:.2f}"
              f"  TotalR={best['totalr']:+.2f}R  LV={best['lv']:.1f}%"
              f"  AvgPct={best['avgpct']:.1f}%")

        # Per-symbol breakdown for best combo
        bt = combo_trades[(best["bq"], best["zf"], best["rec"])]
        print(f"\n  Per-symbol — best combo:")
        h2 = "  {:<7}  {:>5}  {:>6}  {:>5}  {:>8}  {:>7}  {:>8}"
        print(h2.format("Symbol", "N", "WR%", "PF", "TotalR", "LV%", "AvgPct"))
        print("  " + "-" * 58)
        for sym in SYMBOLS:
            st  = [t for t in bt if t["symbol"] == sym]
            m   = _metrics(st)
            ap  = _safe_avg([t["pct_done"] for t in st]) if st else 0.0
            lv  = m["late"] + m["vlate"]
            print(h2.format(sym, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                            f"{m['totalr']:+.1f}R", f"{lv:.0f}%", f"{ap:.1f}%"))
        print("  " + "=" * 58)

        # WR by bucket
        print(f"\n  WR by bucket — best combo:")
        h3 = "  {:<16}  {:>6}  {:>7}  {:>8}"
        print(h3.format("Bucket", "N", "WR%", "AvgR"))
        print("  " + "-" * 42)
        for bk in ["Early", "Mid", "Late", "VeryLate"]:
            bbt  = [t for t in bt if t["bucket"] == bk]
            bm   = _metrics(bbt)
            avgr = _safe_avg([t["r"] for t in bbt])
            print(h3.format(bk, bm["n"], f"{bm['wr']:.1f}%", f"{avgr:+.2f}R"))
        print("  " + "=" * 42)

        # Save best-combo CSV
        path = "backtest_x3_sweep_best.csv"
        fields = ["symbol", "date", "time", "direction", "entry",
                  "bars_since", "b_any", "bars_after_birth", "price_ext_atr",
                  "b_qual", "disp_qual", "zone_fresh", "score",
                  "pct_done", "bucket", "result", "r", "mfe_r", "mae_r"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for row in bt:
                w.writerow({k: row.get(k, "") for k in fields})
        print(f"\n  Best-combo CSV saved -> {path}  ({len(bt)} rows)")

    else:
        # No combo meets all criteria — report top 3 by composite score
        scored = sorted(
            [(r, r["pf"] * (r["n"] ** 0.5) / max(r["lv"], 1.0))
             for r in rows if r["n"] >= 5],
            key=lambda x: -x[1],
        )
        print(f"\n  No combo met all 4 criteria.")
        print(f"  Top 3 by PF * sqrt(N) / LV (composite):")
        for r, sc in scored[:3]:
            print(f"    bq={r['bq']:.2f}  zf={r['zf']:.2f}  rec={r['rec']}"
                  f"  N={r['n']}  WR={r['wr']:.1f}%  PF={r['pf']:.2f}"
                  f"  TotalR={r['totalr']:+.2f}R  LV={r['lv']:.1f}%  [score={sc:.2f}]")

    print("=" * W)


# ── Gate analysis ────────────────────────────────────────────────────────────

def _gate_analysis(x3_all: List[Dict]) -> None:
    """
    Test real-time proxy gates for pct_done<=50 on the X3 baseline trade set.
    The X3 baseline already has 5 hard gates applied (birth_age, recency,
    b_qual, disp_qual, zone_fresh).  Each variant below adds one or more
    additional real-time filters on top of that baseline.

    price_ext_atr = |entry_price - birth_close| / ATR
                    Measures how far price moved from birth before entry.
                    Both prices known at entry time — no look-forward.
    """
    if not x3_all:
        print("\n  No X3 trades for gate analysis.")
        return

    # ── Named gate functions (avoids closure bugs in lambda lists) ────────────
    def g1(t: Dict) -> bool: return t["bars_since"]       <= 20
    def g2(t: Dict) -> bool: return t["bars_since"]       <= 15
    def g3(t: Dict) -> bool: return t["bars_after_birth"] <= 3
    def g4(t: Dict) -> bool: return t["price_ext_atr"]    <= 1.5
    def g5(t: Dict) -> bool: return t["price_ext_atr"]    <= 2.0

    singles: List[Tuple[str, Any]] = [
        ("X3 Baseline",         lambda t: True),
        ("+ bars_since<=20",    g1),
        ("+ bars_since<=15",    g2),
        ("+ bab<=3",            g3),
        ("+ ext<=1.5 ATR",      g4),
        ("+ ext<=2.0 ATR",      g5),
    ]
    pairs: List[Tuple[str, Any]] = [
        ("+ G1+G3",             lambda t: g1(t) and g3(t)),
        ("+ G1+G4",             lambda t: g1(t) and g4(t)),
        ("+ G1+G5",             lambda t: g1(t) and g5(t)),
        ("+ G2+G3",             lambda t: g2(t) and g3(t)),
        ("+ G2+G4",             lambda t: g2(t) and g4(t)),
        ("+ G2+G5",             lambda t: g2(t) and g5(t)),
        ("+ G3+G4",             lambda t: g3(t) and g4(t)),
        ("+ G3+G5",             lambda t: g3(t) and g5(t)),
    ]
    triples: List[Tuple[str, Any]] = [
        ("+ G1+G3+G4",          lambda t: g1(t) and g3(t) and g4(t)),
        ("+ G1+G3+G5",          lambda t: g1(t) and g3(t) and g5(t)),
        ("+ G2+G3+G4",          lambda t: g2(t) and g3(t) and g4(t)),
        ("+ G2+G3+G5",          lambda t: g2(t) and g3(t) and g5(t)),
    ]

    base_n   = len(x3_all)
    base_pct = _safe_avg([t["pct_done"] for t in x3_all])
    base_ext = _safe_avg([t["price_ext_atr"] for t in x3_all])

    W = 86
    print("\n" + "=" * W)
    print("  X3 GATE ANALYSIS — Real-Time Proxies for pct_done<=50")
    print(f"  Baseline: {base_n} trades | avg pct_done={base_pct:.1f}%"
          f" | avg price_ext={base_ext:.2f} ATR")
    print(f"  G1=bars_since<=20  G2=bars_since<=15  G3=bab<=3"
          f"  G4=ext<=1.5ATR  G5=ext<=2.0ATR")
    print(f"  N(%) = fraction of X3 baseline retained")
    print("=" * W)

    hdr = "{:<22}  {:>10}  {:>5}  {:>5}  {:>8}  {:>7}  {:>8}"
    print(hdr.format("Variant", "N (%base)", "WR%", "PF", "TotalR", "L+VL%", "AvgPct"))

    all_results: List[Tuple[str, Dict, float, float, Any]] = []

    def _print_group(group: List[Tuple[str, Any]]) -> None:
        print("-" * W)
        for name, fn in group:
            subset  = [t for t in x3_all if fn(t)]
            m       = _metrics(subset)
            lv      = m["late"] + m["vlate"]
            avg_pct = _safe_avg([t["pct_done"] for t in subset]) if subset else 0.0
            n_pct   = m["n"] / max(base_n, 1) * 100
            print(hdr.format(
                name,
                f"{m['n']} ({n_pct:.0f}%)",
                f"{m['wr']:.1f}",
                f"{m['pf']:.2f}",
                f"{m['totalr']:+.1f}R",
                f"{lv:.0f}%",
                f"{avg_pct:.1f}%",
            ))
            if name != "X3 Baseline":
                all_results.append((name, m, lv, avg_pct, fn))

    _print_group(singles)
    _print_group(pairs)
    _print_group(triples)
    print("=" * W)

    if not all_results:
        return

    # ── Identify best proxy: highest PF, minimum 30% signal retention ─────────
    MIN_N    = max(5, int(base_n * 0.30))
    eligible = [(n, m, lv, pct, fn) for n, m, lv, pct, fn in all_results
                if m["n"] >= MIN_N]

    if not eligible:
        print(f"\n  No variant retained N>={MIN_N} trades (30% of {base_n}).")
        return

    best_pf  = max(eligible, key=lambda r: r[1]["pf"])
    best_lv  = min(eligible, key=lambda r: r[2])

    print(f"\n  Best by PF     (N>={MIN_N}): {best_pf[0]}")
    print(f"    N={best_pf[1]['n']}  WR={best_pf[1]['wr']:.1f}%"
          f"  PF={best_pf[1]['pf']:.2f}  TotalR={best_pf[1]['totalr']:+.2f}R"
          f"  L+VL={best_pf[2]:.1f}%  AvgPct={best_pf[3]:.1f}%")

    if best_lv[0] != best_pf[0]:
        print(f"  Best L+VL reduction (N>={MIN_N}): {best_lv[0]}")
        print(f"    N={best_lv[1]['n']}  WR={best_lv[1]['wr']:.1f}%"
              f"  PF={best_lv[1]['pf']:.2f}  TotalR={best_lv[1]['totalr']:+.2f}R"
              f"  L+VL={best_lv[2]:.1f}%  AvgPct={best_lv[3]:.1f}%")

    # ── Per-symbol for best-by-PF variant ────────────────────────────────────
    best_trades = [t for t in x3_all if best_pf[4](t)]
    if not best_trades:
        return

    print(f"\n  Per-symbol — '{best_pf[0]}'  (best by PF, N>={MIN_N}):")
    h2 = "  {:<7}  {:>5}  {:>6}  {:>5}  {:>8}  {:>7}  {:>8}"
    print(h2.format("Symbol", "N", "WR%", "PF", "TotalR", "LV%", "AvgPct"))
    print("  " + "-" * 58)
    for sym in SYMBOLS:
        st = [t for t in best_trades if t["symbol"] == sym]
        if not st:
            print(h2.format(sym, 0, "—", "—", "—", "—", "—"))
            continue
        m       = _metrics(st)
        avg_pct = _safe_avg([t["pct_done"] for t in st])
        print(h2.format(
            sym, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
            f"{m['totalr']:+.1f}R",
            f"{m['late']+m['vlate']:.0f}%",
            f"{avg_pct:.1f}%",
        ))
    print("  " + "=" * 58)

    # ── WR by bucket for best-by-PF ───────────────────────────────────────────
    print(f"\n  WR by bucket — '{best_pf[0]}':")
    h3 = "  {:<16}  {:>6}  {:>7}  {:>8}"
    print(h3.format("Bucket", "N", "WR%", "AvgR"))
    print("  " + "-" * 42)
    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        bt    = [t for t in best_trades if t["bucket"] == bk]
        bm    = _metrics(bt)
        avgr  = _safe_avg([t["r"] for t in bt])
        print(h3.format(bk, bm["n"], f"{bm['wr']:.1f}%", f"{avgr:+.2f}R"))
    print("  " + "=" * 42)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 80)
    print("  SMART ICT — X3 Birth ICT  (Experimental Backtest)")
    print(f"  Symbols      : {', '.join(SYMBOLS)}")
    print(f"  Window       : {DAYS} days")
    print(f"  Real-time gates:")
    print(f"    birth_age <= {MAX_BIRTH_AGE}b  |  bars_after_birth <= {BIRTH_RECENCY}b")
    print(f"    b_qual >= {MIN_BIRTH_QUAL}  |  disp_qual >= {MIN_DISP_QUAL}"
          f"  |  zone_fresh >= {MIN_ZONE_FRESH}")
    print(f"  pct_done: diagnostic only (post-entry, no look-forward in gates)")
    print(f"  Run at       : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80 + "\n")

    cur_all: List[Dict] = []
    x3_all:  List[Dict] = []

    for sym in SYMBOLS:
        # Current X2 baseline
        print(f"  [{sym:5}] current   ... ", end="", flush=True)
        try:
            ct, _ = _run_current_raw(sym)
        except Exception as e:
            print(f"ERROR: {e}")
            ct = []
        cur_all.extend(ct)
        cn  = len(ct)
        clv = sum(1 for t in ct if t["bucket"] in ("Late", "VeryLate"))
        print(f"{cn:>3} signals | LV={clv} ({clv/cn*100:.0f}%)" if cn else "0 signals")

        # X3 Birth ICT
        print(f"  [{sym:5}] X3 birth  ... ", end="", flush=True)
        try:
            xt = _run_x3(sym)
        except Exception as e:
            print(f"ERROR: {e}")
            xt = []
        x3_all.extend(xt)
        xn  = len(xt)
        xlv = sum(1 for t in xt if t["bucket"] in ("Late", "VeryLate"))
        xb  = _safe_avg([t["b_any"] for t in xt]) if xt else 0.0
        print(f"{xn:>3} signals | LV={xlv} ({xlv/xn*100:.0f}%)  AvgBirth={xb:.1f}b" if xn
              else "0 signals")
        print()

    if not cur_all and not x3_all:
        print("No trades found.")
        return

    _print_comparison(_metrics(cur_all), _metrics(x3_all))
    _print_per_symbol(cur_all, x3_all)
    _print_bucket_wr(cur_all, x3_all)
    _print_x3_internals(x3_all)
    _gate_analysis(x3_all)
    _write_csv(x3_all)
    _param_sweep()

    # Final summary line
    cm = _metrics(cur_all); xm = _metrics(x3_all)
    lv_c = cm["late"] + cm["vlate"]; lv_x = xm["late"] + xm["vlate"]
    print(f"\n  {'-'*76}")
    print(f"  Current X2   : {cm['n']:>4} signals | LV={lv_c:.1f}%"
          f" | WR={cm['wr']:.1f}% | PF={cm['pf']:.2f} | TotalR={cm['totalr']:+.2f}R")
    print(f"  X3 Birth ICT : {xm['n']:>4} signals | LV={lv_x:.1f}%"
          f" | WR={xm['wr']:.1f}% | PF={xm['pf']:.2f} | TotalR={xm['totalr']:+.2f}R")
    print(f"  {'-'*76}")


if __name__ == "__main__":
    main()
