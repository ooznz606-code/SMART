"""Phase 8 -- Portfolio Expansion Analysis.
Tests every Phase 7 PASS configuration as a portfolio.
Adds portfolio-level metrics: diversification, correlation, signal overlap,
incremental value. Classifies A+ / A / B / Reject.
IS window only. No OOS. No production changes.
"""
import os, json, sys, csv, itertools, math
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_CSV = os.path.join('docs', 'results', 'phase8_portfolio_expansion.csv')
OUT_MD  = os.path.join('docs', 'results', 'phase8_portfolio_expansion.md')
RDIR    = 'chart_data_research'

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')
IS_START = date(2025, 9, 17); IS_END = date(2026, 4, 30)
IS_DAYS  = 156; IS_WEEKS = IS_DAYS / 5.0
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

# Phase 7 pass gate (from Phase 7 verified baseline)
GATE_PF = 1.73; GATE_TR = 35.98; GATE_DD = 11.60

# Phase 8 classification thresholds
AP_PF = 2.20; AP_TR = 47.0; AP_DD = 8.0   # A+
A_PF  = 2.00; A_TR  = 44.0; A_DD  = 10.0  # A
B_PF  = 1.85; B_TR  = 38.0; B_DD  = 12.0  # B

# ── loading ───────────────────────────────────────────────────────────────────

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
        elif dt in orb and not orb[dt][2] and sm>=SESS_ORB_DONE_ET: orb[dt][2]=True
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
        if adx<params['adx_min']:              continue
        if rv <params['rvol_min']:             continue
        if body<params['body_atr']*atr:        continue
        if (oh-ol)/atr<params['orb_range_min']: continue
        sm_mult=1.3 if bval!='NEUTRAL' else 1.0
        if (c>oh and c>b['open'] and bval!='BEAR'
                and (dt,'LONG') not in emitted
                and (c-e20)/atr>=params['ema20_dist_min']
                and (c-oh)/atr >=params['break_dist_min']):
            cands.append({'dt':dt,'sym':sym,'dir':'LONG',
                          'score':adx*rv*sm_mult,'bi':i,'atr':atr,'entry':c})
            emitted.add((dt,'LONG'))
        if (c<ol and c<b['open'] and bval!='BULL'
                and (dt,'SHORT') not in emitted
                and not (sym=='MSFT' and bval=='NEUTRAL')
                and (e20-c)/atr>=params['ema20_dist_min']
                and (ol-c)/atr >=params['break_dist_min']):
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
            if b['low'] <=stop: return (stop-entry)/risk
        else:
            if b['low'] <=tp:   return (entry-tp)/risk
            if b['high']>=stop: return (entry-stop)/risk
    j=min(bi+MAX_HOLD,n-1)
    return (bars[j]['close']-entry)/risk if direction=='LONG' else (entry-bars[j]['close'])/risk

def run_config(sym_list, params, precomp, bias):
    """Returns list of {r, dt, sym} in chronological order."""
    all_cands=[]
    for sym in sym_list:
        if sym not in precomp: continue
        all_cands.extend(scan_sym(sym,precomp[sym],bias,params))
    all_cands.sort(key=lambda x:x['dt'])
    return [{'r': simulate(precomp[t['sym']]['bars'],t['bi'],t['dir'],t['entry'],t['atr']),
             'dt': t['dt'], 'sym': t['sym']} for t in all_cands]

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
    rv=[t['r'] for t in trades]; n=len(rv)
    wins=sum(1 for r in rv if r>0)
    gw=sum(r for r in rv if r>0); gl=abs(sum(r for r in rv if r<0))
    pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
    tr=sum(rv); eq=pk=dd=0.0
    for r in rv: eq+=r; pk=max(pk,eq); dd=max(dd,pk-eq)
    return n,100.0*wins/n,pf,tr,dd

def pearson(xs, ys):
    n=len(xs)
    if n<2: return 0.0
    mx=sum(xs)/n; my=sum(ys)/n
    num=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    dx=math.sqrt(sum((x-mx)**2 for x in xs))
    dy=math.sqrt(sum((y-my)**2 for y in ys))
    return num/(dx*dy) if dx>0 and dy>0 else 0.0

def portfolio_metrics(trades, sym_list):
    if not trades:
        return {'div':0.0,'corr':0.0,'overlap':0.0}
    # Per-symbol signal counts for diversification
    sym_count=defaultdict(int)
    for t in trades: sym_count[t['sym']]+=1
    total=len(trades)
    hhi=sum((v/total)**2 for v in sym_count.values())
    n_active=len(sym_count)
    # Normalize diversification: 0=all one symbol, 1=perfectly even
    max_div=1.0-1.0/n_active if n_active>1 else 0.0
    raw_div=1.0-hhi
    div=raw_div/max_div if max_div>0 else 0.0

    # Daily R stream per symbol (0 if no signal that day)
    all_dates=sorted(set(t['dt'] for t in trades))
    daily_r=defaultdict(lambda: defaultdict(float))
    for t in trades: daily_r[t['sym']][t['dt']]+=t['r']

    # Pairwise correlation across all active symbols
    syms_active=list(sym_count.keys())
    corrs=[]
    for i in range(len(syms_active)):
        for j in range(i+1,len(syms_active)):
            sa=syms_active[i]; sb=syms_active[j]
            xs=[daily_r[sa].get(d,0.0) for d in all_dates]
            ys=[daily_r[sb].get(d,0.0) for d in all_dates]
            corrs.append(pearson(xs,ys))
    avg_corr=sum(corrs)/len(corrs) if corrs else 0.0

    # Signal overlap: % of signal days where 2+ symbols fire
    by_day=defaultdict(set)
    for t in trades: by_day[t['dt']].add(t['sym'])
    signal_days=len(by_day)
    overlap_days=sum(1 for s in by_day.values() if len(s)>=2)
    overlap_pct=100.0*overlap_days/signal_days if signal_days>0 else 0.0

    return {'div':div,'corr':avg_corr,'overlap':overlap_pct}

def classify(pf,tr,dd):
    if pf>=AP_PF and tr>=AP_TR and dd<=AP_DD: return 'A+'
    if pf>=A_PF  and tr>=A_TR  and dd<=A_DD:  return 'A'
    if pf>=B_PF  and tr>=B_TR  and dd<=B_DD:  return 'B'
    return 'Reject'

def sym_changes(h_ids):
    added=[]; removed=[]
    for hid in h_ids:
        hd=H_DEFS[hid]
        if 'add'    in hd: added.extend(hd['add'])
        if 'remove' in hd: removed.extend(hd['remove'])
    return added, removed

def ranking_key(r):
    # Priority: 1=higher PF, 2=higher TotalR, 3=lower MaxDD, 4=higher trades/day,
    #           5=lower correlation, 6=lower signal overlap
    grade_order={'A+':0,'A':1,'B':2,'Reject':3}
    return (grade_order[r['grade']],-r['pf'],-r['tr'],r['dd'],
            -r['n']/IS_DAYS, r['pm']['corr'], r['pm']['overlap'])

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 8 -- Portfolio Expansion Analysis\n'+'='*72)
print('Loading bars...', flush=True)

needed=set(BL_SYMS)|{'AAPL','SPY'}
raw={}
for sym in needed:
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
print(f'Precomputed {len(precomp)} symbols.\n')

# Generate all configs
all_configs=[('BASELINE',())]
for h in APPROVED: all_configs.append((h,(h,)))
for a,b_ in itertools.combinations(APPROVED,2): all_configs.append((f'{a}+{b_}',(a,b_)))
for a,b_,c_ in itertools.combinations(APPROVED,3): all_configs.append((f'{a}+{b_}+{c_}',(a,b_,c_)))

print(f'Running {len(all_configs)} configurations...', flush=True)

results=[]
for label,h_ids in all_configs:
    params,sym_list=make_config(h_ids)
    trades=run_config(sym_list,params,precomp,bias)
    n,wr,pf,tr,dd=overall_metrics(trades)
    pm=portfolio_metrics(trades,sym_list)
    avg_r=tr/n if n>0 else 0.0
    grade=classify(pf,tr,dd) if label!='BASELINE' else 'BASELINE'
    # Phase 7 pass gate
    if label=='BASELINE':
        p7='BASELINE'
    elif pf>=GATE_PF and tr>=GATE_TR and dd<=GATE_DD:
        p7='PASS'
    else:
        p7='FAIL'
    added,removed=sym_changes(h_ids)
    results.append({'label':label,'h_ids':h_ids,'sym_list':sym_list,
                    'n_syms':len(sym_list),'n':n,'wr':wr,'pf':pf,'tr':tr,'dd':dd,
                    'avg_r':avg_r,'pm':pm,'grade':grade,'p7':p7,
                    'added':added,'removed':removed,
                    'incr':tr-(results[0]['tr'] if results else 0.0)})

# Fix incremental value (baseline was results[0])
bl=results[0]; BL_TR=bl['tr']
for r in results: r['incr']=r['tr']-BL_TR

print(f'Baseline: {bl["n"]} trades | PF {bl["pf"]:.2f} | TotalR {bl["tr"]:+.2f}R | MaxDD {bl["dd"]:.2f}R')
print()

# Separate baseline + PASS from FAIL
pass_set=[r for r in results if r['p7'] in ('PASS','BASELINE')]
fail_set=[r for r in results if r['p7']=='FAIL']
print(f'Phase 7 PASS: {len(pass_set)-1}  FAIL: {len(fail_set)}')
print()

# Sort PASS (not baseline) by ranking key
non_bl=[r for r in pass_set if r['label']!='BASELINE']
non_bl.sort(key=ranking_key)
ranked=[bl]+non_bl

# Assign rank to non-baseline
for i,r in enumerate(non_bl): r['rank']=i+1
bl['rank']=0

# Count by grade
grade_counts=defaultdict(int)
for r in non_bl: grade_counts[r['grade']]+=1

# ── console output ────────────────────────────────────────────────────────────

PF_S = '{:<28} {:>5} {:>6} {:>5} {:>9} {:>7} {:>6} {:>6} {:>7} {:>6} {:>8}  {}'

def pf_fmt(v): return f'{v:.2f}' if v!=float('inf') else 'inf'

print('Full Portfolio Rankings (Phase 7 PASS + baseline)')
print('-'*110)
print(PF_S.format('Config','Syms','Trades','PF','TotalR','MaxDD','WR%','AvgR',
                  'Div','Corr','Overlap%','Grade'))
print('-'*110)
for r in ranked:
    pm=r['pm']
    print(PF_S.format(
        r['label'], r['n_syms'], r['n'],
        pf_fmt(r['pf']), f"{r['tr']:+.2f}R", f"{r['dd']:.2f}R",
        f"{r['wr']:.1f}", f"{r['avg_r']:+.2f}R",
        f"{pm['div']:.2f}", f"{pm['corr']:.2f}", f"{pm['overlap']:.1f}%",
        r['grade']))
print()

# Grade summary
print(f"A+: {grade_counts['A+']}  |  A: {grade_counts['A']}  |  "
      f"B: {grade_counts['B']}  |  Reject: {grade_counts['Reject']}")
print()
print(f'Classification thresholds:')
print(f'  A+: PF >= {AP_PF} AND TotalR >= +{AP_TR}R AND MaxDD <= {AP_DD}R')
print(f'  A:  PF >= {A_PF}  AND TotalR >= +{A_TR}R AND MaxDD <= {A_DD}R')
print(f'  B:  PF >= {B_PF}  AND TotalR >= +{B_TR}R AND MaxDD <= {B_DD}R')
print(f'  Reject: below B standard')
print()

# ── write CSV ─────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_CSV),exist_ok=True)
cols=['rank','label','grade','n_syms','n','avg_per_day','avg_per_week',
      'pf','tr','dd','wr','avg_r','div','corr','overlap','incr',
      'syms_added','syms_removed','p7']
with open(OUT_CSV,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
    for r in ranked:
        if r['p7']=='FAIL': continue
        pm=r['pm']
        w.writerow({'rank':r['rank'],'label':r['label'],'grade':r['grade'],
                    'n_syms':r['n_syms'],'n':r['n'],
                    'avg_per_day':f"{r['n']/IS_DAYS:.3f}",
                    'avg_per_week':f"{r['n']/IS_WEEKS:.2f}",
                    'pf':f"{r['pf']:.2f}",'tr':f"{r['tr']:.2f}",
                    'dd':f"{r['dd']:.2f}",'wr':f"{r['wr']:.1f}",
                    'avg_r':f"{r['avg_r']:.3f}",
                    'div':f"{pm['div']:.3f}",'corr':f"{pm['corr']:.3f}",
                    'overlap':f"{pm['overlap']:.1f}",
                    'incr':f"{r['incr']:+.2f}",
                    'syms_added':'+'.join(r['added']) if r['added'] else '--',
                    'syms_removed':'-'.join(r['removed']) if r['removed'] else '--',
                    'p7':r['p7']})
print(f'CSV written: {OUT_CSV}')

# ── write MD ──────────────────────────────────────────────────────────────────
def pf_md(v): return f'{v:.2f}' if v!=float('inf') else 'inf'
def badge(g):
    if g=='A+': return '**A+**'
    if g=='A':  return '**A**'
    if g=='B':  return 'B'
    if g=='Reject': return '*Reject*'
    return g

with open(OUT_MD,'w',encoding='utf-8') as f:
    f.write('# Phase 8 -- Portfolio Expansion Analysis\n\n')
    f.write(f'**IS window:** 2025-09-17 → 2026-04-30 ({IS_DAYS} trading days)  \n')
    f.write(f'**Input:** {len(non_bl)} Phase 7 PASS candidates + baseline  \n')
    f.write(f'**Classification:** A+ / A / B / Reject (independent of Phase 7 gate)  \n\n')
    f.write('| Threshold | PF | TotalR | MaxDD |\n|-----------|---:|-------:|------:|\n')
    f.write(f'| A+ | ≥ {AP_PF} | ≥ +{AP_TR}R | ≤ {AP_DD}R |\n')
    f.write(f'| A  | ≥ {A_PF}  | ≥ +{A_TR}R  | ≤ {A_DD}R  |\n')
    f.write(f'| B  | ≥ {B_PF}  | ≥ +{B_TR}R  | ≤ {B_DD}R  |\n\n')
    f.write('---\n\n')

    # Baseline
    pm=bl['pm']
    f.write('## Baseline\n\n')
    f.write('| Syms | Trades | Trades/Day | Trades/Wk | PF | TotalR | MaxDD | WR | AvgR | Div | Corr | Overlap |\n')
    f.write('|-----:|-------:|-----------:|----------:|---:|-------:|------:|---:|-----:|----:|-----:|--------:|\n')
    f.write(f"| {bl['n_syms']} | {bl['n']} | {bl['n']/IS_DAYS:.3f} | {bl['n']/IS_WEEKS:.2f} | "
            f"{pf_md(bl['pf'])} | {bl['tr']:+.2f}R | {bl['dd']:.2f}R | {bl['wr']:.1f}% | "
            f"{bl['avg_r']:+.3f}R | {pm['div']:.2f} | {pm['corr']:.2f} | {pm['overlap']:.1f}% |\n\n")
    f.write('---\n\n')

    # Full portfolio table
    f.write('## Portfolio Rankings\n\n')
    f.write('Ranking priority: PF → TotalR → MaxDD → Trades/Day → Correlation → Signal Overlap\n\n')
    f.write('| Rank | Config | Grade | Syms | Trades | T/Day | PF | TotalR | MaxDD | WR | AvgR | Div | Corr | Overlap | +ΔR | +Syms | -Syms |\n')
    f.write('|-----:|--------|-------|-----:|-------:|------:|---:|-------:|------:|---:|-----:|----:|-----:|--------:|----:|-------|-------|\n')
    for r in non_bl:
        pm=r['pm']
        added_s='+'.join(r['added']) if r['added'] else '--'
        removed_s=','.join(r['removed']) if r['removed'] else '--'
        f.write(f"| {r['rank']} | {r['label']} | {badge(r['grade'])} | {r['n_syms']} | "
                f"{r['n']} | {r['n']/IS_DAYS:.3f} | {pf_md(r['pf'])} | {r['tr']:+.2f}R | "
                f"{r['dd']:.2f}R | {r['wr']:.1f}% | {r['avg_r']:+.3f}R | "
                f"{pm['div']:.2f} | {pm['corr']:.2f} | {pm['overlap']:.1f}% | "
                f"{r['incr']:+.2f}R | {added_s} | {removed_s} |\n")
    f.write('\n---\n\n')

    # Grade breakdown
    f.write('## Grade Breakdown\n\n')
    for g in ['A+','A','B','Reject']:
        grp=[r for r in non_bl if r['grade']==g]
        if not grp: continue
        f.write(f'### {g} ({len(grp)} portfolios)\n\n')
        f.write('| Config | Syms | PF | TotalR | MaxDD | WR | Div | Corr | Overlap | ΔR |\n')
        f.write('|--------|-----:|---:|-------:|------:|---:|----:|-----:|--------:|---:|\n')
        for r in grp:
            pm=r['pm']
            f.write(f"| {r['label']} | {r['n_syms']} | {pf_md(r['pf'])} | {r['tr']:+.2f}R | "
                    f"{r['dd']:.2f}R | {r['wr']:.1f}% | {pm['div']:.2f} | "
                    f"{pm['corr']:.2f} | {pm['overlap']:.1f}% | {r['incr']:+.2f}R |\n")
        f.write('\n')
    f.write('---\n\n')
    f.write('**Next step:** OOS validation requires explicit user approval.\n')

print(f'MD  written: {OUT_MD}')

# ── final summary (as specified) ──────────────────────────────────────────────
best=non_bl[0]  # top ranked
pm=best['pm']
added_s=', '.join(best['added']) if best['added'] else 'none'
removed_s=', '.join(best['removed']) if best['removed'] else 'none'
print()
print('='*72)
print('PHASE 8 SUMMARY')
print('='*72)
print(f"Best portfolio  : {best['label']}  [{best['grade']}]")
print(f"Symbols to add  : {added_s}")
print(f"Symbols to remove: {removed_s}")
print(f"Expected T/day  : {best['n']/IS_DAYS:.3f}  ({best['n']/IS_WEEKS:.2f}/week)")
print(f"PF              : {pf_fmt(best['pf'])}")
print(f"TotalR          : {best['tr']:+.2f}R  (ΔR vs baseline: {best['incr']:+.2f}R)")
print(f"MaxDD           : {best['dd']:.2f}R")
print('='*72)
