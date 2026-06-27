"""Phase 8B -- Symbol-Level Optimization.
Tests each symbol individually across approved parameter combinations.
Classifies each symbol: A (no change) / B (small improvement) /
C (significant improvement) / D (global settings unsuitable).
Recommends: KEEP (unchanged) / KEEP (optimized) / REMOVE.
IS window only. No OOS. No production changes.
"""
import os, json, sys, csv, itertools, math
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_CSV = os.path.join('docs', 'results', 'phase8b_symbol_optimization.csv')
OUT_MD  = os.path.join('docs', 'results', 'phase8b_symbol_optimization.md')
RDIR    = 'chart_data_research'

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')
IS_START = date(2025, 9, 17); IS_END = date(2026, 4, 30)
MIN_LB = 60; SL_ATR = 1.5; TP_ATR = 2.7; MAX_HOLD = 40
SESS_OPEN_ET = 0; SESS_ORB_DONE_ET = 30

# All symbols to evaluate (BL set + AAPL)
ALL_SYMS  = ['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ','AAPL']
BL_SYMS   = ['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ']

BL = dict(adx_min=30.0, rvol_min=1.5, orb_range_min=2.0,
          ema20_dist_min=1.95, break_dist_min=0.05,
          body_atr=0.25, sess_brk_end_et=120)

# 8 parameter sets: all subsets of {H-01, H-02, H-04}
PARAM_SETS = {
    'BL':              {**BL},
    'H-01':            {**BL, 'rvol_min': 1.4},
    'H-02':            {**BL, 'orb_range_min': 1.0},
    'H-04':            {**BL, 'sess_brk_end_et': 150},
    'H-01+H-02':       {**BL, 'rvol_min': 1.4, 'orb_range_min': 1.0},
    'H-01+H-04':       {**BL, 'rvol_min': 1.4, 'sess_brk_end_et': 150},
    'H-02+H-04':       {**BL, 'orb_range_min': 1.0, 'sess_brk_end_et': 150},
    'H-01+H-02+H-04':  {**BL, 'rvol_min': 1.4, 'orb_range_min': 1.0, 'sess_brk_end_et': 150},
}

# Per-symbol grade thresholds
GRADE_A_PF = 1.40   # BL already performs well
GRADE_A_TR = 2.0    # BL TotalR floor for A
GRADE_A_N  = 5      # minimum trades to qualify A
GRADE_BC_PF = 1.20  # optimized PF floor to be kept
GRADE_BC_TR = 1.0   # optimized TotalR floor to be kept

# ── infrastructure ────────────────────────────────────────────────────────────

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

def run_sym(sym, pc, bias, params):
    cands=scan_sym(sym,pc,bias,params)
    return [simulate(pc['bars'],t['bi'],t['dir'],t['entry'],t['atr']) for t in cands]

def sym_metrics(rv):
    if not rv: return dict(n=0,wr=0.0,pf=0.0,tr=0.0,dd=0.0,avg_r=0.0)
    n=len(rv); wins=sum(1 for r in rv if r>0)
    gw=sum(r for r in rv if r>0); gl=abs(sum(r for r in rv if r<0))
    pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
    tr=sum(rv); eq=pk=dd=0.0
    for r in rv: eq+=r; pk=max(pk,eq); dd=max(dd,pk-eq)
    return dict(n=n,wr=100.0*wins/n,pf=pf,tr=tr,dd=dd,avg_r=tr/n)

def grade_and_rec(bl_m, best_m, best_cfg):
    """Grade A/B/C/D and recommend KEEP/KEEP (optimized)/REMOVE."""
    bl_pf=bl_m['pf']; bl_tr=bl_m['tr']; bl_n=bl_m['n']
    bp=best_m['pf']; bt=best_m['tr']

    # Grade A: BL is already performing well
    if bl_pf>=GRADE_A_PF and bl_tr>=GRADE_A_TR and bl_n>=GRADE_A_N:
        if best_cfg=='BL' or (bp-bl_pf)<0.15:
            return 'A','KEEP (unchanged)'
        elif (bp-bl_pf)<0.40:
            return 'B','KEEP (optimized)'
        else:
            return 'C','KEEP (optimized)'

    # Grade D: even optimized is not viable
    if bp<GRADE_BC_PF or bt<=0:
        return 'D','REMOVE'

    # BL is weak, optimization helps significantly
    if bl_pf<1.0 or bl_tr<=0:
        return 'C','KEEP (optimized)'

    # BL is marginal, small improvement from optimization
    if bl_pf<GRADE_A_PF and bp>=GRADE_BC_PF:
        improvement=(bp-bl_pf)/bl_pf if bl_pf>0 else float('inf')
        if improvement>=0.30 or bl_pf<1.10:
            return 'C','KEEP (optimized)'
        return 'B','KEEP (optimized)'

    return 'B','KEEP (optimized)'

def pf_str(v):
    if v==float('inf'): return 'inf'
    return f'{v:.2f}'

# ── main ──────────────────────────────────────────────────────────────────────
print('\nPhase 8B -- Symbol-Level Optimization\n'+'='*72)
print('Loading bars...', flush=True)

needed=set(ALL_SYMS)|{'SPY'}
raw={}
for sym in needed:
    try: raw[sym]=load_bars(sym)
    except FileNotFoundError: print(f'  MISSING: {sym}')

bias=build_bias(raw.get('SPY',[]),raw.get('QQQ',[]))
precomp={}
for sym in ALL_SYMS:
    if sym not in raw: continue
    bars=raw[sym]
    precomp[sym]={'bars':bars,'atrs':calc_atr(bars),'adxs':calc_adx(bars),
                  'rvs':calc_rvol(bars),'ema20s':calc_ema20(bars),'orb':build_orb(bars)}
print(f'Precomputed {len(precomp)} symbols.\n')

print(f'Running {len(ALL_SYMS)} symbols × {len(PARAM_SETS)} param sets = '
      f'{len(ALL_SYMS)*len(PARAM_SETS)} total configs...\n', flush=True)

# Per-symbol results
sym_results = {}
for sym in ALL_SYMS:
    if sym not in precomp: continue
    pc=precomp[sym]
    rows={}
    for pname,params in PARAM_SETS.items():
        rv=run_sym(sym,pc,bias,params)
        rows[pname]=sym_metrics(rv)
    sym_results[sym]=rows

# Determine best config per symbol (maximize TotalR, break ties by PF, require n≥3)
summary={}
for sym,rows in sym_results.items():
    bl_m=rows['BL']
    best_cfg='BL'; best_m=bl_m
    for pname,m in rows.items():
        if m['n']<3: continue
        if (m['tr']>best_m['tr'] or
            (m['tr']==best_m['tr'] and m['pf']>best_m['pf'])):
            best_m=m; best_cfg=pname
    grade,rec=grade_and_rec(bl_m,best_m,best_cfg)
    summary[sym]={'bl':bl_m,'best_m':best_m,'best_cfg':best_cfg,
                  'grade':grade,'rec':rec,'rows':rows}

# ── console output ─────────────────────────────────────────────────────────────

HDR = '{:<6}  {:>5}  {:>5}  {:>8}  {:>7}  {:>7}  {:>16}  {:>5}  {:>5}  {:>8}  {:>7}  {:>5}  {}  {}'
ROW = '{:<6}  {:>5}  {:>5}  {:>8}  {:>7}  {:>7}  {:>16}  {:>5}  {:>5}  {:>8}  {:>7}  {:>5}  {}  {}'

print('Symbol Optimization Results')
print('-'*130)
print(f"{'Sym':<6}  {'BL_n':>5}  {'BL_PF':>5}  {'BL_TR':>8}  {'BL_DD':>7}  {'BL_WR':>7}  "
      f"{'Best_Config':>16}  {'BST_n':>5}  {'BST_PF':>5}  {'BST_TR':>8}  {'BST_DD':>7}  "
      f"{'ΔPF':>5}  {'Gr':>2}  Recommendation")
print('-'*130)

for sym in ALL_SYMS:
    if sym not in summary: continue
    s=summary[sym]
    bl=s['bl']; bm=s['best_m']
    dpf=bm['pf']-bl['pf'] if bl['pf']!=float('inf') else 0.0
    in_bl='*' if sym in BL_SYMS else '+'
    print(f"{sym+in_bl:<6}  {bl['n']:>5}  {pf_str(bl['pf']):>5}  "
          f"{bl['tr']:>+8.2f}  {bl['dd']:>7.2f}  {bl['wr']:>6.1f}%  "
          f"{s['best_cfg']:>16}  {bm['n']:>5}  {pf_str(bm['pf']):>5}  "
          f"{bm['tr']:>+8.2f}  {bm['dd']:>7.2f}  "
          f"{dpf:>+5.2f}  {s['grade']:>2}  {s['rec']}")
print()
print('* = in current BL_SYMS  + = candidate for addition (AAPL)')
print()

# Per-symbol detail breakdown
print('Per-Symbol Parameter Sweep (TotalR per config)')
print('-'*100)
pcols=list(PARAM_SETS.keys())
print(f"{'Sym':<6}  " + '  '.join(f'{p:>16}' for p in pcols))
print('-'*100)
for sym in ALL_SYMS:
    if sym not in summary: continue
    rows=summary[sym]['rows']
    best_cfg=summary[sym]['best_cfg']
    vals=[]
    for p in pcols:
        m=rows[p]; s=f"{m['tr']:>+.2f}R({m['n']})"
        if p==best_cfg: s=f'[{s}]'  # mark best
        vals.append(f'{s:>16}')
    print(f'{sym:<6}  '+'  '.join(vals))
print()

# Classification summary
grades={'A':[],'B':[],'C':[],'D':[]}
for sym,s in summary.items():
    grades[s['grade']].append(sym)

print('Grade breakdown:')
print(f"  A (no change needed)          : {', '.join(grades['A']) or 'none'}")
print(f"  B (small improvement)         : {', '.join(grades['B']) or 'none'}")
print(f"  C (significant improvement)   : {', '.join(grades['C']) or 'none'}")
print(f"  D (global settings unsuitable): {', '.join(grades['D']) or 'none'}")
print()

# Recommendations
keep_unchanged=[sym for sym,s in summary.items() if s['rec']=='KEEP (unchanged)']
keep_optimized=[sym for sym,s in summary.items() if s['rec']=='KEEP (optimized)']
remove=[sym for sym,s in summary.items() if s['rec']=='REMOVE']

print('Recommendations:')
print(f"  KEEP (unchanged) : {', '.join(keep_unchanged) or 'none'}")
print(f"  KEEP (optimized) : {', '.join(keep_optimized) or 'none'}")
print(f"  REMOVE           : {', '.join(remove) or 'none'}")
print()

# Estimated portfolio improvement if custom settings are allowed
# Compare: sum of per-symbol TotalR with BL params vs sum with optimized params
# Only for non-REMOVE symbols
non_remove=[sym for sym in ALL_SYMS if sym in summary and summary[sym]['rec']!='REMOVE']
bl_total=sum(summary[sym]['bl']['tr'] for sym in non_remove)
opt_total=sum(summary[sym]['best_m']['tr'] for sym in non_remove)
delta_r=opt_total-bl_total
print(f'Estimated improvement (custom settings, per-symbol sum, non-REMOVE symbols only):')
print(f'  BL params total  : {bl_total:+.2f}R across {len(non_remove)} symbols')
print(f'  Optimized total  : {opt_total:+.2f}R across {len(non_remove)} symbols')
print(f'  Estimated delta  : {delta_r:+.2f}R')
print(f'  Note: per-symbol sum only; portfolio ordering / MaxDD effects not captured.')
print()

# ── CSV output ────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_CSV),exist_ok=True)
with open(OUT_CSV,'w',newline='',encoding='utf-8') as f:
    cols=['symbol','in_bl','grade','recommendation',
          'bl_n','bl_pf','bl_tr','bl_dd','bl_wr',
          'best_config','best_n','best_pf','best_tr','best_dd','best_wr',
          'delta_pf','delta_tr']
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
    for sym in ALL_SYMS:
        if sym not in summary: continue
        s=summary[sym]; bl=s['bl']; bm=s['best_m']
        w.writerow({'symbol':sym,'in_bl':'YES' if sym in BL_SYMS else 'NO',
                    'grade':s['grade'],'recommendation':s['rec'],
                    'bl_n':bl['n'],'bl_pf':f"{bl['pf']:.2f}" if bl['pf']!=float('inf') else 'inf',
                    'bl_tr':f"{bl['tr']:.2f}",'bl_dd':f"{bl['dd']:.2f}",'bl_wr':f"{bl['wr']:.1f}",
                    'best_config':s['best_cfg'],
                    'best_n':bm['n'],'best_pf':f"{bm['pf']:.2f}" if bm['pf']!=float('inf') else 'inf',
                    'best_tr':f"{bm['tr']:.2f}",'best_dd':f"{bm['dd']:.2f}",'best_wr':f"{bm['wr']:.1f}",
                    'delta_pf':f"{bm['pf']-bl['pf']:.2f}" if bl['pf']!=float('inf') else 'n/a',
                    'delta_tr':f"{bm['tr']-bl['tr']:.2f}"})
print(f'CSV written: {OUT_CSV}')

# ── MD output ─────────────────────────────────────────────────────────────────
grade_desc={'A':'A — already optimal (no change needed)',
            'B':'B — small improvement available',
            'C':'C — significant improvement from custom settings',
            'D':'D — current global settings unsuitable'}

with open(OUT_MD,'w',encoding='utf-8') as f:
    f.write('# Phase 8B — Symbol-Level Optimization\n\n')
    f.write(f'**IS window:** 2025-09-17 → 2026-04-30  \n')
    f.write(f'**Symbols tested:** {len(ALL_SYMS)} ({", ".join(ALL_SYMS)})  \n')
    f.write(f'**Parameter sets:** {len(PARAM_SETS)} (all subsets of H-01, H-02, H-04)  \n\n')

    f.write('## Grade Legend\n\n')
    f.write('| Grade | Meaning |\n|-------|--------|\n')
    f.write('| A | Already optimal — global settings work well, no customization needed |\n')
    f.write('| B | Small improvement — global settings acceptable, optimization gives marginal gain |\n')
    f.write('| C | Significant improvement — symbol viable only with custom settings |\n')
    f.write('| D | Unsuitable — global settings insufficient, optimization does not help enough |\n\n')
    f.write('> **KEEP (optimized):** symbol becomes profitable after optimization → do NOT remove\n')
    f.write('> **REMOVE:** symbol remains weak after optimization\n\n')
    f.write('---\n\n')

    f.write('## Results Summary\n\n')
    f.write('| Symbol | BL_n | BL_PF | BL_TR | BL_DD | Best Config | Best_n | Best_PF | Best_TR | Best_DD | ΔPF | ΔTR | Grade | Recommendation |\n')
    f.write('|--------|-----:|------:|------:|------:|-------------|-------:|--------:|--------:|--------:|----:|----:|:-----:|----------------|\n')
    for sym in ALL_SYMS:
        if sym not in summary: continue
        s=summary[sym]; bl=s['bl']; bm=s['best_m']
        dpf=bm['pf']-bl['pf'] if bl['pf']!=float('inf') else 0.0
        dtr=bm['tr']-bl['tr']
        in_bl_mark='✓' if sym in BL_SYMS else '+'
        grade_b='**'+s['grade']+'**' if s['grade'] in ('C','D') else s['grade']
        rec_b='**'+s['rec']+'**' if 'REMOVE' in s['rec'] else s['rec']
        f.write(f"| {sym}{in_bl_mark} | {bl['n']} | {pf_str(bl['pf'])} | {bl['tr']:+.2f}R | "
                f"{bl['dd']:.2f}R | {s['best_cfg']} | {bm['n']} | {pf_str(bm['pf'])} | "
                f"{bm['tr']:+.2f}R | {bm['dd']:.2f}R | {dpf:+.2f} | {dtr:+.2f}R | "
                f"{grade_b} | {rec_b} |\n")
    f.write('\n✓ = current BL_SYMS  + = candidate for addition\n\n---\n\n')

    f.write('## Parameter Sweep by Symbol (TotalR, [best config marked])\n\n')
    f.write('| Symbol | BL | H-01 | H-02 | H-04 | H-01+H-02 | H-01+H-04 | H-02+H-04 | H-01+H-02+H-04 |\n')
    f.write('|--------|----:|-----:|-----:|-----:|----------:|----------:|----------:|---------------:|\n')
    for sym in ALL_SYMS:
        if sym not in summary: continue
        rows=summary[sym]['rows']; best_cfg=summary[sym]['best_cfg']
        cells=[]
        for p in PARAM_SETS.keys():
            m=rows[p]; v=f'{m["tr"]:+.2f}R({m["n"]})'
            if p==best_cfg: v=f'**{v}**'
            cells.append(v)
        f.write(f"| {sym} | {'|'.join(cells)} |\n")
    f.write('\n---\n\n')

    f.write('## Grade Breakdown\n\n')
    for g in ['A','B','C','D']:
        syms_in_grade=[sym for sym,s in summary.items() if s['grade']==g]
        f.write(f'### {grade_desc[g]}\n\n')
        if not syms_in_grade: f.write('*None*\n\n'); continue
        for sym in syms_in_grade:
            s=summary[sym]; bl=s['bl']; bm=s['best_m']
            f.write(f'- **{sym}**: BL → PF {pf_str(bl["pf"])}, TotalR {bl["tr"]:+.2f}R  ')
            if s['best_cfg']!='BL':
                f.write(f'Best ({s["best_cfg"]}) → PF {pf_str(bm["pf"])}, TotalR {bm["tr"]:+.2f}R  ')
            f.write(f'→ **{s["rec"]}**\n')
        f.write('\n')
    f.write('---\n\n')

    f.write('## Estimated Portfolio Improvement\n\n')
    f.write(f'Per-symbol TotalR sum (KEEP symbols only, excluding REMOVE):\n\n')
    f.write(f'| | Symbols | Sum TotalR |\n|---|---|---:|\n')
    f.write(f'| Global BL params | {", ".join(non_remove)} | {bl_total:+.2f}R |\n')
    f.write(f'| Per-symbol optimal | {", ".join(non_remove)} | {opt_total:+.2f}R |\n')
    f.write(f'| **Estimated delta** | — | **{delta_r:+.2f}R** |\n\n')
    f.write('> *Per-symbol sum only. Portfolio-level MaxDD and signal ordering effects not captured.*\n\n')
    f.write('---\n\n')
    f.write('**Next step:** OOS validation requires explicit user approval.\n')

print(f'MD  written: {OUT_MD}')

# ── final print (as specified) ────────────────────────────────────────────────
print()
print('='*72)
print('PHASE 8B SUMMARY')
print('='*72)
print(f"Symbols to keep unchanged    : {', '.join(keep_unchanged) or 'none'}")
print(f"Symbols requiring custom     : {', '.join(keep_optimized) or 'none'}")
print(f"  settings (KEEP optimized)")
print(f"Symbols recommended REMOVE   : {', '.join(remove) or 'none'}")
print(f"Estimated portfolio improvement if custom settings are allowed:")
print(f"  Per-symbol delta: {delta_r:+.2f}R vs BL params across {len(non_remove)} kept symbols")
print('='*72)
