# -*- coding: utf-8 -*-
"""
backtest_runner_x1.py — باك-تست لمحلل SmartDayTrading X1
TP1 = 2R → WIN فوري | SL → LOSS | انتهاء الوقت → BE
"""
import sys, math, warnings
from datetime import datetime, timedelta
from typing import List

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError:
    print("pip install yfinance pandas numpy")
    sys.exit(1)

from analyzer_x1 import (
    SmartDayTradingAnalyzer, MarketState, Candle, Direction, TradeSignal
)

# ─── إعداد ───────────────────────────────────────────────────
SYMBOLS_15M = ['QQQ', 'SPY', 'NVDA', 'MSFT', 'META', 'AAPL',
               'AMD', 'AMZN', 'AVGO', 'TSLA', 'COST', 'NFLX', 'GOOGL', 'LLY']

LOOKBACK_DAYS = 55       # Yahoo 15m = آخر 60 يوم فقط
MAX_HOLD_BARS = 20       # أقصى شموع 15m قبل BE
VIX_DEFAULT   = 18.0


# ─── تحويل yfinance → Candle ─────────────────────────────────
def _to_candles(df: pd.DataFrame) -> List[Candle]:
    out = []
    for ts, row in df.iterrows():
        dt = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else datetime.utcnow()
        out.append(Candle(
            open=float(row['Open']),
            high=float(row['High']),
            low=float(row['Low']),
            close=float(row['Close']),
            volume=float(row.get('Volume', 0)),
            timestamp=dt,
        ))
    return out


def _ema(values: List[float], period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0.0
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def _atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return candles[-1].high - candles[-1].low if candles else 1.0
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i-1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    used = trs[-period:] if len(trs) >= period else trs
    return sum(used) / len(used)


def _adx(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < period + 2:
        return 20.0
    pdm, mdm, tr = [], [], []
    for i in range(1, len(candles)):
        h, l, c, ph, pl, pc = (candles[i].high, candles[i].low, candles[i].close,
                                candles[i-1].high, candles[i-1].low, candles[i-1].close)
        pdm.append(max(h - ph, 0) if (h - ph) > (pl - l) else 0)
        mdm.append(max(pl - l, 0) if (pl - l) > (h - ph) else 0)
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    def smth(lst, p):
        s = sum(lst[:p])
        out = [s]
        for v in lst[p:]:
            s = s - s / p + v
            out.append(s)
        return out
    p = period
    atr_s = smth(tr, p); pdm_s = smth(pdm, p); mdm_s = smth(mdm, p)
    dx_vals = []
    for a, pd_, md in zip(atr_s, pdm_s, mdm_s):
        if a == 0:
            continue
        pdi = 100 * pd_ / a
        mdi = 100 * md / a
        denom = pdi + mdi
        dx_vals.append(100 * abs(pdi - mdi) / denom if denom else 0)
    if not dx_vals:
        return 20.0
    return sum(dx_vals[-p:]) / min(p, len(dx_vals))


def _build_market(candles_15m: List[Candle], candles_1h: List[Candle]) -> MarketState:
    closes_1h = [c.close for c in candles_1h] if candles_1h else [c.close for c in candles_15m]
    price = candles_15m[-1].close
    ema50  = _ema(closes_1h, 50)
    ema200 = _ema(closes_1h, 200)
    atr14  = _atr(candles_15m, 14)
    adx14  = _adx(candles_15m, 14)
    vols   = [c.volume for c in candles_15m[-20:] if c.volume > 0]
    avg_vol = sum(vols) / len(vols) if vols else 1.0
    cur_vol = candles_15m[-1].volume
    return MarketState(
        vix=VIX_DEFAULT, adx=adx14,
        volume=cur_vol, avg_volume_20=avg_vol,
        ema_50=ema50, ema_200=ema200,
        price=price, atr_14=atr14,
        news_risk="LOW",
    )


# ─── تنفيذ صفقة واحدة ─────────────────────────────────────────
def _simulate_trade(signal: TradeSignal, future_candles: List[Candle]) -> str:
    entry = signal.entry_price
    sl    = signal.stop_loss
    tp1   = signal.target_1
    direction = signal.direction

    for i, c in enumerate(future_candles[:MAX_HOLD_BARS]):
        if direction == Direction.LONG:
            if c.low <= sl:
                return "LOSS"
            if c.high >= tp1:
                return "WIN"
        else:
            if c.high >= sl:
                return "LOSS"
            if c.low <= tp1:
                return "WIN"
    return "BE"


# ─── باك-تست رمز واحد ─────────────────────────────────────────
def backtest_symbol(symbol: str) -> dict:
    end   = datetime.today()
    start = end - timedelta(days=LOOKBACK_DAYS)

    try:
        df15 = yf.download(symbol, start=start, end=end, interval="15m",
                           progress=False, auto_adjust=True)
        df1h = yf.download(symbol, start=start, end=end, interval="1h",
                           progress=False, auto_adjust=True)
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "trades": 0}

    if df15 is None or len(df15) < 100:
        return {"symbol": symbol, "error": "data too short", "trades": 0}

    # Flatten MultiIndex columns if present
    if isinstance(df15.columns, pd.MultiIndex):
        df15.columns = df15.columns.get_level_values(0)
    if isinstance(df1h.columns, pd.MultiIndex):
        df1h.columns = df1h.columns.get_level_values(0)

    candles_15m_all = _to_candles(df15)
    candles_1h_all  = _to_candles(df1h) if df1h is not None and len(df1h) > 10 else []

    analyzer = SmartDayTradingAnalyzer()
    # تجاوز cooldown وحدود يومية في الباك-تست
    analyzer.bypass_filters = True

    results = {"symbol": symbol, "trades": 0, "wins": 0, "losses": 0, "be": 0}
    WARMUP = 250  # شموع 15m للحساب الأولي

    for i in range(WARMUP, len(candles_15m_all) - MAX_HOLD_BARS - 1):
        window_15m = candles_15m_all[:i+1]

        # تعطيل cooldown يدوياً
        analyzer.cooldowns.clear()
        analyzer._daily_signal_count.clear()

        # الشموع 1H المتاحة حتى هذه اللحظة
        ts_now = candles_15m_all[i].timestamp
        window_1h = [c for c in candles_1h_all if c.timestamp <= ts_now] or window_15m

        market = _build_market(window_15m[-200:], window_1h[-300:])

        try:
            result = analyzer.analyze(symbol, market, window_1h[-300:], window_15m[-200:])
        except Exception:
            continue

        if not isinstance(result, TradeSignal):
            continue

        # تحاكي الصفقة على الشموع القادمة
        future = candles_15m_all[i+1:i+1+MAX_HOLD_BARS+1]
        outcome = _simulate_trade(result, future)

        results["trades"] += 1
        if outcome == "WIN":
            results["wins"] += 1
        elif outcome == "LOSS":
            results["losses"] += 1
        else:
            results["be"] += 1

    total = results["trades"]
    if total > 0:
        decisive = results["wins"] + results["losses"]
        results["win_rate"] = round(results["wins"] / decisive * 100, 1) if decisive else 0.0
        results["trades_per_day"] = round(total / LOOKBACK_DAYS, 2)
    else:
        results["win_rate"] = 0.0
        results["trades_per_day"] = 0.0

    return results


# ─── تشغيل كل الرموز ─────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print(f"  SmartDayTrading X1 -- Backtest {LOOKBACK_DAYS} days")
    print(f"  Symbols: {len(SYMBOLS_15M)} | TP1=2R | SL=1R | MAX_HOLD={MAX_HOLD_BARS} bars")
    print("="*60 + "\n")

    all_results = []
    total_trades = total_wins = total_losses = total_be = 0

    for sym in SYMBOLS_15M:
        print(f"  [{sym:5s}] ...", end=" ", flush=True)
        r = backtest_symbol(sym)
        all_results.append(r)

        if "error" in r:
            print(f"خطأ: {r['error']}")
            continue

        t, w, l, b, wr = r["trades"], r["wins"], r["losses"], r["be"], r["win_rate"]
        total_trades += t; total_wins += w; total_losses += l; total_be += b
        bar = "#" * int(wr / 10) + "." * (10 - int(wr / 10))
        flag = "OK" if wr >= 80 else ("~" if wr >= 65 else "X")
        print(f"{t:3d} trades | {w}W {l}L {b}BE | {wr:5.1f}% [{bar}] {flag}")

    print("\n" + "-"*60)
    decisive = total_wins + total_losses
    overall = round(total_wins / decisive * 100, 1) if decisive else 0.0
    print(f"  TOTAL: {total_trades} trades | {total_wins}W {total_losses}L {total_be}BE")
    print(f"  Overall Win Rate: {overall:.1f}%")
    print("-"*60)

    print("\n" + "-"*40)
    print("  Winners (>=80% win rate, >=5 trades):")
    qualified = [(r["symbol"], r["win_rate"], r["trades"]) for r in all_results
                 if "error" not in r and r.get("win_rate", 0) >= 80 and r["trades"] >= 5]
    qualified.sort(key=lambda x: -x[1])
    if qualified:
        for sym, wr, tr in qualified:
            print(f"    {sym:5s}: {wr:.1f}% ({tr} trades)")
    else:
        print("    No symbol reached 80%+")
    print("-"*40 + "\n")

    return all_results


if __name__ == "__main__":
    main()
