# -*- coding: utf-8 -*-
"""
backtest_multivariable_study.py  -  Multivariable Feature Combination Study
============================================================================
Reads backtest_feature_study.csv (output of backtest_feature_study.py).
Tests 6 feature combinations and compares them to single-feature and baseline.

Thresholds are fixed from single-feature bucket analysis -- NOT optimized on outcome:
  HTF_THRESHOLD   = 0.30   htf_strength > 0.30  (first clearly positive bucket)
  SESSION_CUTOFF  = 525    session_min < 525     (before the 24% WR last-hour bucket)
  BIRTH_MAX_AGE   = 2      birth_age <= 2        (includes the fresh birth_age=1 group)
  EMA50_THRESHOLD = -2.0   dist_ema50 < -2.0     (price well extended beyond EMA50)

Research only.  No strategy changes.  No new analyzer.
"""
from __future__ import annotations

import csv
import os
from typing import Callable, Dict, List, Tuple

CSV_PATH = "backtest_feature_study.csv"

# -- Thresholds (fixed from bucket analysis, not optimized) -------------------
HTF_THRESHOLD   =  0.30
SESSION_CUTOFF  = 525.0
BIRTH_MAX_AGE   =  2
EMA50_THRESHOLD = -2.0


# -- Load data -----------------------------------------------------------------

def _load(path: str) -> List[Dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Run backtest_feature_study.py first."
        )
    rows = []
    numeric = {
        "win_binary", "r", "pct_done",
        "htf_strength", "session_min", "birth_age", "dist_ema50",
        "dist_ema20", "dist_vwap", "zone_fresh", "bars_since",
        "bars_after_birth", "b_qual", "disp_qual", "adx",
        "adx_slope", "atr_expansion", "vol_ratio", "confluence",
    }
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            for k in numeric:
                if k in row:
                    try:
                        row[k] = float(row[k])
                    except (ValueError, TypeError):
                        row[k] = 0.0
            rows.append(row)
    return rows


# -- Filter definitions (binary pass/fail) ------------------------------------

def htf_ok(t: Dict)    -> bool: return float(t["htf_strength"]) >  HTF_THRESHOLD
def session_ok(t: Dict)-> bool: return float(t["session_min"])  <  SESSION_CUTOFF
def birth_ok(t: Dict)  -> bool: return float(t["birth_age"])    <= BIRTH_MAX_AGE
def ema50_ok(t: Dict)  -> bool: return float(t["dist_ema50"])   <  EMA50_THRESHOLD


# -- Metrics helper ------------------------------------------------------------

def _m(trades: List[Dict]) -> Dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0, "lv": 0.0}
    wins = sum(1 for t in trades if t["result"] == "WIN")
    loss = sum(1 for t in trades if t["result"] == "LOSS")
    gw   = sum(float(t["r"]) for t in trades if float(t["r"]) > 0)
    gl   = abs(sum(float(t["r"]) for t in trades if float(t["r"]) < 0))
    dec  = wins + loss
    late = sum(1 for t in trades if t.get("bucket") in ("Late", "VeryLate"))
    return {
        "n":      n,
        "wr":     wins / dec * 100 if dec else 0.0,
        "pf":     gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr": sum(float(t["r"]) for t in trades),
        "lv":     late / n * 100,
    }


def _row(label: str, m: Dict, base_n: int) -> str:
    pct = m["n"] / max(base_n, 1) * 100
    return (f"  {label:<42}  {m['n']:>4} ({pct:>3.0f}%)  "
            f"{m['wr']:>6.1f}%  {m['pf']:>5.2f}  "
            f"{m['totalr']:>8.2f}R  {m['lv']:>6.1f}%")


# -- Reporting -----------------------------------------------------------------

def _print_section(
    title:   str,
    filters: List[Callable],
    rows:    List[Dict],
    base_n:  int,
    show_rejected: bool = True,
) -> None:
    approved = [t for t in rows if all(f(t) for f in filters)]
    rejected = [t for t in rows if not all(f(t) for f in filters)]
    ma = _m(approved)
    mr = _m(rejected)

    print(f"\n  {title}")
    print(f"  {'-' * 88}")
    print(_row("Approved (all filters pass)", ma, base_n))
    if show_rejected:
        print(_row("Rejected (>=1 filter fails)", mr, base_n))


def _print_threshold_note() -> None:
    print("""
  Thresholds (fixed from bucket analysis -- NOT optimized):
    HTF_THRESHOLD   =  0.30   htf_strength > 0.30
    SESSION_CUTOFF  = 525     session_min  < 525   (before ~14:15 ET)
    BIRTH_MAX_AGE   =  2      birth_age    <= 2
    EMA50_THRESHOLD = -2.0    dist_ema50   < -2.0  (price extended beyond EMA50)
""")


# -- Main ----------------------------------------------------------------------

def main() -> None:
    rows = _load(CSV_PATH)
    n    = len(rows)
    bm   = _m(rows)

    print("\n" + "=" * 92)
    print(f"  MULTIVARIABLE FEATURE STUDY  --  {n} X2 trades")
    print(f"  Source : {CSV_PATH}")
    print("=" * 92)

    _print_threshold_note()

    hdr = (f"  {'Filter / Subset':<42}  {'N':>9}  {'WR%':>6}  {'PF':>5}  "
           f"{'TotalR':>8}  {'LV%':>6}")
    sep = "  " + "=" * 88

    # -- Baseline -------------------------------------------------------------
    print(sep)
    print(f"  BASELINE  (all {n} trades)")
    print(f"  {'-' * 88}")
    print(_row("All X2 trades", bm, n))

    # -- Single features -------------------------------------------------------
    print(sep)
    print("  SINGLE FEATURES  (for reference)")
    print(hdr)
    for label, flt in [
        (f"HTF > {HTF_THRESHOLD}",      [htf_ok]),
        (f"Session < {SESSION_CUTOFF:.0f} min", [session_ok]),
        (f"Birth age <= {BIRTH_MAX_AGE}", [birth_ok]),
        (f"EMA50 dist < {EMA50_THRESHOLD}", [ema50_ok]),
    ]:
        app = [t for t in rows if all(f(t) for f in flt)]
        rej = [t for t in rows if not all(f(t) for f in flt)]
        print(_row(f"  Approved: {label}", _m(app), n))
        print(_row(f"  Rejected: {label}", _m(rej), n))
        print()

    # -- Combinations ---------------------------------------------------------
    combos: List[Tuple[str, List[Callable]]] = [
        ("1. HTF + Session",                        [htf_ok, session_ok]),
        ("2. HTF + Birth freshness",                [htf_ok, birth_ok]),
        ("3. HTF + EMA50 distance",                 [htf_ok, ema50_ok]),
        ("4. HTF + Birth freshness + Session",      [htf_ok, birth_ok, session_ok]),
        ("5. HTF + Birth freshness + EMA50",        [htf_ok, birth_ok, ema50_ok]),
        ("6. All four  (HTF+Birth+Session+EMA50)",  [htf_ok, birth_ok, session_ok, ema50_ok]),
    ]

    print(sep)
    print("  COMBINATIONS  (Approved = all listed filters pass)")
    print(hdr)

    best_pf_label = ""; best_pf = 0.0
    best_wr_label = ""; best_wr = 0.0
    summary_rows = []

    for label, filters in combos:
        approved = [t for t in rows if all(f(t) for f in filters)]
        rejected = [t for t in rows if not all(f(t) for f in filters)]
        ma = _m(approved); mr = _m(rejected)
        pct = ma["n"] / max(n, 1) * 100

        print()
        print(_row(f"  Approved: {label}", ma, n))
        print(_row(f"  Rejected: {label}", mr, n))

        summary_rows.append((label, ma, pct))

        if ma["pf"] > best_pf and ma["n"] >= 10:
            best_pf = ma["pf"]; best_pf_label = label
        if ma["wr"] > best_wr and ma["n"] >= 10:
            best_wr = ma["wr"]; best_wr_label = label

    # -- Summary table ---------------------------------------------------------
    print()
    print(sep)
    print("  SUMMARY  --  Approved group only, sorted by PF")
    print(hdr)
    print()
    # Baseline first
    print(_row("  Baseline (all trades)", bm, n))
    # Single features (approved only)
    for label, flt in [
        (f"HTF > {HTF_THRESHOLD}",        [htf_ok]),
        (f"Session < {SESSION_CUTOFF:.0f}m",[session_ok]),
        (f"Birth age <= {BIRTH_MAX_AGE}",  [birth_ok]),
        (f"EMA50 < {EMA50_THRESHOLD}",     [ema50_ok]),
    ]:
        app = [t for t in rows if all(f(t) for f in flt)]
        print(_row(f"  Single: {label}", _m(app), n))
    print()
    for label, ma, pct in sorted(summary_rows, key=lambda x: -x[1]["pf"]):
        print(_row(f"  Combo: {label}", ma, n))
    print(sep)

    # -- Verdict ---------------------------------------------------------------
    print(f"\n  VERDICT")
    print(f"  {'-' * 88}")
    print(f"  Baseline       : N={bm['n']}  WR={bm['wr']:.1f}%  PF={bm['pf']:.2f}"
          f"  TotalR={bm['totalr']:+.2f}R  LV={bm['lv']:.1f}%")

    best_combo_name, best_combo_m, best_pct = max(
        summary_rows, key=lambda x: x[1]["pf"] if x[1]["n"] >= 10 else 0
    )
    print(f"  Best by PF     : {best_combo_name}")
    print(f"    N={best_combo_m['n']} ({best_pct:.0f}% of baseline)  "
          f"WR={best_combo_m['wr']:.1f}%  PF={best_combo_m['pf']:.2f}"
          f"  TotalR={best_combo_m['totalr']:+.2f}R  LV={best_combo_m['lv']:.1f}%")

    best_combo_wr, best_combo_m_wr, pct_wr = max(
        summary_rows, key=lambda x: x[1]["wr"] if x[1]["n"] >= 10 else 0
    )
    print(f"  Best by WR     : {best_combo_wr}")
    print(f"    N={best_combo_m_wr['n']} ({pct_wr:.0f}% of baseline)  "
          f"WR={best_combo_m_wr['wr']:.1f}%  PF={best_combo_m_wr['pf']:.2f}"
          f"  TotalR={best_combo_m_wr['totalr']:+.2f}R  LV={best_combo_m_wr['lv']:.1f}%")

    # Did any combination improve on the best single feature?
    best_single_pf = max(
        _m([t for t in rows if f(t)])["pf"]
        for f in [htf_ok, session_ok, birth_ok, ema50_ok]
    )
    print(f"\n  Best single-feature PF : {best_single_pf:.2f}")
    print(f"  Best combination PF    : {best_combo_m['pf']:.2f}")
    pf_lift = best_combo_m["pf"] - best_single_pf
    if pf_lift >= 0.10:
        print(f"  Combination adds value : YES  (+{pf_lift:.2f} PF lift over best single)")
    elif pf_lift >= 0.0:
        print(f"  Combination adds value : MARGINAL  (+{pf_lift:.2f} PF lift)")
    else:
        print(f"  Combination adds value : NO  ({pf_lift:.2f} PF lift, no improvement)")
    print(f"  {'-' * 88}")

    # -- Per-symbol breakdown for best combo -----------------------------------
    best_approved = [t for t in rows if all(f(t) for f in dict(combos)[best_combo_name])]
    if best_approved:
        print(f"\n  PER-SYMBOL  --  best combo: {best_combo_name}")
        syms = sorted({t["symbol"] for t in rows})
        hdr2 = "  {:<8}  {:>7}  {:>6}  {:>5}  {:>8}  {:>6}"
        print(hdr2.format("Symbol", "N (sel)", "WR%", "PF", "TotalR", "LV%"))
        print("  " + "-" * 54)
        for sym in syms:
            st  = [t for t in best_approved if t["symbol"] == sym]
            m   = _m(st)
            print(hdr2.format(
                sym, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                f"{m['totalr']:+.2f}R", f"{m['lv']:.1f}%",
            ))
        all_m = _m(best_approved)
        print("  " + "-" * 54)
        print(hdr2.format("TOTAL", all_m["n"], f"{all_m['wr']:.1f}%",
                           f"{all_m['pf']:.2f}", f"{all_m['totalr']:+.2f}R",
                           f"{all_m['lv']:.1f}%"))
        print("  " + "=" * 54)


if __name__ == "__main__":
    main()
