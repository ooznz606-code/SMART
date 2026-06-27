"""Phase 4 -- Parameter Discovery (DST-Safe, IS Window Only).
Each parameter swept in isolation; all others held at baseline.
Discovery only -- no recommendations, no production changes.
"""
import os, json, sys, csv
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_CSV = os.path.join('docs', 'results', 'phase4_parameter_discovery_dstsafe.csv')
OUT_MD  = os.path.join('docs', 'results', 'phase4_parameter_discovery_dstsafe.md')
RESEARCH_DIR = 'chart_data_research'

IS_START = date(2025, 9, 17)
IS_END   = date(2026, 4, 30)

# Baseline parameters
BL = dict(
    adx_min        = 30.0,
    rvol_min       = 1.5,
    orb_range_min  = 2.0,
    ema20_dist_min = 1.95,
    break_dist_min = 0.05,
    body_atr       = 0.25,
    sess_brk_end_et= 120,   # 11:30 ET in _sm_et units
)

# DST-safe session constants (fixed)
SESS_OPEN_ET     =  0
SESS_ORB_DONE_ET = 30
MIN_LB           = 60
ORB_MAX_DIR      = 2
TOP_N_DAY        = 3
SL_ATR           = 1.5
TP_ATR           = 2.7
MAX_HOLD         = 40

_UTC = ZoneInfo('UTC')
_ET  = ZoneInfo('America/New_York')

SCAN_SYMS = ['AMZN', 'CRM', 'LLY', 'META', 'MSFT', 'NFLX', 'NVDA', 'PANW', 'QQQ']

# Sweep definitions: (param_key, values, display_labels)
# SESS_BRK_END_ET values: 10:30=60, 11:00=90, 11:30=120, 12:00=150, 12:30=180
ET_LABELS = {60: '10:30ET', 90: '11:00ET', 120: '11:30ET', 150: '12:00ET', 180: '12:30ET'}

SWEEPS = [
    ('adx_min',         [15, 20, 25, 28, 30, 32, 35, 40],
     {v: str(v) for v in [15, 20, 25, 28, 30, 32, 35, 40]}),

    ('rvol_min',        [0.8, 1.0, 1.2, 1.4, 1.5, 1.7, 2.0],
     {v: str(v) for v in [0.8, 1.0, 1.2, 1.4, 1.5, 1.7, 2.0]}),

    ('orb_range_min',   [0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.3, 2.6],
     {v: str(v) for v in [0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.3, 2.6]}),

    ('ema20_dist_min',  [0.8, 1.0, 1.2, 1.5, 1.75, 1.95, 2.2, 2.5],
     {v: str(v) for v in [0.8, 1.0, 1.2, 1.5, 1.75, 1.95, 2.2, 2.5]}),

    ('break_dist_min',  [0.01, 0.03, 0.05, 0.08, 0.10, 0.15],
     {v: str(v) for v in [0.01, 0.03, 0.05, 0.08, 0.10, 0.15]}),

    ('body_atr',        [0.10, 0.15, 0.20, 0.25, 0.30, 0.40],
     {v: str(v) for v in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]}),

    ('sess_brk_end_et', [60, 90, 120, 150, 180], ET_LABELS),
]


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
    out = []
    for i in range(min(len(times), len(ops), len(his), len(los), len(cls))):
        naive = datetime.strptime(times[i][:16].replace('T', ' '), '%Y-%m-%d %H:%M')
        vol   = float(vls[i]) if i < len(vls) else 0.0
        out.append(Bar(naive, float(ops[i]), float(his[i]),
                       float(los[i]), float(cls[i]), vol))
    return out


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


def scan_sym(sym, bars, bias_map, params):
    """Single-symbol parameterized scan. Returns (signals_with_r, rej_counts)."""
    adx_min        = params['adx_min']
    rvol_min       = params['rvol_min']
    orb_range_min  = params['orb_range_min']
    ema20_dist_min = params['ema20_dist_min']
    break_dist_min = params['break_dist_min']
    body_atr       = params['body_atr']
    sess_end       = params['sess_brk_end_et']

    n = len(bars)
    cl     = [b.close  for b in bars]
    vol    = [b.volume for b in bars]
    atrs   = _atr(bars, 14)
    adxs   = _adx(bars, 14)
    rvs    = _rvol(vol, 20)
    ema20s = _ema(cl, 20)

    orb = {}; emitted = set(); raw = []; rej = defaultdict(int)

    for i in range(MIN_LB, n):
        b = bars[i]; ts = b.timestamp
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

        if td < IS_START or td > IS_END: continue
        if sm < SESS_ORB_DONE_ET or sm >= sess_end: continue
        if dt not in orb or not orb[dt][2]:
            rej['no_orb'] += 1; continue

        atr = atrs[i]
        if atr <= 0: continue

        oh, ol, _ = orb[dt]
        adx  = adxs[i]; rv = rvs[i]
        bias = bias_map.get(ts, 'NEUTRAL')
        body = abs(b.close - b.open)
        e20  = ema20s[i]
        sc   = adx * rv * (1.3 if bias != 'NEUTRAL' else 1.0)

        if adx  < adx_min:                  rej['adx']          += 1; continue
        if rv   < rvol_min:                  rej['rvol']         += 1; continue
        if body < body_atr * atr:            rej['body']         += 1; continue
        if (oh - ol) / atr < orb_range_min:  rej['orb_range']    += 1; continue

        if (dt, 'LONG') not in emitted:
            if b.close > oh and b.close > b.open:
                if bias == 'BEAR':
                    rej['counter_bias'] += 1
                elif (b.close - e20) / atr < ema20_dist_min:
                    rej['ema20_dist'] += 1
                elif (b.close - oh) / atr < break_dist_min:
                    rej['f3_break'] += 1
                else:
                    raw.append(dict(i=i, dt=dt, dir='LONG', entry=b.close,
                        stop=b.close - SL_ATR * atr, tp1=b.close + TP_ATR * atr, score=sc))
                    emitted.add((dt, 'LONG'))
            else:
                rej['no_breakout'] += 1

        if (dt, 'SHORT') not in emitted:
            if b.close < ol and b.close < b.open:
                if bias == 'BULL':
                    rej['counter_bias'] += 1
                elif (e20 - b.close) / atr < ema20_dist_min:
                    rej['ema20_dist'] += 1
                elif (ol - b.close) / atr < break_dist_min:
                    rej['f3_break'] += 1
                elif sym == 'MSFT' and bias == 'NEUTRAL':
                    rej['f4_msft'] += 1
                else:
                    raw.append(dict(i=i, dt=dt, dir='SHORT', entry=b.close,
                        stop=b.close + SL_ATR * atr, tp1=b.close - TP_ATR * atr, score=sc))
                    emitted.add((dt, 'SHORT'))

    by_date = defaultdict(list)
    for s in raw: by_date[s['dt']].append(s)
    final = []
    for dt, day in sorted(by_date.items()):
        day.sort(key=lambda x: x['score'], reverse=True)
        dc = defaultdict(int)
        for s in day[:TOP_N_DAY]:
            if dc[s['dir']] < ORB_MAX_DIR:
                dc[s['dir']] += 1; final.append(s)

    r_list = [simulate(bars, s['i'], s['dir'], s['entry'], s['stop'], s['tp1'])
              for s in final]
    return r_list, rej


def run_sweep(all_bars, bias_map, params, is_days_n):
    """Aggregate metrics across all symbols for a given param set."""
    all_r = []; all_rej = defaultdict(int)
    for sym, bars in all_bars:
        r_list, rej = scan_sym(sym, bars, bias_map, params)
        all_r.extend(r_list)
        for k, v in rej.items(): all_rej[k] += v

    n = len(all_r)
    if n == 0:
        return dict(sig=0, wins=0, losses=0, be=0, wr=0.0, pf=0.0,
                    totalr=0.0, maxdd=0.0, avgr=0.0,
                    tpd=0.0, top_rej='n/a')

    wins   = sum(1 for r in all_r if r > 0)
    losses = sum(1 for r in all_r if r < 0)
    be     = n - wins - losses
    gw     = sum(r for r in all_r if r > 0)
    gl     = sum(abs(r) for r in all_r if r < 0)
    pf     = gw / gl if gl > 0 else 9.99
    totalr = sum(all_r)
    avgr   = totalr / n
    eq = pk = mdd = 0.0
    for r in all_r:
        eq += r
        if eq > pk: pk = eq
        if pk - eq > mdd: mdd = pk - eq

    rej_filt = {k: v for k, v in all_rej.items() if k != 'no_orb'}
    top_rej  = max(rej_filt, key=rej_filt.get) if rej_filt else 'n/a'

    return dict(sig=n, wins=wins, losses=losses, be=be,
                wr=100.0 * wins / n, pf=round(pf, 3),
                totalr=round(totalr, 2), maxdd=round(mdd, 2),
                avgr=round(avgr, 3), tpd=round(n / is_days_n, 3),
                top_rej=top_rej)


# ── load data ─────────────────────────────────────────────────────────────────
print('\nLoading bars...', flush=True)
spy_bars = load_bars('SPY')
qqq_bars = load_bars('QQQ')
bias_map = build_bias(spy_bars, qqq_bars)

all_sym_bars = [(s, load_bars(s)) for s in SCAN_SYMS]

is_days_set = set()
for b in spy_bars:
    td = date.fromisoformat(_et_date(b.timestamp))
    if IS_START <= td <= IS_END:
        is_days_set.add(str(td))
IS_DAYS_N = len(is_days_set)

print(f'IS window: {IS_START} -> {IS_END}  ({IS_DAYS_N} trading days)')
print(f'Symbols  : {", ".join(SCAN_SYMS)}\n')

# ── baseline run ──────────────────────────────────────────────────────────────
baseline_m = run_sweep(all_sym_bars, bias_map, BL, IS_DAYS_N)

# ── run all sweeps ────────────────────────────────────────────────────────────
all_rows = []   # for CSV

for param_key, values, labels in SWEEPS:
    param_display = param_key.upper().replace('_ET', '')
    baseline_val  = BL[param_key]

    rows = []
    for v in values:
        params = dict(BL); params[param_key] = v
        m = run_sweep(all_sym_bars, bias_map, params, IS_DAYS_N)
        is_bl = (v == baseline_val)
        rows.append((v, labels[v], m, is_bl))
        all_rows.append(dict(
            parameter=param_key,
            value=labels[v],
            is_baseline='YES' if is_bl else 'no',
            signals=m['sig'], wins=m['wins'], losses=m['losses'], breakeven=m['be'],
            wr_pct=round(m['wr'], 1), pf=m['pf'], total_r=m['totalr'],
            max_dd_r=m['maxdd'], avg_r=m['avgr'], trades_per_day=m['tpd'],
            top_rejection=m['top_rej'],
        ))

    # Find best PF and best TotalR (with enough signals: >= 5)
    eligible = [(v, lbl, m) for v, lbl, m, _ in rows if m['sig'] >= 5]
    best_pf_row  = max(eligible, key=lambda x: x[2]['pf'])   if eligible else None
    best_tr_row  = max(eligible, key=lambda x: x[2]['totalr']) if eligible else None

    # console mini-table
    W = '{:>10s}  {:>4s}  {:>5s}  {:>6s}  {:>8s}  {:>6s}  {:>6s}  {}'
    print('=' * 72)
    print(f'Parameter: {param_display}  (baseline = {labels[baseline_val]})')
    print('-' * 72)
    print(W.format('VALUE', 'SIG', 'WR%', 'PF', 'TotalR', 'MaxDD', 'AvgR', 'TOP_REJ'))
    print('-' * 72)
    for v, lbl, m, is_bl in rows:
        marker = '*' if is_bl else ' '
        pf_tag  = '<<PF'  if best_pf_row and v == best_pf_row[0] else ''
        tr_tag  = '<<TR'  if best_tr_row and v == best_tr_row[0] else ''
        tags = (pf_tag + ' ' + tr_tag).strip()
        line = W.format(
            lbl + marker,
            str(m['sig']),
            f"{m['wr']:.1f}%",
            f"{m['pf']:.2f}",
            f"{m['totalr']:+.2f}R",
            f"{m['maxdd']:.2f}R",
            f"{m['avgr']:+.3f}",
            m['top_rej'],
        )
        print(line + (f'  {tags}' if tags else ''))
    print()

    # summary lines
    if best_pf_row:
        print(f'  Best PF    : {labels[best_pf_row[0]]}  (PF={best_pf_row[2]["pf"]:.2f},  TotalR={best_pf_row[2]["totalr"]:+.2f}R,  sig={best_pf_row[2]["sig"]})')
    if best_tr_row:
        print(f'  Best TotalR: {labels[best_tr_row[0]]}  (TotalR={best_tr_row[2]["totalr"]:+.2f}R,  PF={best_tr_row[2]["pf"]:.2f},  sig={best_tr_row[2]["sig"]})')

    # sensitivity note
    pf_vals   = [m['pf']     for _, _, m, _ in rows if m['sig'] >= 5]
    sig_vals  = [m['sig']    for _, _, m, _ in rows]
    pf_range  = max(pf_vals) - min(pf_vals) if len(pf_vals) >= 2 else 0
    sig_ratio = max(sig_vals) / max(min(sig_vals), 1)
    if pf_range > 1.0:
        sens = 'SENSITIVE  -- PF range {:.2f} across sweep'.format(pf_range)
    elif pf_range > 0.4:
        sens = 'MODERATE   -- PF range {:.2f} across sweep'.format(pf_range)
    else:
        sens = 'STABLE     -- PF range {:.2f} across sweep'.format(pf_range)
    vol_note = '  signal volume varies {:.1f}x across sweep'.format(sig_ratio)
    print(f'  Sensitivity: {sens}')
    print(f'  Volume     :{vol_note}')
    print()

# ── write CSV ─────────────────────────────────────────────────────────────────
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    fields = list(all_rows[0].keys())
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows(all_rows)

# ── write MD ──────────────────────────────────────────────────────────────────
# Gather summary data
def best_for(param_key, metric, rows_map):
    rows = [(v, lbl, m) for v, lbl, m in rows_map[param_key] if m['sig'] >= 5]
    if not rows: return None
    return max(rows, key=lambda x: x[2][metric])

def worst_pf_high_vol(param_key, rows_map):
    bl_val  = BL[param_key]
    bl_sig  = next(m['sig'] for v, lbl, m in rows_map[param_key] if v == bl_val)
    bl_pf   = next(m['pf']  for v, lbl, m in rows_map[param_key] if v == bl_val)
    return [(lbl, m) for v, lbl, m in rows_map[param_key]
            if m['sig'] > bl_sig and m['pf'] < bl_pf and v != bl_val]

rows_map = {}
for param_key, values, labels in SWEEPS:
    params_list = []
    for v in values:
        params = dict(BL); params[param_key] = v
        m = run_sweep(all_sym_bars, bias_map, params, IS_DAYS_N)
        params_list.append((v, labels[v], m))
    rows_map[param_key] = params_list

with open(OUT_MD, 'w', encoding='utf-8') as f:
    f.write('# Phase 4 -- Parameter Discovery (DST-Safe, IS Window)\n\n')
    f.write(f'**IS window:** {IS_START} -> {IS_END}  ({IS_DAYS_N} trading days)  \n')
    f.write('**Symbols:** ' + ', '.join(SCAN_SYMS) + '  \n')
    f.write('**Clock:** `_sm_et` (zoneinfo America/New_York)  \n')
    f.write('**Note:** Discovery only. Each parameter swept in isolation.  \n\n')

    f.write('## Baseline\n\n')
    bm = baseline_m
    f.write('| ADX | RVOL | ORB_RANGE | EMA20_DIST | BREAK_DIST | BODY_ATR | SESS_END |\n')
    f.write('|-----|------|-----------|------------|------------|----------|----------|\n')
    f.write('| 30.0 | 1.5 | 2.0 | 1.95 | 0.05 | 0.25 | 11:30ET |\n\n')
    f.write('| Signals | WR% | PF | TotalR | MaxDD | AvgR | Trades/Day |\n')
    f.write('|--------:|----:|---:|-------:|------:|-----:|-----------:|\n')
    f.write('| {} | {:.1f}% | {:.2f} | {:+.2f}R | {:.2f}R | {:+.3f} | {:.3f} |\n\n'.format(
        bm['sig'], bm['wr'], bm['pf'], bm['totalr'], bm['maxdd'], bm['avgr'], bm['tpd']))

    f.write('---\n\n## Sweep Results\n\n')

    for param_key, values, labels in SWEEPS:
        pname = param_key.upper().replace('_ET', '')
        bl_v  = BL[param_key]
        f.write(f'### {pname}  (baseline = {labels[bl_v]})\n\n')
        f.write('| Value | SIG | WR% | PF | TotalR | MaxDD | AvgR | T/Day | Top Rej |\n')
        f.write('|-------|----:|----:|---:|-------:|------:|-----:|------:|---------|\n')
        for v, lbl, m in rows_map[param_key]:
            bl_mark = ' **\\***' if v == bl_v else ''
            f.write('| {}{} | {} | {:.1f}% | {:.2f} | {:+.2f}R | {:.2f}R | {:+.3f} | {:.3f} | {} |\n'.format(
                lbl, bl_mark, m['sig'], m['wr'], m['pf'], m['totalr'],
                m['maxdd'], m['avgr'], m['tpd'], m['top_rej']))
        f.write('\n')

        eligible = [(v, lbl, m) for v, lbl, m in rows_map[param_key] if m['sig'] >= 5]
        if eligible:
            bp = max(eligible, key=lambda x: x[2]['pf'])
            bt = max(eligible, key=lambda x: x[2]['totalr'])
            f.write(f'- **Best PF:** `{bp[1]}` → PF={bp[2]["pf"]:.2f}, TotalR={bp[2]["totalr"]:+.2f}R, sig={bp[2]["sig"]}\n')
            f.write(f'- **Best TotalR:** `{bt[1]}` → TotalR={bt[2]["totalr"]:+.2f}R, PF={bt[2]["pf"]:.2f}, sig={bt[2]["sig"]}\n')

        damaging = worst_pf_high_vol(param_key, rows_map)
        if damaging:
            for lbl, m in damaging:
                f.write(f'- **Trades up / PF down:** `{lbl}` → sig={m["sig"]}, PF={m["pf"]:.2f}, MaxDD={m["maxdd"]:.2f}R\n')

        pf_vals  = [m['pf'] for _, _, m in rows_map[param_key] if m['sig'] >= 5]
        pf_range = max(pf_vals) - min(pf_vals) if len(pf_vals) >= 2 else 0
        if pf_range > 1.0:   sens = f'SENSITIVE (PF range {pf_range:.2f})'
        elif pf_range > 0.4: sens = f'MODERATE (PF range {pf_range:.2f})'
        else:                 sens = f'STABLE (PF range {pf_range:.2f})'
        f.write(f'- **Sensitivity:** {sens}\n\n')

    f.write('---\n\n## Summary Table\n\n')
    f.write('| Parameter | Baseline | Best PF value | Best PF | Best TotalR value | Best TotalR | Sensitivity |\n')
    f.write('|-----------|----------|--------------|---------|-------------------|-------------|-------------|\n')
    for param_key, values, labels in SWEEPS:
        bl_v = BL[param_key]
        eligible = [(v, lbl, m) for v, lbl, m in rows_map[param_key] if m['sig'] >= 5]
        if eligible:
            bp = max(eligible, key=lambda x: x[2]['pf'])
            bt = max(eligible, key=lambda x: x[2]['totalr'])
            pf_vals  = [m['pf'] for _, _, m in eligible]
            pf_range = max(pf_vals) - min(pf_vals)
            if pf_range > 1.0:   sens = 'SENSITIVE'
            elif pf_range > 0.4: sens = 'MODERATE'
            else:                 sens = 'STABLE'
            f.write('| {} | {} | {} | {:.2f} | {} | {:+.2f}R | {} |\n'.format(
                param_key, labels[bl_v],
                bp[1], bp[2]['pf'], bt[1], bt[2]['totalr'], sens))
        else:
            f.write('| {} | {} | n/a | n/a | n/a | n/a | n/a |\n'.format(
                param_key, labels[bl_v]))

    f.write('\n---\n\n## Notes\n\n')
    f.write('- All sweeps use IS window only (2025-09-17 to 2026-04-30).\n')
    f.write('- OOS window has not been inspected.\n')
    f.write('- No parameter value has been selected or recommended.\n')
    f.write('- Hypotheses will be generated in Phase 6 based on Phases 4 and 5 findings.\n')
    f.write('- Best PF / Best TotalR rows require >= 5 signals to be considered eligible.\n')

print(f'Baseline: sig={baseline_m["sig"]}  PF={baseline_m["pf"]:.2f}  '
      f'TotalR={baseline_m["totalr"]:+.2f}R  MaxDD={baseline_m["maxdd"]:.2f}R')
print()
print('Outputs written:')
print(f'  {OUT_CSV}')
print(f'  {OUT_MD}')
