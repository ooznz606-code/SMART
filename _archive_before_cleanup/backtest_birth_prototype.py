# -*- coding: utf-8 -*-
"""
backtest_birth_prototype.py — Birth-Anchored Entry Prototype vs Current X2
===========================================================================
Evaluates whether Birth-Anchored entry improves timing without reducing trade
quality, before any changes are made to the live analyzer (analyzer_x2.py).

Current X2   : Sweep -> Displacement -> Zone -> Retest/FreshZone -> Entry
Birth Prototype: Swing -> Birth Event -> Zone (15m) -> Entry

Birth = earliest of:
  B1 Expansion Candle : body >= 0.8 ATR in direction
  B2 Volume Expansion : volume >= 1.3x prior 20-bar average
  B3 Structure Break  : close breaks prior 5-bar local swing level

Filters kept:
  * HTF trend filter (same _htf_direction)
  * ADX filter (same min/max bounds)
  * Risk model (same zone-based stop, same STOP_BUFFER_ATR, same TP1_R)
  * Confidence model (adapted: b_qual replaces sq+dq)
  * Cooldown (same SETUP_COOLDOWN)

Output: side-by-side WR / PF / TotalR / MFE / MAE / bucket distribution.
No changes to analyzer_x2.py.
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import csv

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
        _dir_enum,
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
    from backtest_move_birth import _birth_expand, _birth_volume, _birth_structure, _cap
except Exception as e:
    print(f"Cannot import backtest_move_birth: {e}")
    sys.exit(1)


# ── Prototype parameters ──────────────────────────────────────────────────────
MAX_BIRTH_AGE  = 12    # birth must fire within this many 15m bars of swing extreme
BIRTH_RECENCY  = 8     # entry bar must be within this many bars of the birth event
MIN_ZONE_FRESH = 0.25  # mirrors analyzer_x2.MIN_ZONE_FRESHNESS
MAX_ZONE_DIST  = 3.2   # ATR gate (same as current backtest retest path)
STOP_BUFFER    = 0.22  # mirrors analyzer_x2.STOP_BUFFER_ATR


# ── Birth-Anchored scan ───────────────────────────────────────────────────────

def _run_birth_proto(symbol: str) -> List[Dict[str, Any]]:
    end   = datetime.today()
    start = end - timedelta(days=DAYS + 3)

    try:
        df15 = _flat(yf.download(symbol, start=start, end=end, interval="15m",
                                  progress=False, auto_adjust=True))
        df1h = _flat(yf.download(symbol, start=start, end=end, interval="1h",
                                  progress=False, auto_adjust=True))
    except Exception as e:
        print(f"  download error: {e}")
        return []

    if df15 is None or len(df15) < MIN_HISTORY:
        return []

    c15_all: List[Candle] = _to_candles(df15)
    c1h_all: List[Candle] = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []

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

        # Always use 15m for zone detection in this prototype (avoids 1H/15m mismatch)
        zone_setup = c15[-220:]
        if len(zone_setup) < 60:
            continue

        # Use 1H for market state and HTF (same as current system)
        htf_candles = c1h[-300:] if c1h and len(c1h) >= 60 else c15[-220:]
        market = _market_state_from(zone_setup, c15_all[i].close)
        atr    = market.atr_14
        adx    = market.adx

        if adx < min_adx or adx > max_adx:
            continue
        if (i - last_bar) < SETUP_COOLDOWN:
            continue

        htf  = _htf_direction(htf_candles, market)
        dirs = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in dirs:
            price = c15_all[i].close

            # ── Swing extreme ─────────────────────────────────────────
            sw_start, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            swing_idx = len(c15) - 1 - bars_since

            # ── Birth anchor detection ────────────────────────────────
            b_expand = _cap(_birth_expand(c15, swing_idx, direction, atr), bars_since)
            b_vol    = _cap(_birth_volume(c15, swing_idx),                  bars_since)
            b_struct = _cap(_birth_structure(c15, swing_idx, direction),    bars_since)
            b_any    = min(b_expand, b_vol, b_struct)

            # Gate 1: birth fires quickly after swing extreme
            if b_any > MAX_BIRTH_AGE:
                continue

            # Gate 2: entry is within BIRTH_RECENCY bars of birth event
            bars_after_birth = bars_since - b_any
            if bars_after_birth > BIRTH_RECENCY:
                continue

            # ── Zone detection in 15m, anchored from swing ────────────
            swing_in_setup = max(0, len(zone_setup) - 1 - bars_since)
            zone = _detect_zone(zone_setup, direction, atr, swing_in_setup)
            if zone is None:
                continue
            zb = _zone_bounds(zone)
            if not zb:
                continue
            z_top, z_bot = zb
            z_mid = _zone_mid(zone)

            if zone.freshness < MIN_ZONE_FRESH:
                continue
            if abs(price - z_mid) / max(atr, 0.01) > MAX_ZONE_DIST:
                continue

            # ── Birth quality (body of birth candle as fraction of ATR) ──
            b_bar_idx = swing_idx + b_any
            b_qual = 0.5
            if 0 <= b_bar_idx < len(c15):
                bc     = c15[b_bar_idx]
                b_body = abs(bc.close - bc.open)
                b_qual = min(1.0, b_body / max(atr, 1e-6))

            zq = float(getattr(zone, "quality",   0.7) or 0.7)
            fr = float(getattr(zone, "freshness", 0.7) or 0.7)
            try:
                vr = _volume_ratio(zone_setup)
            except Exception:
                vr = 1.0

            # Confidence: b_qual*28 replaces sq*14 + dq*14 from the current system
            score = 25.0 + b_qual*28.0 + zq*20.0 + fr*10.0
            score += 5.0 if htf != "NEUTRAL" else 0.0
            score += 5.0 if vr >= 0.8 else 2.0
            if score < min_conf:
                continue

            # ── Risk model (unchanged) ────────────────────────────────
            entry = price
            stop  = (z_bot - atr*STOP_BUFFER) if direction == "LONG" \
                    else (z_top + atr*STOP_BUFFER)
            risk  = abs(entry - stop)
            if risk <= 0:
                continue
            tp1 = entry + risk*TP1_R if direction == "LONG" else entry - risk*TP1_R

            # ── Timing metrics ────────────────────────────────────────
            future  = c15_all[i + 1 : i + 1 + SWING_LOOKFWD]
            sw_end  = _swing_end(future, direction)

            if direction == "LONG":
                total_mv = (sw_end - sw_start) if sw_end else None
                at_entry = entry - sw_start
            else:
                total_mv = (sw_start - sw_end) if sw_end else None
                at_entry = sw_start - entry

            pct = (round(at_entry / total_mv * 100, 1)
                   if (total_mv and total_mv > 0) else 50.0)

            # ── Outcome simulation (unchanged logic) ──────────────────
            outcome = "BE"; r_mult = 0.0; ep = entry; mfe = 0.0; mae = 0.0
            for j, fc in enumerate(c15_all[i + 1 : i + 1 + SIM_BARS + 1], 1):
                if direction == "LONG":
                    mfe = max(mfe, (fc.high - entry) / risk)
                    mae = min(mae, (fc.low  - entry) / risk)
                    if fc.low  <= stop: outcome="LOSS"; ep=stop; r_mult=-1.0; break
                    if fc.high >= tp1:  outcome="WIN";  ep=tp1;  r_mult=TP1_R; break
                else:
                    mfe = max(mfe, (entry - fc.low)  / risk)
                    mae = min(mae, (entry - fc.high) / risk)
                    if fc.high >= stop: outcome="LOSS"; ep=stop; r_mult=-1.0; break
                    if fc.low  <= tp1:  outcome="WIN";  ep=tp1;  r_mult=TP1_R; break
            else:
                ep = c15_all[min(i + SIM_BARS, len(c15_all) - 1)].close

            trades.append({
                "symbol":        symbol,
                "date":          ts.strftime("%Y-%m-%d"),
                "time":          ts.strftime("%H:%M"),
                "direction":     "CALL" if direction == "LONG" else "PUT",
                "entry":         round(entry,    4),
                "stop":          round(stop,     4),
                "tp1":           round(tp1,      4),
                "exit_price":    round(ep,       4),
                "swing_start":   round(sw_start, 4),
                "bars_since":    bars_since,
                "b_any":         b_any,
                "bars_after_birth": bars_after_birth,
                "pct_done":      pct,
                "mfe_r":         round(mfe, 2),
                "mae_r":         round(mae, 2),
                "result":        outcome,
                "r":             round(r_mult, 2),
                "score":         round(score, 1),
                "bucket":        _bucket(pct),
                "zone_fresh":    round(fr, 3),
                "b_qual":        round(b_qual, 3),
            })
            last_bar = i
            break

    return trades


# ── Metrics helper ────────────────────────────────────────────────────────────

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

def _print_comparison(cur: Dict, bth: Dict) -> None:
    W = 74
    print("\n" + "=" * W)
    print("  SIDE-BY-SIDE: Current X2  vs  Birth-Anchored Prototype")
    print(f"  Birth params: MAX_BIRTH_AGE={MAX_BIRTH_AGE}b | BIRTH_RECENCY={BIRTH_RECENCY}b")
    print("=" * W)
    hdr = "{:<22}  {:>22}  {:>22}"

    def row(label: str, cv: Any, bv: Any, fmt: str = "") -> None:
        if   fmt == "pct":  fv = lambda v: f"{v:.1f}%"
        elif fmt == "r":    fv = lambda v: f"{v:+.2f}R"
        elif fmt == "f2":   fv = lambda v: f"{v:.2f}"
        elif fmt == "f1":   fv = lambda v: f"{v:.1f}"
        else:               fv = lambda v: str(v)
        print(hdr.format(label, fv(cv), fv(bv)))

    def delta(cv: float, bv: float, fmt: str = "pct", higher_is_better: bool = True) -> str:
        d = bv - cv
        sign = "+" if d >= 0 else ""
        s = f"{sign}{d:.1f}%" if fmt == "pct" else f"{sign}{d:.2f}R"
        good = (d > 0) == higher_is_better
        return f"  ({s})"

    print(hdr.format("Metric", "Current X2", "Birth Prototype"))
    print("-" * W)
    row("Signals (N)",         cur["n"],       bth["n"])
    row("Win Rate",            cur["wr"],       bth["wr"],      "pct")
    row("Profit Factor",       cur["pf"],       bth["pf"],      "f2")
    row("Total R",             cur["totalr"],   bth["totalr"],  "r")
    row("Avg MFE",             cur["mfe"],      bth["mfe"],     "f2")
    row("Avg MAE",             cur["mae"],      bth["mae"],     "f2")
    row("Avg bars since swing", cur["avg_bars"], bth["avg_bars"], "f1")
    row("Avg pct done",        cur["avg_pct"],  bth["avg_pct"], "pct")
    print("-" * W)
    row("Early   (<25%)",      cur["early"],    bth["early"],   "pct")
    row("Mid     (25-50%)",    cur["mid"],      bth["mid"],     "pct")
    row("Late    (50-75%)",    cur["late"],     bth["late"],    "pct")
    row("VeryLate (75%+)",     cur["vlate"],    bth["vlate"],   "pct")
    lv_c = cur["late"] + cur["vlate"]
    lv_b = bth["late"] + bth["vlate"]
    print("-" * W)
    print(hdr.format("Late+VeryLate", f"{lv_c:.1f}%", f"{lv_b:.1f}%"))
    print("=" * W)


def _print_per_symbol(cur_all: List[Dict], bth_all: List[Dict]) -> None:
    W = 88
    print("\n" + "=" * W)
    print("  PER-SYMBOL  (C = Current X2  |  B = Birth Prototype)")
    print("=" * W)
    hdr = "{:<7}  {:>6}  {:>5}  {:>7}  {:>7}  |  {:>6}  {:>5}  {:>7}  {:>7}  {:>8}"
    print(hdr.format("Symbol",
                     "C:Sig", "C:WR", "C:TotR", "C:LV%",
                     "B:Sig", "B:WR", "B:TotR", "B:LV%", "B:AvgBirth"))
    print("-" * W)

    for sym in SYMBOLS:
        ct = [t for t in cur_all if t["symbol"] == sym]
        bt = [t for t in bth_all if t["symbol"] == sym]
        cm = _metrics(ct)
        bm = _metrics(bt)
        b_avg = _safe_avg([t["b_any"] for t in bt]) if bt else 0.0
        print(hdr.format(
            sym,
            cm["n"], f"{cm['wr']:.0f}%", f"{cm['totalr']:+.1f}R",
            f"{cm['late']+cm['vlate']:.0f}%",
            bm["n"], f"{bm['wr']:.0f}%", f"{bm['totalr']:+.1f}R",
            f"{bm['late']+bm['vlate']:.0f}%",
            f"{b_avg:.1f}b",
        ))
    print("=" * W)


def _print_bucket_detail(cur_all: List[Dict], bth_all: List[Dict]) -> None:
    W = 80
    print("\n" + "=" * W)
    print("  BUCKET BREAKDOWN  (WR and signal count per bucket, both systems)")
    print("=" * W)
    hdr = "{:<16}  {:>8}  {:>7}  {:>8}  |  {:>8}  {:>7}  {:>8}"
    print(hdr.format("Bucket",
                     "C:Count", "C:WR%", "C:AvgR",
                     "B:Count", "B:WR%", "B:AvgR"))
    print("-" * W)

    BUCKETS = ["Early", "Mid", "Late", "VeryLate"]
    tot_c = len(cur_all); tot_b = len(bth_all)

    for bk in BUCKETS:
        ct = [t for t in cur_all if t["bucket"] == bk]
        bt = [t for t in bth_all if t["bucket"] == bk]
        cm = _metrics(ct); bm = _metrics(bt)
        c_pct = len(ct)/tot_c*100 if tot_c else 0
        b_pct = len(bt)/tot_b*100 if tot_b else 0
        c_avgr = _safe_avg([t["r"] for t in ct])
        b_avgr = _safe_avg([t["r"] for t in bt])
        print(hdr.format(
            bk,
            f"{len(ct)} ({c_pct:.0f}%)", f"{cm['wr']:.1f}%", f"{c_avgr:+.2f}R",
            f"{len(bt)} ({b_pct:.0f}%)", f"{bm['wr']:.1f}%", f"{b_avgr:+.2f}R",
        ))
    print("=" * W)


def _print_birth_detail(bth_all: List[Dict]) -> None:
    """Stats specific to the birth prototype."""
    if not bth_all:
        return
    W = 70
    print("\n" + "=" * W)
    print("  BIRTH PROTOTYPE — Internal Stats")
    print("=" * W)
    avg_b   = _safe_avg([t["b_any"]            for t in bth_all])
    avg_bab = _safe_avg([t["bars_after_birth"]  for t in bth_all])
    avg_bq  = _safe_avg([t["b_qual"]            for t in bth_all])
    avg_fr  = _safe_avg([t["zone_fresh"]        for t in bth_all])
    print(f"  Avg birth fires at   : {avg_b:.1f} bars after swing extreme")
    print(f"  Avg entry lag (birth->entry) : {avg_bab:.1f} bars")
    print(f"  Avg birth candle quality : {avg_bq:.3f}  (fraction of ATR)")
    print(f"  Avg zone freshness   : {avg_fr:.3f}")

    # Birth type distribution (which anchor was earliest)
    n = len(bth_all)
    # b_any == b_expand → expand was earliest or tied
    print()
    print("  Anchor type distribution (which fired first / tied for first):")
    for key, label in [("b_expand", "B1 Expansion"), ("b_vol", "B2 Volume"),
                       ("b_struct", "B3 Structure")]:
        if key in bth_all[0]:
            cnt = sum(1 for t in bth_all if t.get(key) == t["b_any"])
            print(f"    {label:<18}: {cnt:>4} ({cnt/n*100:.0f}%)")
    print("=" * W)


def _write_csvs(cur: List[Dict], bth: List[Dict]) -> None:
    def _write(trades: List[Dict], path: str, extra: List[str] = None) -> None:
        if not trades:
            return
        base = ["symbol", "date", "time", "direction", "entry", "stop", "tp1",
                "bars_since", "pct_done", "bucket", "result", "r", "mfe_r", "mae_r", "score"]
        fields = base + (extra or [])
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for row in trades:
                w.writerow({k: row.get(k, "") for k in fields})
        print(f"  Saved -> {path}")

    _write(cur, "birth_proto_current.csv")
    _write(bth, "birth_proto_birth.csv", ["b_any", "bars_after_birth", "b_qual", "zone_fresh"])


# ── Filter analysis ──────────────────────────────────────────────────────────

def _filter_analysis(bth_all: List[Dict]) -> None:
    """Test diagnostic filters on birth trades to isolate what keeps good entries."""
    if not bth_all:
        print("\n  No birth trades to filter.")
        return

    # ── Symbol filter: good symbols have PF >= 1.2 with at least 3 trades ────
    good_syms: set = set()
    for sym in SYMBOLS:
        st = [t for t in bth_all if t["symbol"] == sym]
        if len(st) >= 3:
            m = _metrics(st)
            if m["pf"] >= 1.2:
                good_syms.add(sym)
    gs_str = ", ".join(sorted(good_syms)) if good_syms else "none"

    # ── Named filter functions (avoids lambda-closure pitfalls in loops) ──────
    def f1(t: Dict) -> bool: return t["pct_done"]  <= 50.0
    def f2(t: Dict) -> bool: return t["b_qual"]    >= 0.85
    def f3(t: Dict) -> bool: return t["zone_fresh"] >= 0.80
    def f4(t: Dict) -> bool: return t["symbol"] in good_syms

    filters: List[Tuple[str, Any]] = [
        ("Baseline (none)",    lambda t: True),
        ("F1  pct<=50",        f1),
        ("F2  qual>=0.85",     f2),
        ("F3  fresh>=0.80",    f3),
        ("F4  good_sym",       f4),
        ("F1+F2",              lambda t: f1(t) and f2(t)),
        ("F1+F3",              lambda t: f1(t) and f3(t)),
        ("F2+F3",              lambda t: f2(t) and f3(t)),
        ("F1+F4",              lambda t: f1(t) and f4(t)),
        ("F2+F4",              lambda t: f2(t) and f4(t)),
        ("F1+F2+F3",           lambda t: f1(t) and f2(t) and f3(t)),
        ("F1+F2+F4",           lambda t: f1(t) and f2(t) and f4(t)),
        ("F1+F2+F3+F4",        lambda t: f1(t) and f2(t) and f3(t) and f4(t)),
    ]

    W = 98
    print("\n" + "=" * W)
    print("  FILTER ANALYSIS — Birth Prototype Diagnostic Filters")
    print(f"  F1=pct_done<=50%  F2=b_qual>=0.85  F3=zone_fresh>=0.80"
          f"  F4=good_sym(PF>=1.2, N>=3)")
    print(f"  Good symbols (F4): {gs_str}")
    print("=" * W)

    hdr = "{:<18}  {:>5}  {:>6}  {:>5}  {:>8}  {:>6}  {:>6}  {:>6}  {:>6}  {:>7}"
    print(hdr.format("Filter", "N", "WR%", "PF", "TotalR",
                     "E%", "Mid%", "L%", "VL%", "L+VL%"))
    print("-" * W)

    results: List[Tuple[str, Dict, Any]] = []
    for name, fn in filters:
        subset = [t for t in bth_all if fn(t)]
        m  = _metrics(subset)
        lv = m["late"] + m["vlate"]
        print(hdr.format(
            name, m["n"],
            f"{m['wr']:.1f}", f"{m['pf']:.2f}", f"{m['totalr']:+.1f}R",
            f"{m['early']:.0f}", f"{m['mid']:.0f}",
            f"{m['late']:.0f}", f"{m['vlate']:.0f}", f"{lv:.0f}",
        ))
        results.append((name, m, fn))

    print("=" * W)

    # ── Best combo: highest PF among sets with N >= MIN_N ────────────────────
    MIN_N = 5
    best_name = results[0][0]; best_m = results[0][1]; best_fn = results[0][2]
    for name, m, fn in results[1:]:
        if m["n"] >= MIN_N and m["pf"] > best_m["pf"]:
            best_name = name; best_m = m; best_fn = fn

    best_trades = [t for t in bth_all if best_fn(t)]
    lv_best = best_m["late"] + best_m["vlate"]

    print(f"\n  Best combo (highest PF, N>={MIN_N}): {best_name}")
    print(f"  N={best_m['n']}  WR={best_m['wr']:.1f}%  PF={best_m['pf']:.2f}"
          f"  TotalR={best_m['totalr']:+.2f}R  Late+VL={lv_best:.1f}%")

    # ── Per-symbol for best combo ─────────────────────────────────────────────
    if best_trades:
        print(f"\n  Per-symbol — '{best_name}':")
        h2 = "  {:<7}  {:>5}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}"
        print(h2.format("Symbol", "N", "WR%", "PF", "TotalR", "LV%", "AvgPct"))
        print("  " + "-" * 55)
        for sym in SYMBOLS:
            st = [t for t in best_trades if t["symbol"] == sym]
            if not st:
                print(h2.format(sym, 0, "—", "—", "—", "—", "—"))
                continue
            m = _metrics(st)
            print(h2.format(
                sym, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                f"{m['totalr']:+.1f}R",
                f"{m['late']+m['vlate']:.0f}%",
                f"{m['avg_pct']:.1f}%",
            ))
        print("  " + "=" * 55)

    # ── WR by bucket for best combo ───────────────────────────────────────────
    if len(best_trades) >= MIN_N:
        print(f"\n  WR by bucket — '{best_name}':")
        h3 = "  {:<16}  {:>6}  {:>7}  {:>8}"
        print(h3.format("Bucket", "N", "WR%", "AvgR"))
        print("  " + "-" * 42)
        for bk in ["Early", "Mid", "Late", "VeryLate"]:
            bt = [t for t in best_trades if t["bucket"] == bk]
            bm = _metrics(bt)
            avgr = _safe_avg([t["r"] for t in bt])
            print(h3.format(bk, bm["n"], f"{bm['wr']:.1f}%", f"{avgr:+.2f}R"))
        print("  " + "=" * 42)

    # ── Save filtered CSV for best combo ─────────────────────────────────────
    if best_trades:
        path = "birth_proto_filtered.csv"
        fields = ["symbol", "date", "time", "direction", "entry",
                  "bars_since", "pct_done", "bucket", "b_qual", "zone_fresh",
                  "result", "r", "mfe_r", "mae_r", "score"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for row in best_trades:
                w.writerow({k: row.get(k, "") for k in fields})
        print(f"\n  Filtered CSV saved -> {path}  ({len(best_trades)} rows, filter: {best_name})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 80)
    print("  SMART ICT X2 — Birth-Anchored Entry Prototype")
    print(f"  Symbols      : {', '.join(SYMBOLS)}")
    print(f"  Window       : {DAYS} days")
    print(f"  Birth params : MAX_AGE={MAX_BIRTH_AGE}b | RECENCY={BIRTH_RECENCY}b")
    print(f"  Anchors      : Expansion(0.8 ATR) | Volume(1.3x) | Structure(5-bar)")
    print(f"  Run at       : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80 + "\n")

    cur_all: List[Dict] = []
    bth_all: List[Dict] = []

    for sym in SYMBOLS:
        # Current system
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

        # Birth prototype
        print(f"  [{sym:5}] birth     ... ", end="", flush=True)
        try:
            bt = _run_birth_proto(sym)
        except Exception as e:
            print(f"ERROR: {e}")
            bt = []
        bth_all.extend(bt)
        bn   = len(bt)
        blv  = sum(1 for t in bt if t["bucket"] in ("Late", "VeryLate"))
        bavg = _safe_avg([t["b_any"] for t in bt]) if bt else 0.0
        print(f"{bn:>3} signals | LV={blv} ({blv/bn*100:.0f}%)  AvgBirth={bavg:.1f}b" if bn
              else "0 signals")
        print()

    if not cur_all and not bth_all:
        print("No trades found.")
        return

    _print_comparison(_metrics(cur_all), _metrics(bth_all))
    _print_per_symbol(cur_all, bth_all)
    _print_bucket_detail(cur_all, bth_all)
    _print_birth_detail(bth_all)
    _filter_analysis(bth_all)
    _write_csvs(cur_all, bth_all)

    # Final summary
    n_c  = len(cur_all); lv_c = sum(1 for t in cur_all if t["bucket"] in ("Late","VeryLate"))
    n_b  = len(bth_all); lv_b = sum(1 for t in bth_all if t["bucket"] in ("Late","VeryLate"))
    print(f"\n  {'─'*76}")
    print(f"  Current  : {n_c:>4} signals | Late+VLate = {lv_c:>4} ({lv_c/max(1,n_c)*100:.1f}%)"
          f" | WR = {_metrics(cur_all)['wr']:.1f}%"
          f" | TotalR = {_metrics(cur_all)['totalr']:+.2f}R")
    print(f"  Birth    : {n_b:>4} signals | Late+VLate = {lv_b:>4} ({lv_b/max(1,n_b)*100:.1f}%)"
          f" | WR = {_metrics(bth_all)['wr']:.1f}%"
          f" | TotalR = {_metrics(bth_all)['totalr']:+.2f}R")
    print(f"  {'─'*76}")


if __name__ == "__main__":
    main()
