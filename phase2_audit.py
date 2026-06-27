import os, json, sys
from datetime import datetime
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

RESEARCH_DIR = 'chart_data_research'
ORB_ADX_MIN        = 30.0
ORB_RVOL_MIN       = 1.5
ORB_BODY_ATR       = 0.25
ORB_RANGE_ATR_MIN  = 2.0
ORB_EMA20_DIST_MIN = 1.95
ORB_BREAK_DIST_MIN = 0.05
ORB_MAX_DIR_PER_DAY = 2
TOP_N_DAY          = 3
SESS_OPEN     = 240
SESS_ORB_DONE = 270
SESS_BRK_END  = 390
MIN_LB        = 60
SL_ATR        = 1.5
TP_ATR        = 2.7
MAX_HOLD      = 40
SCAN_SYMS = ['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ']

class Bar:
    __slots__ = ('timestamp','open','high','low','close','volume')
    def __init__(self, ts, o, h, l, c, v):
        self.timestamp=ts; self.open=o; self.high=h; self.low=l; self.close=c; self.volume=v

def load_bars(sym):
    path = os.path.join(RESEARCH_DIR, f'{sym}_15m.json')
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    times=d['times']; ops=d['opens']; his=d['highs']
    los=d['lows']; cls=d['closes']; vls=d.get('volumes',[0]*len(times))
    bars=[]
    for i in range(min(len(times),len(ops),len(his),len(los),len(cls))):
        dt=datetime.strptime(times[i][:16].replace('T',' '),'%Y-%m-%d %H:%M')
        vol=float(vls[i]) if i<len(vls) else 0.0
        bars.append(Bar(dt,float(ops[i]),float(his[i]),float(los[i]),float(cls[i]),vol))
    return bars

def _sm(ts):
    return (ts.hour-9)*60+ts.minute-30

def _ema(v,p):
    k=2.0/(p+1); r=[v[0]]
    for x in v[1:]:
        r.append(r[-1]+k*(x-r[-1]))
    return r

def _wilder(v,p):
    k=1.0/p; r=[v[0]]
    for x in v[1:]:
        r.append(r[-1]+k*(x-r[-1]))
    return r

def _atr(bars,p=14):
    tr=[bars[0].high-bars[0].low]
    for i in range(1,len(bars)):
        h,l,pc=bars[i].high,bars[i].low,bars[i-1].close
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    return _wilder(tr,p)

def _adx(bars,p=14):
    n=len(bars)
    if n<p+2: return [0.0]*n
    pdm,mdm,tr=[],[],[]
    for i in range(1,n):
        h,l=bars[i].high,bars[i].low
        ph,pl,pc=bars[i-1].high,bars[i-1].low,bars[i-1].close
        up,dn=h-ph,pl-l
        pdm.append(up if up>dn and up>0 else 0.0)
        mdm.append(dn if dn>up and dn>0 else 0.0)
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    a=_wilder(tr,p); pd_=_wilder(pdm,p); md=_wilder(mdm,p); dx=[]
    for ai,pi,mi in zip(a,pd_,md):
        pdi=100*pi/ai if ai>0 else 0.0
        mdi=100*mi/ai if ai>0 else 0.0
        dx.append(100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi>0 else 0.0)
    return [0.0]+_wilder(dx,p)

def _rvol(vols,p=20):
    out=[1.0]*p
    for i in range(p,len(vols)):
        avg=sum(vols[i-p:i])/p
        out.append(vols[i]/avg if avg>0 else 1.0)
    return out

def build_bias(spy,qqq):
    bias={}
    for bars in (spy,qqq):
        if not bars: continue
        cl=[b.close for b in bars]; e9=_ema(cl,9); e20=_ema(cl,20)
        for i,b in enumerate(bars):
            bull=e9[i]>e20[i]; prev=bias.get(b.timestamp)
            if prev is None:
                bias[b.timestamp]='BULL' if bull else 'BEAR'
            elif (prev=='BULL')==bull:
                pass
            else:
                bias[b.timestamp]='NEUTRAL'
    return bias

def simulate(bars,idx,direction,entry,stop,tp1):
    risk=abs(entry-stop)
    if risk<=0: return 0.0
    for j in range(idx+1,min(idx+1+MAX_HOLD,len(bars))):
        b=bars[j]
        if direction=='LONG':
            if b.high>=tp1:  return (tp1-entry)/risk
            if b.low<=stop:  return (stop-entry)/risk
        else:
            if b.low<=tp1:   return (entry-tp1)/risk
            if b.high>=stop: return (entry-stop)/risk
    j=min(idx+MAX_HOLD,len(bars)-1)
    if direction=='LONG':
        return (bars[j].close-entry)/risk
    else:
        return (entry-bars[j].close)/risk

def scan(sym,bars,bias_map):
    n=len(bars)
    cl=[b.close for b in bars]; vol=[b.volume for b in bars]
    atrs=_atr(bars,14); adxs=_adx(bars,14)
    rvs=_rvol(vol,20); ema20s=_ema(cl,20)
    orb={}; emitted=set(); raw=[]; rej=defaultdict(int)

    for i in range(MIN_LB,n):
        b=bars[i]; ts=b.timestamp; sm=_sm(ts); dt=str(ts.date())

        if SESS_OPEN<=sm<SESS_ORB_DONE:
            if dt not in orb:
                orb[dt]=[b.high,b.low,False]
            else:
                orb[dt][0]=max(orb[dt][0],b.high)
                orb[dt][1]=min(orb[dt][1],b.low)
        elif dt in orb and not orb[dt][2] and sm>=SESS_ORB_DONE:
            orb[dt][2]=True

        if sm<SESS_ORB_DONE or sm>=SESS_BRK_END: continue
        if dt not in orb or not orb[dt][2]:
            rej['no_orb']+=1; continue

        atr=atrs[i]
        if atr<=0: continue
        oh,ol,_=orb[dt]
        adx=adxs[i]; rv=rvs[i]; bias=bias_map.get(ts,'NEUTRAL')
        body=abs(b.close-b.open); e20=ema20s[i]
        sc_mult=1.3 if bias!='NEUTRAL' else 1.0

        # Common filters (count once per bar)
        if adx<ORB_ADX_MIN:
            rej['adx']+=1; continue
        if rv<ORB_RVOL_MIN:
            rej['rvol']+=1; continue
        if body<ORB_BODY_ATR*atr:
            rej['body']+=1; continue
        if (oh-ol)/atr<ORB_RANGE_ATR_MIN:
            rej['orb_range']+=1; continue

        # LONG direction
        if (dt,'LONG') not in emitted:
            if b.close>oh and b.close>b.open:
                if bias=='BEAR':
                    rej['counter_bias']+=1
                elif (b.close-e20)/atr<ORB_EMA20_DIST_MIN:
                    rej['ema20_dist']+=1
                elif (b.close-oh)/atr<ORB_BREAK_DIST_MIN:
                    rej['f3_break']+=1
                else:
                    raw.append(dict(i=i,dt=dt,dir='LONG',entry=b.close,
                        stop=b.close-SL_ATR*atr,tp1=b.close+TP_ATR*atr,
                        score=adx*rv*sc_mult))
                    emitted.add((dt,'LONG'))
            else:
                rej['no_breakout']+=1

        # SHORT direction
        if (dt,'SHORT') not in emitted:
            if b.close<ol and b.close<b.open:
                if bias=='BULL':
                    rej['counter_bias']+=1
                elif (e20-b.close)/atr<ORB_EMA20_DIST_MIN:
                    rej['ema20_dist']+=1
                elif (ol-b.close)/atr<ORB_BREAK_DIST_MIN:
                    rej['f3_break']+=1
                elif sym=='MSFT' and bias=='NEUTRAL':
                    rej['f4_msft']+=1
                else:
                    raw.append(dict(i=i,dt=dt,dir='SHORT',entry=b.close,
                        stop=b.close+SL_ATR*atr,tp1=b.close-TP_ATR*atr,
                        score=adx*rv*sc_mult))
                    emitted.add((dt,'SHORT'))

    # TOP_N_DAY + F2
    by_date=defaultdict(list)
    for s in raw: by_date[s['dt']].append(s)
    final=[]
    for dt,day in sorted(by_date.items()):
        day.sort(key=lambda x:x['score'],reverse=True)
        dc=defaultdict(int)
        for s in day[:TOP_N_DAY]:
            if dc[s['dir']]<ORB_MAX_DIR_PER_DAY:
                dc[s['dir']]+=1
                final.append(s)
    return final,rej

# ── main ──────────────────────────────────────────────────────────────────────
spy=load_bars('SPY'); qqq=load_bars('QQQ')
bias_map=build_bias(spy,qqq)

print()
print('Phase 2 -- Allowed Symbol Audit')
print('Dataset : chart_data_research/  ({} to {}, {} bars per symbol)'.format(
    spy[0].timestamp.date(), spy[-1].timestamp.date(), len(spy)))
print('Note    : Full dataset used -- IS/OOS split not yet locked (Phase 1 pending).')
print()

ROW = '{:6s}  {:>4s}  {:>5s}  {:>5s}  {:>7s}  {:>6s}  {:24s}  {}'
print(ROW.format('SYM','SIG','W%','PF','TotalR','MaxDD','TOP REJECTION','COMMENT'))
print('-'*100)

for sym in SCAN_SYMS:
    bars=load_bars(sym)
    signals,rej=scan(sym,bars,bias_map)
    r_list=[]
    for s in signals:
        r=simulate(bars,s['i'],s['dir'],s['entry'],s['stop'],s['tp1'])
        r_list.append(r)
    n=len(r_list)
    if n==0:
        print(ROW.format(sym,'0','n/a','n/a','n/a','n/a','n/a','no signals fired'))
        continue
    wins=sum(1 for r in r_list if r>0)
    gw=sum(r for r in r_list if r>0)
    gl=sum(abs(r) for r in r_list if r<0)
    pf=gw/gl if gl>0 else 9.99
    totalr=sum(r_list)
    eq=0.0; pk=0.0; mdd=0.0
    for r in r_list:
        eq+=r
        if eq>pk: pk=eq
        if pk-eq>mdd: mdd=pk-eq
    # no_orb is a DST system artifact (identical across all symbols); show real filter rejection
    rej_filt={k:v for k,v in rej.items() if k!='no_orb'}
    top_rej=max(rej_filt,key=rej_filt.get) if rej_filt else 'n/a'
    top_n=rej_filt.get(top_rej,0)
    wr=100.0*wins/n
    if   pf>=1.5 and totalr>0 and n>=5:   comment='good -- keep'
    elif pf>=1.0 and totalr>0 and n>=5:   comment='marginal positive'
    elif n<5:                              comment='too few signals -- inconclusive'
    elif pf<0.8:                           comment='losing -- review'
    else:                                  comment='breakeven'
    label='{} ({})'.format(top_rej,top_n)
    print(ROW.format(sym,str(n),'{:.1f}%'.format(wr),'{:.2f}'.format(pf),
        '{:+.2f}R'.format(totalr),'{:.2f}R'.format(mdd),label,comment))

print()
print('Rejection keys:')
print('  adx          = ADX < 30.0')
print('  rvol         = RVOL < 1.5')
print('  body         = candle body < 0.25 ATR')
print('  orb_range    = ORB range < 2.0 ATR')
print('  counter_bias = breakout against SPY+QQQ bias')
print('  ema20_dist   = price < 1.95 ATR from EMA20')
print('  f3_break     = breakout distance < 0.05 ATR')
print('  no_breakout  = price did not cross ORB level in window')
print('  no_orb       = ORB range not formed (DST gap: EST months have no ORB data)')
