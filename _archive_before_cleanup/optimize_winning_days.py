# -*- coding: utf-8 -*-
"""
optimize_winning_days.py
الهدف: يوم رابح = يوم فيه صفقة واحدة على الأقل وصلت TP1
نحسب: % الأيام الرابحة من أيام التداول الفعلية
نبحث عن: >= 85% أيام رابحة + >= 2 صفقة/يوم
"""
import sys, warnings, itertools, json
from datetime import datetime, timedelta, date
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

SYMBOLS  = ["QQQ","SPY","NVDA","MSFT","META","AAPL","AMD",
            "AMZN","AVGO","TSLA","COST","NFLX","GOOGL","LLY"]
DAYS     = 55
TP1_R    = 2.0
MAX_WAIT = 30
MAX_HOLD = 22

# شبكة البحث المشتركة لكل الرموز
GRID = {
    "min_adx":  [18, 22, 25],
    "max_adx":  [55, 70, 999],
    "min_score":[55, 65],
    "tf_mode":  ["MIXED"],
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


def _run_one_symbol(c15_all, c1h_all, tf, min_adx, max_adx, min_score):
    """يُعيد قاموس: {date: [outcomes]} للصفقات"""
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

        # Stage 2
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

        # Stage 1
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


def _calc_winning_days(all_day_results: List[Dict[date, List[str]]]):
    """دمج نتائج كل الرموز واحسب % أيام رابحة"""
    combined: Dict[date, List[str]] = defaultdict(list)
    for dr in all_day_results:
        for d, outcomes in dr.items():
            combined[d].extend(outcomes)

    trading_days = sorted(combined.keys())
    if not trading_days:
        return 0.0, 0, 0, 0.0

    winning_days = sum(1 for d in trading_days if "WIN" in combined[d])
    total_trades = sum(len(v) for v in combined.values())
    trades_per_day = total_trades / len(trading_days) if trading_days else 0.0
    pct = round(winning_days / len(trading_days) * 100, 1)
    return pct, winning_days, len(trading_days), trades_per_day


def main():
    print("\n" + "="*65)
    print(f"  Winning Days Optimizer — {DAYS} days | {len(SYMBOLS)} symbols")
    print(f"  Goal: >=85% winning days + >=2 trades/day")
    print("="*65)

    # تحميل البيانات مرة واحدة
    end   = datetime.today()
    start = end - timedelta(days=DAYS)
    data = {}
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

    print(f"\n  Running grid search ({len(list(itertools.product(*GRID.values())))} combos)...\n")

    best_result = {"pct": 0.0, "trades_per_day": 0.0, "params": None, "details": None}

    keys = list(GRID.keys())
    combos = list(itertools.product(*[GRID[k] for k in keys]))

    for combo in combos:
        params = dict(zip(keys, combo))
        mn, mx, ms, tfmode = params["min_adx"], params["max_adx"], params["min_score"], params["tf_mode"]
        if mn >= mx: continue

        all_day_results = []
        for sym, d in data.items():
            # اختر TF
            if tfmode == "15m_only":     tf = "15m"
            elif tfmode == "1H_only":    tf = "1H"
            else:                        tf = "15m" if sym in ["NVDA","MSFT","AMD","TSLA","GOOGL","AAPL"] else "1H"

            dr = _run_one_symbol(d["c15"], d["c1h"], tf, mn, mx, ms)
            all_day_results.append(dr)

        pct, win_days, total_days, tpd = _calc_winning_days(all_day_results)

        # معيار: أيام رابحة أولاً، ثم صفقات/يوم
        score_key = (pct, tpd)
        if score_key > (best_result["pct"], best_result["trades_per_day"]):
            best_result = {
                "pct": pct, "trades_per_day": tpd,
                "win_days": win_days, "total_days": total_days,
                "params": params, "all_day_results": all_day_results,
            }

    # النتيجة الأفضل
    print("="*65)
    p = best_result["params"]
    print(f"  BEST CONFIG:")
    print(f"    min_adx={p['min_adx']} max_adx={p['max_adx']}"
          f" min_score={p['min_score']} tf={p['tf_mode']}")
    print(f"    Winning days: {best_result['win_days']}/{best_result['total_days']}"
          f" = {best_result['pct']:.1f}%")
    print(f"    Trades/day:   {best_result['trades_per_day']:.2f}")

    # تفاصيل كل رمز
    print(f"\n  Per-symbol breakdown:")
    final_profiles = {}
    for sym, dr in zip([s for s in SYMBOLS if s in data], best_result["all_day_results"]):
        all_outcomes = [o for outcomes in dr.values() for o in outcomes]
        t = len(all_outcomes)
        w = all_outcomes.count("WIN")
        l = all_outcomes.count("LOSS")
        b = all_outcomes.count("BE")
        wr = round(w/(w+l)*100,1) if (w+l)>0 else 0.0
        print(f"    {sym:5s}: {t:3d} trades | {w}W {l}L {b}BE | {wr:.1f}% win/trade")

        if p["tf_mode"] == "15m_only":     tf = "15m"
        elif p["tf_mode"] == "1H_only":    tf = "1H"
        else: tf = "15m" if sym in ["NVDA","MSFT","AMD","TSLA","GOOGL","AAPL"] else "1H"

        final_profiles[sym] = {
            "enabled": t >= 3,
            "timeframe": tf,
            "min_conf": p["min_score"],
            "min_adx":  p["min_adx"],
            "max_adx":  p["max_adx"],
            "hold_bars": 16 if tf=="15m" else 40,
            "notes": f"{tf}: {w}W {l}L {wr:.1f}%"
        }

    final_profiles["__DEFAULT__"] = {
        "enabled": False, "timeframe": "1H", "min_conf": 999,
        "min_adx": 99, "max_adx": 999, "hold_bars": 40
    }

    with open("symbol_profiles_x2.json", "w", encoding="utf-8") as f:
        json.dump(final_profiles, f, indent=2, ensure_ascii=False)

    print(f"\n  Profiles saved -> symbol_profiles_x2.json")
    print("="*65 + "\n")


if __name__ == "__main__":
    main()
