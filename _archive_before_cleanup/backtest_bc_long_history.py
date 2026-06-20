# -*- coding: utf-8 -*-
"""
backtest_bc_long_history.py  --  B+C + atr_pct<=0.52 on 6-12 months
=====================================================================
Approved 2026-06-20.

Reads pre-fetched OHLCV data from:
    chart_data/{SYMBOL}_15m.json
    chart_data/{SYMBOL}_1H.json

Data must be populated first by running:
    python tv_datafeed.py --symbols AAPL AMD TSLA AVGO COST LLY PANW CRM
                                    QQQ SPY MSFT META AMZN GOOGL NVDA NFLX
                          --interval 15 --bars 5000 --no-1d

5000 x 15m bars = ~192 trading days = ~9 months.
1H bars are fetched automatically alongside 15m (default behavior).

Rules:
  - B+C scan identical to backtest_bc_feature_study.py
  - atr_pct <= 0.52 filter (exact, not optimized)
  - All other thresholds unchanged

Reports:
  - Per-symbol: span (months), N unfiltered, N filtered
  - Time windows: 30d, 60d, 90d, 180d, Max available
  - Each window: No-filter vs atr_pct<=0.52  (N, WR, PF, TotalR, LV%)
  - Leave-one-out on Max-available filtered pool
  - Robustness verdict

Do not modify analyzer_x2.  Research only.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Try proper timezone handling ─────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    def _utc_to_et(dt_utc: datetime) -> datetime:
        return dt_utc.astimezone(_ET).replace(tzinfo=None)
except Exception:
    # Rough DST approximation: EDT (UTC-4) Mar-Oct, EST (UTC-5) Nov-Feb
    _ET = None
    def _utc_to_et(dt_utc: datetime) -> datetime:
        offset = 4 if 3 <= dt_utc.month <= 10 else 5
        return (dt_utc.replace(tzinfo=None) - timedelta(hours=offset))

# ── Import scanner unchanged from feature study ───────────────────────────────
try:
    from backtest_bc_feature_study import (
        _scan_with_features,
        W,
    )
except ImportError as e:
    print(f"Cannot import backtest_bc_feature_study: {e}")
    sys.exit(1)

try:
    from analyzer_x2 import Candle
except ImportError as e:
    print(f"Cannot import analyzer_x2: {e}"); sys.exit(1)


# ── Config ────────────────────────────────────────────────────────────────────

ATR_THRESHOLD = 0.52

SYMBOLS: List[str] = [
    "AAPL", "AMD",  "TSLA", "AVGO", "COST", "LLY",  "PANW", "CRM",
    "QQQ",  "SPY",  "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
]

CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")

WINDOWS: List[Tuple[str, int]] = [
    ("30 days",  30),
    ("60 days",  60),
    ("90 days",  90),
    ("180 days", 180),
    ("Max avail", 9999),
]


# ── JSON loader ───────────────────────────────────────────────────────────────

def _load_json(symbol: str, tf: str) -> Optional[List[Candle]]:
    """
    Reads chart_data/{SYMBOL}_{tf}.json and returns List[Candle].

    Timestamps in the JSON are UTC ISO strings ("2026-05-07T17:30:00+00:00").
    They are stored as naive UTC datetimes so that the scanner's session
    filter (SESSION_CUTOFF=525, calibrated on UTC hours) works correctly:
      UTC 13:30 = ET 09:30 open  -> session_min = 240
      UTC 18:15 = ET 14:15 cutoff -> session_min = 525  (excluded)
    Converting to ET would break that gate for all market-hours bars.
    """
    path = os.path.join(CHART_DIR, f"{symbol}_{tf}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        print(f"  JSON read error {path}: {e}")
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
            # Keep UTC: strip only the timezone suffix, keep raw UTC h/m/s
            dt = datetime.strptime(times[i][:16].replace("T", " "), "%Y-%m-%d %H:%M")
            vol = float(volumes[i]) if i < len(volumes) else 0.0
            candles.append(Candle(
                timestamp = dt,
                open      = float(opens[i]),
                high      = float(highs[i]),
                low       = float(lows[i]),
                close     = float(closes[i]),
                volume    = vol,
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


# ── Metrics ───────────────────────────────────────────────────────────────────

def _m(trades: List[Dict]) -> Dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0, "lv": 0.0}
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
        "lv":     lv / n * 100,
    }

def _hline(c: str = "-") -> str:
    return "  " + c * (W - 2)

def _slice(trades: List[Dict], days: int) -> List[Dict]:
    if days >= 9999:
        return trades
    cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [t for t in trades if t["date"] >= cutoff]


# ── Reporting ─────────────────────────────────────────────────────────────────

def _print_symbol_coverage(all_trades: List[Dict]) -> None:
    print("\n" + "=" * W)
    print("  SYMBOL COVERAGE")
    print("=" * W)
    hdr = "  {:<6}  {:>7}  {:>7}  {:>5}  {:>5}  {:>5}"
    print(hdr.format("Symbol", "First", "Last", "Months", "N all", "N filt"))
    print(_hline())
    for sym in SYMBOLS:
        st = [t for t in all_trades if t["symbol"] == sym]
        if not st:
            print(hdr.format(sym, "-", "-", "-", "-", "-"))
            continue
        dates = sorted(t["date"] for t in st)
        first, last = dates[0], dates[-1]
        d0 = datetime.strptime(first, "%Y-%m-%d")
        d1 = datetime.strptime(last,  "%Y-%m-%d")
        months = round((d1 - d0).days / 30.4, 1)
        n_all  = len(st)
        n_filt = sum(1 for t in st if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD)
        print(hdr.format(sym, first, last, f"{months:.1f}m", n_all, n_filt))
    print("=" * W)


def _print_window_table(all_trades: List[Dict]) -> None:
    print("\n" + "=" * W)
    print("  ROBUSTNESS ACROSS WINDOWS  --  No filter vs atr_pct<=0.52")
    print("=" * W)
    hdr = "  {:<12}  {:>4}  {:>5}  {:>4}  {:>7}  {:>6}  ||  {:>4}  {:>5}  {:>4}  {:>7}  {:>6}"
    print(hdr.format("Window",
                      "N", "WR%", "PF", "TotalR", "LV%",
                      "N", "WR%", "PF", "TotalR", "LV%"))
    sub = " " * 14 + "-- No filter --" + " " * 22 + "-- atr_pct<=0.52 --"
    print(f"  {sub}")
    print(_hline())
    for label, days in WINDOWS:
        wt = _slice(all_trades, days)
        ft = [t for t in wt if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD]
        mn = _m(wt)
        mf = _m(ft)
        print(hdr.format(
            label,
            mn["n"], f"{mn['wr']:.1f}%", f"{mn['pf']:.2f}", f"{mn['totalr']:+.1f}R", f"{mn['lv']:.1f}%",
            mf["n"], f"{mf['wr']:.1f}%", f"{mf['pf']:.2f}", f"{mf['totalr']:+.1f}R", f"{mf['lv']:.1f}%",
        ))
    print("=" * W)


def _print_by_symbol(all_trades: List[Dict], label: str, trades: List[Dict]) -> None:
    print(f"\n  By symbol [{label}]:")
    hdr = "  {:<6}  {:>4}  {:>5}  {:>4}  {:>7}  |  {:>4}  {:>5}  {:>4}  {:>7}"
    print(hdr.format("Sym",
                      "N", "WR%", "PF", "TotalR",
                      "N", "WR%", "PF", "TotalR"))
    sub = " " * 8 + "-- No filter --" + " " * 20 + "-- atr_pct<=0.52 --"
    print(f"  {sub}")
    print(_hline())
    filt = [t for t in trades if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD]

    rows = []
    for sym in SYMBOLS:
        nf = [t for t in trades if t["symbol"] == sym]
        ff = [t for t in filt   if t["symbol"] == sym]
        mn, mf = _m(nf), _m(ff)
        if mn["n"] == 0 and mf["n"] == 0:
            continue
        rows.append((sym, mn, mf))

    for sym, mn, mf in sorted(rows, key=lambda x: -x[2]["totalr"]):
        def _f(m, k):
            if m["n"] == 0: return "-"
            return {"n": str(m["n"]), "wr": f"{m['wr']:.0f}%",
                    "pf": f"{m['pf']:.2f}", "totalr": f"{m['totalr']:+.1f}R"}[k]
        print(hdr.format(sym,
                          _f(mn,"n"), _f(mn,"wr"), _f(mn,"pf"), _f(mn,"totalr"),
                          _f(mf,"n"), _f(mf,"wr"), _f(mf,"pf"), _f(mf,"totalr")))


def _print_leave_one_out(label: str, trades: List[Dict]) -> None:
    fm = _m(trades)
    if fm["n"] == 0:
        print(f"  Leave-one-out [{label}]: no trades."); return

    print(f"\n  Leave-one-out [{label}]:")
    print(f"  Full: N={fm['n']}  WR={fm['wr']:.1f}%  PF={fm['pf']:.2f}  "
          f"TotalR={fm['totalr']:+.1f}R")
    print(f"  dPF negative = symbol was helping.")
    hdr = "  {:<6}  {:>4}  {:>5}  {:>4}  {:>7}  {:>7}  {:>7}"
    print(hdr.format("Removed", "N", "WR%", "PF", "TotalR", "dWR", "dPF"))
    print(_hline())

    rows = [(sym, _m([t for t in trades if t["symbol"] != sym])) for sym in SYMBOLS]
    rows.sort(key=lambda x: x[1]["pf"] - fm["pf"])
    for sym, sm in rows:
        if _m([t for t in trades if t["symbol"] == sym])["n"] == 0:
            continue
        d_pf = sm["pf"] - fm["pf"]
        d_wr = sm["wr"] - fm["wr"]
        flag = ""
        if d_pf <= -0.40: flag = "  <-- carrying result"
        elif d_pf >= +0.40: flag = "  <-- dragging result"
        print(hdr.format(sym, sm["n"], f"{sm['wr']:.1f}%", f"{sm['pf']:.2f}",
                          f"{sm['totalr']:+.1f}R", f"{d_wr:+.1f}pp", f"{d_pf:+.2f}") + flag)

    # Concentration check
    top_r  = max((_m([t for t in trades if t["symbol"] == s])["totalr"] for s in SYMBOLS), default=0)
    tot_r  = fm["totalr"]
    top_sym = max(SYMBOLS, key=lambda s: _m([t for t in trades if t["symbol"] == s])["totalr"])
    print()
    if tot_r > 0 and top_r > 0:
        pct = top_r / tot_r * 100
        if pct > 60:
            print(f"  CONCENTRATION: {top_sym} = {pct:.0f}% of TotalR  -- concentrated")
        elif pct > 40:
            print(f"  MODERATE: {top_sym} = {pct:.0f}% of TotalR")
        else:
            print(f"  DISTRIBUTED: top sym ({top_sym}) = {pct:.0f}% of TotalR")
    else:
        print(f"  TotalR <= 0  -- filter not helpful in this window")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  B+C LONG HISTORY VALIDATION  --  chart_data JSON source")
    print(f"  Filter  : atr_pct <= {ATR_THRESHOLD}  (exact, not optimized)")
    print(f"  Scanner : backtest_bc_feature_study._scan_with_features (unchanged)")
    print(f"  Data    : {CHART_DIR}")
    print(f"  Run     : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W)

    # Check data availability first
    missing = [s for s in SYMBOLS
               if not os.path.exists(os.path.join(CHART_DIR, f"{s}_15m.json"))]
    if missing:
        print(f"\n  Missing 15m files for: {', '.join(missing)}")
        print(f"  Run this command to fetch them:")
        print()
        syms = " ".join(SYMBOLS)
        print(f"  ! python tv_datafeed.py --symbols {syms} --interval 15 --bars 5000 --no-1d")
        print()
        if len(missing) == len(SYMBOLS):
            print("  No data available at all. Fetch data first, then re-run.")
            return
        print(f"  Continuing with {len(SYMBOLS) - len(missing)} available symbols...\n")
    else:
        print()

    all_trades: List[Dict] = []

    for sym in SYMBOLS:
        path15 = os.path.join(CHART_DIR, f"{sym}_15m.json")
        if not os.path.exists(path15):
            print(f"  [{sym:5}] SKIP  (no 15m file)")
            continue

        print(f"  [{sym:5}] loading ... ", end="", flush=True)
        dl = _load_symbol(sym)
        if dl is None:
            print("SKIP  (load failed or <100 candles)"); continue

        c15_all, c1h_all = dl
        trades = _scan_with_features(sym, c15_all, c1h_all)
        all_trades.extend(trades)

        wins  = sum(1 for t in trades if t["result"] == "WIN")
        loss  = sum(1 for t in trades if t["result"] == "LOSS")
        n_f   = sum(1 for t in trades if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD)
        span  = ""
        if trades:
            dates = sorted(t["date"] for t in trades)
            d0 = datetime.strptime(dates[0],  "%Y-%m-%d")
            d1 = datetime.strptime(dates[-1], "%Y-%m-%d")
            months = (d1 - d0).days / 30.4
            span = f"  span={months:.1f}mo"
        print(f"OK  {len(c15_all):>5} 15m bars  {len(trades):>3} BC  "
              f"WR={wins/max(wins+loss,1)*100:.0f}%  filt={n_f}/{len(trades)}{span}")

    if not all_trades:
        print("\n  No trades found."); return

    n_f = sum(1 for t in all_trades if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD)
    print(f"\n  Total: {len(all_trades)} B+C trades  |  "
          f"atr_pct<={ATR_THRESHOLD}: {n_f} ({n_f/max(len(all_trades),1)*100:.0f}%)")

    _print_symbol_coverage(all_trades)
    _print_window_table(all_trades)

    # Detailed per-window breakdown (Max available + 90d)
    for label, days in [("Max avail", 9999), ("90 days", 90), ("30 days", 30)]:
        wt   = _slice(all_trades, days)
        ft   = [t for t in wt if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD]
        if not wt:
            continue
        print("\n" + "=" * W)
        print(f"  DETAIL -- {label}  (N={len(wt)} unfiltered  |  N={len(ft)} filtered)")
        print("=" * W)
        _print_by_symbol(all_trades, label, wt)
        _print_leave_one_out(f"{label} -- atr_pct<={ATR_THRESHOLD}", ft)

    # Verdict
    max_all  = _slice(all_trades, 9999)
    max_filt = [t for t in max_all if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD]
    ma, mf   = _m(max_all), _m(max_filt)

    no_meta_filt = [t for t in max_filt if t["symbol"] != "META"]
    nm = _m(no_meta_filt)

    pos_syms = [s for s in SYMBOLS
                if _m([t for t in max_filt if t["symbol"] == s])["pf"] >= 1.0
                and _m([t for t in max_filt if t["symbol"] == s])["n"] > 0]

    print("\n" + "=" * W)
    print("  VERDICT")
    print("=" * W)
    print(f"  Full B+C (max avail)   : N={ma['n']}  WR={ma['wr']:.1f}%  "
          f"PF={ma['pf']:.2f}  TotalR={ma['totalr']:+.1f}R  LV={ma['lv']:.1f}%")
    print(f"  ATR filtered           : N={mf['n']}  WR={mf['wr']:.1f}%  "
          f"PF={mf['pf']:.2f}  TotalR={mf['totalr']:+.1f}R  LV={mf['lv']:.1f}%")
    print(f"  ATR filt, no META      : N={nm['n']}  WR={nm['wr']:.1f}%  "
          f"PF={nm['pf']:.2f}  TotalR={nm['totalr']:+.1f}R")
    print(f"  Symbols PF>=1.0 (filt) : {len(pos_syms)}  ({', '.join(pos_syms) or 'none'})")
    print("=" * W)


if __name__ == "__main__":
    main()
