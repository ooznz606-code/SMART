# -*- coding: utf-8 -*-
"""
optimize_per_symbol.py
الهدف: ضبط مستقل لكل رمز للوصول الى >90% ايام رابحة
كل رمز يأخذ افضل min_conf و min_adx و max_adx له بشكل مستقل
"""
import sys, warnings, itertools, json
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

warnings.filterwarnings("ignore")
try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("pip install yfinance pandas"); sys.exit(1)

from analyzer_x2 import (
    Candle, _atr, _adx_calc, _ema,
    _detect_liquidity_sweep, _detect_displacement,
    _detect_fvg, _detect_order_block, _get_1h_trend
)

SYMBOLS  = ["QQQ","SPY","NVDA","MSFT","META","AAPL","AMD",
            "AMZN","TSLA","NFLX","GOOGL","LLY"]
DAYS     = 55
TP1_R    = 2.0
MAX_WAIT = 30
MAX_HOLD = 22

# شبكة البحث لكل رمز بشكل مستقل
PER_SYMBOL_GRID = {
    "min_adx":   [15, 18, 22, 25],
    "max_adx":   [45, 55, 70, 999],
    "min_score": [45, 50, 55, 60, 65],
    "tf":        ["15m", "1H"],
}

# الرموز المناسبة لـ 15m من الباك تست السابق
TF_15M = {"NVDA", "MSFT", "AMD", "TSLA", "GOOGL", "AAPL"}


def _to_candles(df):
    out = []
    for ts, row in df.iterrows():
        dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.utcnow()
        out.append(Candle(float(row["Open"]), float(row["High"]), float(row["Low"]),
                          float(row["Close"]), float(row.get("Volume", 0)), dt))
    return out


def _flat(df):
    if df is not None and isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _score(has_sweep, has_disp, fvg, ob, htf, adx, min_adx, max_adx):
    s = 0.0
    if has_sweep: s += 30
    if has_disp:  s += 20
    if fvg:       s += 20
    if ob:        s += 10
    if htf != "NEUTRAL": s += 15
    else:                s += 5
    if min_adx <= adx <= max_adx: s += 5
    return s


def _run_one_symbol(c15_all, c1h_all, tf, min_adx, max_adx, min_score):
    day_results: Dict[date, List[str]] = defaultdict(list)
    WARMUP = 150
    pending = None
    wait_bars = 0
    last_trade_bar = -99

    for i in range(WARMUP, len(c15_all) - MAX_HOLD - 1):
        c15 = c15_all[:i+1]
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15

        candles = c15[-200:] if tf == "15m" else (c1h[-300:] if len(c1h) >= 50 else c15[-200:])
        if len(candles) < 60: continue

        atr   = _atr(candles, 14)
        adx   = _adx_calc(candles, 14)
        price = c15_all[i].close
        htf   = _get_1h_trend(c1h[-300:])

        if adx < min_adx or adx > max_adx:
            if pending: wait_bars += 1
            if pending and wait_bars > MAX_WAIT: pending = None; wait_bars = 0
            continue

        if pending is not None:
            wait_bars += 1
            z_top = pending["z_top"]; z_bot = pending["z_bot"]
            direction = pending["direction"]
            if htf != "NEUTRAL":
                if (direction=="LONG" and htf=="BEAR") or (direction=="SHORT" and htf=="BULL"):
                    pending = None; wait_bars = 0; continue

            bar = c15_all[i]
            if bar.low <= z_top and bar.high >= z_bot:
                body = abs(bar.close - bar.open)
                reject = False
                if direction == "LONG":
                    wick = min(bar.open, bar.close) - bar.low
                    reject = bar.close > bar.open or wick > body * 0.5
                else:
                    wick = bar.high - max(bar.open, bar.close)
                    reject = bar.close < bar.open or wick > body * 0.5

                if reject and (i - last_trade_bar) >= 15:
                    entry = bar.close
                    stop  = z_bot - atr*0.2 if direction=="LONG" else z_top + atr*0.2
                    risk  = abs(entry - stop)
                    if risk > 0:
                        tp1 = entry + risk*TP1_R if direction=="LONG" else entry - risk*TP1_R
                        outcome = "BE"
                        for fc in c15_all[i+1:i+1+MAX_HOLD+1]:
                            if direction == "LONG":
                                if fc.low  <= stop: outcome="LOSS"; break
                                if fc.high >= tp1:  outcome="WIN";  break
                            else:
                                if fc.high >= stop: outcome="LOSS"; break
                                if fc.low  <= tp1:  outcome="WIN";  break
                        trade_date = bar.timestamp.date()
                        day_results[trade_date].append(outcome)
                        last_trade_bar = i
                        pending = None; wait_bars = 0
                        continue

            if wait_bars > MAX_WAIT: pending = None; wait_bars = 0
            continue

        if (i - last_trade_bar) < 10: continue
        dirs = (["LONG","SHORT"] if htf=="NEUTRAL" else
                ["LONG"] if htf=="BULL" else ["SHORT"])

        for direction in dirs:
            sweep = _detect_liquidity_sweep(candles, direction, atr)
            disp  = _detect_displacement(candles, direction, atr)
            fvg   = _detect_fvg(candles, direction, atr)
            ob    = _detect_order_block(candles, direction, atr)
            has_sweep = sweep is not None
            has_disp  = disp is not None
            has_zone  = fvg is not None or ob is not None
            if not ((has_sweep or has_disp) and has_zone): continue
            sc = _score(has_sweep, has_disp, fvg, ob, htf, adx, min_adx, max_adx)
            if sc < min_score: continue
            zone = fvg or ob
            z_top, z_bot = max(zone), min(zone)
            if direction=="LONG" and price <= z_top: continue
            if direction=="SHORT" and price >= z_bot: continue
            pending = {"direction": direction, "z_top": z_top, "z_bot": z_bot}
            wait_bars = 0
            break

    return day_results


def _symbol_winning_days(day_results: Dict[date, List[str]]) -> Tuple[float, int, int, float]:
    if not day_results:
        return 0.0, 0, 0, 0.0
    days = sorted(day_results.keys())
    winning = sum(1 for d in days if "WIN" in day_results[d])
    total   = sum(len(v) for v in day_results.values())
    pct = round(winning / len(days) * 100, 1) if days else 0.0
    tpd = round(total / len(days), 2) if days else 0.0
    return pct, winning, len(days), tpd


def _combined_winning_days(all_day_results: List[Dict[date, List[str]]]) -> Tuple[float, int, int, float]:
    combined: Dict[date, List[str]] = defaultdict(list)
    for dr in all_day_results:
        for d, outcomes in dr.items():
            combined[d].extend(outcomes)
    days = sorted(combined.keys())
    if not days:
        return 0.0, 0, 0, 0.0
    winning = sum(1 for d in days if "WIN" in combined[d])
    total   = sum(len(v) for v in combined.values())
    pct = round(winning / len(days) * 100, 1)
    tpd = round(total / len(days), 2)
    return pct, winning, len(days), tpd


def main():
    print("\n" + "="*65)
    print(f"  Per-Symbol Optimizer -- {DAYS} days | {len(SYMBOLS)} symbols")
    print(f"  Goal: >90% winning days")
    print("="*65)

    end   = datetime.today()
    start = end - timedelta(days=DAYS)
    data  = {}
    print("\n  Downloading data...")
    for sym in SYMBOLS:
        try:
            df15 = _flat(yf.download(sym, start=start, end=end, interval="15m",
                                     progress=False, auto_adjust=True))
            df1h = _flat(yf.download(sym, start=start, end=end, interval="1h",
                                     progress=False, auto_adjust=True))
            if df15 is not None and len(df15) >= 100:
                data[sym] = {
                    "c15": _to_candles(df15),
                    "c1h": _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []
                }
                print(f"    {sym}: {len(df15)} bars 15m, {len(df1h) if df1h is not None else 0} bars 1H")
        except Exception as e:
            print(f"    {sym}: ERROR {e}")

    # شبكة البحث لكل رمز
    keys   = list(PER_SYMBOL_GRID.keys())
    combos = list(itertools.product(*[PER_SYMBOL_GRID[k] for k in keys]))
    print(f"\n  Per-symbol grid: {len(combos)} combos x {len(data)} symbols")
    print(f"  Running...\n")

    best_per_symbol = {}

    for sym, d in data.items():
        best_sym = {"score": -1, "pct": 0.0, "tpd": 0.0, "params": None, "dr": {}}
        for combo in combos:
            p = dict(zip(keys, combo))
            mn, mx, ms, tf = p["min_adx"], p["max_adx"], p["min_score"], p["tf"]
            if mn >= mx: continue
            # تجاهل TF غير المناسب للرمز (اختياري — نسمح بكليهما)
            dr = _run_one_symbol(d["c15"], d["c1h"], tf, mn, mx, ms)
            pct, win_d, tot_d, tpd = _symbol_winning_days(dr)
            # معيار: % أيام رابحة أولاً، ثم صفقات/يوم
            sc = pct * 100 + tpd
            if sc > best_sym["score"] and tpd >= 0.3:
                best_sym = {"score": sc, "pct": pct, "tpd": tpd,
                            "params": p, "dr": dr,
                            "win_d": win_d, "tot_d": tot_d}
        best_per_symbol[sym] = best_sym
        p = best_sym["params"]
        if p:
            print(f"    {sym:5s}: {best_sym['pct']:5.1f}% wd | {best_sym['tpd']:.2f} t/d "
                  f"| adx={p['min_adx']}-{p['max_adx']} conf={p['min_score']} tf={p['tf']}")
        else:
            print(f"    {sym:5s}: لا صفقات — تعطيل")

    # النتيجة المجمّعة
    all_dr = [v["dr"] for v in best_per_symbol.values() if v["dr"]]
    combined_pct, cwin, ctot, ctpd = _combined_winning_days(all_dr)

    print("\n" + "="*65)
    print(f"  COMBINED RESULT:")
    print(f"    Winning days: {cwin}/{ctot} = {combined_pct:.1f}%")
    print(f"    Trades/day:   {ctpd:.2f}")

    # بناء ملف profiles
    final_profiles = {}
    for sym in SYMBOLS:
        bst = best_per_symbol.get(sym, {})
        p   = bst.get("params")
        dr  = bst.get("dr", {})
        all_outcomes = [o for outcomes in dr.values() for o in outcomes]
        t = len(all_outcomes)
        w = all_outcomes.count("WIN")
        l = all_outcomes.count("LOSS")
        wr = round(w/(w+l)*100, 1) if (w+l) > 0 else 0.0
        enabled = p is not None and t >= 2

        if p:
            tf = p["tf"]
            final_profiles[sym] = {
                "enabled":  enabled,
                "timeframe": tf,
                "min_conf":  p["min_score"],
                "min_adx":   p["min_adx"],
                "max_adx":   p["max_adx"],
                "hold_bars": 16 if tf == "15m" else 40,
                "notes": f"{tf}: {w}W {l}L {wr:.1f}% | {bst.get('pct',0):.1f}% wd"
            }
        else:
            final_profiles[sym] = {
                "enabled": False, "timeframe": "1H",
                "min_conf": 999, "min_adx": 99, "max_adx": 999, "hold_bars": 40,
                "notes": "DISABLED: no trades"
            }

    final_profiles["__DEFAULT__"] = {
        "enabled": False, "timeframe": "1H",
        "min_conf": 999, "min_adx": 99, "max_adx": 999, "hold_bars": 40
    }

    with open("symbol_profiles_x2.json", "w", encoding="utf-8") as f:
        json.dump(final_profiles, f, indent=2, ensure_ascii=False)

    print(f"\n  Profiles saved -> symbol_profiles_x2.json")
    print("="*65 + "\n")


if __name__ == "__main__":
    main()
