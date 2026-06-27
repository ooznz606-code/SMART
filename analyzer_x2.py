# -*- coding: utf-8 -*-
"""
analyzer_x2.py — SMART ICT Institutional Engine X2.1 ELITE
محلل ICT نظيف للبوت: Liquidity Sweep → Displacement → Fresh Zone → Retest → Decision Score

الهدف العملي:
- تقليل الإشارات الضعيفة جداً.
- منع التحول إلى مولد إشارات.
- المحافظة على توافق الواجهة والجسر والتنفيذ الحالي.

المدخلات المتوافقة:
    analyze(symbol, market, candles_1h, candles_15m, option_data=None, candles_5m=None)

المخرجات المتوافقة:
    TradeSignal أو RejectedSignal
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
import json
import logging
import math

_ICT_LOG = logging.getLogger("smart_ict")


# ═════════════════════════════════════════════════════════════════════
# Public Types — same interface expected by trading_app / bridge
# ═════════════════════════════════════════════════════════════════════

class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class SignalGrade(Enum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    REJECTED = "REJECTED"


class TradeStatus(Enum):
    OPEN = "OPEN"
    TARGET_1_HIT = "TARGET_1_HIT"
    TARGET_2_HIT = "TARGET_2_HIT"
    STOPPED_OUT = "STOPPED_OUT"
    TIME_EXPIRED = "TIME_EXPIRED"


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime


@dataclass
class MarketState:
    vix: float
    adx: float
    volume: float
    avg_volume_20: float
    ema_50: float
    ema_200: float
    price: float
    atr_14: float
    news_risk: Literal["LOW", "MEDIUM", "HIGH"]


@dataclass
class OptionData:
    delta: float
    gamma: float
    theta: float
    vega: float
    iv_current: float
    iv_percentile: float
    bid: float
    ask: float
    last_price: float
    open_interest: int
    volume: int
    days_to_expiry: int
    strike: float
    option_type: Literal["CALL", "PUT"]


@dataclass
class RejectionReason:
    step: str
    reason: str
    detail: str
    severity: Literal["CRITICAL", "WARNING", "INFO"]


@dataclass
class ICTEvent:
    kind: str
    price: float
    index: int
    quality: float
    detail: str = ""


@dataclass
class Zone:
    top: float
    bottom: float
    mid: float
    zone_type: str = "ICT_ZONE"
    quality: float = 0.0
    tested_count: int = 0
    width: float = 0.0
    created_index: int = 0
    freshness: float = 0.0


@dataclass
class Retest:
    price: float
    candle: Candle
    body_atr: float
    touched_zone: bool


@dataclass
class ConfidenceBreakdown:
    trend: float
    zone: float
    volume: float
    stop_quality: float
    risk_reward: float
    option_quality: float
    backtest: float
    total: float


@dataclass
class TradeSignal:
    signal_id: str
    timestamp: datetime
    symbol: str
    direction: Direction
    entry_price: float
    stop_loss: float
    current_stop: float
    target_1: float
    target_2: float
    target_3: float
    risk_amount: float
    risk_reward: str
    htf_bias: Direction
    liquidity_sweep: Any
    displacement: Any
    zone: Zone
    retest: Retest
    option_data: Any
    option_verdict: str
    confidence_score: float
    confidence_breakdown: ConfidenceBreakdown
    grade: SignalGrade
    reasons_for_entry: List[str]
    status: TradeStatus = TradeStatus.OPEN
    closed_qty_pct: float = 0.0
    realized_r: float = 0.0

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction.value,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "current_stop": self.current_stop,
            "target_1": self.target_1,
            "target_2": self.target_2,
            "target_3": self.target_3,
            "risk_reward": self.risk_reward,
            "confidence_score": self.confidence_score,
            "grade": self.grade.value,
            "status": self.status.value,
            "option_verdict": self.option_verdict,
            "reasons_for_entry": self.reasons_for_entry,
        }


@dataclass
class RejectedSignal:
    timestamp: datetime
    symbol: str
    direction: Direction
    rejection_reasons: List[RejectionReason]
    partial_analysis: Dict[str, Any]
    grade: SignalGrade = SignalGrade.REJECTED

    def to_report(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction.value,
            "verdict": "REJECTED",
            "grade": self.grade.value,
            "rejection_reasons": [r.__dict__ for r in self.rejection_reasons],
            "partial_analysis": self.partial_analysis,
        }


AnalyzerResult = Union[TradeSignal, RejectedSignal]


# ═════════════════════════════════════════════════════════════════════
# Symbol Profiles — conservative but not dead
# ═════════════════════════════════════════════════════════════════════

_DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    # X2.3 DAILY SELECTIVE — رموز البوت الثمانية فقط.
    # الهدف: صفقات يومية مع جودة جيدة، وليس خنق النظام إلى صفر صفقات.
    # min_conf ليس نسبة نجاح؛ هو حد جودة القرار.
    "QQQ":  {"enabled": True, "timeframe": "1H",  "min_conf": 72, "min_adx": 17, "max_adx": 65,  "max_daily_signals": 1},
    "NVDA": {"enabled": True, "timeframe": "15m", "min_conf": 74, "min_adx": 19, "max_adx": 68,  "max_daily_signals": 1},
    "MSFT": {"enabled": True, "timeframe": "1H",  "min_conf": 72, "min_adx": 17, "max_adx": 65,  "max_daily_signals": 1},
    "META": {"enabled": True, "timeframe": "1H",  "min_conf": 73, "min_adx": 18, "max_adx": 66,  "max_daily_signals": 1},
    "AMZN": {"enabled": True, "timeframe": "1H",  "min_conf": 72, "min_adx": 17, "max_adx": 65,  "max_daily_signals": 1},
    "NFLX": {"enabled": True, "timeframe": "1H",  "min_conf": 74, "min_adx": 19, "max_adx": 67,  "max_daily_signals": 1},
    "GOOGL":{"enabled": True, "timeframe": "1H",  "min_conf": 72, "min_adx": 17, "max_adx": 65,  "max_daily_signals": 1},
    "SQQQ": {"enabled": True, "timeframe": "15m", "min_conf": 71, "min_adx": 16, "max_adx": 70,  "max_daily_signals": 1},
    "__DEFAULT__": {"enabled": False, "timeframe": "1H", "min_conf": 999, "min_adx": 99, "max_adx": 999, "max_daily_signals": 0},
}
def _load_profiles() -> Dict[str, Dict[str, Any]]:
    path = Path(__file__).resolve().parent / "symbol_profiles_x2.json"
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict) and data:
                merged = dict(_DEFAULT_PROFILES)
                for k, v in data.items():
                    if isinstance(v, dict):
                        base = dict(merged.get(k, {}))
                        base.update(v)
                        merged[k] = base
                return merged
        except Exception:
            pass
    return _DEFAULT_PROFILES


SYMBOL_PROFILES = _load_profiles()


# ═════════════════════════════════════════════════════════════════════
# Math helpers
# ═════════════════════════════════════════════════════════════════════

def _safe(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except Exception:
        return default


def _ema(values: List[float], period: int) -> float:
    vals = [_safe(v) for v in values if v is not None]
    if not vals:
        return 0.0
    if len(vals) < period:
        period = max(1, len(vals))
    k = 2.0 / (period + 1.0)
    e = sum(vals[:period]) / period
    for v in vals[period:]:
        e = v * k + e * (1 - k)
    return e


def _atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return max(candles[-1].high - candles[-1].low, 0.01) if candles else 1.0
    trs: List[float] = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    used = trs[-period:] if len(trs) >= period else trs
    return max(sum(used) / max(1, len(used)), 0.001)


def _adx_calc(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < period * 2:
        return 0.0
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(candles)):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        ))
    dxs = []
    for i in range(period, len(tr) + 1):
        tr_sum = sum(tr[i - period:i])
        if tr_sum <= 0:
            continue
        pdi = 100.0 * sum(plus_dm[i - period:i]) / tr_sum
        mdi = 100.0 * sum(minus_dm[i - period:i]) / tr_sum
        den = pdi + mdi
        if den > 0:
            dxs.append(100.0 * abs(pdi - mdi) / den)
    if not dxs:
        return 0.0
    return sum(dxs[-period:]) / min(period, len(dxs))


def _volume_ratio(candles: List[Candle], period: int = 20) -> float:
    if len(candles) < period + 1:
        return 1.0
    vols = [max(0.0, _safe(c.volume)) for c in candles[-period-1:-1]]
    avg = sum(vols) / max(1, len(vols))
    return candles[-1].volume / avg if avg > 0 else 1.0


def _direction_from_htf(candles_1h: List[Candle], market: MarketState) -> Tuple[Direction, float, str]:
    """HTF alignment: EMA + structure. Returns direction, score, detail."""
    if candles_1h and len(candles_1h) >= 55:
        closes = [c.close for c in candles_1h]
        e21 = _ema(closes, 21)
        e50 = _ema(closes, 50)
        e200 = _ema(closes, 200) if len(closes) >= 200 else _ema(closes, len(closes))
        price = closes[-1]
        recent = candles_1h[-20:]
        highs = [c.high for c in recent]
        lows = [c.low for c in recent]
        slope = e21 - _ema(closes[:-3], 21) if len(closes) > 60 else e21 - e50
        hh = highs[-1] >= max(highs[:-1]) if len(highs) > 1 else False
        ll = lows[-1] <= min(lows[:-1]) if len(lows) > 1 else False
        if price > e21 > e50 and e50 >= e200 * 0.998 and slope > 0:
            score = 30.0 + (5.0 if hh else 0.0)
            return Direction.LONG, score, f"HTF bullish EMA stack | e21={e21:.2f} e50={e50:.2f}"
        if price < e21 < e50 and e50 <= e200 * 1.002 and slope < 0:
            score = 30.0 + (5.0 if ll else 0.0)
            return Direction.SHORT, score, f"HTF bearish EMA stack | e21={e21:.2f} e50={e50:.2f}"

    # fallback from MarketState, but lower score
    if market.price > market.ema_50 > market.ema_200:
        return Direction.LONG, 26.0, "MarketState EMA bullish fallback"
    if market.price < market.ema_50 < market.ema_200:
        return Direction.SHORT, 26.0, "MarketState EMA bearish fallback"
    return Direction.NEUTRAL, 0.0, "HTF not aligned"


# ═════════════════════════════════════════════════════════════════════
# ICT detectors
# ═════════════════════════════════════════════════════════════════════

def _detect_sweep(candles: List[Candle], direction: Direction, atr: float) -> Optional[ICTEvent]:
    look = candles[-60:] if len(candles) > 60 else candles[:]
    if len(look) < 18:
        return None
    prior = look[:-5]
    recent = look[-8:]
    base_index = len(candles) - len(recent)

    if direction == Direction.LONG:
        level = min(c.low for c in prior)
        for j, c in enumerate(recent):
            body = max(abs(c.close - c.open), atr * 0.05)
            lower_wick = min(c.open, c.close) - c.low
            penetration = (level - c.low) / atr
            close_back = c.close >= level - atr * 0.08
            wick_ratio = lower_wick / body
            if penetration >= 0.08 and close_back and wick_ratio >= 1.0:
                quality = min(1.0, 0.35 + penetration * 0.35 + min(wick_ratio, 3.0) * 0.10)
                return ICTEvent("SELL_SIDE_SWEEP", c.low, base_index + j, quality, f"pen={penetration:.2f}ATR wick={wick_ratio:.1f}")
    else:
        level = max(c.high for c in prior)
        for j, c in enumerate(recent):
            body = max(abs(c.close - c.open), atr * 0.05)
            upper_wick = c.high - max(c.open, c.close)
            penetration = (c.high - level) / atr
            close_back = c.close <= level + atr * 0.08
            wick_ratio = upper_wick / body
            if penetration >= 0.08 and close_back and wick_ratio >= 1.0:
                quality = min(1.0, 0.35 + penetration * 0.35 + min(wick_ratio, 3.0) * 0.10)
                return ICTEvent("BUY_SIDE_SWEEP", c.high, base_index + j, quality, f"pen={penetration:.2f}ATR wick={wick_ratio:.1f}")
    return None


def _detect_displacement(candles: List[Candle], direction: Direction, atr: float, after_index: int = 0) -> Optional[ICTEvent]:
    start = max(0, after_index)
    search = candles[start:][-18:]
    if len(search) < 2:
        return None
    vols = [c.volume for c in candles[-35:] if c.volume and c.volume > 0]
    avg_vol = sum(vols) / max(1, len(vols)) if vols else 1.0
    base_index = len(candles) - len(search)
    best: Optional[ICTEvent] = None
    for j, c in enumerate(search):
        body = abs(c.close - c.open)
        body_atr = body / atr
        if body_atr < 0.55:
            continue
        if direction == Direction.LONG and c.close <= c.open:
            continue
        if direction == Direction.SHORT and c.close >= c.open:
            continue
        vr = c.volume / avg_vol if avg_vol > 0 else 1.0
        if vr < 0.60:
            continue
        quality = min(1.0, 0.40 + (body_atr - 0.70) * 0.30 + min(vr, 2.5) * 0.10)
        ev = ICTEvent("DISPLACEMENT", c.close, base_index + j, quality, f"body={body_atr:.2f}ATR vol={vr:.2f}")
        if best is None or ev.quality > best.quality:
            best = ev
    return best


def _detect_fvg(candles: List[Candle], direction: Direction, atr: float, after_index: int = 0) -> Optional[Zone]:
    start = max(2, after_index)
    end = len(candles)
    best: Optional[Zone] = None
    for i in range(end - 1, start, -1):
        a, b, c = candles[i - 2], candles[i - 1], candles[i]
        if direction == Direction.LONG:
            gap = c.low - a.high
            if gap >= atr * 0.12 and b.close > b.open:
                z = _make_zone(c.low, a.high, "FVG", 0.72 + min(0.18, gap / atr * 0.10), i)
                best = z
                break
        else:
            gap = a.low - c.high
            if gap >= atr * 0.12 and b.close < b.open:
                z = _make_zone(a.low, c.high, "FVG", 0.72 + min(0.18, gap / atr * 0.10), i)
                best = z
                break
    return best


def _detect_order_block(candles: List[Candle], direction: Direction, atr: float, after_index: int = 0) -> Optional[Zone]:
    start = max(3, after_index)
    for i in range(len(candles) - 2, start, -1):
        ob = candles[i - 1]
        nxt = candles[i]
        move_atr = abs(nxt.close - nxt.open) / atr
        if move_atr < 0.55:
            continue
        if direction == Direction.LONG and ob.close < ob.open and nxt.close > nxt.open:
            return _make_zone(ob.high, ob.low, "ORDER_BLOCK", 0.68 + min(0.20, move_atr * 0.05), i - 1)
        if direction == Direction.SHORT and ob.close > ob.open and nxt.close < nxt.open:
            return _make_zone(ob.high, ob.low, "ORDER_BLOCK", 0.68 + min(0.20, move_atr * 0.05), i - 1)
    return None


def _make_zone(top: float, bottom: float, zone_type: str, quality: float, created_index: int) -> Zone:
    top, bottom = max(top, bottom), min(top, bottom)
    width = max(top - bottom, 1e-6)
    return Zone(
        top=round(top, 4), bottom=round(bottom, 4), mid=round((top + bottom) / 2.0, 4),
        zone_type=zone_type, quality=round(min(1.0, max(0.0, quality)), 3),
        tested_count=0, width=round(width, 4), created_index=created_index, freshness=1.0,
    )


def _count_zone_touches(candles: List[Candle], zone: Zone, start_index: int) -> int:
    touches = 0
    for c in candles[max(0, start_index):]:
        if c.low <= zone.top and c.high >= zone.bottom:
            touches += 1
    return max(0, touches - 1)  # created candle itself does not count as a retest


def _resolve_zone(candles: List[Candle], direction: Direction, atr: float, after_index: int) -> Tuple[Optional[Zone], Dict[str, Any]]:
    fvg = _detect_fvg(candles, direction, atr, after_index)
    ob = _detect_order_block(candles, direction, atr, after_index)
    candidates = [z for z in (fvg, ob) if z is not None]
    if not candidates:
        return None, {"fvg": False, "ob": False, "reason": "no fresh FVG/OB"}

    # Prefer fresh zone nearest to current price but punish over-tested zones.
    price = candles[-1].close
    best: Optional[Zone] = None
    best_score = -999.0
    debug_candidates = []
    for z in candidates:
        touches = _count_zone_touches(candles, z, z.created_index)
        age = max(0, len(candles) - z.created_index)
        freshness = max(0.0, 1.0 - touches * 0.35 - max(0, age - 18) * 0.015)
        z.tested_count = touches
        z.freshness = round(freshness, 3)
        dist = abs(price - z.mid) / atr
        score = z.quality * 60.0 + freshness * 30.0 - dist * 8.0
        debug_candidates.append({"type": z.zone_type, "dist": round(dist, 2), "touches": touches, "fresh": round(freshness, 2), "score": round(score, 1)})
        if score > best_score:
            best = z
            best_score = score

    return best, {"fvg": bool(fvg), "ob": bool(ob), "candidates": debug_candidates, "zone_select_score": round(best_score, 1)}


def _trigger_retest(candles: List[Candle], zone: Zone, direction: Direction, atr: float) -> Tuple[Optional[Retest], Dict[str, Any]]:
    window = candles[-6:] if len(candles) >= 6 else candles
    if not window:
        return None, {"reason": "no trigger candles"}
    last = window[-1]
    touched = last.low <= zone.top and last.high >= zone.bottom
    dist = abs(last.close - zone.mid) / atr
    body_atr = abs(last.close - last.open) / atr

    if not touched and dist > 0.85:
        return None, {"reason": f"far from zone {dist:.2f}ATR", "dist_atr": round(dist, 2), "touched": touched}

    # Clean rejection / reclaim: close must reclaim the zone midpoint in direction.
    if direction == Direction.LONG:
        reclaim = last.close > zone.mid and last.close > last.open
        continuation = last.close >= max(c.high for c in window[:-1]) - atr * 0.10 if len(window) > 1 else False
    else:
        reclaim = last.close < zone.mid and last.close < last.open
        continuation = last.close <= min(c.low for c in window[:-1]) + atr * 0.10 if len(window) > 1 else False

    if not reclaim and not continuation:
        return None, {"reason": "no reclaim/continuation", "dist_atr": round(dist, 2), "touched": touched, "body_atr": round(body_atr, 2)}

    # Avoid tiny indecision candles.
    if body_atr < 0.12:
        return None, {"reason": f"weak trigger candle {body_atr:.2f}ATR", "dist_atr": round(dist, 2), "touched": touched}

    rt = Retest(price=last.close, candle=last, body_atr=round(body_atr, 2), touched_zone=bool(touched))
    return rt, {"mode": "RECLAIM" if reclaim else "CONTINUATION", "dist_atr": round(dist, 2), "touched": touched, "body_atr": round(body_atr, 2)}


def _fresh_zone_qualifies(
    sweep: Optional[ICTEvent],
    setup_len: int,
    displacement: Optional[ICTEvent],
    zone: Zone,
    price: float,
    atr: float,
    direction: Direction,
) -> Tuple[bool, str]:
    """
    Balanced Design (revised): enter during the displacement phase without
    waiting for a retest pullback.  Active gates:
      1. Zone freshness >= 0.90  (zone just formed, untested)
      2. Displacement quality >= 0.65  (strong institutional candle)
      3. Displacement age <= 5 bars within the setup window
      4. Price on correct side of zone.mid:
           LONG  -> price >= zone.mid  (cleared zone upward)
           SHORT -> price <= zone.mid  (cleared zone downward)
      5. HTF not NEUTRAL -- guaranteed by caller.
    Returns (qualifies, reason_string).
    """
    if zone.freshness < 0.90:
        return False, f"freshness={zone.freshness:.2f}<0.90"
    if displacement is None:
        return False, "no_displacement"
    if displacement.quality < 0.65:
        return False, f"disp_q={displacement.quality:.2f}<0.65"
    disp_age = setup_len - displacement.index
    if disp_age > 5:
        return False, f"disp_age={disp_age}>5"
    if direction == Direction.LONG and price < zone.mid:
        return False, f"price {price:.2f} < zone.mid {zone.mid:.2f}"
    if direction == Direction.SHORT and price > zone.mid:
        return False, f"price {price:.2f} > zone.mid {zone.mid:.2f}"
    return True, (
        f"disp_age={disp_age} fresh={zone.freshness:.2f} "
        f"disp_q={displacement.quality:.2f} "
        f"side={'above' if direction == Direction.LONG else 'below'}_mid"
    )


# ═════════════════════════════════════════════════════════════════════
# Main Analyzer
# ═════════════════════════════════════════════════════════════════════

class SmartICTAnalyzer:
    """X2.2 Balanced Daily: one clean decision engine. No legacy wrappers, no bypass."""

    MAX_VIX = 28.0
    MIN_ATR_PCT = 0.0030
    MAX_ATR_PCT = 0.0400
    STOP_BUFFER_ATR = 0.22
    MAX_ZONE_DIST_ATR = 1.45
    MIN_ZONE_FRESHNESS = 0.25
    MIN_RR = 1.2
    TP1_R = 1.2
    TP2_R = 2.4
    TP3_R = 3.6

    COOLDOWN_AFTER_SIGNAL = timedelta(minutes=90)
    COOLDOWN_WIN = timedelta(hours=1)
    COOLDOWN_LOSS = timedelta(hours=2)

    def __init__(self):
        self.cooldowns: Dict[str, datetime] = {}
        self.cooldown_reasons: Dict[str, str] = {}
        self.active_trades: Dict[str, TradeSignal] = {}
        self.rejected_signals: List[RejectedSignal] = []
        self._daily_count: Dict[Tuple[str, str], int] = {}
        self.signal_counter = 0

    def analyze(self, symbol: str, market: MarketState,
                candles_1h: List[Candle], candles_15m: List[Candle],
                option_data: Optional[OptionData] = None,
                candles_5m: Optional[List[Candle]] = None) -> AnalyzerResult:
        symbol = str(symbol).upper().strip()
        self.signal_counter += 1
        sid = f"X2E-{symbol}-{datetime.now().strftime('%Y%m%d')}-{self.signal_counter:04d}"
        partial: Dict[str, Any] = {"engine": "X2.3_DAILY_SELECTIVE"}
        reasons: List[RejectionReason] = []

        profile = SYMBOL_PROFILES.get(symbol, SYMBOL_PROFILES["__DEFAULT__"])
        partial["profile"] = profile
        if not profile.get("enabled", True):
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "SYMBOL", "الرمز معطّل", symbol)

        cd = self.get_cooldown_status(symbol)
        if cd.get("active"):
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "COOLDOWN", "Cooldown نشط", f"{cd.get('remaining_minutes', 0)}m")

        day_key = (symbol, datetime.now().strftime("%Y%m%d"))
        max_daily = int(profile.get("max_daily_signals", 1))
        if self._daily_count.get(day_key, 0) >= max_daily:
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "DAILY_LIMIT", "اكتفى اليوم", f"max={max_daily}")

        if market.news_risk == "HIGH":
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "NEWS", "خطر أخبار عالي", "news_risk=HIGH")
        if market.vix > self.MAX_VIX:
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "REGIME", "VIX مرتفع", f"VIX={market.vix:.1f}>{self.MAX_VIX}")

        # Timeframe selection: setup candles, trigger candles.
        tf = profile.get("timeframe", "1H")
        setup_candles = candles_15m if tf == "15m" else (candles_1h if candles_1h and len(candles_1h) >= 60 else candles_15m)
        trigger_candles = candles_5m if candles_5m and len(candles_5m) >= 30 else candles_15m
        if not setup_candles or len(setup_candles) < 60 or not candles_15m or len(candles_15m) < 60:
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "DATA", "بيانات قليلة", f"setup={len(setup_candles) if setup_candles else 0}")

        price = setup_candles[-1].close
        atr = _atr(setup_candles, 14)
        atr_pct = atr / max(price, 0.01)
        if atr_pct < self.MIN_ATR_PCT:
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "REGIME", "تذبذب ضعيف", f"ATR%={atr_pct*100:.2f}")
        if atr_pct > self.MAX_ATR_PCT:
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "REGIME", "تذبذب مبالغ", f"ATR%={atr_pct*100:.2f}")

        adx = _adx_calc(setup_candles, 14)
        min_adx = float(profile.get("min_adx", 22))
        max_adx = float(profile.get("max_adx", 60))
        partial.update({"adx": round(adx, 1), "atr_pct": round(atr_pct * 100, 2)})
        if adx < min_adx:
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "ADX", "ADX ضعيف", f"{adx:.1f}<{min_adx:.1f}")
        if adx > max_adx:
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial, "ADX", "ADX مبالغ", f"{adx:.1f}>{max_adx:.1f}")

        direction, trend_score, trend_detail = _direction_from_htf(candles_1h, market)
        partial.update({"direction": direction.value, "trend_score": round(trend_score, 1), "trend_detail": trend_detail})
        if direction == Direction.NEUTRAL:
            return self._reject(symbol, direction, reasons, partial, "HTF_ALIGNMENT", "الاتجاه العام غير متوافق", trend_detail)

        # X2.3: لا نخنق الدخول باشتراط Sweep AND Displacement دائماً.
        # المسار المقبول:
        #   A) Sweep + Zone + Retest
        #   B) Displacement + Zone + Retest مع HTF واضح
        # الأفضلية والسكور الأعلى عندما يجتمع Sweep + Displacement.
        sweep = _detect_sweep(setup_candles, direction, atr)
        sweep_index = sweep.index if sweep else max(0, len(setup_candles) - 36)
        displacement = _detect_displacement(setup_candles, direction, atr, sweep_index)
        if not sweep and not displacement:
            return self._reject(symbol, direction, reasons, partial, "SETUP", "لا Sweep ولا Displacement", "need at least one institutional event")

        after_index = displacement.index if displacement else sweep_index
        zone, zone_debug = _resolve_zone(setup_candles, direction, atr, after_index)
        partial["zone_debug"] = zone_debug
        if not zone:
            return self._reject(symbol, direction, reasons, partial, "ZONE", "لا توجد FVG/OB حديثة بعد الاندفاع", zone_debug.get("reason", "none"))
        if zone.freshness < self.MIN_ZONE_FRESHNESS:
            return self._reject(symbol, direction, reasons, partial, "ZONE", "المنطقة مستهلكة", f"fresh={zone.freshness:.2f}< {self.MIN_ZONE_FRESHNESS}")

        dist_atr = abs(price - zone.mid) / atr

        # ── Balanced Design (revised): Fresh Zone Entry -- evaluated BEFORE distance gate ──
        _fz_ok, _fz_reason = _fresh_zone_qualifies(
            sweep, len(setup_candles), displacement, zone, price, atr, direction
        )
        partial["fresh_zone"] = {"qualifies": _fz_ok, "reason": _fz_reason}

        if _fz_ok:
            # Enter at current price -- bypass distance gate, no pullback required.
            _fz_candle = setup_candles[-1]
            rt = Retest(
                price=price,
                candle=_fz_candle,
                body_atr=round(abs(_fz_candle.close - _fz_candle.open) / max(atr, 1e-6), 2),
                touched_zone=(zone.bottom <= price <= zone.top),
            )
            trigger_debug = {
                "mode": "FRESH_ZONE_ENTRY",
                "dist_atr": round(dist_atr, 2),
                "touched": rt.touched_zone,
                "body_atr": rt.body_atr,
                "fz_reason": _fz_reason,
            }
            entry_mode = "FRESH_ZONE_ENTRY"
            _ICT_LOG.info(
                "[FRESH_ZONE_ENTRY] %s %s price=%.2f disp_age=%s "
                "fresh=%.2f disp_q=%.2f dist=%.2fATR",
                symbol, direction.value, price,
                (len(setup_candles) - displacement.index) if displacement else "?",
                zone.freshness,
                displacement.quality if displacement else 0.0,
                dist_atr,
            )
        else:
            # Existing path: apply distance gate first, then retest.
            if dist_atr > self.MAX_ZONE_DIST_ATR:
                partial["fresh_zone"]["dist_atr"] = round(dist_atr, 2)
                return self._reject(symbol, direction, reasons, partial, "ZONE_DISTANCE",
                                    "السعر بعيد عن المنطقة",
                                    f"{dist_atr:.2f}ATR>{self.MAX_ZONE_DIST_ATR}")
            rt, trigger_debug = _trigger_retest(trigger_candles, zone, direction, atr)
            if not rt:
                partial["trigger"] = trigger_debug
                return self._reject(symbol, direction, reasons, partial, "TRIGGER",
                                    "لا يوجد Retest/Reclaim نظيف",
                                    trigger_debug.get("reason", "none"))
            entry_mode = "RETEST_ENTRY"
            _ICT_LOG.debug(
                "[RETEST_ENTRY] %s %s dist=%.2fATR mode=%s",
                symbol, direction.value, dist_atr, trigger_debug.get("mode", ""),
            )
        partial["trigger"] = trigger_debug
        partial["entry_mode"] = entry_mode
        # ── end Fresh Zone / Retest branch ──────────────────────────────────

        entry = rt.price
        stop = (zone.bottom - atr * self.STOP_BUFFER_ATR) if direction == Direction.LONG else (zone.top + atr * self.STOP_BUFFER_ATR)
        if direction == Direction.LONG and stop >= entry:
            return self._reject(symbol, direction, reasons, partial, "RISK", "وقف غير صالح", "LONG stop >= entry")
        if direction == Direction.SHORT and stop <= entry:
            return self._reject(symbol, direction, reasons, partial, "RISK", "وقف غير صالح", "SHORT stop <= entry")
        risk = abs(entry - stop)
        if risk < atr * 0.25:
            return self._reject(symbol, direction, reasons, partial, "RISK", "وقف ضيق جداً", f"risk={risk/atr:.2f}ATR")
        if risk > atr * 2.2:
            return self._reject(symbol, direction, reasons, partial, "RISK", "وقف واسع جداً", f"risk={risk/atr:.2f}ATR")

        target_1, target_2, target_3 = self._targets(entry, risk, direction)
        rr = abs(target_1 - entry) / risk if risk > 0 else 0.0
        if rr < self.MIN_RR:
            return self._reject(symbol, direction, reasons, partial, "RR", "العائد غير كافٍ", f"RR={rr:.2f}")

        vol_ratio = _volume_ratio(setup_candles, 20)
        option_score, option_verdict = self._option_score(option_data, direction)
        zone_score = zone.quality * 18.0 + zone.freshness * 12.0
        sweep_score = (sweep.quality * 14.0) if sweep else 0.0
        displacement_score = (displacement.quality * 14.0) if displacement else 0.0
        combo_bonus = 6.0 if (sweep and displacement) else 0.0
        trigger_score = min(12.0, 6.0 + rt.body_atr * 3.0 + (2.0 if rt.touched_zone else 0.0))
        volume_score = min(6.0, max(0.0, (vol_ratio - 0.8) * 5.0))
        rr_score = min(6.0, rr * 2.5)
        risk_score = min(6.0, max(0.0, 6.0 - abs((risk / atr) - 1.0) * 2.0))

        score = round(
            trend_score + zone_score + sweep_score + displacement_score + combo_bonus + trigger_score + volume_score + rr_score + risk_score + option_score,
            1,
        )
        min_conf = float(profile.get("min_conf", 84))
        partial.update({
            "sweep": sweep.__dict__ if sweep else None,
            "displacement": displacement.__dict__ if displacement else None,
            "zone": zone.__dict__,
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "rr": round(rr, 2),
            "vol_ratio": round(vol_ratio, 2),
            "score": score,
            "min_conf": min_conf,
            "option_verdict": option_verdict,
        })
        if score < min_conf:
            return self._reject(symbol, direction, reasons, partial, "SCORE", "Score أقل من الحد", f"{score:.1f}<{min_conf:.1f}")

        grade = SignalGrade.A_PLUS if score >= 92 else (SignalGrade.A if score >= 84 else SignalGrade.B)
        conf = ConfidenceBreakdown(
            trend=round(trend_score, 1),
            zone=round(zone_score, 1),
            volume=round(volume_score, 1),
            stop_quality=round(risk_score, 1),
            risk_reward=round(rr_score, 1),
            option_quality=round(option_score, 1),
            backtest=0.0,
            total=score,
        )
        signal = TradeSignal(
            signal_id=sid,
            timestamp=datetime.now(),
            symbol=symbol,
            direction=direction,
            entry_price=round(entry, 2),
            stop_loss=round(stop, 2),
            current_stop=round(stop, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            target_3=round(target_3, 2),
            risk_amount=round(risk, 2),
            risk_reward=f"1:{rr:.1f}",
            htf_bias=direction,
            liquidity_sweep=sweep,
            displacement=displacement,
            zone=zone,
            retest=rt,
            option_data=option_data,
            option_verdict=option_verdict,
            confidence_score=score,
            confidence_breakdown=conf,
            grade=grade,
            reasons_for_entry=[
                f"HTF={direction.value} | {trend_detail}",
                f"Sweep={sweep.detail if sweep else 'NONE'} | Displacement={displacement.detail if displacement else 'NONE'}",
                f"Zone={zone.zone_type} fresh={zone.freshness:.2f} touches={zone.tested_count}",
                f"[{entry_mode}] Trigger={trigger_debug.get('mode')} | dist={trigger_debug.get('dist_atr')}ATR",
                f"Score={score:.1f} | Grade={grade.value} | R:R=1:{rr:.1f}",
            ],
        )
        self.active_trades[sid] = signal
        self.cooldowns[symbol] = datetime.now() + self.COOLDOWN_AFTER_SIGNAL
        self.cooldown_reasons[symbol] = "SIGNAL"
        self._daily_count[day_key] = self._daily_count.get(day_key, 0) + 1
        return signal

    def _targets(self, entry: float, risk: float, direction: Direction) -> Tuple[float, float, float]:
        if direction == Direction.LONG:
            return entry + risk * self.TP1_R, entry + risk * self.TP2_R, entry + risk * self.TP3_R
        return entry - risk * self.TP1_R, entry - risk * self.TP2_R, entry - risk * self.TP3_R

    def _option_score(self, option: Optional[OptionData], direction: Direction) -> Tuple[float, str]:
        # Strategy decision can run without Greeks; execution.py remains the only contract selector.
        if option is None:
            return 3.0, "DEFERRED_TO_EXECUTION"
        score = 0.0
        expected = "CALL" if direction == Direction.LONG else "PUT"
        try:
            option.option_type = expected
        except Exception:
            pass
        delta = abs(_safe(getattr(option, "delta", 0.0)))
        if 0.40 <= delta <= 0.70:
            score += 3.0
        elif 0.30 <= delta <= 0.80:
            score += 1.5
        bid = _safe(getattr(option, "bid", 0.0))
        ask = _safe(getattr(option, "ask", 0.0))
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            spread = (ask - bid) / mid if mid > 0 else 9.0
            if spread <= 0.10:
                score += 3.0
            elif spread <= 0.15:
                score += 1.5
        if int(getattr(option, "open_interest", 0) or 0) >= 500:
            score += 2.0
        dte = int(getattr(option, "days_to_expiry", 0) or 0)
        if 0 <= dte <= 10:
            score += 2.0
        return min(10.0, score), f"TYPE={expected}"

    def _reject(self, symbol: str, direction: Direction, reasons: List[RejectionReason], partial: Dict[str, Any],
                step: str, reason: str, detail: str) -> RejectedSignal:
        rs = list(reasons)
        rs.append(RejectionReason(step, reason, detail, "CRITICAL"))
        r = RejectedSignal(datetime.now(), symbol, direction, rs, partial)
        self.rejected_signals.append(r)
        return r

    def get_cooldown_status(self, symbol: str) -> dict:
        exp = self.cooldowns.get(str(symbol).upper())
        if not exp or datetime.now() >= exp:
            return {"active": False}
        remaining = int((exp - datetime.now()).total_seconds() // 60)
        return {"active": True, "remaining_minutes": remaining, "reason": self.cooldown_reasons.get(str(symbol).upper(), "")}

    def record_outcome(self, symbol: str, outcome: str, **_: Any) -> None:
        symbol = str(symbol).upper()
        if outcome == "WIN":
            self.cooldowns[symbol] = datetime.now() + self.COOLDOWN_WIN
            self.cooldown_reasons[symbol] = "WIN"
        elif outcome == "LOSS":
            self.cooldowns[symbol] = datetime.now() + self.COOLDOWN_LOSS
            self.cooldown_reasons[symbol] = "LOSS"

    @property
    def bypass_filters(self) -> bool:
        return False

    @bypass_filters.setter
    def bypass_filters(self, _: Any) -> None:
        # No bypass in the clean decision engine.
        pass
