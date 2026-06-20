# -*- coding: utf-8 -*-
"""
optimize_90pct.py
الهدف: >90% ايام رابحة
المنطق: نحتاج 7+ صفقات/يوم بنسبة 30%+ لكل صفقة
P(winning_day) = 1 - (1-wr)^n
n=7, wr=30% -> P = 1-(0.70)^7 = 91.8%
"""
import sys, warnings, itertools, json, math
from datetime import datetime, timedelta, date
from typing import List, Dict, Tuple
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
            "AMZN","TSLA","NFLX","GOOGL","LLY","GLD","SQQQ"]
DAYS     = 55
TP1_R    = 2.0
MAX_WAIT = 30
MAX_HOLD = 22

# شبكة موسّعة للوصول لـ 90%
# min_conf اقل = صفقات اكثر = ايام رابحة اكثر
GRID = {
    "min_adx":   [15, 18, 22],
    "max_adx":   [55, 70, 999],
    "min_score": [45, 48, 50, 52, 55],
    "tf_mode":   ["MIXED", "15m_only"],
}
# MIXED: 15m للرموز المتقلبة، 1H للباقي (من الباك تست السابق)
TF_15M_SYMS = {"NVDA", "MSFT", "AMD", "TSLA", "GOOGL", "AAPL"}


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

        atr  = _atr(candles, 14)
        adx  = _adx_calc(candles, 14)
        price= c15_all[i].close
        htf  = _get_1h_trend(c1h[-300:])

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
                        day_results[bar.timestamp.date()].append(outcome)
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


def _calc(all_dr):
    combined: Dict[date, List[str]] = defaultdict(list)
    for dr in all_dr:
        for d, outcomes in dr.items():
            combined[d].extend(outcomes)
    days = sorted(combined.keys())
    if not days: return 0.0, 0, 0, 0.0
    winning = sum(1 for d in days if "WIN" in combined[d])
    total   = sum(len(v) for v in combined.values())
    return round(winning/len(days)*100, 1), winning, len(days), round(total/len(days), 2)


def main():
    combos = [c for c in itertools.product(*[GRID[k] for k in GRID])
              if c[0] < c[1]]  # min_adx < max_adx
    print("\n" + "="*65)
    print(f"  Optimizer 90% -- {DAYS} days | {len(SYMBOLS)} symbols")
    print(f"  Grid: {len(combos)} combos")
    print("="*65)

    end   = datetime.today()
    start = end - timedelta(days=DAYS)
    data  = {}
    print("\n  Downloading...")
    for sym in SYMBOLS:
        try:
            df15 = _flat(yf.download(sym, start=start, end=end, interval="15m",
                                     progress=False, auto_adjust=True))
            df1h = _flat(yf.download(sym, start=start, end=end, interval="1h",
                                     progress=False, auto_adjust=True))
            if df15 is not None and len(df15) >= 100:
                data[sym] = {"c15": _to_candles(df15),
                             "c1h": _to_candles(df1h) if df1h is not None and len(df1h)>20 else []}
                print(f"    {sym}: OK")
        except Exception as e:
            print(f"    {sym}: ERROR {e}")

    print(f"\n  Running {len(combos)} combos...\n")
    best = {"pct": 0.0, "tpd": 0.0, "params": None, "all_dr": None}
    keys = list(GRID.keys())

    for combo in combos:
        p = dict(zip(keys, combo))
        mn, mx, ms, tfmode = p["min_adx"], p["max_adx"], p["min_score"], p["tf_mode"]

        all_dr = []
        for sym, d in data.items():
            tf = "15m" if (tfmode == "15m_only" or
                           (tfmode == "MIXED" and sym in TF_15M_SYMS)) else "1H"
            all_dr.append(_run_one_symbol(d["c15"], d["c1h"], tf, mn, mx, ms))

        pct, win_d, tot_d, tpd = _calc(all_dr)

        # معيار: ايام رابحة اولاً، ثم صفقات/يوم
        if (pct, tpd) > (best["pct"], best["tpd"]):
            best = {"pct": pct, "tpd": tpd, "win_d": win_d, "tot_d": tot_d,
                    "params": p, "all_dr": all_dr}
            print(f"  >> NEW BEST: {pct:.1f}% wd | {tpd:.2f} t/d | "
                  f"adx={mn}-{mx} conf={ms} tf={tfmode}")

    print("\n" + "="*65)
    p = best["params"]
    print(f"  BEST: {best['pct']:.1f}% ({best['win_d']}/{best['tot_d']} days)"
          f" | {best['tpd']:.2f} t/d")
    print(f"  Config: adx={p['min_adx']}-{p['max_adx']} "
          f"conf={p['min_score']} tf={p['tf_mode']}")

    # تفاصيل لكل رمز
    print(f"\n  Per-symbol:")
    final_profiles = {}
    for sym, dr in zip([s for s in SYMBOLS if s in data], best["all_dr"]):
        all_out = [o for v in dr.values() for o in v]
        t = len(all_out); w = all_out.count("WIN")
        l = all_out.count("LOSS"); b = all_out.count("BE")
        wr = round(w/(w+l)*100,1) if (w+l)>0 else 0.0
        print(f"    {sym:5s}: {t:3d} trades | {w}W {l}L {b}BE | {wr:.1f}%")

        tf = "15m" if (p["tf_mode"]=="15m_only" or
                       (p["tf_mode"]=="MIXED" and sym in TF_15M_SYMS)) else "1H"
        final_profiles[sym] = {
            "enabled": t >= 2,
            "timeframe": tf,
            "min_conf":  p["min_score"],
            "min_adx":   p["min_adx"],
            "max_adx":   p["max_adx"],
            "hold_bars": 16 if tf=="15m" else 40,
            "notes": f"{tf}: {w}W {l}L {wr:.1f}%"
        }

    # حساب win rate نظري للايام الرابحة
    total_t = sum(len([o for v in dr.values() for o in v]) for dr in best["all_dr"])
    total_w = sum(len([o for v in dr.values() for o in v if o=="WIN"]) for dr in best["all_dr"])
    total_l = sum(len([o for v in dr.values() for o in v if o=="LOSS"]) for dr in best["all_dr"])
    overall_wr = round(total_w/(total_w+total_l)*100,1) if (total_w+total_l)>0 else 0
    tpd = best["tpd"]
    p_win_day = round((1 - (1-overall_wr/100)**tpd)*100, 1)
    print(f"\n  Overall win rate: {overall_wr}% | Trades/day: {tpd:.2f}")
    print(f"  Theoretical P(winning day): {p_win_day}%")

    final_profiles["__DEFAULT__"] = {
        "enabled": False, "timeframe": "1H",
        "min_conf": 999, "min_adx": 99, "max_adx": 999, "hold_bars": 40
    }
    with open("symbol_profiles_x2.json", "w", encoding="utf-8") as f:
        json.dump(final_profiles, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved -> symbol_profiles_x2.json")
    print("="*65 + "\n")


if __name__ == "__main__":
    main()
