# -*- coding: utf-8 -*-
"""
optimize_smart.py
بحث شبكي لكل رمز بشكل مستقل — هدف: 80-85% أيام رابحة
يوم رابح = يوم فيه صفقة واحدة على الأقل وصلت TP
"""
import warnings, json, itertools
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

from analyzer_x2 import (
    Candle, Direction, Zone,
    _atr, _adx_calc, _ema,
    _detect_sweep, _detect_displacement,
    _detect_fvg, _detect_order_block,
)

# ══════════════════════════════════════════════════════════════════
DAYS     = 55
TP1_R    = 1.2
MAX_WAIT = 35    # شموع انتظار الريتيست
MAX_HOLD = 30    # شموع الاحتفاظ قبل BE

GRID = {
    "tf":        ["15m", "1H"],
    "min_adx":   [15, 18, 22, 25],
    "max_adx":   [45, 55, 65, 80],
    "min_score": [40, 50, 60, 70],
}
# ══════════════════════════════════════════════════════════════════


def _flat(df):
    if df is not None and isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _to_candles(df) -> List[Candle]:
    out = []
    for ts, row in df.iterrows():
        dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.utcnow()
        out.append(Candle(
            open=float(row["Open"]),  high=float(row["High"]),
            low=float(row["Low"]),   close=float(row["Close"]),
            volume=float(row.get("Volume", 0)), timestamp=dt,
        ))
    return out


def _htf_direction(c1h: List[Candle]) -> Direction:
    """اتجاه HTF بناءً على EMA21 vs EMA50."""
    if not c1h or len(c1h) < 55:
        return Direction.NEUTRAL
    closes = [c.close for c in c1h]
    e21 = _ema(closes, 21)
    e50 = _ema(closes, 50)
    price = closes[-1]
    if price > e21 > e50:
        return Direction.LONG
    if price < e21 < e50:
        return Direction.SHORT
    return Direction.NEUTRAL


def _score(sweep, disp, fvg, ob, adx, min_adx, max_adx, htf: Direction) -> float:
    s = 0.0
    if sweep: s += 28.0
    if disp:  s += 22.0
    if fvg:   s += 22.0
    if ob:    s += 13.0
    if htf != Direction.NEUTRAL: s += 15.0
    if min_adx <= adx <= max_adx: s += 5.0
    return min(s, 100.0)


def _find_zone(candles: List[Candle], direction: Direction, atr: float, price: float) -> Optional[Tuple[float, float]]:
    """ابحث عن FVG أو OB في جانب السعر الصحيح."""
    fvg = _detect_fvg(candles, direction, atr)
    ob  = _detect_order_block(candles, direction, atr)

    for z in ([fvg, ob] if direction == Direction.LONG else [ob, fvg]):
        if z is None:
            continue
        zt, zb = z.top, z.bottom
        if direction == Direction.LONG  and price > zt:
            return zt, zb
        if direction == Direction.SHORT and price < zb:
            return zt, zb
    return None


def _run(c15_all: List[Candle], c1h_all: List[Candle],
         tf: str, min_adx: float, max_adx: float, min_score: float
         ) -> Dict[date, List[str]]:

    day_results: Dict[date, List[str]] = defaultdict(list)
    WARMUP = 150
    pending = None
    wait_bars = 0
    last_trade_bar = -99

    for i in range(WARMUP, len(c15_all) - MAX_HOLD - 1):
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts]

        candles = (c15_all[:i+1][-200:] if tf == "15m"
                   else (c1h[-300:] if len(c1h) >= 55 else c15_all[:i+1][-200:]))
        if len(candles) < 60:
            continue

        atr   = _atr(candles, 14)
        adx   = _adx_calc(candles, 14)
        price = c15_all[i].close
        htf   = _htf_direction(c1h[-300:] if len(c1h) >= 55 else [])

        # ADX فلتر
        if adx < min_adx or adx > max_adx:
            if pending:
                wait_bars += 1
                if wait_bars > MAX_WAIT:
                    pending = None; wait_bars = 0
            continue

        # ── حالة انتظار الريتيست ──────────────────────────────────
        if pending is not None:
            wait_bars += 1
            z_top     = pending["z_top"]
            z_bot     = pending["z_bot"]
            direction = pending["direction"]

            # إلغاء إذا انعكس الترند
            if ((direction == Direction.LONG  and htf == Direction.SHORT) or
                    (direction == Direction.SHORT and htf == Direction.LONG)):
                pending = None; wait_bars = 0; continue

            bar = c15_all[i]
            if bar.low <= z_top and bar.high >= z_bot:
                # شمعة ريتيست
                if direction == Direction.LONG:
                    rej = bar.close > bar.open
                else:
                    rej = bar.close < bar.open

                if rej and (i - last_trade_bar) >= 12:
                    entry = bar.close
                    stop  = (z_bot - atr * 0.22 if direction == Direction.LONG
                             else z_top + atr * 0.22)
                    risk  = abs(entry - stop)
                    if risk < atr * 0.20 or risk <= 0:
                        pending = None; wait_bars = 0; continue
                    if direction == Direction.LONG  and stop >= entry:
                        pending = None; wait_bars = 0; continue
                    if direction == Direction.SHORT and stop <= entry:
                        pending = None; wait_bars = 0; continue

                    tp1 = (entry + risk * TP1_R if direction == Direction.LONG
                           else entry - risk * TP1_R)

                    outcome = "BE"
                    for fc in c15_all[i+1: i+1+MAX_HOLD+1]:
                        if direction == Direction.LONG:
                            if fc.low  <= stop: outcome = "LOSS"; break
                            if fc.high >= tp1:  outcome = "WIN";  break
                        else:
                            if fc.high >= stop: outcome = "LOSS"; break
                            if fc.low  <= tp1:  outcome = "WIN";  break

                    trade_date = bar.timestamp.date()
                    day_results[trade_date].append(outcome)
                    last_trade_bar = i
                    pending = None; wait_bars = 0
                    continue

            if wait_bars > MAX_WAIT:
                pending = None; wait_bars = 0
            continue

        # ── بحث عن إعداد جديد ────────────────────────────────────
        if (i - last_trade_bar) < 10:
            continue
        if htf == Direction.NEUTRAL:
            continue

        direction = htf  # LONG أو SHORT

        sweep = _detect_sweep(candles, direction, atr)
        disp  = _detect_displacement(candles, direction, atr)
        fvg   = _detect_fvg(candles, direction, atr)
        ob    = _detect_order_block(candles, direction, atr)

        if not ((sweep or disp) and (fvg or ob)):
            continue

        sc = _score(sweep, disp, fvg, ob, adx, min_adx, max_adx, htf)
        if sc < min_score:
            continue

        zone = _find_zone(candles, direction, atr, price)
        if zone is None:
            continue

        z_top, z_bot = zone
        pending   = {"direction": direction, "z_top": z_top, "z_bot": z_bot}
        wait_bars = 0

    return day_results


def _stats(day_results: Dict[date, List[str]]):
    if not day_results:
        return 0.0, 0, 0, 0.0
    days    = sorted(day_results.keys())
    winning = sum(1 for d in days if "WIN" in day_results[d])
    total_t = sum(len(v) for v in day_results.values())
    pct     = round(winning / len(days) * 100, 1)
    tpd     = round(total_t / len(days), 2)
    return pct, winning, len(days), tpd


def _combined_stats(all_dr: List[Dict[date, List[str]]]):
    merged: Dict[date, List[str]] = defaultdict(list)
    for dr in all_dr:
        for d, outcomes in dr.items():
            merged[d].extend(outcomes)
    return _stats(merged)


# ══════════════════════════════════════════════════════════════════
def main():
    with open("symbol_profiles_x2.json", encoding="utf-8") as f:
        profiles = json.load(f)

    SYMBOLS = [k for k in profiles if k != "__DEFAULT__" and profiles[k].get("enabled", True)]

    end   = datetime.today()
    start = end - timedelta(days=DAYS)

    print(f"\n{'='*70}")
    print(f"  SMART Optimizer — {DAYS} days | هدف: 80-85% أيام رابحة")
    print(f"  {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
    print(f"{'='*70}\n  تحميل البيانات...")

    data = {}
    for sym in SYMBOLS:
        try:
            df15 = _flat(yf.download(sym, start=start, end=end, interval="15m",
                                     progress=False, auto_adjust=True))
            df1h = _flat(yf.download(sym, start=start, end=end, interval="1h",
                                     progress=False, auto_adjust=True))
            if df15 is not None and len(df15) >= 120:
                data[sym] = {
                    "c15": _to_candles(df15),
                    "c1h": _to_candles(df1h) if df1h is not None and len(df1h) > 20 else [],
                }
                print(f"    {sym}: {len(df15)}x15m | {len(df1h) if df1h is not None else 0}x1h")
        except Exception as e:
            print(f"    {sym}: ERROR {e}")

    keys   = list(GRID.keys())
    combos = list(itertools.product(*[GRID[k] for k in keys]))
    print(f"\n  شبكة البحث: {len(combos)} مجموعة x {len(data)} رمز\n  يعمل...\n")

    best_per_sym = {}
    new_profiles = dict(profiles)

    for sym in SYMBOLS:
        if sym not in data:
            print(f"  {sym}: لا بيانات")
            continue

        best = {"score": -1, "pct": 0.0, "tpd": 0.0, "params": None, "dr": {}}

        for combo in combos:
            p  = dict(zip(keys, combo))
            tf, mna, mxa, msc = p["tf"], p["min_adx"], p["max_adx"], p["min_score"]
            if mna >= mxa:
                continue
            dr  = _run(data[sym]["c15"], data[sym]["c1h"], tf, mna, mxa, msc)
            pct, win_d, tot_d, tpd = _stats(dr)
            if tpd < 0.25:          # أقل من صفقة كل 4 أيام = لا يُعتد به
                continue
            sc = pct * 100 + tpd   # أولوية للأيام الرابحة ثم الكثافة
            if sc > best["score"]:
                all_out = [o for v in dr.values() for o in v]
                w = all_out.count("WIN"); l = all_out.count("LOSS")
                best = {
                    "score": sc, "pct": pct, "tpd": tpd,
                    "params": p, "dr": dr,
                    "win_d": win_d, "tot_d": tot_d,
                    "wins": w, "losses": l, "be": all_out.count("BE"),
                }

        best_per_sym[sym] = best
        p = best["params"]
        if p:
            wr = round(best["wins"]/(best["wins"]+best["losses"])*100, 1) if (best["wins"]+best["losses"]) > 0 else 0.0
            print(f"  {sym:<6}: {best['pct']:5.1f}% wd | {best['win_d']}/{best['tot_d']} أيام "
                  f"| {best['tpd']:.2f} t/d | WR={wr:.0f}% "
                  f"({best['wins']}W/{best['losses']}L/{best['be']}BE) "
                  f"| tf={p['tf']} adx={p['min_adx']}-{p['max_adx']} conf={p['min_score']}")
            all_out = [o for v in best["dr"].values() for o in v]
            w = all_out.count("WIN"); l = all_out.count("LOSS")
            wr2 = round(w/(w+l)*100, 1) if (w+l) > 0 else 0.0
            new_profiles[sym] = {
                "enabled":          True,
                "timeframe":        p["tf"],
                "min_conf":         p["min_score"],
                "min_adx":          p["min_adx"],
                "max_adx":          p["max_adx"],
                "max_daily_signals": 1,
                "notes": (f"{p['tf']}: {w}W {l}L {wr2:.1f}% WR | "
                          f"{best['pct']:.1f}% wd ({best['win_d']}/{best['tot_d']} days)"),
            }
        else:
            print(f"  {sym:<6}: لا إعداد مناسب — يبقى مفعّلاً بمعاملاته الحالية")

    # ── النتيجة المجمّعة ─────────────────────────────────────────
    all_dr = [v["dr"] for v in best_per_sym.values() if v.get("dr")]
    cpct, cwin, ctot, ctpd = _combined_stats(all_dr)

    print(f"\n{'='*70}")
    print(f"  الملخص النهائي:")
    print(f"  {'رمز':<7} {'%wd':>6} {'W/T':>6} {'t/d':>5} {'WR%':>5} {'TF':<5} {'ADX':>8} {'conf':>5}")
    print(f"  {'-'*65}")
    for sym in SYMBOLS:
        b = best_per_sym.get(sym, {})
        if not b.get("params"):
            print(f"  {sym:<7}   ---")
            continue
        p   = b["params"]
        out = [o for v in b["dr"].values() for o in v]
        w   = out.count("WIN"); l = out.count("LOSS")
        wr  = round(w/(w+l)*100, 1) if (w+l) > 0 else 0.0
        print(f"  {sym:<7} {b['pct']:>5.1f}% {b['win_d']}/{b['tot_d']} "
              f"{b['tpd']:>5.2f} {wr:>4.0f}% {p['tf']:<5} "
              f"{p['min_adx']}-{p['max_adx']:>2} {p['min_score']:>5}")

    print(f"  {'-'*65}")
    print(f"  المجمّع: {cpct:.1f}% أيام رابحة | {cwin}/{ctot} يوم | {ctpd:.2f} صفقة/يوم")

    # حفظ
    new_profiles["__DEFAULT__"] = profiles.get("__DEFAULT__", {
        "enabled": False, "timeframe": "1H",
        "min_conf": 999, "min_adx": 99, "max_adx": 999, "max_daily_signals": 0
    })
    with open("symbol_profiles_x2.json", "w", encoding="utf-8") as f:
        json.dump(new_profiles, f, indent=2, ensure_ascii=False)

    print(f"\n  الإعدادات حُفظت في symbol_profiles_x2.json")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
