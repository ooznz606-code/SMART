# -*- coding: utf-8 -*-
"""
backtest_x3_overlay.py — X3 as an overlay filter on current X2 signals
=======================================================================
For every X2 signal, re-evaluate the same bar against X3 gate conditions.
Tag each X2 trade as approved or rejected by the overlay.

Three-way comparison:
  1. X2 all         — baseline (current system, unfiltered)
  2. X2 + X3        — X2 trades where X3 conditions held at entry time
  3. X2 - X3        — X2 trades that X3 would have skipped

X3 overlay gates (all real-time, applied at the X2 entry bar):
  G1  birth_age        <= 10   bars from swing extreme
  G2  bars_after_birth <= 5    bars from birth to entry
  G3  birth_quality    >= 0.85 (birth candle body / ATR)
  G4  disp_quality     >= 0.55 (body x dir_close x acceleration)
  G5  zone_freshness   >= 0.80 (15m zone anchored from swing)

No changes to analyzer_x2.py.  Diagnostics only.
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
    print("pip install yfinance"); sys.exit(1)

try:
    from backtest_runner_x2 import (
        _flat, _to_candles, _market_state_from,
        _detect_zone, _zone_bounds,
        MIN_HISTORY,
    )
except Exception as e:
    print(f"Cannot import backtest_runner_x2: {e}"); sys.exit(1)

try:
    from analyzer_x2 import Candle
except Exception as e:
    print(f"Cannot import analyzer_x2: {e}"); sys.exit(1)

try:
    from backtest_entry_timing import (
        _safe_avg, _bucket,
        SYMBOLS, DAYS, SWING_LOOKBACK,
        _run_symbol as _run_current_raw,
    )
except Exception as e:
    print(f"Cannot import backtest_entry_timing: {e}"); sys.exit(1)

try:
    from backtest_move_birth import _birth_expand, _birth_volume, _birth_structure, _cap
except Exception as e:
    print(f"Cannot import backtest_move_birth: {e}"); sys.exit(1)

try:
    from backtest_x3_birth_ict import _disp_quality
    # X3 gate thresholds — same values used in the X3 backtest
    MAX_BIRTH_AGE  = 10
    BIRTH_RECENCY  = 5
    MIN_BIRTH_QUAL = 0.85
    MIN_DISP_QUAL  = 0.55
    MIN_ZONE_FRESH = 0.80
except Exception as e:
    print(f"Cannot import backtest_x3_birth_ict: {e}"); sys.exit(1)


# ── Data download ─────────────────────────────────────────────────────────────

def _download(symbol: str) -> Optional[Tuple[List[Candle], List[Candle]]]:
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
    c15 = _to_candles(df15)
    c1h = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []
    return c15, c1h


# ── X3 overlay gate check ─────────────────────────────────────────────────────

def _x3_check(
    c15_all:   List[Candle],
    bar_idx:   int,
    direction: str,
    bars_since: int,
) -> Tuple[bool, str]:
    """
    Apply X3 gate conditions at a specific bar (from an existing X2 trade).

    bars_since  : how many 15m bars since the swing extreme — taken directly
                  from the X2 trade dict so we use the same swing reference.
    direction   : "LONG" or "SHORT"

    Returns (approved: bool, first_failing_gate_or_pass_info: str).
    All computations use only bars up to bar_idx (no look-forward).
    """
    c15       = c15_all[: bar_idx + 1]
    zone_setup = c15[-220:]
    if len(zone_setup) < 60:
        return False, "setup_too_short"

    market = _market_state_from(zone_setup, c15_all[bar_idx].close)
    atr    = market.atr_14

    swing_idx = len(c15) - 1 - bars_since

    # ── G1: birth age ────────────────────────────────────────────────────────
    b_expand = _cap(_birth_expand(c15, swing_idx, direction, atr), bars_since)
    b_vol    = _cap(_birth_volume(c15, swing_idx),                  bars_since)
    b_struct = _cap(_birth_structure(c15, swing_idx, direction),    bars_since)
    b_any    = min(b_expand, b_vol, b_struct)

    if b_any > MAX_BIRTH_AGE:
        return False, f"birth_age={b_any}"

    # ── G2: bars after birth ─────────────────────────────────────────────────
    bab = bars_since - b_any
    if bab > BIRTH_RECENCY:
        return False, f"recency={bab}"

    # ── G3: birth quality ────────────────────────────────────────────────────
    b_bar = swing_idx + b_any
    if not (0 <= b_bar < len(c15)):
        return False, "birth_oob"

    bc     = c15[b_bar]
    b_body = abs(bc.close - bc.open)
    b_qual = min(1.0, b_body / max(atr, 1e-9))

    if b_qual < MIN_BIRTH_QUAL:
        return False, f"b_qual={b_qual:.3f}"

    # ── G4: displacement quality ─────────────────────────────────────────────
    dq = _disp_quality(c15, b_bar, direction, atr)
    if dq < MIN_DISP_QUAL:
        return False, f"disp_qual={dq:.3f}"

    # ── G5: zone freshness ───────────────────────────────────────────────────
    swing_in_setup = max(0, len(zone_setup) - 1 - bars_since)
    zone = _detect_zone(zone_setup, direction, atr, swing_in_setup)
    if zone is None:
        return False, "no_zone"
    if not _zone_bounds(zone):
        return False, "zone_no_bounds"

    fr = float(getattr(zone, "freshness", 0.0) or 0.0)
    if fr < MIN_ZONE_FRESH:
        return False, f"zone_fresh={fr:.3f}"

    return True, (f"b_any={b_any} bab={bab} bq={b_qual:.2f} "
                  f"dq={dq:.2f} fr={fr:.2f}")


def _gate_key(reason: str) -> str:
    """Normalize rejection reason to a gate label for grouping."""
    if reason.startswith("birth_age") or reason == "birth_oob":
        return "G1 birth_age"
    if reason.startswith("recency"):
        return "G2 recency"
    if reason.startswith("b_qual"):
        return "G3 b_qual"
    if reason.startswith("disp_qual"):
        return "G4 disp_qual"
    if reason.startswith("zone_fresh") or reason in ("no_zone", "zone_no_bounds"):
        return "G5 zone_fresh"
    return "other"


# ── Per-symbol tagging ────────────────────────────────────────────────────────

def _tag_symbol(symbol: str) -> List[Dict[str, Any]]:
    """
    Get X2 trades, then overlay-check each one against X3 gates.
    Returns the X2 trade list with x3_ok and x3_reason fields added.
    """
    # X2 trades (downloads internally)
    print(f"  [{symbol:5}] X2 scan    ... ", end="", flush=True)
    try:
        x2_trades, _ = _run_current_raw(symbol)
    except Exception as e:
        print(f"ERROR: {e}")
        return []
    print(f"{len(x2_trades):>3} signals")

    if not x2_trades:
        return []

    # Fresh candle data for overlay (separate download)
    print(f"  [{symbol:5}] X3 overlay ... ", end="", flush=True)
    dl = _download(symbol)
    if dl is None:
        print("download failed — skipping overlay")
        for t in x2_trades:
            t["x3_ok"] = False; t["x3_reason"] = "download_failed"
        return x2_trades

    c15_all, _ = dl

    # Build timestamp → bar_index lookup
    ts_map: Dict[str, int] = {
        c.timestamp.strftime("%Y-%m-%d %H:%M"): i
        for i, c in enumerate(c15_all)
    }

    approved = rejected = 0
    for t in x2_trades:
        ts_str    = f"{t['date']} {t['time']}"
        bar_idx   = ts_map.get(ts_str)
        direction = "LONG" if t["direction"] == "CALL" else "SHORT"

        if bar_idx is None:
            t["x3_ok"] = False; t["x3_reason"] = "bar_not_found"
            rejected += 1
            continue

        ok, reason = _x3_check(c15_all, bar_idx, direction, t["bars_since"])
        t["x3_ok"]     = ok
        t["x3_reason"] = reason
        if ok:
            approved += 1
        else:
            rejected += 1

    pct_app = approved / max(len(x2_trades), 1) * 100
    print(f"approved={approved} ({pct_app:.0f}%)  rejected={rejected}")
    return x2_trades


# ── Metrics ───────────────────────────────────────────────────────────────────

def _metrics(trades: List[Dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0,
                "late": 0.0, "vlate": 0.0, "early": 0.0, "mid": 0.0,
                "avg_pct": 0.0}
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
        "early":   sum(1 for t in trades if t.get("bucket") == "Early")    / n * 100,
        "mid":     sum(1 for t in trades if t.get("bucket") == "Mid")      / n * 100,
        "late":    sum(1 for t in trades if t.get("bucket") == "Late")     / n * 100,
        "vlate":   sum(1 for t in trades if t.get("bucket") == "VeryLate") / n * 100,
        "avg_pct": _safe_avg([t.get("pct_done", 50.0) for t in trades]),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def _print_overlay(all_t: List[Dict], app: List[Dict], rej: List[Dict]) -> None:
    W = 78
    am = _metrics(all_t); pm = _metrics(app); rm = _metrics(rej)

    print("\n" + "=" * W)
    print("  X3 OVERLAY ON X2 — Three-Way Comparison")
    print(f"  X3 gates: birth_age<={MAX_BIRTH_AGE}  recency<={BIRTH_RECENCY}"
          f"  bq>={MIN_BIRTH_QUAL}  dq>={MIN_DISP_QUAL}  zfresh>={MIN_ZONE_FRESH}")
    print("=" * W)
    hdr = "{:<22}  {:>16}  {:>16}  {:>16}"
    print(hdr.format("Metric", "X2 All", "X2 + X3 Approved", "X2 - X3 Rejected"))
    print("-" * W)

    def row(label: str, a: Any, p: Any, r: Any, fmt: str = "") -> None:
        if   fmt == "pct": fv = lambda v: f"{v:.1f}%"
        elif fmt == "r":   fv = lambda v: f"{v:+.2f}R"
        elif fmt == "f2":  fv = lambda v: f"{v:.2f}"
        else:              fv = str
        print(hdr.format(label, fv(a), fv(p), fv(r)))

    row("Signals (N)",        am["n"],      pm["n"],      rm["n"])
    row("Win Rate",           am["wr"],     pm["wr"],     rm["wr"],    "pct")
    row("Profit Factor",      am["pf"],     pm["pf"],     rm["pf"],    "f2")
    row("Total R",            am["totalr"], pm["totalr"], rm["totalr"],"r")
    row("Avg pct done",       am["avg_pct"],pm["avg_pct"],rm["avg_pct"],"pct")
    print("-" * W)
    row("Early   (<25%)",     am["early"],  pm["early"],  rm["early"], "pct")
    row("Mid     (25-50%)",   am["mid"],    pm["mid"],    rm["mid"],   "pct")
    row("Late    (50-75%)",   am["late"],   pm["late"],   rm["late"],  "pct")
    row("VeryLate (75%+)",    am["vlate"],  pm["vlate"],  rm["vlate"], "pct")
    lv_a = am["late"] + am["vlate"]
    lv_p = pm["late"] + pm["vlate"]
    lv_r = rm["late"] + rm["vlate"]
    print("-" * W)
    print(hdr.format("Late+VeryLate",
                     f"{lv_a:.1f}%", f"{lv_p:.1f}%", f"{lv_r:.1f}%"))
    print("=" * W)


def _print_rejection_breakdown(rej: List[Dict], all_n: int) -> None:
    if not rej:
        return
    W = 76
    print("\n" + "=" * W)
    print("  X3 REJECTION BREAKDOWN — Which gate blocks X2 trades (first failure)")
    print("=" * W)
    hdr = "{:<20}  {:>7}  {:>7}  {:>7}  {:>7}  {:>8}"
    print(hdr.format("Gate", "Count", "%Rej", "%All", "WR%", "TotalR"))
    print("-" * W)

    # Group by gate
    groups: Dict[str, List[Dict]] = {}
    for t in rej:
        key = _gate_key(t.get("x3_reason", "other"))
        groups.setdefault(key, []).append(t)

    ordered = ["G1 birth_age", "G2 recency", "G3 b_qual",
               "G4 disp_qual", "G5 zone_fresh", "other"]

    for gate in ordered:
        gt = groups.get(gate, [])
        if not gt:
            continue
        m  = _metrics(gt)
        pct_rej = len(gt) / max(len(rej), 1) * 100
        pct_all = len(gt) / max(all_n,   1) * 100
        print(hdr.format(
            gate,
            len(gt),
            f"{pct_rej:.0f}%",
            f"{pct_all:.0f}%",
            f"{m['wr']:.1f}%",
            f"{m['totalr']:+.1f}R",
        ))

    print("-" * W)
    # Also show bar_not_found separately
    bnf = [t for t in rej if t.get("x3_reason") == "bar_not_found"]
    if bnf:
        print(hdr.format("(bar_not_found)", len(bnf),
                         f"{len(bnf)/max(len(rej),1)*100:.0f}%",
                         f"{len(bnf)/max(all_n,1)*100:.0f}%", "—", "—"))
    print("=" * W)


def _print_per_symbol(all_t: List[Dict]) -> None:
    W = 90
    print("\n" + "=" * W)
    print("  PER-SYMBOL  (A = All X2  |  P = Approved  |  R = Rejected)")
    print("=" * W)
    hdr = "{:<7}  {:>5}  {:>5}  {:>5}  |  {:>5}  {:>5}  {:>7}  {:>6}  |  {:>5}  {:>5}  {:>7}  {:>6}"
    print(hdr.format("Symbol",
                     "A:N", "A:WR", "A:PF",
                     "P:N", "P:WR", "P:TotR", "P:LV%",
                     "R:N", "R:WR", "R:TotR", "R:LV%"))
    print("-" * W)

    for sym in SYMBOLS:
        at = [t for t in all_t if t["symbol"] == sym]
        pt = [t for t in at if t.get("x3_ok")]
        rt = [t for t in at if not t.get("x3_ok")]
        am = _metrics(at); pm = _metrics(pt); rm = _metrics(rt)
        print(hdr.format(
            sym,
            am["n"], f"{am['wr']:.0f}%", f"{am['pf']:.2f}",
            pm["n"], f"{pm['wr']:.0f}%", f"{pm['totalr']:+.1f}R",
            f"{pm['late']+pm['vlate']:.0f}%",
            rm["n"], f"{rm['wr']:.0f}%", f"{rm['totalr']:+.1f}R",
            f"{rm['late']+rm['vlate']:.0f}%",
        ))
    print("=" * W)


def _print_bucket_wr(app: List[Dict], rej: List[Dict]) -> None:
    W = 72
    print("\n" + "=" * W)
    print("  WR BY BUCKET — Approved vs Rejected")
    print("=" * W)
    hdr = "{:<16}  {:>10}  {:>7}  {:>8}  |  {:>10}  {:>7}  {:>8}"
    print(hdr.format("Bucket",
                     "P:Count", "P:WR%", "P:AvgR",
                     "R:Count", "R:WR%", "R:AvgR"))
    print("-" * W)
    tot_p = max(len(app), 1); tot_r = max(len(rej), 1)
    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        pt = [t for t in app if t.get("bucket") == bk]
        rt = [t for t in rej if t.get("bucket") == bk]
        pm = _metrics(pt); rm = _metrics(rt)
        p_avgr = _safe_avg([t["r"] for t in pt])
        r_avgr = _safe_avg([t["r"] for t in rt])
        print(hdr.format(
            bk,
            f"{len(pt)} ({len(pt)/tot_p*100:.0f}%)", f"{pm['wr']:.1f}%", f"{p_avgr:+.2f}R",
            f"{len(rt)} ({len(rt)/tot_r*100:.0f}%)", f"{rm['wr']:.1f}%", f"{r_avgr:+.2f}R",
        ))
    print("=" * W)


def _write_csv(all_t: List[Dict]) -> None:
    if not all_t:
        return
    path   = "backtest_x3_overlay.csv"
    fields = ["symbol", "date", "time", "direction", "entry",
              "bars_since", "pct_done", "bucket", "result", "r",
              "mfe_r", "mae_r", "score", "x3_ok", "x3_reason"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in all_t:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"\n  CSV saved -> {path}  ({len(all_t)} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 80)
    print("  X3 OVERLAY ON X2 — Diagnostic")
    print(f"  Symbols : {', '.join(SYMBOLS)}")
    print(f"  Window  : {DAYS} days")
    print(f"  X3 gates applied at each X2 entry bar (real-time, no look-forward):")
    print(f"    birth_age<={MAX_BIRTH_AGE}  recency<={BIRTH_RECENCY}"
          f"  b_qual>={MIN_BIRTH_QUAL}  disp_qual>={MIN_DISP_QUAL}"
          f"  zone_fresh>={MIN_ZONE_FRESH}")
    print(f"  Run at  : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80 + "\n")

    all_trades: List[Dict] = []

    for sym in SYMBOLS:
        tagged = _tag_symbol(sym)
        all_trades.extend(tagged)
        print()

    if not all_trades:
        print("No trades found.")
        return

    approved = [t for t in all_trades if t.get("x3_ok")]
    rejected = [t for t in all_trades if not t.get("x3_ok")]

    _print_overlay(all_trades, approved, rejected)
    _print_rejection_breakdown(rejected, len(all_trades))
    _print_per_symbol(all_trades)
    _print_bucket_wr(approved, rejected)
    _write_csv(all_trades)

    # Final summary
    am = _metrics(all_trades); pm = _metrics(approved); rm = _metrics(rejected)
    print(f"\n  {'-'*76}")
    print(f"  X2 All      : {am['n']:>4}  WR={am['wr']:.1f}%  PF={am['pf']:.2f}"
          f"  TotalR={am['totalr']:+.2f}R  LV={am['late']+am['vlate']:.1f}%")
    print(f"  X2 Approved : {pm['n']:>4}  WR={pm['wr']:.1f}%  PF={pm['pf']:.2f}"
          f"  TotalR={pm['totalr']:+.2f}R  LV={pm['late']+pm['vlate']:.1f}%")
    print(f"  X2 Rejected : {rm['n']:>4}  WR={rm['wr']:.1f}%  PF={rm['pf']:.2f}"
          f"  TotalR={rm['totalr']:+.2f}R  LV={rm['late']+rm['vlate']:.1f}%")
    print(f"  {'-'*76}")


if __name__ == "__main__":
    main()
