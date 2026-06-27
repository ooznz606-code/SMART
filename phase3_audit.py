"""Phase 3 -- DST-Safe Excluded Symbol Audit (IS window only)."""
import os, json, sys, csv
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

RESEARCH_DIR = 'chart_data_research'
OUT_CSV = os.path.join('docs', 'results', 'phase3_excluded_symbol_audit_dstsafe.csv')
OUT_MD  = os.path.join('docs', 'results', 'phase3_excluded_symbol_audit_dstsafe.md')

IS_START = date(2025, 9, 17)
IS_END   = date(2026, 4, 30)

ORB_ADX_MIN        = 30.0
ORB_RVOL_MIN       = 1.5
ORB_BODY_ATR       = 0.25
ORB_RANGE_ATR_MIN  = 2.0
ORB_EMA20_DIST_MIN = 1.95
ORB_BREAK_DIST_MIN = 0.05
ORB_MAX_DIR_PER_DAY = 2
TOP_N_DAY           = 3
MIN_LB              = 60
SL_ATR              = 1.5
TP_ATR              = 2.7
MAX_HOLD            = 40

SESS_OPEN_ET     =   0
SESS_ORB_DONE_ET =  30
SESS_BRK_END_ET  = 120

_UTC = ZoneInfo('UTC')
_ET  = ZoneInfo('America/New_York')

EXCL_SYMS = ['AAPL', 'AMD', 'AVGO', 'COST', 'GOOGL', 'SPY', 'TSLA', 'UBER']

# Original exclusion rationale (from production ORB_EXCLUDED docstring context)
EXCL_REASON = {
    'AAPL':  'high correlation with QQQ; adds index noise',
    'AMD':   'erratic ORB behaviour; high false-breakout rate',
    'AVGO':  'low liquidity relative to notional; gap risk',
    'COST':  'slow mover; rarely meets RVOL threshold',
    'GOOGL': 'high correlation with QQQ; redundant signal source',
    'SPY':   'used for bias calculation; conflict of interest',
    'TSLA':  'extreme volatility; ATR-based SL frequently breached pre-TP',
    'UBER':  'low ADX consistency; trend structure unreliable',
}


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


def _sm_et(naive_utc):
    et = naive_utc.replace(tzinfo=_UTC).astimezone(_ET)
    return (et.hour - 9) * 60 + et.minute - 30


def _et_date(naive_utc):
    return str(naive_utc.replace(tzinfo=_UTC).astimezone(_ET).date())


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
            bull = e9[i] > e20[i]; prev = bias.get(b.timestamp)
            if prev is None:
                bias[b.timestamp] = 'BULL' if bull else 'BEAR'
            elif (prev == 'BULL') == bull:
                pass
            else:
                bias[b.timestamp] = 'NEUTRAL'
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
    cl     = [b.close  for b in bars]
    vol    = [b.volume for b in bars]
    atrs   = _atr(bars, 14)
    adxs   = _adx(bars, 14)
    rvs    = _rvol(vol, 20)
    ema20s = _ema(cl, 20)

    orb     = {}
    emitted = set()
    raw     = []
    rej     = defaultdict(int)

    for i in range(MIN_LB, n):
        b  = bars[i]; ts = b.timestamp
        sm = _sm_et(ts); dt = _et_date(ts)
        td = date.fromisoformat(dt)

        if SESS_OPEN_ET <= sm < SESS_ORB_DONE_ET:
            if dt not in orb:
                orb[dt] = [b.high, b.low, False]
            else:
                orb[dt][0] = max(orb[dt][0], b.high)
                orb[dt][1] = min(orb[dt][1], b.low)
        elif dt in orb and not orb[dt][2] and sm >= SESS_ORB_DONE_ET:
            orb[dt][2] = True

        if td < IS_START or td > IS_END:
            continue
        if sm < SESS_ORB_DONE_ET or sm >= SESS_BRK_END_ET:
            continue
        if dt not in orb or not orb[dt][2]:
            rej['no_orb'] += 1; continue

        atr = atrs[i]
        if atr <= 0: continue

        oh, ol, _ = orb[dt]
        adx  = adxs[i]; rv = rvs[i]
        bias = bias_map.get(ts, 'NEUTRAL')
        body = abs(b.close - b.open)
        e20  = ema20s[i]
        sc_mult = 1.3 if bias != 'NEUTRAL' else 1.0

        if adx  < ORB_ADX_MIN:             rej['adx']          += 1; continue
        if rv   < ORB_RVOL_MIN:            rej['rvol']         += 1; continue
        if body < ORB_BODY_ATR * atr:      rej['body']         += 1; continue
        if (oh - ol) / atr < ORB_RANGE_ATR_MIN: rej['orb_range'] += 1; continue

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

        if (dt, 'SHORT') not in emitted:
            if b.close < ol and b.close < b.open:
                if bias == 'BULL':
                    rej['counter_bias'] += 1
                elif (e20 - b.close) / atr < ORB_EMA20_DIST_MIN:
                    rej['ema20_dist'] += 1
                elif (ol - b.close) / atr < ORB_BREAK_DIST_MIN:
                    rej['f3_break'] += 1
                else:
                    raw.append(dict(i=i, dt=dt, dir='SHORT', entry=b.close,
                        stop=b.close + SL_ATR * atr, tp1=b.close - TP_ATR * atr,
                        score=adx * rv * sc_mult))
                    emitted.add((dt, 'SHORT'))

    by_date = defaultdict(list)
    for s in raw: by_date[s['dt']].append(s)
    final = []
    for dt, day in sorted(by_date.items()):
        day.sort(key=lambda x: x['score'], reverse=True)
        dc = defaultdict(int)
        for s in day[:TOP_N_DAY]:
            if dc[s['dir']] < ORB_MAX_DIR_PER_DAY:
                dc[s['dir']] += 1; final.append(s)
    return final, rej


def classify(sig, pf, totalr, maxdd):
    if pf >= 2.0 and totalr > 0 and maxdd <= 3.0 and sig >= 5:
        return 'RECONSIDER'
    if pf < 1.2 or totalr <= 0 or maxdd > 4.0:
        return 'REJECT'
    return 'WATCHLIST'


# ── main ──────────────────────────────────────────────────────────────────────
spy = load_bars('SPY')
qqq = load_bars('QQQ')
bias_map = build_bias(spy, qqq)

is_days = set()
for b in spy:
    td = date.fromisoformat(_et_date(b.timestamp))
    if IS_START <= td <= IS_END:
        is_days.add(str(td))

print()
print('Phase 3 -- DST-Safe Excluded Symbol Audit')
print('=' * 60)
print('IS window  : {} -> {}  ({} trading days)'.format(IS_START, IS_END, len(is_days)))
print('Clock      : _sm_et  (zoneinfo America/New_York)')
print('Symbols    : {} (hypothetical scan -- no production change)'.format(', '.join(EXCL_SYMS)))
print()

results = {}
for sym in EXCL_SYMS:
    bars = load_bars(sym)
    signals, rej = scan(sym, bars, bias_map)
    r_list = []
    for s in signals:
        r = simulate(bars, s['i'], s['dir'], s['entry'], s['stop'], s['tp1'])
        r_list.append(r)
    n = len(r_list)
    if n == 0:
        results[sym] = dict(sig=0, wr=0.0, pf=0.0, totalr=0.0, maxdd=0.0,
                            rej='n/a', rej_n=0, cls='REJECT')
        continue
    wins = sum(1 for r in r_list if r > 0)
    gw   = sum(r for r in r_list if r > 0)
    gl   = sum(abs(r) for r in r_list if r < 0)
    pf   = gw / gl if gl > 0 else 9.99
    totalr = sum(r_list)
    eq = pk = mdd = 0.0
    for r in r_list:
        eq += r
        if eq > pk: pk = eq
        if pk - eq > mdd: mdd = pk - eq
    wr = 100.0 * wins / n
    rej_filt = {k: v for k, v in rej.items() if k != 'no_orb'}
    top_rej  = max(rej_filt, key=rej_filt.get) if rej_filt else 'n/a'
    top_n    = rej_filt.get(top_rej, 0)
    cls      = classify(n, pf, totalr, mdd)
    results[sym] = dict(sig=n, wr=wr, pf=pf, totalr=totalr, maxdd=mdd,
                        rej=top_rej, rej_n=top_n, cls=cls)

# ── console table ─────────────────────────────────────────────────────────────
HDR = '{:6s}  {:>4s}  {:>5s}  {:>6s}  {:>8s}  {:>6s}  {:12s}  {:20s}  {}'
print(HDR.format('SYM', 'SIG', 'W%', 'PF', 'TotalR', 'MaxDD',
                 'CLASS', 'TOP REJECTION', 'ORIGINAL RATIONALE'))
print('-' * 120)

for sym in EXCL_SYMS:
    r = results[sym]
    label = '{} ({})'.format(r['rej'], r['rej_n']) if r['rej'] != 'n/a' else 'n/a'
    print(HDR.format(
        sym,
        str(r['sig']),
        '{:.1f}%'.format(r['wr']),
        '{:.2f}'.format(r['pf']),
        '{:+.2f}R'.format(r['totalr']),
        '{:.2f}R'.format(r['maxdd']),
        r['cls'],
        label,
        EXCL_REASON[sym],
    ))

# ── summary lines ─────────────────────────────────────────────────────────────
# Best: highest TotalR among RECONSIDER/WATCHLIST, else highest PF
ranked = sorted(results.items(),
                key=lambda x: (x[1]['totalr'] if x[1]['totalr'] > 0 else -99, x[1]['pf']),
                reverse=True)
best_sym, best_r = ranked[0]
worst_sym, worst_r = ranked[-1]

reconsider = [s for s, r in results.items() if r['cls'] == 'RECONSIDER']

print()
print('Best excluded symbol  : {} (PF {:.2f}, TotalR {:+.2f}R, MaxDD {:.2f}R, {})'.format(
    best_sym, best_r['pf'], best_r['totalr'], best_r['maxdd'], best_r['cls']))
print('Worst excluded symbol : {} (PF {:.2f}, TotalR {:+.2f}R, MaxDD {:.2f}R, {})'.format(
    worst_sym, worst_r['pf'], worst_r['totalr'], worst_r['maxdd'], worst_r['cls']))
print()
if reconsider:
    print('Phase 6 hypothesis consideration:')
    for sym in reconsider:
        r = results[sym]
        print('  {} -- RECONSIDER (PF {:.2f}, TotalR {:+.2f}R, MaxDD {:.2f}R, {} signals)'.format(
            sym, r['pf'], r['totalr'], r['maxdd'], r['sig']))
        print('    Original rationale: {}'.format(EXCL_REASON[sym]))
        print('    Hypothesis candidate: remove {} from ORB_EXCLUDED and re-audit in Phase 7'.format(sym))
else:
    print('Phase 6 hypothesis consideration: NONE')
    print('  No excluded symbol meets RECONSIDER criteria.')
    print('  Exclusion list is confirmed by data for this IS window.')

# ── write CSV ─────────────────────────────────────────────────────────────────
csv_rows = []
for sym in EXCL_SYMS:
    r = results[sym]
    csv_rows.append({
        'symbol':            sym,
        'is_start':          str(IS_START),
        'is_end':            str(IS_END),
        'is_days':           len(is_days),
        'signals':           r['sig'],
        'win_rate_pct':      round(r['wr'], 1),
        'pf':                round(r['pf'], 2),
        'total_r':           round(r['totalr'], 2),
        'max_dd_r':          round(r['maxdd'], 2),
        'classification':    r['cls'],
        'top_rejection':     r['rej'],
        'top_rej_count':     r['rej_n'],
        'original_rationale':EXCL_REASON[sym],
    })

with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
    w.writeheader(); w.writerows(csv_rows)

# ── write MD ──────────────────────────────────────────────────────────────────
with open(OUT_MD, 'w', encoding='utf-8') as f:
    f.write('# Phase 3 -- DST-Safe Excluded Symbol Audit\n\n')
    f.write('**IS window:** {} -> {}  \n'.format(IS_START, IS_END))
    f.write('**IS trading days:** {}  \n'.format(len(is_days)))
    f.write('**Clock:** `_sm_et` via `zoneinfo` `America/New_York`  \n')
    f.write('**Note:** Hypothetical scan only -- no production change made  \n\n')
    f.write('### Classification rules\n\n')
    f.write('| Class | Criteria |\n|-------|----------|\n')
    f.write('| RECONSIDER | PF >= 2.0 AND TotalR > 0 AND MaxDD <= 3R AND signals >= 5 |\n')
    f.write('| REJECT     | PF < 1.2 OR TotalR <= 0 OR MaxDD > 4R |\n')
    f.write('| WATCHLIST  | otherwise |\n\n')
    f.write('---\n\n## Results\n\n')
    f.write('| SYM | Signals | W% | PF | TotalR | MaxDD | Class | Top Rejection | Original Rationale |\n')
    f.write('|-----|--------:|---:|---:|-------:|------:|-------|---------------|--------------------|\n')
    for sym in EXCL_SYMS:
        r = results[sym]
        label = '{} ({})'.format(r['rej'], r['rej_n']) if r['rej'] != 'n/a' else 'n/a'
        f.write('| {} | {} | {:.1f}% | {:.2f} | {:+.2f}R | {:.2f}R | {} | {} | {} |\n'.format(
            sym, r['sig'], r['wr'], r['pf'], r['totalr'], r['maxdd'],
            r['cls'], label, EXCL_REASON[sym]))
    f.write('\n---\n\n## Summary\n\n')
    f.write('**Best excluded symbol:** {} (PF {:.2f}, TotalR {:+.2f}R, {})\n\n'.format(
        best_sym, best_r['pf'], best_r['totalr'], best_r['cls']))
    f.write('**Worst excluded symbol:** {} (PF {:.2f}, TotalR {:+.2f}R, {})\n\n'.format(
        worst_sym, worst_r['pf'], worst_r['totalr'], worst_r['cls']))
    f.write('**Phase 6 candidates:** {}\n\n'.format(
        ', '.join(reconsider) if reconsider else 'None'))
    if reconsider:
        f.write('Symbols meeting RECONSIDER criteria should be considered for a Phase 6 '
                'hypothesis: remove from `ORB_EXCLUDED` and validate in Phase 7 IS window.\n\n')
    else:
        f.write('No excluded symbol meets RECONSIDER criteria. '
                'Exclusion list is confirmed by IS data.\n\n')
    f.write('---\n\n## Rejection key\n\n')
    f.write('`adx` = ADX < 30.0  |  `rvol` = RVOL < 1.5  |  `orb_range` = range < 2.0 ATR  \n')
    f.write('`ema20_dist` = dist < 1.95 ATR  |  `counter_bias` = against SPY+QQQ  \n')
    f.write('`f3_break` = break dist < 0.05 ATR  |  `no_breakout` = price did not cross ORB\n')

print()
print('Outputs written:')
print('  {}'.format(OUT_CSV))
print('  {}'.format(OUT_MD))
