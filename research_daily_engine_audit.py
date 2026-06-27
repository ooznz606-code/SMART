# -*- coding: utf-8 -*-
"""
research_daily_engine_audit.py

ضع الملف داخل مجلد SMART ثم شغله:
    python research_daily_engine_audit.py
أو:
    python research_daily_engine_audit.py 10

Research only:
- لا يغير أي ملف
- لا يرسل أوامر
- يفحص B+C و ORB
- يختبر بوابة B+C الحالية مقابل تخفيض score إلى 72 و70
"""

from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import sys

try:
    from analyzer_bc_core import (
        CHART_DIR, SYMBOLS, TOP_N_DAILY, SIM_BARS,
        load_symbol_candles, scan_symbol, select_daily, ATR_THRESHOLD
    )
except Exception as e:
    raise SystemExit(f"cannot import analyzer_bc_core: {e}")

try:
    from smart_analyzer_bridge_bc import _enrich, passes_exec_gate
except Exception as e:
    raise SystemExit(f"cannot import smart_analyzer_bridge_bc: {e}")

try:
    import smart_analyzer_bridge_orb as orb
except Exception as e:
    orb = None
    print(f"WARNING: cannot import ORB module: {e}")

WIN_R, LOSS_R, BE_R = 1.8, -1.0, 0.0


def sep(title="", ch="=", w=110):
    print("\n" + ch * w)
    if title:
        print(title)
        print(ch * w)


def find_idx(c15, ts):
    for i, c in enumerate(c15):
        if c.timestamp == ts:
            return i
    return None


def simulate(c15, sig, max_hold=40):
    ts = sig.get("entry_ts") or sig.get("birth_ts")
    if not isinstance(ts, datetime):
        return "BE", BE_R
    idx = find_idx(c15, ts)
    if idx is None:
        return "BE", BE_R

    direction = str(sig.get("direction", "")).upper()
    entry = float(sig.get("entry_price", 0) or 0)
    stop = float(sig.get("stop_price", 0) or sig.get("sl", 0) or 0)
    tp1 = float(sig.get("tp1", 0) or sig.get("target", 0) or 0)

    if (stop <= 0 or tp1 <= 0) and entry > 0:
        atr = float(sig.get("atr", 0) or 0)
        if atr > 0:
            if direction == "LONG":
                stop, tp1 = entry - 1.5 * atr, entry + 2.7 * atr
            else:
                stop, tp1 = entry + 1.5 * atr, entry - 2.7 * atr

    if stop <= 0 or tp1 <= 0:
        return "BE", BE_R

    for bar in c15[idx + 1: idx + 1 + max_hold]:
        if direction == "LONG":
            if bar.low <= stop:
                return "LOSS", LOSS_R
            if bar.high >= tp1:
                return "WIN", WIN_R
        elif direction == "SHORT":
            if bar.high >= stop:
                return "LOSS", LOSS_R
            if bar.low <= tp1:
                return "WIN", WIN_R
    return "BE", BE_R


def metrics(trades):
    n = len(trades)
    w = sum(1 for t in trades if t["outcome"] == "WIN")
    l = sum(1 for t in trades if t["outcome"] == "LOSS")
    be = sum(1 for t in trades if t["outcome"] == "BE")
    total_r = sum(float(t.get("R", 0)) for t in trades)
    wr = (w / max(1, w + l)) * 100 if (w + l) else 0.0
    gw = sum(t["R"] for t in trades if t["R"] > 0)
    gl = abs(sum(t["R"] for t in trades if t["R"] < 0))
    pf = gw / gl if gl > 0 else (999.0 if gw > 0 else 0.0)

    eq = peak = maxdd = 0.0
    maxls = cur_ls = 0
    for t in trades:
        r = float(t.get("R", 0))
        eq += r
        peak = max(peak, eq)
        maxdd = max(maxdd, peak - eq)
        if r < 0:
            cur_ls += 1
            maxls = max(maxls, cur_ls)
        elif r > 0:
            cur_ls = 0

    return dict(N=n, W=w, L=l, BE=be, WR=wr, PF=pf, TotalR=total_r, MaxDD=maxdd, MaxLS=maxls)


def print_table(title, rows):
    sep(title, "-")
    print(f"{'Scenario':32} {'N':>4} {'W':>4} {'L':>4} {'BE':>4} {'WR%':>8} {'PF':>8} {'TotalR':>9} {'MaxDD':>8} {'MaxLS':>6}")
    print("-" * 110)
    for name, m in rows:
        pf = "inf" if m["PF"] >= 999 else f"{m['PF']:.2f}"
        print(f"{name:32} {m['N']:4d} {m['W']:4d} {m['L']:4d} {m['BE']:4d} {m['WR']:7.1f}% {pf:>8} {m['TotalR']:+9.2f} {m['MaxDD']:8.2f} {m['MaxLS']:6d}")


def cutoff_date(c15_map, days):
    ds = sorted({str(b.timestamp.date()) for bars in c15_map.values() for b in bars})
    if not ds:
        return "0000-00-00"
    return ds[-days] if len(ds) >= days else ds[0]


def load_all():
    c15_map, c1h_map = {}, {}
    for sym in SYMBOLS:
        try:
            data = load_symbol_candles(sym, CHART_DIR, lookback_days=None)
        except TypeError:
            data = load_symbol_candles(sym, CHART_DIR)
        except Exception as e:
            print(f"WARNING load {sym}: {e}")
            continue
        if not data:
            continue
        c15, c1h = data
        c15_map[sym] = c15
        c1h_map[sym] = c1h
    return c15_map, c1h_map


def bc_gate_reason(sig, score_min=75.0, adx_min=40.0, rvi_allowed=("High",)):
    if sig.get("symbol") == "AAPL":
        return "blocked_AAPL"
    if float(sig.get("rank_score", 0) or 0) < score_min:
        return f"score<{score_min}"
    if sig.get("rvi_bucket") not in set(rvi_allowed):
        return "rvi"
    if sig.get("regime") != "Trend":
        return "regime"
    if float(sig.get("adx", 0) or 0) < adx_min:
        return f"adx<{adx_min}"
    if int(sig.get("entry_offset", 999) or 999) > 4:
        return "offset>4"
    if float(sig.get("atr_pct", 999) or 999) > ATR_THRESHOLD:
        return f"atr>{ATR_THRESHOLD}"
    if int(sig.get("session_min", 999) or 999) >= 525:
        return "after_14:15"
    return "PASS"


VARIANTS = [
    ("PROJECT_CURRENT_GATE", None),
    ("TEST_score72_adx40_high", dict(score_min=72.0, adx_min=40.0, rvi=("High",))),
    ("TEST_score70_adx40_high", dict(score_min=70.0, adx_min=40.0, rvi=("High",))),
    ("TEST_score72_adx35_high", dict(score_min=72.0, adx_min=35.0, rvi=("High",))),
    ("TEST_score70_adx35_high", dict(score_min=70.0, adx_min=35.0, rvi=("High",))),
    ("TEST_score72_adx40_high_med", dict(score_min=72.0, adx_min=40.0, rvi=("High", "Medium"))),
]


def scan_bc(c15_map, c1h_map, cutoff):
    all_sigs = []
    for sym, c15 in c15_map.items():
        try:
            sigs = scan_symbol(sym, c15, c1h_map.get(sym, []))
            _enrich(sigs, sym, c15)
            all_sigs.extend(sigs)
        except Exception as e:
            print(f"WARNING BC scan {sym}: {e}")
    all_sigs = [s for s in all_sigs if str(s.get("date", "")) >= cutoff]
    selected = select_daily(all_sigs, TOP_N_DAILY)
    return all_sigs, selected


def apply_bc_variant(selected, c15_map, cfg):
    out = []
    for sig in selected:
        if cfg is None:
            try:
                ok, _ = passes_exec_gate(sig)
            except Exception:
                ok = False
        else:
            ok = bc_gate_reason(sig, cfg["score_min"], cfg["adx_min"], cfg["rvi"]) == "PASS"
        if not ok:
            continue
        c15 = c15_map.get(sig["symbol"])
        if not c15:
            continue
        outcome, r = simulate(c15, sig, SIM_BARS)
        out.append({**sig, "outcome": outcome, "R": r})
    return out


def print_added(base, new, title):
    base_keys = {(t["symbol"], t["direction"], t["date"], t.get("birth_time", ""), t.get("entry_time", "")) for t in base}
    added = [t for t in new if (t["symbol"], t["direction"], t["date"], t.get("birth_time", ""), t.get("entry_time", "")) not in base_keys]
    sep(title, "-")
    if not added:
        print("No added trades.")
        return
    print(f"{'Date':10} {'Time':5} {'Sym':5} {'Dir':6} {'Score':>6} {'ADX':>6} {'RVI':>6} {'Bucket':>7} {'Outcome':>7} {'R':>6}")
    print("-" * 88)
    for t in added[-60:]:
        print(f"{t.get('date',''):10} {t.get('birth_time',''):5} {t.get('symbol',''):5} {t.get('direction',''):6} "
              f"{float(t.get('rank_score',0)):6.1f} {float(t.get('adx',0)):6.1f} {float(t.get('rvi',0)):6.3f} "
              f"{t.get('rvi_bucket',''):>7} {t.get('outcome',''):>7} {float(t.get('R',0)):+6.2f}")


def orb_audit(c15_map, cutoff):
    sep("ORB AUDIT", "=")
    if orb is None:
        print("ORB module not available.")
        return []

    print(f"Rules: ADX>={orb.ORB_ADX_MIN}, RVOL>={orb.ORB_RVOL_MIN}, ORB range>={orb.ORB_RANGE_ATR_MIN}ATR, EMA20dist>={orb.ORB_EMA20_DIST_MIN}ATR, break>={orb.ORB_BREAK_DIST_MIN}ATR")
    scan_syms = [s for s in SYMBOLS if s not in orb.ORB_EXCLUDED]
    print("Symbols:", scan_syms)

    try:
        bias_map = orb._build_bias(c15_map)
    except Exception as e:
        print(f"ORB bias error: {e}")
        return []

    all_orb = []
    for sym in scan_syms:
        bars = c15_map.get(sym)
        if not bars:
            continue
        try:
            sigs = orb.scan_orb_live(sym, bars, bias_map)
            for s in sigs:
                if str(s.get("date", "")) < cutoff:
                    continue
                outcome, r = simulate(bars, s, getattr(orb, "MAX_HOLD", 40))
                all_orb.append({**s, "outcome": outcome, "R": r})
        except Exception as e:
            print(f"WARNING ORB {sym}: {e}")

    by_date = defaultdict(list)
    for s in all_orb:
        by_date[s["date"]].append(s)

    selected = []
    for d in sorted(by_date):
        ranked = sorted(by_date[d], key=lambda x: -float(x.get("score", 0)))
        top = ranked[:orb.TOP_N_DAY]
        if hasattr(orb, "_f2_filter"):
            top = orb._f2_filter(top)
        selected.extend(top)

    print_table("ORB selected results", [("ORB_CURRENT", metrics(selected))])
    if selected:
        print(f"{'Date':10} {'Time':5} {'Sym':5} {'Dir':6} {'Score':>8} {'ADX':>6} {'RVOL':>6} {'Bias':>8} {'Outcome':>7} {'R':>6}")
        print("-" * 94)
        for t in selected[-50:]:
            ts = t.get("entry_ts")
            tm = ts.strftime("%H:%M") if isinstance(ts, datetime) else ""
            print(f"{t.get('date',''):10} {tm:5} {t.get('symbol',''):5} {t.get('direction',''):6} "
                  f"{float(t.get('score',0)):8.1f} {float(t.get('adx',0)):6.1f} {float(t.get('rvol',0)):6.2f} "
                  f"{t.get('bias',''):>8} {t.get('outcome',''):>7} {float(t.get('R',0)):+6.2f}")
    else:
        print("No ORB trades in window.")
    return selected


def main():
    days = 10
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except Exception:
            pass

    sep("DAILY ENGINE AUDIT — B+C + ORB")
    print("Research only. No files modified. No orders sent.")
    print("CHART_DIR:", CHART_DIR)

    c15_map, c1h_map = load_all()
    print(f"Loaded symbols: {len(c15_map)}/{len(SYMBOLS)}")
    cutoff = cutoff_date(c15_map, days)
    print(f"Window cutoff: {cutoff}")

    sep("B+C AUDIT", "=")
    raw, selected = scan_bc(c15_map, c1h_map, cutoff)
    print(f"Raw B+C signals: {len(raw)}")
    print(f"Selected Top-{TOP_N_DAILY}/day: {len(selected)}")

    trades_by_variant = {}
    rows = []
    for name, cfg in VARIANTS:
        tr = apply_bc_variant(selected, c15_map, cfg)
        trades_by_variant[name] = tr
        rows.append((name, metrics(tr)))

    print_table("B+C gate variants", rows)

    sep("B+C current gate rejection reasons", "-")
    reasons = Counter()
    for s in selected:
        reasons[bc_gate_reason(s)] += 1
    for k, v in reasons.most_common():
        print(f"{k:18}: {v}")

    print_added(trades_by_variant["PROJECT_CURRENT_GATE"], trades_by_variant["TEST_score72_adx40_high"], "Added by score >=72")
    print_added(trades_by_variant["PROJECT_CURRENT_GATE"], trades_by_variant["TEST_score70_adx40_high"], "Added by score >=70")

    sep("B+C daily activity", "-")
    for name in ["PROJECT_CURRENT_GATE", "TEST_score72_adx40_high", "TEST_score70_adx40_high", "TEST_score72_adx35_high"]:
        print("\n" + name)
        by_day = Counter(t["date"] for t in trades_by_variant.get(name, []))
        if not by_day:
            print("  no live-eligible trades")
        for d in sorted(by_day):
            print(f"  {d}: {by_day[d]}")

    orb_selected = orb_audit(c15_map, cutoff)

    sep("RECOMMENDATION", "=")
    cur = metrics(trades_by_variant["PROJECT_CURRENT_GATE"])
    s72 = metrics(trades_by_variant["TEST_score72_adx40_high"])
    s70 = metrics(trades_by_variant["TEST_score70_adx40_high"])

    print(f"Current : N={cur['N']} WR={cur['WR']:.1f}% PF={cur['PF']:.2f} TotalR={cur['TotalR']:+.2f}")
    print(f"Score72 : N={s72['N']} WR={s72['WR']:.1f}% PF={s72['PF']:.2f} TotalR={s72['TotalR']:+.2f}")
    print(f"Score70 : N={s70['N']} WR={s70['WR']:.1f}% PF={s70['PF']:.2f} TotalR={s70['TotalR']:+.2f}")

    if s72["N"] > cur["N"] and s72["PF"] >= 1.6 and s72["TotalR"] >= cur["TotalR"]:
        print("\n✅ Candidate: research lowering B+C EXEC_RANK_MIN from 75 to 72.")
        print("Do not change live code until you inspect added trades above.")
    else:
        print("\n⚠️ Do not lower the gate yet. Score72 did not clearly improve risk-adjusted results.")

    print("\nNote: ORB should stay unchanged unless ORB audit shows good added trades under a researched variant.")


if __name__ == "__main__":
    main()
