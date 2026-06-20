# -*- coding: utf-8 -*-
"""
analyzer_x1.py — Smart Institutional Analyzer X1.2 STRICT
محلل قرار مؤسسي صارم للبوت — هدفه تقليل الإشارات الضعيفة بقوة

الفلسفة:
- ليس مولّد إشارات عشوائية.
- ليس تجميع مؤشرات.
- القرار يمر عبر 4 طبقات فقط:
  1) Market Regime
  2) Direction Engine
  3) Institutional Zone Engine
  4) Trigger Engine

المدخلات المتوافقة مع الجسر القديم:
    analyze(symbol, market, candles_1h, candles_15m, option_data=None, candles_5m=None)

المخرجات المتوافقة مع التنفيذ القديم:
    TradeSignal أو RejectedSignal
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Literal, Optional, Union, Any, Tuple
import json
import math
from pathlib import Path


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class ZoneType(Enum):
    FVG = "FVG"
    ORDER_BLOCK = "ORDER_BLOCK"
    SWEEP_ZONE = "SWEEP_ZONE"
    NONE = "NONE"


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
    TARGET_3_HIT = "TARGET_3_HIT"
    STOPPED_OUT = "STOPPED_OUT"
    TIME_EXPIRED = "TIME_EXPIRED"


class CooldownReason(Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    CANCELLED = "CANCELLED"


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
class LiquiditySweep:
    sweep_type: str
    level: float
    prior_level: float
    candle_index: int
    wick_ratio: float
    quality: str


@dataclass
class Displacement:
    candle: Candle
    body_atr: float
    volume_ratio: float
    direction: Direction


@dataclass
class Zone:
    zone_type: ZoneType
    top: float
    bottom: float
    mid: float
    width: float
    quality: float
    tested_count: int = 0


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
class BacktestMetrics:
    pattern_name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    gross_profit_r: float = 0.0
    gross_loss_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_r: float = 0.0
    equity_r: float = 0.0
    peak_equity_r: float = 0.0
    last_updated: Optional[datetime] = None


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
    liquidity_sweep: Optional[LiquiditySweep]
    displacement: Optional[Displacement]
    zone: Zone
    retest: Retest
    option_data: Optional[OptionData]
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
            "grade": self.grade.value,
            "verdict": "REJECTED",
            "rejection_reasons": [r.__dict__ for r in self.rejection_reasons],
            "partial_analysis": self.partial_analysis,
        }


AnalyzerResult = Union[TradeSignal, RejectedSignal]


# 14 symbols الهدف — كلها مفعلة افتراضياً.
_DEFAULT_SYMBOL_PROFILES: Dict[str, Dict[str, Any]] = {
    # X1.2 STRICT:
    # الهدف ليس كثرة الصفقات؛ الهدف تصفية الإشارات الضعيفة جداً.
    # ملاحظة مهمة: 85-90% WR لا تُضمن برفع السكور فقط، لكنها إعدادات شديدة المحافظة.
    "QQQ":  {"enabled": True, "min_score": 90, "min_adx": 22, "max_daily_signals": 1},
    "SPY":  {"enabled": True, "min_score": 90, "min_adx": 21, "max_daily_signals": 1},
    "NVDA": {"enabled": True, "min_score": 92, "min_adx": 26, "max_daily_signals": 1},
    "MSFT": {"enabled": True, "min_score": 90, "min_adx": 22, "max_daily_signals": 1},
    "META": {"enabled": True, "min_score": 90, "min_adx": 23, "max_daily_signals": 1},
    "AAPL": {"enabled": True, "min_score": 91, "min_adx": 23, "max_daily_signals": 1},
    "AMD":  {"enabled": True, "min_score": 90, "min_adx": 24, "max_daily_signals": 1},
    "AMZN": {"enabled": True, "min_score": 90, "min_adx": 22, "max_daily_signals": 1},
    "AVGO": {"enabled": True, "min_score": 92, "min_adx": 26, "max_daily_signals": 1},
    "TSLA": {"enabled": True, "min_score": 93, "min_adx": 28, "max_daily_signals": 1},
    "COST": {"enabled": True, "min_score": 94, "min_adx": 24, "max_daily_signals": 1},
    "NFLX": {"enabled": True, "min_score": 92, "min_adx": 25, "max_daily_signals": 1},
    "GOOGL":{"enabled": True, "min_score": 90, "min_adx": 22, "max_daily_signals": 1},
    "LLY":  {"enabled": True, "min_score": 93, "min_adx": 25, "max_daily_signals": 1},
    "__DEFAULT__": {"enabled": False, "min_score": 999, "min_adx": 99, "max_daily_signals": 0},
}

_RUNTIME_PROFILE_OVERRIDE: Dict[str, Dict[str, Any]] = {}


def _load_symbol_profiles() -> Dict[str, Dict[str, Any]]:
    path = Path(__file__).resolve().parent / "symbol_profiles_x1.json"
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                merged = dict(_DEFAULT_SYMBOL_PROFILES)
                merged.update(data)
                return merged
        except Exception:
            pass
    return _DEFAULT_SYMBOL_PROFILES


SYMBOL_PROFILES = _load_symbol_profiles()


def _profile_for(symbol: str) -> Dict[str, Any]:
    sym = str(symbol).upper().strip()
    if sym in _RUNTIME_PROFILE_OVERRIDE:
        return _RUNTIME_PROFILE_OVERRIDE[sym]
    return SYMBOL_PROFILES.get(sym, SYMBOL_PROFILES["__DEFAULT__"])


class SmartDayTradingAnalyzer:
    """X1: Decision Engine موحد. أي رفض هنا هو رفض استراتيجي حقيقي."""

    MAX_VIX = 24.0
    MIN_ATR_PCT = 0.0040
    MAX_ATR_PCT = 0.030
    PIVOT_LEFT = 2
    PIVOT_RIGHT = 2
    STRUCTURE_LOOKBACK = 96
    ZONE_LOOKBACK = 72
    TRIGGER_LOOKBACK = 6
    MAX_ENTRY_DISTANCE_ATR = 0.70
    MIN_ZONE_SCORE = 55
    MIN_RR = 1.8
    STOP_BUFFER_ATR = 0.22
    COOLDOWN_AFTER_SIGNAL = timedelta(hours=3)
    COOLDOWN_LOSS = timedelta(hours=1, minutes=30)
    COOLDOWN_WIN = timedelta(minutes=45)

    def __init__(self):
        self.cooldowns: Dict[str, datetime] = {}
        self.cooldown_reasons: Dict[str, CooldownReason] = {}
        self.active_trades: Dict[str, TradeSignal] = {}
        self.rejected_signals: List[RejectedSignal] = []
        self.pattern_history: Dict[str, BacktestMetrics] = {}
        self.signal_counter = 0
        self._daily_signal_count: Dict[Tuple[str, str], int] = {}
        self._current_symbol = ""
        self.bypass_filters = False

    def analyze(self, symbol: str, market: MarketState, candles_1h: List[Candle],
                candles_15m: List[Candle], option_data: Optional[OptionData] = None,
                candles_5m: Optional[List[Candle]] = None) -> AnalyzerResult:
        symbol = str(symbol).upper().strip()
        self._current_symbol = symbol
        self.signal_counter += 1
        signal_id = f"X1-{symbol}-{datetime.now().strftime('%Y%m%d')}-{self.signal_counter:04d}"
        partial: Dict[str, Any] = {"engine": "X1"}
        reasons: List[RejectionReason] = []

        profile = _profile_for(symbol)
        partial["profile"] = profile
        if not profile.get("enabled", True):
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial,
                                "SYMBOL_FILTER", "الرمز معطّل", f"{symbol} disabled")

        cd = self.get_cooldown_status(symbol)
        if cd.get("active"):
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial,
                                "COOLDOWN", "Cooldown نشط", f"متبقي {cd.get('remaining_minutes', 0)} دقيقة")

        today_key = (symbol, datetime.now().strftime("%Y%m%d"))
        if self._daily_signal_count.get(today_key, 0) >= int(profile.get("max_daily_signals", 1)):
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial,
                                "DAILY_LIMIT", "تم الاكتفاء من الرمز اليوم", "صفقة واحدة يومياً لكل رمز")

        regime, regime_score, regime_detail = self._market_regime(market, candles_15m)
        partial.update({"regime": regime, "regime_score": regime_score, "regime_detail": regime_detail})
        if regime == "CHAOTIC":
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial,
                                "MARKET_REGIME", "السوق عشوائي", regime_detail)
        if market.news_risk == "HIGH":
            return self._reject(symbol, Direction.NEUTRAL, reasons, partial,
                                "NEWS", "خطر أخبار عالي", "لا دخول وقت الأخبار العالية")

        direction, trend_score, trend_detail = self._direction_engine(market, candles_1h, candles_15m)
        partial.update({"htf_bias": direction.value, "trend_score": trend_score, "structure": trend_detail})
        if direction == Direction.NEUTRAL:
            return self._reject(symbol, direction, reasons, partial,
                                "DIRECTION", "الاتجاه غير واضح", trend_detail.get("reason", "لا يوجد اتجاه مؤكد"))

        if trend_score < 30.0:
            return self._reject(symbol, direction, reasons, partial,
                                "DIRECTION", "الاتجاه ضعيف", f"TrendScore={trend_score:.1f} < 30.0")

        min_adx = float(profile.get("min_adx", 22))
        if market.adx < min_adx:
            return self._reject(symbol, direction, reasons, partial,
                                "DIRECTION", "اتجاه ضعيف", f"ADX={market.adx:.1f} < {min_adx:.1f}")

        zone_pack = self._zone_engine(candles_15m, direction, market)
        partial["zone_pack"] = zone_pack["debug"]
        if not zone_pack["zone"] or zone_pack["score"] < self.MIN_ZONE_SCORE:
            return self._reject(symbol, direction, reasons, partial,
                                "ZONE", "لا توجد منطقة مؤسسية كافية", f"ZoneScore={zone_pack['score']:.1f} < {self.MIN_ZONE_SCORE}")


        trigger_candles = candles_5m if candles_5m and len(candles_5m) >= 20 else candles_15m
        trigger_pack = self._trigger_engine(trigger_candles, zone_pack["zone"], direction, market)
        partial["trigger"] = trigger_pack["debug"]
        if not trigger_pack["retest"] or trigger_pack["score"] < 24:
            return self._reject(symbol, direction, reasons, partial,
                                "TRIGGER", "لا يوجد Trigger قوي", trigger_pack["debug"].get("reason", "لا دخول"))

        entry = float(trigger_pack["retest"].price)

        # X1.2: منع المطاردة. الدخول يجب أن يكون قريباً جداً من منتصف المنطقة.
        entry_dist_atr = abs(entry - zone_pack["zone"].mid) / max(market.atr_14, 0.01)
        partial["entry_dist_atr"] = round(entry_dist_atr, 2)
        if entry_dist_atr > self.MAX_ENTRY_DISTANCE_ATR:
            return self._reject(symbol, direction, reasons, partial,
                                "ENTRY_DISTANCE", "الدخول بعيد عن المنطقة", f"{entry_dist_atr:.2f} ATR > {self.MAX_ENTRY_DISTANCE_ATR}")

        stop = self._calculate_stop(zone_pack["zone"], direction, market)
        risk = abs(entry - stop)
        if risk <= 0:
            return self._reject(symbol, direction, reasons, partial, "RISK", "مخاطرة غير صالحة", "risk <= 0")

        target_1, target_2, target_3 = self._calculate_targets(entry, risk, direction)
        rr = abs(target_1 - entry) / risk if risk else 0.0
        if rr < self.MIN_RR:
            return self._reject(symbol, direction, reasons, partial,
                                "RR", "العائد مقابل الخطر ضعيف", f"RR={rr:.2f} < {self.MIN_RR}")

        option_score, option_verdict = self._option_score(option_data, direction)
        decision_score = round(trend_score + zone_pack["score"] * 0.30 + trigger_pack["score"] + regime_score + option_score, 1)
        min_score = float(profile.get("min_score", 75))
        partial.update({
            "entry": entry, "stop": stop, "risk": risk,
            "rr": round(rr, 2), "option_score": option_score,
            "option_verdict": option_verdict,
            "decision_score": decision_score, "min_score": min_score,
        })
        if decision_score < min_score:
            return self._reject(symbol, direction, reasons, partial,
                                "DECISION_SCORE", "القرار أقل من الحد", f"Score={decision_score:.1f} < {min_score:.1f}")

        grade = self._grade(decision_score)
        confidence = ConfidenceBreakdown(
            trend=round(trend_score, 1),
            zone=round(zone_pack["score"] * 0.30, 1),
            volume=round(regime_score, 1),
            stop_quality=round(min(10.0, max(0.0, 10.0 - (risk / max(market.atr_14, 0.01) - 1.0) * 2.0)), 1),
            risk_reward=round(min(10.0, rr * 4.0), 1),
            option_quality=round(option_score, 1),
            backtest=0.0,
            total=decision_score,
        )

        signal = TradeSignal(
            signal_id=signal_id,
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
            liquidity_sweep=zone_pack.get("sweep"),
            displacement=zone_pack.get("displacement"),
            zone=zone_pack["zone"],
            retest=trigger_pack["retest"],
            option_data=option_data,
            option_verdict=option_verdict,
            confidence_score=decision_score,
            confidence_breakdown=confidence,
            grade=grade,
            reasons_for_entry=[
                f"Regime: {regime} | {regime_detail}",
                f"Direction: {direction.value} | {trend_detail.get('reason', '')}",
                f"ZoneScore={zone_pack['score']:.0f} | {zone_pack['debug'].get('components', '')}",
                f"Trigger: {trigger_pack['debug'].get('mode')} | Entry={entry:.2f}",
                f"R:R=1:{rr:.1f} | DecisionScore={decision_score:.1f} | Grade={grade.value}",
            ],
        )
        self.active_trades[signal_id] = signal
        self.cooldowns[symbol] = datetime.now() + self.COOLDOWN_AFTER_SIGNAL
        self.cooldown_reasons[symbol] = CooldownReason.CANCELLED
        self._daily_signal_count[today_key] = self._daily_signal_count.get(today_key, 0) + 1
        return signal

    def _reject(self, symbol: str, direction: Direction, reasons: List[RejectionReason], partial: Dict[str, Any],
                step: str, reason: str, detail: str, severity: str = "CRITICAL") -> RejectedSignal:
        reasons = list(reasons)
        reasons.append(RejectionReason(step, reason, detail, severity))
        r = RejectedSignal(datetime.now(), symbol, direction, reasons, partial)
        self.rejected_signals.append(r)
        return r

    def get_cooldown_status(self, symbol: str) -> dict:
        exp = self.cooldowns.get(str(symbol).upper())
        if not exp:
            return {"active": False}
        rem = exp - datetime.now()
        if rem.total_seconds() <= 0:
            return {"active": False}
        return {"active": True, "remaining_minutes": int(rem.total_seconds() // 60),
                "reason": self.cooldown_reasons.get(str(symbol).upper(), CooldownReason.CANCELLED).value}

    def record_outcome(self, symbol: str, outcome: str, direction: str = "", bar_index: int = 0):
        symbol = str(symbol).upper()
        if outcome == "LOSS":
            self.cooldowns[symbol] = datetime.now() + self.COOLDOWN_LOSS
            self.cooldown_reasons[symbol] = CooldownReason.LOSS
        elif outcome == "WIN":
            self.cooldowns[symbol] = datetime.now() + self.COOLDOWN_WIN
            self.cooldown_reasons[symbol] = CooldownReason.WIN

    # ───────────────────────── Engines ─────────────────────────
    def _market_regime(self, market: MarketState, candles: List[Candle]) -> Tuple[str, float, str]:
        if market.vix > self.MAX_VIX:
            return "CHAOTIC", 0.0, f"VIX={market.vix:.1f} مرتفع"
        if not candles or len(candles) < 30 or market.price <= 0 or market.atr_14 <= 0:
            return "CHAOTIC", 0.0, "بيانات غير كافية"
        atr_pct = market.atr_14 / market.price
        if atr_pct < self.MIN_ATR_PCT:
            return "RANGING", 5.0, f"تذبذب منخفض ATR%={atr_pct*100:.2f}"
        if atr_pct > self.MAX_ATR_PCT:
            return "CHAOTIC", 0.0, f"تذبذب مبالغ ATR%={atr_pct*100:.2f}"
        closes = [c.close for c in candles[-20:]]
        net = abs(closes[-1] - closes[0])
        noise = sum(abs(closes[i] - closes[i-1]) for i in range(1, len(closes))) or 1e-9
        efficiency = net / noise
        if market.adx >= 24 and efficiency >= 0.34:
            return "TRENDING", 10.0, f"ADX={market.adx:.1f}, efficiency={efficiency:.2f}"
        if efficiency < 0.18 and market.adx < 18:
            return "CHAOTIC", 0.0, f"chop عالي ADX={market.adx:.1f}, efficiency={efficiency:.2f}"
        return "RANGING", 7.0, f"قابل للتداول ADX={market.adx:.1f}, efficiency={efficiency:.2f}"

    def _direction_engine(self, market: MarketState, candles_1h: List[Candle], candles_15m: List[Candle]) -> Tuple[Direction, float, dict]:
        candles = candles_1h if candles_1h and len(candles_1h) >= 35 else candles_15m
        if not candles or len(candles) < 35:
            return Direction.NEUTRAL, 0.0, {"method": "NO_STRUCTURE", "reason": "بيانات هيكل قليلة"}
        piv = self._pivots(candles[-self.STRUCTURE_LOOKBACK:])
        highs, lows = piv["highs"], piv["lows"]
        close = candles[-1].close
        atr = max(market.atr_14, market.price * 0.003, 0.01)
        ema_dir = self._ema_direction(market)
        if len(highs) >= 2 and len(lows) >= 2:
            _, last_h = highs[-1]; _, prev_h = highs[-2]
            _, last_l = lows[-1];  _, prev_l = lows[-2]
            buffer = atr * 0.08
            hh_hl = last_h > prev_h and last_l > prev_l
            lh_ll = last_h < prev_h and last_l < prev_l
            bos_up = close > last_h - buffer and hh_hl
            bos_dn = close < last_l + buffer and lh_ll
            if bos_up and ema_dir != Direction.SHORT:
                score = 38.0 if ema_dir == Direction.LONG else 34.0
                return Direction.LONG, score, {"method": "STRUCTURE", "reason": f"HH+HL وبالقرب من كسر {last_h:.2f}"}
            if bos_dn and ema_dir != Direction.LONG:
                score = 38.0 if ema_dir == Direction.SHORT else 34.0
                return Direction.SHORT, score, {"method": "STRUCTURE", "reason": f"LH+LL وبالقرب من كسر {last_l:.2f}"}
        if ema_dir != Direction.NEUTRAL and market.adx >= 20:
            dist = abs(market.price - market.ema_50) / atr
            if dist >= 0.30:
                return ema_dir, 30.0, {"method": "EMA_TREND", "reason": f"EMA aligned, dist={dist:.2f}ATR"}
        return Direction.NEUTRAL, 0.0, {"method": "NONE", "reason": "لا هيكل مؤسسي واضح"}

    def _zone_engine(self, candles: List[Candle], direction: Direction, market: MarketState) -> dict:
        debug = {"components": []}
        atr = max(market.atr_14, 0.01)
        window = candles[-self.ZONE_LOOKBACK:] if len(candles) > self.ZONE_LOOKBACK else candles[:]
        if len(window) < 20:
            return {"zone": None, "score": 0.0, "debug": {"reason": "شموع قليلة"}}
        sweep = self._detect_sweep(window, direction, atr)
        displacement = self._detect_displacement(window, direction, atr)
        fvg_zone = self._detect_fvg(window, direction, atr)
        ob_zone = self._detect_order_block(window, direction, atr)
        score = 0.0
        zones: List[Zone] = []
        if sweep:
            score += 40.0; debug["components"].append("Sweep+40")
            zones.append(Zone(ZoneType.SWEEP_ZONE, sweep.level + atr * .20, sweep.level - atr * .20, sweep.level, atr * .40, 0.62))
        if fvg_zone:
            score += 30.0; debug["components"].append("FVG+30"); zones.append(fvg_zone)
        if ob_zone:
            score += 30.0; debug["components"].append("OB+30"); zones.append(ob_zone)
        if displacement:
            score += min(10.0, max(0.0, (displacement.body_atr - 0.45) * 10.0))
            debug["components"].append(f"Disp={displacement.body_atr:.2f}ATR")
        if not zones:
            debug["components"] = "None"
            return {"zone": None, "score": 0.0, "sweep": sweep, "displacement": displacement, "debug": debug}
        price = market.price
        best = min(zones, key=lambda z: abs(price - z.mid))
        # لا تدخل إذا المنطقة قديمة وبعيدة جداً.
        dist_atr = abs(price - best.mid) / atr
        if dist_atr > 1.2:
            score -= min(20.0, (dist_atr - 1.2) * 12.0)
            debug["components"].append(f"Far-{dist_atr:.1f}ATR")
        debug["components"] = ", ".join(debug["components"])
        debug.update({"zone_type": best.zone_type.value, "zone_mid": round(best.mid, 2), "score": round(score, 1)})
        return {"zone": best, "score": max(0.0, min(100.0, score)), "sweep": sweep, "displacement": displacement, "debug": debug}

    def _trigger_engine(self, candles: List[Candle], zone: Zone, direction: Direction, market: MarketState) -> dict:
        atr = max(market.atr_14, 0.01)
        window = candles[-self.TRIGGER_LOOKBACK:] if len(candles) >= self.TRIGGER_LOOKBACK else candles
        last = window[-1]
        touched = last.low <= zone.top and last.high >= zone.bottom
        dist_atr = abs(last.close - zone.mid) / atr
        debug = {"dist_atr": round(dist_atr, 2), "zone_mid": round(zone.mid, 2)}
        if dist_atr > self.MAX_ENTRY_DISTANCE_ATR and not touched:
            debug["reason"] = f"السعر بعيد {dist_atr:.2f} ATR عن المنطقة"
            return {"retest": None, "score": 0.0, "debug": debug}
        prev_high = max(c.high for c in window[:-1]) if len(window) > 1 else last.high
        prev_low = min(c.low for c in window[:-1]) if len(window) > 1 else last.low
        body = abs(last.close - last.open)
        body_atr = body / atr
        if direction == Direction.LONG:
            continuation = last.close > max(last.open, zone.mid) and last.close >= prev_high + atr * .03
            reclaim = touched and last.close > zone.mid
        else:
            continuation = last.close < min(last.open, zone.mid) and last.close <= prev_low - atr * .03
            reclaim = touched and last.close < zone.mid
        score = 0.0
        mode = "NONE"
        if reclaim:
            score += 15.0; mode = "PULLBACK_RECLAIM"
        if continuation:
            score += 20.0; mode = "BREAK_PULLBACK_CONTINUE"
        if body_atr >= 0.55:
            score += min(5.0, body_atr * 4.0)
        debug.update({"mode": mode, "body_atr": round(body_atr, 2), "touched": touched, "score": round(score, 1)})
        if score <= 0:
            debug["reason"] = "لا reclaim ولا continuation"
            return {"retest": None, "score": 0.0, "debug": debug}
        retest = Retest(price=last.close, candle=last, body_atr=round(body_atr, 2), touched_zone=bool(touched))
        return {"retest": retest, "score": min(25.0, score), "debug": debug}

    # ───────────────────────── Helpers ─────────────────────────
    def _ema_direction(self, market: MarketState) -> Direction:
        if market.price > market.ema_50 > market.ema_200:
            return Direction.LONG
        if market.price < market.ema_50 < market.ema_200:
            return Direction.SHORT
        return Direction.NEUTRAL

    def _pivots(self, candles: List[Candle]) -> dict:
        highs, lows = [], []
        n = len(candles)
        for i in range(self.PIVOT_LEFT, n - self.PIVOT_RIGHT):
            c = candles[i]
            if all(c.high >= candles[j].high for j in range(i-self.PIVOT_LEFT, i+self.PIVOT_RIGHT+1) if j != i):
                highs.append((i, c.high))
            if all(c.low <= candles[j].low for j in range(i-self.PIVOT_LEFT, i+self.PIVOT_RIGHT+1) if j != i):
                lows.append((i, c.low))
        return {"highs": highs, "lows": lows}

    def _detect_sweep(self, candles: List[Candle], direction: Direction, atr: float) -> Optional[LiquiditySweep]:
        look = candles[-40:]
        if len(look) < 12:
            return None
        prior = look[:-2]
        last_area = look[-10:]
        if direction == Direction.LONG:
            level = min(c.low for c in prior)
            for idx, c in enumerate(last_area, start=len(candles)-len(last_area)):
                lower = min(c.open, c.close) - c.low
                body = max(abs(c.close - c.open), atr * 0.05)
                if c.low < level - atr * 0.04 and c.close > level - atr * 0.08 and lower / body >= 1.25:
                    return LiquiditySweep("SELL_SIDE_SWEEP", c.low, level, idx, round(lower/body, 2), "GOOD")
        else:
            level = max(c.high for c in prior)
            for idx, c in enumerate(last_area, start=len(candles)-len(last_area)):
                upper = c.high - max(c.open, c.close)
                body = max(abs(c.close - c.open), atr * 0.05)
                if c.high > level + atr * 0.04 and c.close < level + atr * 0.08 and upper / body >= 1.25:
                    return LiquiditySweep("BUY_SIDE_SWEEP", c.high, level, idx, round(upper/body, 2), "GOOD")
        return None

    def _detect_displacement(self, candles: List[Candle], direction: Direction, atr: float) -> Optional[Displacement]:
        vols = [c.volume for c in candles[-30:] if c.volume is not None]
        avg_vol = sum(vols) / len(vols) if vols else 0.0
        for c in reversed(candles[-16:]):
            body = abs(c.close - c.open)
            body_atr = body / atr
            if body_atr < 0.75:
                continue
            if direction == Direction.LONG and c.close <= c.open:
                continue
            if direction == Direction.SHORT and c.close >= c.open:
                continue
            vr = (c.volume / avg_vol) if avg_vol > 0 else 1.0
            if vr >= 0.85:
                return Displacement(c, round(body_atr, 2), round(vr, 2), direction)
        return None

    def _detect_fvg(self, candles: List[Candle], direction: Direction, atr: float) -> Optional[Zone]:
        for i in range(len(candles) - 1, 2, -1):
            a, b, c = candles[i-2], candles[i-1], candles[i]
            if direction == Direction.LONG:
                gap = c.low - a.high
                if gap >= atr * 0.18 and b.close > b.open:
                    top, bottom = c.low, a.high
                    return self._zone(ZoneType.FVG, top, bottom, 0.72)
            else:
                gap = a.low - c.high
                if gap >= atr * 0.18 and b.close < b.open:
                    top, bottom = a.low, c.high
                    return self._zone(ZoneType.FVG, top, bottom, 0.72)
        return None

    def _detect_order_block(self, candles: List[Candle], direction: Direction, atr: float) -> Optional[Zone]:
        # آخر شمعة معاكسة قبل اندفاع مناسب.
        for i in range(len(candles) - 2, 5, -1):
            ob = candles[i-1]
            nxt = candles[i]
            move_atr = abs(nxt.close - nxt.open) / atr
            if move_atr < 0.65:
                continue
            if direction == Direction.LONG and ob.close < ob.open and nxt.close > nxt.open:
                return self._zone(ZoneType.ORDER_BLOCK, ob.high, ob.low, 0.68)
            if direction == Direction.SHORT and ob.close > ob.open and nxt.close < nxt.open:
                return self._zone(ZoneType.ORDER_BLOCK, ob.high, ob.low, 0.68)
        return None

    def _zone(self, ztype: ZoneType, top: float, bottom: float, quality: float) -> Zone:
        top, bottom = max(top, bottom), min(top, bottom)
        width = max(top - bottom, 1e-6)
        return Zone(ztype, round(top, 4), round(bottom, 4), round((top+bottom)/2, 4), round(width, 4), quality)

    def _calculate_stop(self, zone: Zone, direction: Direction, market: MarketState) -> float:
        buf = max(market.atr_14 * self.STOP_BUFFER_ATR, market.price * 0.0015)
        if direction == Direction.LONG:
            return zone.bottom - buf
        return zone.top + buf

    def _calculate_targets(self, entry: float, risk: float, direction: Direction) -> Tuple[float, float, float]:
        if direction == Direction.LONG:
            return entry + risk * 2.0, entry + risk * 4.0, entry + risk * 6.0
        return entry - risk * 2.0, entry - risk * 4.0, entry - risk * 6.0

    def _option_score(self, option: Optional[OptionData], direction: Direction) -> Tuple[float, str]:
        # القرار الاستراتيجي لا يعتمد على توفر Greeks لأن التنفيذ هو المسؤول عن العقد.
        if option is None:
            return 6.0, "OPTION_CHECK_DEFERRED_TO_EXECUTION"
        score = 0.0
        if 0.35 <= abs(option.delta) <= 0.75: score += 3.0
        if option.ask and option.bid and option.ask > 0:
            mid = (option.ask + option.bid) / 2
            spread = (option.ask - option.bid) / mid if mid > 0 else 9
            if spread <= 0.15: score += 3.0
        if option.open_interest >= 300: score += 2.0
        if 0 <= option.days_to_expiry <= 21: score += 2.0
        expected = "CALL" if direction == Direction.LONG else "PUT"
        verdict = "OK" if option.option_type == expected else f"TYPE_FIXED_TO_{expected}"
        try:
            option.option_type = expected
        except Exception:
            pass
        return min(10.0, score), verdict

    def _grade(self, score: float) -> SignalGrade:
        if score >= 94: return SignalGrade.A_PLUS
        if score >= 90: return SignalGrade.A
        if score >= 82: return SignalGrade.B
        return SignalGrade.C
