"""Phase 9 -- Out-of-Sample (OOS) Validation.
First inspection of OOS data. No tuning. No modifications after seeing results.
Candidates from Phase 7B OOS advancement list + Phase 8B SYMBOL_SPECIFIC portfolio.
OOS window: 2026-05-01 -> 2026-06-25 (38 trading days).
"""
import os, json, sys, csv, itertools
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_CSV = os.path.join('docs', 'results', 'phase9_oos_validation.csv')
OUT_MD  = os.path.join('docs', 'results', 'phase9_oos_validation.md')
RDIR    = 'chart_data_research'

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')

# ── OOS window (first inspection) ─────────────────────────────────────────────
OOS_START   = date(2026, 5, 1)
OOS_END     = date(2026, 6, 25)
OOS_DAYS    = 38
OOS_WEEKS   = OOS_DAYS / 5.0
OOS_MONTHS  = ['2026-05', '2026-06']

# ── constants (locked from IS research — do not change) ───────────────────────
MIN_LB = 60; SL_ATR = 1.5; TP_ATR = 2.7; MAX_HOLD = 40
SESS_OPEN_ET = 0; SESS_ORB_DONE_ET = 30

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

# ── Phase 8B per-symbol optimal params (locked — derived IS-only) ─────────────
SYM_PARAMS = {
    'AMZN': {**BL, 'rvol_min': 1.4},
    'CRM':  {**BL},
    'LLY':  {**BL, 'rvol_min': 1.4, 'orb_range_min': 1.0},
    'META': {**BL, 'orb_range_min': 1.0},
    'MSFT': {**BL, 'rvol_min': 1.4, 'sess_brk_end_et': 150},
    'NFLX': {**BL, 'rvol_min': 1.4, 'sess_brk_end_et': 150},
    'NVDA': {**BL, 'orb_range_min': 1.0, 'sess_brk_end_et': 150},
    'QQQ':  {**BL, 'rvol_min': 1.4, 'orb_range_min': 1.0},
    'AAPL': {**BL, 'rvol_min': 1.4, 'orb_range_min': 1.0, 'sess_brk_end_et': 150},
    # PANW: REMOVE — not in portfolio
}

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

# ── scan (production-matched) ─────────────────────────────────────────────────

def scan_sym(sym, pc, bias, params, win_start, win_end):
    bars=pc['bars']; atrs=pc['atrs']; adxs=pc['adxs']
    rvs=pc['rvs']; ema20s=pc['ema20s']; orb=pc['orb']
    sess_end=params['sess_brk_end_et']
    cands=[]; emitted=set()
    for i in range(MIN_LB,len(bars)):
        b=bars[i]; td=b['td']; dt=b['dt']; sm=b['sm']
        if td<win_start or td>win_end: continue
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
    all_cands=[]
    for sym in sym_list:
        if sym not in precomp: continue
        all_cands.extend(scan_sym(sym,precomp[sym],bias,params,OOS_START,OOS_END))
    all_cands.sort(key=lambda x:x['dt'])
    return [{'r':simulate(precomp[t['sym']]['bars'],t['bi'],t['dir'],t['entry'],t['atr']),
             'sym':t['sym'],'dt':t['dt'],'ym':t['ym'],'dir':t['dir']} for t in all_cands]

def run_sym_specific(sym_param_map, precomp, bias):
    all_cands=[]
    for sym,params in sym_param_map.items():
        if sym not in precomp: continue
        all_cands.extend(scan_sym(sym,precomp[sym],bias,params,OOS_START,OOS_END))
    all_cands.sort(key=lambda x:x['dt'])
    return [{'r':simulate(precomp[t['sym']]['bars'],t['bi'],t['dir'],t['entry'],t['atr']),
             'sym':t['sym'],'dt':t['dt'],'ym':t['ym'],'dir':t['dir']} for t in all_cands]

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
    if not trades: return dict(n=0,wr=0.0,pf=0.0,tr=0.0,dd=0.0,avg_r=0.0)
    rv=[t['r'] for t in trades]; n=len(rv)
    wins=sum(1 for r in rv if r>0)
    gw=sum(r for r in rv if r>0); gl=abs(sum(r for r in rv if r<0))
    pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
    tr=sum(rv); eq=pk=dd=0.0
    for r in rv: eq+=r; pk=max(pk,eq); dd=max(dd,pk-eq)
    return dict(n=n,wr=100.0*wins/n,pf=pf,tr=tr,dd=dd,avg_r=tr/n)

def monthly_breakdown(trades):
    by_ym=defaultdict(list)
    for t in trades: by_ym[t['ym']].append(t['r'])
    rows={}
    for ym in OOS_MONTHS:
        rv=by_ym.get(ym,[])
        n=len(rv); wins=sum(1 for r in rv if r>0)
        gw=sum(r for r in rv if r>0); gl=abs(sum(r for r in rv if r<0))
        pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
        wr=100.0*wins/n if n>0 else 0.0
        rows[ym]=dict(n=n,wr=wr,pf=pf,tr=sum(rv))
    return rows

def sym_contribution(trades):
    by_sym=defaultdict(list)
    for t in trades: by_sym[t['sym']].append(t['r'])
    out={}
    for sym,rv in by_sym.items():
        n=len(rv); wins=sum(1 for r in rv if r>0)
        gw=sum(r for r in rv if r>0); gl=abs(sum(r for r in rv if r<0))
        pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
        tr=sum(rv)
        out[sym]=dict(n=n,wr=100.0*wins/n if n>0 else 0.0,pf=pf,tr=tr,avg_r=tr/n if n>0 else 0.0)
    return out

def pf_s(v): return f'{v:.2f}' if v!=float('inf') else 'inf'

def best_worst_sym(sc):
    if not sc: return 'none','none'
    best=max(sc,key=lambda s:sc[s]['tr'])
    worst=min(sc,key=lambda s:sc[s]['tr'])
    return best,worst

def gate_check(m, bl_m):
    """Returns (passes, flags dict)."""
    pf_ok  = m['pf']  > bl_m['pf']
    tr_ok  = m['tr']  > bl_m['tr']
    dd_ok  = m['dd'] <= bl_m['dd']
    n_ok   = m['n']  >= bl_m['n']
    return all([pf_ok,tr_ok,dd_ok,n_ok]), dict(pf=pf_ok,tr=tr_ok,dd=dd_ok,n=n_ok)

# ── candidate definitions ─────────────────────────────────────────────────────

GLOBAL_CANDS = [
    ('H-02+H-05+H-08', ('H-02','H-05','H-08')),
    ('H-01+H-05+H-08', ('H-01','H-05','H-08')),
    ('H-05+H-06+H-08', ('H-05','H-06','H-08')),
    ('H-01+H-02+H-08', ('H-01','H-02','H-08')),
    ('H-02+H-08',      ('H-02','H-08')),
    ('H-01+H-04+H-08', ('H-01','H-04','H-08')),
    ('H-02+H-05',      ('H-02','H-05')),
]

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 9 — Out-of-Sample Validation')
print('='*72)
print(f'OOS window : {OOS_START} → {OOS_END}  ({OOS_DAYS} trading days)')
print(f'Candidates : {len(GLOBAL_CANDS)} global + 1 symbol-specific')
print('='*72)
print()

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

# Run baseline
print('Running BASELINE...', flush=True)
bl_trades=run_config(BL_SYMS,BL,precomp,bias)
bl_m=overall_metrics(bl_trades)
bl_monthly=monthly_breakdown(bl_trades)
bl_sc=sym_contribution(bl_trades)
bl_best,bl_worst=best_worst_sym(bl_sc)

# Run global candidates
cand_results=[]
for label,h_ids in GLOBAL_CANDS:
    params,sym_list=make_config(h_ids)
    trades=run_config(sym_list,params,precomp,bias)
    m=overall_metrics(trades)
    monthly=monthly_breakdown(trades)
    sc=sym_contribution(trades)
    best_sym,worst_sym=best_worst_sym(sc)
    passes,flags=gate_check(m,bl_m)
    cand_results.append({'label':label,'type':'GLOBAL','sym_list':sym_list,
                         'trades':trades,'m':m,'monthly':monthly,'sc':sc,
                         'best_sym':best_sym,'worst_sym':worst_sym,
                         'passes':passes,'flags':flags})

# Run symbol-specific
print('Running SYMBOL_SPECIFIC_PORTFOLIO...', flush=True)
ss_trades=run_sym_specific(SYM_PARAMS,precomp,bias)
ss_m=overall_metrics(ss_trades)
ss_monthly=monthly_breakdown(ss_trades)
ss_sc=sym_contribution(ss_trades)
ss_best,ss_worst=best_worst_sym(ss_sc)
ss_passes,ss_flags=gate_check(ss_m,bl_m)
cand_results.append({'label':'SYMBOL_SPECIFIC','type':'SYM_SPECIFIC',
                     'sym_list':list(SYM_PARAMS.keys()),
                     'trades':ss_trades,'m':ss_m,'monthly':ss_monthly,'sc':ss_sc,
                     'best_sym':ss_best,'worst_sym':ss_worst,
                     'passes':ss_passes,'flags':ss_flags})

print()

# ── console output ─────────────────────────────────────────────────────────────
RULE = '-'*90

def print_candidate(label, m, monthly, sc, best_sym, worst_sym, passes, flags, syms):
    print(f'  Trades     : {m["n"]}  ({m["n"]/OOS_DAYS:.3f}/day | {m["n"]/OOS_WEEKS:.2f}/week)')
    print(f'  WR         : {m["wr"]:.1f}%')
    print(f'  PF         : {pf_s(m["pf"])}')
    print(f'  TotalR     : {m["tr"]:+.2f}R')
    print(f'  MaxDD      : {m["dd"]:.2f}R')
    print(f'  AvgR       : {m["avg_r"]:+.3f}R')
    print(f'  Symbols    : {", ".join(syms)}  ({len(syms)} total)')
    print()
    print('  Monthly Breakdown:')
    print(f'  {"Month":<10} {"n":>5} {"WR":>7} {"PF":>6} {"TotalR":>9}')
    print(f'  {"-"*40}')
    for ym in OOS_MONTHS:
        r=monthly.get(ym,{}); n=r.get('n',0)
        if n==0:
            print(f'  {ym:<10} {"0":>5} {"--":>7} {"--":>6} {"0.00R":>9}')
        else:
            print(f'  {ym:<10} {n:>5} {r["wr"]:>6.1f}% {pf_s(r["pf"]):>6} {r["tr"]:>+8.2f}R')
    print()
    print('  Symbol Contribution:')
    print(f'  {"Symbol":<8} {"n":>4} {"WR":>7} {"PF":>6} {"TotalR":>9} {"AvgR":>8}')
    print(f'  {"-"*48}')
    for sym in sorted(sc, key=lambda s:-sc[s]['tr']):
        s=sc[sym]
        print(f'  {sym:<8} {s["n"]:>4} {s["wr"]:>6.1f}% {pf_s(s["pf"]):>6} '
              f'{s["tr"]:>+8.2f}R {s["avg_r"]:>+7.3f}R')
    best_tr=sc[best_sym]['tr'] if best_sym in sc else 0
    worst_tr=sc[worst_sym]['tr'] if worst_sym in sc else 0
    print(f'  Best symbol  : {best_sym} ({best_tr:+.2f}R)')
    print(f'  Worst symbol : {worst_sym} ({worst_tr:+.2f}R)')
    print()
    flag_str = '  '.join([f'PF:{"PASS" if flags["pf"] else "FAIL"}',
                           f'TR:{"PASS" if flags["tr"] else "FAIL"}',
                           f'DD:{"PASS" if flags["dd"] else "FAIL"}',
                           f'N:{"PASS"  if flags["n"]  else "FAIL"}'])
    verdict = 'OOS PASS ✓' if passes else 'OOS FAIL ✗'
    print(f'  Gate result  : {verdict}  [{flag_str}]')

# Print baseline
print('BASELINE')
print(RULE)
print_candidate('BASELINE',bl_m,bl_monthly,bl_sc,bl_best,bl_worst,
                {'pf':True,'tr':True,'dd':True,'n':True},
                {'pf':True,'tr':True,'dd':True,'n':True},BL_SYMS)

# Print each candidate
for cr in cand_results:
    print(f'{cr["label"]}  [{cr["type"]}]')
    print(RULE)
    print_candidate(cr['label'],cr['m'],cr['monthly'],cr['sc'],
                    cr['best_sym'],cr['worst_sym'],cr['passes'],cr['flags'],cr['sym_list'])

# Summary table
print()
print('='*90)
print('OOS SUMMARY TABLE')
print('='*90)
H = f'{"Candidate":<24} {"Type":<14} {"n":>4} {"T/Day":>6} {"WR":>6} {"PF":>5} {"TotalR":>9} {"MaxDD":>7}  Result'
print(H); print('-'*90)
print(f'{"BASELINE":<24} {"—":<14} {bl_m["n"]:>4} {bl_m["n"]/OOS_DAYS:>6.3f} '
      f'{bl_m["wr"]:>5.1f}% {pf_s(bl_m["pf"]):>5} {bl_m["tr"]:>+8.2f}R {bl_m["dd"]:>6.2f}R  —')
for cr in cand_results:
    m=cr['m']; res='PASS ✓' if cr['passes'] else 'FAIL ✗'
    print(f'{cr["label"]:<24} {cr["type"]:<14} {m["n"]:>4} {m["n"]/OOS_DAYS:>6.3f} '
          f'{m["wr"]:>5.1f}% {pf_s(m["pf"]):>5} {m["tr"]:>+8.2f}R {m["dd"]:>6.2f}R  {res}')
print()

# Baseline vs GLOBAL_PORTFOLIO vs SYMBOL_SPECIFIC_PORTFOLIO
best_global=max((cr for cr in cand_results if cr['type']=='GLOBAL'),
                key=lambda c:(c['m']['tr'],c['m']['pf']),
                default=None)
ss_cand=next((cr for cr in cand_results if cr['type']=='SYM_SPECIFIC'),None)
print('BASELINE vs GLOBAL_PORTFOLIO vs SYMBOL_SPECIFIC_PORTFOLIO')
print('-'*72)
rows=[('BASELINE',bl_m,None),
      (best_global['label'] if best_global else '—',
       best_global['m'] if best_global else None,'GLOBAL'),
      ('SYMBOL_SPECIFIC',ss_m,'SYM_SPECIFIC')]
for lbl,m,t in rows:
    if m is None: continue
    print(f'  {lbl:<28} n={m["n"]:>3}  PF={pf_s(m["pf"]):>5}  TR={m["tr"]:>+7.2f}R  DD={m["dd"]:.2f}R  WR={m["wr"]:.1f}%')
print()

# Final OOS winner
winners=[cr for cr in cand_results if cr['passes']]
print('OOS WINNER(S)')
print('-'*72)
if not winners:
    print('  No candidate passed all four gate conditions.')
    print(f'  Gate required: PF > {pf_s(bl_m["pf"])}  TR > {bl_m["tr"]:+.2f}R  '
          f'DD <= {bl_m["dd"]:.2f}R  N >= {bl_m["n"]}')
    # Show closest
    scored=sorted(cand_results,key=lambda c:sum(c['flags'].values()),reverse=True)
    top=scored[0]; passed_count=sum(top['flags'].values())
    print(f'  Closest candidate: {top["label"]} ({passed_count}/4 gate conditions met)')
    gate_detail='  '.join([f'PF:{"✓" if top["flags"]["pf"] else "✗"}',
                            f'TR:{"✓" if top["flags"]["tr"] else "✗"}',
                            f'DD:{"✓" if top["flags"]["dd"] else "✗"}',
                            f'N:{"✓"  if top["flags"]["n"]  else "✗"}'])
    print(f'  Gate detail : {gate_detail}')
    print(f'  PF {pf_s(top["m"]["pf"])} vs baseline {pf_s(bl_m["pf"])}  |  '
          f'TR {top["m"]["tr"]:+.2f}R vs baseline {bl_m["tr"]:+.2f}R  |  '
          f'DD {top["m"]["dd"]:.2f}R vs baseline {bl_m["dd"]:.2f}R  |  '
          f'N {top["m"]["n"]} vs baseline {bl_m["n"]}')
else:
    best_w=max(winners,key=lambda c:(c['m']['pf'],c['m']['tr']))
    for w in winners:
        star=' ← TOP' if w is best_w else ''
        print(f'  PASS: {w["label"]}  PF={pf_s(w["m"]["pf"])}  TR={w["m"]["tr"]:+.2f}R  '
              f'DD={w["m"]["dd"]:.2f}R{star}')
print()

# ── CSV output ────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_CSV),exist_ok=True)
all_rows=[{'label':'BASELINE','type':'BASELINE','n_syms':len(BL_SYMS),
           'n':bl_m['n'],'tpd':f"{bl_m['n']/OOS_DAYS:.3f}",'tpw':f"{bl_m['n']/OOS_WEEKS:.2f}",
           'wr':f"{bl_m['wr']:.1f}",'pf':pf_s(bl_m['pf']),'tr':f"{bl_m['tr']:.2f}",
           'dd':f"{bl_m['dd']:.2f}",'avg_r':f"{bl_m['avg_r']:.3f}",
           'best_sym':bl_best,'worst_sym':bl_worst,
           'may_tr':f"{bl_monthly['2026-05']['tr']:.2f}" if bl_monthly.get('2026-05') else '0',
           'jun_tr':f"{bl_monthly['2026-06']['tr']:.2f}" if bl_monthly.get('2026-06') else '0',
           'gate':'BASELINE'}]
for cr in cand_results:
    m=cr['m']
    all_rows.append({'label':cr['label'],'type':cr['type'],'n_syms':len(cr['sym_list']),
                     'n':m['n'],'tpd':f"{m['n']/OOS_DAYS:.3f}",'tpw':f"{m['n']/OOS_WEEKS:.2f}",
                     'wr':f"{m['wr']:.1f}",'pf':pf_s(m['pf']),'tr':f"{m['tr']:.2f}",
                     'dd':f"{m['dd']:.2f}",'avg_r':f"{m['avg_r']:.3f}",
                     'best_sym':cr['best_sym'],'worst_sym':cr['worst_sym'],
                     'may_tr':f"{cr['monthly']['2026-05']['tr']:.2f}" if cr['monthly'].get('2026-05') else '0',
                     'jun_tr':f"{cr['monthly']['2026-06']['tr']:.2f}" if cr['monthly'].get('2026-06') else '0',
                     'gate':'PASS' if cr['passes'] else 'FAIL'})
cols=['label','type','n_syms','n','tpd','tpw','wr','pf','tr','dd','avg_r',
      'best_sym','worst_sym','may_tr','jun_tr','gate']
with open(OUT_CSV,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
    for r in all_rows: w.writerow(r)
print(f'CSV written: {OUT_CSV}')

# ── MD output ─────────────────────────────────────────────────────────────────
with open(OUT_MD,'w',encoding='utf-8') as f:
    f.write('# Phase 9 — Out-of-Sample Validation\n\n')
    f.write(f'**OOS window:** {OOS_START} → {OOS_END} ({OOS_DAYS} trading days, 2 months)  \n')
    f.write(f'**Candidates:** {len(GLOBAL_CANDS)} global parameter portfolios + 1 symbol-specific portfolio  \n')
    f.write(f'**Gate:** PF > baseline OOS PF  AND  TotalR > baseline OOS TotalR  '
            f'AND  MaxDD ≤ baseline OOS MaxDD  AND  n ≥ baseline OOS n  \n\n')
    f.write('> First and only inspection of OOS data. No modifications made after seeing results.\n\n---\n\n')

    f.write('## OOS Baseline\n\n')
    f.write('| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |\n')
    f.write('|--:|------:|-------:|---:|---:|-------:|------:|-----:|\n')
    f.write(f'| {bl_m["n"]} | {bl_m["n"]/OOS_DAYS:.3f} | {bl_m["n"]/OOS_WEEKS:.2f} | '
            f'{bl_m["wr"]:.1f}% | {pf_s(bl_m["pf"])} | {bl_m["tr"]:+.2f}R | '
            f'{bl_m["dd"]:.2f}R | {bl_m["avg_r"]:+.3f}R |\n\n')
    f.write('**Baseline monthly breakdown:**\n\n')
    f.write('| Month | n | WR | PF | TotalR |\n|-------|--:|---:|---:|-------:|\n')
    for ym in OOS_MONTHS:
        r=bl_monthly.get(ym,{}); n=r.get('n',0)
        if n==0: f.write(f'| {ym} | 0 | -- | -- | 0.00R |\n')
        else: f.write(f'| {ym} | {n} | {r["wr"]:.1f}% | {pf_s(r["pf"])} | {r["tr"]:+.2f}R |\n')
    f.write('\n---\n\n')

    f.write('## Results Summary\n\n')
    f.write('| Candidate | Type | Syms | n | T/Day | WR | PF | TotalR | MaxDD | AvgR | May | Jun | Gate |\n')
    f.write('|-----------|------|-----:|--:|------:|---:|---:|-------:|------:|-----:|----:|----:|:----:|\n')
    for cr in cand_results:
        m=cr['m']; mo=cr['monthly']
        may_r=mo.get('2026-05',{}).get('tr',0); jun_r=mo.get('2026-06',{}).get('tr',0)
        gate_s='**PASS ✓**' if cr['passes'] else 'FAIL ✗'
        f.write(f"| {cr['label']} | {cr['type']} | {len(cr['sym_list'])} | {m['n']} | "
                f"{m['n']/OOS_DAYS:.3f} | {m['wr']:.1f}% | {pf_s(m['pf'])} | {m['tr']:+.2f}R | "
                f"{m['dd']:.2f}R | {m['avg_r']:+.3f}R | {may_r:+.2f}R | {jun_r:+.2f}R | {gate_s} |\n")
    f.write('\n---\n\n')

    f.write('## Detailed Results per Candidate\n\n')
    all_cands_detail=[('BASELINE',bl_m,bl_monthly,bl_sc,bl_best,bl_worst,BL_SYMS,True,{})]
    for cr in cand_results:
        all_cands_detail.append((cr['label'],cr['m'],cr['monthly'],cr['sc'],
                                  cr['best_sym'],cr['worst_sym'],cr['sym_list'],
                                  cr['passes'],cr['flags']))

    for (lbl,m,monthly,sc,best_sym,worst_sym,syms,passes,flags) in all_cands_detail:
        f.write(f'### {lbl}\n\n')
        if lbl!='BASELINE':
            gate_md='**OOS PASS ✓**' if passes else 'OOS FAIL ✗'
            flag_md=' | '.join([f'PF {"✓" if flags["pf"] else "✗"}',
                                  f'TR {"✓" if flags["tr"] else "✗"}',
                                  f'DD {"✓" if flags["dd"] else "✗"}',
                                  f'N {"✓" if flags["n"] else "✗"}'])
            f.write(f'**Gate:** {gate_md}  [{flag_md}]\n\n')
        f.write(f'Symbols ({len(syms)}): {", ".join(syms)}\n\n')
        f.write('| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |\n')
        f.write('|--:|------:|-------:|---:|---:|-------:|------:|-----:|\n')
        f.write(f'| {m["n"]} | {m["n"]/OOS_DAYS:.3f} | {m["n"]/OOS_WEEKS:.2f} | '
                f'{m["wr"]:.1f}% | {pf_s(m["pf"])} | {m["tr"]:+.2f}R | '
                f'{m["dd"]:.2f}R | {m["avg_r"]:+.3f}R |\n\n')
        f.write('**Monthly breakdown:**\n\n')
        f.write('| Month | n | WR | PF | TotalR |\n|-------|--:|---:|---:|-------:|\n')
        for ym in OOS_MONTHS:
            r=monthly.get(ym,{}); n=r.get('n',0)
            if n==0: f.write(f'| {ym} | 0 | -- | -- | 0.00R |\n')
            else: f.write(f'| {ym} | {n} | {r["wr"]:.1f}% | {pf_s(r["pf"])} | {r["tr"]:+.2f}R |\n')
        f.write('\n**Symbol contribution:**\n\n')
        f.write('| Symbol | n | WR | PF | TotalR | AvgR |\n')
        f.write('|--------|--:|---:|---:|-------:|-----:|\n')
        for sym in sorted(sc,key=lambda s:-sc[s]['tr']):
            s=sc[sym]
            best_w='🏆 ' if sym==best_sym else ('⚠ ' if sym==worst_sym else '')
            f.write(f'| {best_w}{sym} | {s["n"]} | {s["wr"]:.1f}% | {pf_s(s["pf"])} | '
                    f'{s["tr"]:+.2f}R | {s["avg_r"]:+.3f}R |\n')
        f.write(f'\n**Best symbol:** {best_sym}  **Worst symbol:** {worst_sym}\n\n---\n\n')

    # Winners section
    f.write('## OOS Gate Results\n\n')
    f.write(f'Gate conditions (vs baseline OOS):\n')
    f.write(f'- PF > {pf_s(bl_m["pf"])}\n- TotalR > {bl_m["tr"]:+.2f}R\n'
            f'- MaxDD ≤ {bl_m["dd"]:.2f}R\n- Trades ≥ {bl_m["n"]}\n\n')
    winners2=[cr for cr in cand_results if cr['passes']]
    if not winners2:
        f.write('**No candidate passed all four gate conditions.**\n\n')
        scored=sorted(cand_results,key=lambda c:sum(c['flags'].values()),reverse=True)
        top=scored[0]
        f.write(f'Closest: {top["label"]} with {sum(top["flags"].values())}/4 conditions met.\n\n')
    else:
        best_w2=max(winners2,key=lambda c:(c['m']['pf'],c['m']['tr']))
        f.write('| Candidate | PF | TotalR | MaxDD | n | Winner |\n')
        f.write('|-----------|---:|-------:|------:|--:|:------:|\n')
        for w in winners2:
            star='**← TOP**' if w is best_w2 else ''
            f.write(f'| {w["label"]} | {pf_s(w["m"]["pf"])} | {w["m"]["tr"]:+.2f}R | '
                    f'{w["m"]["dd"]:.2f}R | {w["m"]["n"]} | {star} |\n')
    f.write('\n> Next step: production recommendation requires explicit user approval.\n')

print(f'MD  written: {OUT_MD}')
print()
print('Phase 9 complete. Stopped before production recommendation.')
