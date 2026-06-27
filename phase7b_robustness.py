"""Phase 7B — Robustness Check (PASS candidates from Phase 7 only).
Monthly breakdown for every PASS configuration from Phase 7.
Classifies each candidate as ROBUST / CONDITIONAL / FRAGILE.
IS window: 2025-09-17 -> 2026-04-30 (8 months).
No OOS data touched.
"""
import os, json, sys, csv, itertools
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_CSV = os.path.join('docs', 'results', 'phase7b_robustness.csv')
OUT_MD  = os.path.join('docs', 'results', 'phase7b_robustness.md')
RDIR    = 'chart_data_research'

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')
IS_START = date(2025, 9, 17); IS_END = date(2026, 4, 30)
SESS_OPEN_ET = 0; SESS_ORB_DONE_ET = 30
MIN_LB = 60; SL_ATR = 1.5; TP_ATR = 2.7; MAX_HOLD = 40

BL_SYMS = ['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ']
BL = dict(adx_min=30.0, rvol_min=1.5, orb_range_min=2.0,
          ema20_dist_min=1.95, break_dist_min=0.05,
          body_atr=0.25, sess_brk_end_et=120)
H_DEFS = {
    'H-01': {'rvol_min': 1.4},
    'H-02': {'orb_range_min': 1.0},
    'H-04': {'sess_brk_end_et': 150},
    'H-05': {'remove': ['PANW']},
    'H-06': {'add': ['AAPL']},
    'H-08': {'remove': ['MSFT']},
}
APPROVED = ['H-01','H-02','H-04','H-05','H-06','H-08']

# IS months in display order
IS_MONTHS = ['2025-09','2025-10','2025-11','2025-12',
             '2026-01','2026-02','2026-03','2026-04']
MONTH_LABELS = ['Sep25','Oct25','Nov25','Dec25','Jan26','Feb26','Mar26','Apr26']

# Robustness thresholds
ROB_WIN_MIN      = 5      # must be profitable in >= 5 of 8 months
ROB_CONC_MAX     = 50.0   # best month <= 50% of total TotalR
ROB_WORST_MIN    = -3.0   # worst month >= -3.0R
FRAG_WIN_MAX     = 3      # <= 3 win months -> FRAGILE
FRAG_CONC_MIN    = 60.0   # best month > 60% of TotalR -> FRAGILE
FRAG_WORST_MAX   = -4.0   # worst month < -4.0R -> FRAGILE

# Phase 7 pass gate (from actual baseline)
GATE_PF  = 1.73; GATE_TR = 35.98; GATE_DD = 11.60

# OOS advancement criteria (applied after robustness classification)
OOS_PF_MIN = 2.00; OOS_TR_MIN = 45.0; OOS_DD_MAX = 10.0

# ── data loading ──────────────────────────────────────────────────────────────

def load_bars(sym):
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
                    'dt':str(et.date()),'td':et.date(),
                    'ym':f'{et.year}-{et.month:02d}'})
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
    vols=[b['vol'] for b in bars]
    out=[1.0]*p
    for i in range(p,len(vols)):
        avg=sum(vols[i-p:i])/p
        out.append(vols[i]/avg if avg>0 else 1.0)
    return out
def calc_ema20(bars): return _ema([b['close'] for b in bars],20)

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

def build_orb(bars):
    orb={}
    for b in bars:
        sm=b['sm']; dt=b['dt']
        if SESS_OPEN_ET<=sm<SESS_ORB_DONE_ET:
            if dt not in orb: orb[dt]=[b['high'],b['low'],False]
            else: orb[dt][0]=max(orb[dt][0],b['high']); orb[dt][1]=min(orb[dt][1],b['low'])
        elif dt in orb and not orb[dt][2] and sm>=SESS_ORB_DONE_ET:
            orb[dt][2]=True
    return {dt:(v[0],v[1]) for dt,v in orb.items() if v[2]}

# ── scan (mirrors production scan_orb_live) ───────────────────────────────────

def scan_sym(sym, pc, bias, params):
    bars=pc['bars']; atrs=pc['atrs']; adxs=pc['adxs']
    rvs=pc['rvs']; ema20s=pc['ema20s']; orb=pc['orb']
    sess_end=params['sess_brk_end_et']
    cands=[]; emitted=set()
    for i in range(MIN_LB,len(bars)):
        b=bars[i]; td=b['td']; dt=b['dt']; sm=b['sm']
        if td<IS_START or td>IS_END: continue
        if sm<SESS_ORB_DONE_ET or sm>=sess_end: continue
        if dt not in orb: continue
        atr=atrs[i]
        if atr<=0: continue
        oh,ol=orb[dt]
        adx=adxs[i]; rv=rvs[i]; bval=bias.get(b['ts'],'NEUTRAL')
        body=abs(b['close']-b['open']); e20=ema20s[i]; c=b['close']
        if adx <params['adx_min']:              continue
        if rv  <params['rvol_min']:             continue
        if body<params['body_atr']*atr:         continue
        if (oh-ol)/atr<params['orb_range_min']: continue
        sm_mult=1.3 if bval!='NEUTRAL' else 1.0
        if (c>oh and c>b['open'] and bval!='BEAR'
                and (dt,'LONG') not in emitted
                and (c-e20)/atr>=params['ema20_dist_min']
                and (c-oh)/atr >=params['break_dist_min']):
            cands.append({'dt':dt,'ym':b['ym'],'sym':sym,'dir':'LONG',
                          'score':adx*rv*sm_mult,'bi':i,'atr':atr,'entry':c})
            emitted.add((dt,'LONG'))
        if (c<ol and c<b['open'] and bval!='BULL'
                and (dt,'SHORT') not in emitted
                and not (sym=='MSFT' and bval=='NEUTRAL')
                and (e20-c)/atr>=params['ema20_dist_min']
                and (ol-c)/atr >=params['break_dist_min']):
            cands.append({'dt':dt,'ym':b['ym'],'sym':sym,'dir':'SHORT',
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
            if b['low'] <=stop: return (stop-entry)/risk
        else:
            if b['low'] <=tp:   return (entry-tp)/risk
            if b['high']>=stop: return (entry-stop)/risk
    j=min(bi+MAX_HOLD,n-1)
    return (bars[j]['close']-entry)/risk if direction=='LONG' else (entry-bars[j]['close'])/risk

def run_config(sym_list, params, precomp, bias):
    """Returns list of {r, ym} dicts in chronological order."""
    all_cands=[]
    for sym in sym_list:
        if sym not in precomp: continue
        all_cands.extend(scan_sym(sym,precomp[sym],bias,params))
    all_cands.sort(key=lambda x:x['dt'])
    return [{'r': simulate(precomp[t['sym']]['bars'],t['bi'],t['dir'],t['entry'],t['atr']),
             'ym': t['ym']} for t in all_cands]

def make_config(h_ids):
    p=BL.copy(); s=list(BL_SYMS)
    for hid in h_ids:
        hd=H_DEFS[hid]
        for k,v in hd.items():
            if k=='remove':
                for sym in v:
                    if sym in s: s.remove(sym)
            elif k=='add':
                for sym in v:
                    if sym not in s: s.append(sym)
            else: p[k]=v
    return p,s

# ── metrics ───────────────────────────────────────────────────────────────────

def overall_metrics(trades):
    if not trades: return 0,0.0,0.0,0.0,0.0
    r_vals=[t['r'] for t in trades]
    n=len(r_vals); wins=sum(1 for r in r_vals if r>0)
    gw=sum(r for r in r_vals if r>0)
    gl=abs(sum(r for r in r_vals if r<0))
    pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
    tr=sum(r_vals)
    eq=pk=dd=0.0
    for r in r_vals: eq+=r; pk=max(pk,eq); dd=max(dd,pk-eq)
    return n,100.0*wins/n,pf,tr,dd

def monthly_breakdown(trades):
    """Returns dict: month -> {n, wr, pf, tr}"""
    by_month=defaultdict(list)
    for t in trades: by_month[t['ym']].append(t['r'])
    result={}
    for ym in IS_MONTHS:
        rv=by_month.get(ym,[])
        if not rv:
            result[ym]={'n':0,'wr':0.0,'pf':0.0,'tr':0.0}
            continue
        n=len(rv); wins=sum(1 for r in rv if r>0)
        gw=sum(r for r in rv if r>0); gl=abs(sum(r for r in rv if r<0))
        pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
        result[ym]={'n':n,'wr':100.0*wins/n,'pf':pf,'tr':sum(rv)}
    return result

def robustness(monthly, total_tr):
    trs=[monthly[ym]['tr'] for ym in IS_MONTHS]
    win_months=sum(1 for v in trs if v>0)
    active_months=sum(1 for v in [monthly[ym]['n'] for ym in IS_MONTHS] if v>0)
    worst=min(trs) if trs else 0.0
    best=max(trs) if trs else 0.0
    conc=100.0*best/total_tr if total_tr>0 else 0.0
    # consecutive losing months
    max_consec=0; cur=0
    for v in trs:
        if v<=0: cur+=1; max_consec=max(max_consec,cur)
        else: cur=0
    # classify
    is_fragile=(win_months<=FRAG_WIN_MAX or conc>=FRAG_CONC_MIN or worst<FRAG_WORST_MAX)
    is_robust =(win_months>=ROB_WIN_MIN  and conc<ROB_CONC_MAX   and worst>=ROB_WORST_MIN)
    if is_fragile:     cls='FRAGILE'
    elif is_robust:    cls='ROBUST'
    else:              cls='CONDITIONAL'
    return {'win_months':win_months,'active_months':active_months,
            'worst':worst,'best':best,'conc':conc,'max_consec':max_consec,'cls':cls}

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 7B — Robustness Check\n'+'='*72)
print('Loading bars...', flush=True)

needed_syms=set(BL_SYMS)|{'AAPL','SPY'}
raw={}
for sym in needed_syms:
    try: raw[sym]=load_bars(sym)
    except FileNotFoundError: print(f'  MISSING: {sym}')

bias=build_bias(raw.get('SPY',[]),raw.get('QQQ',[]))
scan_needed=set(BL_SYMS)|{'AAPL'}
precomp={}
for sym in scan_needed:
    if sym not in raw: continue
    bars=raw[sym]
    precomp[sym]={'bars':bars,'atrs':calc_atr(bars),'adxs':calc_adx(bars),
                  'rvs':calc_rvol(bars),'ema20s':calc_ema20(bars),'orb':build_orb(bars)}
print(f'Precomputed {len(precomp)} symbols.')

# Generate all configs (same as Phase 7)
all_configs=[('BASELINE',(),'BASELINE')]
for h in APPROVED: all_configs.append((h,(h,),'INDIVIDUAL'))
for a,b_ in itertools.combinations(APPROVED,2): all_configs.append((f'{a}+{b_}',(a,b_),'PAIR'))
for a,b_,c_ in itertools.combinations(APPROVED,3): all_configs.append((f'{a}+{b_}+{c_}',(a,b_,c_),'TRIPLE'))

print(f'Running {len(all_configs)} configs, extracting PASS set...\n', flush=True)

# Run all configs, collect Phase 7 metrics + monthly breakdown
records=[]
for label,h_ids,level in all_configs:
    params,sym_list=make_config(h_ids)
    trades=run_config(sym_list,params,precomp,bias)
    n,wr,pf,tr,dd=overall_metrics(trades)
    monthly=monthly_breakdown(trades)
    # Phase 7 pass gate
    if label=='BASELINE':
        verdict='BASELINE'
    elif pf>=GATE_PF and tr>=GATE_TR and dd<=GATE_DD:
        verdict='PASS'
    else:
        verdict='FAIL'
    records.append({'label':label,'level':level,'h_ids':h_ids,
                    'n':n,'wr':wr,'pf':pf,'tr':tr,'dd':dd,
                    'monthly':monthly,'verdict':verdict})

# Baseline reference
bl=[r for r in records if r['label']=='BASELINE'][0]
BN=bl['n']; BWR=bl['wr']; BPF=bl['pf']; BTR=bl['tr']; BDD=bl['dd']

# Filter to PASS + BASELINE
pass_set=[r for r in records if r['verdict'] in ('PASS','BASELINE')]
pass_set.sort(key=lambda x:(0 if x['verdict']=='BASELINE' else 1, -x['tr']))

# Compute robustness for each PASS candidate (and baseline)
for r in pass_set:
    rob=robustness(r['monthly'],r['tr'])
    r['rob']=rob
    r['cls']=rob['cls'] if r['verdict']!='BASELINE' else 'BASELINE'

# Separate baseline from candidates for display
bl_rec=pass_set[0]
candidates=[r for r in pass_set if r['verdict']=='PASS']

# Classify
robust=[r for r in candidates if r['cls']=='ROBUST']
conditional=[r for r in candidates if r['cls']=='CONDITIONAL']
fragile=[r for r in candidates if r['cls']=='FRAGILE']

# OOS shortlist: ROBUST + meets OOS gate
oos_candidates=[r for r in robust if r['pf']>=OOS_PF_MIN and r['tr']>=OOS_TR_MIN and r['dd']<=OOS_DD_MAX]

# ── console output ────────────────────────────────────────────────────────────

MH = '{:<28} {:>6} {:>6} {:>6} {:>6} {:>6} {:>6} {:>6} {:>6} | {:>2} {:>5} {:>7}  {}'
MR = '{:<28} {:>6} {:>6} {:>6} {:>6} {:>6} {:>6} {:>6} {:>6} | {:>2} {:>5} {:>7}  {}'

def trf(v): return f'{v:+.1f}' if v!=0 else '  0.0'

print('Monthly TotalR Summary (per config, R units)')
print('-'*105)
print(MH.format('Config','Sep25','Oct25','Nov25','Dec25','Jan26','Feb26','Mar26','Apr26',
                 'W','Conc','Worst','Class'))
print('-'*105)

for r in pass_set:
    m=r['monthly']
    trs=[m[ym]['tr'] for ym in IS_MONTHS]
    rob=r['rob'] if r['verdict']=='PASS' else robustness(m,r['tr'])
    cls=rob['cls'] if r['verdict']=='PASS' else 'BASELINE'
    print(MR.format(
        r['label'],
        trf(trs[0]),trf(trs[1]),trf(trs[2]),trf(trs[3]),
        trf(trs[4]),trf(trs[5]),trf(trs[6]),trf(trs[7]),
        rob['win_months'],f"{rob['conc']:.0f}%",f"{rob['worst']:+.2f}R",cls))

print()
print(f'PASS candidates: {len(candidates)}  |  ROBUST: {len(robust)}  |  '
      f'CONDITIONAL: {len(conditional)}  |  FRAGILE: {len(fragile)}')
print()
print('Robustness criteria:')
print(f'  ROBUST:   win_months >= {ROB_WIN_MIN} AND concentration < {ROB_CONC_MAX:.0f}% AND worst_month >= {ROB_WORST_MIN:.1f}R')
print(f'  FRAGILE:  win_months <= {FRAG_WIN_MAX} OR concentration >= {FRAG_CONC_MIN:.0f}% OR worst_month < {FRAG_WORST_MAX:.1f}R')
print(f'  CONDITIONAL: everything else')
print()

print('ROBUST candidates:')
if robust:
    print(f"  {'Config':<28} {'Trades':>6} {'PF':>5} {'TotalR':>9} {'MaxDD':>7} {'W':>2} {'Conc':>5} {'Worst':>7}")
    print('  '+'-'*75)
    for r in robust:
        rob=r['rob']
        print(f"  {r['label']:<28} {r['n']:>6} {r['pf']:>5.2f} {r['tr']:>+9.2f}R {r['dd']:>6.2f}R "
              f"{rob['win_months']:>2} {rob['conc']:>4.0f}% {rob['worst']:>+6.2f}R")
else:
    print('  (none)')

print()
print('FRAGILE candidates:')
if fragile:
    for r in fragile:
        rob=r['rob']
        reasons=[]
        if rob['win_months']<=FRAG_WIN_MAX: reasons.append(f"only {rob['win_months']} win months")
        if rob['conc']>=FRAG_CONC_MIN: reasons.append(f"concentration {rob['conc']:.0f}%")
        if rob['worst']<FRAG_WORST_MAX: reasons.append(f"worst month {rob['worst']:+.2f}R")
        print(f"  {r['label']:<28} [{', '.join(reasons)}]")
else:
    print('  (none)')

print()
print('CONDITIONAL candidates:')
if conditional:
    for r in conditional:
        rob=r['rob']
        notes=[]
        if rob['win_months']<ROB_WIN_MIN: notes.append(f"{rob['win_months']}/8 win months")
        if rob['conc']>=ROB_CONC_MAX: notes.append(f"concentration {rob['conc']:.0f}%")
        if rob['worst']<ROB_WORST_MIN: notes.append(f"worst {rob['worst']:+.2f}R")
        print(f"  {r['label']:<28} [{', '.join(notes) if notes else 'borderline'}]")
else:
    print('  (none)')

print()
print('='*72)
print('OOS ADVANCEMENT RECOMMENDATION')
print(f'Gate: ROBUST + PF >= {OOS_PF_MIN:.2f} + TotalR >= +{OOS_TR_MIN:.0f}R + MaxDD <= {OOS_DD_MAX:.1f}R')
print()
if oos_candidates:
    for r in oos_candidates:
        rob=r['rob']
        print(f"  ADVANCE: {r['label']}")
        print(f"    PF {r['pf']:.2f}  TotalR {r['tr']:+.2f}R  MaxDD {r['dd']:.2f}R  "
              f"Win months {rob['win_months']}/8  Worst {rob['worst']:+.2f}R  Conc {rob['conc']:.0f}%")
else:
    print('  No candidates meet all OOS advancement criteria.')
    print('  Strongest ROBUST candidates for consideration:')
    for r in sorted(robust,key=lambda x:-x['tr'])[:3]:
        rob=r['rob']
        print(f"    {r['label']}: PF {r['pf']:.2f}, TotalR {r['tr']:+.2f}R, MaxDD {r['dd']:.2f}R, "
              f"Win {rob['win_months']}/8, Worst {rob['worst']:+.2f}R, Conc {rob['conc']:.0f}%")
print('='*72)

# ── write CSV ─────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_CSV),exist_ok=True)
csv_cols=['label','level','n','wr','pf','tr','dd']+\
         [f'tr_{ym.replace("-","")}' for ym in IS_MONTHS]+\
         [f'n_{ym.replace("-","")}' for ym in IS_MONTHS]+\
         ['win_months','worst_month','conc','max_consec','class','oos_candidate']
with open(OUT_CSV,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=csv_cols); w.writeheader()
    for r in pass_set:
        m=r['monthly']
        rob=r['rob'] if 'rob' in r else robustness(m,r['tr'])
        cls=r.get('cls','BASELINE')
        oos='YES' if r in oos_candidates else 'NO'
        row={'label':r['label'],'level':r['level'],
             'n':r['n'],'wr':f"{r['wr']:.1f}",'pf':f"{r['pf']:.2f}",
             'tr':f"{r['tr']:.2f}",'dd':f"{r['dd']:.2f}",
             'win_months':rob['win_months'],'worst_month':f"{rob['worst']:.2f}",
             'conc':f"{rob['conc']:.1f}",'max_consec':rob['max_consec'],
             'class':cls,'oos_candidate':oos}
        for ym in IS_MONTHS: row[f'tr_{ym.replace("-","")}'] = f"{m[ym]['tr']:.2f}"
        for ym in IS_MONTHS: row[f'n_{ym.replace("-","")}']  = m[ym]['n']
        w.writerow(row)
print(f'\nCSV written: {OUT_CSV}')

# ── write MD ──────────────────────────────────────────────────────────────────
def pf_s(v): return f'{v:.2f}' if v!=float('inf') else 'inf'

with open(OUT_MD,'w',encoding='utf-8') as f:
    f.write('# Phase 7B — Robustness Check\n\n')
    f.write(f'**IS window:** 2025-09-17 → 2026-04-30 (8 months)  \n')
    f.write(f'**Input:** {len(candidates)} PASS candidates from Phase 7  \n')
    f.write(f'**Robustness gate — ROBUST:** win months ≥ {ROB_WIN_MIN} AND concentration < {ROB_CONC_MAX:.0f}% AND worst month ≥ {ROB_WORST_MIN:.1f}R  \n')
    f.write(f'**Robustness gate — FRAGILE:** win months ≤ {FRAG_WIN_MAX} OR concentration ≥ {FRAG_CONC_MIN:.0f}% OR worst month < {FRAG_WORST_MAX:.1f}R  \n')
    f.write(f'**OOS gate:** ROBUST AND PF ≥ {OOS_PF_MIN:.2f} AND TotalR ≥ +{OOS_TR_MIN:.0f}R AND MaxDD ≤ {OOS_DD_MAX:.1f}R  \n\n')
    f.write('---\n\n')

    # Monthly TotalR table
    f.write('## Monthly TotalR Breakdown (all PASS candidates + baseline)\n\n')
    hdr='| Config | Level | '+' | '.join(MONTH_LABELS)+' | Win | Conc | Worst | Class |\n'
    sep='|--------|-------|'+('----:|'*8)+'----:|-----:|------:|-------|\n'
    f.write(hdr); f.write(sep)
    for r in pass_set:
        m=r['monthly']; rob=r['rob'] if 'rob' in r else robustness(m,r['tr'])
        cls=r.get('cls','BASELINE')
        badge='**ROBUST**' if cls=='ROBUST' else ('*FRAGILE*' if cls=='FRAGILE' else cls)
        trs=[m[ym]['tr'] for ym in IS_MONTHS]
        cells=' | '.join(f'{v:+.2f}' for v in trs)
        f.write(f"| {r['label']} | {r['level']} | {cells} | "
                f"{rob['win_months']}/8 | {rob['conc']:.0f}% | {rob['worst']:+.2f}R | {badge} |\n")
    f.write('\n---\n\n')

    # Monthly trade count table
    f.write('## Monthly Trade Count\n\n')
    hdr='| Config | '+' | '.join(MONTH_LABELS)+' | Total |\n'
    sep='|--------|'+('---:|'*8)+'------:|\n'
    f.write(hdr); f.write(sep)
    for r in pass_set:
        m=r['monthly']
        ns=[m[ym]['n'] for ym in IS_MONTHS]
        f.write(f"| {r['label']} | {' | '.join(str(v) for v in ns)} | {sum(ns)} |\n")
    f.write('\n---\n\n')

    # Robustness summary
    f.write('## Robustness Summary\n\n')
    f.write('| Config | Level | PF | TotalR | MaxDD | Win/8 | Conc | Worst | MaxConsec | Class |\n')
    f.write('|--------|-------|---:|-------:|------:|------:|-----:|------:|----------:|-------|\n')
    for r in pass_set:
        rob=r['rob'] if 'rob' in r else robustness(r['monthly'],r['tr'])
        cls=r.get('cls','BASELINE')
        badge='**ROBUST**' if cls=='ROBUST' else ('*FRAGILE*' if cls=='FRAGILE' else cls)
        f.write(f"| {r['label']} | {r['level']} | {pf_s(r['pf'])} | {r['tr']:+.2f}R | "
                f"{r['dd']:.2f}R | {rob['win_months']}/8 | {rob['conc']:.0f}% | "
                f"{rob['worst']:+.2f}R | {rob['max_consec']} | {badge} |\n")
    f.write('\n---\n\n')

    # OOS recommendation
    f.write('## OOS Advancement Recommendation\n\n')
    f.write(f'Gate applied: ROBUST + PF ≥ {OOS_PF_MIN:.2f} + TotalR ≥ +{OOS_TR_MIN:.0f}R + MaxDD ≤ {OOS_DD_MAX:.1f}R\n\n')
    if oos_candidates:
        f.write('| Config | Level | PF | TotalR | MaxDD | Win/8 | Conc | Worst | Verdict |\n')
        f.write('|--------|-------|---:|-------:|------:|------:|-----:|------:|--------|\n')
        for r in oos_candidates:
            rob=r['rob']
            f.write(f"| {r['label']} | {r['level']} | {pf_s(r['pf'])} | {r['tr']:+.2f}R | "
                    f"{r['dd']:.2f}R | {rob['win_months']}/8 | {rob['conc']:.0f}% | "
                    f"{rob['worst']:+.2f}R | **ADVANCE TO OOS** |\n")
    else:
        f.write('_No candidates meet all OOS criteria. Strongest ROBUST candidates listed below._\n\n')
        if robust:
            f.write('| Config | PF | TotalR | MaxDD | Win/8 | Conc | Worst | Gap to OOS gate |\n')
            f.write('|--------|---:|-------:|------:|------:|-----:|------:|----------------|\n')
            for r in sorted(robust,key=lambda x:-x['tr'])[:5]:
                rob=r['rob']; gaps=[]
                if r['pf']<OOS_PF_MIN:  gaps.append(f'PF {r["pf"]:.2f}<{OOS_PF_MIN}')
                if r['tr']<OOS_TR_MIN:  gaps.append(f'TR {r["tr"]:+.2f}<+{OOS_TR_MIN}')
                if r['dd']>OOS_DD_MAX:  gaps.append(f'DD {r["dd"]:.2f}>{OOS_DD_MAX}')
                f.write(f"| {r['label']} | {pf_s(r['pf'])} | {r['tr']:+.2f}R | {r['dd']:.2f}R | "
                        f"{rob['win_months']}/8 | {rob['conc']:.0f}% | {rob['worst']:+.2f}R | "
                        f"{'; '.join(gaps) if gaps else 'meets all'} |\n")
    f.write('\n---\n\n')
    f.write('**Next step:** OOS testing requires explicit user approval.\n')

print(f'MD  written: {OUT_MD}')
print(f'\nPhase 7B complete. ROBUST: {len(robust)} | CONDITIONAL: {len(conditional)} | FRAGILE: {len(fragile)}')
print(f'OOS candidates: {len(oos_candidates)}')
