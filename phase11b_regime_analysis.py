"""Phase 11B — Regime Analysis.
Reads phase11_regime_database.csv and reports raw statistics by regime group.
NO filters. NO strategy changes. NO parameter optimization. NO recommendations.
Analysis and reporting only.
"""
import os, csv, sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

IN_CSV = os.path.join('docs', 'research', 'phase11_regime_database.csv')
OUT_MD = os.path.join('docs', 'research', 'phase11b_regime_analysis.md')

# ── load database ─────────────────────────────────────────────────────────────

def load_db(path):
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            rows.append({
                'date':             r['date'],
                'ym':               r['ym'],
                'window':           r['window'],
                'spy_gap_pct':      float(r['spy_gap_pct']),
                'spy_atr14':        float(r['spy_atr14']),
                'spy_atr14_pct':    float(r['spy_atr14_pct']),
                'spy_range_ratio':  float(r['spy_range_ratio']),
                'spy_adx14':        float(r['spy_adx14']),
                'spy_trend':        int(r['spy_ema9_gt_ema20']),
                'qqq_trend':        int(r['qqq_ema9_gt_ema20']),
                'market_regime':    r['market_regime'],
                'breadth_pct':      float(r['breadth_pct']) if r['breadth_pct'] else None,
                'orb_avg_atr':      float(r['orb_range_avg_atr']) if r['orb_range_avg_atr'] else None,
                'orb_n_valid':      int(r['orb_n_valid']),
                'n_signals':        int(r['n_signals']),
                'total_r':          float(r['total_r']),
                'n_wins':           int(r['n_wins']),
                'wr_pct':           float(r['wr_pct']) if r['wr_pct'] else None,
            })
    return rows

# ── aggregate helper ──────────────────────────────────────────────────────────

def agg(rows):
    """Compute raw group statistics from a list of day-rows."""
    n_days     = len(rows)
    sig_days   = [r for r in rows if r['n_signals'] > 0]
    n_sig_days = len(sig_days)
    n_sigs     = sum(r['n_signals'] for r in rows)
    total_r    = sum(r['total_r']   for r in rows)
    n_wins     = sum(r['n_wins']    for r in rows)

    # collect individual trade R values from daily totals isn't possible directly,
    # so compute PF from win/loss R sums using the aggregate total_r and win count
    # proxy: gross_wins = n_wins * avg_win_r, gross_losses = (n_sigs-n_wins) * avg_loss_r
    # Better: compute from signal-day data only using total_r and WR
    win_r  = sum(r['total_r'] for r in rows if r['total_r'] > 0)
    loss_r = abs(sum(r['total_r'] for r in rows if r['total_r'] < 0))
    pf     = win_r / loss_r if loss_r > 0 else (float('inf') if win_r > 0 else 0.0)

    avg_r_per_trade  = total_r / n_sigs     if n_sigs > 0     else 0.0
    avg_r_per_day    = total_r / n_sig_days if n_sig_days > 0 else 0.0
    wr               = 100.0 * n_wins / n_sigs if n_sigs > 0  else 0.0

    # feature averages (over all days in group)
    def fmean(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else 0.0

    return {
        'n_days':        n_days,
        'n_sig_days':    n_sig_days,
        'sig_day_pct':   100.0 * n_sig_days / n_days if n_days > 0 else 0.0,
        'n_sigs':        n_sigs,
        'total_r':       total_r,
        'pf':            pf,
        'wr':            wr,
        'avg_r_trade':   avg_r_per_trade,
        'avg_r_sigday':  avg_r_per_day,
        'avg_gap':       fmean('spy_gap_pct'),
        'avg_atr_pct':   fmean('spy_atr14_pct'),
        'avg_rr':        fmean('spy_range_ratio'),
        'avg_adx':       fmean('spy_adx14'),
        'avg_breadth':   fmean('breadth_pct'),
        'avg_orb_atr':   fmean('orb_avg_atr'),
    }

def pf_s(v):
    return f'{v:.2f}' if v != float('inf') else 'inf'

def group_table(groups, title, col_label='Group'):
    """Print a formatted group comparison table. Returns list of (label, agg) tuples."""
    print(f'\n{title}')
    print('-' * 110)
    H = (f'{"Group":<22} {"Days":>5} {"SigDays":>8} {"Sig%":>6} {"NSig":>5} '
         f'{"TotalR":>8} {"AvgR/T":>8} {"WR":>7} {"PF":>6} '
         f'{"AvgGap%":>8} {"AvgADX":>7} {"AvgRR":>7} {"Breadth":>8}')
    print(H)
    print('-' * 110)
    results = []
    for label, m in groups:
        print(f'{label:<22} {m["n_days"]:>5} {m["n_sig_days"]:>8} '
              f'{m["sig_day_pct"]:>5.1f}% {m["n_sigs"]:>5} '
              f'{m["total_r"]:>+8.2f} {m["avg_r_trade"]:>+8.3f} '
              f'{m["wr"]:>6.1f}% {pf_s(m["pf"]):>6} '
              f'{m["avg_gap"]:>+7.3f}% {m["avg_adx"]:>7.1f} '
              f'{m["avg_rr"]:>7.3f} {m["avg_breadth"]:>7.1f}%')
        results.append((label, m))
    return results

# ── band helpers ──────────────────────────────────────────────────────────────

def adx_band(v):
    if v < 20:  return 'ADX <20 (weak trend)'
    if v < 35:  return 'ADX 20-35 (moderate)'
    return              'ADX ≥35 (strong trend)'

def rr_band(v):
    if v < 0.80: return 'RR <0.80 (compression)'
    if v < 1.20: return 'RR 0.80-1.20 (normal)'
    return               'RR ≥1.20 (expansion)'

def breadth_band(v):
    if v is None: return 'Unknown'
    if v < 33.4: return 'Breadth <33% (weak)'
    if v < 66.7: return 'Breadth 33-67% (mixed)'
    return               'Breadth ≥67% (strong)'

def orb_band(v):
    if v is None: return 'Unknown'
    if v < 2.5:  return 'ORB <2.5 ATR (tight)'
    if v < 3.5:  return 'ORB 2.5-3.5 ATR (avg)'
    return               'ORB ≥3.5 ATR (wide)'

def gap_band(v):
    if v < -0.50: return 'Gap ≤-0.50% (gap down)'
    if v <  0.00: return 'Gap -0.50-0.00% (sm dn)'
    if v <  0.50: return 'Gap 0.00-0.50% (sm up)'
    return                'Gap ≥0.50% (gap up)'

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 11B — Regime Analysis')
print('='*70)
print('Analysis only. No filters. No optimization. No recommendations.')
print()

db = load_db(IN_CSV)
print(f'Loaded {len(db)} rows from {IN_CSV}')

is_rows  = [r for r in db if r['window'] == 'IS']
oos_rows = [r for r in db if r['window'] == 'OOS']
may_rows = [r for r in db if r['ym'] == '2026-05']
jun_rows = [r for r in db if r['ym'] == '2026-06']

# ── 1. By market regime ───────────────────────────────────────────────────────

regime_groups = {}
for reg in ('BULL', 'BEAR', 'NEUTRAL'):
    regime_groups[reg] = [r for r in db if r['market_regime'] == reg]

reg_pairs = [(k, agg(v)) for k, v in regime_groups.items()]
group_table(reg_pairs, '1. BY MARKET REGIME (BULL/BEAR/NEUTRAL) — IS + OOS combined')

# Split by window too
print('\n  IS only:')
reg_is = [(k, agg([r for r in v if r['window']=='IS'])) for k,v in regime_groups.items()]
for lbl, m in reg_is:
    print(f'    {lbl:<10} Days={m["n_days"]:>3}  SigDays={m["n_sig_days"]:>2}  '
          f'TotalR={m["total_r"]:>+7.2f}  AvgR/T={m["avg_r_trade"]:>+6.3f}  '
          f'WR={m["wr"]:>5.1f}%  PF={pf_s(m["pf"]):>5}')
print('  OOS only:')
reg_oos = [(k, agg([r for r in v if r['window']=='OOS'])) for k,v in regime_groups.items()]
for lbl, m in reg_oos:
    print(f'    {lbl:<10} Days={m["n_days"]:>3}  SigDays={m["n_sig_days"]:>2}  '
          f'TotalR={m["total_r"]:>+7.2f}  AvgR/T={m["avg_r_trade"]:>+6.3f}  '
          f'WR={m["wr"]:>5.1f}%  PF={pf_s(m["pf"]):>5}')

# ── 2. By ADX band ────────────────────────────────────────────────────────────

by_adx = defaultdict(list)
for r in db: by_adx[adx_band(r['spy_adx14'])].append(r)
adx_order = ['ADX <20 (weak trend)', 'ADX 20-35 (moderate)', 'ADX ≥35 (strong trend)']
adx_pairs = [(k, agg(by_adx[k])) for k in adx_order if by_adx[k]]
group_table(adx_pairs, '2. BY SPY DAILY ADX BAND')

# ── 3. By range-ratio band ────────────────────────────────────────────────────

by_rr = defaultdict(list)
for r in db: by_rr[rr_band(r['spy_range_ratio'])].append(r)
rr_order = ['RR <0.80 (compression)', 'RR 0.80-1.20 (normal)', 'RR ≥1.20 (expansion)']
rr_pairs = [(k, agg(by_rr[k])) for k in rr_order if by_rr[k]]
group_table(rr_pairs, '3. BY SPY DAILY RANGE RATIO (today range / 20-day avg)')

# ── 4. By breadth band ────────────────────────────────────────────────────────

by_br = defaultdict(list)
for r in db: by_br[breadth_band(r['breadth_pct'])].append(r)
br_order = ['Breadth <33% (weak)', 'Breadth 33-67% (mixed)', 'Breadth ≥67% (strong)']
br_pairs = [(k, agg(by_br[k])) for k in br_order if by_br[k]]
group_table(br_pairs, '4. BY INTRADAY BREADTH AT 10:00 ET')

# ── 5. By ORB size band ───────────────────────────────────────────────────────

by_orb = defaultdict(list)
for r in db: by_orb[orb_band(r['orb_avg_atr'])].append(r)
orb_order = ['ORB <2.5 ATR (tight)', 'ORB 2.5-3.5 ATR (avg)', 'ORB ≥3.5 ATR (wide)']
orb_pairs = [(k, agg(by_orb[k])) for k in orb_order if by_orb[k]]
group_table(orb_pairs, '5. BY AVERAGE ORB RANGE (ATR units, across scan symbols)')

# ── 6. By gap band ────────────────────────────────────────────────────────────

by_gap = defaultdict(list)
for r in db: by_gap[gap_band(r['spy_gap_pct'])].append(r)
gap_order = ['Gap ≤-0.50% (gap down)', 'Gap -0.50-0.00% (sm dn)',
             'Gap 0.00-0.50% (sm up)', 'Gap ≥0.50% (gap up)']
gap_pairs = [(k, agg(by_gap[k])) for k in gap_order if by_gap[k]]
group_table(gap_pairs, '6. BY SPY OVERNIGHT GAP SIZE')

# ── 7. Regime × ADX cross-tab ─────────────────────────────────────────────────

print('\n7. REGIME × ADX CROSS-TAB (AvgR/trade  |  n_sigs)')
print('-' * 70)
print(f'{"Regime":<12}  {"ADX <20":>14}  {"ADX 20-35":>14}  {"ADX ≥35":>14}')
print('-' * 70)
for reg in ('BULL', 'BEAR', 'NEUTRAL'):
    cells = []
    for band in adx_order:
        sub = [r for r in db if r['market_regime']==reg and adx_band(r['spy_adx14'])==band]
        if not sub:
            cells.append('   --        ')
        else:
            m = agg(sub)
            cells.append(f'{m["avg_r_trade"]:>+6.3f}R (n={m["n_sigs"]:>2})')
    print(f'{reg:<12}  {"  |  ".join(cells)}')

# ── 8. May vs June deep-dive ──────────────────────────────────────────────────

print('\n8. MAY vs JUNE 2026 — FEATURE COMPARISON (OOS only)')
print('-' * 70)

def _mean(vals): return sum(vals) / len(vals) if vals else 0.0
def _med(vals):
    s = sorted(v for v in vals if v is not None)
    if not s: return 0.0
    n = len(s); return (s[n//2-1]+s[n//2])/2 if n%2==0 else s[n//2]

features = [
    ('spy_adx14',        'SPY ADX (daily)'),
    ('spy_range_ratio',  'SPY range ratio'),
    ('spy_atr14_pct',    'SPY ATR % of price'),
    ('spy_gap_pct',      'SPY gap %'),
    ('breadth_pct',      'Breadth at 10:00 ET'),
    ('orb_avg_atr',      'ORB range (ATR)'),
]

print(f'{"Feature":<26} {"May mean":>10} {"Jun mean":>10} {"Delta":>10}')
print('-' * 60)
for key, label in features:
    may_v = _mean([r[key] for r in may_rows if r[key] is not None])
    jun_v = _mean([r[key] for r in jun_rows if r[key] is not None])
    print(f'{label:<26} {may_v:>10.3f} {jun_v:>10.3f} {jun_v-may_v:>+10.3f}')

print()
print('  May 2026 regime split:')
for reg in ('BULL','BEAR','NEUTRAL'):
    n = sum(1 for r in may_rows if r['market_regime']==reg)
    if n: print(f'    {reg}: {n} days')

print('  Jun 2026 regime split:')
for reg in ('BULL','BEAR','NEUTRAL'):
    n = sum(1 for r in jun_rows if r['market_regime']==reg)
    if n: print(f'    {reg}: {n} days')

print()
may_m = agg(may_rows); jun_m = agg(jun_rows)
print(f'  {"Metric":<20} {"May":>10} {"Jun":>10} {"Delta":>10}')
print(f'  {"-"*52}')
for label, mk, jk in [
    ('Signal days',    may_m["n_sig_days"],   jun_m["n_sig_days"]),
    ('Total signals',  may_m["n_sigs"],        jun_m["n_sigs"]),
    ('Total R',        may_m["total_r"],       jun_m["total_r"]),
    ('AvgR/trade',     may_m["avg_r_trade"],   jun_m["avg_r_trade"]),
    ('Win rate %',     may_m["wr"],            jun_m["wr"]),
    ('PF',             may_m["pf"],            jun_m["pf"]),
]:
    if isinstance(mk, float):
        print(f'  {label:<20} {mk:>+10.3f} {jk:>+10.3f} {jk-mk:>+10.3f}')
    else:
        print(f'  {label:<20} {mk:>10} {jk:>10} {jk-mk:>+10}')

# ── 9. Signal-day vs no-signal-day feature distribution ──────────────────────

print('\n9. SIGNAL DAYS vs NO-SIGNAL DAYS — FEATURE AVERAGES')
print('-' * 60)
sig_d  = [r for r in db if r['n_signals'] > 0]
nosig_d= [r for r in db if r['n_signals'] == 0]
print(f'{"Feature":<26} {"Signal days":>12} {"No-signal":>12} {"Delta":>10}')
print('-' * 60)
for key, label in features:
    sv = _mean([r[key] for r in sig_d  if r[key] is not None])
    nv = _mean([r[key] for r in nosig_d if r[key] is not None])
    print(f'{label:<26} {sv:>12.3f} {nv:>12.3f} {sv-nv:>+10.3f}')

# ── 10. Best and worst single-dimension groups ────────────────────────────────

# Collect all groups with n_sigs >= 5 for reliability
all_groups = (
    [(f'REGIME:{k}', agg(v)) for k,v in regime_groups.items()] +
    [(f'ADX:{k}', agg(by_adx[k])) for k in adx_order if by_adx[k]] +
    [(f'RR:{k}', agg(by_rr[k])) for k in rr_order if by_rr[k]] +
    [(f'BREADTH:{k}', agg(by_br[k])) for k in br_order if by_br[k]] +
    [(f'ORB:{k}', agg(by_orb[k])) for k in orb_order if by_orb[k]] +
    [(f'GAP:{k}', agg(by_gap[k])) for k in gap_order if by_gap[k]]
)
reliable = [(lbl, m) for lbl, m in all_groups if m['n_sigs'] >= 5]
best  = max(reliable, key=lambda x: x[1]['avg_r_trade'])
worst = min(reliable, key=lambda x: x[1]['avg_r_trade'])

print('\n10. BEST AND WORST GROUPS (by AvgR/trade, n_sigs >= 5)')
print('-' * 70)
for tag, (lbl, m) in [('BEST', best), ('WORST', worst)]:
    print(f'  {tag}: {lbl}')
    print(f'       Days={m["n_days"]}  SigDays={m["n_sig_days"]}  NSigs={m["n_sigs"]}  '
          f'TotalR={m["total_r"]:+.2f}R  AvgR/T={m["avg_r_trade"]:+.3f}R  '
          f'WR={m["wr"]:.1f}%  PF={pf_s(m["pf"])}')

print()
spread = best[1]['avg_r_trade'] - worst[1]['avg_r_trade']
print(f'  AvgR/trade spread (best - worst): {spread:+.3f}R per trade')

# ── console summary (as requested) ───────────────────────────────────────────

print()
print('='*70)
print('PHASE 11B SUMMARY')
print('='*70)

print(f'\nBest regime group  : {best[0]}')
print(f'  AvgR/trade={best[1]["avg_r_trade"]:+.3f}R  WR={best[1]["wr"]:.1f}%  '
      f'PF={pf_s(best[1]["pf"])}  n_sigs={best[1]["n_sigs"]}')

print(f'\nWorst regime group : {worst[0]}')
print(f'  AvgR/trade={worst[1]["avg_r_trade"]:+.3f}R  WR={worst[1]["wr"]:.1f}%  '
      f'PF={pf_s(worst[1]["pf"])}  n_sigs={worst[1]["n_sigs"]}')

print(f'\nMay vs June differences:')
may_sig = agg(may_rows); jun_sig = agg(jun_rows)
print(f'  May: AvgR/trade={may_sig["avg_r_trade"]:+.3f}R  WR={may_sig["wr"]:.1f}%  '
      f'PF={pf_s(may_sig["pf"])}  avg ADX={_mean([r["spy_adx14"] for r in may_rows]):.1f}  '
      f'avg breadth={_mean([r["breadth_pct"] for r in may_rows if r["breadth_pct"] is not None]):.0f}%  '
      f'avg ORB={_mean([r["orb_avg_atr"] for r in may_rows if r["orb_avg_atr"] is not None]):.2f}ATR')
print(f'  Jun: AvgR/trade={jun_sig["avg_r_trade"]:+.3f}R  WR={jun_sig["wr"]:.1f}%  '
      f'PF={pf_s(jun_sig["pf"])}  avg ADX={_mean([r["spy_adx14"] for r in jun_rows]):.1f}  '
      f'avg breadth={_mean([r["breadth_pct"] for r in jun_rows if r["breadth_pct"] is not None]):.0f}%  '
      f'avg ORB={_mean([r["orb_avg_atr"] for r in jun_rows if r["orb_avg_atr"] is not None]):.2f}ATR')
# Key distinguishing features
may_adx = _mean([r['spy_adx14'] for r in may_rows])
jun_adx = _mean([r['spy_adx14'] for r in jun_rows])
may_rr  = _mean([r['spy_range_ratio'] for r in may_rows])
jun_rr  = _mean([r['spy_range_ratio'] for r in jun_rows])
print(f'  Key deltas: ADX {may_adx:.1f} -> {jun_adx:.1f} ({jun_adx-may_adx:+.1f})  '
      f'RangeRatio {may_rr:.3f} -> {jun_rr:.3f} ({jun_rr-may_rr:+.3f})')

print(f'\nRegime conditioning — does it appear promising?')
# Criteria: spread >= 0.3R/trade across reliable groups
if spread >= 0.30:
    verdict = f'YES — AvgR/trade spread of {spread:+.3f}R across dimension groups indicates material separation.'
elif spread >= 0.15:
    verdict = f'WEAK — AvgR/trade spread of {spread:+.3f}R is modest. More data needed.'
else:
    verdict = f'NO — AvgR/trade spread of {spread:+.3f}R is too small to be reliable.'
print(f'  {verdict}')
print('  (Raw data observation only — no filter recommended at this stage.)')
print()

# ── write MD ──────────────────────────────────────────────────────────────────

def md_agg_table(pairs, title, notes=''):
    lines = [f'### {title}\n']
    if notes: lines.append(f'*{notes}*\n')
    lines.append('| Group | Days | SigDays | Sig% | n | TotalR | AvgR/T | WR | PF | AvgADX | AvgRR | Breadth |')
    lines.append('|-------|-----:|--------:|-----:|--:|-------:|-------:|---:|---:|-------:|------:|--------:|')
    for lbl, m in pairs:
        lines.append(
            f'| {lbl} | {m["n_days"]} | {m["n_sig_days"]} | {m["sig_day_pct"]:.0f}% | '
            f'{m["n_sigs"]} | {m["total_r"]:+.2f}R | {m["avg_r_trade"]:+.3f}R | '
            f'{m["wr"]:.1f}% | {pf_s(m["pf"])} | {m["avg_adx"]:.1f} | '
            f'{m["avg_rr"]:.3f} | {m["avg_breadth"]:.0f}% |')
    return '\n'.join(lines)

with open(OUT_MD, 'w', encoding='utf-8') as f:
    f.write('# Phase 11B — Regime Analysis\n\n')
    f.write('> **Scope:** Analysis and reporting only. '
            'No filters. No strategy changes. No optimization. No recommendations.\n\n')
    f.write(f'**Source:** `{IN_CSV}` ({len(db)} trading days)  \n')
    f.write(f'**Windows:** IS (2025-09-17 → 2026-04-30) + OOS (2026-05-01 → 2026-06-25)  \n\n')
    f.write('---\n\n')

    # Regime
    f.write(md_agg_table(reg_pairs,
        'By Market Regime (BULL / BEAR / NEUTRAL)',
        'SPY and QQQ both: EMA9 vs EMA20 on daily bars.') + '\n\n')

    # Regime × window
    f.write('#### By Regime, split by IS / OOS\n\n')
    f.write('| Regime | Window | Days | SigDays | n | TotalR | AvgR/T | WR | PF |\n')
    f.write('|--------|--------|-----:|--------:|--:|-------:|-------:|---:|---:|\n')
    for reg in ('BULL','BEAR','NEUTRAL'):
        for win, wrows in [('IS', is_rows), ('OOS', oos_rows)]:
            sub = [r for r in wrows if r['market_regime'] == reg]
            if not sub: continue
            m = agg(sub)
            f.write(f'| {reg} | {win} | {m["n_days"]} | {m["n_sig_days"]} | '
                    f'{m["n_sigs"]} | {m["total_r"]:+.2f}R | {m["avg_r_trade"]:+.3f}R | '
                    f'{m["wr"]:.1f}% | {pf_s(m["pf"])} |\n')
    f.write('\n')

    # ADX
    f.write(md_agg_table(adx_pairs,
        'By SPY Daily ADX Band',
        'ADX computed on daily OHLC bars with 14-period Wilder smoothing.') + '\n\n')

    # Range ratio
    f.write(md_agg_table(rr_pairs,
        'By SPY Daily Range Ratio',
        'range_ratio = today_range / 20-day avg range. >1 = expansion, <1 = compression.') + '\n\n')

    # Breadth
    f.write(md_agg_table(br_pairs,
        'By Intraday Breadth at 10:00 ET',
        '% of BL_SYMS with close ≥ EMA20 at first bar of scan window (sm=30).') + '\n\n')

    # ORB
    f.write(md_agg_table(orb_pairs,
        'By Average ORB Range (ATR units)',
        'Mean (ORB_high − ORB_low) / ATR across all scan symbols with a valid ORB.') + '\n\n')

    # Gap
    f.write(md_agg_table(gap_pairs,
        'By SPY Overnight Gap Size',
        'gap_pct = (open − prev_close) / prev_close × 100.') + '\n\n')

    # Regime × ADX
    f.write('### Regime × ADX Cross-Tab (AvgR/trade | n signals)\n\n')
    f.write('| Regime | ADX <20 | ADX 20-35 | ADX ≥35 |\n|--------|---------|-----------|--------|\n')
    for reg in ('BULL','BEAR','NEUTRAL'):
        cells = []
        for band in adx_order:
            sub = [r for r in db if r['market_regime']==reg and adx_band(r['spy_adx14'])==band]
            if not sub: cells.append('—')
            else:
                m = agg(sub)
                cells.append(f'{m["avg_r_trade"]:+.3f}R (n={m["n_sigs"]})')
        f.write(f'| {reg} | {" | ".join(cells)} |\n')
    f.write('\n')

    # May vs June
    f.write('### May vs June 2026 — Feature Comparison\n\n')
    f.write('| Feature | May 2026 | Jun 2026 | Delta |\n|---------|--------:|---------:|------:|\n')
    for key, label in features:
        mv = _mean([r[key] for r in may_rows if r[key] is not None])
        jv = _mean([r[key] for r in jun_rows if r[key] is not None])
        f.write(f'| {label} | {mv:.3f} | {jv:.3f} | {jv-mv:+.3f} |\n')
    f.write('\n')
    f.write('| Outcome | May | Jun | Delta |\n|---------|----:|----:|------:|\n')
    for label, mk, jk in [
        ('Signal days', may_m["n_sig_days"], jun_m["n_sig_days"]),
        ('Total R', may_m["total_r"], jun_m["total_r"]),
        ('AvgR/trade', may_m["avg_r_trade"], jun_m["avg_r_trade"]),
        ('Win rate %', may_m["wr"], jun_m["wr"]),
        ('PF', may_m["pf"], jun_m["pf"]),
    ]:
        if isinstance(mk, float):
            f.write(f'| {label} | {mk:.3f} | {jk:.3f} | {jk-mk:+.3f} |\n')
        else:
            f.write(f'| {label} | {mk} | {jk} | {jk-mk:+d} |\n')
    f.write('\n')

    # Signal day vs no-signal day
    f.write('### Signal Days vs No-Signal Days — Feature Averages\n\n')
    f.write('| Feature | Signal days | No-signal days | Delta |\n')
    f.write('|---------|------------:|---------------:|------:|\n')
    for key, label in features:
        sv = _mean([r[key] for r in sig_d  if r[key] is not None])
        nv = _mean([r[key] for r in nosig_d if r[key] is not None])
        f.write(f'| {label} | {sv:.3f} | {nv:.3f} | {sv-nv:+.3f} |\n')
    f.write('\n---\n\n')

    # Summary
    f.write('## Phase 11B Summary\n\n')
    f.write(f'**Best group:** {best[0]}  \n')
    f.write(f'AvgR/trade = {best[1]["avg_r_trade"]:+.3f}R  WR = {best[1]["wr"]:.1f}%  '
            f'PF = {pf_s(best[1]["pf"])}  n = {best[1]["n_sigs"]} signals\n\n')
    f.write(f'**Worst group:** {worst[0]}  \n')
    f.write(f'AvgR/trade = {worst[1]["avg_r_trade"]:+.3f}R  WR = {worst[1]["wr"]:.1f}%  '
            f'PF = {pf_s(worst[1]["pf"])}  n = {worst[1]["n_sigs"]} signals\n\n')
    f.write(f'**AvgR/trade spread (best − worst):** {spread:+.3f}R\n\n')
    f.write(f'**Regime conditioning appears promising:** {verdict}\n\n')
    f.write('> No filters recommended. No parameters changed. '
            'Next step requires explicit user approval.\n')

print(f'MD  written: {OUT_MD}')
print()
print('Phase 11B complete.')
