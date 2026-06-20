# -*- coding: utf-8 -*-
"""
optimize_x2.py — Per-Symbol Parameter Optimizer for ICT X2
لكل رمز: يجرب كل مجموعة معايير ويختار الأفضل (win_rate >= 75%, trades >= 4)
"""
import sys, warnings, itertools
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

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

SYMBOLS = ["QQQ","SPY","NVDA","MSFT","META","AAPL","AMD","AMZN","AVGO","TSLA","COST","NFLX","GOOGL","LLY"]
DAYS    = 55
TP1_R   = 2.0
MAX_WAIT = 30
MAX_HOLD = 22

# شبكة البحث
GRID = {
    "timeframe": ["1H", "15m"],
    "min_adx":   [15, 18, 20, 22, 25, 28],
    "max_adx":   [50, 55, 60, 70, 999],
    "min_score": [55, 60, 65, 70, 75],
}


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


def _run_backtest(c15_all, c1h_all, tf, min_adx, max_adx, min_score):
    wins = losses = bes = 0
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

        # Stage 2
        if pending is not None:
            wait_bars += 1
            z_top = pending["z_top"]; z_bot = pending["z_bot"]
            direction = pending["direction"]

            if htf != "NEUTRAL":
                if (direction == "LONG" and htf == "BEAR") or (direction == "SHORT" and htf == "BULL"):
                    pending = None; wait_bars = 0; continue

            bar = c15_all[i]
            touched = bar.low <= z_top and bar.high >= z_bot
            if touched:
                body = abs(bar.close - bar.open)
                reject = False
                if direction == "LONG":
                    wick = min(bar.open, bar.close) - bar.low
                    reject = bar.close > bar.open or wick > body * 0.5
                else:
                    wick = bar.high - max(bar.open, bar.close)
                    reject = bar.close < bar.open or wick > body * 0.5

                if reject and (i - last_trade_bar) >= 20:
                    entry = bar.close
                    stop  = z_bot - atr * 0.2 if direction == "LONG" else z_top + atr * 0.2
                    risk  = abs(entry - stop)
                    if risk > 0:
                        tp1 = entry + risk * TP1_R if direction == "LONG" else entry - risk * TP1_R
                        outcome = "BE"
                        for fc in c15_all[i+1:i+1+MAX_HOLD+1]:
                            if direction == "LONG":
                                if fc.low  <= stop: outcome = "LOSS"; break
                                if fc.high >= tp1:  outcome = "WIN";  break
                            else:
                                if fc.high >= stop: outcome = "LOSS"; break
                                if fc.low  <= tp1:  outcome = "WIN";  break
                        if outcome == "WIN":   wins   += 1
                        elif outcome == "LOSS": losses += 1
                        else:                  bes    += 1
                        last_trade_bar = i
                        pending = None; wait_bars = 0
                        continue

            if wait_bars > MAX_WAIT: pending = None; wait_bars = 0
            continue

        # Stage 1
        if (i - last_trade_bar) < 10: continue

        dirs = (["LONG","SHORT"] if htf == "NEUTRAL" else
                ["LONG"] if htf == "BULL" else ["SHORT"])

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

            if direction == "LONG" and price <= z_top: continue
            if direction == "SHORT" and price >= z_bot: continue

            pending = {"direction": direction, "z_top": z_top, "z_bot": z_bot}
            wait_bars = 0
            break

    total = wins + losses + bes
    decisive = wins + losses
    wr = round(wins / decisive * 100, 1) if decisive else 0.0
    return {"trades": total, "wins": wins, "losses": losses, "be": bes, "win_rate": wr}


def optimize_symbol(symbol: str, c15_all, c1h_all) -> dict:
    best = {"win_rate": 0.0, "trades": 0, "score_key": None, "params": None}

    keys = list(GRID.keys())
    combos = list(itertools.product(*[GRID[k] for k in keys]))

    for combo in combos:
        params = dict(zip(keys, combo))
        tf, mn, mx, ms = params["timeframe"], params["min_adx"], params["max_adx"], params["min_score"]
        if mn >= mx: continue

        r = _run_backtest(c15_all, c1h_all, tf, mn, mx, ms)
        if r["trades"] < 4: continue

        # معيار الاختيار: win_rate أولاً، ثم عدد الصفقات
        score_key = (r["win_rate"], r["trades"])
        if score_key > (best["win_rate"], best["trades"]):
            best = {**r, "score_key": score_key, "params": params}

    return best


def main():
    print("\n" + "="*70)
    print(f"  SMART X2 Per-Symbol Optimizer — {DAYS} days | {len(SYMBOLS)} symbols")
    print(f"  Grid: {sum(1 for _ in itertools.product(*GRID.values()))} combos/symbol")
    print("="*70 + "\n")

    end   = datetime.today()
    start = end - timedelta(days=DAYS)

    final_profiles = {}
    summary = []

    for sym in SYMBOLS:
        print(f"  [{sym:5s}] downloading...", end=" ", flush=True)
        try:
            df15 = _flat(yf.download(sym, start=start, end=end, interval="15m",
                                     progress=False, auto_adjust=True))
            df1h = _flat(yf.download(sym, start=start, end=end, interval="1h",
                                     progress=False, auto_adjust=True))
        except Exception as e:
            print(f"ERROR: {e}"); continue

        if df15 is None or len(df15) < 100:
            print("data short"); continue

        c15 = _to_candles(df15)
        c1h = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []

        print("optimizing...", end=" ", flush=True)
        best = optimize_symbol(sym, c15, c1h)

        if best["params"] is None:
            print("no result (< 4 trades in all combos)")
            continue

        p   = best["params"]
        wr  = best["win_rate"]
        t   = best["trades"]
        w, l, b = best["wins"], best["losses"], best["be"]
        bar  = "#" * int(wr / 10) + "." * (10 - int(wr / 10))
        flag = "OK" if wr >= 80 else ("~" if wr >= 70 else "X")
        print(f"BEST: {t:3d} trades | {w}W {l}L {b}BE | {wr:5.1f}% [{bar}] {flag}"
              f" | tf={p['timeframe']} adx={p['min_adx']}-{p['max_adx']} score>={p['min_score']}")

        final_profiles[sym] = {
            "enabled": wr >= 65,
            "timeframe": p["timeframe"],
            "min_conf": p["min_score"],
            "min_adx":  p["min_adx"],
            "max_adx":  p["max_adx"],
            "hold_bars": 40 if p["timeframe"] == "1H" else 16,
            "notes": f"{p['timeframe']}: {w}W {l}L {wr:.1f}%"
        }
        summary.append((sym, wr, t, p))

    # حفظ النتائج
    import json
    final_profiles["__DEFAULT__"] = {
        "enabled": False, "timeframe": "1H", "min_conf": 999,
        "min_adx": 99, "max_adx": 999, "hold_bars": 40
    }
    out_path = "symbol_profiles_x2.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(final_profiles, f, indent=2, ensure_ascii=False)

    print("\n" + "-"*70)
    print("  FINAL RESULTS (sorted by win rate):")
    summary.sort(key=lambda x: -x[1])
    for sym, wr, t, p in summary:
        flag = "OK" if wr >= 80 else ("~" if wr >= 70 else "X")
        print(f"  {flag} {sym:5s}: {wr:5.1f}% | {t} trades | tf={p['timeframe']}"
              f" adx={p['min_adx']}-{p['max_adx']} score>={p['min_score']}")

    qualified = [(s,w,t) for s,w,t,_ in summary if w >= 75 and t >= 4]
    print(f"\n  Qualified (>=75%, >=4 trades): {len(qualified)}")
    for s,w,t in qualified:
        print(f"    {s:5s}: {w:.1f}% ({t} trades)")

    print(f"\n  Profiles saved to: {out_path}")
    print("-"*70 + "\n")


if __name__ == "__main__":
    main()
