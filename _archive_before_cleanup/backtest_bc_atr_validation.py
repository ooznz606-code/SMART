# -*- coding: utf-8 -*-
"""
backtest_bc_atr_validation.py  --  ATR Filter Robustness Validation
====================================================================
Approved 2026-06-20.

Tests whether atr_pct <= 0.52 is genuinely robust or a concentration
effect (like the META finding in the original validation study).

Approach:
  - B+C scan is IDENTICAL to backtest_bc_feature_study.py (which
    replicates backtest_bc_research.py exactly).
  - No thresholds changed.  atr_pct = ATR/entry_price*100, threshold
    is 0.52 exactly as found in the feature separation study.
  - Same 16 symbols as validation study.
  - Robustness windows: 30d, 55d, 90d, Max available.
    (yfinance 15m limit ~60 days; 90d/Max will show same data as 55d)

For each window, two views side-by-side:
  A) No ATR filter  (baseline)
  B) atr_pct <= 0.52

Each view reports:
  Overall: N, WR, PF, TotalR, LV%
  By symbol: N, WR, PF, TotalR
  Leave-one-out: remove each symbol, recompute aggregate

Goal: determine whether the ATR filter edge is:
  (a) Robust across symbols  -- genuine regime filter
  (b) Concentrated in one symbol  -- artifact

Do not optimize.  Do not test 0.51 or 0.53.  Validation only.
Do not modify analyzer_x2.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    from backtest_bc_feature_study import (
        _scan_with_features,
        _download,
        SYMBOLS as _BASE_SYMBOLS,
        W,
    )
except ImportError as e:
    print(f"Cannot import backtest_bc_feature_study: {e}")
    print("Ensure backtest_bc_feature_study.py is present and importable.")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

ATR_THRESHOLD = 0.52          # exact, do not change

VALIDATION_SYMBOLS: List[str] = [
    "AAPL", "AMD",  "TSLA", "AVGO", "COST", "LLY",  "PANW", "CRM",
    "QQQ",  "SPY",  "MSFT", "META", "AMZN", "GOOGL","NVDA", "NFLX",
]

WINDOWS: List[Tuple[str, int]] = [
    ("30 days",   30),
    ("55 days",   55),
    ("90 days",   90),
    ("Max avail", 9999),
]


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


# ── Window slicer ─────────────────────────────────────────────────────────────

def _slice_window(trades: List[Dict], days: int) -> List[Dict]:
    if days >= 9999:
        return trades
    cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [t for t in trades if t["date"] >= cutoff]


# ── Reporting helpers ──────────────────────────────────────────────────────────

def _print_overall_pair(label: str, no_filt: List[Dict], filt: List[Dict]) -> None:
    mn = _m(no_filt)
    mf = _m(filt)

    print(f"\n  {'-'*(W-2)}")
    print(f"  Window: {label}")
    print(f"  {'-'*(W-2)}")
    hdr = "  {:<28}  {:>5}  {:>6}  {:>5}  {:>8}  {:>8}"
    print(hdr.format("View", "N", "WR%", "PF", "TotalR", "LV%"))
    print(_hline())
    print(hdr.format(
        "A) No ATR filter (baseline)",
        mn["n"], f"{mn['wr']:.1f}%", f"{mn['pf']:.2f}",
        f"{mn['totalr']:+.1f}R", f"{mn['lv']:.1f}%",
    ))
    print(hdr.format(
        f"B) atr_pct <= {ATR_THRESHOLD}",
        mf["n"], f"{mf['wr']:.1f}%", f"{mf['pf']:.2f}",
        f"{mf['totalr']:+.1f}R", f"{mf['lv']:.1f}%",
    ))


def _print_by_symbol_pair(
    label: str,
    no_filt: List[Dict],
    filt:    List[Dict],
) -> None:
    print(f"\n  By symbol -- {label}:")
    hdr = "  {:<6}  {:>4}  {:>5}  {:>4}  {:>7}  |  {:>4}  {:>5}  {:>4}  {:>7}"
    print(hdr.format(
        "Sym",
        "N", "WR%", "PF", "TotalR",
        "N", "WR%", "PF", "TotalR",
    ))
    sub = " " * 8 + "No ATR filter" + " " * 20 + "atr_pct<=0.52"
    print(f"  {sub}")
    print(_hline())

    rows = []
    for sym in VALIDATION_SYMBOLS:
        nf = [t for t in no_filt if t["symbol"] == sym]
        ff = [t for t in filt    if t["symbol"] == sym]
        mn = _m(nf)
        mf = _m(ff)
        if mn["n"] == 0 and mf["n"] == 0:
            continue
        rows.append((sym, mn, mf))

    for sym, mn, mf in sorted(rows, key=lambda x: -(x[2]["totalr"])):
        def _fmt(m: Dict, col: str) -> str:
            if m["n"] == 0:
                return "-"
            return {"n": str(m["n"]),
                    "wr": f"{m['wr']:.0f}%",
                    "pf": f"{m['pf']:.2f}",
                    "totalr": f"{m['totalr']:+.1f}R"}[col]
        print(hdr.format(
            sym,
            _fmt(mn,"n"), _fmt(mn,"wr"), _fmt(mn,"pf"), _fmt(mn,"totalr"),
            _fmt(mf,"n"), _fmt(mf,"wr"), _fmt(mf,"pf"), _fmt(mf,"totalr"),
        ))


def _print_leave_one_out(
    label:   str,
    trades:  List[Dict],
    view:    str,    # "No filter" or "atr_pct<=0.52"
) -> None:
    full_m = _m(trades)
    print(f"\n  Leave-one-out -- {label} -- {view}:")
    print(f"  Full pool: N={full_m['n']}  WR={full_m['wr']:.1f}%  "
          f"PF={full_m['pf']:.2f}  TotalR={full_m['totalr']:+.1f}R")
    print(f"  dPF = PF_without - PF_full.  Neg = symbol was helping.")

    hdr = "  {:<6}  {:>5}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}"
    print(hdr.format("Removed", "N", "WR%", "PF", "TotalR", "dWR", "dPF"))
    print(_hline())

    rows = []
    for sym in VALIDATION_SYMBOLS:
        sub = [t for t in trades if t["symbol"] != sym]
        sm  = _m(sub)
        rows.append((sym, sm))

    rows.sort(key=lambda x: x[1]["pf"] - full_m["pf"])
    for sym, sm in rows:
        d_pf = sm["pf"] - full_m["pf"]
        d_wr = sm["wr"] - full_m["wr"]
        flag = ""
        if d_pf <= -0.40:
            flag = "  <-- carrying result"
        elif d_pf >= +0.40:
            flag = "  <-- dragging result"
        print(hdr.format(
            sym, sm["n"],
            f"{sm['wr']:.1f}%", f"{sm['pf']:.2f}",
            f"{sm['totalr']:+.1f}R",
            f"{d_wr:+.1f}pp", f"{d_pf:+.2f}",
        ) + flag)

    # Concentration check
    top_r = max(
        (_m([t for t in trades if t["symbol"] == s])["totalr"]
         for s in VALIDATION_SYMBOLS), default=0
    )
    print()
    if full_m["totalr"] > 0 and top_r > 0 and top_r / full_m["totalr"] > 0.60:
        best_sym = max(
            VALIDATION_SYMBOLS,
            key=lambda s: _m([t for t in trades if t["symbol"] == s])["totalr"]
        )
        print(f"  CONCENTRATION: {best_sym} contributes "
              f"{top_r/full_m['totalr']*100:.0f}% of TotalR  -- concentrated")
    else:
        print(f"  No single symbol contributes > 60% of TotalR  -- distributed")


# ── Summary table across windows ──────────────────────────────────────────────

def _print_summary_table(
    all_trades: List[Dict],
) -> None:
    print("\n" + "=" * W)
    print("  ROBUSTNESS SUMMARY -- No filter vs atr_pct<=0.52 across windows")
    print("=" * W)

    hdr = "  {:<12}  {:>4}  {:>5}  {:>4}  {:>7}  ||  {:>4}  {:>5}  {:>4}  {:>7}"
    print(hdr.format(
        "Window",
        "N", "WR%", "PF", "TotalR",
        "N", "WR%", "PF", "TotalR",
    ))
    sub = " " * 14 + "No filter" + " " * 22 + "atr_pct<=0.52"
    print(f"  {sub}")
    print(_hline())

    for label, days in WINDOWS:
        wt  = _slice_window(all_trades, days)
        ft  = [t for t in wt if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD]
        mn  = _m(wt)
        mf  = _m(ft)

        print(hdr.format(
            label,
            mn["n"], f"{mn['wr']:.1f}%", f"{mn['pf']:.2f}", f"{mn['totalr']:+.1f}R",
            mf["n"], f"{mf['wr']:.1f}%", f"{mf['pf']:.2f}", f"{mf['totalr']:+.1f}R",
        ))

    print("=" * W)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  B+C ATR FILTER VALIDATION STUDY")
    print(f"  Symbols   : {len(VALIDATION_SYMBOLS)}")
    print(f"  Filter    : atr_pct <= {ATR_THRESHOLD}  (exact, not optimized)")
    print(f"  Scanner   : backtest_bc_feature_study._scan_with_features (unchanged)")
    print(f"  Run       : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W + "\n")

    all_trades: List[Dict] = []

    for sym in VALIDATION_SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download(sym)
        if dl is None:
            print("SKIP"); continue
        c15_all, c1h_all = dl

        trades = _scan_with_features(sym, c15_all, c1h_all)
        all_trades.extend(trades)

        wins  = sum(1 for t in trades if t["result"] == "WIN")
        loss  = sum(1 for t in trades if t["result"] == "LOSS")
        filt  = [t for t in trades if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD]
        print(f"OK  {len(trades):>3} BC  "
              f"WR={wins/max(wins+loss,1)*100:.0f}%  "
              f"ATR-filtered={len(filt)}/{len(trades)}")

    if not all_trades:
        print("\nNo B+C trades."); return

    n_filt = sum(1 for t in all_trades if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD)
    print(f"\n  Total B+C: {len(all_trades)}  |  atr_pct<={ATR_THRESHOLD}: {n_filt} "
          f"({n_filt/max(len(all_trades),1)*100:.0f}%)\n")

    # ── Summary table first (overview) ────────────────────────────────────────
    _print_summary_table(all_trades)

    # ── Per-window detailed breakdown ─────────────────────────────────────────
    for label, days in WINDOWS:
        wt     = _slice_window(all_trades, days)
        filt_t = [t for t in wt if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD]

        if not wt:
            continue

        print("\n" + "=" * W)
        print(f"  WINDOW: {label}  (N={len(wt)} unfiltered  |  N={len(filt_t)} filtered)")
        print("=" * W)

        _print_overall_pair(label, wt, filt_t)
        _print_by_symbol_pair(label, wt, filt_t)

        # Leave-one-out for BOTH views -- most important for the robustness question
        print()
        _print_leave_one_out(label, wt,     "No filter (baseline)")
        _print_leave_one_out(label, filt_t, f"atr_pct<={ATR_THRESHOLD}")

    # ── Final verdict ─────────────────────────────────────────────────────────
    all_filt = [t for t in all_trades
                if float(t.get("atr_pct", 999)) <= ATR_THRESHOLD]
    ma_full  = _m(all_trades)
    ma_filt  = _m(all_filt)

    print("\n" + "=" * W)
    print("  VERDICT SUMMARY")
    print("=" * W)
    print(f"  Full B+C pool  : N={ma_full['n']:>3}  WR={ma_full['wr']:.1f}%  "
          f"PF={ma_full['pf']:.2f}  TotalR={ma_full['totalr']:+.1f}R  "
          f"LV={ma_full['lv']:.1f}%")
    print(f"  ATR filtered   : N={ma_filt['n']:>3}  WR={ma_filt['wr']:.1f}%  "
          f"PF={ma_filt['pf']:.2f}  TotalR={ma_filt['totalr']:+.1f}R  "
          f"LV={ma_filt['lv']:.1f}%")

    # Determine concentration in filtered pool
    sym_rs = {s: _m([t for t in all_filt if t["symbol"] == s])["totalr"]
              for s in VALIDATION_SYMBOLS}
    top_sym = max(sym_rs, key=sym_rs.get)
    top_r   = sym_rs[top_sym]
    total_r = ma_filt["totalr"]

    print()
    if total_r > 0 and top_r > 0:
        pct = top_r / total_r * 100
        if pct > 60:
            print(f"  CONCENTRATION: {top_sym} = {pct:.0f}% of filtered TotalR  "
                  f"--> concentrated, similar to META effect")
        elif pct > 40:
            print(f"  MODERATE CONCENTRATION: {top_sym} = {pct:.0f}% of filtered TotalR")
        else:
            print(f"  DISTRIBUTED: top symbol ({top_sym}) = {pct:.0f}% of filtered TotalR"
                  f"  --> genuine regime filter")
    else:
        print(f"  Filtered TotalR <= 0 -- filter not helpful on this data")

    # Count symbols with positive PF after filter
    pos_pf_syms  = [s for s in VALIDATION_SYMBOLS
                    if _m([t for t in all_filt if t["symbol"] == s])["pf"] >= 1.0
                    and _m([t for t in all_filt if t["symbol"] == s])["n"] > 0]
    neg_pf_syms  = [s for s in VALIDATION_SYMBOLS
                    if _m([t for t in all_filt if t["symbol"] == s])["pf"] < 1.0
                    and _m([t for t in all_filt if t["symbol"] == s])["n"] > 0]

    print(f"  Symbols PF>=1.0 after filter: {len(pos_pf_syms)}"
          f"  ({', '.join(pos_pf_syms) or 'none'})")
    print(f"  Symbols PF<1.0  after filter: {len(neg_pf_syms)}"
          f"  ({', '.join(neg_pf_syms) or 'none'})")
    print("=" * W)


if __name__ == "__main__":
    main()
