# -*- coding: utf-8 -*-
from collections import defaultdict
from analyzer_bc_core import CHART_DIR, load_symbol_candles

SYMBOLS = ["SPY", "QQQ", "MSFT", "NVDA"]
TARGET_R = 1.5
MAX_HOLD = 30
MAX_TRADES_DAY = 2

def ema(vals, n):
    if len(vals) < n: return vals[-1]
    k = 2/(n+1)
    e = sum(vals[:n])/n
    for v in vals[n:]:
        e = v*k + e*(1-k)
    return e

def vwap(day):
    num = den = 0
    out = []
    for b in day:
        tp = (b.high + b.low + b.close) / 3
        num += tp * b.volume
        den += b.volume
        out.append(num / den if den else b.close)
    return out

def atr(bars, i, n=14):
    if i < n+1: return 0
    trs = []
    for j in range(i-n+1, i+1):
        h,l,pc = bars[j].high, bars[j].low, bars[j-1].close
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs)/len(trs)

def rvol(day, i):
    if i < 20: return 1
    avg = sum(b.volume for b in day[i-20:i]) / 20
    return day[i].volume / avg if avg else 1

def simulate(day, i, direction, entry, stop, target):
    for b in day[i+1:i+1+MAX_HOLD]:
        if direction == "LONG":
            if b.low <= stop: return "LOSS", -1
            if b.high >= target: return "WIN", TARGET_R
        else:
            if b.high >= stop: return "LOSS", -1
            if b.low <= target: return "WIN", TARGET_R
    return "BE", 0

trades = []

for sym in SYMBOLS:
    data = load_symbol_candles(sym, CHART_DIR)
    if not data:
        continue

    c15, c1h = data
    byday = defaultdict(list)
    for b in c15:
        byday[str(b.timestamp.date())].append(b)

    for d, day in byday.items():
        if len(day) < 40:
            continue

        vw = vwap(day)
        used = 0

        for i in range(25, len(day)-MAX_HOLD):
            if used >= MAX_TRADES_DAY:
                break

            b = day[i]
            closes = [x.close for x in day[:i+1]]
            e20 = ema(closes, 20)
            a = atr(day, i)
            if a <= 0:
                continue

            rv = rvol(day, i)
            if rv < 1.1:
                continue

            # LONG VWAP reclaim:
            # الشمعة السابقة تحت VWAP الحالية تغلق فوق VWAP و EMA20
            long_ok = (
                day[i-1].close < vw[i-1] and
                b.close > vw[i] and
                b.close > e20 and
                b.close > b.open and
                abs(b.close - vw[i]) <= 0.8 * a
            )

            # SHORT VWAP reject:
            # الشمعة السابقة فوق VWAP الحالية تغلق تحت VWAP و EMA20
            short_ok = (
                day[i-1].close > vw[i-1] and
                b.close < vw[i] and
                b.close < e20 and
                b.close < b.open and
                abs(b.close - vw[i]) <= 0.8 * a
            )

            if not long_ok and not short_ok:
                continue

            direction = "LONG" if long_ok else "SHORT"
            entry = b.close

            if direction == "LONG":
                stop = min(x.low for x in day[max(0,i-5):i+1]) - 0.1*a
                risk = entry - stop
                if risk <= 0: continue
                target = entry + risk * TARGET_R
            else:
                stop = max(x.high for x in day[max(0,i-5):i+1]) + 0.1*a
                risk = stop - entry
                if risk <= 0: continue
                target = entry - risk * TARGET_R

            out, R = simulate(day, i, direction, entry, stop, target)
            trades.append(dict(date=d,time=b.timestamp.strftime("%H:%M"),sym=sym,dir=direction,entry=entry,out=out,R=R,rvol=rv))
            used += 1

W = sum(1 for t in trades if t["out"] == "WIN")
L = sum(1 for t in trades if t["out"] == "LOSS")
BE = sum(1 for t in trades if t["out"] == "BE")
N = len(trades)
total = sum(t["R"] for t in trades)
wr = W / max(1, W+L) * 100
pf = (sum(t["R"] for t in trades if t["R"] > 0) / abs(sum(t["R"] for t in trades if t["R"] < 0))) if L else 999

print("="*90)
print("VWAP Reclaim / Reject Research")
print("="*90)
print(f"N={N} W={W} L={L} BE={BE} WR={wr:.1f}% PF={pf:.2f} TotalR={total:+.2f}")
print("-"*90)
for t in trades[-80:]:
    print(f"{t['date']} {t['time']} {t['sym']:5} {t['dir']:5} entry={t['entry']:.2f} {t['out']:4} R={t['R']:+.2f} rvol={t['rvol']:.2f}")
