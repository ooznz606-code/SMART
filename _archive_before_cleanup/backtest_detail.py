# -*- coding: utf-8 -*-
"""
backtest_detail.py
باك تست تفصيلي — 8 رموز نشطة (15m/1H فقط)
"""
import sys, warnings, json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
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

with open("symbol_profiles_x2.json", encoding="utf-8") as f:
    PROFILES = json.load(f)

SYMBOLS  = [k for k in PROFILES if k != "__DEFAULT__"]
DAYS     = 55
TP1_R    = 2.0
MAX_WAIT = 30
MAX_HOLD = 22


def _to_candles(df) -> List[Candle]:
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
    s += 15 if htf != "NEUTRAL" else 5
    if min_adx <= adx <= max_adx: s += 5
    return s


def _run_symbol(sym, c15_all, c1h_all, tf, min_adx, max_adx, min_score):
    trades = []
    WARMUP = 150
    pending = None
    wait_bars = 0
    last_trade_bar = -99

    for i in range(WARMUP, len(c15_all) - MAX_HOLD - 1):
        c15 = c15_all[:i+1]
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15

        candles = c15[-200:] if tf == "15m" else (c1h[-300:] if len(c1h) >= 50 else c15[-200:])
        if len(candles) < 60:
            continue

        atr   = _atr(candles, 14)
        adx   = _adx_calc(candles, 14)
        price = c15_all[i].close
        htf   = _get_1h_trend(c1h[-300:])

        if adx < min_adx or adx > max_adx:
            if pending:
                wait_bars += 1
                if wait_bars > MAX_WAIT:
                    pending = None; wait_bars = 0
            continue

        if pending is not None:
            wait_bars += 1
            z_top = pending["z_top"]; z_bot = pending["z_bot"]
            direction = pending["direction"]

            if htf != "NEUTRAL":
                if (direction == "LONG" and htf == "BEAR") or (direction == "SHORT" and htf == "BULL"):
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
                    stop  = z_bot - atr * 0.2 if direction == "LONG" else z_top + atr * 0.2
                    risk  = abs(entry - stop)

                    if direction == "LONG"  and stop >= entry: pending = None; wait_bars = 0; continue
                    if direction == "SHORT" and stop <= entry: pending = None; wait_bars = 0; continue
                    if risk < atr * 0.25:   pending = None; wait_bars = 0; continue
                    if risk <= 0:           pending = None; wait_bars = 0; continue

                    tp1 = entry + risk * TP1_R if direction == "LONG" else entry - risk * TP1_R

                    outcome = "BE"
                    exit_price = entry
                    for fc in c15_all[i+1:i+1+MAX_HOLD+1]:
                        if direction == "LONG":
                            if fc.low  <= stop: outcome = "LOSS"; exit_price = stop; break
                            if fc.high >= tp1:  outcome = "WIN";  exit_price = tp1;  break
                        else:
                            if fc.high >= stop: outcome = "LOSS"; exit_price = stop; break
                            if fc.low  <= tp1:  outcome = "WIN";  exit_price = tp1;  break

                    pnl_r = TP1_R if outcome == "WIN" else (-1.0 if outcome == "LOSS" else 0.0)
                    trades.append({
                        "date":      bar.timestamp.strftime("%Y-%m-%d"),
                        "time":      bar.timestamp.strftime("%H:%M"),
                        "symbol":    sym,
                        "direction": direction,
                        "entry":     round(entry, 2),
                        "sl":        round(stop, 2),
                        "tp1":       round(tp1, 2),
                        "exit":      round(exit_price, 2),
                        "risk_$":    round(risk, 2),
                        "outcome":   outcome,
                        "pnl_r":     pnl_r,
                    })
                    last_trade_bar = i
                    pending = None; wait_bars = 0
                    continue

            if wait_bars > MAX_WAIT:
                pending = None; wait_bars = 0
            continue

        if (i - last_trade_bar) < 10:
            continue
        dirs = (["LONG", "SHORT"] if htf == "NEUTRAL" else
                ["LONG"] if htf == "BULL" else ["SHORT"])

        for direction in dirs:
            sweep = _detect_liquidity_sweep(candles, direction, atr)
            disp  = _detect_displacement(candles, direction, atr)
            fvg   = _detect_fvg(candles, direction, atr)
            ob    = _detect_order_block(candles, direction, atr)
            has_sweep = sweep is not None
            has_disp  = disp is not None
            if not ((has_sweep or has_disp) and (fvg or ob)):
                continue
            sc = _score(has_sweep, has_disp, fvg, ob, htf, adx, min_adx, max_adx)
            if sc < min_score:
                continue
            zone = fvg or ob
            z_top, z_bot = max(zone), min(zone)
            if direction == "LONG"  and price <= z_top: continue
            if direction == "SHORT" and price >= z_bot: continue
            pending = {"direction": direction, "z_top": z_top, "z_bot": z_bot}
            wait_bars = 0
            break

    return trades


def main():
    end   = datetime.today()
    start = end - timedelta(days=DAYS)

    print("\n" + "="*70)
    print(f"  BACKTEST -- {DAYS} days | {len(SYMBOLS)} symbols")
    print(f"  Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
    print("="*70)

    print("\n  Downloading data...")
    data = {}
    for sym in SYMBOLS:
        prof = PROFILES.get(sym, {})
        if not prof:
            continue
        try:
            df15 = _flat(yf.download(sym, start=start, end=end, interval="15m",
                                     progress=False, auto_adjust=True))
            df1h = _flat(yf.download(sym, start=start, end=end, interval="1h",
                                     progress=False, auto_adjust=True))
            if df15 is not None and len(df15) >= 100:
                data[sym] = {
                    "c15":  _to_candles(df15),
                    "c1h":  _to_candles(df1h) if df1h is not None and len(df1h) > 20 else [],
                    "prof": prof,
                }
                print(f"    {sym}: {len(df15)} x15m | {len(df1h) if df1h is not None else 0} x1h")
        except Exception as e:
            print(f"    {sym}: ERROR {e}")

    all_trades = []
    for sym, d in data.items():
        p  = d["prof"]
        tf = p.get("timeframe", "1H")
        trades = _run_symbol(sym, d["c15"], d["c1h"], tf,
                             p["min_adx"], p["max_adx"], p["min_conf"])
        all_trades.extend(trades)

    if not all_trades:
        print("\n  no trades")
        return

    all_trades.sort(key=lambda x: (x["date"], x["time"]))

    print(f"\n  {'Date':<12} {'Sym':<6} {'Dir':<6} {'Entry':>8} {'SL':>8} "
          f"{'TP1':>8} {'Exit':>8} Result")
    print("  " + "-"*68)

    day_results: Dict[str, List[str]] = defaultdict(list)
    for t in all_trades:
        icon = "WIN " if t["outcome"] == "WIN" else ("LOSS" if t["outcome"] == "LOSS" else "BE  ")
        print(f"  {t['date']:<12} {t['symbol']:<6} {t['direction']:<6} "
              f"{t['entry']:>8.2f} {t['sl']:>8.2f} {t['tp1']:>8.2f} "
              f"{t['exit']:>8.2f} {icon}")
        day_results[t["date"]].append(t["outcome"])

    print("\n" + "="*70)
    print("  PER-SYMBOL SUMMARY")
    print("  " + "-"*68)
    print(f"  {'Symbol':<8} {'Trades':>6} {'Wins':>6} {'Loss':>6} {'BE':>6} {'WR%':>7} {'Net R':>7}")
    print("  " + "-"*68)

    sym_stats = defaultdict(lambda: {"t": 0, "w": 0, "l": 0, "b": 0, "r": 0.0})
    for t in all_trades:
        s = sym_stats[t["symbol"]]
        s["t"] += 1; s["r"] += t["pnl_r"]
        if t["outcome"] == "WIN":    s["w"] += 1
        elif t["outcome"] == "LOSS": s["l"] += 1
        else:                        s["b"] += 1

    total_t = total_w = total_l = total_b = 0
    total_r = 0.0
    for sym in SYMBOLS:
        if sym not in sym_stats: continue
        s = sym_stats[sym]
        wr = round(s["w"] / (s["w"] + s["l"]) * 100, 1) if (s["w"] + s["l"]) > 0 else 0.0
        print(f"  {sym:<8} {s['t']:>6} {s['w']:>6} {s['l']:>6} {s['b']:>6} "
              f"{wr:>6.1f}% {s['r']:>+7.1f}R")
        total_t += s["t"]; total_w += s["w"]; total_l += s["l"]
        total_b += s["b"]; total_r += s["r"]

    print("  " + "-"*68)
    total_wr = round(total_w / (total_w + total_l) * 100, 1) if (total_w + total_l) > 0 else 0
    print(f"  {'TOTAL':<8} {total_t:>6} {total_w:>6} {total_l:>6} {total_b:>6} "
          f"{total_wr:>6.1f}% {total_r:>+7.1f}R")

    print("\n" + "="*70)
    print("  WINNING DAYS SUMMARY")
    print("  " + "-"*68)
    trading_days = sorted(day_results.keys())
    winning_days = [d for d in trading_days if "WIN" in day_results[d]]
    losing_days  = [d for d in trading_days if "WIN" not in day_results[d]]

    print(f"  Total trading days : {len(trading_days)}")
    print(f"  Winning days       : {len(winning_days)} ({round(len(winning_days)/len(trading_days)*100,1)}%)")
    print(f"  Losing days        : {len(losing_days)}")
    print(f"  Trades/day         : {round(total_t/len(trading_days),2)}")
    print(f"\n  Losing days:")
    for d in losing_days:
        print(f"    {d}: {day_results[d]}")

    print("\n" + "="*70)
    print("  FINANCIAL SUMMARY (risk $100/trade)")
    print("  " + "-"*68)
    gross_win  = total_w * 100.0 * TP1_R
    gross_loss = total_l * 100.0
    net        = gross_win - gross_loss
    print(f"  Gross profit : ${gross_win:,.0f}")
    print(f"  Gross loss   : ${gross_loss:,.0f}")
    print(f"  Net P&L      : ${net:+,.0f}")
    print(f"  Net R        : {total_r:+.1f}R")
    print(f"  Profit Factor: {round(gross_win/gross_loss,2) if gross_loss > 0 else 'inf'}")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
