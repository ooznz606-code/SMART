# -*- coding: utf-8 -*-
"""
livecheck_bc_core.py — Compare analyzer_bc_atr_daily vs analyzer_bc_core
=========================================================================
Loads chart_data, runs both scanners, and reports:
  - old signal count
  - new signal count
  - matched (same symbol / direction / date / birth_time / entry_time)
  - missing (in old, not in new)
  - extra   (in new, not in old)
  - first 20 side by side

Run:  python livecheck_bc_core.py
"""
from __future__ import annotations
import os, sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")

if not os.path.isdir(CHART_DIR):
    print(f"[livecheck] chart_data not found: {CHART_DIR}")
    sys.exit(1)

# -- load OLD scanner (analyzer_bc_atr_daily) ---------------------------------
try:
    import analyzer_bc_atr_daily as _old
    print("[livecheck] loaded analyzer_bc_atr_daily  (old)")
except Exception as e:
    print(f"[livecheck] FAILED to load analyzer_bc_atr_daily: {e}")
    sys.exit(1)

# -- load NEW scanner (analyzer_bc_core) --------------------------------------
try:
    import analyzer_bc_core as _new
    print("[livecheck] loaded analyzer_bc_core        (new)")
except Exception as e:
    print(f"[livecheck] FAILED to load analyzer_bc_core: {e}")
    sys.exit(1)


# -- signal key: identity anchor ----------------------------------------------

def _sig_key(s: Dict) -> str:
    return (f"{s['symbol']}|{s['direction']}|{s['date']}|"
            f"{s['birth_time']}|{s.get('entry_time','?')}")


def _sig_label(s: Dict) -> str:
    return (f"{s['date']} {s['birth_time']}->{s.get('entry_time','?')}  "
            f"{s['symbol']:<6} {s['direction']:<5}  "
            f"entry={s.get('entry_price',0):.2f}  "
            f"stop={s.get('stop_price',0):.2f}  "
            f"tp1={s.get('tp1',0):.2f}  "
            f"rank={s.get('rank_score',0):.1f}")


# -- collect chart symbols ----------------------------------------------------

def _available_symbols(chart_dir: str) -> List[str]:
    seen = set()
    for fn in os.listdir(chart_dir):
        if fn.endswith("_15m.json"):
            seen.add(fn[:-9])
    return sorted(seen)


# -- run both scanners on the same data ---------------------------------------

def _run_scanner(scanner_mod, symbols, chart_dir) -> List[Dict]:
    all_sigs: List[Dict] = []
    for sym in symbols:
        data = scanner_mod.load_symbol_candles(sym, chart_dir)
        if data is None:
            continue
        c15, c1h = data
        try:
            sigs = scanner_mod.scan_symbol(sym, c15, c1h)
        except Exception as e:
            print(f"  [{sym}] scan error: {e}")
            continue
        all_sigs.extend(sigs)
    return all_sigs


# -- main comparison ----------------------------------------------------------

def main() -> None:
    symbols = _available_symbols(CHART_DIR)
    if not symbols:
        print(f"[livecheck] No *_15m.json files found in {CHART_DIR}")
        sys.exit(1)

    print(f"\n  Symbols found: {len(symbols)}: {', '.join(symbols)}")
    print(f"  chart_dir    : {CHART_DIR}")
    print(f"  Run at       : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    print("  [OLD] scanning with analyzer_bc_atr_daily ...")
    old_raw = _run_scanner(_old, symbols, CHART_DIR)
    old_sel = _old.select_daily(old_raw, _old.TOP_N_DAILY)
    print(f"        raw={len(old_raw)}  selected={len(old_sel)}")

    print("  [NEW] scanning with analyzer_bc_core ...")
    new_raw = _run_scanner(_new, symbols, CHART_DIR)
    new_sel = _new.select_daily(new_raw, _new.TOP_N_DAILY)
    print(f"        raw={len(new_raw)}  selected={len(new_sel)}\n")

    # -- compare RAW signals --------------------------------------------------
    old_keys = {_sig_key(s): s for s in old_raw}
    new_keys = {_sig_key(s): s for s in new_raw}
    matched  = [k for k in old_keys if k in new_keys]
    missing  = [k for k in old_keys if k not in new_keys]
    extra    = [k for k in new_keys if k not in old_keys]

    W = 96
    print("=" * W)
    print("  RAW SIGNAL COMPARISON  (before daily cap)")
    print("=" * W)
    print(f"  Old total  : {len(old_raw):>5}")
    print(f"  New total  : {len(new_raw):>5}")
    print(f"  Matched    : {len(matched):>5}  ({len(matched)/max(len(old_raw),1)*100:.1f}% of old)")
    print(f"  Missing    : {len(missing):>5}  (in old, NOT in new)")
    print(f"  Extra      : {len(extra):>5}  (in new, NOT in old)")

    # -- side-by-side first 20 matched ----------------------------------------
    print("\n" + "=" * W)
    print("  FIRST 20 MATCHED SIGNALS — SIDE BY SIDE")
    print("  [OLD]:                                             [NEW]:")
    print("  " + "-" * (W - 2))
    n_show = min(20, len(matched))
    for k in sorted(matched)[:n_show]:
        os_ = old_keys[k]
        ns  = new_keys[k]
        same_price = abs(os_.get("entry_price",0) - ns.get("entry_price",0)) < 0.0001
        diff_flag  = "" if same_price else "  *** PRICE DIFF ***"
        print(f"  OLD: {_sig_label(os_)}")
        print(f"  NEW: {_sig_label(ns)}{diff_flag}")
        print()

    # -- list missing ---------------------------------------------------------
    if missing:
        print("=" * W)
        print(f"  MISSING IN NEW ({len(missing)}) — signals found by OLD but not NEW:")
        print("  " + "-" * (W - 2))
        for k in sorted(missing)[:30]:
            print(f"  {_sig_label(old_keys[k])}")
        if len(missing) > 30:
            print(f"  ... and {len(missing)-30} more")

    # -- list extra -----------------------------------------------------------
    if extra:
        print("=" * W)
        print(f"  EXTRA IN NEW ({len(extra)}) — signals found by NEW but not OLD:")
        print("  " + "-" * (W - 2))
        for k in sorted(extra)[:30]:
            print(f"  {_sig_label(new_keys[k])}")
        if len(extra) > 30:
            print(f"  ... and {len(extra)-30} more")

    # -- selected comparison --------------------------------------------------
    old_sel_keys = {_sig_key(s): s for s in old_sel}
    new_sel_keys = {_sig_key(s): s for s in new_sel}
    sel_matched  = [k for k in old_sel_keys if k in new_sel_keys]
    sel_missing  = [k for k in old_sel_keys if k not in new_sel_keys]
    sel_extra    = [k for k in new_sel_keys if k not in old_sel_keys]
    print("\n" + "=" * W)
    print(f"  SELECTED SIGNALS (top-{_old.TOP_N_DAILY}/day) COMPARISON")
    print("=" * W)
    print(f"  Old selected : {len(old_sel):>5}")
    print(f"  New selected : {len(new_sel):>5}")
    print(f"  Matched      : {len(sel_matched):>5}  ({len(sel_matched)/max(len(old_sel),1)*100:.1f}% of old)")
    print(f"  Missing      : {len(sel_missing):>5}")
    print(f"  Extra        : {len(sel_extra):>5}")

    # -- verdict --------------------------------------------------------------
    print("\n" + "=" * W)
    if len(missing) == 0 and len(extra) == 0 and len(old_raw) == len(new_raw):
        print("  VERDICT: PERFECT MATCH — analyzer_bc_core is a drop-in replacement.")
    elif len(missing) == 0 and len(extra) == 0:
        print("  VERDICT: ALL KEYS MATCH — signal counts equal, no missing/extra.")
    else:
        pct = len(matched) / max(len(old_raw), 1) * 100
        print(f"  VERDICT: {pct:.1f}% match  "
              f"({len(missing)} missing, {len(extra)} extra) — review diffs above.")
    print("=" * W + "\n")


if __name__ == "__main__":
    main()
