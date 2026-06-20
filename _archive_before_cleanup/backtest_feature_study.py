# -*- coding: utf-8 -*-
"""
backtest_feature_study.py  -  Feature Correlation Study  (Research Only)
=========================================================================
For every X2 trade, compute 13 real-time features at the entry bar
and measure Pearson + Spearman correlation with three outcome targets:
  1. win_binary  (1 = WIN, 0 = LOSS/BE)
  2. r           (total R earned)
  3. pct_done    (how deep into the swing move the entry was)

All distance features are direction-adjusted so that positive values
always mean "favorable for the trade direction" -- this prevents
LONG/SHORT signals from cancelling each other in the correlation.

Features computed:
  adx               ADX at entry bar
  adx_slope         ADX change over last 5 bars (rising = strengthening trend)
  atr_expansion     current ATR / ATR 20 bars ago  (>1 = expanding volatility)
  vol_ratio         current bar volume / 20-bar avg (from analyzer)
  dist_vwap         (VWAP - price) / ATR  if LONG  -- positive = below VWAP
                    (price - VWAP) / ATR  if SHORT -- positive = above VWAP
  dist_ema20        similar direction-adjusted distance from 15m EMA20
  dist_ema50        similar direction-adjusted distance from 15m EMA50
  htf_strength      1H EMA20 vs EMA50 pct gap, direction-signed
                    positive = HTF trend agrees with trade direction
  session_min       minutes since 09:30 ET  (0 = open, 390 = close)
  zone_fresh        zone freshness at entry (from X2 trade dict if present)
  birth_age         bars from swing extreme to birth event (b_any)
  bars_since        bars from swing extreme to entry (from X2 trade dict)
  bars_after_birth  = bars_since - birth_age  (how long after birth we enter)

Do not modify analyzer_x2.  Research only.  No strategy changes.
"""
from __future__ import annotations

import sys
import csv
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
        _detect_zone, _zone_bounds, _zone_mid,
        MIN_HISTORY,
    )
except Exception as e:
    print(f"Cannot import backtest_runner_x2: {e}"); sys.exit(1)

try:
    from analyzer_x2 import Candle, _volume_ratio
except Exception as e:
    print(f"Cannot import analyzer_x2: {e}"); sys.exit(1)

try:
    from backtest_entry_timing import (
        _safe_avg, _bucket,
        SYMBOLS, DAYS, SWING_LOOKBACK,
        _run_symbol as _run_x2,
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


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# ── VWAP for one trading day ──────────────────────────────────────────────────

def _daily_vwap(c15_slice: List[Candle], date_str: str) -> float:
    """Volume-weighted average price for bars up to current bar on same day."""
    day = [c for c in c15_slice if c.timestamp.strftime("%Y-%m-%d") == date_str]
    if not day:
        return c15_slice[-1].close
    total_pv = sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in day)
    total_v  = sum(c.volume for c in day)
    return total_pv / max(total_v, 1.0)


# ── Feature extraction at one bar ────────────────────────────────────────────

def _compute_features(
    c15_all:   List[Candle],
    c1h_all:   List[Candle],
    bar_idx:   int,
    direction: str,      # "LONG" or "SHORT"
    bars_since: int,     # from X2 trade dict (swing to entry)
    zone_fresh_x2: Optional[float],   # from X2 trade dict if available
) -> Dict[str, Any]:
    """
    Compute all 13 features at bar_idx using only data up to that bar.
    No look-forward.  Direction-adjusts all signed distance features.
    """
    price = c15_all[bar_idx].close
    ts    = c15_all[bar_idx].timestamp
    date  = ts.strftime("%Y-%m-%d")

    # Window slices (no look-forward)
    c15  = c15_all[: bar_idx + 1]
    c1h  = [c for c in c1h_all if c.timestamp <= ts] or c15[:40]

    zone_setup = c15[-220:]
    if len(zone_setup) < 60:
        return {}

    # ── 1. ADX and ATR now ────────────────────────────────────────────────────
    market_now = _market_state_from(zone_setup, price)
    adx_now    = market_now.adx
    atr_now    = market_now.atr_14

    # ── 2. ADX slope (5 bars ago) ────────────────────────────────────────────
    adx_slope = 0.0
    if bar_idx >= 5:
        z5 = c15_all[max(0, bar_idx - 219 - 5): bar_idx - 4]
        if len(z5) >= 30:
            m5 = _market_state_from(z5, c15_all[bar_idx - 5].close)
            adx_slope = round(adx_now - m5.adx, 2)

    # ── 3. ATR expansion (vs 20 bars ago) ────────────────────────────────────
    atr_expansion = 1.0
    if bar_idx >= 20:
        z20 = c15_all[max(0, bar_idx - 219 - 20): bar_idx - 19]
        if len(z20) >= 30:
            m20 = _market_state_from(z20, c15_all[bar_idx - 20].close)
            atr_expansion = round(atr_now / max(m20.atr_14, 1e-9), 3)

    # ── 4. Volume ratio ───────────────────────────────────────────────────────
    try:
        vol_ratio = round(float(_volume_ratio(zone_setup)), 3)
    except Exception:
        vol_ratio = 1.0

    # ── 5. VWAP distance (direction-adjusted) ────────────────────────────────
    vwap = _daily_vwap(c15, date)
    raw_vwap = (price - vwap) / max(atr_now, 1e-9)
    # positive = favorable: LONG wants price BELOW vwap, SHORT wants ABOVE
    dist_vwap = round(-raw_vwap if direction == "LONG" else raw_vwap, 3)

    # ── 6-7. EMA20 / EMA50 distances (direction-adjusted) ────────────────────
    closes = [c.close for c in c15]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    raw_e20 = (price - ema20) / max(atr_now, 1e-9)
    raw_e50 = (price - ema50) / max(atr_now, 1e-9)
    # positive = price below indicator (pullback opportunity in direction)
    dist_ema20 = round(-raw_e20 if direction == "LONG" else raw_e20, 3)
    dist_ema50 = round(-raw_e50 if direction == "LONG" else raw_e50, 3)

    # ── 8. HTF trend strength (direction-signed) ─────────────────────────────
    h_closes = [c.close for c in c1h] if len(c1h) >= 50 else closes
    h_ema20 = _ema(h_closes, 20)
    h_ema50 = _ema(h_closes, 50)
    raw_htf = (h_ema20 - h_ema50) / max(abs(h_ema50), 1e-9) * 100.0
    # positive = HTF trend agrees with trade direction
    htf_strength = round(raw_htf if direction == "LONG" else -raw_htf, 3)

    # ── 9. Session time ───────────────────────────────────────────────────────
    h, m = ts.hour, ts.minute
    # Minutes since 09:30 ET (negative values clipped to 0)
    session_min = max(0, (h - 9) * 60 + m - 30)

    # ── 10. Zone freshness ────────────────────────────────────────────────────
    # Use value from X2 trade dict if present, else recompute
    if zone_fresh_x2 is not None:
        zone_fresh = round(float(zone_fresh_x2), 3)
    else:
        try:
            swing_in_setup = max(0, len(zone_setup) - 1 - bars_since)
            zone = _detect_zone(zone_setup, direction, atr_now, swing_in_setup)
            fr   = float(getattr(zone, "freshness", 0.0) or 0.0) if zone else 0.0
            zone_fresh = round(fr, 3)
        except Exception:
            zone_fresh = 0.0

    # ── 11-14. Birth features ─────────────────────────────────────────────────
    swing_idx = len(c15) - 1 - bars_since
    b_any = bars_since   # default: no birth found
    b_qual = 0.0
    disp_qual_val = 0.0
    confluence = 0

    if 0 <= swing_idx < len(c15):
        b_expand = _cap(_birth_expand(c15, swing_idx, direction, atr_now), bars_since)
        b_vol    = _cap(_birth_volume(c15, swing_idx),                      bars_since)
        b_struct = _cap(_birth_structure(c15, swing_idx, direction),        bars_since)
        b_any    = min(b_expand, b_vol, b_struct)

        b_bar = swing_idx + b_any
        if 0 <= b_bar < len(c15):
            bc     = c15[b_bar]
            b_body = abs(bc.close - bc.open)
            b_qual = round(min(1.0, b_body / max(atr_now, 1e-9)), 3)
            try:
                disp_qual_val = round(_disp_quality(c15, b_bar, direction, atr_now), 3)
            except Exception:
                disp_qual_val = 0.0

        near = b_any + 3
        confluence = sum([
            1 if b_expand <= near else 0,
            1 if b_vol    <= near else 0,
            1 if b_struct <= near else 0,
        ])

    bars_after_birth = bars_since - b_any

    return {
        "adx":            round(adx_now,       2),
        "adx_slope":      round(adx_slope,     2),
        "atr_expansion":  round(atr_expansion, 3),
        "vol_ratio":      round(vol_ratio,     3),
        "dist_vwap":      dist_vwap,
        "dist_ema20":     dist_ema20,
        "dist_ema50":     dist_ema50,
        "htf_strength":   htf_strength,
        "session_min":    session_min,
        "zone_fresh":     zone_fresh,
        "birth_age":      b_any,
        "bars_since":     bars_since,
        "bars_after_birth": bars_after_birth,
        "b_qual":         b_qual,
        "disp_qual":      disp_qual_val,
        "confluence":     confluence,
    }


# ── Statistics helpers ────────────────────────────────────────────────────────

def _pearson(x: List[float], y: List[float]) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mx = sum(x) / n;  my = sum(y) / n
    cov  = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    vx   = sum((xi - mx) ** 2 for xi in x)
    vy   = sum((yi - my) ** 2 for yi in y)
    denom = (vx * vy) ** 0.5
    return round(cov / denom, 4) if denom > 0 else 0.0


def _spearman(x: List[float], y: List[float]) -> float:
    """Rank-based Pearson = Spearman (handles ties by average rank)."""
    n = len(x)
    if n < 3:
        return 0.0

    def _ranks(vals: List[float]) -> List[float]:
        indexed = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and vals[indexed[j]] == vals[indexed[j + 1]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[indexed[k]] = avg_rank
            i = j + 1
        return r

    return _pearson(_ranks(x), _ranks(y))


# ── Main study loop ───────────────────────────────────────────────────────────

def _run_study() -> List[Dict[str, Any]]:
    """
    Download data per symbol, get X2 trades, compute features at each
    entry bar, return one row per trade with features + outcomes.
    """
    rows: List[Dict] = []

    for sym in SYMBOLS:
        print(f"  [{sym:5}] ", end="", flush=True)

        # X2 trades (this download is internal to _run_x2)
        try:
            x2_trades, _ = _run_x2(sym)
        except Exception as e:
            print(f"X2 error: {e}")
            continue
        if not x2_trades:
            print("0 X2 trades")
            continue
        print(f"{len(x2_trades):>3} X2 trades | ", end="", flush=True)

        # Fresh download for feature computation
        end   = datetime.today()
        start = end - timedelta(days=DAYS + 3)
        try:
            df15 = _flat(yf.download(sym, start=start, end=end, interval="15m",
                                      progress=False, auto_adjust=True))
            df1h = _flat(yf.download(sym, start=start, end=end, interval="1h",
                                      progress=False, auto_adjust=True))
        except Exception as e:
            print(f"download error: {e}")
            continue
        if df15 is None or len(df15) < MIN_HISTORY:
            print("insufficient data")
            continue

        c15_all = _to_candles(df15)
        c1h_all = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []

        ts_map: Dict[str, int] = {
            c.timestamp.strftime("%Y-%m-%d %H:%M"): i
            for i, c in enumerate(c15_all)
        }

        computed = 0
        for t in x2_trades:
            ts_key    = f"{t['date']} {t['time']}"
            bar_idx   = ts_map.get(ts_key)
            if bar_idx is None:
                continue
            direction = "LONG" if t["direction"] == "CALL" else "SHORT"

            zf_x2 = t.get("zone_fresh")  # use X2's value if present

            feats = _compute_features(
                c15_all, c1h_all, bar_idx,
                direction, int(t["bars_since"]), zf_x2,
            )
            if not feats:
                continue

            win_bin = 1 if t["result"] == "WIN" else 0

            row = {
                "symbol":    t["symbol"],
                "date":      t["date"],
                "time":      t["time"],
                "direction": t["direction"],
                "win_binary": win_bin,
                "r":          float(t["r"]),
                "pct_done":   float(t.get("pct_done", 50.0)),
                "result":     t["result"],
                "bucket":     t.get("bucket", ""),
                **feats,
            }
            rows.append(row)
            computed += 1

        print(f"features computed for {computed}/{len(x2_trades)}")

    return rows


# ── Reporting ─────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    "adx", "adx_slope", "atr_expansion", "vol_ratio",
    "dist_vwap", "dist_ema20", "dist_ema50", "htf_strength",
    "session_min", "zone_fresh", "birth_age", "bars_since",
    "bars_after_birth", "b_qual", "disp_qual", "confluence",
]

FEATURE_LABELS = {
    "adx":             "ADX",
    "adx_slope":       "ADX slope (5b)",
    "atr_expansion":   "ATR expansion (20b)",
    "vol_ratio":       "Volume ratio",
    "dist_vwap":       "Dist from VWAP (dir-adj)",
    "dist_ema20":      "Dist from EMA20 (dir-adj)",
    "dist_ema50":      "Dist from EMA50 (dir-adj)",
    "htf_strength":    "HTF trend strength (dir-adj)",
    "session_min":     "Session time (min since open)",
    "zone_fresh":      "Zone freshness",
    "birth_age":       "Birth age (bars from swing)",
    "bars_since":      "Bars since swing",
    "bars_after_birth":"Bars after birth",
    "b_qual":          "Birth quality",
    "disp_qual":       "Displacement quality",
    "confluence":      "Birth confluence (0-3)",
}


def _corr_table(rows: List[Dict]) -> None:
    if not rows:
        return
    n  = len(rows)
    W  = 90

    targets = [
        ("win_binary", "Win/Loss"),
        ("r",          "Total R"),
        ("pct_done",   "pct_done"),
    ]

    print("\n" + "=" * W)
    print(f"  FEATURE CORRELATION  --  {n} X2 trades  (Pearson r  |  Spearman rho)")
    print(f"  Significance threshold: |r| > {2/n**0.5:.3f}  (n={n}, 2/sqrt(n))")
    print(f"  Direction-adjusted: dist_vwap, dist_ema20, dist_ema50, htf_strength")
    print(f"  positive win_binary r = feature associated with wins")
    print("=" * W)

    hdr = "  {:<32}  {:>10}  {:>10}  {:>10}  {:>10}  {:>8}"
    print(hdr.format(
        "Feature",
        "Win P", "Win S",
        "R P",   "R S",
        "Pct P",
    ) + "  Rank")
    print("  " + "-" * (W - 2))

    sig = 2.0 / n ** 0.5   # rough significance threshold

    results = []
    for col in FEATURE_COLS:
        vals = [float(r[col]) for r in rows if col in r]
        if len(vals) < 10:
            continue

        corrs = {}
        for tgt, _ in targets:
            y = [float(r[tgt]) for r in rows if col in r]
            if len(y) != len(vals):
                continue
            corrs[f"{tgt}_p"] = _pearson(vals, y)
            corrs[f"{tgt}_s"] = _spearman(vals, y)

        # Composite rank: mean absolute Pearson across all three targets
        abs_r = abs(corrs.get("win_binary_p", 0.0))
        abs_r_r = abs(corrs.get("r_p", 0.0))
        abs_p_r = abs(corrs.get("pct_done_p", 0.0))
        avg_abs = (abs_r + abs_r_r + abs_p_r) / 3.0

        results.append((col, corrs, avg_abs))

    results.sort(key=lambda x: -x[2])

    for rank, (col, corrs, avg_abs) in enumerate(results, 1):
        def fmt(key):
            v = corrs.get(key, 0.0)
            marker = "*" if abs(v) >= sig else " "
            return f"{v:+.3f}{marker}"

        print(hdr.format(
            FEATURE_LABELS.get(col, col)[:32],
            fmt("win_binary_p"), fmt("win_binary_s"),
            fmt("r_p"),         fmt("r_s"),
            fmt("pct_done_p"),
        ) + f"  #{rank}")

    print("  " + "-" * (W - 2))
    print(f"  * = |r| > {sig:.3f}  (approx significant at n={n})")
    print(f"  Columns: Win/Loss Pearson | Win/Loss Spearman | R Pearson | R Spearman | pct_done Pearson")
    print("=" * W)


def _bucket_breakdown(rows: List[Dict], feature: str, n_buckets: int = 5) -> None:
    """
    Split all trades into N equal-size buckets by feature value.
    Show WR%, AvgR, AvgPct, N for each bucket.
    """
    if not rows or feature not in rows[0]:
        return
    sorted_rows = sorted(rows, key=lambda r: float(r.get(feature, 0)))
    n   = len(sorted_rows)
    sz  = max(1, n // n_buckets)
    lbl = FEATURE_LABELS.get(feature, feature)

    W = 74
    print(f"\n  {lbl} -- bucket breakdown ({n} trades, {n_buckets} equal-size bins):")
    hdr = "  {:<20}  {:>6}  {:>6}  {:>7}  {:>8}"
    print(hdr.format("Range", "N", "WR%", "AvgR", "AvgPct"))
    print("  " + "-" * (W - 2))

    for i in range(n_buckets):
        lo = i * sz
        hi = (i + 1) * sz if i < n_buckets - 1 else n
        grp = sorted_rows[lo:hi]
        if not grp:
            continue
        vlo  = float(grp[0][feature])
        vhi  = float(grp[-1][feature])
        wins = sum(1 for r in grp if r["result"] == "WIN")
        loss = sum(1 for r in grp if r["result"] == "LOSS")
        dec  = wins + loss
        wr   = wins / dec * 100 if dec else 0.0
        ar   = _safe_avg([float(r["r"]) for r in grp])
        ap   = _safe_avg([float(r["pct_done"]) for r in grp])
        print(hdr.format(
            f"[{vlo:.2f}, {vhi:.2f}]",
            len(grp), f"{wr:.1f}%", f"{ar:+.2f}R", f"{ap:.1f}%",
        ))
    print("  " + "=" * (W - 2))


def _print_data_summary(rows: List[Dict]) -> None:
    W = 76
    print("\n" + "=" * W)
    print(f"  DATA SUMMARY  --  {len(rows)} trades with features")
    print("=" * W)

    # Overall outcomes
    wins = sum(1 for r in rows if r["result"] == "WIN")
    loss = sum(1 for r in rows if r["result"] == "LOSS")
    be   = sum(1 for r in rows if r["result"] == "BE")
    dec  = wins + loss
    wr   = wins / dec * 100 if dec else 0.0
    gw   = sum(r["r"] for r in rows if r["r"] > 0)
    gl   = abs(sum(r["r"] for r in rows if r["r"] < 0))
    pf   = gw / gl if gl > 0 else 99.0
    tr   = sum(r["r"] for r in rows)

    print(f"  N={len(rows)}  WIN={wins}  LOSS={loss}  BE={be}  "
          f"WR={wr:.1f}%  PF={pf:.2f}  TotalR={tr:+.2f}R")

    # Feature value ranges
    print(f"\n  Feature value ranges:")
    hdr = "  {:<32}  {:>9}  {:>9}  {:>9}  {:>9}"
    print(hdr.format("Feature", "Min", "Mean", "Max", "StdDev"))
    print("  " + "-" * 72)
    for col in FEATURE_COLS:
        vals = [float(r[col]) for r in rows if col in r]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        std  = (sum((v - mean)**2 for v in vals) / max(len(vals)-1, 1))**0.5
        print(hdr.format(
            FEATURE_LABELS.get(col, col)[:32],
            f"{min(vals):.2f}", f"{mean:.2f}", f"{max(vals):.2f}", f"{std:.2f}",
        ))
    print("  " + "=" * 72)


def _write_csv(rows: List[Dict]) -> None:
    if not rows:
        return
    path   = "backtest_feature_study.csv"
    fields = (["symbol", "date", "time", "direction", "result", "win_binary",
               "r", "pct_done", "bucket"]
              + FEATURE_COLS)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"\n  Feature matrix CSV -> {path}  ({len(rows)} rows x {len(FEATURE_COLS)} features)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 80)
    print("  FEATURE CORRELATION STUDY  --  X2 Trades")
    print(f"  Symbols : {', '.join(SYMBOLS)}")
    print(f"  Window  : {DAYS} days")
    print(f"  Targets : win_binary (1/0)  |  r (total R)  |  pct_done")
    print(f"  Methods : Pearson r  +  Spearman rho (rank-based)")
    print(f"  Run at  : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80 + "\n")

    rows = _run_study()

    if not rows:
        print("No trades computed."); return

    _print_data_summary(rows)
    _corr_table(rows)

    # Bucket breakdowns for the most likely predictive features
    breakdowns = ["bars_since", "session_min", "zone_fresh",
                  "birth_age", "bars_after_birth", "htf_strength", "dist_ema50"]
    for feat in breakdowns:
        _bucket_breakdown(rows, feat)

    _write_csv(rows)

    # Final ranked summary (top 5 by avg |Pearson| across targets)
    print("\n  TOP FEATURES by mean |Pearson r| across all three targets:")
    print("  (These are the strongest real-time predictors of outcome)")
    print("  " + "-" * 60)
    results = []
    for col in FEATURE_COLS:
        vals = [float(r[col]) for r in rows if col in r]
        if len(vals) < 10:
            continue
        abs_sum = 0.0
        for tgt in ["win_binary", "r", "pct_done"]:
            y = [float(r[tgt]) for r in rows if col in r]
            if len(y) == len(vals):
                abs_sum += abs(_pearson(vals, y))
        results.append((col, abs_sum / 3.0))
    results.sort(key=lambda x: -x[1])
    for rank, (col, avg) in enumerate(results[:5], 1):
        lbl = FEATURE_LABELS.get(col, col)
        print(f"  #{rank}  {lbl:<38}  avg|r| = {avg:.3f}")
    print("  " + "=" * 60)


if __name__ == "__main__":
    main()
