# -*- coding: utf-8 -*-
"""
backtest_bc_daily_report.py
===========================
Simulates B+C strategy outcomes on existing 15m chart_data candles.
Uses analyzer_bc_core only.  No backtest_* imports.

Outcome rules (per trade, evaluated bar-by-bar after entry close):
  WIN  : first bar whose high (LONG) / low (SHORT) touches TP1   -> +1.8 R
  LOSS : first bar whose low  (LONG) / high (SHORT) touches SL   -> -1.0 R
         SL takes priority when both SL and TP hit in the same bar
  BE   : neither hit within MAX_HOLD (40) bars                   ->  0.0 R

Per-day columns : Date | Trades | W | L | BE | WR% | TotalR | Symbols
Totals          : Trades | W | L | BE | WR% | PF | TotalR

Two sections are shown:
  [1] ALL Top-3/day   -- baseline (no execution gate)
  [2] GATE-FILTERED   -- signals that would route to live execution
      Gate: rank_score>=75, RVI=High, Regime=Trend, ATR%<=0.52, !AAPL, before 14:15 ET
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from analyzer_bc_core import (
    CHART_DIR,
    SYMBOLS,
    SIM_BARS,
    load_symbol_candles,
    scan_symbol,
    select_daily,
)
from analyzer_x2 import Candle
from smart_analyzer_bridge_bc import _enrich, passes_exec_gate

WIN_R    =  1.8
LOSS_R   = -1.0
BE_R     =  0.0
MAX_HOLD = SIM_BARS   # 40 bars after entry close


# -- simulation ----------------------------------------------------------------

def _find_idx(c15: List[Candle], ts: datetime) -> Optional[int]:
    for i, c in enumerate(c15):
        if c.timestamp == ts:
            return i
    return None


def simulate(
    c15:       List[Candle],
    entry_ts:  datetime,
    stop:      float,
    tp1:       float,
    direction: str,
) -> Tuple[str, float]:
    idx = _find_idx(c15, entry_ts)
    if idx is None:
        return "BE", BE_R

    sim_bars = c15[idx + 1 : idx + 1 + MAX_HOLD]
    if not sim_bars:
        return "BE", BE_R

    for bar in sim_bars:
        if direction == "LONG":
            if bar.low  <= stop:  return "LOSS", LOSS_R
            if bar.high >= tp1:   return "WIN",  WIN_R
        else:
            if bar.high >= stop:  return "LOSS", LOSS_R
            if bar.low  <= tp1:   return "WIN",  WIN_R

    return "BE", BE_R


# -- report helpers ------------------------------------------------------------

def _print_daily(results: List[Dict], title: str, W: int = 100) -> None:
    print("\n" + "=" * W)
    print(f"  {title}")
    print("=" * W)

    if not results:
        print("  (no trades)")
        print("=" * W)
        return

    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for t in results:
        by_date[t["date"]].append(t)

    hdr = "  {:<12}  {:>6}  {:>4}  {:>4}  {:>4}  {:>6}  {:>8}  {}"
    print(hdr.format("Date", "Trades", "W", "L", "BE", "WR%", "TotalR", "Symbols"))
    print("  " + "-" * (W - 2))

    total_trades = total_w = total_l = total_be = 0
    total_r   = 0.0
    gross_win = 0.0
    gross_los = 0.0

    for date in sorted(by_date):
        day  = by_date[date]
        dw   = sum(1 for t in day if t["outcome"] == "WIN")
        dl   = sum(1 for t in day if t["outcome"] == "LOSS")
        dbe  = sum(1 for t in day if t["outcome"] == "BE")
        dt   = len(day)
        dr   = sum(t["R"] for t in day)
        dwr  = dw / dt * 100 if dt else 0.0
        syms = sorted({t["symbol"] for t in day})

        total_trades += dt
        total_w      += dw
        total_l      += dl
        total_be     += dbe
        total_r      += dr
        gross_win    += sum(t["R"] for t in day if t["R"] > 0)
        gross_los    += sum(abs(t["R"]) for t in day if t["R"] < 0)

        print(hdr.format(
            date, dt, dw, dl, dbe,
            f"{dwr:.1f}%",
            f"{dr:+.2f}R",
            " ".join(syms),
        ))

    total_wr = total_w / total_trades * 100 if total_trades else 0.0
    pf_str   = (f"{gross_win / gross_los:.2f}" if gross_los > 0
                else "inf" if gross_win > 0 else "n/a")

    print("  " + "=" * (W - 2))
    print(hdr.format(
        "TOTAL", total_trades, total_w, total_l, total_be,
        f"{total_wr:.1f}%",
        f"{total_r:+.2f}R",
        "",
    ))
    print("=" * W)
    print()
    print(f"  Total trades  : {total_trades}")
    print(f"  Wins          : {total_w}")
    print(f"  Losses        : {total_l}")
    print(f"  Break-even    : {total_be}")
    print(f"  Win rate      : {total_wr:.1f}%")
    print(f"  Profit factor : {pf_str}")
    print(f"  Total R       : {total_r:+.2f}R")
    print()


# -- main ----------------------------------------------------------------------

def main() -> None:
    W = 100

    print("\n" + "=" * W)
    print("  backtest_bc_daily_report.py  --  B+C Strategy Simulation")
    print(f"  Engine : analyzer_bc_core  |  chart_dir : {CHART_DIR}")
    print(f"  Rules  : TP1 = +{WIN_R}R  |  SL = {LOSS_R}R  |  "
          f"BE = 0R  (max {MAX_HOLD} bars after entry)")
    print(f"  Select : Top-3 / day by rank_score")
    print("=" * W + "\n")

    # 1. load candles
    print("Loading chart data ...")
    c15_cache: Dict[str, List[Candle]] = {}
    c1h_cache: Dict[str, List[Candle]] = {}
    missing:   List[str]               = []

    for sym in SYMBOLS:
        data = load_symbol_candles(sym, CHART_DIR)
        if data is None:
            missing.append(sym)
            continue
        c15_cache[sym], c1h_cache[sym] = data

    if missing:
        print(f"  No data for: {missing}")

    loaded = [s for s in SYMBOLS if s in c15_cache]
    print(f"  Loaded  : {len(loaded)}/{len(SYMBOLS)} symbols")
    if not loaded:
        print("\nERROR: No chart_data files found. Run tv_datafeed first.")
        sys.exit(1)

    # 2. scan, enrich, select
    print("\nScanning & enriching signals ...")
    all_sigs: List[Dict[str, Any]] = []
    for sym in loaded:
        sigs = scan_symbol(sym, c15_cache[sym], c1h_cache.get(sym, []))
        _enrich(sigs, sym, c15_cache[sym])   # adds rvi, rvi_bucket, regime, grade
        all_sigs.extend(sigs)
        print(f"  [{sym:5}]  {len(sigs):>3} raw signals")

    all_sigs.sort(key=lambda s: (s["date"], s["birth_time"], s["symbol"]))
    selected = select_daily(all_sigs, top_n=3)

    print(f"\n  Raw signals    : {len(all_sigs)}")
    print(f"  After Top-3/day: {len(selected)}")

    # gate-filtered subset
    gated = [s for s in selected if passes_exec_gate(s)[0]]
    blocked = [s for s in selected if not passes_exec_gate(s)[0]]
    print(f"  Gate-filtered  : {len(gated)}  (blocked: {len(blocked)})")

    if blocked:
        print("\n  Blocked signals:")
        for s in blocked:
            _, reason = passes_exec_gate(s)
            print(f"    {s['date']} {s['symbol']:5} {s['direction']:5} "
                  f"score={s.get('rank_score',0):.1f}  grade={s.get('grade','?')}  "
                  f"-> {reason}")

    if not selected:
        print("\nNo signals to simulate.")
        sys.exit(0)

    # 3. simulate all selected
    print("\nSimulating outcomes ...")
    results_all: List[Dict[str, Any]] = []
    for sig in selected:
        sym = sig["symbol"]
        c15 = c15_cache.get(sym)
        if c15 is None:
            continue
        outcome, r = simulate(
            c15       = c15,
            entry_ts  = sig["entry_ts"],
            stop      = sig["stop_price"],
            tp1       = sig["tp1"],
            direction = sig["direction"],
        )
        results_all.append({**sig, "outcome": outcome, "R": r})

    # gate-filtered results (subset of results_all)
    gated_keys = {
        (s["symbol"], s["direction"], s["date"], s.get("birth_time", ""))
        for s in gated
    }
    results_gated = [
        r for r in results_all
        if (r["symbol"], r["direction"], r["date"], r.get("birth_time", "")) in gated_keys
    ]

    # 4. report
    _print_daily(results_all, "ALL Top-3/day  (baseline -- no execution gate)", W)
    _print_daily(
        results_gated,
        "GATE-FILTERED  (score>=75, RVI=High, Trend, ATR%<=0.52, !AAPL, before 14:15 ET)",
        W,
    )


if __name__ == "__main__":
    main()
