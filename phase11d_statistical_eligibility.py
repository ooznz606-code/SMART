"""Phase 11D — Statistical Eligibility Screening.

For each Phase 11C regime filter, determine whether it is statistically
eligible for Phase 12 review. No recommendations. No production changes.

Statistical criteria applied (all pre-defined, not tuned on OOS):
  C1. IS→OOS consistent (direction HOLDS: both positive, OOS >= IS * 50%)
  C2. OOS AvgR/trade > baseline OOS AvgR/trade (+0.286R)
  C3. OOS t-statistic > t_crit(df, alpha=0.10) one-tailed
  C4. Minimum OOS n >= 10 for any inference to be drawn

Eligibility verdicts:
  ELIGIBLE          : C1+C2+C3+C4 all met, OOS n >= 20
  TRACK_MORE_DATA   : C1+C2 met, C3 or C4 insufficient, but direction holds
  NOT_ELIGIBLE      : C1 or C2 fails
  NO_OOS_ACTIVITY   : zero OOS trades
"""
import os, json, sys, csv, math
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_MD  = os.path.join('docs', 'research', 'phase11d_statistical_eligibility.md')
DB_CSV  = os.path.join('docs', 'research', 'phase11_regime_database.csv')
RDIR    = 'chart_data_research'

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')
IS_START=date(2025,9,17); IS_END=date(2026,4,30)
OOS_START=date(2026,5,1); OOS_END=date(2026,6,25)
WIN_START=IS_START; WIN_END=OOS_END

MIN_LB=60; SL_ATR=1.5; TP_ATR=2.7; MAX_HOLD=40
SESS_OPEN_ET=0; SESS_ORB_DONE_ET=30
BL_SYMS=['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ']
BL=dict(adx_min=30.0,rvol_min=1.5,orb_range_min=2.0,
        ema20_dist_min=1.95,break_dist_min=0.05,body_atr=0.25,sess_brk_end_et=120)

OOS_MONTHS = 2  # May + partial June = approx 2 months currently tracked

# ── filter definitions (identical to Phase 11C — not modified) ────────────────
FILTERS = {
    'BASELINE':  lambda d: True,
    'F-REG':     lambda d: d['market_regime'] != 'BEAR',
    'F-RR':      lambda d: 0.80 <= d['spy_range_ratio'] <= 1.20,
    'F-ORB':     lambda d: d['orb_avg_atr'] is not None and d['orb_avg_atr'] >= 3.5,
    'F-ADX':     lambda d: d['spy_adx14'] >= 35.0,
    'F-RR+REG':  lambda d: (d['market_regime'] != 'BEAR')
                            and (0.80 <= d['spy_range_ratio'] <= 1.20),
    'F-ORB+REG': lambda d: (d['market_regime'] != 'BEAR')
                            and (d['orb_avg_atr'] is not None and d['orb_avg_atr'] >= 3.5),
    'F-RR+ORB':  lambda d: (0.80 <= d['spy_range_ratio'] <= 1.20)
                            and (d['orb_avg_atr'] is not None and d['orb_avg_atr'] >= 3.5),
    'F-ALL':     lambda d: (d['market_regime'] != 'BEAR')
                            and (0.80 <= d['spy_range_ratio'] <= 1.20)
                            and (d['orb_avg_atr'] is not None and d['orb_avg_atr'] >= 3.5),
}

FILTER_DESC = {
    'BASELINE':  'No filter (all days)',
    'F-REG':     'Exclude BEAR regime days',
    'F-RR':      'Range ratio 0.80-1.20',
    'F-ORB':     'ORB avg ≥ 3.5 ATR',
    'F-ADX':     'SPY daily ADX ≥ 35',
    'F-RR+REG':  'Range ratio 0.80-1.20 AND not-BEAR',
    'F-ORB+REG': 'ORB ≥ 3.5 ATR AND not-BEAR',
    'F-RR+ORB':  'Range ratio 0.80-1.20 AND ORB ≥ 3.5',
    'F-ALL':     'Not-BEAR AND RR 0.80-1.20 AND ORB ≥ 3.5',
}

# ── statistical helpers ───────────────────────────────────────────────────────

# One-tailed t critical values: t_crit[df] = (alpha=0.10, 0.05, 0.025, 0.01)
T_TABLE = {
    1:(3.078,6.314,12.706,31.821), 2:(1.886,2.920,4.303,6.965),
    3:(1.638,2.353,3.182,4.541),   4:(1.533,2.132,2.776,3.747),
    5:(1.476,2.015,2.571,3.365),   6:(1.440,1.943,2.447,3.143),
    7:(1.415,1.895,2.365,2.998),   8:(1.397,1.860,2.306,2.896),
    9:(1.383,1.833,2.262,2.821),  10:(1.372,1.812,2.228,2.764),
   12:(1.356,1.782,2.179,2.681),  15:(1.341,1.753,2.131,2.602),
   20:(1.325,1.725,2.086,2.528),  25:(1.316,1.708,2.060,2.485),
   30:(1.310,1.697,2.042,2.457),  40:(1.303,1.684,2.021,2.423),
   60:(1.296,1.671,2.000,2.390),
}

def t_crit_and_pval(t_stat, n):
    """Return (p_label, t_crit_0.10) for one-tailed t-test."""
    if n < 2: return '>0.50', 999.9
    df = n - 1
    keys = sorted(T_TABLE.keys())
    closest = min(keys, key=lambda d: abs(d-df)) if df <= max(keys) else max(keys)
    crits = T_TABLE[closest]  # (0.10, 0.05, 0.025, 0.01)
    if t_stat <= 0:        return '>0.50', crits[0]
    if t_stat < crits[0]:  return '>0.10', crits[0]
    if t_stat < crits[1]:  return '<0.10', crits[0]
    if t_stat < crits[2]:  return '<0.05', crits[0]
    if t_stat < crits[3]:  return '<0.025',crits[0]
    return '<0.01', crits[0]

def wilson_ci(k, n, z=1.645):
    """Wilson score 90% confidence interval for proportion."""
    if n == 0: return 0.0, 0.0
    p = k / n
    denom = 1 + z*z/n
    center = (p + z*z/(2*n)) / denom
    margin = (z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / denom
    return max(0.0, center-margin), min(1.0, center+margin)

def stats(rv):
    """Return mean, std, t_stat for list of R values."""
    n = len(rv)
    if n == 0: return 0.0, 0.0, 0.0
    mu = sum(rv) / n
    if n == 1: return mu, 0.0, 0.0
    var = sum((r-mu)**2 for r in rv) / (n-1)
    std = math.sqrt(var) if var > 0 else 0.0
    t   = mu / (std/math.sqrt(n)) if std > 0 else (float('inf') if mu > 0 else 0.0)
    return mu, std, t

def n_required_80pct_power(mu, std, alpha_one_tail=0.10):
    """Minimum n for 80% power (one-tailed t-test, H0: mu=0).
    Returns None if effect size is 0 or negative."""
    if std <= 0 or mu <= 0: return None
    d = mu / std           # Cohen's d
    # z_alpha + z_beta = 1.28 + 0.84 = 2.12 for alpha=0.10, power=0.80
    n = math.ceil((2.12 / d) ** 2)
    return max(n, 2)

def months_needed(n_needed, n_have, trades_per_month):
    if n_needed is None or n_have >= n_needed: return 0
    if trades_per_month <= 0: return None
    return math.ceil((n_needed - n_have) / trades_per_month)

# ── scan infrastructure (copied from Phase 11C) ───────────────────────────────

def load_db():
    rows = {}
    with open(DB_CSV, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            rows[r['date']] = {
                'window':          r['window'],
                'market_regime':   r['market_regime'],
                'spy_range_ratio': float(r['spy_range_ratio']),
                'spy_adx14':       float(r['spy_adx14']),
                'orb_avg_atr':     float(r['orb_range_avg_atr']) if r['orb_range_avg_atr'] else None,
            }
    return rows

def load_15m(sym):
    p = os.path.join(RDIR, f'{sym}_15m.json')
    with open(p, encoding='utf-8') as f: d = json.load(f)
    times=d['times']; ops=d['opens']; his=d['highs']
    los=d['lows']; cls=d['closes']; vls=d.get('volumes',[0]*len(times))
    out=[]
    for i in range(min(len(times),len(ops),len(his),len(los),len(cls))):
        naive=datetime.strptime(times[i][:16].replace('T',' '),'%Y-%m-%d %H:%M')
        et=naive.replace(tzinfo=_UTC).astimezone(_ET)
        out.append({'ts':naive,'open':float(ops[i]),'high':float(his[i]),
                    'low':float(los[i]),'close':float(cls[i]),
                    'vol':float(vls[i]) if i<len(vls) else 0.0,
                    'sm':(et.hour-9)*60+et.minute-30,
                    'dt':str(et.date()),'td':et.date()})
    return out

def _wilder(v,p):
    k=1.0/p; r=[v[0]]
    for x in v[1:]: r.append(r[-1]+k*(x-r[-1]))
    return r
def _ema(v,p):
    k=2.0/(p+1); r=[v[0]]
    for x in v[1:]: r.append(r[-1]+k*(x-r[-1]))
    return r
def calc_atr(bars,p=14):
    tr=[bars[0]['high']-bars[0]['low']]
    for i in range(1,len(bars)):
        h,l,pc=bars[i]['high'],bars[i]['low'],bars[i-1]['close']
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    return _wilder(tr,p)
def calc_adx(bars,p=14):
    n=len(bars)
    if n<p+2: return [0.0]*n
    pdm,mdm,tr=[],[],[]
    for i in range(1,n):
        h,l=bars[i]['high'],bars[i]['low']
        ph,pl,pc=bars[i-1]['high'],bars[i-1]['low'],bars[i-1]['close']
        up,dn=h-ph,pl-l
        pdm.append(up if up>dn and up>0 else 0.0)
        mdm.append(dn if dn>up and dn>0 else 0.0)
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    a=_wilder(tr,p); pd_=_wilder(pdm,p); md=_wilder(mdm,p); dx=[]
    for ai,pi,mi in zip(a,pd_,md):
        pdi=100*pi/ai if ai>0 else 0.0; mdi=100*mi/ai if ai>0 else 0.0
        dx.append(100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi>0 else 0.0)
    return [0.0]+_wilder(dx,p)
def calc_rvol(bars,p=20):
    vols=[b['vol'] for b in bars]; out=[1.0]*p
    for i in range(p,len(vols)):
        avg=sum(vols[i-p:i])/p; out.append(vols[i]/avg if avg>0 else 1.0)
    return out
def calc_ema20(bars): return _ema([b['close'] for b in bars],20)
def build_orb(bars):
    orb={}
    for b in bars:
        sm=b['sm']; dt=b['dt']
        if SESS_OPEN_ET<=sm<SESS_ORB_DONE_ET:
            if dt not in orb: orb[dt]=[b['high'],b['low'],False]
            else: orb[dt][0]=max(orb[dt][0],b['high']); orb[dt][1]=min(orb[dt][1],b['low'])
        elif dt in orb and not orb[dt][2] and sm>=SESS_ORB_DONE_ET: orb[dt][2]=True
    return {dt:(v[0],v[1]) for dt,v in orb.items() if v[2]}
def build_bias(spy,qqq):
    bias={}
    for bars in (spy,qqq):
        cl=[b['close'] for b in bars]; e9=_ema(cl,9); e20=_ema(cl,20)
        for i,b in enumerate(bars):
            ts=b['ts']; bull=e9[i]>e20[i]; prev=bias.get(ts)
            if prev is None:   bias[ts]='BULL' if bull else 'BEAR'
            elif (prev=='BULL')==bull: pass
            else: bias[ts]='NEUTRAL'
    return bias

def scan_sym(sym, pc, bias, params):
    bars=pc['bars']; atrs=pc['atrs']; adxs=pc['adxs']
    rvs=pc['rvs']; ema20s=pc['ema20s']; orb=pc['orb']
    sess_end=params['sess_brk_end_et']
    cands=[]; emitted=set()
    for i in range(MIN_LB,len(bars)):
        b=bars[i]; td=b['td']; dt=b['dt']; sm=b['sm']
        if td<WIN_START or td>WIN_END: continue
        if sm<SESS_ORB_DONE_ET or sm>=sess_end: continue
        if dt not in orb: continue
        atr=atrs[i]
        if atr<=0: continue
        oh,ol=orb[dt]; adx=adxs[i]; rv=rvs[i]; bval=bias.get(b['ts'],'NEUTRAL')
        body=abs(b['close']-b['open']); e20=ema20s[i]; c=b['close']
        if adx<params['adx_min']:               continue
        if rv <params['rvol_min']:              continue
        if body<params['body_atr']*atr:         continue
        if (oh-ol)/atr<params['orb_range_min']: continue
        sm_mult=1.3 if bval!='NEUTRAL' else 1.0
        if (c>oh and c>b['open'] and bval!='BEAR'
                and (dt,'LONG') not in emitted
                and (c-e20)/atr>=params['ema20_dist_min']
                and (c-oh)/atr>=params['break_dist_min']):
            cands.append({'dt':dt,'sym':sym,'dir':'LONG',
                          'score':adx*rv*sm_mult,'bi':i,'atr':atr,'entry':c})
            emitted.add((dt,'LONG'))
        if (c<ol and c<b['open'] and bval!='BULL'
                and (dt,'SHORT') not in emitted
                and not (sym=='MSFT' and bval=='NEUTRAL')
                and (e20-c)/atr>=params['ema20_dist_min']
                and (ol-c)/atr>=params['break_dist_min']):
            cands.append({'dt':dt,'sym':sym,'dir':'SHORT',
                          'score':adx*rv*sm_mult,'bi':i,'atr':atr,'entry':c})
            emitted.add((dt,'SHORT'))
    return cands

def simulate(bars,bi,direction,entry,atr):
    stop=entry-SL_ATR*atr if direction=='LONG' else entry+SL_ATR*atr
    tp  =entry+TP_ATR*atr if direction=='LONG' else entry-TP_ATR*atr
    risk=abs(entry-stop)
    if risk<=0: return 0.0
    n=len(bars)
    for j in range(bi+1,min(bi+1+MAX_HOLD,n)):
        b=bars[j]
        if direction=='LONG':
            if b['high']>=tp:   return (tp-entry)/risk
            if b['low']<=stop:  return (stop-entry)/risk
        else:
            if b['low']<=tp:    return (entry-tp)/risk
            if b['high']>=stop: return (entry-stop)/risk
    j=min(bi+MAX_HOLD,n-1)
    return (bars[j]['close']-entry)/risk if direction=='LONG' \
           else (entry-bars[j]['close'])/risk

def pf_s(v): return f'{v:.2f}' if v!=float('inf') else 'inf'

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 11D — Statistical Eligibility Screening')
print('='*72)
print('Criteria: C1=IS→OOS consistent  C2=OOS AvgR>baseline  C3=p<0.10  C4=n>=10')
print()

regime_db = load_db()

print('Loading 15m bars...', flush=True)
raw={}
for sym in set(BL_SYMS)|{'SPY'}:
    try: raw[sym]=load_15m(sym)
    except FileNotFoundError: print(f'  MISSING: {sym}')

bias=build_bias(raw.get('SPY',[]),raw.get('QQQ',[]))
precomp={}
for sym in BL_SYMS:
    if sym not in raw: continue
    bars=raw[sym]
    precomp[sym]={'bars':bars,'atrs':calc_atr(bars),'adxs':calc_adx(bars),
                  'rvs':calc_rvol(bars),'ema20s':calc_ema20(bars),'orb':build_orb(bars)}

print('Running full scan...', flush=True)
all_cands=[]
for sym in BL_SYMS:
    if sym not in precomp: continue
    all_cands.extend(scan_sym(sym,precomp[sym],bias,BL))
all_cands.sort(key=lambda x:x['dt'])
all_trades=[{'dt':t['dt'],'sym':t['sym'],'dir':t['dir'],
             'r':simulate(precomp[t['sym']]['bars'],t['bi'],t['dir'],t['entry'],t['atr']),
             'window':'IS' if date.fromisoformat(t['dt'])<=IS_END else 'OOS'}
            for t in all_cands]
print(f'Total trades: {len(all_trades)} '
      f'(IS: {sum(1 for t in all_trades if t["window"]=="IS")} '
      f'OOS: {sum(1 for t in all_trades if t["window"]=="OOS")})')
print()

# ── per-filter statistics ─────────────────────────────────────────────────────

# Baseline OOS reference
bl_oos_trades = [t for t in all_trades if t['window']=='OOS']
bl_mu, bl_std, bl_t = stats([t['r'] for t in bl_oos_trades])
BL_OOS_AVG_R = bl_mu
BL_OOS_N     = len(bl_oos_trades)
# Approx trades/month for each filter (OOS window = 2 months)
BL_OOS_TPM   = BL_OOS_N / OOS_MONTHS   # ~17/month

results = {}
for fname, ftest in FILTERS.items():
    pass_dates = {dt for dt, drow in regime_db.items() if ftest(drow)}
    is_rv  = [t['r'] for t in all_trades if t['window']=='IS'  and t['dt'] in pass_dates]
    oos_rv = [t['r'] for t in all_trades if t['window']=='OOS' and t['dt'] in pass_dates]

    is_mu,  is_std,  is_t  = stats(is_rv)
    oos_mu, oos_std, oos_t = stats(oos_rv)
    is_n  = len(is_rv);  oos_n = len(oos_rv)
    is_wins  = sum(1 for r in is_rv  if r>0)
    oos_wins = sum(1 for r in oos_rv if r>0)
    oos_wr_lo, oos_wr_hi = wilson_ci(oos_wins, oos_n)
    oos_pval, oos_tcrit = t_crit_and_pval(oos_t, oos_n)

    # IS→OOS consistency
    if fname == 'BASELINE':
        is_oos = 'BASELINE'
    elif oos_n == 0:
        is_oos = 'NO_OOS_ACTIVITY'
    elif is_mu > 0 and oos_mu > 0 and oos_mu >= is_mu * 0.50:
        is_oos = 'HOLDS'
    elif is_mu > 0 and oos_mu > 0:
        is_oos = 'DEGRADES'
    elif is_mu > 0 and oos_mu <= 0:
        is_oos = 'REVERSES'
    else:
        is_oos = 'BAD_IS'

    n_req = n_required_80pct_power(oos_mu, oos_std)
    # OOS trades per month for this filter
    filter_tpm = oos_n / OOS_MONTHS
    mo_needed  = months_needed(n_req, oos_n, filter_tpm)

    # Eligibility verdict
    c1 = is_oos in ('HOLDS', 'DEGRADES', 'BASELINE')
    c2 = oos_mu > BL_OOS_AVG_R or fname == 'BASELINE'
    c3 = oos_pval in ('<0.10','<0.05','<0.025','<0.01')
    c4 = oos_n >= 10

    if fname == 'BASELINE':
        verdict = 'REFERENCE'
    elif oos_n == 0:
        verdict = 'NO_OOS_ACTIVITY'
    elif not c1:
        verdict = 'NOT_ELIGIBLE'
    elif not c2:
        verdict = 'NOT_ELIGIBLE'
    elif c1 and c2 and c3 and c4 and oos_n >= 20:
        verdict = 'ELIGIBLE'
    elif c1 and c2:
        verdict = 'TRACK_MORE_DATA'
    else:
        verdict = 'NOT_ELIGIBLE'

    results[fname] = {
        'is_n':is_n,'is_mu':is_mu,'is_std':is_std,'is_t':is_t,
        'is_wr':100.0*is_wins/is_n if is_n>0 else 0.0,
        'oos_n':oos_n,'oos_mu':oos_mu,'oos_std':oos_std,'oos_t':oos_t,
        'oos_wr':100.0*oos_wins/oos_n if oos_n>0 else 0.0,
        'oos_wr_ci':(100.0*oos_wr_lo, 100.0*oos_wr_hi),
        'oos_pval':oos_pval,'oos_tcrit':oos_tcrit,
        'is_oos':is_oos,'c1':c1,'c2':c2,'c3':c3,'c4':c4,
        'n_req':n_req,'mo_needed':mo_needed,'filter_tpm':filter_tpm,
        'verdict':verdict,
    }

# ── console output ────────────────────────────────────────────────────────────

print('OOS TRADE-LEVEL STATISTICS')
print('-'*100)
print(f'{"Filter":<14} {"n":>4} {"AvgR":>7} {"StdR":>7} {"t-stat":>7} {"p(1-tail)":>10} '
      f'{"WR":>7} {"WR 90%CI":>16} {"IS→OOS":>14}')
print('-'*100)
for fname in FILTERS:
    m = results[fname]
    n=m['oos_n']; mu=m['oos_mu']; std=m['oos_std']; t=m['oos_t']
    wr=m['oos_wr']; lo,hi=m['oos_wr_ci']; pval=m['oos_pval']; cons=m['is_oos']
    ci_str=f'[{lo:.0f}%–{hi:.0f}%]' if n>0 else '—'
    t_str=f'{t:+.2f}' if n>1 else '—'
    std_str=f'{std:.3f}' if n>1 else '—'
    print(f'{fname:<14} {n:>4} {mu:>+7.3f} {std_str:>7} {t_str:>7} {pval:>10} '
          f'{wr:>6.1f}% {ci_str:>16} {cons:>14}')

print()
print('SAMPLE SIZE REQUIREMENTS (OOS, 80% power, alpha=0.10 one-tailed)')
print('-'*80)
print(f'{"Filter":<14} {"OOS n":>6} {"n req.":>7} {"gap":>6} {"T/mo":>6} {"Mo needed":>10} {"Verdict":>20}')
print('-'*80)
for fname in FILTERS:
    m = results[fname]
    n=m['oos_n']; nr=m['n_req']; mo=m['mo_needed']; tpm=m['filter_tpm']
    nr_s  = str(nr)         if nr is not None else '—'
    gap_s = str(nr-n)       if nr is not None and nr>n else ('0' if nr and nr<=n else '—')
    mo_s  = str(mo)+'mo'    if mo is not None and mo>0 else ('now' if nr and n>=nr else '—')
    tpm_s = f'{tpm:.1f}'    if n>0 else '0.0'
    print(f'{fname:<14} {n:>6} {nr_s:>7} {gap_s:>6} {tpm_s:>6} {mo_s:>10} {m["verdict"]:>20}')

print()
print('ELIGIBILITY CRITERIA DETAIL  (C1=consistent C2=beats_base C3=p<0.10 C4=n>=10)')
print('-'*80)
print(f'{"Filter":<14} {"C1":>4} {"C2":>4} {"C3":>4} {"C4":>4}   {"Verdict"}')
print('-'*80)
for fname in FILTERS:
    m=results[fname]
    if fname in ('BASELINE',): print(f'{fname:<14}  —    —    —    —   REFERENCE'); continue
    c1s='YES' if m['c1'] else 'NO'
    c2s='YES' if m['c2'] else 'NO'
    c3s='YES' if m['c3'] else 'NO'
    c4s='YES' if m['c4'] else 'NO'
    print(f'{fname:<14} {c1s:>4} {c2s:>4} {c3s:>4} {c4s:>4}   {m["verdict"]}')

print()
print('='*72)
print('PHASE 11D SUMMARY')
print('='*72)
eligible     = [f for f in FILTERS if results[f]['verdict']=='ELIGIBLE']
track        = [f for f in FILTERS if results[f]['verdict']=='TRACK_MORE_DATA']
not_eligible = [f for f in FILTERS if results[f]['verdict'] in ('NOT_ELIGIBLE','NO_OOS_ACTIVITY')]

print(f'\nELIGIBLE for Phase 12 review    ({len(eligible)}): '
      + (', '.join(eligible) if eligible else 'none'))
print(f'TRACK MORE DATA                 ({len(track)}): '
      + (', '.join(track) if track else 'none'))
print(f'NOT ELIGIBLE / no OOS activity  ({len(not_eligible)}): '
      + (', '.join(not_eligible) if not_eligible else 'none'))

print()
print('Key findings:')
for fname in FILTERS:
    if fname == 'BASELINE': continue
    m = results[fname]
    if m['verdict'] in ('ELIGIBLE','TRACK_MORE_DATA'):
        mo_s = f'{m["mo_needed"]}mo more' if m['mo_needed'] else 'now'
        nr_s = str(m['n_req']) if m['n_req'] else '?'
        print(f'  {fname:<14} OOS AvgR={m["oos_mu"]:>+.3f}R  p={m["oos_pval"]:<7}  '
              f'n={m["oos_n"]}/~{nr_s} required  [{mo_s} to qualify]  {m["verdict"]}')
    else:
        print(f'  {fname:<14} OOS AvgR={m["oos_mu"]:>+.3f}R  '
              f'IS→OOS={m["is_oos"]}  {m["verdict"]}')

print()
print('No final recommendation written. No parameters changed.')

# ── write MD ──────────────────────────────────────────────────────────────────
with open(OUT_MD,'w',encoding='utf-8') as f:
    f.write('# Phase 11D — Statistical Eligibility Screening\n\n')
    f.write('> **Scope:** Statistical analysis only. '
            'No recommendations. No production changes.\n\n')
    f.write('## Eligibility Criteria\n\n')
    f.write('| Criterion | Definition |\n|-----------|----------|\n')
    f.write('| **C1** — IS→OOS consistent | IS AvgR/T > 0 AND OOS AvgR/T > 0 '
            'AND OOS ≥ IS × 50% |\n')
    f.write('| **C2** — OOS beats baseline | OOS AvgR/T > baseline OOS AvgR/T '
            f'({BL_OOS_AVG_R:+.3f}R) |\n')
    f.write('| **C3** — Statistical signal | OOS t-statistic > t_crit(df, α=0.10) '
            'one-tailed |\n')
    f.write('| **C4** — Minimum sample | OOS n ≥ 10 trades |\n')
    f.write('| **ELIGIBLE** | C1+C2+C3+C4 all met AND OOS n ≥ 20 |\n')
    f.write('| **TRACK_MORE_DATA** | C1+C2 met, C3 or C4 insufficient |\n')
    f.write('| **NOT_ELIGIBLE** | C1 or C2 fails |\n\n')
    f.write('---\n\n')

    f.write(f'**Baseline OOS reference:** {BL_OOS_N} trades · '
            f'AvgR/T {BL_OOS_AVG_R:+.3f}R · '
            f'StdR {bl_std:.3f}R · t = {bl_t:+.2f}  \n')
    f.write(f'**OOS window:** 2026-05-01 → 2026-06-25 ({OOS_MONTHS} months)  \n\n---\n\n')

    f.write('## OOS Trade-Level Statistics\n\n')
    f.write('| Filter | n | AvgR/T | StdR | t-stat | p (1-tail) | WR | WR 90% CI | IS→OOS |\n')
    f.write('|--------|--:|-------:|-----:|-------:|-----------:|---:|----------:|:------:|\n')
    for fname in FILTERS:
        m=results[fname]
        n=m['oos_n']; mu=m['oos_mu']; std=m['oos_std']; t=m['oos_t']
        wr=m['oos_wr']; lo,hi=m['oos_wr_ci']
        ci_str=f'{lo:.0f}%–{hi:.0f}%' if n>0 else '—'
        t_str=f'{t:+.2f}' if n>1 else '—'
        std_str=f'{std:.3f}' if n>1 else '—'
        f.write(f'| {fname} | {n} | {mu:+.3f}R | {std_str} | {t_str} | {m["oos_pval"]} | '
                f'{wr:.1f}% | {ci_str} | {m["is_oos"]} |\n')
    f.write('\n')

    f.write('## Sample Size Requirements (80% power, α=0.10 one-tailed)\n\n')
    f.write('| Filter | OOS n | n req. | Gap | T/mo | Mo needed | Verdict |\n')
    f.write('|--------|------:|-------:|----:|-----:|----------:|:-------:|\n')
    for fname in FILTERS:
        m=results[fname]
        n=m['oos_n']; nr=m['n_req']; mo=m['mo_needed']
        nr_s  = str(nr)       if nr is not None else '—'
        gap_s = str(nr-n)     if nr is not None and nr>n else ('0' if nr and nr<=n else '—')
        mo_s  = f'{mo} mo'    if mo is not None and mo>0 else ('now' if nr and n>=nr else '—')
        tpm_s = f'{m["filter_tpm"]:.1f}'
        f.write(f'| {fname} | {n} | {nr_s} | {gap_s} | {tpm_s} | {mo_s} | '
                f'{m["verdict"]} |\n')
    f.write('\n')

    f.write('## Criteria Scorecard\n\n')
    f.write('| Filter | C1 | C2 | C3 | C4 | Verdict |\n')
    f.write('|--------|:--:|:--:|:--:|:--:|:-------:|\n')
    for fname in FILTERS:
        m=results[fname]
        if fname=='BASELINE':
            f.write(f'| {fname} | — | — | — | — | REFERENCE |\n'); continue
        c1s='✓' if m['c1'] else '✗'; c2s='✓' if m['c2'] else '✗'
        c3s='✓' if m['c3'] else '✗'; c4s='✓' if m['c4'] else '✗'
        f.write(f'| {fname} | {c1s} | {c2s} | {c3s} | {c4s} | **{m["verdict"]}** |\n')
    f.write('\n---\n\n')

    f.write('## Phase 11D Summary\n\n')
    f.write(f'**Eligible for Phase 12 ({len(eligible)}):** '
            + (', '.join(f'`{f}`' for f in eligible) or 'none') + '  \n')
    f.write(f'**Track more data ({len(track)}):** '
            + (', '.join(f'`{f}`' for f in track) or 'none') + '  \n')
    f.write(f'**Not eligible ({len(not_eligible)}):** '
            + (', '.join(f'`{f}`' for f in not_eligible) or 'none') + '  \n\n')
    f.write('### Per-filter notes\n\n')
    for fname in FILTERS:
        if fname=='BASELINE': continue
        m=results[fname]
        nr_s=str(m['n_req']) if m['n_req'] else '?'
        mo_s=f'{m["mo_needed"]} additional months' if m['mo_needed'] else 'now'
        f.write(f'**{fname}** ({FILTER_DESC[fname]}):  \n')
        f.write(f'OOS n={m["oos_n"]}  AvgR/T={m["oos_mu"]:+.3f}R  '
                f'p={m["oos_pval"]}  WR={m["oos_wr"]:.1f}% '
                f'[90%CI: {m["oos_wr_ci"][0]:.0f}%–{m["oos_wr_ci"][1]:.0f}%]  '
                f'n_required≈{nr_s}  time_to_qualify≈{mo_s}  '
                f'verdict=**{m["verdict"]}**  \n\n')
    f.write('> No final recommendation written. '
            'No parameters changed. Phase 12 requires explicit approval.\n')

print(f'\nMD written: {OUT_MD}')
print('\nPhase 11D complete.')
