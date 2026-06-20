# -*- coding: utf-8 -*-
"""
optimize_65pct_winrate.py
الهدف: كل رمز win rate > 65% (win/win+loss)
كل رمز يأخذ افضل معاملات تحقق >65% مع اكبر عدد صفقات ممكن
"""
import sys, warnings, itertools, json
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

SYMBOLS = ["QQQ","SPY","NVDA","MSFT","META","AAPL","AMD",
           "AMZN","TSLA","NFLX","GOOGL","LLY","SQQQ"]

DAYS     = 55
TP1_R    = 2.0
MAX_WAIT = 30
MAX_HOLD = 22
TARGET_WR = 65.0   # هدف win rate لكل رمز
MIN_TRADES = 3     # أقل عدد صفقات مقبول

TF_15M_SYMS = {"NVDA","MSFT","AMD","TSLA","GOOGL","AAPL"}

# شبكة مضغوطة للسرعة
GRID = {
    "min_adx":   [15, 20, 25],
    "max_adx":   [40, 55, 70],
    "min_score": [60, 65, 70, 75],
    "tf":        ["15m", "1H"],
}


def _to_candles(df):
    out = []
    for ts, row in df.iterrows():
        dt = ts.to_pydatetime() if hasattr(ts,"to_pydatetime") else datetime.utcnow()
        out.append(Candle(float(row["Open"]), float(row["High"]), float(row["Low"]),
                          float(row["Close"]), float(row.get("Volume",0)), dt))
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
    s += 15 if htf != "NEUTRAL" else 5
    if min_adx <= adx <= max_adx: s += 5
    return s

def _run_symbol(c15_all, c1h_all, tf, min_adx, max_adx, min_score):
    results = []
    WARMUP = 150
    pending = None
    wait_bars = 0
    last_trade_bar = -99

    for i in range(WARMUP, len(c15_all) - MAX_HOLD - 1):
        c15 = c15_all[:i+1]
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15
        candles = c15[-200:] if tf=="15m" else (c1h[-300:] if len(c1h)>=50 else c15[-200:])
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
                        results.append(outcome)
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
            if not ((sweep or disp) and (fvg or ob)): continue
            sc = _score(sweep is not None, disp is not None, fvg, ob, htf, adx, min_adx, max_adx)
            if sc < min_score: continue
            zone = fvg or ob
            z_top, z_bot = max(zone), min(zone)
            if direction=="LONG" and price <= z_top: continue
            if direction=="SHORT" and price >= z_bot: continue
            pending = {"direction": direction, "z_top": z_top, "z_bot": z_bot}
            wait_bars = 0
            break

    return results


def _calc_wr(results):
    w = results.count("WIN")
    l = results.count("LOSS")
    return round(w/(w+l)*100, 1) if (w+l) > 0 else 0.0, w, l, results.count("BE")


def main():
    keys   = list(GRID.keys())
    combos = [(dict(zip(keys,c))) for c in itertools.product(*[GRID[k] for k in keys])
              if c[0] < c[1]]  # min_adx < max_adx

    print("\n" + "="*65)
    print(f"  Optimizer 65% WinRate -- {DAYS} days | {len(SYMBOLS)} symbols")
    print(f"  Grid: {len(combos)} combos per symbol")
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

    print(f"\n  Running {len(combos)} combos x {len(data)} symbols...\n")

    final_profiles = {}
    summary_rows = []

    for sym, d in data.items():
        # للعثور على افضل معاملات تحقق >65% مع اكبر عدد صفقات
        best = {"wr": 0.0, "trades": 0, "w": 0, "l": 0, "b": 0, "params": None}

        for p in combos:
            mn, mx, ms, tf = p["min_adx"], p["max_adx"], p["min_score"], p["tf"]
            results = _run_symbol(d["c15"], d["c1h"], tf, mn, mx, ms)
            wr, w, l, b = _calc_wr(results)
            t = len(results)
            if t < MIN_TRADES: continue

            # يجب ان يكون win rate >= 65%
            if wr < TARGET_WR: continue

            # اختر: اكبر عدد صفقات مع اعلى win rate
            score = t * 10 + wr  # اولوية للصفقات الاكثر
            prev_score = best["trades"] * 10 + best["wr"]
            if score > prev_score:
                best = {"wr": wr, "trades": t, "w": w, "l": l, "b": b, "params": p}

        p = best["params"]
        if p:
            tf = p["tf"]
            final_profiles[sym] = {
                "enabled":   True,
                "timeframe": tf,
                "min_conf":  p["min_score"],
                "min_adx":   p["min_adx"],
                "max_adx":   p["max_adx"],
                "hold_bars": 16 if tf=="15m" else 40,
                "notes":     f"{tf}: {best['w']}W {best['l']}L {best['wr']:.1f}%"
            }
            summary_rows.append((sym, best["trades"], best["w"], best["l"],
                                  best["b"], best["wr"], p))
            print(f"  {sym:6s}: {best['wr']:5.1f}% | {best['trades']:2d} trades "
                  f"| adx={p['min_adx']}-{p['max_adx']} conf={p['min_score']} tf={tf}")
        else:
            # لم يصل لـ65% — خذ افضل ما يمكن مع تعطيل
            best2 = {"wr": 0.0, "trades": 0, "w": 0, "l": 0, "b": 0, "params": None}
            for p2 in combos:
                mn, mx, ms, tf2 = p2["min_adx"], p2["max_adx"], p2["min_score"], p2["tf"]
                results = _run_symbol(d["c15"], d["c1h"], tf2, mn, mx, ms)
                wr, w, l, b = _calc_wr(results)
                t = len(results)
                if t < 2: continue
                if wr > best2["wr"]: best2 = {"wr": wr, "trades": t, "w": w, "l": l, "b": b, "params": p2}
            p2 = best2.get("params")
            final_profiles[sym] = {
                "enabled":   False,
                "timeframe": p2["tf"] if p2 else "1H",
                "min_conf":  p2["min_score"] if p2 else 999,
                "min_adx":   p2["min_adx"] if p2 else 99,
                "max_adx":   p2["max_adx"] if p2 else 999,
                "hold_bars": 40,
                "notes":     f"DISABLED: best={best2['wr']:.1f}% < 65%"
            }
            summary_rows.append((sym, best2["trades"], best2["w"], best2["l"],
                                  best2["b"], best2["wr"], None))
            print(f"  {sym:6s}: {best2['wr']:5.1f}% | DISABLED (لم يصل 65%)")

    final_profiles["__DEFAULT__"] = {
        "enabled": False, "timeframe": "1H",
        "min_conf": 999, "min_adx": 99, "max_adx": 999, "hold_bars": 40
    }

    with open("symbol_profiles_x2.json", "w", encoding="utf-8") as f:
        json.dump(final_profiles, f, indent=2, ensure_ascii=False)

    # ملخص نهائي
    print("\n" + "="*65)
    print(f"  {'Symbol':<8} {'Trades':>6} {'W':>4} {'L':>4} {'BE':>4} {'WR%':>7} {'Status'}")
    print("  " + "-"*55)
    enabled_syms = []
    for sym, t, w, l, b, wr, p in summary_rows:
        status = "ACTIVE" if p else "DISABLED"
        print(f"  {sym:<8} {t:>6} {w:>4} {l:>4} {b:>4} {wr:>6.1f}%  {status}")
        if p: enabled_syms.append(sym)

    print(f"\n  Active symbols : {len(enabled_syms)} — {', '.join(enabled_syms)}")
    print(f"  Saved -> symbol_profiles_x2.json")
    print("="*65 + "\n")


if __name__ == "__main__":
    main()
