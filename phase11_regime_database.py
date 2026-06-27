"""Phase 11 — Regime-Conditioned ORB Research: Database Build.

Scope: data collection ONLY.
  - NO hypotheses
  - NO parameter recommendations
  - NO optimization
  - NO analysis or interpretation

Builds a per-day research database joining two data streams:
  1. Daily regime features  (derived from 15m bars aggregated to daily OHLC)
  2. Baseline ORB outcomes  (BL params, BL_SYMS, per-day signal count and R)

Coverage: IS window (2025-09-17 -> 2026-04-30) + OOS window (2026-05-01 -> 2026-06-25)

Outputs:
  docs/research/phase11_regime_database.csv   -- one row per trading day
  docs/research/phase11_regime_database.md    -- column definitions + raw summary
"""
import os, json, sys, csv
from datetime import datetime, date
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_CSV = os.path.join('docs', 'research', 'phase11_regime_database.csv')
OUT_MD  = os.path.join('docs', 'research', 'phase11_regime_database.md')
RDIR    = 'chart_data_research'

_UTC = ZoneInfo('UTC'); _ET = ZoneInfo('America/New_York')

# ── windows ───────────────────────────────────────────────────────────────────
IS_START  = date(2025, 9, 17)
IS_END    = date(2026, 4, 30)
OOS_START = date(2026, 5,  1)
OOS_END   = date(2026, 6, 25)
WIN_START = IS_START   # scan the full combined window
WIN_END   = OOS_END

# ── locked baseline (do not change) ──────────────────────────────────────────
MIN_LB = 60; SL_ATR = 1.5; TP_ATR = 2.7; MAX_HOLD = 40
SESS_OPEN_ET = 0; SESS_ORB_DONE_ET = 30

BL_SYMS = ['AMZN','CRM','LLY','META','MSFT','NFLX','NVDA','PANW','QQQ']
BL = dict(adx_min=30.0, rvol_min=1.5, orb_range_min=2.0,
          ema20_dist_min=1.95, break_dist_min=0.05,
          body_atr=0.25, sess_brk_end_et=120)

# ── daily indicator period lengths ────────────────────────────────────────────
DAILY_ATR_P    = 14
DAILY_ADX_P    = 14
DAILY_EMA_FAST = 9
DAILY_EMA_SLOW = 20
DAILY_RANGE_P  = 20   # look-back for avg-range ratio

# ── loading ───────────────────────────────────────────────────────────────────

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
                    'dt':str(et.date()),'td':et.date(),
                    'ym':f'{et.year}-{et.month:02d}'})
    return out

def make_daily_bars(bars_15m):
    """Aggregate 15m bars to daily OHLC; open = first 15m open, close = last 15m close."""
    by_dt = defaultdict(list)
    for b in bars_15m:
        if b['sm'] >= 0:   # regular session bars only (sm >= 0 = 9:30 ET onwards)
            by_dt[b['dt']].append(b)
    result = []
    for dt in sorted(by_dt):
        day = sorted(by_dt[dt], key=lambda x: x['sm'])
        result.append({
            'dt':  dt,
            'td':  day[0]['td'],
            'ym':  day[0]['ym'],
            'open':  day[0]['open'],
            'high':  max(b['high'] for b in day),
            'low':   min(b['low']  for b in day),
            'close': day[-1]['close'],
        })
    return result

# ── daily-bar indicators ──────────────────────────────────────────────────────

def _wilder(v, p):
    k = 1.0 / p; r = [v[0]]
    for x in v[1:]: r.append(r[-1] + k * (x - r[-1]))
    return r

def _ema(v, p):
    k = 2.0 / (p + 1); r = [v[0]]
    for x in v[1:]: r.append(r[-1] + k * (x - r[-1]))
    return r

def daily_atr(bars, p=DAILY_ATR_P):
    tr = [bars[0]['high'] - bars[0]['low']]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]['high'], bars[i]['low'], bars[i-1]['close']
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return _wilder(tr, p)

def daily_adx(bars, p=DAILY_ADX_P):
    n = len(bars)
    if n < p + 2: return [0.0] * n
    pdm, mdm, tr = [], [], []
    for i in range(1, n):
        h, l = bars[i]['high'], bars[i]['low']
        ph, pl, pc = bars[i-1]['high'], bars[i-1]['low'], bars[i-1]['close']
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

def daily_range_ratio(bars, p=DAILY_RANGE_P):
    """Today range / p-day rolling avg range. 1.0 = average."""
    ranges = [b['high'] - b['low'] for b in bars]
    out = [1.0] * p
    for i in range(p, len(ranges)):
        avg = sum(ranges[i - p:i]) / p
        out.append(ranges[i] / avg if avg > 0 else 1.0)
    return out

def daily_gap_pct(bars):
    """(today open - prev close) / prev close × 100. First bar = 0.0."""
    out = [0.0]
    for i in range(1, len(bars)):
        pc = bars[i - 1]['close']
        out.append((bars[i]['open'] - pc) / pc * 100 if pc > 0 else 0.0)
    return out

# ── 15m-bar indicators (for intraday scan) ───────────────────────────────────

def calc_atr_15m(bars, p=14):
    tr = [bars[0]['high'] - bars[0]['low']]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]['high'], bars[i]['low'], bars[i-1]['close']
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return _wilder(tr, p)

def calc_adx_15m(bars, p=14):
    n = len(bars)
    if n < p + 2: return [0.0] * n
    pdm, mdm, tr = [], [], []
    for i in range(1, n):
        h, l = bars[i]['high'], bars[i]['low']
        ph, pl, pc = bars[i-1]['high'], bars[i-1]['low'], bars[i-1]['close']
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

def calc_rvol(bars, p=20):
    vols = [b['vol'] for b in bars]; out = [1.0] * p
    for i in range(p, len(vols)):
        avg = sum(vols[i-p:i]) / p
        out.append(vols[i] / avg if avg > 0 else 1.0)
    return out

def calc_ema20_15m(bars): return _ema([b['close'] for b in bars], 20)

def build_bias_15m(spy, qqq):
    bias = {}
    for bars in (spy, qqq):
        cl = [b['close'] for b in bars]; e9 = _ema(cl, 9); e20 = _ema(cl, 20)
        for i, b in enumerate(bars):
            ts = b['ts']; bull = e9[i] > e20[i]; prev = bias.get(ts)
            if prev is None:        bias[ts] = 'BULL' if bull else 'BEAR'
            elif (prev == 'BULL') == bull: pass
            else:                   bias[ts] = 'NEUTRAL'
    return bias

def build_orb(bars):
    orb = {}
    for b in bars:
        sm = b['sm']; dt = b['dt']
        if SESS_OPEN_ET <= sm < SESS_ORB_DONE_ET:
            if dt not in orb: orb[dt] = [b['high'], b['low'], False]
            else:
                orb[dt][0] = max(orb[dt][0], b['high'])
                orb[dt][1] = min(orb[dt][1], b['low'])
        elif dt in orb and not orb[dt][2] and sm >= SESS_ORB_DONE_ET:
            orb[dt][2] = True
    return {dt: (v[0], v[1]) for dt, v in orb.items() if v[2]}

# ── baseline scan (production-matched) ───────────────────────────────────────

def scan_sym(sym, pc, bias, params, win_start, win_end):
    bars=pc['bars']; atrs=pc['atrs']; adxs=pc['adxs']
    rvs=pc['rvs']; ema20s=pc['ema20s']; orb=pc['orb']
    sess_end = params['sess_brk_end_et']
    cands = []; emitted = set()
    for i in range(MIN_LB, len(bars)):
        b = bars[i]; td = b['td']; dt = b['dt']; sm = b['sm']
        if td < win_start or td > win_end: continue
        if sm < SESS_ORB_DONE_ET or sm >= sess_end: continue
        if dt not in orb: continue
        atr = atrs[i]
        if atr <= 0: continue
        oh, ol = orb[dt]
        adx = adxs[i]; rv = rvs[i]; bval = bias.get(b['ts'], 'NEUTRAL')
        body = abs(b['close'] - b['open']); e20 = ema20s[i]; c = b['close']
        if adx  < params['adx_min']:              continue
        if rv   < params['rvol_min']:             continue
        if body < params['body_atr'] * atr:       continue
        if (oh - ol) / atr < params['orb_range_min']: continue
        sm_mult = 1.3 if bval != 'NEUTRAL' else 1.0
        if (c > oh and c > b['open'] and bval != 'BEAR'
                and (dt, 'LONG') not in emitted
                and (c - e20) / atr >= params['ema20_dist_min']
                and (c - oh)  / atr >= params['break_dist_min']):
            cands.append({'dt': dt, 'sym': sym, 'dir': 'LONG',
                          'score': adx * rv * sm_mult, 'bi': i, 'atr': atr, 'entry': c})
            emitted.add((dt, 'LONG'))
        if (c < ol and c < b['open'] and bval != 'BULL'
                and (dt, 'SHORT') not in emitted
                and not (sym == 'MSFT' and bval == 'NEUTRAL')
                and (e20 - c) / atr >= params['ema20_dist_min']
                and (ol - c)  / atr >= params['break_dist_min']):
            cands.append({'dt': dt, 'sym': sym, 'dir': 'SHORT',
                          'score': adx * rv * sm_mult, 'bi': i, 'atr': atr, 'entry': c})
            emitted.add((dt, 'SHORT'))
    return cands

def simulate(bars, bi, direction, entry, atr):
    stop = entry - SL_ATR * atr if direction == 'LONG' else entry + SL_ATR * atr
    tp   = entry + TP_ATR * atr if direction == 'LONG' else entry - TP_ATR * atr
    risk = abs(entry - stop)
    if risk <= 0: return 0.0
    n = len(bars)
    for j in range(bi + 1, min(bi + 1 + MAX_HOLD, n)):
        b = bars[j]
        if direction == 'LONG':
            if b['high'] >= tp:   return (tp   - entry) / risk
            if b['low']  <= stop: return (stop - entry) / risk
        else:
            if b['low']  <= tp:   return (entry - tp)   / risk
            if b['high'] >= stop: return (entry - stop) / risk
    j = min(bi + MAX_HOLD, n - 1)
    return (bars[j]['close'] - entry) / risk if direction == 'LONG' \
           else (entry - bars[j]['close']) / risk

def run_baseline(precomp, bias):
    all_cands = []
    for sym in BL_SYMS:
        if sym not in precomp: continue
        all_cands.extend(scan_sym(sym, precomp[sym], bias, BL, WIN_START, WIN_END))
    all_cands.sort(key=lambda x: x['dt'])
    trades = []
    for t in all_cands:
        r = simulate(precomp[t['sym']]['bars'], t['bi'], t['dir'], t['entry'], t['atr'])
        trades.append({'dt': t['dt'], 'sym': t['sym'], 'dir': t['dir'], 'r': r})
    return trades

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 11 — Regime-Conditioned ORB Research: Database Build')
print('='*70)
print('Scope: data collection only. No hypotheses. No recommendations.')
print(f'Window: {WIN_START} → {WIN_END}')
print()

# Load 15m bars
print('Loading 15m bars...', flush=True)
needed_15m = set(BL_SYMS) | {'SPY'}
raw_15m = {}
for sym in needed_15m:
    try: raw_15m[sym] = load_15m(sym)
    except FileNotFoundError: print(f'  MISSING: {sym}')

# Build 15m bias
bias = build_bias_15m(raw_15m.get('SPY', []), raw_15m.get('QQQ', []))

# Precompute 15m indicators (for baseline scan)
precomp = {}
for sym in BL_SYMS:
    if sym not in raw_15m: continue
    bars = raw_15m[sym]
    precomp[sym] = {
        'bars':   bars,
        'atrs':   calc_atr_15m(bars),
        'adxs':   calc_adx_15m(bars),
        'rvs':    calc_rvol(bars),
        'ema20s': calc_ema20_15m(bars),
        'orb':    build_orb(bars),
    }

# Build sm=30 index for breadth feature
# sm=30 → 10:00 ET bar (first bar of ORB scan window)
sm30_idx = {}   # (sym, dt) -> bar index
for sym in BL_SYMS:
    if sym not in precomp: continue
    for i, b in enumerate(precomp[sym]['bars']):
        if b['sm'] == 30:
            sm30_idx[(sym, b['dt'])] = i

print(f'Precomputed {len(precomp)} symbols (15m).')

# Build daily bars for SPY and QQQ (for regime features)
print('Building daily bars for SPY, QQQ...', flush=True)
spy_daily  = make_daily_bars(raw_15m['SPY'])
qqq_daily  = make_daily_bars(raw_15m.get('QQQ', raw_15m['SPY']))

# Daily indicators for SPY
spy_atr14      = daily_atr(spy_daily)
spy_adx14      = daily_adx(spy_daily)
spy_range_ratio = daily_range_ratio(spy_daily)
spy_gap_pct    = daily_gap_pct(spy_daily)
spy_ema9       = _ema([b['close'] for b in spy_daily], DAILY_EMA_FAST)
spy_ema20      = _ema([b['close'] for b in spy_daily], DAILY_EMA_SLOW)

# Daily indicators for QQQ
qqq_ema9  = _ema([b['close'] for b in qqq_daily], DAILY_EMA_FAST)
qqq_ema20 = _ema([b['close'] for b in qqq_daily], DAILY_EMA_SLOW)

# Map dt -> index for SPY and QQQ daily bars
spy_dt_idx = {b['dt']: i for i, b in enumerate(spy_daily)}
qqq_dt_idx = {b['dt']: i for i, b in enumerate(qqq_daily)}

# Run baseline scan (IS + OOS combined)
print('Running baseline scan (IS + OOS)...', flush=True)
bl_trades = run_baseline(precomp, bias)

# Group baseline outcomes by date
by_date = defaultdict(list)
for t in bl_trades: by_date[t['dt']].append(t['r'])

# Collect all trading days in window from SPY daily bars
trading_days = [b for b in spy_daily
                if WIN_START <= b['td'] <= WIN_END]

print(f'Trading days in database: {len(trading_days)}'
      f'  (IS: {sum(1 for b in trading_days if b["td"] <= IS_END)}'
      f'  OOS: {sum(1 for b in trading_days if b["td"] > IS_END)})')
print()

# ── build database rows ───────────────────────────────────────────────────────

rows = []
for b in trading_days:
    dt  = b['dt']
    td  = b['td']
    ym  = b['ym']
    win = 'IS' if td <= IS_END else 'OOS'

    # ── SPY daily regime features ─────────────────────────────────────────────
    si = spy_dt_idx.get(dt)
    if si is None:
        continue   # should not happen

    spy_gap    = spy_gap_pct[si]
    spy_atr    = spy_atr14[si]
    spy_atr_pct = spy_atr / spy_daily[si]['close'] * 100 if spy_daily[si]['close'] > 0 else 0.0
    spy_rr     = spy_range_ratio[si]
    spy_adx    = spy_adx14[si]
    spy_e9     = spy_ema9[si]
    spy_e20    = spy_ema20[si]
    spy_bull   = 1 if spy_e9 > spy_e20 else 0

    # ── QQQ daily regime features ─────────────────────────────────────────────
    qi = qqq_dt_idx.get(dt)
    if qi is not None:
        qqq_bull = 1 if qqq_ema9[qi] > qqq_ema20[qi] else 0
    else:
        qqq_bull = spy_bull   # fallback if QQQ daily bar missing

    if   spy_bull == 1 and qqq_bull == 1: regime = 'BULL'
    elif spy_bull == 0 and qqq_bull == 0: regime = 'BEAR'
    else:                                 regime = 'NEUTRAL'

    # ── intraday breadth at 10:00 ET (sm=30) ─────────────────────────────────
    above_ema20 = 0; total_syms = 0
    for sym in BL_SYMS:
        key = (sym, dt)
        if key in sm30_idx:
            idx = sm30_idx[key]
            close_sm30 = precomp[sym]['bars'][idx]['close']
            ema20_sm30 = precomp[sym]['ema20s'][idx]
            total_syms += 1
            if close_sm30 >= ema20_sm30:
                above_ema20 += 1
    breadth = round(100.0 * above_ema20 / total_syms, 1) if total_syms > 0 else None

    # ── ORB quality across scan symbols ──────────────────────────────────────
    orb_ranges_atr = []
    for sym in BL_SYMS:
        if sym not in precomp: continue
        orb_map = precomp[sym]['orb']
        if dt not in orb_map: continue
        oh, ol = orb_map[dt]
        key = (sym, dt)
        if key in sm30_idx:
            idx = sm30_idx[key]
            atr_val = precomp[sym]['atrs'][idx]
            if atr_val > 0:
                orb_ranges_atr.append((oh - ol) / atr_val)
    orb_avg_atr = round(sum(orb_ranges_atr) / len(orb_ranges_atr), 3) \
                  if orb_ranges_atr else None
    orb_n_valid = len(orb_ranges_atr)

    # ── baseline outcomes ─────────────────────────────────────────────────────
    day_r = by_date.get(dt, [])
    n_sig   = len(day_r)
    total_r = round(sum(day_r), 4)
    n_wins  = sum(1 for r in day_r if r > 0)
    wr_pct  = round(100.0 * n_wins / n_sig, 1) if n_sig > 0 else None

    rows.append({
        'date':           dt,
        'ym':             ym,
        'window':         win,
        'spy_gap_pct':    round(spy_gap, 3),
        'spy_atr14':      round(spy_atr, 4),
        'spy_atr14_pct':  round(spy_atr_pct, 3),
        'spy_range_ratio':round(spy_rr, 3),
        'spy_adx14':      round(spy_adx, 2),
        'spy_ema9_gt_ema20': spy_bull,
        'qqq_ema9_gt_ema20': qqq_bull,
        'market_regime':  regime,
        'breadth_pct':    breadth,
        'orb_range_avg_atr': orb_avg_atr,
        'orb_n_valid':    orb_n_valid,
        'n_signals':      n_sig,
        'total_r':        total_r,
        'n_wins':         n_wins,
        'wr_pct':         wr_pct,
    })

# ── write CSV ─────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
COLS = ['date','ym','window',
        'spy_gap_pct','spy_atr14','spy_atr14_pct','spy_range_ratio','spy_adx14',
        'spy_ema9_gt_ema20','qqq_ema9_gt_ema20','market_regime',
        'breadth_pct','orb_range_avg_atr','orb_n_valid',
        'n_signals','total_r','n_wins','wr_pct']
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    for r in rows: w.writerow(r)
print(f'CSV written: {OUT_CSV}  ({len(rows)} rows × {len(COLS)} columns)')

# ── raw summary for console ───────────────────────────────────────────────────

# Count by window
is_rows  = [r for r in rows if r['window'] == 'IS']
oos_rows = [r for r in rows if r['window'] == 'OOS']

# Count by regime
from collections import Counter
regime_counts = Counter(r['market_regime'] for r in rows)
is_regime     = Counter(r['market_regime'] for r in is_rows)
oos_regime    = Counter(r['market_regime'] for r in oos_rows)

# Signal days (days with at least 1 baseline signal)
sig_days = [r for r in rows if r['n_signals'] > 0]
no_sig   = [r for r in rows if r['n_signals'] == 0]

def _mean(vals): return sum(vals) / len(vals) if vals else 0.0
def _fmt(v, d=2): return f'{v:.{d}f}' if v is not None else 'n/a'

print()
print('Database Summary (raw counts — no interpretation)')
print('-'*60)
print(f'Total rows        : {len(rows)}')
print(f'  IS  ({IS_START} → {IS_END}): {len(is_rows)}')
print(f'  OOS ({OOS_START} → {OOS_END}): {len(oos_rows)}')
print()
print('Market regime distribution (IS + OOS combined):')
for reg in ('BULL', 'BEAR', 'NEUTRAL'):
    total_c = regime_counts.get(reg, 0)
    is_c    = is_regime.get(reg, 0)
    oos_c   = oos_regime.get(reg, 0)
    print(f'  {reg:<8}: {total_c:>4} days  (IS:{is_c:>3}  OOS:{oos_c:>3})')
print()
print('Signal activity (baseline, IS + OOS):')
print(f'  Days with signals : {len(sig_days)}')
print(f'  Days no signals   : {len(no_sig)}')
print(f'  Total signals     : {sum(r["n_signals"] for r in rows)}')
print(f'  Total R (all days): {sum(r["total_r"] for r in rows):+.2f}R')
print()
print('Feature ranges (IS + OOS):')
spy_gaps = [r['spy_gap_pct'] for r in rows]
spy_adxs = [r['spy_adx14']   for r in rows]
spy_rrs  = [r['spy_range_ratio'] for r in rows]
breadths = [r['breadth_pct'] for r in rows if r['breadth_pct'] is not None]
orb_avgs = [r['orb_range_avg_atr'] for r in rows if r['orb_range_avg_atr'] is not None]
print(f'  spy_gap_pct       : min {min(spy_gaps):.2f}%  max {max(spy_gaps):.2f}%  mean {_mean(spy_gaps):.3f}%')
print(f'  spy_adx14         : min {min(spy_adxs):.1f}   max {max(spy_adxs):.1f}   mean {_mean(spy_adxs):.2f}')
print(f'  spy_range_ratio   : min {min(spy_rrs):.2f}   max {max(spy_rrs):.2f}   mean {_mean(spy_rrs):.3f}')
print(f'  breadth_pct       : min {min(breadths):.0f}%   max {max(breadths):.0f}%   mean {_mean(breadths):.1f}%')
print(f'  orb_range_avg_atr : min {min(orb_avgs):.2f}   max {max(orb_avgs):.2f}   mean {_mean(orb_avgs):.3f}')
print()

# Per-month raw signal totals (no analysis)
print('Per-month raw totals (IS + OOS):')
print(f'  {"Month":<10} {"Window":>6} {"Regime days (B/N/Be)":>24} {"SignalDays":>10} {"TotalR":>9}')
print(f'  {"-"*65}')
from itertools import groupby
by_ym = defaultdict(list)
for r in rows: by_ym[r['ym']].append(r)
for ym in sorted(by_ym):
    grp  = by_ym[ym]
    win  = grp[0]['window']
    bull = sum(1 for r in grp if r['market_regime'] == 'BULL')
    neut = sum(1 for r in grp if r['market_regime'] == 'NEUTRAL')
    bear = sum(1 for r in grp if r['market_regime'] == 'BEAR')
    sdays = sum(1 for r in grp if r['n_signals'] > 0)
    tr   = sum(r['total_r'] for r in grp)
    print(f'  {ym:<10} {win:>6} {bull:>5}B {neut:>3}N {bear:>3}Be {sdays:>10} {tr:>+9.2f}R')

# ── write MD ──────────────────────────────────────────────────────────────────
with open(OUT_MD, 'w', encoding='utf-8') as f:
    f.write('# Phase 11 — Regime-Conditioned ORB Research: Database\n\n')
    f.write('> **Scope:** Data collection only. No hypotheses. No recommendations. No optimization.\n\n')
    f.write(f'**IS window:** {IS_START} → {IS_END}  \n')
    f.write(f'**OOS window:** {OOS_START} → {OOS_END}  \n')
    f.write(f'**Database:** `{OUT_CSV}`  \n')
    f.write(f'**Rows:** {len(rows)} trading days  \n')
    f.write(f'**Columns:** {len(COLS)}  \n\n---\n\n')

    f.write('## Column Definitions\n\n')
    f.write('| Column | Type | Source | Description |\n')
    f.write('|--------|------|--------|-------------|\n')
    defs = [
        ('date',              'str',   'SPY 15m',  'Trading day YYYY-MM-DD'),
        ('ym',                'str',   'derived',  'Year-month YYYY-MM'),
        ('window',            'str',   'derived',  'IS or OOS'),
        ('spy_gap_pct',       'float', 'SPY daily','(open − prev_close) / prev_close × 100'),
        ('spy_atr14',         'float', 'SPY daily','14-day Wilder ATR on daily bars (points)'),
        ('spy_atr14_pct',     'float', 'SPY daily','spy_atr14 / close × 100 (% of price, normalized)'),
        ('spy_range_ratio',   'float', 'SPY daily','today range / 20-day avg range (1.0 = average)'),
        ('spy_adx14',         'float', 'SPY daily','14-day ADX on daily bars (trend strength)'),
        ('spy_ema9_gt_ema20', 'int',   'SPY daily','1 if daily EMA9 > EMA20, else 0'),
        ('qqq_ema9_gt_ema20', 'int',   'QQQ daily','1 if daily EMA9 > EMA20, else 0'),
        ('market_regime',     'str',   'derived',  'BULL (both=1) / BEAR (both=0) / NEUTRAL (mixed)'),
        ('breadth_pct',       'float', '15m sm=30','% of BL_SYMS with close ≥ EMA20 at 10:00 ET'),
        ('orb_range_avg_atr', 'float', '15m precomp','mean (ORB_high−ORB_low)/ATR across valid symbols'),
        ('orb_n_valid',       'int',   '15m precomp','count of symbols with a valid ORB on this day'),
        ('n_signals',         'int',   'BL scan',  'baseline ORB signals fired (all BL_SYMS)'),
        ('total_r',           'float', 'BL scan',  'sum of R multiples from baseline trades that day'),
        ('n_wins',            'int',   'BL scan',  'count of winning baseline trades'),
        ('wr_pct',            'float', 'BL scan',  'win rate % (null if n_signals=0)'),
    ]
    for col, typ, src, desc in defs:
        f.write(f'| `{col}` | {typ} | {src} | {desc} |\n')
    f.write('\n')
    f.write('**ATR periods:** 14-day Wilder on daily bars for SPY/QQQ.  \n')
    f.write('**ADX period:** 14-day on daily bars (standard Wilder).  \n')
    f.write('**EMA periods:** fast=9, slow=20 on daily close.  \n')
    f.write('**Range ratio look-back:** 20 trading days.  \n')
    f.write('**Breadth bar:** first bar of scan window (sm=30, 10:00 ET open).  \n')
    f.write('**ORB ATR:** 14-period Wilder ATR from 15m bars at the sm=30 bar index.  \n')
    f.write('**Baseline:** BL_SYMS, BL params (locked from Phase 4).  \n\n---\n\n')

    f.write('## Raw Database Statistics\n\n')
    f.write('> Statistics only. No analysis or recommendation.\n\n')

    f.write('### Row counts\n\n')
    f.write(f'| Window | Days |\n|--------|-----:|\n')
    f.write(f'| IS  ({IS_START} → {IS_END}) | {len(is_rows)} |\n')
    f.write(f'| OOS ({OOS_START} → {OOS_END}) | {len(oos_rows)} |\n')
    f.write(f'| **Total** | **{len(rows)}** |\n\n')

    f.write('### Market regime counts\n\n')
    f.write('| Regime | IS | OOS | Total |\n|--------|---:|----:|------:|\n')
    for reg in ('BULL', 'BEAR', 'NEUTRAL'):
        f.write(f'| {reg} | {is_regime.get(reg,0)} | {oos_regime.get(reg,0)} | '
                f'{regime_counts.get(reg,0)} |\n')
    f.write('\n')

    f.write('### Signal activity (baseline, combined window)\n\n')
    f.write(f'| Metric | Value |\n|--------|------:|\n')
    f.write(f'| Days with ≥1 signal | {len(sig_days)} |\n')
    f.write(f'| Days with 0 signals | {len(no_sig)} |\n')
    f.write(f'| Total signals | {sum(r["n_signals"] for r in rows)} |\n')
    f.write(f'| Total R | {sum(r["total_r"] for r in rows):+.2f}R |\n\n')

    f.write('### Feature ranges (IS + OOS combined)\n\n')
    f.write('| Feature | Min | Max | Mean |\n|---------|----:|----:|-----:|\n')
    f.write(f'| spy_gap_pct | {min(spy_gaps):.2f}% | {max(spy_gaps):.2f}% | {_mean(spy_gaps):.3f}% |\n')
    f.write(f'| spy_atr14 | {min(spy_atr14[spy_dt_idx[r["date"]]] for r in rows):.3f} | '
            f'{max(spy_atr14[spy_dt_idx[r["date"]]] for r in rows):.3f} | '
            f'{_mean([spy_atr14[spy_dt_idx[r["date"]]] for r in rows]):.3f} |\n')
    f.write(f'| spy_atr14_pct | {min(r["spy_atr14_pct"] for r in rows):.3f}% | '
            f'{max(r["spy_atr14_pct"] for r in rows):.3f}% | '
            f'{_mean([r["spy_atr14_pct"] for r in rows]):.3f}% |\n')
    f.write(f'| spy_range_ratio | {min(spy_rrs):.3f} | {max(spy_rrs):.3f} | {_mean(spy_rrs):.3f} |\n')
    f.write(f'| spy_adx14 | {min(spy_adxs):.1f} | {max(spy_adxs):.1f} | {_mean(spy_adxs):.2f} |\n')
    f.write(f'| breadth_pct | {min(breadths):.0f}% | {max(breadths):.0f}% | {_mean(breadths):.1f}% |\n')
    f.write(f'| orb_range_avg_atr | {min(orb_avgs):.3f} | {max(orb_avgs):.3f} | {_mean(orb_avgs):.3f} |\n\n')

    f.write('### Per-month raw totals\n\n')
    f.write('| Month | Window | Bull | Neutral | Bear | Signal Days | Total R |\n')
    f.write('|-------|--------|-----:|--------:|-----:|------------:|--------:|\n')
    for ym in sorted(by_ym):
        grp  = by_ym[ym]
        win  = grp[0]['window']
        bull = sum(1 for r in grp if r['market_regime'] == 'BULL')
        neut = sum(1 for r in grp if r['market_regime'] == 'NEUTRAL')
        bear = sum(1 for r in grp if r['market_regime'] == 'BEAR')
        sdays = sum(1 for r in grp if r['n_signals'] > 0)
        tr   = sum(r['total_r'] for r in grp)
        f.write(f'| {ym} | {win} | {bull} | {neut} | {bear} | {sdays} | {tr:+.2f}R |\n')
    f.write('\n---\n\n')
    f.write('**Next step:** Phase 11 regime analysis requires explicit user approval before any filtering or hypothesis testing.\n')

print(f'MD  written: {OUT_MD}')
print()
print('Phase 11 database build complete.')
print(f'  Rows    : {len(rows)} trading days')
print(f'  Columns : {len(COLS)}')
print(f'  IS days : {len(is_rows)}  |  OOS days: {len(oos_rows)}')
