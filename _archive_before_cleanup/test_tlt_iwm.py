# -*- coding: utf-8 -*-
import warnings, json, itertools
warnings.filterwarnings("ignore")
import yfinance as yf, pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
from analyzer_x2 import Candle, Direction, _atr, _adx_calc, _detect_sweep, _detect_displacement, _detect_fvg, _detect_order_block

DAYS=55; TP1_R=2.0; MAX_WAIT=35; MAX_HOLD=30

def _flat(df):
    if df is not None and isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def _to_c(df):
    return [Candle(open=float(r["Open"]),high=float(r["High"]),low=float(r["Low"]),
                   close=float(r["Close"]),volume=float(r.get("Volume",0)),
                   timestamp=ts.to_pydatetime()) for ts,r in df.iterrows()]

def _htf(c1h):
    if not c1h or len(c1h)<55: return Direction.NEUTRAL
    closes=[c.close for c in c1h]
    k=2.0/22; e=sum(closes[:21])/21
    for v in closes[21:]: e=v*k+e*(1-k)
    e21=e
    k=2.0/51; e=sum(closes[:50])/50
    for v in closes[50:]: e=v*k+e*(1-k)
    e50=e; p=closes[-1]
    if p>e21>e50: return Direction.LONG
    if p<e21<e50: return Direction.SHORT
    return Direction.NEUTRAL

def _run(c15, c1h, tf, mna, mxa, msc):
    day_res=defaultdict(list); pending=None; wb=0; ltb=-99
    for i in range(150, len(c15)-MAX_HOLD-1):
        ts=c15[i].timestamp; ch=[c for c in c1h if c.timestamp<=ts]
        candles=(c15[:i+1][-200:] if tf=="15m" else (ch[-300:] if len(ch)>=55 else c15[:i+1][-200:]))
        if len(candles)<60: continue
        atr=_atr(candles,14); adx=_adx_calc(candles,14); price=c15[i].close
        htf=_htf(ch[-300:] if len(ch)>=55 else [])
        if adx<mna or adx>mxa:
            if pending:
                wb+=1
                if wb>MAX_WAIT: pending=None; wb=0
            continue
        if pending:
            wb+=1; zt=pending["zt"]; zb=pending["zb"]; d=pending["d"]
            if (d==Direction.LONG and htf==Direction.SHORT) or (d==Direction.SHORT and htf==Direction.LONG):
                pending=None; wb=0; continue
            bar=c15[i]
            if bar.low<=zt and bar.high>=zb:
                rej=(bar.close>bar.open if d==Direction.LONG else bar.close<bar.open)
                if rej and (i-ltb)>=12:
                    entry=bar.close; stop=(zb-atr*0.22 if d==Direction.LONG else zt+atr*0.22)
                    risk=abs(entry-stop)
                    if risk>=atr*0.20 and risk>0:
                        tp1=(entry+risk*TP1_R if d==Direction.LONG else entry-risk*TP1_R)
                        out="BE"
                        for fc in c15[i+1:i+1+MAX_HOLD+1]:
                            if d==Direction.LONG:
                                if fc.low<=stop: out="LOSS"; break
                                if fc.high>=tp1: out="WIN"; break
                            else:
                                if fc.high>=stop: out="LOSS"; break
                                if fc.low<=tp1: out="WIN"; break
                        day_res[bar.timestamp.date()].append(out)
                        ltb=i; pending=None; wb=0; continue
            if wb>MAX_WAIT: pending=None; wb=0
            continue
        if (i-ltb)<10 or htf==Direction.NEUTRAL: continue
        d=htf
        sw=_detect_sweep(candles,d,atr); dp=_detect_displacement(candles,d,atr)
        fvg=_detect_fvg(candles,d,atr); ob=_detect_order_block(candles,d,atr)
        if not ((sw or dp) and (fvg or ob)): continue
        sc=0
        if sw: sc+=28
        if dp: sc+=22
        if fvg: sc+=22
        if ob: sc+=13
        sc+=15; sc+=(5 if mna<=adx<=mxa else 0)
        if sc<msc: continue
        zone=None
        for z in ([fvg,ob] if d==Direction.LONG else [ob,fvg]):
            if z is None: continue
            if d==Direction.LONG and price>z.top: zone=(z.top,z.bottom); break
            if d==Direction.SHORT and price<z.bottom: zone=(z.top,z.bottom); break
        if not zone: continue
        pending={"d":d,"zt":zone[0],"zb":zone[1]}; wb=0
    return day_res

def grid_search(c15, c1h):
    best={"score":-1,"dr":{}}
    for tf,mna,mxa,msc in itertools.product(["15m","1H"],[15,18,22,25,28],[45,55,65],[40,50,60]):
        if mna>=mxa: continue
        dr=_run(c15,c1h,tf,mna,mxa,msc)
        if not dr: continue
        days=sorted(dr.keys())
        win_d=sum(1 for d in days if "WIN" in dr[d])
        all_o=[o for v in dr.values() for o in v]
        w=all_o.count("WIN"); l=all_o.count("LOSS"); be=all_o.count("BE")
        tpd=round(len(all_o)/len(days),2)
        pct=round(win_d/len(days)*100,1)
        if tpd<0.15: continue
        sc=pct*100+tpd
        if sc>best["score"]:
            wr=round(w/(w+l)*100,1) if (w+l)>0 else 0
            best={"score":sc,"pct":pct,"win_d":win_d,"tot":len(days),"tpd":tpd,
                  "w":w,"l":l,"be":be,"wr":wr,"tf":tf,"mna":mna,"mxa":mxa,"msc":msc,"dr":dr}
    return best

# الرموز الحالية + TLT و IWM
CURRENT_CONFIGS = {
    "QQQ":   ("15m", 22, 55, 40),
    "MSFT":  ("15m", 25, 45, 40),
    "META":  ("15m", 22, 65, 40),
    "AMZN":  ("15m", 22, 65, 40),
    "NFLX":  ("1H",  15, 65, 40),
    "GOOGL": ("15m", 25, 55, 40),
    "SPY":   ("15m", 22, 65, 40),
    "TSLA":  ("1H",  15, 45, 40),
}
NEW_SYMS = ["TLT", "IWM"]

end=datetime.today(); start=end-timedelta(days=DAYS)
print("Downloading all symbols...")
data={}
for sym in list(CURRENT_CONFIGS.keys()) + NEW_SYMS:
    df15=_flat(yf.download(sym,start=start,end=end,interval="15m",progress=False,auto_adjust=True))
    df1h=_flat(yf.download(sym,start=start,end=end,interval="1h",progress=False,auto_adjust=True))
    data[sym]={"c15":_to_c(df15),"c1h":_to_c(df1h) if df1h is not None and len(df1h)>20 else []}

print("\nGrid search for TLT and IWM...")
new_best={}
for sym in NEW_SYMS:
    b=grid_search(data[sym]["c15"],data[sym]["c1h"])
    new_best[sym]=b
    if b["score"]>0:
        print(f"  {sym}: {b['pct']}% wd | {b['win_d']}/{b['tot']} days | WR={b['wr']}% ({b['w']}W/{b['l']}L/{b['be']}BE) | tf={b['tf']} adx={b['mna']}-{b['mxa']} conf={b['msc']}")
    else:
        print(f"  {sym}: no signals")

# بناء CONFIGS الجديدة: استبدل TSLA و NFLX (الأضعف) بـ TLT و IWM
NEW_CONFIGS = dict(CURRENT_CONFIGS)
for sym in ["TSLA", "NFLX"]:
    NEW_CONFIGS.pop(sym, None)
for sym in NEW_SYMS:
    b=new_best[sym]
    if b["score"]>0:
        NEW_CONFIGS[sym]=(b["tf"],b["mna"],b["mxa"],b["msc"])
    else:
        NEW_CONFIGS[sym]=("15m",22,65,40)

print("\n--- Combined backtest (QQQ MSFT META AMZN GOOGL SPY + TLT IWM) ---")
merged=defaultdict(list)
sym_dr={}
for sym,cfg in NEW_CONFIGS.items():
    tf,mna,mxa,msc=cfg
    dr=_run(data[sym]["c15"],data[sym]["c1h"],tf,mna,mxa,msc)
    sym_dr[sym]=dr
    for d,v in dr.items(): merged[d].extend(v)

print(f"\n  {'Symbol':<7} {'wd%':>6}  {'W/T':>6}  {'WR%':>5}")
print("  "+"-"*35)
for sym,cfg in NEW_CONFIGS.items():
    dr=sym_dr[sym]; days=sorted(dr.keys())
    if not days: print(f"  {sym:<7}   ---"); continue
    win_d=sum(1 for d in days if "WIN" in dr[d])
    all_o=[o for v in dr.values() for o in v]
    w=all_o.count("WIN"); l=all_o.count("LOSS")
    wr=round(w/(w+l)*100,1) if (w+l)>0 else 0
    pct=round(win_d/len(days)*100,1)
    print(f"  {sym:<7} {pct:>5.1f}%  {win_d}/{len(days):>2}  {wr:>4.0f}%")

print("  "+"-"*35)
days_m=sorted(merged.keys())
win_m=sum(1 for d in days_m if "WIN" in merged[d])
all_m=[o for v in merged.values() for o in v]
wm=all_m.count("WIN"); lm=all_m.count("LOSS"); bm=all_m.count("BE")
tpd=round(len(all_m)/len(days_m),2)
pct_m=round(win_m/len(days_m)*100,1)
wr_m=round(wm/(wm+lm)*100,1) if (wm+lm)>0 else 0
print(f"  {'COMBINED':<7} {pct_m:>5.1f}%  {win_m}/{len(days_m)}  {wr_m:>4.0f}%  | {tpd} t/d | {wm}W/{lm}L/{bm}BE")

if pct_m >= 76.9:
    print(f"\n  --- تحسّن! ({pct_m}% vs 72.4% سابقاً) ---")
    # حفظ
    with open("symbol_profiles_x2.json","r",encoding="utf-8") as f:
        profiles=json.load(f)
    for rm in ["TSLA","NFLX"]:
        profiles.pop(rm,None)
    for sym,cfg in NEW_CONFIGS.items():
        tf,mna,mxa,msc=cfg
        dr=sym_dr[sym]; all_o=[o for v in dr.values() for o in v]
        w=all_o.count("WIN"); l=all_o.count("LOSS")
        wr=round(w/(w+l)*100,1) if (w+l)>0 else 0
        days=sorted(dr.keys()); win_d=sum(1 for d in days if "WIN" in dr[d])
        pct=round(win_d/len(days)*100,1) if days else 0
        profiles[sym]={
            "enabled":True,"timeframe":tf,"min_conf":msc,
            "min_adx":mna,"max_adx":mxa,"max_daily_signals":1,
            "notes":f"{tf}: {w}W {l}L {wr}% WR | {pct}% wd ({win_d}/{len(days)} days)"
        }
    with open("symbol_profiles_x2.json","w",encoding="utf-8") as f:
        json.dump(profiles,f,indent=2,ensure_ascii=False)
    print("  symbol_profiles_x2.json saved.")
else:
    print(f"\n  لم يتحسن ({pct_m}% < 72.4%) - الملف لم يتغير.")
