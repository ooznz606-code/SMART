"""
market_brain_gate.py — Phase 13 Daily Decision Engine

UPGRADE from Phase 12A (binary ALLOW/BLOCK) to per-signal ranking + daily budget.

New public API:
  daily_session(...)          → DailyPlan      (call once at session start)
  rank_signal(sig, plan, n)   → SignalVerdict   (call per incoming signal)

Backward compat:
  evaluate(...)               → (str, str)     (ORB bridge unchanged)

Decision engine logic:
  BEAR  → PUT only, max 1–2 trades (no CALL blocked)
  BULL  → CALL preferred, PUT allowed on strong score, max 3
  RANGE → both directions, max 2 (or 3 if VIX high)
  TIGHT → any regime, max 1 trade
  OVEREXTENDED → max 1 trade, cautious scoring
"""

import csv
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# ── Verdicts & reasons ────────────────────────────────────────────────────────

VERDICT_ALLOW   = 'ALLOW_ORB'
VERDICT_BLOCK   = 'BLOCK_ORB'
VERDICT_APPROVE = 'APPROVED'
VERDICT_SKIP    = 'SKIP'

REASON_MISSING      = 'DATA_MISSING_SAFE_ALLOW'
REASON_BEAR         = 'BEAR_REGIME'
REASON_ORB_TIGHT    = 'ORB_TOO_TIGHT'
REASON_OVEREXTENDED = 'EXPANSION_OVEREXTENDED'
REASON_NORMAL       = 'NORMAL_ALLOW'

# ── Regime constants ──────────────────────────────────────────────────────────

REGIME_BULL  = 'BULL'
REGIME_BEAR  = 'BEAR'
REGIME_RANGE = 'RANGE'

# ── Thresholds ────────────────────────────────────────────────────────────────

_ORB_TIGHT_THRESHOLD     = 2.5    # orb_range_avg_atr < this → tight market
_RR_EXPAND_THRESHOLD     = 1.20   # spy_range_ratio >= this → overextended
_BREADTH_HIGH_THRESHOLD  = 60.0   # breadth_pct > this → overextended (combined)
_SIGNAL_APPROVE_MIN      = 60.0   # composite rank_score >= this → APPROVED
_QUALITY_MIN_ORB         = 45.0   # raw ORB score floor — skip if below regardless of bonuses
_QUALITY_MIN_BC          = 55.0   # raw BC rank_score floor
_VIX_HIGH                = 25.0
_VIX_NORMAL              = 18.0

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_PATH   = os.path.join('logs', 'brain_gate_decisions.csv')
LOG_PATH_S = os.path.join('logs', 'brain_gate_signals.csv')

_DAILY_HEADER = [
    'timestamp_utc', 'date',
    'market_regime', 'spy_range_ratio', 'orb_range_avg_atr', 'breadth_pct',
    'verdict', 'reason',
]
_SIGNAL_HEADER = [
    'timestamp_utc', 'date', 'sym', 'direction', 'source',
    'signal_score', 'rank_score', 'verdict', 'reason', 'priority',
]

_log_lock = threading.Lock()


def _write_log(row: dict, path: str, header: list) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    write_header = not os.path.exists(path)
    with _log_lock:
        with open(path, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=header)
            if write_header:
                w.writeheader()
            w.writerow(row)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DailyPlan:
    """Today's full trading plan — built once at session start."""
    date:          str
    regime:        str        # BULL | BEAR | RANGE
    allowed_dirs:  List[str]  # ['CALL','PUT'] | ['PUT'] | ['CALL'] | []
    max_trades:    int        # daily trade budget
    confidence:    float      # 0–100 plan confidence
    bias_score:    float      # -100 (bearish) → +100 (bullish)
    vix_regime:    str        # HIGH | NORMAL | LOW
    notes:         List[str]

    def allows(self, direction: str) -> bool:
        return direction in self.allowed_dirs

    def has_budget(self, used: int) -> bool:
        return used < self.max_trades

    def summary(self) -> str:
        return (f"[{self.regime}] dirs={self.allowed_dirs} "
                f"max={self.max_trades} conf={self.confidence:.0f} "
                f"bias={self.bias_score:+.0f} VIX={self.vix_regime}")


@dataclass
class SignalVerdict:
    """Per-signal decision from rank_signal()."""
    verdict:    str    # APPROVED | SKIP
    rank_score: float  # 0–100 composite
    reason:     str
    priority:   int    # 1 = highest (execute first)


# ── Daily session builder ─────────────────────────────────────────────────────

def daily_session(
    market_regime:     Optional[str]   = None,
    spy_range_ratio:   Optional[float] = None,
    orb_range_avg_atr: Optional[float] = None,
    breadth_pct:       Optional[float] = None,
    vix_level:         Optional[float] = None,
    spy_gap_pct:       Optional[float] = None,
    date:              str             = '',
) -> DailyPlan:
    """
    Build today's DailyPlan from market conditions.
    Call once per day before session start.

    Parameters
    ----------
    market_regime      : 'BULL' | 'BEAR' | 'RANGE'
    spy_range_ratio    : today's SPY range / 20d avg range
    orb_range_avg_atr  : ORB range in ATR units
    breadth_pct        : % advancing issues
    vix_level          : VIX closing value
    spy_gap_pct        : SPY overnight gap as fraction (e.g. 0.005 = +0.5%)
    date               : 'YYYY-MM-DD'
    """
    date  = date or datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')
    notes: List[str] = []

    # ── VIX regime ────────────────────────────────────────────────────────────
    if vix_level is None:
        vix_regime = 'NORMAL'
        notes.append('VIX unknown → NORMAL assumed')
    elif vix_level >= _VIX_HIGH:
        vix_regime = 'HIGH'
        notes.append(f'VIX={vix_level:.1f} HIGH → bigger moves, adjust budget')
    elif vix_level >= _VIX_NORMAL:
        vix_regime = 'NORMAL'
        notes.append(f'VIX={vix_level:.1f} NORMAL')
    else:
        vix_regime = 'LOW'
        notes.append(f'VIX={vix_level:.1f} LOW → smaller moves expected')

    # ── Missing regime → safe default ────────────────────────────────────────
    if market_regime is None:
        notes.append('regime missing → safe RANGE default, max 2')
        return DailyPlan(
            date=date, regime=REGIME_RANGE,
            allowed_dirs=['CALL', 'PUT'], max_trades=2,
            confidence=30.0, bias_score=0.0,
            vix_regime=vix_regime, notes=notes,
        )

    bias = _compute_bias(market_regime, spy_gap_pct)

    # ── BEAR regime: PUT only ─────────────────────────────────────────────────
    if market_regime == REGIME_BEAR:
        notes.append('BEAR → PUT direction only')
        if spy_gap_pct is not None and spy_gap_pct < -0.005:
            notes.append(f'gap-down {spy_gap_pct*100:.2f}% confirms BEAR')
            conf   = 85.0
            max_t  = 2
        elif vix_regime == 'HIGH':
            notes.append('VIX HIGH in BEAR → bigger put moves likely')
            conf   = 80.0
            max_t  = 2
        else:
            conf   = 65.0
            max_t  = 1
        return DailyPlan(
            date=date, regime=REGIME_BEAR,
            allowed_dirs=['PUT'], max_trades=max_t,
            confidence=conf, bias_score=bias,
            vix_regime=vix_regime, notes=notes,
        )

    # ── ORB too tight: low opportunity ───────────────────────────────────────
    if orb_range_avg_atr is not None and orb_range_avg_atr < _ORB_TIGHT_THRESHOLD:
        notes.append(f'ORB tight ({orb_range_avg_atr:.2f} ATR) → max 1 trade')
        return DailyPlan(
            date=date, regime=market_regime,
            allowed_dirs=_dirs(market_regime), max_trades=1,
            confidence=45.0, bias_score=bias,
            vix_regime=vix_regime, notes=notes,
        )

    # ── Overextended: caution ─────────────────────────────────────────────────
    if (spy_range_ratio is not None and breadth_pct is not None
            and spy_range_ratio >= _RR_EXPAND_THRESHOLD
            and breadth_pct > _BREADTH_HIGH_THRESHOLD):
        notes.append(
            f'overextended (rr={spy_range_ratio:.2f} breadth={breadth_pct:.0f}%) → max 1')
        return DailyPlan(
            date=date, regime=market_regime,
            allowed_dirs=_dirs(market_regime), max_trades=1,
            confidence=50.0, bias_score=bias * 0.5,
            vix_regime=vix_regime, notes=notes,
        )

    # ── BULL regime ───────────────────────────────────────────────────────────
    if market_regime == REGIME_BULL:
        notes.append('BULL → CALL preferred, PUT on strong signal')
        if spy_gap_pct is not None and spy_gap_pct > 0.003:
            notes.append(f'gap-up {spy_gap_pct*100:.2f}% confirms BULL')
            conf  = 88.0
            max_t = 3
        elif vix_regime == 'HIGH':
            conf  = 82.0
            max_t = 3
        else:
            conf  = 78.0
            max_t = 3
        return DailyPlan(
            date=date, regime=REGIME_BULL,
            allowed_dirs=['CALL', 'PUT'], max_trades=max_t,
            confidence=conf, bias_score=bias,
            vix_regime=vix_regime, notes=notes,
        )

    # ── RANGE regime ──────────────────────────────────────────────────────────
    notes.append('RANGE → both directions, strict quality filter')
    max_t = 3 if vix_regime == 'HIGH' else 2
    if vix_regime == 'HIGH':
        notes.append('VIX HIGH in RANGE → wider swings, +1 trade budget')
    return DailyPlan(
        date=date, regime=REGIME_RANGE,
        allowed_dirs=['CALL', 'PUT'], max_trades=max_t,
        confidence=62.0, bias_score=bias,
        vix_regime=vix_regime, notes=notes,
    )


def _dirs(regime: str) -> List[str]:
    if regime == REGIME_BEAR:
        return ['PUT']
    return ['CALL', 'PUT']


def _compute_bias(regime: str, gap_pct: Optional[float]) -> float:
    base = {REGIME_BULL: 65.0, REGIME_BEAR: -65.0, REGIME_RANGE: 0.0}.get(regime, 0.0)
    if gap_pct is not None:
        base = max(-100.0, min(100.0, base + gap_pct * 1000))
    return base


# ── Signal ranking ────────────────────────────────────────────────────────────

def rank_signal(
    signal:            Dict,
    plan:              DailyPlan,
    trades_used_today: int = 0,
) -> SignalVerdict:
    """
    Score and approve/skip a single incoming signal.

    signal dict keys
    ----------------
    sym        str    symbol  e.g. 'AAPL'
    direction  str    'CALL' | 'PUT'
    score      float  signal-native quality score (ORB 0-100, BC rank_score 0-100)
    source     str    'ORB' | 'BC'
    sess_min   int    session minute from 9:00 ET open (e.g. 75 = 10:15 ET)

    Scoring (100 pts total)
    -----------------------
    Signal quality   : 0–40 pts  (native score scaled)
    Regime alignment : 0–30 pts  (how well direction matches bias)
    Time of day      : 0–15 pts  (10:00–10:30 ET = prime)
    Source weight    : 0–15 pts  (ORB proven 71% WR > BC 50%)
    VIX modifier     : ×1.10 HIGH | ×0.90 LOW
    """
    sym       = signal.get('sym', '?')
    direction = signal.get('direction', '')
    score     = float(signal.get('score', 0))
    source    = signal.get('source', 'ORB')
    sess_min  = int(signal.get('sess_min', 300))

    # ── Budget ────────────────────────────────────────────────────────────────
    if not plan.has_budget(trades_used_today):
        return _emit_signal(
            VERDICT_SKIP, 0.0,
            f'budget full ({trades_used_today}/{plan.max_trades})',
            99, signal, plan,
        )

    # ── Direction allowed ─────────────────────────────────────────────────────
    if not plan.allows(direction):
        return _emit_signal(
            VERDICT_SKIP, 0.0,
            f'{direction} blocked in {plan.regime} regime',
            99, signal, plan,
        )

    # ── Raw quality floor — bonuses cannot rescue a weak signal ───────────────
    q_min = _QUALITY_MIN_BC if source == 'BC' else _QUALITY_MIN_ORB
    if score < q_min:
        return _emit_signal(
            VERDICT_SKIP, round(score, 1),
            f'raw score={score:.1f} < floor={q_min} ({source})',
            99, signal, plan,
        )

    # ── Composite score ───────────────────────────────────────────────────────

    # 1. Signal quality (0–40)
    sig_pts = min(score / 100.0 * 40.0, 40.0)

    # 2. Regime alignment (0–30)
    align_pts = _alignment_pts(direction, plan)

    # 3. Time of day (0–15)
    time_pts = _time_pts(sess_min)

    # 4. Source reliability (0–15)
    src_pts = 15.0 if source == 'ORB' else 8.0

    rank = sig_pts + align_pts + time_pts + src_pts

    # 5. VIX modifier
    if plan.vix_regime == 'HIGH':
        rank = min(rank * 1.10, 100.0)
    elif plan.vix_regime == 'LOW':
        rank *= 0.90

    rank = round(min(rank, 100.0), 1)

    # ── Decision ──────────────────────────────────────────────────────────────
    if rank >= _SIGNAL_APPROVE_MIN:
        priority = _priority(rank)
        reason   = (f'rank={rank:.1f} '
                    f'[qual={sig_pts:.0f} align={align_pts:.0f} '
                    f'time={time_pts:.0f} src={src_pts:.0f}]')
        return _emit_signal(VERDICT_APPROVE, rank, reason, priority, signal, plan)
    else:
        reason = f'rank={rank:.1f} < min={_SIGNAL_APPROVE_MIN}'
        return _emit_signal(VERDICT_SKIP, rank, reason, 99, signal, plan)


def _alignment_pts(direction: str, plan: DailyPlan) -> float:
    """How well does direction align with today's bias? 0–30 pts."""
    bias = plan.bias_score  # -100 to +100
    if direction == 'CALL':
        return max(0.0, min(30.0, (bias + 100.0) / 200.0 * 30.0))
    else:
        return max(0.0, min(30.0, (-bias + 100.0) / 200.0 * 30.0))


def _time_pts(sess_min: int) -> float:
    """
    Optimal window 10:00–10:30 ET = sess_min 60–90.
    Decay after, penalty after 11:30.
    """
    if sess_min < 30:    return 5.0   # pre-10am: too early
    if sess_min < 60:    return 9.0   # 9:30–10:00: ORB forming
    if sess_min <= 90:   return 15.0  # 10:00–10:30: prime window
    if sess_min <= 120:  return 11.0  # 10:30–11:00: good
    if sess_min <= 150:  return 7.0   # 11:00–11:30: declining
    return 3.0                        # after 11:30: low priority


def _priority(rank: float) -> int:
    if rank >= 85: return 1
    if rank >= 70: return 2
    if rank >= 55: return 3
    return 4


def _emit_signal(verdict, rank_score, reason, priority, signal, plan) -> SignalVerdict:
    now_utc = datetime.now(tz=timezone.utc)
    row = {
        'timestamp_utc': now_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'date':          plan.date,
        'sym':           signal.get('sym', ''),
        'direction':     signal.get('direction', ''),
        'source':        signal.get('source', ''),
        'signal_score':  f"{signal.get('score', 0):.1f}",
        'rank_score':    f'{rank_score:.1f}',
        'verdict':       verdict,
        'reason':        reason,
        'priority':      str(priority),
    }
    _write_log(row, LOG_PATH_S, _SIGNAL_HEADER)
    return SignalVerdict(
        verdict=verdict, rank_score=rank_score,
        reason=reason, priority=priority,
    )


# ── Backward-compatible evaluate() ───────────────────────────────────────────

def evaluate(
    market_regime:      Optional[str],
    spy_range_ratio:    Optional[float],
    orb_range_avg_atr:  Optional[float],
    breadth_pct:        Optional[float],
    date:               str = '',
) -> Tuple[str, str]:
    """
    Legacy API — unchanged for ORB bridge compatibility.
    Internally builds a DailyPlan; maps result to old ALLOW/BLOCK verdicts.
    """
    plan = daily_session(
        market_regime=market_regime,
        spy_range_ratio=spy_range_ratio,
        orb_range_avg_atr=orb_range_avg_atr,
        breadth_pct=breadth_pct,
        date=date,
    )

    # Map plan → legacy verdict
    if any(v is None for v in (market_regime, spy_range_ratio,
                                orb_range_avg_atr, breadth_pct)):
        verdict, reason = VERDICT_ALLOW, REASON_MISSING
    elif plan.regime == REGIME_BEAR:
        # BEAR now allows PUT trades — don't block ORB entirely
        # ORB bridge will naturally generate PUT on bearish breaks
        verdict, reason = VERDICT_ALLOW, REASON_BEAR + '_PUT_ONLY'
    elif plan.max_trades == 1 and orb_range_avg_atr is not None and orb_range_avg_atr < _ORB_TIGHT_THRESHOLD:
        verdict, reason = VERDICT_ALLOW, REASON_ORB_TIGHT
    elif plan.max_trades == 1 and (spy_range_ratio or 0) >= _RR_EXPAND_THRESHOLD:
        verdict, reason = VERDICT_BLOCK, REASON_OVEREXTENDED
    else:
        verdict, reason = VERDICT_ALLOW, REASON_NORMAL

    _emit_legacy(verdict, reason, market_regime, spy_range_ratio,
                 orb_range_avg_atr, breadth_pct, date)
    return verdict, reason


def _emit_legacy(verdict, reason, market_regime, spy_range_ratio,
                 orb_range_avg_atr, breadth_pct, date) -> None:
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
    _write_log(row, LOG_PATH, _DAILY_HEADER)
