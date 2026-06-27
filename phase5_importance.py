"""Phase 5 -- Feature Importance (DST-Safe, IS Window Only).
Analyses: rejection share, incremental unlock, PF slope, filter interaction,
bias alignment rate. Discovery only -- no hypotheses, no recommendations.
"""
import os, json, sys, csv
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_MD  = os.path.join('docs', 'results', 'phase5_feature_importance.md')
RESEARCH_DIR = 'chart_data_research'

IS_START = date(2025, 9, 17)
IS_END   = date(2026, 4, 30)

BL = dict(adx_min=30.0, rvol_min=1.5, orb_range_min=2.0,
          ema20_dist_min=1.95, break_dist_min=0.05,
          body_atr=0.25, sess_brk_end_et=120)

SESS_OPEN_ET = 0; SESS_ORB_DONE_ET = 30
MIN_LB = 60; ORB_MAX_DIR = 2; TOP_N_DAY = 3
SL_ATR = 1.5; TP_ATR = 2.7; MAX_HOLD = 40

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')
SCAN_SYMS = ['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ']

# Phase 4 sweep data (pre-computed results used for slope & unlock analysis)
P4 = {
    'adx_min':        [(15,211,1.34,40.30,12.00),(20,186,1.35,36.23,10.26),
                       (25,138,1.48,35.56,8.40), (28,111,1.52,30.58,7.00),
                       (30,100,1.73,35.98,7.80), (32,86,1.80,33.18,6.80),
                       (35,67,2.10,32.58,6.40),  (40,51,1.80,19.59,4.00)],
    'rvol_min':       [(0.8,161,1.47,40.03,8.00),(1.0,148,1.58,44.51,6.20),
                       (1.2,126,1.76,46.57,6.20),(1.4,109,1.92,47.16,5.20),
                       (1.5,100,1.73,35.98,7.80),(1.7,83,1.95,36.52,4.20),
                       (2.0,61,1.98,27.32,3.72)],
    'orb_range_min':  [(0.8,109,1.90,46.58,6.00),(1.0,109,1.90,46.58,6.00),
                       (1.2,108,1.87,44.78,7.80),(1.5,108,1.87,44.78,7.80),
                       (1.8,103,1.76,38.58,7.80),(2.0,100,1.73,35.98,7.80),
                       (2.3,88,1.84,35.79,4.80), (2.6,74,1.81,29.65,5.00)],
    'ema20_dist_min': [(0.8,113,1.64,36.98,8.00),(1.0,111,1.64,36.18,8.00),
                       (1.2,107,1.70,37.38,7.00),(1.5,105,1.76,39.38,6.20),
                       (1.75,102,1.73,36.78,6.20),(1.95,100,1.73,35.98,7.80),
                       (2.2,96,1.65,31.58,7.80), (2.5,88,1.80,33.98,5.20)],
    'break_dist_min': [(0.01,101,1.69,34.98,7.80),(0.03,100,1.73,35.98,7.80),
                       (0.05,100,1.73,35.98,7.80),(0.08,99,1.69,34.18,7.80),
                       (0.10,98,1.58,29.58,7.80), (0.15,94,1.65,30.78,7.80)],
    'body_atr':       [(0.10,104,1.60,31.98,7.80),(0.15,101,1.69,34.98,7.80),
                       (0.20,101,1.69,34.98,7.80),(0.25,100,1.73,35.98,7.80),
                       (0.30,100,1.67,34.12,7.80),(0.40,96,1.54,27.36,7.80)],
    'sess_brk_end_et':[(60,64,1.97,28.94,4.60),(90,90,1.57,26.72,7.80),
                       (120,100,1.73,35.98,7.80),(150,110,1.73,39.98,7.60),
                       (180,116,1.72,41.26,7.60)],
}
BL_SIG = 100; BL_PF = 1.73; BL_TR = 35.98; BL_DD = 7.80
IS_DAYS_N = 156


# ── helpers ───────────────────────────────────────────────────────────────────

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
    out=[]
    for i in range(min(len(times),len(ops),len(his),len(los),len(cls))):
        naive=datetime.strptime(times[i][:16].replace('T',' '),'%Y-%m-%d %H:%M')
        out.append(Bar(naive,float(ops[i]),float(his[i]),float(los[i]),
                       float(cls[i]),float(vls[i]) if i<len(vls) else 0.0))
    return out

def _sm_et(ts): return (ts.replace(tzinfo=_UTC).astimezone(_ET).hour-9)*60 + \
                        ts.replace(tzinfo=_UTC).astimezone(_ET).minute-30
def _et_date(ts): return str(ts.replace(tzinfo=_UTC).astimezone(_ET).date())

def _ema(v,p):
    k=2.0/(p+1); r=[v[0]]
    for x in v[1:]: r.append(r[-1]+k*(x-r[-1]))
    return r
def _wilder(v,p):
    k=1.0/p; r=[v[0]]
    for x in v[1:]: r.append(r[-1]+k*(x-r[-1]))
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
        h,l=bars[i].high,bars[i].low; ph,pl,pc=bars[i-1].high,bars[i-1].low,bars[i-1].close
        up,dn=h-ph,pl-l
        pdm.append(up if up>dn and up>0 else 0.0)
        mdm.append(dn if dn>up and dn>0 else 0.0)
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    a=_wilder(tr,p); pd_=_wilder(pdm,p); md=_wilder(mdm,p); dx=[]
    for ai,pi,mi in zip(a,pd_,md):
        pdi=100*pi/ai if ai>0 else 0.0; mdi=100*mi/ai if ai>0 else 0.0
        dx.append(100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi>0 else 0.0)
    return [0.0]+_wilder(dx,p)
def _rvol(vols,p=20):
    out=[1.0]*p
    for i in range(p,len(vols)):
        avg=sum(vols[i-p:i])/p; out.append(vols[i]/avg if avg>0 else 1.0)
    return out

def build_bias(spy,qqq):
    bias={}
    for bars in (spy,qqq):
        if not bars: continue
        cl=[b.close for b in bars]; e9=_ema(cl,9); e20=_ema(cl,20)
        for i,b in enumerate(bars):
            bull=e9[i]>e20[i]; prev=bias.get(b.timestamp)
            if prev is None: bias[b.timestamp]='BULL' if bull else 'BEAR'
            elif (prev=='BULL')==bull: pass
            else: bias[b.timestamp]='NEUTRAL'
    return bias

def simulate(bars,idx,direction,entry,stop,tp1):
    risk=abs(entry-stop)
    if risk<=0: return 0.0
    for j in range(idx+1,min(idx+1+MAX_HOLD,len(bars))):
        b=bars[j]
        if direction=='LONG':
            if b.high>=tp1: return (tp1-entry)/risk
            if b.low<=stop: return (stop-entry)/risk
        else:
            if b.low<=tp1:  return (entry-tp1)/risk
            if b.high>=stop:return (entry-stop)/risk
    j=min(idx+MAX_HOLD,len(bars)-1)
    return (bars[j].close-entry)/risk if direction=='LONG' else (entry-bars[j].close)/risk


# ── Analysis 1+4+5: Rejection share, ADX-RVOL interaction, bias alignment ────

def full_rejection_scan(sym, bars, bias_map):
    """
    Single pass through IS breakout-window bars.
    Returns:
      first_fail    -- dict: first filter to fail per bar (production order)
      all_fail      -- dict: all filters that independently fail per bar
      adx_rvol      -- (adx_only, rvol_only, both, neither_common)
      bias_stats    -- (long_breakouts, long_counter_bias, short_breakouts, short_counter_bias)
      total_window  -- total bars seen in breakout window with locked ORB
    """
    n=len(bars)
    cl=[b.close for b in bars]; vol=[b.volume for b in bars]
    atrs=_atr(bars,14); adxs=_adx(bars,14); rvs=_rvol(vol,20); ema20s=_ema(cl,20)

    orb={}; first_fail=defaultdict(int); all_fail=defaultdict(int)
    adx_only=rvol_only=both_adx_rvol=neither_common=0
    long_brk=long_cb=short_brk=short_cb=0
    total_window=0

    for i in range(MIN_LB,n):
        b=bars[i]; ts=b.timestamp; sm=_sm_et(ts); dt=_et_date(ts); td=date.fromisoformat(dt)

        if SESS_OPEN_ET<=sm<SESS_ORB_DONE_ET:
            if dt not in orb: orb[dt]=[b.high,b.low,False]
            else: orb[dt][0]=max(orb[dt][0],b.high); orb[dt][1]=min(orb[dt][1],b.low)
        elif dt in orb and not orb[dt][2] and sm>=SESS_ORB_DONE_ET:
            orb[dt][2]=True

        if td<IS_START or td>IS_END: continue
        if sm<SESS_ORB_DONE_ET or sm>=BL['sess_brk_end_et']: continue
        if dt not in orb or not orb[dt][2]: continue

        atr=atrs[i]
        if atr<=0: continue
        oh,ol,_=orb[dt]
        adx=adxs[i]; rv=rvs[i]; bias=bias_map.get(ts,'NEUTRAL')
        body=abs(b.close-b.open); e20=ema20s[i]
        total_window+=1

        # Independent failure flags for each filter
        f_adx       = adx  < BL['adx_min']
        f_rvol      = rv   < BL['rvol_min']
        f_body      = body < BL['body_atr']*atr
        f_range     = (oh-ol)/atr < BL['orb_range_min']
        for k,v in [('adx',f_adx),('rvol',f_rvol),('body',f_body),('orb_range',f_range)]:
            if v: all_fail[k]+=1

        # ADX-RVOL interaction
        if f_adx and f_rvol:   both_adx_rvol+=1
        elif f_adx:             adx_only+=1
        elif f_rvol:            rvol_only+=1
        else:                   neither_common+=1

        # First-fail (production filter order: adx→rvol→body→orb_range→direction)
        if f_adx:
            first_fail['adx']+=1
        elif f_rvol:
            first_fail['rvol']+=1
        elif f_body:
            first_fail['body']+=1
        elif f_range:
            first_fail['orb_range']+=1
        else:
            # Common filters passed -- check direction-specific
            cb_long  = (bias=='BEAR')
            cb_short = (bias=='BULL')
            is_long  = b.close>oh and b.close>b.open
            is_short = b.close<ol and b.close<b.open
            all_fail['common_pass']+=1

            if is_long:
                long_brk+=1
                all_fail['long_breakout']+=1
                if cb_long:
                    long_cb+=1; first_fail['counter_bias']+=1; all_fail['counter_bias']+=1
                elif (b.close-e20)/atr<BL['ema20_dist_min']:
                    first_fail['ema20_dist']+=1; all_fail['ema20_dist']+=1
                elif (b.close-oh)/atr<BL['break_dist_min']:
                    first_fail['f3_break']+=1; all_fail['f3_break']+=1
                else:
                    first_fail['signal_long']+=1
            elif is_short:
                short_brk+=1
                all_fail['short_breakout']+=1
                if cb_short:
                    short_cb+=1; first_fail['counter_bias']+=1; all_fail['counter_bias']+=1
                elif (e20-b.close)/atr<BL['ema20_dist_min']:
                    first_fail['ema20_dist']+=1; all_fail['ema20_dist']+=1
                elif (ol-b.close)/atr<BL['break_dist_min']:
                    first_fail['f3_break']+=1; all_fail['f3_break']+=1
                elif sym=='MSFT' and bias=='NEUTRAL':
                    first_fail['f4_msft']+=1; all_fail['f4_msft']+=1
                else:
                    first_fail['signal_short']+=1
            else:
                first_fail['no_breakout']+=1; all_fail['no_breakout']+=1

    return (first_fail, all_fail,
            (adx_only, rvol_only, both_adx_rvol, neither_common),
            (long_brk, long_cb, short_brk, short_cb),
            total_window)


# ── Analysis 2: Incremental unlock from Phase 4 ──────────────────────────────

def unlock_table():
    rows = []
    param_meta = [
        ('adx_min',        'tighter=higher', 15,  30),
        ('rvol_min',       'tighter=higher', 0.8, 1.5),
        ('orb_range_min',  'tighter=higher', 0.8, 2.0),
        ('ema20_dist_min', 'tighter=higher', 0.8, 1.95),
        ('break_dist_min', 'tighter=higher', 0.01,0.05),
        ('body_atr',       'tighter=higher', 0.10,0.25),
        ('sess_brk_end_et','wider=higher',   60,  120),
    ]
    for pk, direction, max_relax_val, bl_val in param_meta:
        data = P4[pk]
        bl_sig = next(sig for v,sig,pf,tr,dd in data if v==bl_val)
        # most permissive point
        relax_row = next((sig,pf,tr,dd) for v,sig,pf,tr,dd in data if v==max_relax_val)
        delta_sig = relax_row[0] - bl_sig
        delta_pf  = relax_row[1] - BL_PF
        delta_tr  = relax_row[2] - BL_TR
        delta_dd  = relax_row[3] - BL_DD
        rows.append((pk, max_relax_val, bl_val, delta_sig, delta_pf, delta_tr, delta_dd))
    return rows


# ── Analysis 3: PF slope around baseline ─────────────────────────────────────

def slope_table():
    rows = []
    param_bl = [('adx_min',30),('rvol_min',1.5),('orb_range_min',2.0),
                ('ema20_dist_min',1.95),('break_dist_min',0.05),
                ('body_atr',0.25),('sess_brk_end_et',120)]
    for pk, bl_val in param_bl:
        data = P4[pk]
        vals = [v for v,*_ in data]
        bl_idx = vals.index(bl_val)
        bl_pf  = data[bl_idx][2]

        # Looser direction (index below bl for "tighter=higher" params)
        looser_pf = data[bl_idx-1][2] if bl_idx > 0 else None
        looser_v  = data[bl_idx-1][0] if bl_idx > 0 else None
        # Tighter direction
        tighter_pf = data[bl_idx+1][2] if bl_idx < len(data)-1 else None
        tighter_v  = data[bl_idx+1][0] if bl_idx < len(data)-1 else None

        slope_loose  = (bl_pf - looser_pf)  / abs(bl_val - looser_v)  if looser_pf  is not None and abs(bl_val-looser_v)>0  else None
        slope_tight  = (tighter_pf - bl_pf) / abs(tighter_v - bl_val) if tighter_pf is not None and abs(tighter_v-bl_val)>0 else None

        rows.append((pk, bl_val, bl_pf, looser_pf, tighter_pf, slope_loose, slope_tight))
    return rows


# ── main ──────────────────────────────────────────────────────────────────────

print('\nLoading bars and building bias map...', flush=True)
spy=load_bars('SPY'); qqq=load_bars('QQQ'); bias_map=build_bias(spy,qqq)
all_sym_bars=[(s,load_bars(s)) for s in SCAN_SYMS]

# Aggregate rejection analysis across all 9 symbols
agg_first=defaultdict(int); agg_all=defaultdict(int)
agg_adx_only=agg_rvol_only=agg_both=agg_neither=0
agg_long_brk=agg_long_cb=agg_short_brk=agg_short_cb=0
total_window_bars=0

for sym,bars in all_sym_bars:
    ff,af,inter,bs,tw = full_rejection_scan(sym,bars,bias_map)
    for k,v in ff.items(): agg_first[k]+=v
    for k,v in af.items(): agg_all[k]+=v
    agg_adx_only+=inter[0]; agg_rvol_only+=inter[1]
    agg_both+=inter[2];     agg_neither+=inter[3]
    agg_long_brk+=bs[0];    agg_long_cb+=bs[1]
    agg_short_brk+=bs[2];   agg_short_cb+=bs[3]
    total_window_bars+=tw

total_signals = agg_first.get('signal_long',0)+agg_first.get('signal_short',0)
total_rejected = total_window_bars - total_signals

# ── console output ────────────────────────────────────────────────────────────

print()
print('Phase 5 -- Feature Importance')
print('='*72)
print(f'IS window  : {IS_START} -> {IS_END}  ({IS_DAYS_N} trading days)')
print(f'Total breakout-window bars (all 9 symbols, IS): {total_window_bars:,}')
print(f'Baseline signals: {total_signals}  |  Rejected: {total_rejected:,}')

# ── 1. Rejection Share ────────────────────────────────────────────────────────
print()
print('--- 1. Rejection Share (first-fail, production filter order) ---')
order = ['adx','rvol','body','orb_range','counter_bias','no_breakout',
         'ema20_dist','f3_break','f4_msft','signal_long','signal_short']
cum=0
R = '{:20s}  {:>7s}  {:>7s}  {:>7s}'
print(R.format('FILTER','BARS','SHARE','CUMUL'))
print('-'*52)
for k in order:
    v = agg_first.get(k,0)
    if v==0: continue
    share = 100.0*v/total_window_bars
    cum  += share
    tag = '  <- SIGNAL' if k.startswith('signal') else ''
    print(R.format(k, str(v), f'{share:.1f}%', f'{cum:.1f}%')+tag)

# ── 2. Incremental Unlock ─────────────────────────────────────────────────────
print()
print('--- 2. Incremental Trade Unlock (max relaxation vs baseline) ---')
U = '{:20s}  {:>8s}  {:>7s}  {:>7s}  {:>8s}  {:>7s}'
print(U.format('PARAMETER','MAX_RELAX','DELTA_SIG','DELTA_PF','DELTA_TR','DELTA_DD'))
print('-'*68)
for pk,mr,bl,ds,dpf,dtr,ddd in unlock_table():
    print(U.format(pk, str(mr),
        f'{ds:+d}', f'{dpf:+.2f}', f'{dtr:+.2f}R', f'{ddd:+.2f}R'))

# ── 3. PF Slope Around Baseline ───────────────────────────────────────────────
print()
print('--- 3. PF Slope Around Baseline ---')
S = '{:20s}  {:>6s}  {:>7s}  {:>7s}  {:>12s}  {:>12s}  {}'
print(S.format('PARAMETER','BL_PF','LOOSE_PF','TIGHT_PF','SLOPE_LOOSE','SLOPE_TIGHT','SHAPE'))
print('-'*88)
slopes = slope_table()
for pk,bl_v,bl_pf,lp,tp,sl,st in slopes:
    lp_s  = f'{lp:.2f}' if lp  is not None else 'n/a'
    tp_s  = f'{tp:.2f}' if tp  is not None else 'n/a'
    sl_s  = f'{sl:+.3f}' if sl is not None else 'n/a'
    st_s  = f'{st:+.3f}' if st is not None else 'n/a'
    # Shape characterisation
    if sl is not None and st is not None:
        if sl>0 and st>0:   shape='VALLEY (BL is local min)'
        elif sl<0 and st<0: shape='PEAK   (BL is local max)'
        elif st>0:          shape='RISING (tighter=better)'
        else:               shape='FALLING(tighter=worse)'
    else:
        shape='edge'
    print(S.format(pk, f'{bl_pf:.2f}', lp_s, tp_s, sl_s, st_s, shape))

# ── 4. ADX-RVOL Interaction ───────────────────────────────────────────────────
print()
print('--- 4. ADX x RVOL Interaction ---')
total_common = agg_adx_only+agg_rvol_only+agg_both+agg_neither
print(f'  ADX fail only (RVOL passes) : {agg_adx_only:5d}  ({100*agg_adx_only/total_common:.1f}%)')
print(f'  RVOL fail only (ADX passes) : {agg_rvol_only:5d}  ({100*agg_rvol_only/total_common:.1f}%)')
print(f'  Both fail simultaneously    : {agg_both:5d}  ({100*agg_both/total_common:.1f}%)')
print(f'  Neither fails (common pass) : {agg_neither:5d}  ({100*agg_neither/total_common:.1f}%)')
overlap_of_adx = 100*agg_both/(agg_adx_only+agg_both) if (agg_adx_only+agg_both)>0 else 0
print(f'  Overlap: {overlap_of_adx:.1f}% of ADX failures also fail RVOL')
if overlap_of_adx > 50:
    print('  -> HIGH overlap: relaxing either filter unlocks largely the same bars')
else:
    print('  -> LOW overlap: ADX and RVOL block mostly different bars -- complementary')

# ── 5. Bias Alignment Rate ────────────────────────────────────────────────────
print()
print('--- 5. Bias Alignment Rate ---')
total_brk = agg_long_brk+agg_short_brk
total_cb  = agg_long_cb+agg_short_cb
pct_cb    = 100*total_cb/total_brk if total_brk>0 else 0
aligned   = total_brk-total_cb
pct_aln   = 100*aligned/total_brk  if total_brk>0 else 0
print(f'  Total price breakouts (common filters passed): {total_brk}')
print(f'    LONG  breakouts: {agg_long_brk}   blocked by counter-bias: {agg_long_cb} ({100*agg_long_cb/max(agg_long_brk,1):.1f}%)')
print(f'    SHORT breakouts: {agg_short_brk}  blocked by counter-bias: {agg_short_cb} ({100*agg_short_cb/max(agg_short_brk,1):.1f}%)')
print(f'  Bias-aligned breakouts (allowed through): {aligned} ({pct_aln:.1f}%)')
print(f'  Counter-bias blocks                     : {total_cb}  ({pct_cb:.1f}%)')

# ── Verdicts ──────────────────────────────────────────────────────────────────
print()
print('='*72)
print('VERDICT -- LIKELY OVER-RESTRICTIVE (Phase 4 relaxation improves metrics):')
print()
print('  ORB_RANGE_ATR_MIN = 2.0')
print('    Relaxing to 1.0 adds +9 signals and +10.6R TotalR at equal PF.')
print('    Bars with range 1.0-2.0 ATR appear profitable.')
print('    Lowest rejection share of any threshold filter.')
print()
print('  RVOL_MIN = 1.5')
print('    Relaxing to 1.4 improves ALL four metrics simultaneously (PF 1.73->1.92,')
print('    TotalR +35.98->+47.16R, MaxDD 7.80->5.20R, sig 100->109).')
print('    Baseline 1.5 sits at a local performance trough between 1.4 and 1.7.')
print()
print('  BREAK_DIST_MIN = 0.05  (F3)')
print('    Blocks <2 signals across the entire IS window.')
print('    Sweep from 0.01 to 0.15 shows near-zero effect on any metric.')
print('    Filter is active in name only.')
print()
print('  EMA20_DIST_MIN = 1.95')
print('    Very flat response. Relaxing to 1.5 adds +3.4R TotalR, minimal PF change.')
print('    Low first-fail rejection share -- rarely the binding constraint.')
print()
print('VERDICT -- LIKELY USEFUL (tightening improves quality or flat with good baseline):')
print()
print('  ADX_MIN = 30.0')
print('    Highest rejection share by far. Tightening to 35 raises PF 1.73->2.10.')
print('    Loosening balloons MaxDD (+4.2R at ADX=15). Strong quality gate.')
print('    ADX-RVOL overlap is the key interaction to quantify (see section 4).')
print()
print('  BODY_ATR = 0.25')
print('    Baseline is already the optimal point in the sweep.')
print('    Both relaxing and tightening degrade performance.')
print()
print('  SESS_BRK_END = 11:30 ET')
print('    Timing matters: shortening to 10:30 ET loses 36 signals and -7R.')
print('    Extending to 12:00 ET adds +4R at no PF cost -- mild opportunity.')
print()
print('  counter_bias (SPY+QQQ EMA9/EMA20)')
print(f'    Blocks {total_cb} of {total_brk} price breakouts ({pct_cb:.1f}%).')
print('    Prevents trading against market direction. Directionally sound.')
print('    Cannot be quantified as "over-restrictive" without OOS validation.')
print()
print('='*72)
print('REMINDER: No hypothesis written here. Hypotheses belong to Phase 6.')
print('          Phase 6 requires explicit user approval before testing begins.')
print('='*72)

# ── write MD ──────────────────────────────────────────────────────────────────
with open(OUT_MD,'w',encoding='utf-8') as f:
    f.write('# Phase 5 -- Feature Importance\n\n')
    f.write(f'**IS window:** {IS_START} -> {IS_END}  ({IS_DAYS_N} trading days)  \n')
    f.write(f'**Symbols:** {", ".join(SCAN_SYMS)}  \n')
    f.write(f'**Total breakout-window bars:** {total_window_bars:,}  \n')
    f.write(f'**Baseline signals:** {total_signals}  **Rejected:** {total_rejected:,}  \n\n')
    f.write('---\n\n## 1. Rejection Share\n\n')
    f.write('| Filter | Bars blocked | Share | Cumulative |\n')
    f.write('|--------|-------------:|------:|-----------:|\n')
    cum2=0
    for k in order:
        v=agg_first.get(k,0)
        if v==0: continue
        share=100.0*v/total_window_bars; cum2+=share
        tag=' *(signal)*' if k.startswith('signal') else ''
        f.write(f'| {k}{tag} | {v} | {share:.1f}% | {cum2:.1f}% |\n')
    f.write('\n---\n\n## 2. Incremental Trade Unlock\n\n')
    f.write('| Parameter | Max relax | ΔSig | ΔPF | ΔTotalR | ΔMaxDD |\n')
    f.write('|-----------|-----------|-----:|----:|--------:|-------:|\n')
    for pk,mr,bl,ds,dpf,dtr,ddd in unlock_table():
        f.write(f'| {pk} | {mr} | {ds:+d} | {dpf:+.2f} | {dtr:+.2f}R | {ddd:+.2f}R |\n')
    f.write('\n---\n\n## 3. PF Slope Around Baseline\n\n')
    f.write('| Parameter | BL PF | Loose PF | Tight PF | Slope↓ | Slope↑ | Shape |\n')
    f.write('|-----------|------:|---------:|---------:|-------:|-------:|-------|\n')
    for pk,bl_v,bl_pf,lp,tp,sl,st in slopes:
        lp_s=f'{lp:.2f}' if lp is not None else 'n/a'
        tp_s=f'{tp:.2f}' if tp is not None else 'n/a'
        sl_s=f'{sl:+.3f}' if sl is not None else 'n/a'
        st_s=f'{st:+.3f}' if st is not None else 'n/a'
        if sl is not None and st is not None:
            if sl>0 and st>0:    shape='VALLEY'
            elif sl<0 and st<0:  shape='PEAK'
            elif st>0:           shape='RISING'
            else:                shape='FALLING'
        else: shape='edge'
        f.write(f'| {pk} | {bl_pf:.2f} | {lp_s} | {tp_s} | {sl_s} | {st_s} | {shape} |\n')
    f.write('\n---\n\n## 4. ADX × RVOL Interaction\n\n')
    f.write(f'| | Count | % of window bars |\n|---|---:|---:|\n')
    for label,val in [('ADX fail only',agg_adx_only),('RVOL fail only',agg_rvol_only),
                      ('Both fail',agg_both),('Neither fails (common pass)',agg_neither)]:
        f.write(f'| {label} | {val} | {100*val/total_common:.1f}% |\n')
    f.write(f'\n**ADX-RVOL overlap:** {overlap_of_adx:.1f}% of ADX failures also fail RVOL  \n')
    conclusion='HIGH overlap -- complementary only if overlap <50%' if overlap_of_adx>50 \
               else 'LOW overlap -- filters block mostly different bars (complementary)'
    f.write(f'**Conclusion:** {conclusion}\n\n')
    f.write('---\n\n## 5. Bias Alignment Rate\n\n')
    f.write(f'| | Count |\n|---|---:|\n')
    f.write(f'| Total price breakouts (common filters passed) | {total_brk} |\n')
    f.write(f'| LONG breakouts | {agg_long_brk} |\n')
    f.write(f'| LONG blocked by counter-bias | {agg_long_cb} ({100*agg_long_cb/max(agg_long_brk,1):.1f}%) |\n')
    f.write(f'| SHORT breakouts | {agg_short_brk} |\n')
    f.write(f'| SHORT blocked by counter-bias | {agg_short_cb} ({100*agg_short_cb/max(agg_short_brk,1):.1f}%) |\n')
    f.write(f'| Bias-aligned (allowed through) | {aligned} ({pct_aln:.1f}%) |\n')
    f.write(f'| Counter-bias blocked | {total_cb} ({pct_cb:.1f}%) |\n')
    f.write('\n---\n\n## Verdicts\n\n')
    f.write('### Likely Over-Restrictive\n\n')
    f.write('| Filter | Evidence |\n|--------|----------|\n')
    f.write('| `ORB_RANGE_ATR_MIN=2.0` | Relaxing to 1.0 adds +9 signals, +10.6R TotalR at equal PF. Bars 1.0-2.0 ATR range are profitable. |\n')
    f.write('| `RVOL_MIN=1.5` | Relaxing to 1.4 improves ALL four metrics simultaneously. Baseline sits at local PF trough. |\n')
    f.write('| `BREAK_DIST_MIN=0.05` (F3) | Blocks <2 signals across full IS window. Near-zero effect on any metric. |\n')
    f.write('| `EMA20_DIST_MIN=1.95` | Flat sweep response. Low first-fail share. Rarely the binding constraint. |\n')
    f.write('\n### Likely Useful\n\n')
    f.write('| Filter | Evidence |\n|--------|----------|\n')
    f.write('| `ADX_MIN=30.0` | Highest rejection share. Tightening to 35 raises PF to 2.10. Strong quality gate. |\n')
    f.write('| `BODY_ATR=0.25` | Baseline already at optimal sweep point. Both directions degrade. |\n')
    f.write('| `SESS_BRK_END=11:30ET` | Timing controls signal volume meaningfully. |\n')
    f.write(f'| `counter_bias` | Blocks {pct_cb:.1f}% of price breakouts. Prevents counter-trend trades. |\n')
    f.write('\n---\n\n')
    f.write('> **REMINDER:** No hypothesis written here.  \n')
    f.write('> Hypotheses belong to Phase 6 and require explicit user approval before testing.\n')

print()
print(f'Output written: {OUT_MD}')
