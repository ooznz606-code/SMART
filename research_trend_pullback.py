# -*- coding: utf-8 -*-
from collections import defaultdict
from analyzer_bc_core import SYMBOLS, CHART_DIR, load_symbol_candles

EXCLUDE = {"COST","UBER","TSLA"}
ADX_MIN = 20
RVOL_MIN = 1.0
TARGET_R = 1.5
MAX_TRADES_DAY = 2

def ema(vals, n):
    if len(vals) < n: return vals[-1]
    k=2/(n+1); e=sum(vals[:n])/n
    for v in vals[n:]: e=v*k+e*(1-k)
    return e

def atr(bars, n=14):
    if len(bars)<n+1: return 0
    trs=[]
    for i in range(-n,0):
        h,l,pc=bars[i].high,bars[i].low,bars[i-1].close
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    return sum(trs)/len(trs)

def rvol(bars, i):
    if i < 21: return 1
    avg=sum(b.volume for b in bars[i-20:i])/20
    return bars[i].volume/avg if avg else 1

def trend(bars, i):
    closes=[b.close for b in bars[:i+1]]
    if len(closes)<50: return None
    e20=ema(closes,20); e50=ema(closes,50)
    price=closes[-1]
    if price>e20>e50: return "LONG", e20
    if price<e20<e50: return "SHORT", e20
    return None

def simulate(bars, i, direction, entry, stop, target):
    for b in bars[i+1:i+41]:
        if direction=="LONG":
            if b.low<=stop: return "LOSS",-1
            if b.high>=target: return "WIN",TARGET_R
        else:
            if b.high>=stop: return "LOSS",-1
            if b.low<=target: return "WIN",TARGET_R
    return "BE",0

trades=[]
for sym in SYMBOLS:
    if sym in EXCLUDE: continue
    data=load_symbol_candles(sym, CHART_DIR)
    if not data: continue
    c15,c1h=data
    byday=defaultdict(int)

    for i in range(60,len(c15)-41):
        b=c15[i]
        d=str(b.timestamp.date())
        if byday[d] >= MAX_TRADES_DAY: continue

        tr=trend(c15,i)
        if not tr: continue
        direction,e20=tr
        a=atr(c15[:i+1])
        if a<=0: continue
        rv=rvol(c15,i)
        if rv<RVOL_MIN: continue

        # ADX proxy بسيط: قوة ميل EMA20 خلال 10 شمعات
        closes=[x.close for x in c15[:i+1]]
        e20_now=ema(closes,20)
        e20_prev=ema(closes[:-10],20) if len(closes)>60 else e20_now
        strength=abs(e20_now-e20_prev)/a
        if strength < 0.35: continue

        # Pullback to EMA20 zone
        if direction=="LONG":
            touched=b.low <= e20 + 0.25*a and b.close > e20 and b.close > b.open
            if not touched: continue
            entry=b.close
            stop=min(x.low for x in c15[max(0,i-5):i+1]) - 0.1*a
            risk=entry-stop
            if risk<=0: continue
            target=entry+risk*TARGET_R
        else:
            touched=b.high >= e20 - 0.25*a and b.close < e20 and b.close < b.open
            if not touched: continue
            entry=b.close
            stop=max(x.high for x in c15[max(0,i-5):i+1]) + 0.1*a
            risk=stop-entry
            if risk<=0: continue
            target=entry-risk*TARGET_R

        out,R=simulate(c15,i,direction,entry,stop,target)
        trades.append(dict(date=d,time=b.timestamp.strftime("%H:%M"),sym=sym,dir=direction,entry=entry,out=out,R=R,rvol=rv,strength=strength))
        byday[d]+=1

W=sum(1 for t in trades if t["out"]=="WIN")
L=sum(1 for t in trades if t["out"]=="LOSS")
BE=sum(1 for t in trades if t["out"]=="BE")
N=len(trades)
total=sum(t["R"] for t in trades)
pf=(sum(t["R"] for t in trades if t["R"]>0)/abs(sum(t["R"] for t in trades if t["R"]<0))) if L else 999
wr=W/max(1,W+L)*100

print("="*90)
print("Daily Trend Pullback Research")
print("="*90)
print(f"N={N} W={W} L={L} BE={BE} WR={wr:.1f}% PF={pf:.2f} TotalR={total:+.2f}")
print("-"*90)
for t in trades[-80:]:
    print(f"{t['date']} {t['time']} {t['sym']:5} {t['dir']:5} entry={t['entry']:.2f} {t['out']:4} R={t['R']:+.2f} rvol={t['rvol']:.2f} strength={t['strength']:.2f}")
