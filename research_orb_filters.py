# -*- coding: utf-8 -*-
"""
research_orb_filters.py
=======================

ضعه داخل مجلد SMART ثم شغله:

    python research_orb_filters.py

Research only:
- لا يعدل أي ملف.
- لا يرسل أوامر.
- يبني صفقات ORB الحالية من chart_data.
- يختبر فلاتر ORB متعددة ويقارنها مع baseline الحالي.

مهم:
هذا السكربت يستخدم نفس scan_orb_live و _f2_filter و TOP_N_DAY من smart_analyzer_bridge_orb.py.
"""

from __future__ import annotations

import itertools
import math
import os
import traceback
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _time_to_min(t: str) -> int:
    try:
        h, m = str(t).split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _max_drawdown(rs: List[float]) -> float:
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd


def _max_losing_streak(trades: List[Dict]) -> int:
    cur = mx = 0
    for t in trades:
        if t.get("outcome") == "LOSS":
            cur += 1
            mx = max(mx, cur)
        elif t.get("outcome") == "WIN":
            cur = 0
    return mx


def _stats(name: str, trades: List[Dict]) -> Dict:
    n = len(trades)
    w = sum(1 for t in trades if t.get("outcome") == "WIN")
    l = sum(1 for t in trades if t.get("outcome") == "LOSS")
    be = sum(1 for t in trades if t.get("outcome") == "BE")
    rs = [safe_float(t.get("R")) for t in trades]
    wins = sum(r for r in rs if r > 0)
    losses = abs(sum(r for r in rs if r < 0))
    dates = sorted({str(t.get("date", "")) for t in trades if t.get("date")})
    active_days = len(dates)
    return {
        "name": name,
        "N": n,
        "W": w,
        "L": l,
        "BE": be,
        "WR": w / max(1, w + l) * 100,
        "PF": wins / losses if losses > 0 else (999.0 if wins > 0 else 0.0),
        "TotalR": sum(rs),
        "MaxDD": _max_drawdown(rs),
        "MaxLS": _max_losing_streak(trades),
        "Days": active_days,
        "PerDay": n / max(1, active_days),
    }


def _print_stats(rows: List[Dict], limit: int = 30) -> None:
    print(f"{'Scenario':50} {'N':>4} {'W':>3} {'L':>3} {'BE':>3} {'WR%':>7} {'PF':>7} {'TotalR':>8} {'MaxDD':>7} {'MaxLS':>5} {'/Day':>6}")
    print("-" * 116)
    for r in rows[:limit]:
        print(
            f"{r['name'][:50]:50} {r['N']:4d} {r['W']:3d} {r['L']:3d} {r['BE']:3d} "
            f"{r['WR']:7.1f} {r['PF']:7.2f} {r['TotalR']:8.2f} {r['MaxDD']:7.2f} {r['MaxLS']:5d} {r['PerDay']:6.2f}"
        )


def _find_bar_index(bars: List[Any], ts: Any) -> Optional[int]:
    for i, b in enumerate(bars):
        if getattr(b, "timestamp", None) == ts:
            return i
    return None


def _simulate_stock_trade(
    bars: List[Any],
    start_idx: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    max_hold_bars: int = 40,
    win_r: float = 1.8,
) -> Tuple[str, float]:
    direction = direction.upper()
    end = min(len(bars), start_idx + max_hold_bars + 1)
    for b in bars[start_idx + 1:end]:
        if direction in ("LONG", "CALL"):
            if getattr(b, "low") <= stop:
                return "LOSS", -1.0
            if getattr(b, "high") >= tp1:
                return "WIN", win_r
        else:
            if getattr(b, "high") >= stop:
                return "LOSS", -1.0
            if getattr(b, "low") <= tp1:
                return "WIN", win_r
    return "BE", 0.0


def load_orb_baseline() -> List[Dict]:
    import smart_analyzer_bridge_orb as orb
    from analyzer_bc_core import load_symbol_candles, SYMBOLS, CHART_DIR

    c15_map: Dict[str, List[Any]] = {}
    for sym in SYMBOLS:
        try:
            data = load_symbol_candles(sym, CHART_DIR)
            if data and data[0]:
                c15_map[sym] = data[0]
        except Exception:
            pass

    bias_map = orb._build_bias(c15_map) if "SPY" in c15_map and "QQQ" in c15_map else {}

    by_date: Dict[str, List[Dict]] = defaultdict(list)
    bars_by_sym: Dict[str, List[Any]] = {}

    for sym, bars in c15_map.items():
        if sym in orb.ORB_EXCLUDED:
            continue
        bars_by_sym[sym] = bars
        try:
            sigs = orb.scan_orb_live(sym, bars, bias_map)
        except Exception as exc:
            print(f"⚠ ORB scan failed {sym}: {exc}")
            continue
        for s in sigs:
            by_date[str(s.get("date", ""))].append(s)

    selected: List[Dict] = []
    for d, sigs in by_date.items():
        sigs.sort(key=lambda x: -safe_float(x.get("score")))
        selected.extend(orb._f2_filter(sigs[:orb.TOP_N_DAY]))

    selected.sort(key=lambda s: (str(s.get("date", "")), str(s.get("entry_ts", ""))))

    trades: List[Dict] = []
    for s in selected:
        sym = s["symbol"]
        bars = bars_by_sym.get(sym, [])
        idx = _find_bar_index(bars, s.get("entry_ts"))
        if idx is None:
            continue
        out, r = _simulate_stock_trade(
            bars=bars,
            start_idx=idx,
            direction=s["direction"],
            entry=s["entry_price"],
            stop=s["stop_price"],
            tp1=s["tp1"],
            max_hold_bars=40,
            win_r=1.8,
        )
        ts = s.get("entry_ts")
        trades.append({
            "engine": "ORB",
            "symbol": sym,
            "raw_direction": s["direction"],
            "direction": "CALL" if s["direction"] == "LONG" else "PUT",
            "date": str(s.get("date", "")),
            "time": ts.strftime("%H:%M") if hasattr(ts, "strftime") else "",
            "entry": safe_float(s.get("entry_price")),
            "stop": safe_float(s.get("stop_price")),
            "tp1": safe_float(s.get("tp1")),
            "score": safe_float(s.get("score")),
            "adx": safe_float(s.get("adx")),
            "rvol": safe_float(s.get("rvol")),
            "bias": str(s.get("bias", "")),
            "atr": safe_float(s.get("atr")),
            "outcome": out,
            "R": r,
        })

    return trades


def apply_filter(
    trades: List[Dict],
    min_score: float = 0.0,
    allowed_bias: Optional[set] = None,
    excluded_symbols: Optional[set] = None,
    max_time: Optional[str] = None,
    max_per_day: Optional[int] = None,
    stop_after_first_loss: bool = False,
    stop_after_first_win: bool = False,
    long_only: bool = False,
    short_only: bool = False,
) -> List[Dict]:
    excluded_symbols = excluded_symbols or set()
    out: List[Dict] = []
    day_state: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "stopped": False})

    max_min = _time_to_min(max_time) if max_time else None

    for t in sorted(trades, key=lambda x: (x.get("date", ""), x.get("time", ""))):
        d = t.get("date", "")
        st = day_state[d]
        if st["stopped"]:
            continue

        if t.get("symbol") in excluded_symbols:
            continue
        if safe_float(t.get("score")) < min_score:
            continue
        if allowed_bias is not None and t.get("bias") not in allowed_bias:
            continue
        if max_min is not None and _time_to_min(t.get("time", "")) > max_min:
            continue
        if max_per_day is not None and st["count"] >= max_per_day:
            continue
        if long_only and t.get("direction") != "CALL":
            continue
        if short_only and t.get("direction") != "PUT":
            continue

        out.append(t)
        st["count"] += 1

        if stop_after_first_loss and t.get("outcome") == "LOSS":
            st["stopped"] = True
        if stop_after_first_win and t.get("outcome") == "WIN":
            st["stopped"] = True

    return out


def main() -> None:
    print("=" * 118)
    print("ORB FILTER RESEARCH — Research only / No code changes / No orders")
    print("=" * 118)
    print("CWD:", os.getcwd())

    try:
        trades = load_orb_baseline()
    except Exception:
        print("❌ Failed to load ORB baseline")
        traceback.print_exc()
        return

    if not trades:
        print("No ORB trades found. Check chart_data.")
        return

    baseline = _stats("BASELINE current ORB", trades)

    print("\nBASELINE")
    _print_stats([baseline], limit=1)

    print("\nBASELINE TRADES")
    print(f"{'Date':10} {'Time':5} {'Sym':6} {'Dir':5} {'Bias':8} {'Score':>7} {'ADX':>6} {'RVOL':>6} {'Out':>5} {'R':>5}")
    print("-" * 86)
    for t in trades:
        print(
            f"{t['date']:10} {t['time']:5} {t['symbol']:6} {t['direction']:5} {t['bias']:8} "
            f"{t['score']:7.1f} {t['adx']:6.1f} {t['rvol']:6.2f} {t['outcome']:>5} {t['R']:5.1f}"
        )

    # ── simple single-factor tests ──────────────────────────────────────────
    scenarios: List[Tuple[str, List[Dict]]] = []
    scenarios.append(("BASELINE current ORB", trades))

    for sc in [50, 60, 65, 70, 75, 80, 90, 100, 120]:
        scenarios.append((f"score>={sc}", apply_filter(trades, min_score=sc)))

    scenarios.append(("bias only BULL/BEAR (no NEUTRAL)", apply_filter(trades, allowed_bias={"BULL", "BEAR"})))
    scenarios.append(("bias only NEUTRAL", apply_filter(trades, allowed_bias={"NEUTRAL"})))

    for tm in ["14:15", "14:30", "14:45", "15:00", "15:15"]:
        scenarios.append((f"time<={tm}", apply_filter(trades, max_time=tm)))

    for n in [1, 2]:
        scenarios.append((f"max {n} ORB trade/day", apply_filter(trades, max_per_day=n)))

    scenarios.append(("stop day after first loss", apply_filter(trades, stop_after_first_loss=True)))
    scenarios.append(("stop day after first win", apply_filter(trades, stop_after_first_win=True)))
    scenarios.append(("CALL only", apply_filter(trades, long_only=True)))
    scenarios.append(("PUT only", apply_filter(trades, short_only=True)))

    symbols = sorted({t["symbol"] for t in trades})
    for sym in symbols:
        scenarios.append((f"exclude {sym}", apply_filter(trades, excluded_symbols={sym})))

    single_rows = [_stats(name, tr) for name, tr in scenarios]
    print("\nSINGLE FILTER TESTS")
    _print_stats(single_rows, limit=200)

    # ── combo search ────────────────────────────────────────────────────────
    score_grid = [0, 60, 65, 70, 75, 80, 90, 100]
    bias_grid = [
        None,
        {"BULL", "BEAR"},
        {"NEUTRAL"},
    ]
    time_grid = [None, "14:30", "14:45", "15:00", "15:15"]
    max_day_grid = [None, 1, 2, 3]
    stop_loss_grid = [False, True]
    stop_win_grid = [False, True]

    # Exclude sets: none + single symbol + likely weak combos
    exclude_grid = [set()]
    exclude_grid += [{s} for s in symbols]
    likely = ["LLY", "CRM", "AMZN", "MSFT", "META"]
    for r in [2, 3]:
        for combo in itertools.combinations([s for s in likely if s in symbols], r):
            exclude_grid.append(set(combo))

    combo_rows: List[Dict] = []
    for min_score, allowed_bias, max_time, max_per_day, stop_loss, stop_win, excluded in itertools.product(
        score_grid, bias_grid, time_grid, max_day_grid, stop_loss_grid, stop_win_grid, exclude_grid
    ):
        tr = apply_filter(
            trades,
            min_score=min_score,
            allowed_bias=allowed_bias,
            excluded_symbols=excluded,
            max_time=max_time,
            max_per_day=max_per_day,
            stop_after_first_loss=stop_loss,
            stop_after_first_win=stop_win,
        )
        st = _stats(
            f"score>={min_score}|bias={('ALL' if allowed_bias is None else '+'.join(sorted(allowed_bias)))}|"
            f"time<={max_time or 'ALL'}|maxDay={max_per_day or 'ALL'}|"
            f"stopL={int(stop_loss)}|stopW={int(stop_win)}|ex={','.join(sorted(excluded)) or '-'}",
            tr
        )
        # لا نعتبر سيناريوهات قليلة جداً كأفضلية إلا في جدول خاص
        combo_rows.append(st)

    # ترتيبين:
    # 1) أفضل متوازن: على الأقل 8 صفقات، TotalR أعلى، PF أعلى، DD أقل
    balanced = [r for r in combo_rows if r["N"] >= max(6, int(baseline["N"] * 0.50))]
    balanced.sort(key=lambda r: (r["TotalR"], r["PF"], r["WR"], -r["MaxDD"], r["N"]), reverse=True)

    # 2) أفضل حماية: PF/WR مع N مقبول
    protective = [r for r in combo_rows if r["N"] >= max(5, int(baseline["N"] * 0.35))]
    protective.sort(key=lambda r: (r["PF"], r["TotalR"], r["WR"], -r["MaxDD"], r["N"]), reverse=True)

    print("\nTOP COMBO — BALANCED (keeps at least ~50% of trades)")
    _print_stats(balanced, limit=25)

    print("\nTOP COMBO — PROTECTIVE (prioritize PF, keeps at least ~35% of trades)")
    _print_stats(protective, limit=25)

    # ── Symbol breakdown ────────────────────────────────────────────────────
    print("\nSYMBOL BREAKDOWN")
    sym_rows = []
    for sym in symbols:
        sym_rows.append(_stats(sym, [t for t in trades if t["symbol"] == sym]))
    sym_rows.sort(key=lambda r: (r["TotalR"], r["PF"], r["WR"]), reverse=True)
    _print_stats(sym_rows, limit=100)

    # ── Recommendation ──────────────────────────────────────────────────────
    print("\n" + "=" * 118)
    print("RECOMMENDATION")
    print("=" * 118)

    best = balanced[0] if balanced else baseline
    print("Baseline:")
    _print_stats([baseline], limit=1)
    print("\nBest balanced candidate:")
    _print_stats([best], limit=1)

    print("\nRead:")
    if best["TotalR"] <= baseline["TotalR"] and best["PF"] <= baseline["PF"]:
        print("لا يوجد فلتر مركب واضح يحسن TotalR و PF معاً. لا تعدل ORB الآن.")
    else:
        print("يوجد فلتر مرشح. لا تطبقه مباشرة قبل مراجعة جدول الصفقات والتأكد أنه لا يعتمد على عينة صغيرة.")
        print("اسم السيناريو المرشح:")
        print(best["name"])


if __name__ == "__main__":
    main()
