"""Phase 12A — Data Extension Only.

Fetches the latest 15m bars for all research symbols via TradingView,
merges them with existing chart_data_research files, and reports
what was added. No analysis. No Phase 11D re-run.

Rules:
  - Never shorten existing history.
  - Deduplicate by UTC timestamp (string match).
  - Write back only if at least one new bar was added.
  - Abort a symbol and keep its existing file if the TV fetch fails.
"""
import os, json, sys, time
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from tv_datafeed import TVDataFeed, TV_SYMBOLS

RDIR   = 'chart_data_research'
FETCH_BARS = 700          # covers ~4 weeks at 15m; enough to catch July
INTERVAL   = '15'
DELAY_S    = 3.0          # polite pause between symbol fetches

# all symbols present in the research directory
RESEARCH_SYMS = [
    'AAPL', 'AMD', 'AMZN', 'AVGO', 'COST', 'CRM',
    'GOOGL', 'LLY', 'META', 'MSFT', 'NFLX', 'NVDA',
    'PANW', 'QQQ', 'SPY', 'TSLA', 'UBER',
]

# ── helpers ───────────────────────────────────────────────────────────────────

def ts_label(iso: str) -> str:
    """Return YYYY-MM-DD from ISO timestamp string."""
    return iso[:10]

def load_existing(sym: str) -> dict | None:
    path = os.path.join(RDIR, f'{sym}_15m.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def save(sym: str, data: dict) -> None:
    path = os.path.join(RDIR, f'{sym}_15m.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))

def merge(existing: dict, fetched: dict) -> tuple[dict, int]:
    """
    Merge fetched bars into existing. Returns (merged_data, n_new_bars).
    Keyed by UTC ISO timestamp. Existing values take precedence for any
    timestamp already present (avoids overwriting settled bars with live
    partial data), except for the very last existing bar which may be
    a partial candle — we allow that to update.
    """
    # Build index: timestamp -> (o, h, l, c, v)
    ex_times = existing['times']
    last_existing_ts = ex_times[-1] if ex_times else ''

    idx: dict[str, tuple] = {}
    for i, ts in enumerate(ex_times):
        idx[ts] = (
            existing['opens'][i],
            existing['highs'][i],
            existing['lows'][i],
            existing['closes'][i],
            existing['volumes'][i],
        )

    # Add fetched bars; allow overwrite only for the last bar of existing
    # (in case it was a partial candle when originally fetched)
    n_new = 0
    new_ts = []
    for i, ts in enumerate(fetched['times']):
        ohlcv = (
            fetched['opens'][i],
            fetched['highs'][i],
            fetched['lows'][i],
            fetched['closes'][i],
            fetched['volumes'][i],
        )
        if ts not in idx:
            idx[ts] = ohlcv
            n_new += 1
            new_ts.append(ts)
        elif ts == last_existing_ts:
            # update potentially partial last bar
            idx[ts] = ohlcv

    # Sort chronologically
    all_ts = sorted(idx.keys())
    merged = {
        'symbol':   existing.get('symbol', existing.get('sym', '')),
        'tf':       existing.get('tf', '15m'),
        'saved_at': datetime.now(tz=timezone.utc).isoformat(),
        'opens':    [idx[ts][0] for ts in all_ts],
        'highs':    [idx[ts][1] for ts in all_ts],
        'lows':     [idx[ts][2] for ts in all_ts],
        'closes':   [idx[ts][3] for ts in all_ts],
        'volumes':  [idx[ts][4] for ts in all_ts],
        'times':    all_ts,
    }
    return merged, n_new

# ── main ──────────────────────────────────────────────────────────────────────

print('\nPhase 12A — Data Extension')
print('='*65)
print(f'Target dir : {RDIR}')
print(f'Symbols    : {len(RESEARCH_SYMS)}')
print(f'Fetch bars : {FETCH_BARS} per symbol (15m)')
print()

feed = TVDataFeed()

summary = []  # (sym, first, last, total, new_bars, status)

for sym in RESEARCH_SYMS:
    existing = load_existing(sym)
    if existing is None:
        print(f'  {sym:<8}  SKIP — file not found in {RDIR}')
        summary.append((sym, '—', '—', 0, 0, 'MISSING'))
        continue

    ex_n     = len(existing['times'])
    ex_first = ts_label(existing['times'][0])
    ex_last  = ts_label(existing['times'][-1])

    print(f'  {sym:<8}  existing: {ex_n} bars  {ex_first} → {ex_last}  ', end='', flush=True)

    fetched = feed.get_bars(sym, interval=INTERVAL, bars=FETCH_BARS)
    if fetched is None or not fetched.get('times'):
        print('FETCH FAILED — keeping existing file unchanged')
        summary.append((sym, ex_first, ex_last, ex_n, 0, 'FETCH_FAILED'))
        time.sleep(DELAY_S)
        continue

    merged, n_new = merge(existing, fetched)
    new_n     = len(merged['times'])
    new_first = ts_label(merged['times'][0])
    new_last  = ts_label(merged['times'][-1])

    if n_new > 0:
        save(sym, merged)
        status = 'UPDATED'
    else:
        status = 'NO_NEW'

    print(f'→ {new_first} → {new_last}  total={new_n}  new={n_new}  [{status}]')
    summary.append((sym, new_first, new_last, new_n, n_new, status))
    time.sleep(DELAY_S)

# ── verification table ────────────────────────────────────────────────────────

print()
print('='*65)
print('VERIFICATION')
print('='*65)
print(f'{"Symbol":<8} {"First date":>12} {"Last date":>12} {"Total bars":>11} {"New bars":>9} {"Status"}')
print('-'*65)
for sym, first, last, total, new, status in summary:
    print(f'{sym:<8} {first:>12} {last:>12} {total:>11,} {new:>9,}  {status}')

# newest last date across all symbols
last_dates = [last for _, _, last, _, _, status in summary
              if status in ('UPDATED', 'NO_NEW') and last != '—']
if last_dates:
    newest = max(last_dates)
    oldest_last = min(last_dates)
    if oldest_last < newest:
        print(f'\n  WARNING: symbol coverage not uniform.')
        print(f'  Newest last date : {newest}')
        print(f'  Oldest last date : {oldest_last}')
        lag = [sym for sym, _, last, _, _, _ in summary if last == oldest_last]
        print(f'  Lagging          : {", ".join(lag)}')
    else:
        print(f'\n  All symbols reach: {newest}')

n_updated = sum(1 for *_, status in summary if status == 'UPDATED')
n_no_new  = sum(1 for *_, status in summary if status == 'NO_NEW')
n_failed  = sum(1 for *_, status in summary if status == 'FETCH_FAILED')
total_new = sum(new for *_, new, _ in [(*x[:4], x[4], x[5]) for x in summary])

print()
print(f'  Symbols updated  : {n_updated}')
print(f'  No new data      : {n_no_new}')
print(f'  Fetch failures   : {n_failed}')
print(f'  Total bars added : {total_new}')
print()
print('Phase 12A complete. No analysis run.')
