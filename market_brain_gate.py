"""
market_brain_gate.py — Phase 12A Production Brain Gate

Read-only pre-execution safety layer for ORB signals.
Evaluates daily market conditions and returns ALLOW_ORB or BLOCK_ORB.

CRITICAL constraints:
  - Does NOT modify ORB logic, parameters, execution, symbols, or strategy.
  - Does NOT alter signal generation or execution paths.
  - Logs every decision to logs/brain_gate_decisions.csv.

Evaluation order (first match wins):
  1. Any required input is None  → ALLOW_ORB  / DATA_MISSING_SAFE_ALLOW
  2. market_regime == 'BEAR'     → BLOCK_ORB  / BEAR_REGIME
  3. orb_range_avg_atr < 2.5    → BLOCK_ORB  / ORB_TOO_TIGHT
  4. spy_range_ratio >= 1.20
     AND breadth_pct > 60        → BLOCK_ORB  / EXPANSION_OVEREXTENDED
  5. Otherwise                   → ALLOW_ORB  / NORMAL_ALLOW
"""

import csv
import os
import threading
from datetime import datetime, timezone
from typing import Optional

VERDICT_ALLOW = 'ALLOW_ORB'
VERDICT_BLOCK = 'BLOCK_ORB'

REASON_MISSING      = 'DATA_MISSING_SAFE_ALLOW'
REASON_BEAR         = 'BEAR_REGIME'
REASON_ORB_TIGHT    = 'ORB_TOO_TIGHT'
REASON_OVEREXTENDED = 'EXPANSION_OVEREXTENDED'
REASON_NORMAL       = 'NORMAL_ALLOW'

LOG_PATH = os.path.join('logs', 'brain_gate_decisions.csv')
LOG_HEADER = [
    'timestamp_utc', 'date',
    'market_regime', 'spy_range_ratio', 'orb_range_avg_atr', 'breadth_pct',
    'verdict', 'reason',
]

# Rule thresholds
_ORB_TIGHT_THRESHOLD   = 2.5    # Rule 3: orb_range_avg_atr < this → BLOCK
_RR_EXPAND_THRESHOLD   = 1.20   # Rule 4: spy_range_ratio >= this (combined)
_BREADTH_HIGH_THRESHOLD = 60.0  # Rule 4: breadth_pct > this (combined)

_log_lock = threading.Lock()


# ── public API ────────────────────────────────────────────────────────────────

def evaluate(
    market_regime:      Optional[str],
    spy_range_ratio:    Optional[float],
    orb_range_avg_atr:  Optional[float],
    breadth_pct:        Optional[float],
    date: str = '',
) -> tuple:
    """
    Evaluate market conditions for ORB execution.

    Returns (verdict, reason).
    Rules are evaluated in order; first match wins.
    """
    # Rule 1: any required input missing → safe allow
    if any(v is None for v in (market_regime, spy_range_ratio,
                                orb_range_avg_atr, breadth_pct)):
        return _emit(VERDICT_ALLOW, REASON_MISSING,
                     market_regime, spy_range_ratio, orb_range_avg_atr, breadth_pct, date)

    # Rule 2: BEAR regime
    if market_regime == 'BEAR':
        return _emit(VERDICT_BLOCK, REASON_BEAR,
                     market_regime, spy_range_ratio, orb_range_avg_atr, breadth_pct, date)

    # Rule 3: ORB too tight
    if orb_range_avg_atr < _ORB_TIGHT_THRESHOLD:
        return _emit(VERDICT_BLOCK, REASON_ORB_TIGHT,
                     market_regime, spy_range_ratio, orb_range_avg_atr, breadth_pct, date)

    # Rule 4: market expansion overextended
    if spy_range_ratio >= _RR_EXPAND_THRESHOLD and breadth_pct > _BREADTH_HIGH_THRESHOLD:
        return _emit(VERDICT_BLOCK, REASON_OVEREXTENDED,
                     market_regime, spy_range_ratio, orb_range_avg_atr, breadth_pct, date)

    # Rule 5: normal allow
    return _emit(VERDICT_ALLOW, REASON_NORMAL,
                 market_regime, spy_range_ratio, orb_range_avg_atr, breadth_pct, date)


# ── internals ─────────────────────────────────────────────────────────────────

def _emit(verdict, reason, market_regime, spy_range_ratio,
          orb_range_avg_atr, breadth_pct, date) -> tuple:
    now_utc = datetime.now(tz=timezone.utc)
    row = {
        'timestamp_utc':     now_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'date':              date or now_utc.strftime('%Y-%m-%d'),
        'market_regime':     market_regime if market_regime is not None else '',
        'spy_range_ratio':   f'{spy_range_ratio:.4f}' if spy_range_ratio is not None else '',
        'orb_range_avg_atr': f'{orb_range_avg_atr:.4f}' if orb_range_avg_atr is not None else '',
        'breadth_pct':       f'{breadth_pct:.2f}' if breadth_pct is not None else '',
        'verdict':           verdict,
        'reason':            reason,
    }
    _write_log(row)
    return verdict, reason


def _write_log(row: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH) or '.', exist_ok=True)
    write_header = not os.path.exists(LOG_PATH)
    with _log_lock:
        with open(LOG_PATH, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=LOG_HEADER)
            if write_header:
                w.writeheader()
            w.writerow(row)
