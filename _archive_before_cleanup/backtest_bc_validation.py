# -*- coding: utf-8 -*-
"""
backtest_bc_validation.py  --  B+C Signal Validation Study
===========================================================
Approved 2026-06-20.

Imports _scan_symbol and _download DIRECTLY from backtest_bc_research.py
so no threshold, constant, or logic can differ.  This is an import, not
a copy -- any change to the source file immediately affects this study.

Expands symbols from 8 to 16:
  AAPL AMD TSLA AVGO COST LLY PANW CRM   (new)
  QQQ SPY MSFT META AMZN GOOGL NVDA NFLX (original)

Reports:
  1. Overall WR, PF, TotalR, LV%
  2. By symbol: N, WR, PF, TotalR
  3. Leave-one-out: remove each symbol and recompute PF, WR, TotalR
     to detect whether a single symbol drives the aggregate result.

No optimization.  No tuning.  No threshold changes.
Do not modify analyzer_x2.
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ── Import scanner unchanged from research file ───────────────────────────────
try:
    from backtest_bc_research import (
        _scan_symbol,   # core B+C scanner -- unchanged
        _download,      # download function -- unchanged
        _m,             # metrics -- unchanged
        DOWNLOAD_DAYS,  # 55 -- unchanged
        W,              # line width
    )
except ImportError as e:
    print(f"Cannot import backtest_bc_research: {e}")
    print("Run backtest_bc_research.py first to verify it is present.")
    sys.exit(1)

try:
    from backtest_entry_timing import _safe_avg
except ImportError as e:
    print(f"Cannot import backtest_entry_timing: {e}"); sys.exit(1)


# ── Validation symbol universe (16 symbols) ───────────────────────────────────
VALIDATION_SYMBOLS: List[str] = [
    # New symbols added for validation
    "AAPL", "AMD", "TSLA", "AVGO", "COST", "LLY", "PANW", "CRM",
    # Original 8 symbols from research file
    "QQQ", "SPY", "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hline(c: str = "-", w: int = W) -> str:
    return "  " + c * (w - 2)


# ── Section 1: Overall ────────────────────────────────────────────────────────

def _print_overall(trades: List[Dict]) -> None:
    m = _m(trades)
    if not trades:
        print("  No trades."); return

    dates = sorted({t["date"] for t in trades})
    span  = f"{dates[0]}  to  {dates[-1]}"

    print("\n" + "=" * W)
    print(f"  B+C VALIDATION STUDY  --  {len(VALIDATION_SYMBOLS)} SYMBOLS")
    print(f"  Period  : {span}  ({len(dates)} days with signals)")
    print(f"  Scanner : imported unchanged from backtest_bc_research.py")
    print("=" * W)

    hdr = "  {:>5}  {:>6}  {:>5}  {:>8}  {:>8}  {:>8}  {:>6}"
    print(hdr.format("N", "WR%", "PF", "TotalR", "AvgMFE", "AvgMAE", "LV%"))
    print(_hline())
    print(hdr.format(
        m["n"],
        f"{m['wr']:.1f}%",
        f"{m['pf']:.2f}",
        f"{m['totalr']:+.1f}R",
        f"{m['mfe']:+.2f}R",
        f"{m['mae']:+.2f}R",
        f"{m['lv']:.1f}%",
    ))
    print(_hline("="))
    print(f"  Ref X2 baseline : N=207  WR=39.5%  PF=1.18  TotalR=+19.0R  LV=75.8%")
    print(f"  B+C research    : N= 18  WR=53.3%  PF=2.06  TotalR= +7.4R  LV=16.7%"
          f"  (8 symbols, same window)")
    print("=" * W)


# ── Section 2: By symbol ──────────────────────────────────────────────────────

def _print_by_symbol(trades: List[Dict]) -> None:
    full_m = _m(trades)

    print("\n" + "=" * W)
    print("  2. BY SYMBOL  (sorted by TotalR descending)")
    print("=" * W)
    hdr = "  {:<7}  {:>5}  {:>6}  {:>5}  {:>8}  {}"
    print(hdr.format("Symbol", "N", "WR%", "PF", "TotalR", "Note"))
    print(_hline())

    sym_metrics = []
    for sym in VALIDATION_SYMBOLS:
        st = [t for t in trades if t["symbol"] == sym]
        sm = _m(st)
        sym_metrics.append((sym, sm))

    for sym, sm in sorted(sym_metrics, key=lambda x: -x[1]["totalr"]):
        note = ""
        if sm["n"] == 0:
            note = "no signals"
        elif sm["n"] < 5:
            note = "*** low N"
        elif sm["pf"] >= 2.0:
            note = "high PF"
        elif sm["pf"] == 0:
            note = "all losses"
        print(hdr.format(
            sym, sm["n"],
            f"{sm['wr']:.1f}%" if sm["n"] else "-",
            f"{sm['pf']:.2f}"  if sm["n"] else "-",
            f"{sm['totalr']:+.1f}R" if sm["n"] else "-",
            note,
        ))

    print(_hline())
    print(hdr.format(
        "TOTAL", full_m["n"],
        f"{full_m['wr']:.1f}%", f"{full_m['pf']:.2f}",
        f"{full_m['totalr']:+.1f}R", "",
    ))

    # Positive PF count
    pos_pf = sum(1 for _, sm in sym_metrics if sm["n"] > 0 and sm["pf"] >= 1.0)
    active  = sum(1 for _, sm in sym_metrics if sm["n"] > 0)
    print(f"\n  Symbols with signals : {active}/{len(VALIDATION_SYMBOLS)}")
    print(f"  Symbols PF >= 1.0   : {pos_pf}/{active}")
    print(f"  Symbols PF < 1.0    : {active - pos_pf}/{active}")
    print("=" * W)


# ── Section 3: Leave-one-out ──────────────────────────────────────────────────

def _print_leave_one_out(trades: List[Dict]) -> None:
    full_m   = _m(trades)
    full_pf  = full_m["pf"]
    full_wr  = full_m["wr"]
    full_r   = full_m["totalr"]
    full_n   = full_m["n"]

    print("\n" + "=" * W)
    print("  3. LEAVE-ONE-OUT  --  Remove each symbol, recompute aggregate")
    print(f"     Full pool baseline : N={full_n}  WR={full_wr:.1f}%"
          f"  PF={full_pf:.2f}  TotalR={full_r:+.1f}R")
    print(f"     Read: dPF = PF_without - PF_full."
          f"  Negative dPF = symbol was HELPING.")
    print("=" * W)

    hdr = "  {:<7}  {:>5}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}  {:>7}"
    print(hdr.format("Removed", "N", "WR%", "PF", "TotalR",
                      "dWR", "dPF", "dTotalR"))
    print(_hline())

    results = []
    for sym in VALIDATION_SYMBOLS:
        subset = [t for t in trades if t["symbol"] != sym]
        sm     = _m(subset)
        sym_trades = [t for t in trades if t["symbol"] == sym]
        results.append((sym, sm, len(sym_trades)))

    # Sort by dPF ascending (biggest helpers at bottom -- most impactful removals first)
    results.sort(key=lambda x: x[1]["pf"] - full_pf)

    for sym, sm, sym_n in results:
        d_pf = sm["pf"] - full_pf
        d_wr = sm["wr"] - full_wr
        d_r  = sm["totalr"] - full_r
        flag = ""
        if d_pf <= -0.30:
            flag = "  <-- was carrying result"
        elif d_pf >= +0.30:
            flag = "  <-- was dragging result"
        print(hdr.format(
            sym, sm["n"],
            f"{sm['wr']:.1f}%", f"{sm['pf']:.2f}",
            f"{sm['totalr']:+.1f}R",
            f"{d_wr:+.1f}pp", f"{d_pf:+.2f}", f"{d_r:+.1f}R",
        ) + flag)

    print(_hline())

    # Summary
    helpers  = [(sym, sm, n) for sym, sm, n in results
                if (sm["pf"] - full_pf) <= -0.20 and n > 0]
    draggers = [(sym, sm, n) for sym, sm, n in results
                if (sm["pf"] - full_pf) >= +0.20 and n > 0]

    print(f"\n  Symbols where removal drops  PF by >= 0.20 (was helping): "
          f"{', '.join(s for s,_,_ in helpers) or 'none'}")
    print(f"  Symbols where removal raises PF by >= 0.20 (was dragging): "
          f"{', '.join(s for s,_,_ in draggers) or 'none'}")

    # Is the result concentrated?
    top_sym_r = max(
        (_m([t for t in trades if t["symbol"] == sym])["totalr"]
         for sym in VALIDATION_SYMBOLS), default=0
    )
    if full_r > 0 and top_sym_r / full_r > 0.60:
        print(f"\n  CONCENTRATION WARNING: single best symbol contributes"
              f" {top_sym_r/full_r*100:.0f}% of total TotalR.")
    elif full_r > 0:
        print(f"\n  No single symbol contributes > 60% of TotalR.")

    print("=" * W)


# ── Bucket summary ────────────────────────────────────────────────────────────

def _print_bucket_summary(trades: List[Dict]) -> None:
    print("\n" + "=" * W)
    print("  BUCKET SUMMARY  (pct_done diagnostic)")
    print("=" * W)
    hdr = "  {:<12}  {:>5}  {:>6}  {:>5}  {:>8}"
    print(hdr.format("Bucket", "N", "WR%", "PF", "TotalR"))
    print(_hline())
    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        grp = [t for t in trades if t.get("bucket") == bk]
        m   = _m(grp)
        if m["n"] == 0:
            continue
        print(hdr.format(bk, m["n"], f"{m['wr']:.1f}%",
                          f"{m['pf']:.2f}", f"{m['totalr']:+.1f}R"))
    print(_hline())
    mt = _m(trades)
    print(hdr.format("TOTAL", mt["n"], f"{mt['wr']:.1f}%",
                      f"{mt['pf']:.2f}", f"{mt['totalr']:+.1f}R"))
    print("=" * W)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  B+C VALIDATION STUDY")
    print(f"  Symbols  : {len(VALIDATION_SYMBOLS)} total")
    print(f"  New      : AAPL AMD TSLA AVGO COST LLY PANW CRM")
    print(f"  Original : QQQ SPY MSFT META AMZN GOOGL NVDA NFLX")
    print(f"  Scanner  : backtest_bc_research._scan_symbol (unchanged)")
    print(f"  Run      : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W + "\n")

    all_trades: List[Dict] = []

    for sym in VALIDATION_SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download(sym)
        if dl is None:
            print("SKIP"); continue
        c15_all, c1h_all = dl

        trades = _scan_symbol(sym, c15_all, c1h_all)
        all_trades.extend(trades)

        wins = sum(1 for t in trades if t["result"] == "WIN")
        loss = sum(1 for t in trades if t["result"] == "LOSS")
        print(f"OK  {len(trades):>3} BC  "
              f"WR={wins/max(wins+loss,1)*100:.0f}%")

    if not all_trades:
        print("\nNo B+C trades found."); return

    print(f"\n  Total B+C trades: {len(all_trades)}"
          f"  across {len({t['symbol'] for t in all_trades})} symbols\n")

    _print_overall(all_trades)
    _print_by_symbol(all_trades)
    _print_bucket_summary(all_trades)
    _print_leave_one_out(all_trades)

    # Final one-liner
    m = _m(all_trades)
    print(f"\n  {'-' * (W-2)}")
    print(f"  VALIDATION RESULT:  N={m['n']}  WR={m['wr']:.1f}%"
          f"  PF={m['pf']:.2f}  TotalR={m['totalr']:+.1f}R  LV={m['lv']:.1f}%")
    print(f"  {'-' * (W-2)}")


if __name__ == "__main__":
    main()
