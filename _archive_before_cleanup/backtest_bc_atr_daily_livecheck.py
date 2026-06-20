# -*- coding: utf-8 -*-
"""
backtest_bc_atr_daily_livecheck.py  --  Signal Verification: Production vs Research
=====================================================================================
Approved 2026-06-20.

Purpose:
  Verify that analyzer_bc_atr_daily.scan_symbol (production analyzer) produces
  the same signals as the validated research backtest (_scan_bc_ranked from
  backtest_bc_daily_selection) when both are fed the same chart_data candles.

Engine verified (must match exactly):
  B + C + ATR_pct <= 0.52 + Top-3 per day (ranked by _rank_score)

RVI & Regime enrichment (computed after scanning, for display only):
  RVI = (ATR_today / ATR_20d_avg) * (session_vol / avg_session_vol_20d)
  RVI bucket: Low / Medium / High  (data-driven terciles)
  Regime     : Trend (ADX >= 25)  |  Range (ADX < 25)  -- Range reported separately

Comparison keys:
  (symbol, direction, date, birth_time_utc)
  -- direction normalised to LONG/SHORT for both scanners

Reports:
  1. Per-symbol load status + raw signal counts
  2. Before/after daily cap counts -- Research vs Production
  3. Matched / Missing / Extra signals
  4. Regime and RVI breakdown for both signal sets
  5. First 20 signals side-by-side with full detail row
  6. Missing and Extra signal detail (if any)

Do not modify analyzer_x2.  Do not modify execution.py.  No live orders.
Validation only.
"""
from __future__ import annotations

import os
import sys
import warnings
from collections import defaultdict
from datetime import date as date_type, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

warnings.filterwarnings("ignore")

# ── Research reference (independent implementation) ───────────────────────────
try:
    from backtest_bc_daily_selection import _scan_bc_ranked
except Exception as e:
    print(f"Cannot import _scan_bc_ranked from backtest_bc_daily_selection: {e}")
    sys.exit(1)

# ── Production analyzer ───────────────────────────────────────────────────────
try:
    from analyzer_bc_atr_daily import (
        scan_symbol   as prod_scan_symbol,
        select_daily  as prod_select_daily,
        load_json,
        load_symbol_candles,
    )
except Exception as e:
    print(f"Cannot import from analyzer_bc_atr_daily: {e}")
    sys.exit(1)

# ── Core market-state infrastructure (RVI needs _market_state_from) ───────────
try:
    from backtest_runner_x2 import _market_state_from, MIN_HISTORY
except Exception as e:
    print(f"Cannot import from backtest_runner_x2: {e}")
    sys.exit(1)

try:
    from analyzer_x2 import Candle
except Exception as e:
    print(f"Cannot import Candle from analyzer_x2: {e}")
    sys.exit(1)


# ── Constants (match production engine exactly) ───────────────────────────────

SYMBOLS: List[str] = [
    "AAPL", "AMD",  "TSLA", "AVGO", "COST", "LLY",  "PANW", "CRM",
    "QQQ",  "SPY",  "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
]

CHART_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart_data")
ATR_THRESHOLD = 0.52
TOP_N_DAILY   = 3
ADX_TREND_MIN = 25.0
SHOW_N        = 20   # side-by-side rows to display

W = 108


# ── RVI helpers ───────────────────────────────────────────────────────────────

def _precompute_session_vols(
    c15_all: List[Candle],
) -> Dict[date_type, Dict[int, float]]:
    """Cumulative session volume from UTC 13:30 to each bar, keyed by (date, offset)."""
    result: Dict[date_type, Dict[int, float]] = defaultdict(dict)
    for c in c15_all:
        sm = (c.timestamp.hour - 9) * 60 + c.timestamp.minute - 30
        if sm < 240 or sm >= 525:
            continue
        offset = (sm - 240) // 15
        d      = c.timestamp.date()
        prev   = result[d].get(offset - 1, 0.0) if offset > 0 else 0.0
        result[d][offset] = prev + c.volume
    return dict(result)


def _atr_20d_avg(c15_all: List[Candle], idx: int, lookback: int = 400) -> float:
    start = max(1, idx - lookback)
    trs   = []
    for j in range(start, idx):
        hi = c15_all[j].high; lo = c15_all[j].low; pc = c15_all[j-1].close
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _compute_rvi(
    c15_all:     List[Candle],
    bar_idx:     int,
    session_min: int,
    atr:         float,
    session_cum: Dict[date_type, Dict[int, float]],
) -> Tuple[float, float, float]:
    """Return (rvi, vol_ratio, atr_ratio)."""
    atr_20d   = _atr_20d_avg(c15_all, bar_idx, 400)
    atr_ratio = atr / atr_20d if atr_20d > 1e-9 else 1.0

    if session_min >= 240:
        offset    = (session_min - 240) // 15
        b_date    = c15_all[bar_idx].timestamp.date()
        vol_now   = session_cum.get(b_date, {}).get(offset, 0.0)
        prior_d   = sorted(d for d in session_cum if d < b_date and offset in session_cum[d])
        past20    = prior_d[-20:]
        avg_vol   = (sum(session_cum[d][offset] for d in past20) / len(past20)
                     if past20 else vol_now)
        vol_ratio = vol_now / avg_vol if avg_vol > 1e-9 else 1.0
    else:
        vol_ratio = 1.0

    rvi = atr_ratio * vol_ratio
    return round(rvi, 4), round(vol_ratio, 4), round(atr_ratio, 4)


# ── Enrichment: add RVI and Regime to each signal ────────────────────────────

def _enrich(
    signals:     List[Dict],
    symbol:      str,
    c15_all:     List[Candle],
) -> List[Dict]:
    """
    Add rvi, vol_ratio, atr_ratio, adx, regime to every signal for symbol.
    Uses birth bar timestamp to locate the bar.
    """
    session_cum = _precompute_session_vols(c15_all)
    ts_to_idx   = {c.timestamp: i for i, c in enumerate(c15_all)}

    for sig in signals:
        if sig.get("symbol") != symbol:
            continue
        btime = sig.get("birth_time") or sig.get("time", "")
        bdate = sig.get("date", "")
        try:
            birth_dt = datetime.strptime(f"{bdate} {btime}", "%Y-%m-%d %H:%M")
        except ValueError:
            sig.update(rvi=1.0, vol_ratio=1.0, atr_ratio=1.0, adx=0.0, regime="Unknown")
            continue

        bar_idx = ts_to_idx.get(birth_dt)
        if bar_idx is None or bar_idx < MIN_HISTORY:
            sig.update(rvi=1.0, vol_ratio=1.0, atr_ratio=1.0, adx=0.0, regime="Unknown")
            continue

        setup  = c15_all[max(0, bar_idx - 220) : bar_idx + 1]
        market = _market_state_from(setup, c15_all[bar_idx].close)
        atr    = market.atr_14
        adx    = market.adx
        sm     = (birth_dt.hour - 9) * 60 + birth_dt.minute - 30

        rvi, vol_r, atr_r = _compute_rvi(c15_all, bar_idx, sm, atr, session_cum)
        sig["rvi"]       = rvi
        sig["vol_ratio"] = vol_r
        sig["atr_ratio"] = atr_r
        sig["adx"]       = round(adx, 1)
        sig["regime"]    = "Trend" if adx >= ADX_TREND_MIN else "Range"

    return signals


def _assign_rvi_buckets(signals: List[Dict]) -> None:
    """Add rvi_bucket (Low/Medium/High) by tercile across the full pool."""
    vals = [s["rvi"] for s in signals if "rvi" in s]
    if not vals:
        return
    p33 = float(np.percentile(vals, 33.33))
    p67 = float(np.percentile(vals, 66.67))
    for s in signals:
        v = s.get("rvi", 1.0)
        s["rvi_bucket"] = ("Low" if v <= p33 else ("Medium" if v <= p67 else "High"))
    return


# ── Reference scanner helpers ─────────────────────────────────────────────────

def _norm_dir(d: str) -> str:
    """Normalise CALL→LONG, PUT→SHORT for matching."""
    return "LONG" if d in ("CALL", "LONG") else "SHORT"


def _sig_key(sig: Dict) -> str:
    """Canonical match key: symbol|LONG_or_SHORT|date|birth_time_utc."""
    btime = sig.get("birth_time") or sig.get("time", "")
    return f"{sig['symbol']}|{_norm_dir(sig['direction'])}|{sig['date']}|{btime}"


def _ref_daily_select(pool: List[Dict], top_n: int = TOP_N_DAILY) -> List[Dict]:
    """Apply top-N daily cap by rank_score (mirrors prod_select_daily)."""
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for s in pool:
        by_date[s["date"]].append(s)
    out: List[Dict] = []
    for dt in sorted(by_date):
        ranked = sorted(by_date[dt], key=lambda x: -x["rank_score"])
        out.extend(ranked[:top_n])
    return out


# ── Display helpers ───────────────────────────────────────────────────────────

def _hline(ch: str = "-") -> str:
    return "  " + ch * (W - 2)


def _pct(n: int, total: int) -> str:
    return f"{n/total*100:.0f}%" if total else "N/A"


# ── Side-by-side detail table ─────────────────────────────────────────────────

def _print_side_by_side(
    ref_cap:  List[Dict],
    prod_cap: List[Dict],
    ref_keys: Set[str],
    prod_keys: Set[str],
    title: str,
    n: int = SHOW_N,
) -> None:
    # Merge all unique keys from both sets, sorted by date+time
    all_sigs: Dict[str, Dict] = {}
    for s in ref_cap:
        all_sigs[_sig_key(s)] = {"ref": s, "prod": None}
    for s in prod_cap:
        k = _sig_key(s)
        if k in all_sigs:
            all_sigs[k]["prod"] = s
        else:
            all_sigs[k] = {"ref": None, "prod": s}

    # Sort by date+birth_time
    def _sort_key(item: Tuple[str, Dict]) -> str:
        k = item[0]
        parts = k.split("|")
        # key is symbol|dir|date|time
        return f"{parts[2]}|{parts[3]}|{parts[0]}"

    rows = sorted(all_sigs.items(), key=_sort_key)[:n]

    print(f"\n  {title} (first {n})")
    hdr = ("  {:<5}  {:<6}  {:<10}  {:<5}  {:<5}  {:>6}  {:>7}  {:>8}  {:<8}  {:>7}  {:>6}")
    print(hdr.format("#", "Sym", "Date", "Time", "Dir", "ATR%", "RVI",
                      "RVI-Bkt", "Regime", "RankScr", "Match"))
    print(_hline())
    for idx, (key, pair) in enumerate(rows, 1):
        # Prefer ref signal for display (they should be identical on match)
        s    = pair["ref"] or pair["prod"]
        btime = s.get("birth_time") or s.get("time", "")
        dirn  = _norm_dir(s["direction"])
        atr_p = s.get("atr_pct", 0.0)
        rvi   = s.get("rvi", 0.0)
        bkt   = s.get("rvi_bucket", "?")
        reg   = s.get("regime", "?")
        rank  = s.get("rank_score", 0.0)

        in_ref  = key in ref_keys
        in_prod = key in prod_keys
        if   in_ref and in_prod:  match = "OK"
        elif in_ref:              match = "MISS"    # in ref but not prod
        else:                     match = "EXTRA"   # in prod but not ref

        print(hdr.format(
            idx, s["symbol"], s["date"], btime, dirn,
            f"{atr_p:.3f}", f"{rvi:.3f}", bkt, reg,
            f"{rank:.1f}", match,
        ))
    print(_hline())


def _print_detail_list(signals: List[Dict], label: str) -> None:
    if not signals:
        print(f"\n  {label}: none")
        return
    print(f"\n  {label} ({len(signals)} signals):")
    hdr = "  {:<6}  {:<5}  {:<10}  {:<5}  {:<5}  {:>6}  {:>7}  {:>8}  {:<8}  {:>7}"
    print(hdr.format("#", "Sym", "Date", "Time", "Dir", "ATR%", "RVI", "RVI-Bkt",
                     "Regime", "RankScr"))
    print(_hline("-"))
    for i, s in enumerate(signals, 1):
        btime = s.get("birth_time") or s.get("time", "")
        print(hdr.format(
            i, s["symbol"], s["date"], btime, _norm_dir(s["direction"]),
            f"{s.get('atr_pct',0):.3f}", f"{s.get('rvi',0):.3f}",
            s.get("rvi_bucket","?"), s.get("regime","?"),
            f"{s.get('rank_score',0):.1f}",
        ))


# ── Count table helper ────────────────────────────────────────────────────────

def _counts_row(
    label: str,
    raw_n: int,
    cap_n: int,
    trend_n: int,
    range_n: int,
    low_n: int,
    mid_n: int,
    high_n: int,
) -> str:
    return (f"  {label:<22}  {raw_n:>8}  {cap_n:>9}  "
            f"{trend_n:>8}  {range_n:>7}  "
            f"{low_n:>6}  {mid_n:>8}  {high_n:>8}")


def _breakdown(signals: List[Dict]) -> Tuple[int, int, int, int, int, int]:
    trend = sum(1 for s in signals if s.get("regime") == "Trend")
    rang  = sum(1 for s in signals if s.get("regime") == "Range")
    lo    = sum(1 for s in signals if s.get("rvi_bucket") == "Low")
    mid   = sum(1 for s in signals if s.get("rvi_bucket") == "Medium")
    hi    = sum(1 for s in signals if s.get("rvi_bucket") == "High")
    return trend, rang, lo, mid, hi, 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  BC-ATR-DAILY LIVECHECK  --  Production Analyzer vs Research Backtest")
    print(f"  Engine : B + C + ATR<=0.52 + Top-{TOP_N_DAILY}/day")
    print(f"  RVI    : ATR_today/ATR_20d * session_vol/avg_session_vol_20d  (tercile buckets)")
    print(f"  Regime : Trend=ADX>={ADX_TREND_MIN:.0f}  Range=ADX<{ADX_TREND_MIN:.0f}  "
          f"(Range reported separately)")
    print(f"  Data   : chart_data/  (~8 months)")
    print(f"  Run    : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W + "\n")

    # ── Step 1: Load candles and run both scanners ────────────────────────────
    ref_raw:  List[Dict] = []
    prod_raw: List[Dict] = []
    sym_c15:  Dict[str, List[Candle]] = {}

    for sym in SYMBOLS:
        print(f"  [{sym:5}] loading chart_data ... ", end="", flush=True)
        data = load_symbol_candles(sym, CHART_DIR)
        if data is None:
            print("NO DATA"); continue
        c15, c1h = data
        sym_c15[sym] = c15

        # Reference (independent engine, yfinance-origin but fed chart_data candles)
        ref_sigs  = _scan_bc_ranked(sym, c15, c1h)
        # Apply ATR filter (reference scanner does not filter internally)
        ref_filt  = [s for s in ref_sigs if s.get("atr_pct", 99.0) <= ATR_THRESHOLD]
        # Normalise direction for matching
        for s in ref_filt:
            s["_dir_norm"] = _norm_dir(s["direction"])

        # Production (analyzer_bc_atr_daily -- ATR filter applied inside)
        prod_sigs = prod_scan_symbol(sym, c15, c1h)

        ref_raw.extend(ref_filt)
        prod_raw.extend(prod_sigs)

        print(f"OK  ref_raw={len(ref_filt)}  prod_raw={len(prod_sigs)}")

    # ── Step 2: Apply Top-N daily cap ────────────────────────────────────────
    ref_cap  = _ref_daily_select(ref_raw,  TOP_N_DAILY)
    prod_cap = prod_select_daily(prod_raw, TOP_N_DAILY)

    # ── Step 3: Enrich with RVI and Regime ───────────────────────────────────
    print("\n  Computing RVI and Regime for all signals ... ", end="", flush=True)
    for sym, c15 in sym_c15.items():
        r_sym  = [s for s in ref_raw  if s["symbol"] == sym]
        p_sym  = [s for s in prod_raw if s["symbol"] == sym]
        _enrich(r_sym,  sym, c15)
        _enrich(p_sym,  sym, c15)
    # Propagate enrichment to capped sets (they share same dict objects)
    _assign_rvi_buckets(ref_raw)
    _assign_rvi_buckets(prod_raw)
    # Sync to capped sets (same dicts, already mutated)
    print("done")

    # ── Step 4: Build match/miss/extra sets ───────────────────────────────────
    ref_keys  = {_sig_key(s) for s in ref_cap}
    prod_keys = {_sig_key(s) for s in prod_cap}

    matched_keys = ref_keys & prod_keys
    missing_keys = ref_keys - prod_keys   # in research, not in production
    extra_keys   = prod_keys - ref_keys   # in production, not in research

    missing_sigs = [s for s in ref_cap  if _sig_key(s) in missing_keys]
    extra_sigs   = [s for s in prod_cap if _sig_key(s) in extra_keys]

    # ── Step 5: Count breakdown ───────────────────────────────────────────────
    print("\n" + "=" * W)
    print("  SIGNAL COUNTS")
    print("=" * W)
    hdr2 = ("  {:<22}  {:>8}  {:>9}  {:>8}  {:>7}  {:>6}  {:>8}  {:>8}")
    print(hdr2.format("Source", "Raw", "AfterCap", "Trend", "Range",
                      "RVI-Lo", "RVI-Mid", "RVI-Hi"))
    print(_hline())

    for label, raw, cap in [
        ("Research (ref)",  ref_raw,  ref_cap),
        ("Production",      prod_raw, prod_cap),
    ]:
        t_raw, r_raw, lo_r, mid_r, hi_r, _ = _breakdown(raw)
        t_cap, r_cap, lo_c, mid_c, hi_c, _ = _breakdown(cap)
        print(_counts_row(
            label + " [raw]", len(raw),  0,       t_raw, r_raw, lo_r, mid_r, hi_r))
        print(_counts_row(
            label + " [top-3]", 0,      len(cap), t_cap, r_cap, lo_c, mid_c, hi_c))
        print()

    # ── Step 6: Comparison summary ────────────────────────────────────────────
    print("\n" + "=" * W)
    print("  COMPARISON SUMMARY")
    print("=" * W)

    total_all = len(ref_keys | prod_keys)
    print(f"  Research signals (after cap)   : {len(ref_cap):>5}")
    print(f"  Production signals (after cap) : {len(prod_cap):>5}")
    print(f"  Matched (in both)              : {len(matched_keys):>5}"
          f"  ({_pct(len(matched_keys), max(len(ref_keys),1))} of research)")
    print(f"  Missing (ref only)             : {len(missing_keys):>5}"
          f"  ({_pct(len(missing_keys), len(ref_keys))} of research)")
    print(f"  Extra   (prod only)            : {len(extra_keys):>5}"
          f"  ({_pct(len(extra_keys),  len(prod_keys))} of production)")
    print()

    if len(matched_keys) == len(ref_keys) == len(prod_keys):
        print("  VERDICT: PERFECT MATCH -- production analyzer is verified.")
    elif len(missing_keys) == 0 and len(extra_keys) > 0:
        print(f"  VERDICT: Production has {len(extra_keys)} EXTRA signals not in research.")
        print("           Likely cause: daily cap ordering difference or Regime/RVI filter gap.")
    elif len(missing_keys) > 0 and len(extra_keys) == 0:
        print(f"  VERDICT: Production MISSING {len(missing_keys)} research signals.")
        print("           Likely cause: ATR filter applied at different point in engine.")
    else:
        print(f"  VERDICT: PARTIAL MATCH. Missing={len(missing_keys)}, Extra={len(extra_keys)}.")
        print("           Investigate both lists below.")

    # ── Step 7: Regime breakdown for matched signals ──────────────────────────
    matched_sigs = [s for s in ref_cap if _sig_key(s) in matched_keys]
    if matched_sigs:
        print("\n" + "=" * W)
        print("  MATCHED SIGNALS  --  Regime & RVI Breakdown")
        print("=" * W)
        t, r, lo, mid, hi, _ = _breakdown(matched_sigs)
        print(f"  Trend  : {t:>4}  ({_pct(t, len(matched_sigs))})")
        print(f"  Range  : {r:>4}  ({_pct(r, len(matched_sigs))})  <- reported separately")
        print(f"  RVI Lo : {lo:>4}  ({_pct(lo, len(matched_sigs))})")
        print(f"  RVI Mid: {mid:>4}  ({_pct(mid,len(matched_sigs))})")
        print(f"  RVI Hi : {hi:>4}  ({_pct(hi, len(matched_sigs))})  <- preferred (PF=2.64 in study)")
        rvs = [s.get("rvi", 0.0) for s in matched_sigs]
        if rvs:
            p33 = float(np.percentile(rvs, 33.33))
            p67 = float(np.percentile(rvs, 66.67))
            print(f"  RVI stats: mean={np.mean(rvs):.3f}  median={np.median(rvs):.3f}"
                  f"  p33={p33:.3f}  p67={p67:.3f}")

        # Trend-only High-RVI signals (best bucket from study)
        best = [s for s in matched_sigs
                if s.get("regime") == "Trend" and s.get("rvi_bucket") == "High"]
        worst = [s for s in matched_sigs if s.get("regime") == "Range"]
        print(f"\n  Trend + High-RVI signals : {len(best):>4}  "
              f"(study PF=2.64, WR=59.5%)")
        print(f"  Range signals            : {len(worst):>4}  "
              f"(study PF=0.48, WR=21.1%)  -> consider excluding")

    # ── Step 8: Side-by-side first SHOW_N ────────────────────────────────────
    _print_side_by_side(
        ref_cap, prod_cap, ref_keys, prod_keys,
        title=f"SIDE-BY-SIDE (first {SHOW_N})",
        n=SHOW_N,
    )

    # ── Step 9: Missing signal detail ────────────────────────────────────────
    if missing_sigs:
        _print_detail_list(missing_sigs, "MISSING FROM PRODUCTION (in research only)")
        print("  NOTE: Missing signals indicate the production engine fires/filters")
        print("        at a slightly different point than the research scanner.")
        print("        Check: ATR filter timing (break vs continue), CONF_END range.")

    # ── Step 10: Extra signal detail ──────────────────────────────────────────
    if extra_sigs:
        _print_detail_list(extra_sigs, "EXTRA IN PRODUCTION (not in research)")
        print("  NOTE: Extra signals indicate production fires signals the research")
        print("        scanner does not.  Check: POOL_COOLDOWN vs last_birth guard.")

    # ── Step 11: Per-symbol match table ──────────────────────────────────────
    print("\n" + "=" * W)
    print("  PER-SYMBOL MATCH  (after Top-3 cap)")
    print("=" * W)
    shdr = "  {:<6}  {:>5}  {:>5}  {:>7}  {:>7}  {:>7}  {}"
    print(shdr.format("Symbol", "Ref", "Prod", "Matched", "Missing", "Extra", "Status"))
    print(_hline())
    for sym in SYMBOLS:
        r_k = {k for k in ref_keys  if k.startswith(sym + "|")}
        p_k = {k for k in prod_keys if k.startswith(sym + "|")}
        mat = r_k & p_k; mis = r_k - p_k; ext = p_k - r_k
        status = "OK" if (not mis and not ext) else ("MISS" if not ext else ("EXTRA" if not mis else "DIFF"))
        if r_k or p_k:
            print(shdr.format(sym, len(r_k), len(p_k),
                              len(mat), len(mis), len(ext), status))
    print(_hline())
    print(shdr.format("TOTAL", len(ref_keys), len(prod_keys),
                      len(matched_keys), len(missing_keys), len(extra_keys),
                      "PASS" if not missing_keys and not extra_keys else "FAIL"))

    # ── Step 12: Range signal list (always shown separately) ─────────────────
    range_sigs = [s for s in ref_cap if s.get("regime") == "Range"]
    if range_sigs:
        print("\n" + "=" * W)
        print("  RANGE-REGIME SIGNALS  (ADX < 25)  --  Reported Separately")
        print(f"  Study result: Range PF=0.48, WR=21.1%  -- these are the worst performers.")
        print("=" * W)
        _print_detail_list(range_sigs, "Range signals in research pool (after cap)")

    print("\n" + "=" * W)
    print("  LIVECHECK COMPLETE")
    print("=" * W + "\n")


if __name__ == "__main__":
    main()
