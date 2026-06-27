"""Phase 7 — IS Validation (Approved Hypotheses Only).
H-01, H-02, H-04, H-05, H-06, H-08 — individual + pairwise + triple combinations.
IS: 2025-09-17 -> 2026-04-30. DST-safe _sm_et. chart_data_research/ only.
No production code touched. H-03 and H-07 excluded from this phase.
"""
import os, json, sys, csv, itertools
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_CSV = os.path.join('docs', 'results', 'phase7_validation.csv')
OUT_MD  = os.path.join('docs', 'results', 'phase7_validation.md')
RDIR    = 'chart_data_research'

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')

IS_START = date(2025, 9, 17); IS_END = date(2026, 4, 30)
SESS_OPEN_ET = 0; SESS_ORB_DONE_ET = 30
MIN_LB = 60; TOP_N = 3; MAX_DIR = 2
SL_ATR = 1.5; TP_ATR = 2.7; MAX_HOLD = 40

BL_SYMS = ['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ']
BL = dict(adx_min=30.0, rvol_min=1.5, orb_range_min=2.0,
          ema20_dist_min=1.95, break_dist_min=0.05,
          body_atr=0.25, sess_brk_end_et=120)

# Approved hypotheses — parameter changes and symbol list mutations
H_DEFS = {
    'H-01': {'rvol_min': 1.4},
    'H-02': {'orb_range_min': 1.0},
    'H-04': {'sess_brk_end_et': 150},
    'H-05': {'remove': ['PANW']},
    'H-06': {'add': ['AAPL']},
    'H-08': {'remove': ['MSFT']},
}
APPROVED = ['H-01','H-02','H-04','H-05','H-06','H-08']

# ── loading ───────────────────────────────────────────────────────────────────

def load_bars(sym):
    p = os.path.join(RDIR, f'{sym}_15m.json')
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    times=d['times']; ops=d['opens']; his=d['highs']
    los=d['lows']; cls=d['closes']; vls=d.get('volumes',[0]*len(times))
    out = []
    for i in range(min(len(times),len(ops),len(his),len(los),len(cls))):
        naive = datetime.strptime(times[i][:16].replace('T',' '),'%Y-%m-%d %H:%M')
        et = naive.replace(tzinfo=_UTC).astimezone(_ET)
        sm = (et.hour-9)*60 + et.minute-30
        out.append({'ts':naive,'open':float(ops[i]),'high':float(his[i]),
                    'low':float(los[i]),'close':float(cls[i]),
                    'vol':float(vls[i]) if i<len(vls) else 0.0,
                    'sm':sm,'dt':str(et.date()),'td':et.date()})
    return out

# ── indicators ────────────────────────────────────────────────────────────────

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

def calc_ema20(bars):
    return _ema([b['close'] for b in bars],20)

def build_bias(spy_bars,qqq_bars):
    bias={}
    for bars in (spy_bars,qqq_bars):
        cl=[b['close'] for b in bars]
        e9=_ema(cl,9); e20=_ema(cl,20)
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

# ── scan one symbol ───────────────────────────────────────────────────────────

def scan_sym(sym, pc, bias, params):
    """
    Mirrors production scan_orb_live() exactly:
    - emitted set: each (date, direction) fires at most once per symbol
    - score_mult = 1.3 for any non-NEUTRAL bias, 1.0 for NEUTRAL
    - direction requires close > open (LONG) / close < open (SHORT)
    """
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
        sm_mult = 1.3 if bval!='NEUTRAL' else 1.0
        # LONG: close above ORB high, bullish bar, bias not BEAR, (dt,LONG) not yet emitted
        if (c>oh and c>b['open'] and bval!='BEAR'
                and (dt,'LONG') not in emitted
                and (c-e20)/atr>=params['ema20_dist_min']
                and (c-oh)/atr >=params['break_dist_min']):
            cands.append({'dt':dt,'td':td,'sym':sym,'dir':'LONG',
                          'score':adx*rv*sm_mult,'bi':i,'atr':atr,'entry':c})
            emitted.add((dt,'LONG'))
        # SHORT: close below ORB low, bearish bar, bias not BULL, (dt,SHORT) not yet emitted
        if (c<ol and c<b['open'] and bval!='BULL'
                and (dt,'SHORT') not in emitted
                and not (sym=='MSFT' and bval=='NEUTRAL')
                and (e20-c)/atr>=params['ema20_dist_min']
                and (ol-c)/atr >=params['break_dist_min']):
            cands.append({'dt':dt,'td':td,'sym':sym,'dir':'SHORT',
                          'score':adx*rv*sm_mult,'bi':i,'atr':atr,'entry':c})
            emitted.add((dt,'SHORT'))
    return cands

# ── simulate one trade ────────────────────────────────────────────────────────

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

# ── run one full config ───────────────────────────────────────────────────────

def run_config(sym_list, params, precomp, bias):
    """
    Matches the Phase 4 research convention: each symbol scanned independently
    with an emitted set (at most 1 LONG and 1 SHORT per symbol per day).
    All per-symbol signals are taken to simulation without a global cross-symbol
    TOP_N cap — consistent with the established 100-signal baseline.
    """
    all_cands=[]
    for sym in sym_list:
        if sym not in precomp: continue
        all_cands.extend(scan_sym(sym, precomp[sym], bias, params))
    # Sort chronologically for accurate equity-curve MaxDD
    all_cands.sort(key=lambda x: x['dt'])
    r_vals=[]
    for t in all_cands:
        r=simulate(precomp[t['sym']]['bars'],t['bi'],t['dir'],t['entry'],t['atr'])
        r_vals.append(r)
    return r_vals

# ── metrics ───────────────────────────────────────────────────────────────────

def calc_metrics(r_vals):
    if not r_vals: return 0,0.0,0.0,0.0,0.0
    n=len(r_vals)
    wins=sum(1 for r in r_vals if r>0)
    gw=sum(r for r in r_vals if r>0)
    gl=abs(sum(r for r in r_vals if r<0))
    pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
    tr=sum(r_vals)
    eq=pk=dd=0.0
    for r in r_vals:
        eq+=r; pk=max(pk,eq); dd=max(dd,pk-eq)
    return n, 100.0*wins/n, pf, tr, dd

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

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 7 — IS Validation\n'+'='*60)
print('Loading bars...', flush=True)

needed_syms = set(BL_SYMS)|{'AAPL','SPY'}
raw={}
for sym in needed_syms:
    try: raw[sym]=load_bars(sym)
    except FileNotFoundError: print(f'  MISSING: {sym}_15m.json')

bias=build_bias(raw.get('SPY',[]),raw.get('QQQ',[]))

scan_syms_needed = set(BL_SYMS)|{'AAPL'}
precomp={}
for sym in scan_syms_needed:
    if sym not in raw: continue
    bars=raw[sym]
    precomp[sym]={'bars':bars,'atrs':calc_atr(bars),'adxs':calc_adx(bars),
                  'rvs':calc_rvol(bars),'ema20s':calc_ema20(bars),'orb':build_orb(bars)}
print(f'Precomputed {len(precomp)} symbols.')

# Generate all test configurations
configs=[('BASELINE',(),'BASELINE')]
for h in APPROVED:
    configs.append((h,(h,),'INDIVIDUAL'))
for a,b_ in itertools.combinations(APPROVED,2):
    configs.append((f'{a}+{b_}',(a,b_),'PAIR'))
for a,b_,c_ in itertools.combinations(APPROVED,3):
    configs.append((f'{a}+{b_}+{c_}',(a,b_,c_),'TRIPLE'))

print(f'Running {len(configs)} configurations (1 baseline + 6 individual + 15 pairs + 20 triples)...\n')

rows=[]
for label,h_ids,level in configs:
    params,sym_list=make_config(h_ids)
    r_vals=run_config(sym_list,params,precomp,bias)
    n,wr,pf,tr,dd=calc_metrics(r_vals)
    rows.append({'label':label,'level':level,'h_ids':h_ids,
                 'sym_n':len(sym_list),'n':n,'wr':wr,'pf':pf,'tr':tr,'dd':dd})

# Extract baseline actuals
bl=rows[0]
BN=bl['n']; BWR=bl['wr']; BPF=bl['pf']; BTR=bl['tr']; BDD=bl['dd']

print(f'Baseline: {BN} trades | WR {BWR:.1f}% | PF {BPF:.2f} | TotalR {BTR:+.2f}R | MaxDD {BDD:.2f}R')
if abs(BN-100)>5: print(f'  NOTE: expected ~100 trades, got {BN}')
if abs(BPF-1.73)>0.10: print(f'  NOTE: expected PF ~1.73, got {BPF:.2f}')
print()

# PASS/FAIL: all three gate metrics must not degrade vs baseline
# PASS: PF >= BL and TotalR >= BL and MaxDD <= BL
def verdict(r):
    if r['label']=='BASELINE': return 'BASELINE'
    if r['pf']>=BPF and r['tr']>=BTR and r['dd']<=BDD: return 'PASS'
    return 'FAIL'

for r in rows:
    r['verdict']=verdict(r)
    r['dn'] =r['n'] -BN;  r['dwr']=r['wr']-BWR
    r['dpf']=r['pf']-BPF; r['dtr']=r['tr']-BTR; r['ddd']=r['dd']-BDD

# Sort non-baseline: PASS first by TotalR desc, then FAIL by TotalR desc
non_bl=rows[1:]
non_bl.sort(key=lambda x:(0 if x['verdict']=='PASS' else 1,-x['tr']))
ranked=[rows[0]]+non_bl

# Assign rank (PASS only, FAIL gets '--')
rank=0
for r in ranked:
    if r['verdict']=='PASS': rank+=1; r['rank']=rank
    elif r['verdict']=='BASELINE': r['rank']=0
    else: r['rank']=None

# ── console output ────────────────────────────────────────────────────────────

HDR = '{:<28} {:<10} {:>5} {:>7} {:>6} {:>9} {:>8}  {:>8}'
ROW = '{:<28} {:<10} {:>5} {:>7.1f} {:>6.2f} {:>9.2f} {:>8.2f}  {:>8}'

def pf_str(v):
    return f'{v:.2f}' if v != float('inf') else 'inf'

print(HDR.format('Config','Verdict','Trades','WR%','PF','TotalR','MaxDD','Level'))
print('-'*82)
for r in ranked:
    v=r['verdict']
    tag='**'+v+'**' if v=='PASS' else v
    pf_s=pf_str(r['pf'])
    print(f"{r['label']:<28} {v:<10} {r['n']:>5} {r['wr']:>7.1f} {pf_s:>6} {r['tr']:>9.2f} {r['dd']:>8.2f}  {r['level']:>8}")

pass_list=[r for r in ranked if r['verdict']=='PASS']
fail_list=[r for r in ranked if r['verdict']=='FAIL']
print()
print(f'PASS: {len(pass_list)} configs | FAIL: {len(fail_list)} configs')
print()

# ── write CSV ─────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_CSV),exist_ok=True)
with open(OUT_CSV,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=['rank','label','level','sym_n','n','wr','pf','tr','dd',
                                    'dn','dwr','dpf','dtr','ddd','verdict'])
    w.writeheader()
    for r in ranked:
        w.writerow({'rank':r['rank'] if r['rank'] is not None else '',
                    'label':r['label'],'level':r['level'],'sym_n':r['sym_n'],
                    'n':r['n'],'wr':f"{r['wr']:.1f}",'pf':f"{r['pf']:.2f}",
                    'tr':f"{r['tr']:.2f}",'dd':f"{r['dd']:.2f}",
                    'dn':f"{r['dn']:+d}",'dwr':f"{r['dwr']:+.1f}",
                    'dpf':f"{r['dpf']:+.2f}",'dtr':f"{r['dtr']:+.2f}",'ddd':f"{r['ddd']:+.2f}",
                    'verdict':r['verdict']})
print(f'CSV written: {OUT_CSV}')

# ── write MD ──────────────────────────────────────────────────────────────────

def pf_md(v): return f'{v:.2f}' if v!=float('inf') else 'inf'
def delta_pf(v): return f'{v:+.2f}' if v!=float('inf') else 'n/a'

with open(OUT_MD,'w',encoding='utf-8') as f:
    f.write('# Phase 7 — IS Validation\n\n')
    f.write(f'**IS window:** 2025-09-17 → 2026-04-30  \n')
    f.write(f'**Hypotheses validated:** H-01, H-02, H-04, H-05, H-06, H-08  \n')
    f.write(f'**Not tested here:** H-03 (conditional/risk-control, separate), H-07 (no-change confirmed)  \n')
    f.write(f'**Configurations:** 1 baseline + 6 individual + 15 pairs + 20 triples = {len(configs)} total  \n')
    f.write(f'**Pass gate:** PF ≥ {BPF:.2f} AND TotalR ≥ {BTR:.2f}R AND MaxDD ≤ {BDD:.2f}R (all three simultaneously)  \n\n')
    f.write('---\n\n')

    # Baseline
    f.write('## Baseline\n\n')
    f.write(f'| Trades | WR | PF | TotalR | MaxDD | Symbols |\n|--------|-----|-----|--------|-------|--------|\n')
    f.write(f'| {BN} | {BWR:.1f}% | {BPF:.2f} | {BTR:+.2f}R | {BDD:.2f}R | {len(BL_SYMS)} |\n\n')
    f.write('---\n\n')

    def write_section(title, level_rows):
        f.write(f'## {title}\n\n')
        f.write('| Config | Trades | ΔTrades | WR | ΔWR | PF | ΔPF | TotalR | ΔTotalR | MaxDD | ΔMaxDD | Verdict |\n')
        f.write('|--------|-------:|--------:|---:|----:|---:|----:|-------:|--------:|------:|-------:|--------|\n')
        for r in level_rows:
            v=r['verdict']
            badge='**PASS**' if v=='PASS' else ('*FAIL*' if v=='FAIL' else v)
            pf_s=pf_md(r['pf']); dpf_s=delta_pf(r['dpf'])
            dn_s=f"{r['dn']:+d}"; dwr_s=f"{r['dwr']:+.1f}%"
            dtr_s=f"{r['dtr']:+.2f}R"; ddd_s=f"{r['ddd']:+.2f}R"
            f.write(f"| {r['label']} | {r['n']} | {dn_s} | {r['wr']:.1f}% | {dwr_s} | "
                    f"{pf_s} | {dpf_s} | {r['tr']:+.2f}R | {dtr_s} | {r['dd']:.2f}R | {ddd_s} | {badge} |\n")
        f.write('\n')

    indiv=[r for r in ranked if r['level']=='INDIVIDUAL']
    pairs=[r for r in ranked if r['level']=='PAIR']
    triples=[r for r in ranked if r['level']=='TRIPLE']

    write_section('Individual Hypothesis Results', indiv)
    f.write('---\n\n')
    write_section('Pairwise Combination Results', pairs)
    f.write('---\n\n')
    write_section('Triple Combination Results', triples)
    f.write('---\n\n')

    # Combined ranking — PASS only
    f.write('## Combined Ranking (PASS Candidates Only)\n\n')
    if pass_list:
        f.write('| Rank | Config | Level | Trades | WR | PF | TotalR | MaxDD | ΔPF | ΔTotalR | ΔMaxDD |\n')
        f.write('|-----:|--------|-------|-------:|---:|---:|-------:|------:|----:|--------:|-------:|\n')
        for r in pass_list:
            dpf_s=delta_pf(r['dpf'])
            f.write(f"| {r['rank']} | {r['label']} | {r['level']} | {r['n']} | {r['wr']:.1f}% | "
                    f"{pf_md(r['pf'])} | {r['tr']:+.2f}R | {r['dd']:.2f}R | "
                    f"{dpf_s} | {r['dtr']:+.2f}R | {r['ddd']:+.2f}R |\n")
    else:
        f.write('_No configurations passed all three gate metrics._\n')
    f.write('\n---\n\n')

    # Rejected candidates
    f.write('## Rejected Candidates\n\n')
    if fail_list:
        f.write('| Config | Level | Trades | PF | TotalR | MaxDD | Failure reason |\n')
        f.write('|--------|-------|-------:|---:|-------:|------:|----------------|\n')
        for r in fail_list:
            reasons=[]
            if r['pf']<BPF:  reasons.append(f'PF {pf_md(r["pf"])} < {BPF:.2f}')
            if r['tr']<BTR:  reasons.append(f'TotalR {r["tr"]:+.2f}R < {BTR:+.2f}R')
            if r['dd']>BDD:  reasons.append(f'MaxDD {r["dd"]:.2f}R > {BDD:.2f}R')
            f.write(f"| {r['label']} | {r['level']} | {r['n']} | {pf_md(r['pf'])} | "
                    f"{r['tr']:+.2f}R | {r['dd']:.2f}R | {'; '.join(reasons)} |\n")
    else:
        f.write('_No candidates rejected._\n')
    f.write('\n---\n\n')

    # Summary
    f.write('## Summary\n\n')
    f.write(f'- Total configurations tested: {len(configs)}\n')
    f.write(f'- PASS: {len(pass_list)}\n')
    f.write(f'- FAIL: {len(fail_list)}\n\n')
    if pass_list:
        best=pass_list[0]
        f.write(f'**Best by TotalR:** {best["label"]} — '
                f'{best["tr"]:+.2f}R TotalR, PF {pf_md(best["pf"])}, MaxDD {best["dd"]:.2f}R\n\n')
    f.write('**Scope note:** H-03 (ADX 30→35, conditional/risk-control) '
            'was not included in this validation. '
            'H-07 (BREAK_DIST no-change) requires no backtest.\n\n')
    f.write('**Next step:** Recommendations and Phase 8 design require explicit user approval.\n')

print(f'MD  written: {OUT_MD}')
print(f'\nDone. {len(pass_list)} PASS, {len(fail_list)} FAIL out of {len(configs)-1} tested configs.')
