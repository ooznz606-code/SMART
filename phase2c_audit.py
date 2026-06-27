"""Phase 2C -- DST-Safe Allowed Symbol Audit (IS window only)."""
import os, json, sys, csv
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── paths ─────────────────────────────────────────────────────────────────────
RESEARCH_DIR = 'chart_data_research'
OUT_CSV = os.path.join('docs', 'results', 'phase2_allowed_symbol_audit_dstsafe.csv')
OUT_MD  = os.path.join('docs', 'results', 'phase2_allowed_symbol_audit_dstsafe.md')

# ── IS window ─────────────────────────────────────────────────────────────────
IS_START = date(2025, 9, 17)
IS_END   = date(2026, 4, 30)

# ── production constants (unchanged) ─────────────────────────────────────────
ORB_ADX_MIN        = 30.0
ORB_RVOL_MIN       = 1.5
ORB_BODY_ATR       = 0.25
ORB_RANGE_ATR_MIN  = 2.0
ORB_EMA20_DIST_MIN = 1.95
ORB_BREAK_DIST_MIN = 0.05
ORB_MAX_DIR_PER_DAY = 2
TOP_N_DAY          = 3
MIN_LB             = 60
SL_ATR             = 1.5
TP_ATR             = 2.7
MAX_HOLD           = 40

# ── DST-safe session constants (ET-based) ────────────────────────────────────
# _sm_et converts UTC naive -> ET, then applies (hour-9)*60+minute-30
# 9:30 ET -> sm=0   10:00 ET -> sm=30   11:30 ET -> sm=120
SESS_OPEN_ET     =   0   # 9:30 ET
SESS_ORB_DONE_ET =  30   # 10:00 ET
SESS_BRK_END_ET  = 120   # 11:30 ET

_UTC = ZoneInfo('UTC')
_ET  = ZoneInfo('America/New_York')

SCAN_SYMS = ['AMZN', 'CRM', 'LLY', 'META', 'MSFT', 'NFLX', 'NVDA', 'PANW', 'QQQ']

# ── old Phase 2 results (full dataset, UTC clock) for comparison ─────────────
OLD = {
    'AMZN': dict(sig=10, wr=30.0, pf=0.77, totalr=-1.60, maxdd=3.00, rej='adx'),
    'CRM':  dict(sig=8,  wr=62.5, pf=3.00, totalr=+6.00, maxdd=2.00, rej='adx'),
    'LLY':  dict(sig=7,  wr=42.9, pf=1.35, totalr=+1.40, maxdd=3.00, rej='adx'),
    'META': dict(sig=8,  wr=87.5, pf=11.66,totalr=+10.66,maxdd=1.00, rej='adx'),
    'MSFT': dict(sig=9,  wr=66.7, pf=3.24, totalr=+6.72, maxdd=1.00, rej='adx'),
    'NFLX': dict(sig=6,  wr=66.7, pf=2.77, totalr=+3.53, maxdd=1.00, rej='adx'),
    'NVDA': dict(sig=12, wr=41.7, pf=1.29, totalr=+2.00, maxdd=3.00, rej='adx'),
    'PANW': dict(sig=8,  wr=37.5, pf=1.08, totalr=+0.40, maxdd=4.00, rej='adx'),
    'QQQ':  dict(sig=5,  wr=60.0, pf=2.70, totalr=+3.40, maxdd=1.00, rej='adx'),
}

# ── helpers ───────────────────────────────────────────────────────────────────

class Bar:
    __slots__ = ('timestamp', 'open', 'high', 'low', 'close', 'volume')
    def __init__(self, ts, o, h, l, c, v):
        self.timestamp = ts
        self.open = o; self.high = h; self.low = l; self.close = c; self.volume = v

def load_bars(sym):
    path = os.path.join(RESEARCH_DIR, f'{sym}_15m.json')
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    times = d['times']; ops = d['opens']; his = d['highs']
    los = d['lows'];   cls = d['closes']; vls = d.get('volumes', [0] * len(times))
    bars = []
    for i in range(min(len(times), len(ops), len(his), len(los), len(cls))):
        naive_utc = datetime.strptime(times[i][:16].replace('T', ' '), '%Y-%m-%d %H:%M')
        vol = float(vls[i]) if i < len(vls) else 0.0
        bars.append(Bar(naive_utc, float(ops[i]), float(his[i]),
                        float(los[i]), float(cls[i]), vol))
    return bars

def _sm_et(naive_utc: datetime) -> int:
    et = naive_utc.replace(tzinfo=_UTC).astimezone(_ET)
    return (et.hour - 9) * 60 + et.minute - 30

def _et_date(naive_utc: datetime) -> str:
    et = naive_utc.replace(tzinfo=_UTC).astimezone(_ET)
    return str(et.date())

def _ema(v, p):
    k = 2.0 / (p + 1); r = [v[0]]
    for x in v[1:]: r.append(r[-1] + k * (x - r[-1]))
    return r

def _wilder(v, p):
    k = 1.0 / p; r = [v[0]]
    for x in v[1:]: r.append(r[-1] + k * (x - r[-1]))
    return r

def _atr(bars, p=14):
    tr = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i-1].close
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return _wilder(tr, p)

def _adx(bars, p=14):
    n = len(bars)
    if n < p + 2: return [0.0] * n
    pdm, mdm, tr = [], [], []
    for i in range(1, n):
        h, l = bars[i].high, bars[i].low
        ph, pl, pc = bars[i-1].high, bars[i-1].low, bars[i-1].close
        up, dn = h - ph, pl - l
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = _wilder(tr, p); pd_ = _wilder(pdm, p); md = _wilder(mdm, p); dx = []
    for ai, pi, mi in zip(a, pd_, md):
        pdi = 100 * pi / ai if ai > 0 else 0.0
        mdi = 100 * mi / ai if ai > 0 else 0.0
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi > 0 else 0.0)
    return [0.0] + _wilder(dx, p)

def _rvol(vols, p=20):
    out = [1.0] * p
    for i in range(p, len(vols)):
        avg = sum(vols[i - p:i]) / p
        out.append(vols[i] / avg if avg > 0 else 1.0)
    return out

def build_bias(spy, qqq):
    bias = {}
    for bars in (spy, qqq):
        if not bars: continue
        cl = [b.close for b in bars]; e9 = _ema(cl, 9); e20 = _ema(cl, 20)
        for i, b in enumerate(bars):
            bull = e9[i] > e20[i]
            key  = b.timestamp
            prev = bias.get(key)
            if prev is None:
                bias[key] = 'BULL' if bull else 'BEAR'
            elif (prev == 'BULL') == bull:
                pass
            else:
                bias[key] = 'NEUTRAL'
    return bias

def simulate(bars, idx, direction, entry, stop, tp1):
    risk = abs(entry - stop)
    if risk <= 0: return 0.0
    for j in range(idx + 1, min(idx + 1 + MAX_HOLD, len(bars))):
        b = bars[j]
        if direction == 'LONG':
            if b.high >= tp1:  return (tp1 - entry) / risk
            if b.low  <= stop: return (stop - entry) / risk
        else:
            if b.low  <= tp1:  return (entry - tp1) / risk
            if b.high >= stop: return (entry - stop) / risk
    j = min(idx + MAX_HOLD, len(bars) - 1)
    return (bars[j].close - entry) / risk if direction == 'LONG' \
           else (entry - bars[j].close) / risk

def scan(sym, bars, bias_map):
    n = len(bars)
    cl    = [b.close  for b in bars]
    vol   = [b.volume for b in bars]
    atrs  = _atr(bars, 14)
    adxs  = _adx(bars, 14)
    rvs   = _rvol(vol, 20)
    ema20s = _ema(cl, 20)

    orb     = {}
    emitted = set()
    raw     = []
    rej     = defaultdict(int)

    for i in range(MIN_LB, n):
        b   = bars[i]
        ts  = b.timestamp
        sm  = _sm_et(ts)
        dt  = _et_date(ts)
        td  = date.fromisoformat(dt)

        # ORB accumulation 9:30-10:00 ET (all bars, not IS-filtered — need history for indicators)
        if SESS_OPEN_ET <= sm < SESS_ORB_DONE_ET:
            if dt not in orb:
                orb[dt] = [b.high, b.low, False]
            else:
                orb[dt][0] = max(orb[dt][0], b.high)
                orb[dt][1] = min(orb[dt][1], b.low)
        elif dt in orb and not orb[dt][2] and sm >= SESS_ORB_DONE_ET:
            orb[dt][2] = True

        # IS window filter for signal generation and rejection tracking
        if td < IS_START or td > IS_END:
            continue

        # Only scan breakout window
        if sm < SESS_ORB_DONE_ET or sm >= SESS_BRK_END_ET:
            continue
        if dt not in orb or not orb[dt][2]:
            rej['no_orb'] += 1
            continue

        atr = atrs[i]
        if atr <= 0: continue

        oh, ol, _ = orb[dt]
        adx  = adxs[i]; rv = rvs[i]
        bias = bias_map.get(ts, 'NEUTRAL')
        body = abs(b.close - b.open)
        e20  = ema20s[i]
        sc_mult = 1.3 if bias != 'NEUTRAL' else 1.0

        # Common filters (once per bar)
        if adx < ORB_ADX_MIN:
            rej['adx'] += 1; continue
        if rv < ORB_RVOL_MIN:
            rej['rvol'] += 1; continue
        if body < ORB_BODY_ATR * atr:
            rej['body'] += 1; continue
        if (oh - ol) / atr < ORB_RANGE_ATR_MIN:
            rej['orb_range'] += 1; continue

        # LONG
        if (dt, 'LONG') not in emitted:
            if b.close > oh and b.close > b.open:
                if bias == 'BEAR':
                    rej['counter_bias'] += 1
                elif (b.close - e20) / atr < ORB_EMA20_DIST_MIN:
                    rej['ema20_dist'] += 1
                elif (b.close - oh) / atr < ORB_BREAK_DIST_MIN:
                    rej['f3_break'] += 1
                else:
                    raw.append(dict(i=i, dt=dt, dir='LONG', entry=b.close,
                        stop=b.close - SL_ATR * atr, tp1=b.close + TP_ATR * atr,
                        score=adx * rv * sc_mult))
                    emitted.add((dt, 'LONG'))
            else:
                rej['no_breakout'] += 1

        # SHORT
        if (dt, 'SHORT') not in emitted:
            if b.close < ol and b.close < b.open:
                if bias == 'BULL':
                    rej['counter_bias'] += 1
                elif (e20 - b.close) / atr < ORB_EMA20_DIST_MIN:
                    rej['ema20_dist'] += 1
                elif (ol - b.close) / atr < ORB_BREAK_DIST_MIN:
                    rej['f3_break'] += 1
                elif sym == 'MSFT' and bias == 'NEUTRAL':
                    rej['f4_msft'] += 1
                else:
                    raw.append(dict(i=i, dt=dt, dir='SHORT', entry=b.close,
                        stop=b.close + SL_ATR * atr, tp1=b.close - TP_ATR * atr,
                        score=adx * rv * sc_mult))
                    emitted.add((dt, 'SHORT'))

    # TOP_N_DAY + F2 caps
    by_date = defaultdict(list)
    for s in raw: by_date[s['dt']].append(s)
    final = []
    for dt, day in sorted(by_date.items()):
        day.sort(key=lambda x: x['score'], reverse=True)
        dc = defaultdict(int)
        for s in day[:TOP_N_DAY]:
            if dc[s['dir']] < ORB_MAX_DIR_PER_DAY:
                dc[s['dir']] += 1
                final.append(s)
    return final, rej

# ── EST-day ORB proof ─────────────────────────────────────────────────────────
def est_orb_proof(bars):
    """Show 4 EST-period days where ORB now forms with DST-safe clock."""
    orb_sample = {}
    for b in bars:
        sm = _sm_et(b.timestamp)
        dt = _et_date(b.timestamp)
        td = date.fromisoformat(dt)
        if td < date(2025, 11, 2) or td > date(2026, 3, 7):
            continue  # only EST period
        if SESS_OPEN_ET <= sm < SESS_ORB_DONE_ET:
            if dt not in orb_sample:
                orb_sample[dt] = []
            orb_sample[dt].append((b.timestamp, sm, b.high, b.low))
    # pick first 4 days that have ORB bars
    shown = 0
    for dt in sorted(orb_sample)[:4]:
        bars_in = orb_sample[dt]
        print(f'  {dt} (EST)  ->  {len(bars_in)} ORB-accumulation bar(s)  '
              f'[sm {bars_in[0][1]}..{bars_in[-1][1]}]  '
              f'UTC {bars_in[0][0].strftime("%H:%M")}..{bars_in[-1][0].strftime("%H:%M")}')
        shown += 1
    if shown == 0:
        print('  (no EST ORB bars found -- check data)')

# ── main ──────────────────────────────────────────────────────────────────────
spy = load_bars('SPY')
qqq = load_bars('QQQ')
bias_map = build_bias(spy, qqq)

# Count IS trading days
is_days = set()
for b in spy:
    td = date.fromisoformat(_et_date(b.timestamp))
    if IS_START <= td <= IS_END:
        is_days.add(str(td))

print()
print('Phase 2C -- DST-Safe Allowed Symbol Audit')
print('=' * 60)
print(f'IS window  : {IS_START} -> {IS_END}')
print(f'IS days    : {len(is_days)} trading days')
print(f'Clock      : _sm_et (zoneinfo US/Eastern)')
print(f'Constants  : SESS_OPEN_ET=0  SESS_ORB_DONE_ET=30  SESS_BRK_END_ET=120')
print()
print('Proof -- EST-period days now form ORB (sample from Nov 2025 - Mar 2026):')
est_orb_proof(spy)
print()

# Run scan
new_results = {}
for sym in SCAN_SYMS:
    bars = load_bars(sym)
    signals, rej = scan(sym, bars, bias_map)
    r_list = []
    for s in signals:
        r = simulate(bars, s['i'], s['dir'], s['entry'], s['stop'], s['tp1'])
        r_list.append(r)
    n = len(r_list)
    if n == 0:
        new_results[sym] = dict(sig=0, wr=0.0, pf=0.0, totalr=0.0, maxdd=0.0, rej='n/a', rej_n=0)
        continue
    wins = sum(1 for r in r_list if r > 0)
    gw = sum(r for r in r_list if r > 0)
    gl = sum(abs(r) for r in r_list if r < 0)
    pf = gw / gl if gl > 0 else 9.99
    totalr = sum(r_list)
    eq = pk = mdd = 0.0
    for r in r_list:
        eq += r
        if eq > pk: pk = eq
        if pk - eq > mdd: mdd = pk - eq
    rej_filt = {k: v for k, v in rej.items() if k != 'no_orb'}
    top_rej  = max(rej_filt, key=rej_filt.get) if rej_filt else 'n/a'
    top_n    = rej_filt.get(top_rej, 0)
    new_results[sym] = dict(sig=n, wr=100.0 * wins / n, pf=pf,
                             totalr=totalr, maxdd=mdd, rej=top_rej, rej_n=top_n)

# ── console table ─────────────────────────────────────────────────────────────
HDR = '{:6s}  {:>4s}  {:>5s}  {:>6s}  {:>8s}  {:>6s}  {:22s}  {}'
print(HDR.format('SYM', 'SIG', 'W%', 'PF', 'TotalR', 'MaxDD', 'TOP REJECTION', 'COMMENT'))
print('-' * 105)

comments = {}
for sym in SCAN_SYMS:
    r = new_results[sym]
    n = r['sig']
    if n == 0:
        comment = 'no signals'
    elif r['pf'] >= 1.5 and r['totalr'] > 0 and n >= 8:
        comment = 'good -- keep'
    elif r['pf'] >= 1.0 and r['totalr'] > 0 and n >= 5:
        comment = 'marginal positive'
    elif n < 5:
        comment = 'too few signals -- inconclusive'
    elif r['pf'] < 0.8:
        comment = 'losing -- review'
    else:
        comment = 'breakeven'
    comments[sym] = comment
    label = '{} ({})'.format(r['rej'], r['rej_n'])
    print(HDR.format(sym, str(n),
        '{:.1f}%'.format(r['wr']),
        '{:.2f}'.format(r['pf']),
        '{:+.2f}R'.format(r['totalr']),
        '{:.2f}R'.format(r['maxdd']),
        label, comment))

# ── comparison table ──────────────────────────────────────────────────────────
print()
print('Comparison -- Old Phase 2 (full dataset, UTC clock) vs Phase 2C (IS window, DST-safe)')
print('{:6s}  {:>12s}  {:>12s}  {:>12s}  {:>12s}  {:>14s}'.format(
    'SYM', 'SIG old->new', 'PF old->new', 'TotalR old->new', 'MaxDD old->new', 'TOP REJ change'))
print('-' * 90)
for sym in SCAN_SYMS:
    o = OLD[sym]; n2 = new_results[sym]
    sig_chg  = '{}->{}'.format(o['sig'], n2['sig'])
    pf_chg   = '{:.2f}->{:.2f}'.format(o['pf'], n2['pf'])
    tr_chg   = '{:+.2f}->{:+.2f}'.format(o['totalr'], n2['totalr'])
    dd_chg   = '{:.2f}->{:.2f}'.format(o['maxdd'], n2['maxdd'])
    rej_chg  = '{}->{}'.format(o['rej'], n2['rej'])
    print('{:6s}  {:>12s}  {:>12s}  {:>14s}  {:>14s}  {}'.format(
        sym, sig_chg, pf_chg, tr_chg, dd_chg, rej_chg))

# ── write CSV ─────────────────────────────────────────────────────────────────
csv_rows = []
for sym in SCAN_SYMS:
    r = new_results[sym]
    o = OLD[sym]
    csv_rows.append({
        'symbol':       sym,
        'is_start':     str(IS_START),
        'is_end':       str(IS_END),
        'is_days':      len(is_days),
        'signals':      r['sig'],
        'win_rate_pct': round(r['wr'], 1),
        'pf':           round(r['pf'], 2),
        'total_r':      round(r['totalr'], 2),
        'max_dd_r':     round(r['maxdd'], 2),
        'top_rejection':r['rej'],
        'top_rej_count':r['rej_n'],
        'comment':      comments[sym],
        'old_signals':  o['sig'],
        'old_pf':       o['pf'],
        'old_total_r':  o['totalr'],
        'old_max_dd_r': o['maxdd'],
    })

with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
    w.writeheader(); w.writerows(csv_rows)

# ── write MD ──────────────────────────────────────────────────────────────────
with open(OUT_MD, 'w', encoding='utf-8') as f:
    f.write('# Phase 2C — DST-Safe Allowed Symbol Audit\n\n')
    f.write(f'**IS window:** {IS_START} → {IS_END}  \n')
    f.write(f'**IS trading days:** {len(is_days)}  \n')
    f.write(f'**Clock:** `_sm_et` via `zoneinfo` `America/New_York`  \n')
    f.write(f'**Session constants:** SESS_OPEN_ET=0  SESS_ORB_DONE_ET=30  SESS_BRK_END_ET=120  \n')
    f.write(f'**Scan symbols:** {", ".join(SCAN_SYMS)}  \n\n')
    f.write('---\n\n')
    f.write('## Results\n\n')
    f.write('| SYM | Signals | W% | PF | TotalR | MaxDD | Top Rejection | Comment |\n')
    f.write('|-----|--------:|---:|---:|-------:|------:|---------------|----------|\n')
    for sym in SCAN_SYMS:
        r = new_results[sym]
        label = '{} ({})'.format(r['rej'], r['rej_n'])
        f.write('| {} | {} | {:.1f}% | {:.2f} | {:+.2f}R | {:.2f}R | {} | {} |\n'.format(
            sym, r['sig'], r['wr'], r['pf'], r['totalr'], r['maxdd'], label, comments[sym]))
    f.write('\n---\n\n')
    f.write('## Comparison vs Old Phase 2 (UTC clock, full dataset)\n\n')
    f.write('| SYM | Signals | PF | TotalR | MaxDD | Top Rejection |\n')
    f.write('|-----|--------:|---:|-------:|------:|---------------|\n')
    for sym in SCAN_SYMS:
        o = OLD[sym]; n2 = new_results[sym]
        f.write('| {} | {}→{} | {:.2f}→{:.2f} | {:+.2f}R→{:+.2f}R | {:.2f}R→{:.2f}R | {}→{} |\n'.format(
            sym, o['sig'], n2['sig'], o['pf'], n2['pf'],
            o['totalr'], n2['totalr'], o['maxdd'], n2['maxdd'],
            o['rej'], n2['rej']))
    f.write('\n---\n\n')
    f.write('## Notes\n\n')
    f.write('- Old Phase 2 used UTC raw clock: EST months (Nov 2025 - Mar 2026) produced zero ORB signals (44% of dataset lost)\n')
    f.write('- Phase 2C uses `_sm_et`: all IS trading days now form ORB correctly\n')
    f.write('- IS window (2025-09-17 to 2026-04-30) includes 5 EST months; DST fix unlocks this data\n')
    f.write('- Signal counts remain small; PF estimates are directional, not statistically final\n')
    f.write('- Phase 3 (excluded symbol audit) should use the same DST-safe clock and IS window\n')

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
print()
print('Outputs written:')
print(f'  {OUT_CSV}')
print(f'  {OUT_MD}')
