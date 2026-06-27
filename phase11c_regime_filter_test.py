"""Phase 11C — Regime Filter Backtest.

Tests the regime signals identified in Phase 11B as day-level pre-filters.
A pre-filter SKIPS all signals on days that do not meet the condition.
NO recommendations. NO parameter changes. NO strategy modifications.
Reports IS and OOS performance per filter, side-by-side.

Filters tested (all derived from Phase 11B findings, no tuning):
  F-REG     : market_regime != BEAR  (exclude all BEAR days)
  F-RR      : range_ratio 0.80-1.20  (normal daily range only)
  F-ORB     : orb_range_avg_atr >= 3.5  (wide ORB days only)
  F-ADX     : spy_adx14 >= 35  (strong daily trend only)
  F-RR+REG  : F-RR AND F-REG
  F-ORB+REG : F-ORB AND F-REG
  F-RR+ORB  : F-RR AND F-ORB
  F-ALL     : F-REG AND F-RR AND F-ORB
"""
import os, json, sys, csv
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_CSV = os.path.join('docs', 'research', 'phase11c_regime_filter_test.csv')
OUT_MD  = os.path.join('docs', 'research', 'phase11c_regime_filter_test.md')
DB_CSV  = os.path.join('docs', 'research', 'phase11_regime_database.csv')
RDIR    = 'chart_data_research'

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')

IS_START  = date(2025, 9, 17); IS_END  = date(2026, 4, 30)
OOS_START = date(2026, 5,  1); OOS_END = date(2026, 6, 25)
WIN_START = IS_START; WIN_END = OOS_END

MIN_LB=60; SL_ATR=1.5; TP_ATR=2.7; MAX_HOLD=40
SESS_OPEN_ET=0; SESS_ORB_DONE_ET=30

BL_SYMS = ['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ']
BL = dict(adx_min=30.0, rvol_min=1.5, orb_range_min=2.0,
          ema20_dist_min=1.95, break_dist_min=0.05,
          body_atr=0.25, sess_brk_end_et=120)

# ── filter definitions (locked from Phase 11B — not tuned here) ───────────────
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
    'F-REG':     'Exclude BEAR regime days (SPY+QQQ EMA9 < EMA20)',
    'F-RR':      'Range ratio 0.80-1.20 (normal daily range)',
    'F-ORB':     'ORB avg ≥ 3.5 ATR (wide opening range)',
    'F-ADX':     'SPY daily ADX ≥ 35 (strong trend)',
    'F-RR+REG':  'F-RR AND F-REG',
    'F-ORB+REG': 'F-ORB AND F-REG',
    'F-RR+ORB':  'F-RR AND F-ORB',
    'F-ALL':     'F-REG AND F-RR AND F-ORB',
}

# ── loading ───────────────────────────────────────────────────────────────────

def load_db():
    rows = {}
    with open(DB_CSV, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            rows[r['date']] = {
                'window':           r['window'],
                'market_regime':    r['market_regime'],
                'spy_range_ratio':  float(r['spy_range_ratio']),
                'spy_adx14':        float(r['spy_adx14']),
                'orb_avg_atr':      float(r['orb_range_avg_atr']) if r['orb_range_avg_atr'] else None,
                'breadth_pct':      float(r['breadth_pct']) if r['breadth_pct'] else None,
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

# ── scan ──────────────────────────────────────────────────────────────────────

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
        if adx<params['adx_min']:              continue
        if rv <params['rvol_min']:             continue
        if body<params['body_atr']*atr:        continue
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

# ── metrics ───────────────────────────────────────────────────────────────────

def metrics(trades):
    if not trades:
        return dict(n=0,wr=0.0,pf=0.0,tr=0.0,dd=0.0,avg_r=0.0)
    rv=[t['r'] for t in trades]; n=len(rv)
    wins=sum(1 for r in rv if r>0)
    gw=sum(r for r in rv if r>0); gl=abs(sum(r for r in rv if r<0))
    pf=gw/gl if gl>0 else (float('inf') if gw>0 else 0.0)
    tr=sum(rv); eq=pk=dd=0.0
    for r in rv: eq+=r; pk=max(pk,eq); dd=max(dd,pk-eq)
    return dict(n=n,wr=100.0*wins/n,pf=pf,tr=tr,dd=dd,avg_r=tr/n)

def pf_s(v): return f'{v:.2f}' if v!=float('inf') else 'inf'

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 11C — Regime Filter Backtest')
print('='*72)
print('No recommendations. No parameter changes. No strategy modifications.')
print(f'Filters tested: {len(FILTERS)-1} regime pre-filters + baseline')
print()

# Load regime database
regime_db = load_db()
print(f'Regime DB loaded: {len(regime_db)} trading days')

# Load bars and precomp
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
print(f'Precomputed {len(precomp)} symbols.')

# Run full baseline scan once — collect all trades with dates
print('Running full scan (IS + OOS combined)...', flush=True)
all_cands=[]
for sym in BL_SYMS:
    if sym not in precomp: continue
    all_cands.extend(scan_sym(sym,precomp[sym],bias,BL))
all_cands.sort(key=lambda x:x['dt'])
all_trades=[{'dt':t['dt'],'sym':t['sym'],'dir':t['dir'],
             'r':simulate(precomp[t['sym']]['bars'],t['bi'],t['dir'],t['entry'],t['atr']),
             'window': 'IS' if date.fromisoformat(t['dt'])<=IS_END else 'OOS'}
            for t in all_cands]
print(f'Total trades: {len(all_trades)} '
      f'(IS: {sum(1 for t in all_trades if t["window"]=="IS")} '
      f'OOS: {sum(1 for t in all_trades if t["window"]=="OOS")})')
print()

# Count trading days per window
is_days_total  = sum(1 for d,v in regime_db.items()
                     if date.fromisoformat(d) <= IS_END and v['window']=='IS')
oos_days_total = sum(1 for d,v in regime_db.items()
                     if date.fromisoformat(d) >= OOS_START)

# ── apply each filter ─────────────────────────────────────────────────────────

results = {}  # filter_name -> {is: metrics, oos: metrics, is_days, oos_days}

for fname, ftest in FILTERS.items():
    # Days that pass the filter
    pass_dates = {dt for dt, drow in regime_db.items() if ftest(drow)}

    # Filter trades
    is_trades  = [t for t in all_trades if t['window']=='IS'  and t['dt'] in pass_dates]
    oos_trades = [t for t in all_trades if t['window']=='OOS' and t['dt'] in pass_dates]

    # Days that pass in each window
    is_days_pass  = sum(1 for d in regime_db
                        if date.fromisoformat(d) <= IS_END  and d in pass_dates)
    oos_days_pass = sum(1 for d in regime_db
                        if date.fromisoformat(d) >= OOS_START and d in pass_dates)

    results[fname] = {
        'is':        metrics(is_trades),
        'oos':       metrics(oos_trades),
        'is_days':   is_days_pass,
        'oos_days':  oos_days_pass,
        'is_trades_pct':  100.0*len(is_trades)/max(1,len([t for t in all_trades if t['window']=='IS'])),
        'oos_trades_pct': 100.0*len(oos_trades)/max(1,len([t for t in all_trades if t['window']=='OOS'])),
    }

# ── reference values ──────────────────────────────────────────────────────────
bl_is  = results['BASELINE']['is']
bl_oos = results['BASELINE']['oos']

# ── console output ────────────────────────────────────────────────────────────

def print_block(title, key):
    ref = bl_is if key == 'is' else bl_oos
    days_total = is_days_total if key == 'is' else oos_days_total
    win_label  = 'IS' if key == 'is' else 'OOS'
    print(f'\n{title}')
    print('-'*100)
    H=(f'{"Filter":<14} {"Days":>5} {"D%":>5} {"n":>5} {"T%":>5} '
       f'{"TotalR":>8} {"ΔvsBase":>8} {"AvgR/T":>8} {"WR":>7} {"PF":>6} {"MaxDD":>7}')
    print(H); print('-'*100)
    for fname in FILTERS:
        res = results[fname]
        m   = res[key]
        dp  = 100.0 * res[f'{key}_days'] / days_total if days_total else 0
        tp  = res[f'{key}_trades_pct']
        dtr = m['tr'] - ref['tr']
        mark = ' ←base' if fname == 'BASELINE' else ''
        print(f'{fname:<14} {res[f"{key}_days"]:>5} {dp:>4.0f}% {m["n"]:>5} {tp:>4.0f}% '
              f'{m["tr"]:>+8.2f} {dtr:>+8.2f} {m["avg_r"]:>+8.3f} '
              f'{m["wr"]:>6.1f}% {pf_s(m["pf"]):>6} {m["dd"]:>6.2f}R{mark}')

print_block('IS PERFORMANCE BY FILTER  (2025-09-17 → 2026-04-30)', 'is')
print_block('OOS PERFORMANCE BY FILTER (2026-05-01 → 2026-06-25)', 'oos')

# IS → OOS consistency table
print('\nIS → OOS DIRECTION CONSISTENCY')
print('-'*80)
print(f'{"Filter":<14} {"IS AvgR/T":>10} {"OOS AvgR/T":>11} {"IS PF":>7} '
      f'{"OOS PF":>7} {"IS→OOS":>10}')
print('-'*80)
for fname in FILTERS:
    is_m  = results[fname]['is']
    oos_m = results[fname]['oos']
    is_avg  = is_m['avg_r']
    oos_avg = oos_m['avg_r']
    # Consistency: both positive, both negative, or diverge
    if fname == 'BASELINE':
        cons = '—'
    elif is_avg > 0 and oos_avg > 0:
        if oos_avg >= is_avg * 0.50:
            cons = 'HOLDS ✓'
        else:
            cons = 'DEGRADES ~'
    elif is_avg > 0 and oos_avg <= 0:
        cons = 'REVERSES ✗'
    elif is_avg <= 0:
        cons = 'BAD IS'
    else:
        cons = '?'
    print(f'{fname:<14} {is_avg:>+10.3f} {oos_avg:>+11.3f} '
          f'{pf_s(is_m["pf"]):>7} {pf_s(oos_m["pf"]):>7} {cons:>10}')

# Monthly breakdown per filter for OOS
print('\nOOS MONTHLY DETAIL BY FILTER')
print('-'*65)
print(f'{"Filter":<14} {"May n":>6} {"May R":>8} {"Jun n":>6} {"Jun R":>8} {"Total R":>8}')
print('-'*65)
may_dates = {dt for dt,v in regime_db.items() if v['window']=='OOS' and dt.startswith('2026-05')}
jun_dates = {dt for dt,v in regime_db.items() if v['window']=='OOS' and dt.startswith('2026-06')}
for fname, ftest in FILTERS.items():
    pass_dates = {dt for dt,drow in regime_db.items() if ftest(drow)}
    may_t=[t for t in all_trades if t['window']=='OOS' and t['dt'] in pass_dates and t['dt'] in may_dates]
    jun_t=[t for t in all_trades if t['window']=='OOS' and t['dt'] in pass_dates and t['dt'] in jun_dates]
    may_r=sum(t['r'] for t in may_t); jun_r=sum(t['r'] for t in jun_t)
    print(f'{fname:<14} {len(may_t):>6} {may_r:>+8.2f} {len(jun_t):>6} {jun_r:>+8.2f} '
          f'{may_r+jun_r:>+8.2f}')

# Summary
print()
print('='*72)
print('PHASE 11C SUMMARY')
print('='*72)

# Rank filters by OOS AvgR/trade (excluding BASELINE)
ranked = sorted(
    [(fname, results[fname]) for fname in FILTERS if fname != 'BASELINE'],
    key=lambda x: x[1]['oos']['avg_r'], reverse=True
)

print('\nFilters ranked by OOS AvgR/trade:')
for i, (fname, res) in enumerate(ranked, 1):
    oos_m = res['oos']; is_m = res['is']
    cons_tag = ''
    if is_m['avg_r'] > 0 and oos_m['avg_r'] > 0:
        cons_tag = ' [IS→OOS consistent]'
    elif oos_m['avg_r'] <= bl_oos['avg_r']:
        cons_tag = ' [no OOS improvement]'
    print(f'  {i}. {fname:<14} OOS AvgR/T={oos_m["avg_r"]:>+.3f}R  '
          f'OOS PF={pf_s(oos_m["pf"]):<5}  IS AvgR/T={is_m["avg_r"]:>+.3f}R{cons_tag}')

print(f'\nBaseline (no filter): IS AvgR/T={bl_is["avg_r"]:>+.3f}R  '
      f'OOS AvgR/T={bl_oos["avg_r"]:>+.3f}R')

# Filters that improve OOS AvgR/trade vs baseline
improves_oos = [(f,r) for f,r in ranked if r['oos']['avg_r'] > bl_oos['avg_r']]
worsens_oos  = [(f,r) for f,r in ranked if r['oos']['avg_r'] <= bl_oos['avg_r']]
print(f'\nFilters improving OOS AvgR/trade vs baseline : '
      f'{", ".join(f for f,_ in improves_oos) or "none"}')
print(f'Filters NOT improving OOS AvgR/trade vs baseline: '
      f'{", ".join(f for f,_ in worsens_oos) or "none"}')
print()
print('No recommendations written. No parameters changed.')

# ── write CSV ─────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
cols = ['filter','description',
        'is_days','is_days_pct','is_n','is_trades_pct','is_tr','is_avg_r','is_wr','is_pf','is_dd',
        'oos_days','oos_days_pct','oos_n','oos_trades_pct','oos_tr','oos_avg_r','oos_wr','oos_pf','oos_dd',
        'delta_is_tr','delta_oos_tr','is_oos_consistent']
with open(OUT_CSV,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader()
    for fname in FILTERS:
        res=results[fname]; im=res['is']; om=res['oos']
        is_dp  = 100.0*res['is_days']/is_days_total  if is_days_total  else 0
        oos_dp = 100.0*res['oos_days']/oos_days_total if oos_days_total else 0
        if fname=='BASELINE': cons='BASELINE'
        elif im['avg_r']>0 and om['avg_r']>0: cons='YES' if om['avg_r']>=im['avg_r']*0.5 else 'DEGRADES'
        elif im['avg_r']>0 and om['avg_r']<=0: cons='NO'
        else: cons='BAD_IS'
        w.writerow({'filter':fname,'description':FILTER_DESC[fname],
                    'is_days':res['is_days'],'is_days_pct':f'{is_dp:.0f}%',
                    'is_n':im['n'],'is_trades_pct':f"{res['is_trades_pct']:.0f}%",
                    'is_tr':f'{im["tr"]:.2f}','is_avg_r':f'{im["avg_r"]:.3f}',
                    'is_wr':f'{im["wr"]:.1f}','is_pf':pf_s(im["pf"]),'is_dd':f'{im["dd"]:.2f}',
                    'oos_days':res['oos_days'],'oos_days_pct':f'{oos_dp:.0f}%',
                    'oos_n':om['n'],'oos_trades_pct':f"{res['oos_trades_pct']:.0f}%",
                    'oos_tr':f'{om["tr"]:.2f}','oos_avg_r':f'{om["avg_r"]:.3f}',
                    'oos_wr':f'{om["wr"]:.1f}','oos_pf':pf_s(om["pf"]),'oos_dd':f'{om["dd"]:.2f}',
                    'delta_is_tr':f'{im["tr"]-bl_is["tr"]:+.2f}',
                    'delta_oos_tr':f'{om["tr"]-bl_oos["tr"]:+.2f}',
                    'is_oos_consistent':cons})
print(f'\nCSV written: {OUT_CSV}')

# ── write MD ──────────────────────────────────────────────────────────────────
with open(OUT_MD,'w',encoding='utf-8') as f:
    f.write('# Phase 11C — Regime Filter Backtest\n\n')
    f.write('> **Scope:** Backtest only. No recommendations. No parameter changes. No strategy modifications.\n\n')
    f.write(f'**Baseline IS:** {bl_is["n"]} trades · PF {pf_s(bl_is["pf"])} · '
            f'TotalR {bl_is["tr"]:+.2f}R · MaxDD {bl_is["dd"]:.2f}R  \n')
    f.write(f'**Baseline OOS:** {bl_oos["n"]} trades · PF {pf_s(bl_oos["pf"])} · '
            f'TotalR {bl_oos["tr"]:+.2f}R · MaxDD {bl_oos["dd"]:.2f}R  \n\n')
    f.write('## Filter Definitions\n\n')
    f.write('| Filter | Condition |\n|--------|----------|\n')
    for fname,desc in FILTER_DESC.items():
        f.write(f'| {fname} | {desc} |\n')
    f.write('\n> All thresholds taken directly from Phase 11B findings. Not tuned here.\n\n---\n\n')

    def md_block(title, key):
        ref = bl_is if key=='is' else bl_oos
        days_t = is_days_total if key=='is' else oos_days_total
        lines=[f'## {title}\n']
        lines.append('| Filter | Days | D% | n | T% | TotalR | ΔBase | AvgR/T | WR | PF | MaxDD |')
        lines.append('|--------|-----:|---:|--:|---:|-------:|------:|-------:|---:|---:|------:|')
        for fname in FILTERS:
            res=results[fname]; m=res[key]
            dp=100.0*res[f'{key}_days']/days_t if days_t else 0
            tp=res[f'{key}_trades_pct']
            dtr=m['tr']-ref['tr']
            lines.append(f'| {fname} | {res[f"{key}_days"]} | {dp:.0f}% | {m["n"]} | {tp:.0f}% | '
                         f'{m["tr"]:+.2f}R | {dtr:+.2f}R | {m["avg_r"]:+.3f}R | '
                         f'{m["wr"]:.1f}% | {pf_s(m["pf"])} | {m["dd"]:.2f}R |')
        return '\n'.join(lines)

    f.write(md_block('IS Performance by Filter  (2025-09-17 → 2026-04-30)', 'is')+'\n\n')
    f.write(md_block('OOS Performance by Filter (2026-05-01 → 2026-06-25)', 'oos')+'\n\n')

    f.write('## IS → OOS Consistency\n\n')
    f.write('| Filter | IS AvgR/T | OOS AvgR/T | IS PF | OOS PF | IS→OOS |\n')
    f.write('|--------|----------:|-----------:|------:|-------:|:------:|\n')
    for fname in FILTERS:
        im=results[fname]['is']; om=results[fname]['oos']
        if fname=='BASELINE': cons='—'
        elif im['avg_r']>0 and om['avg_r']>0:
            cons='HOLDS ✓' if om['avg_r']>=im['avg_r']*0.5 else 'DEGRADES ~'
        elif im['avg_r']>0 and om['avg_r']<=0: cons='REVERSES ✗'
        elif im['avg_r']<=0: cons='BAD IS'
        else: cons='?'
        f.write(f'| {fname} | {im["avg_r"]:+.3f}R | {om["avg_r"]:+.3f}R | '
                f'{pf_s(im["pf"])} | {pf_s(om["pf"])} | {cons} |\n')
    f.write('\n')

    f.write('## OOS Monthly Detail\n\n')
    f.write('| Filter | May n | May R | Jun n | Jun R | OOS Total |\n')
    f.write('|--------|------:|------:|------:|------:|----------:|\n')
    for fname, ftest in FILTERS.items():
        pass_dates={dt for dt,drow in regime_db.items() if ftest(drow)}
        mt=[t for t in all_trades if t['window']=='OOS' and t['dt'] in pass_dates and t['dt'] in may_dates]
        jt=[t for t in all_trades if t['window']=='OOS' and t['dt'] in pass_dates and t['dt'] in jun_dates]
        mr=sum(t['r'] for t in mt); jr=sum(t['r'] for t in jt)
        f.write(f'| {fname} | {len(mt)} | {mr:+.2f}R | {len(jt)} | {jr:+.2f}R | {mr+jr:+.2f}R |\n')
    f.write('\n---\n\n')

    f.write('## Phase 11C Summary\n\n')
    f.write('> No recommendation written. No parameters changed. '
            'Next step requires explicit user approval.\n\n')
    f.write('**Filters ranked by OOS AvgR/trade:**\n\n')
    f.write('| Rank | Filter | IS AvgR/T | OOS AvgR/T | OOS PF | IS→OOS |\n')
    f.write('|-----:|--------|----------:|-----------:|-------:|:------:|\n')
    for i,(fname,res) in enumerate(ranked,1):
        im=res['is']; om=res['oos']
        if im['avg_r']>0 and om['avg_r']>0:
            cons='HOLDS ✓' if om['avg_r']>=im['avg_r']*0.5 else 'DEGRADES ~'
        elif im['avg_r']>0 and om['avg_r']<=0: cons='REVERSES ✗'
        elif im['avg_r']<=0: cons='BAD IS'
        else: cons='?'
        f.write(f'| {i} | {fname} | {im["avg_r"]:+.3f}R | {om["avg_r"]:+.3f}R | '
                f'{pf_s(om["pf"])} | {cons} |\n')
    f.write(f'\n**Baseline:** IS {bl_is["avg_r"]:+.3f}R/trade → OOS {bl_oos["avg_r"]:+.3f}R/trade\n')

print(f'MD  written: {OUT_MD}')
print('\nPhase 11C complete.')
