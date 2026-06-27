# -*- coding: utf-8 -*-
"""
execution.py — محرك التنفيذ الرئيسي.
نسخة تنظيف آمن: حذف كود ميت فقط، بدون تغيير منطق التداول أو اختيار العقود.
"""
from __future__ import annotations
import math, time, threading, uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from ib_insync import IB, Stock, Index, Option, LimitOrder, MarketOrder

TRUE_INDICES = {"SPX", "XSP", "NDX", "VIX", "RUT"}

# نطاق عقد ثابت ومطلوب للتنفيذ الحقيقي: 70$ إلى 160$ فقط
CONTRACT_COST_MIN = 70.0
CONTRACT_COST_MAX = 160.0

# ══════════════════════════════════════════════════════════════════
# Diagnostic latency tracker — logging only, zero behavior change
# ══════════════════════════════════════════════════════════════════

class _ExecLatencyTracker:
    """Rolling per-category timing accumulator. Thread-safe. Diagnostic only."""
    _KEEP = 200

    def __init__(self):
        self._data: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def record(self, category: str, ms: float) -> None:
        with self._lock:
            if category not in self._data:
                self._data[category] = []
            self._data[category].append(ms)
            if len(self._data[category]) > self._KEEP:
                self._data[category].pop(0)

    def report_lines(self) -> List[str]:
        from datetime import datetime as _dt
        bar = "=" * 72
        lines = [
            bar,
            f"[EXEC LATENCY REPORT] {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
            bar,
            f"  {'Category':<34}  {'n':>4}  {'best':>8}  {'avg':>8}  {'worst':>8}",
            "-" * 72,
        ]
        def _f(v: float) -> str:
            return f"{v:6.0f}ms" if v < 10_000 else f"{v/1000:5.1f}s  "
        with self._lock:
            for cat, samples in sorted(self._data.items()):
                if not samples:
                    continue
                n = len(samples)
                lines.append(
                    f"  {cat:<34}  {n:>4}  {_f(min(samples))}  "
                    f"{_f(sum(samples)/n)}  {_f(max(samples))}"
                )
        lines.append(bar)
        return lines


_EXEC_LAT = _ExecLatencyTracker()   # module-level singleton
_exec_fill_count: int = 0            # counts fills for periodic report trigger

# Cross-file signal timestamp registry (bridge writes here before dispatch)
_signal_origin_ts: Dict[str, float] = {}

def register_signal_ts(symbol: str, ts: float) -> None:
    """Called by smart_analyzer_bridge before dispatch for end-to-end latency."""
    _signal_origin_ts[symbol.upper()] = ts


# ── نطاق التكلفة الديناميكي بحسب سعر الأصل ─────────────────────────
def _calc_cost_range(stock_price: float) -> Tuple[float, float]:
    """
    نطاق تكلفة العقد الثابت: $70 - $160 بغض النظر عن سعر الأصل.
    """
    return CONTRACT_COST_MIN, CONTRACT_COST_MAX

# ── run_in_ib_thread يُسجَّل من trading_app ──────────────────────────
_ib_fn = None

def set_ib_thread_fn(fn):
    global _ib_fn
    _ib_fn = fn

def _ib(func, *args, **kwargs):
    if _ib_fn:
        return _ib_fn(func, *args, **kwargs)
    try:
        return func(*args, **kwargs)
    except Exception as e:
        print(f"⚠ ib: {e}")
        return None

def _v(x) -> bool:
    try:
        f = float(x)
        return not math.isnan(f) and not math.isinf(f) and f > 0
    except Exception:
        return False

def _ticker_price(tk) -> Optional[float]:
    for attr in ("last", "ask", "close", "bid"):
        v = getattr(tk, attr, None)
        if _v(v):
            return float(v)
    return None


# ══════════════════════════════════════════════════════════════════════
# الإعدادات
# ══════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionConfig:
    dry_run:                bool  = False
    min_signal_pct:         int   = 62      # ↓ خُفض من 65 للحصول على صفقات أكثر
    trade_pct:              float = 0.40    # ↓ 40% لكل صفقة (من 50%) — حماية عند 3 صفقات مفتوحة
    daily_loss_pct:         float = 0.10
    max_open_trades:        int   = 3       # ↑ رُفع من 2 إلى 3 صفقات مفتوحة
    max_daily_trades:       int   = 20
    max_consecutive_losses: int   = 3
    max_contracts:          int   = 1
    min_contract_cost:      float = CONTRACT_COST_MIN  # $70 — أدنى تكلفة للعقد
    max_contract_cost:      float = CONTRACT_COST_MAX  # $160 — أقصى تكلفة للعقد
    max_spread_pct:         float = 40.0   # ✅ خُفض من 70 إلى 40 — spread أضيق = سيولة أفضل
    stop_loss_pct:          float = 0.50   # -50% من premium
    take_profit_1_pct:      float = 0.80   # +80% من premium  (R:R=2.3:1)
    take_profit_2_pct:      float = 2.00   # +200% من premium (للترند القوي)
    # ── Trailing Stop 8% Ratchet ──────────────────────────────
    # كل ما ارتفع الأوبشن 8% من آخر نقطة trail → يتحرك SL
    trail_step_pct:         float = 0.08   # 8%  — خطوة التحريك
    trail_floor_pct:        float = 0.85   # 85% — SL عند 85% من highest


# ══════════════════════════════════════════════════════════════════════
# إدارة المخاطر
# ══════════════════════════════════════════════════════════════════════

class RiskLedger:
    def __init__(self):
        self._lock              = threading.Lock()
        self.daily_pnl          = 0.0
        self.daily_trades       = 0
        self.open_trades        = 0
        self.consecutive_losses = 0
        self._date              = datetime.now().date()

    def _reset_if_new_day(self):
        today = datetime.now().date()
        if today != self._date:
            self.daily_pnl          = 0.0
            self.daily_trades       = 0
            self.open_trades        = 0   # ✅ إصلاح: يُصفَّر مع بداية اليوم
            self.consecutive_losses = 0
            self._date              = today

    def can_open(self, balance: float, cfg: ExecutionConfig) -> Tuple[bool, str]:
        with self._lock:
            self._reset_if_new_day()
            if self.consecutive_losses >= cfg.max_consecutive_losses:
                return False, f"وصلنا {self.consecutive_losses} خسائر متتالية"
            if self.daily_trades >= cfg.max_daily_trades:
                return False, f"وصلنا حد {cfg.max_daily_trades} صفقة يومياً"
            if self.open_trades >= cfg.max_open_trades:
                return False, f"وصلنا حد {cfg.max_open_trades} صفقات مفتوحة"
            if balance > 0 and self.daily_pnl <= -(balance * cfg.daily_loss_pct):
                return False, f"وصلنا حد الخسارة اليومية ${balance * cfg.daily_loss_pct:,.0f}"
            return True, "ok"

    def on_open(self):
        with self._lock:
            self._reset_if_new_day()
            self.open_trades  += 1
            self.daily_trades += 1

    def on_close(self, pnl: float):
        with self._lock:
            self.daily_pnl         += pnl
            self.open_trades        = max(0, self.open_trades - 1)  # ✅ صح: -1 فقط بدون len()
            self.consecutive_losses = (self.consecutive_losses + 1) if pnl < 0 else 0

    def sync_open_trades(self, actual_count: int):
        """✅ مزامنة العداد مع العدد الفعلي للصفقات — يُستدعى عند أي تعارض"""
        with self._lock:
            self.open_trades = actual_count

    def summary(self) -> str:
        return (f"PnL=${self.daily_pnl:+.2f} | "
                f"مفتوحة={self.open_trades} | "
                f"يومي={self.daily_trades} | "
                f"خسائر={self.consecutive_losses}")


# ══════════════════════════════════════════════════════════════════════
# محرك التنفيذ
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# تقييم Greeks وجودة العقد + Contract Efficiency Score
# ══════════════════════════════════════════════════════════════════════

@dataclass
class ContractScore:
    """
    نتيجة تقييم عقد واحد.

    ⚠️ تعريف الـ score:
        quality_score = execution quality ranking (0→1)
        ≠ احتمال نجاح الصفقة
        ≠ نسبة ربح متوقعة
        0.80 يعني "هذا العقد أفضل تنفيذياً من 0.50"

    Normalization:
        كل sub-score محسوب: max(0, 1 - distance_from_ideal / range)
        حتى لا يسيطر عامل واحد بسبب اختلاف الـ ranges.
    """
    strike:      float
    expiry:      str
    dte:         int
    premium:     float

    # Greeks snapshot
    delta:       Optional[float] = None
    gamma:       Optional[float] = None
    theta:       Optional[float] = None
    vega:        Optional[float] = None
    iv:          Optional[float] = None
    spread_pct:  Optional[float] = None
    bid:         Optional[float] = None
    ask:         Optional[float] = None

    # Execution Quality Scores — normalized 0→1
    score_total:   float = 0.0
    score_delta:   float = 0.0
    score_theta:   float = 0.0
    score_spread:  float = 0.0
    score_iv:      float = 0.0
    score_gamma:   float = 0.0
    score_dte:     float = 0.0

    # Hard Filters
    hard_pass:   bool  = True
    hard_reason: str   = ""

    # Rank
    rank:        int   = 0
    rank_of:     int   = 0

    # Outcome — يُملأ بعد إغلاق الصفقة للـ research
    realized_pnl:    Optional[float] = None   # PnL فعلي بالدولار
    realized_pnl_pct:Optional[float] = None   # PnL كنسبة من البريميوم
    mfe:             Optional[float] = None   # Maximum Favorable Excursion
    mae:             Optional[float] = None   # Maximum Adverse Excursion
    holding_minutes: Optional[int]   = None   # وقت الاحتفاظ بالدقائق
    exit_reason:     Optional[str]   = None   # SL / TP1 / TP2 / TIMEOUT

    def log_line(self) -> str:
        g = ""
        if self.delta      is not None: g += f"Δ={self.delta:.2f} "
        if self.theta      is not None: g += f"θ={self.theta:.3f} "
        if self.gamma      is not None: g += f"γ={self.gamma:.4f} "
        if self.iv         is not None: g += f"IV={self.iv*100:.0f}% "
        if self.spread_pct is not None: g += f"Sprd={self.spread_pct*100:.1f}% "
        sub = (f"[Δ:{self.score_delta:.2f} θ:{self.score_theta:.2f} "
               f"S:{self.score_spread:.2f} IV:{self.score_iv:.2f} γ:{self.score_gamma:.2f}]")
        return (
            f"  [#{self.rank}/{self.rank_of}] "
            f"strike={self.strike} DTE={self.dte} prem=${self.premium:.2f} | "
            f"{g}| quality={self.score_total:.3f} {sub} "
            f"{'✅' if self.hard_pass else f'❌({self.hard_reason})'}"
        )

    def to_dict(self) -> dict:
        return {
            # تعريف صريح لمنع سوء الفهم
            "_score_meaning": "execution_quality [0→1], NOT win_probability",
            "_normalization":  "each sub-score: max(0, 1 - dist_from_ideal / range)",
            # العقد
            "strike":       self.strike,
            "expiry":       self.expiry,
            "dte":          self.dte,
            "premium":      self.premium,
            # Greeks snapshot
            "delta":        self.delta,
            "gamma":        self.gamma,
            "theta":        self.theta,
            "vega":         self.vega,
            "iv_pct":       round(self.iv * 100, 1) if self.iv else None,
            "spread_pct":   round(self.spread_pct * 100, 2) if self.spread_pct else None,
            "bid":          self.bid,
            "ask":          self.ask,
            # Scores
            "quality_score":  round(self.score_total,  4),
            "quality_delta":  round(self.score_delta,  4),
            "quality_theta":  round(self.score_theta,  4),
            "quality_spread": round(self.score_spread, 4),
            "quality_iv":     round(self.score_iv,     4),
            "quality_gamma":  round(self.score_gamma,  4),
            "hard_pass":      self.hard_pass,
            "hard_reason":    self.hard_reason,
            "rank":           self.rank,
            "rank_of":        self.rank_of,
            # Outcome (يُملأ بعد الإغلاق)
            "realized_pnl":     self.realized_pnl,
            "realized_pnl_pct": self.realized_pnl_pct,
            "mfe":              self.mfe,
            "mae":              self.mae,
            "holding_minutes":  self.holding_minutes,
            "exit_reason":      self.exit_reason,
        }


class GreeksEvaluator:
    """
    يجلب Greeks ويُقيّم جودة العقد.

    منطقان متكاملان:
    ─────────────────────────────────────────────────────────
    1. Hard Filters: فلاتر صارمة (pass/fail) — إذا فشل يُحذف
    2. Contract Efficiency Score: تقييم مرجّح للعقود الباقية
       → نختار الأعلى score وليس أول عقد يمر

    Feature Flags: كل شيء قابل للتعطيل بـ False
    Configurable Weights: غيّر الأوزان حسب نتائج التشغيل الحي
    ─────────────────────────────────────────────────────────
    """

    # ══════════════════════════════════════════════════════
    # Feature Flags — عطّل بـ False بدون أي تعديل آخر
    # ══════════════════════════════════════════════════════
    ENABLE_GREEKS_FETCH    = False  # لا تجلب Greeks قبل التنفيذ
    ENABLE_HARD_FILTERS    = True   # فلاتر صارمة pass/fail
    ENABLE_SCORING         = True   # Contract Efficiency Score
    ENABLE_DELTA_FILTER    = True
    ENABLE_THETA_FILTER    = True
    ENABLE_SPREAD_FILTER   = True
    ENABLE_IV_FILTER       = True
    ENABLE_DTE_FILTER      = True

    # ══════════════════════════════════════════════════════
    # Hard Filter Limits
    # ══════════════════════════════════════════════════════
    DELTA_MIN      = 0.20   # ↓ تخفيف لتغطية الأسهم الغالية (كـSPY)
    DELTA_MAX      = 0.60   # ↑ توسيع للسماح بـATM قريب
    THETA_MAX_PCT  = 0.03   # theta / premium يومياً
    SPREAD_MAX_PCT = 0.15   # (ask-bid) / mid
    IV_MAX         = 0.80   # فوق هذا = خطر IV crush
    DTE_MIN        = 0
    DTE_MAX        = 2

    # ══════════════════════════════════════════════════════
    # Scoring Weights (المجموع = 1.0)
    # قابلة للتعديل حسب نتائج التشغيل الحي
    # ══════════════════════════════════════════════════════
    W_DELTA  = 0.35   # أهم: responsiveness
    W_THETA  = 0.25   # decay cost
    W_SPREAD = 0.20   # execution quality
    W_IV     = 0.15   # IV context
    W_GAMMA  = 0.05   # weight صغير — بيانات غير كافية للتأكيد

    # ══════════════════════════════════════════════════════
    # Minimum Score للقبول (حتى لو Signal ممتاز)
    # إذا كل العقود أقل من هذا — نختار الأفضل بدون رفض الصفقة
    # ══════════════════════════════════════════════════════
    MIN_SCORE_TO_REJECT = 0.20   # أقل من هذا = اختار الأفضل المتاح
    # إذا كل العقود أقل من هذا = ارفض الصفقة كاملاً
    HARD_REJECT_SCORE   = 0.05

    def __init__(self, ib, ib_fn=None):
        self.ib      = ib
        self._ib_fn  = ib_fn

    def get_greeks(self, contract, u_price: float) -> Optional[dict]:
        """يجلب Greeks من IBKR"""
        if not self.ENABLE_GREEKS_FETCH:
            return None
        try:
            _ib(self.ib.reqMarketDataType, 1)
            tk = _ib(self.ib.reqMktData, contract, "106", False, False)
            if not tk:
                return None

            delta = gamma = theta = vega = iv = bid = ask = None

            for _ in range(12):
                time.sleep(0.5)
                for src_name in ("modelGreeks", "bidGreeks", "askGreeks"):
                    src = getattr(tk, src_name, None)
                    if not src: continue
                    for attr, var in [("delta",delta),("gamma",gamma),
                                      ("theta",theta),("vega",vega),("impliedVol",iv)]:
                        val = getattr(src, attr, None)
                        if val is not None:
                            try:
                                f = float(val)
                                if not math.isnan(f):
                                    if attr == "delta":      delta = abs(f)
                                    elif attr == "gamma":    gamma = f
                                    elif attr == "theta":    theta = f
                                    elif attr == "vega":     vega  = f
                                    elif attr == "impliedVol": iv  = f
                            except: pass
                    if delta is not None: break
                bid = getattr(tk, "bid", None)
                ask = getattr(tk, "ask", None)
                if delta is not None: break

            try: _ib(self.ib.cancelMktData, contract)
            except: pass

            if delta is None:
                return None

            mid = None
            if _v(bid) and _v(ask):   mid = (float(bid) + float(ask)) / 2
            elif _v(ask):             mid = float(ask)
            elif _v(bid):             mid = float(bid)

            spread_pct = None
            if _v(bid) and _v(ask) and mid and mid > 0:
                spread_pct = (float(ask) - float(bid)) / mid

            return {
                "delta":      round(delta,      4) if delta      is not None else None,
                "gamma":      round(gamma,      6) if gamma      is not None else None,
                "theta":      round(theta,      4) if theta      is not None else None,
                "vega":       round(vega,       4) if vega       is not None else None,
                "iv":         round(iv,         4) if iv         is not None else None,
                "bid":        round(float(bid), 2) if _v(bid)    else None,
                "ask":        round(float(ask), 2) if _v(ask)    else None,
                "mid":        round(mid,        2) if mid        is not None else None,
                "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
            }
        except Exception as e:
            print(f"⚠ Greeks: {e}")
            return None

    def score_contract(self, greeks: Optional[dict],
                       premium: float, dte: int,
                       strike: float, expiry: str) -> ContractScore:
        """
        يحسب Contract Efficiency Score.

        كل sub-score بين 0.0 و1.0.
        Score النهائي = مجموع مرجّح.

        لا يرفض العقد هنا — فقط يحسب الـ score.
        القرار النهائي في select_best_contract().
        """
        cs = ContractScore(
            strike=strike, expiry=expiry, dte=dte, premium=premium
        )

        if greeks:
            cs.delta      = greeks.get("delta")
            cs.gamma      = greeks.get("gamma")
            cs.theta      = greeks.get("theta")
            cs.vega       = greeks.get("vega")
            cs.iv         = greeks.get("iv")
            cs.spread_pct = greeks.get("spread_pct")
            cs.bid        = greeks.get("bid")
            cs.ask        = greeks.get("ask")
            mid           = greeks.get("mid") or premium

            # ── Hard Filters ──────────────────────────────────────
            if self.ENABLE_HARD_FILTERS:
                if self.ENABLE_DELTA_FILTER and cs.delta is not None:
                    if cs.delta < self.DELTA_MIN:
                        cs.hard_pass   = False
                        cs.hard_reason = f"Delta={cs.delta:.2f}<{self.DELTA_MIN}(lottery)"
                    elif cs.delta > self.DELTA_MAX:
                        cs.hard_pass   = False
                        cs.hard_reason = f"Delta={cs.delta:.2f}>{self.DELTA_MAX}(ATM/ITM)"

                if cs.hard_pass and self.ENABLE_THETA_FILTER and cs.theta is not None and mid > 0:
                    theta_pct = abs(cs.theta) / mid
                    if theta_pct > self.THETA_MAX_PCT:
                        cs.hard_pass   = False
                        cs.hard_reason = f"Theta={theta_pct*100:.1f}%/day>{self.THETA_MAX_PCT*100:.0f}%"

                if cs.hard_pass and self.ENABLE_SPREAD_FILTER and cs.spread_pct is not None:
                    if cs.spread_pct > self.SPREAD_MAX_PCT:
                        cs.hard_pass   = False
                        cs.hard_reason = f"Spread={cs.spread_pct*100:.1f}%>{self.SPREAD_MAX_PCT*100:.0f}%"

                if cs.hard_pass and self.ENABLE_IV_FILTER and cs.iv is not None:
                    if cs.iv > self.IV_MAX:
                        cs.hard_pass   = False
                        cs.hard_reason = f"IV={cs.iv*100:.0f}%>{self.IV_MAX*100:.0f}%"

            if self.ENABLE_DTE_FILTER:
                if dte < self.DTE_MIN:
                    cs.hard_pass   = False
                    cs.hard_reason = f"DTE={dte}<{self.DTE_MIN}"
                elif dte > self.DTE_MAX:
                    cs.hard_pass   = False
                    cs.hard_reason = f"DTE={dte}>{self.DTE_MAX}"

            # ── Efficiency Scores (0→1) ────────────────────────────
            if self.ENABLE_SCORING:

                # Delta Score: 0.40 = perfect (middle of range)
                if cs.delta is not None:
                    ideal = (self.DELTA_MIN + self.DELTA_MAX) / 2   # 0.40
                    dist  = abs(cs.delta - ideal) / (self.DELTA_MAX - self.DELTA_MIN)
                    cs.score_delta = max(0.0, 1.0 - dist * 2)
                else:
                    cs.score_delta = 0.5   # لا معلومات = وسط

                # Theta Score: أقل decay = أحسن
                if cs.theta is not None and mid > 0:
                    theta_pct = abs(cs.theta) / mid
                    # 0% decay = 1.0 | THETA_MAX_PCT = 0.5 | ضعف = 0.0
                    cs.score_theta = max(0.0, 1.0 - (theta_pct / self.THETA_MAX_PCT))
                else:
                    cs.score_theta = 0.5

                # Spread Score: أضيق = أحسن
                if cs.spread_pct is not None:
                    cs.score_spread = max(0.0, 1.0 - cs.spread_pct / self.SPREAD_MAX_PCT)
                else:
                    cs.score_spread = 0.3   # لا معلومات = عقاب خفيف

                # IV Score: أقل IV = أحسن (تجنب crush)
                if cs.iv is not None:
                    cs.score_iv = max(0.0, 1.0 - cs.iv / self.IV_MAX)
                else:
                    cs.score_iv = 0.5

                # Gamma Score: وسط = أحسن (لا lottery ولا sluggish)
                # بيانات غير كافية للتحقق → weight صغير جداً
                if cs.gamma is not None and premium > 0:
                    gamma_ratio = cs.gamma / premium
                    # 0.01-0.05 نسبة gamma/premium = ideal range (تقدير)
                    if 0.01 <= gamma_ratio <= 0.05:
                        cs.score_gamma = 1.0
                    elif gamma_ratio < 0.01:
                        cs.score_gamma = gamma_ratio / 0.01
                    else:
                        cs.score_gamma = max(0.0, 1.0 - (gamma_ratio - 0.05) / 0.05)
                else:
                    cs.score_gamma = 0.5

                # المجموع المرجّح
                cs.score_total = (
                    cs.score_delta  * self.W_DELTA  +
                    cs.score_theta  * self.W_THETA  +
                    cs.score_spread * self.W_SPREAD +
                    cs.score_iv     * self.W_IV     +
                    cs.score_gamma  * self.W_GAMMA
                )
                cs.score_total = round(min(1.0, cs.score_total), 4)

        else:
            # لا Greeks — DTE filter فقط
            if self.ENABLE_DTE_FILTER:
                if dte < self.DTE_MIN or dte > self.DTE_MAX:
                    cs.hard_pass   = False
                    cs.hard_reason = f"DTE={dte} خارج النطاق"
            cs.score_total = 0.3   # بدون Greeks = score منخفض لكن غير صفر

        return cs

    def select_best_contract(self,
                             candidates: List[dict],
                             u_price: float,
                             log_fn=None) -> Tuple[Optional[dict], Optional[ContractScore]]:
        """
        يجلب Greeks لكل عقد، يحسب Score، يختار الأفضل.

        candidates: قائمة من {'contract', 'premium', 'strike', 'expiry', 'dte'}

        المنطق:
        1. جلب Greeks لكل عقد
        2. حساب Score
        3. فصل: passed_hard (اجتاز الفلاتر الصارمة) و failed_hard
        4. إذا في passed_hard → اختر الأعلى score
        5. إذا كل شيء فشل hard لكن في عقود بدون Greeks → اختر الأعلى score
        6. إذا كل شيء score < HARD_REJECT_SCORE → None (رفض الصفقة)

        لا تقتل الصفقة إلا إذا كانت العقود سيئة جداً.
        """
        def _log(msg):
            if log_fn: log_fn(msg)

        if not candidates:
            return None, None

        scored: List[Tuple[ContractScore, dict]] = []

        _log(f"    [Scoring] تقييم سريع بدون Greeks قبل التنفيذ: {len(candidates)} عقد...")

        for cand in candidates:
            # لا نجلب Greeks هنا حتى لا يتأخر التنفيذ.
            cs = self.score_contract(
                greeks   = None,
                premium  = cand["premium"],
                dte      = cand["dte"],
                strike   = cand["strike"],
                expiry   = cand["expiry"],
            )
            # أولوية تنفيذية: أضيق spread ثم أقرب ATM إن توفرت.
            scored.append((cs, cand))

        # رتّب: الاجتياز أولاً، ثم Score تنازلياً
        scored.sort(key=lambda x: (x[0].hard_pass, x[0].score_total), reverse=True)

        # أضف rank
        for i, (cs, _) in enumerate(scored):
            cs.rank    = i + 1
            cs.rank_of = len(scored)

        # log كامل لكل العقود
        _log(f"    [Scoring] النتائج ({len(scored)} عقد):")
        for cs, _ in scored:
            _log(cs.log_line())

        # اختيار الأفضل
        passed  = [(cs, c) for cs, c in scored if cs.hard_pass]
        best_cs, best_cand = scored[0]   # الأعلى score بغض النظر

        if passed:
            best_cs, best_cand = passed[0]
            _log(
                f"    [Scoring] ✅ اختيار: strike={best_cs.strike} "
                f"score={best_cs.score_total:.3f} (#{best_cs.rank} من {best_cs.rank_of} | اجتاز الفلاتر)"
            )
        elif best_cs.score_total >= self.MIN_SCORE_TO_REJECT:
            _log(
                f"    [Scoring] ⚠️ كل العقود فشلت Hard Filters — "
                f"اختيار الأفضل المتاح: strike={best_cs.strike} "
                f"score={best_cs.score_total:.3f}"
            )
        elif best_cs.score_total < self.HARD_REJECT_SCORE:
            _log(
                f"    [Scoring] ❌ كل العقود score < {self.HARD_REJECT_SCORE} — "
                f"رفض الصفقة كاملاً (Signal جيد لكن لا عقد مناسب)"
            )
            return None, None
        else:
            _log(
                f"    [Scoring] ⚠️ أفضل متاح (score={best_cs.score_total:.3f}): "
                f"strike={best_cs.strike}"
            )

        # أضف Greeks snapshot + كل العقود للـ research
        best_cand = dict(best_cand)
        best_cand["greeks"]         = best_cs.to_dict()
        best_cand["contract_score"] = best_cs
        best_cand["all_scored_contracts"] = [
            {
                "rank":          cs.rank,
                "strike":        cs.strike,
                "dte":           cs.dte,
                "premium":       cs.premium,
                "quality_score": cs.score_total,
                "hard_pass":     cs.hard_pass,
                "hard_reason":   cs.hard_reason,
                "delta":         cs.delta,
                "theta":         cs.theta,
                "iv_pct":        round(cs.iv*100,1) if cs.iv else None,
                "spread_pct":    round(cs.spread_pct*100,2) if cs.spread_pct else None,
            }
            for cs, _ in scored
        ]
        return best_cand, best_cs


# ══════════════════════════════════════════════════════════════════════
# Trade Context Tags — للـ research اللاحق
# ══════════════════════════════════════════════════════════════════════

def build_trade_context(symbol: str,
                        direction: str,
                        signal_pct: int,
                        regime: str = "",
                        tf_alignment: str = "") -> dict:
    """
    يبني context tags لكل صفقة.
    يُحفظ وقت الدخول — يُربط لاحقاً مع outcome.

    لا تحليل هنا — فقط تصنيف موضوعي.
    """
    from datetime import datetime, timezone

    now_utc = datetime.now(timezone.utc)
    hour    = now_utc.hour
    minute  = now_utc.minute
    total_m = hour * 60 + minute

    # ── Session (بتوقيت ET = UTC-4 في الصيف / UTC-5 في الشتاء) ──
    # تقريب: نستخدم UTC - 4
    et_hour = (hour - 4) % 24
    et_min  = et_hour * 60 + minute

    if   360 <= et_min <  390:  session = "pre_market_early"   # 6:00-6:30 ET
    elif 390 <= et_min <  570:  session = "pre_market"         # 6:30-9:30 ET
    elif 570 <= et_min <  630:  session = "ny_open"            # 9:30-10:30 ET
    elif 630 <= et_min <  720:  session = "mid_morning"        # 10:30-12:00 ET
    elif 720 <= et_min <  780:  session = "lunch"              # 12:00-13:00 ET
    elif 780 <= et_min <  900:  session = "afternoon"          # 13:00-15:00 ET
    elif 900 <= et_min <  960:  session = "power_hour"         # 15:00-16:00 ET
    elif 960 <= et_min < 1200:  session = "after_hours"        # 16:00-20:00 ET
    else:                       session = "overnight"

    # ── Day of Week ──────────────────────────────────────────────
    day_name = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][now_utc.weekday()]

    # ── Symbol Category ───────────────────────────────────────────
    _indices  = {"SPX","XSP","NDX","VIX","RUT","SPY","QQQ","IWM"}
    _semis    = {"NVDA","AMD","AVGO","QCOM","INTC","MU","AMAT"}
    _mag7     = {"AAPL","MSFT","GOOGL","AMZN","META","TSLA"}
    _financials = {"JPM","GS","BAC","V","MA","WFC"}

    sym = symbol.upper()
    if sym in _indices:    sym_cat = "index_etf"
    elif sym in _mag7:     sym_cat = "mag7"
    elif sym in _semis:    sym_cat = "semiconductors"
    elif sym in _financials: sym_cat = "financials"
    else:                  sym_cat = "other"

    # ── Signal Strength ───────────────────────────────────────────
    if   signal_pct >= 80: sig_strength = "strong"
    elif signal_pct >= 65: sig_strength = "medium"
    else:                  sig_strength = "weak"

    # ── Regime (من المحلل إذا متوفر) ─────────────────────────────
    regime_tag = regime if regime else "unknown"

    # ── TF Alignment ──────────────────────────────────────────────
    align_tag  = tf_alignment if tf_alignment else "unknown"

    return {
        # الوقت
        "timestamp_utc":   now_utc.isoformat(),
        "session":         session,
        "day_of_week":     day_name,
        "et_hour":         et_hour,
        # الرمز
        "symbol":          sym,
        "symbol_category": sym_cat,
        "direction":       direction,
        # الإشارة
        "signal_pct":      signal_pct,
        "signal_strength": sig_strength,
        # السوق
        "regime":          regime_tag,
        "tf_alignment":    align_tag,
        # للـ research — يُملأ لاحقاً
        "_outcome_linked": False,
    }


# ══════════════════════════════════════════════════════════════════════
# Execution Safety Layer
# ══════════════════════════════════════════════════════════════════════

# ── Strategy Freeze ───────────────────────────────────────────────────
# بعد بدء التشغيل: لا تغيّر weights أو filters حتى تصل 50 صفقة
STRATEGY_FROZEN    = True    # ← False فقط للتطوير
FREEZE_MIN_TRADES  = 50      # عدد الصفقات قبل أي تعديل

# ── Conservative Startup ──────────────────────────────────────────────
STARTUP_MODE       = True    # أصغر sizing + أقل مخاطرة
STARTUP_MAX_COST   = CONTRACT_COST_MAX  # $160 — لا يضيّق نطاق العقد المطلوب 70-160

# ── Stale Quote ───────────────────────────────────────────────────────
STALE_QUOTE_SEC    = 30      # رفض سعر عمره أكثر من 30 ثانية

# ── Slippage Tracking ──────────────────────────────────────────────────
# يُحسب: (actual_fill - expected_fill) / expected_fill
# يُحفظ في كل صفقة للـ research


class SafetyMonitor:
    """
    مراقب الأمان — يعمل بشكل مستقل عن منطق التداول.

    المهام:
    1. Emergency Flatten: إغلاق كل الصفقات فوراً
    2. Stale Quote Detection: رفض الأسعار القديمة
    3. IBKR Disconnect Detection: إيقاف التداول عند انقطاع الاتصال
    4. Fill Latency Logging: تسجيل زمن التنفيذ
    5. KPI Tracking: EV/trade, slippage, drawdown
    """

    def __init__(self):
        self._emergency_stop   = False
        self._fill_times: List[float] = []
        self._slippages:  List[float] = []
        self._ev_per_trade: List[float] = []

    # ── Emergency Flatten ────────────────────────────────────────
    def trigger_emergency_stop(self, reason: str):
        self._emergency_stop = True
        print(f"🚨 EMERGENCY STOP: {reason}")

    def is_stopped(self) -> bool:
        return self._emergency_stop

    def reset_emergency(self):
        """يُستدعى يدوياً فقط بعد مراجعة السبب"""
        self._emergency_stop = False
        print("⚠️ Emergency stop reset — تأكد من مراجعة السبب أولاً")

    # ── Stale Quote ───────────────────────────────────────────────
    def is_stale(self, quote_time: float) -> bool:
        """quote_time = time.time() وقت آخر تحديث للسعر"""
        age = time.time() - quote_time
        if age > STALE_QUOTE_SEC:
            print(f"⚠️ Stale quote: {age:.0f}s > {STALE_QUOTE_SEC}s — رفض")
            return True
        return False

    # ── Fill Latency ──────────────────────────────────────────────
    def record_fill(self, expected: float, actual: float,
                    fill_time_sec: float, spread_at_entry: float = 0):
        """يُسجّل تفاصيل كل تنفيذ"""
        slippage = (actual - expected) / expected if expected > 0 else 0
        self._fill_times.append(fill_time_sec)
        self._slippages.append(slippage)

        print(
            f"  📋 Fill: expected=${expected:.2f} actual=${actual:.2f} | "
            f"slippage={slippage*100:+.2f}% | "
            f"latency={fill_time_sec:.1f}s | "
            f"spread_entry={spread_at_entry*100:.1f}%"
        )
        return {
            "expected_fill":   round(expected, 4),
            "actual_fill":     round(actual,   4),
            "slippage_pct":    round(slippage * 100, 3),
            "fill_latency_sec":round(fill_time_sec, 2),
            "spread_at_entry": round(spread_at_entry * 100, 3) if spread_at_entry else None,
        }

    # ── KPI Tracking ──────────────────────────────────────────────
    def record_outcome(self, pnl_pct: float):
        self._ev_per_trade.append(pnl_pct)

    def kpi_summary(self) -> dict:
        if not self._ev_per_trade:
            return {"status": "لا بيانات كافية"}

        n      = len(self._ev_per_trade)
        wins   = [x for x in self._ev_per_trade if x > 0]
        losses = [x for x in self._ev_per_trade if x <= 0]
        ev     = sum(self._ev_per_trade) / n

        # Drawdown
        cumulative = 0; peak = 0; max_dd = 0
        for pnl in self._ev_per_trade:
            cumulative += pnl
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)

        avg_slip = sum(self._slippages) / len(self._slippages) if self._slippages else 0
        avg_lat  = sum(self._fill_times) / len(self._fill_times) if self._fill_times else 0

        frozen_status = (
            f"مجمّد — انتظر {max(0, FREEZE_MIN_TRADES - n)} صفقة أخرى"
            if STRATEGY_FROZEN and n < FREEZE_MIN_TRADES
            else "جاهز للمراجعة" if n >= FREEZE_MIN_TRADES
            else "مجمّد"
        )

        return {
            # KPIs الأساسية
            "n_trades":       n,
            "ev_per_trade":   round(ev, 2),
            "win_rate":       round(len(wins)/n*100, 1) if n else 0,
            "avg_win":        round(sum(wins)/len(wins), 2) if wins else 0,
            "avg_loss":       round(sum(losses)/len(losses), 2) if losses else 0,
            "max_drawdown":   round(max_dd, 2),
            # Execution Quality
            "avg_slippage":   round(avg_slip * 100, 3),
            "avg_fill_latency": round(avg_lat, 1),
            # Strategy Freeze Status
            "strategy_frozen": STRATEGY_FROZEN,
            "freeze_status":   frozen_status,
            "note": "EV/trade و score↔pnl correlation أهم من WR وحده",
        }

    # ── IBKR Disconnect ───────────────────────────────────────────
    @staticmethod
    def check_ibkr(ib) -> bool:
        """True = متصل | False = منقطع"""
        try:
            return getattr(ib, "isConnected", lambda: False)()
        except Exception:
            return False

    def validate_before_trade(self, ib, balance: float) -> Tuple[bool, str]:
        """
        تحقق شامل قبل أي تنفيذ.
        يُستدعى في execute_signal قبل كل شيء.
        """
        if self._emergency_stop:
            return False, "🚨 Emergency Stop مفعّل — راجع السبب وأعد التشغيل يدوياً"

        if not self.check_ibkr(ib):
            return False, "❌ IBKR غير متصل — انتظار إعادة الاتصال"

        if balance <= 0:
            return False, "❌ رصيد = 0"

        if STARTUP_MODE:
            pass   # Startup Mode: sizing محافظ فقط، لا حد أدنى للرصيد

        return True, "ok"


class ExecutionEngine:

    def __init__(self, ib: IB, config: Optional[ExecutionConfig] = None):
        self.ib                 = ib
        self.cfg                = config or ExecutionConfig()
        self.ledger             = RiskLedger()
        self._lock              = threading.Lock()
        self.open_positions:    Dict[str, dict] = {}
        self.closed_positions:  Dict[str, dict] = {}
        self._executing:        set = set()
        self._log_fn            = None
        self.last_reject_reason = ""
        self.last_decision      = ""
        # ── الرصيد يُمرَّر من trading_app مباشرة ──
        self.balance            = 0.0
        # ── Safety Monitor ──
        self.safety             = SafetyMonitor()
        # ── Greeks Evaluator ──
        self.greeks_eval        = GreeksEvaluator(ib, ib_fn=_ib_fn)
        # Feature Flag: عطّل Greeks بـ False للرجوع لـ price-only mode
        self.ENABLE_GREEKS      = False  # Clean fast execution: Greeks بعد الصفقة فقط، لا قبل الإرسال
        # ── Caches ──
        self._chain_cache: Dict[str, Tuple[float, dict]]  = {}   # sym → (ts, data)
        self._quote_cache: Dict[str, Tuple[float, dict]]  = {}   # key → (ts, quote)
        self._qualified_cache: Dict[str, Tuple[float, int]] = {}  # key → (ts, conId) TTL 24h
        self._CHAIN_TTL     = 1800   # 30 دقيقة
        self._QUOTE_TTL     = 5      # 5 ثواني فقط
        self._QUALIFIED_TTL = 86400  # يوم كامل
        # ── Ready-contracts cache: مُعدّ مسبقاً من prefetch_loop ──────
        self._ready_contracts: Dict[str, Dict[str, dict]] = {}
        self._READY_TTL = 90
        # ── Per-symbol locks: تمنع الاستدعاءات المتزامنة لنفس الرمز ──
        self._sym_locks: Dict[str, threading.Lock] = {}
        # ── flag: هل اشتراكات بيانات السوق متاحة؟ (False = استخدام fallback مباشرة) ──
        self._market_data_available: bool = True
        # ── cache سعر الأصل: sym → (ts, price) TTL 60 ثانية ──
        self._price_cache: Dict[str, Tuple[float, float]] = {}
        self._PRICE_TTL = 60

    def set_log_fn(self, fn):
        self._log_fn = fn

    def _log(self, msg: str):
        self.last_decision = msg
        if self._log_fn:
            try: self._log_fn(msg)
            except Exception: pass
        else:
            print(msg)

    def _reject(self, msg: str) -> None:
        self.last_reject_reason = msg
        self._log(msg)
        print(f"[REJECT] {msg}")

    # ─── نقطة الدخول الرئيسية ────────────────────────────────────

    def execute_signal(self, symbol: str, direction: str, pct: int,
                       balance: float = 0.0,
                       sl_price: float = 0.0,
                       tp1_price: float = 0.0,
                       tp2_price: float = 0.0,
                       entry_stock_price: float = 0.0) -> Optional[str]:
        """
        balance, sl_price, tp1_price, tp2_price يُمرَّران من trading_app.
        sl/tp أسعار السهم من المحلل — يُحوَّلان لنسب من premium عند التنفيذ.
        """
        _t0_exec = time.time()   # [DIAG] measurement A: total execute_signal time
        self.last_reject_reason = ""
        sym       = symbol.upper().strip()
        direction = direction.upper().strip()
        right     = "C" if direction == "CALL" else "P"

        # ① Safety Validation — أول شيء قبل أي منطق
        _bal_now = balance if balance > 0 else self.balance
        _safe_ok, _safe_reason = self.safety.validate_before_trade(self.ib, _bal_now)
        if not _safe_ok:
            self._reject(f"🛡️ Safety: {_safe_reason}")
            return None

        # ① حفظ الرصيد والأسعار
        if balance > 0:
            self.balance = balance

        # ② قوة الإشارة
        if pct < self.cfg.min_signal_pct:
            self._reject(f"⏭ {sym}: إشارة ضعيفة ({pct}% < {self.cfg.min_signal_pct}%)")
            return None

        # ③ منع التكرار
        with self._lock:
            if sym in self._executing:
                self._reject(f"⏭ {sym}: قيد التنفيذ — تجاهل")
                return None
            if self._has_open(sym):
                self._reject(f"⏭ {sym}: مفتوح بالفعل")
                return None
            self._executing.add(sym)

        # بناء Context Tags قبل التنفيذ
        _regime = ""
        _align  = ""
        try:
            _ctx = build_trade_context(sym, direction, pct,
                                       regime=_regime, tf_alignment=_align)
        except Exception:
            _ctx = {}

        try:
            return self._run(sym, direction, right, pct,
                             sl_price, tp1_price, tp2_price, entry_stock_price,
                             trade_context=_ctx)
        finally:
            with self._lock:
                self._executing.discard(sym)
            # [DIAG] Measurement A: total execute_signal time
            global _exec_fill_count
            _exec_ms = (time.time() - _t0_exec) * 1000
            _EXEC_LAT.record("A_execute_signal_ms", _exec_ms)
            self._log(f"[LATENCY] {sym}: execute_signal_total={_exec_ms:.0f}ms")
            # [DIAG] Measurement Z: end-to-end from analyzer to here
            _origin = _signal_origin_ts.pop(sym, None)
            if _origin:
                _e2e_ms = (time.time() - _origin) * 1000
                _EXEC_LAT.record("Z_signal_to_exec_end_ms", _e2e_ms)
                self._log(f"[LATENCY] {sym}: END-TO-END signal→exec_complete={_e2e_ms:.0f}ms")
            _exec_fill_count += 1
            if _exec_fill_count % 5 == 0:
                for _line in _EXEC_LAT.report_lines():
                    self._log(_line)

    def _run(self, sym: str, direction: str, right: str, pct: int,
             sl_price: float = 0.0, tp1_price: float = 0.0,
             tp2_price: float = 0.0, entry_stock_price: float = 0.0,
             trade_context: Optional[dict] = None) -> Optional[str]:

        # ④ تحقق من الرصيد
        bal = self.balance
        if bal <= 0:
            self._reject(f"❌ {sym}: الرصيد = 0 — تأكد من اتصال IBKR")
            return None

        self._log(f"💰 {sym}: رصيد=${bal:,.2f}")

        # ⑤ إدارة المخاطر — مزامنة العداد مع الواقع أولاً
        self.ledger.sync_open_trades(len(self.open_positions))  # ✅ يمنع "صفقات وهمية"
        ok, reason = self.ledger.can_open(bal, self.cfg)
        if not ok:
            self._reject(f"⛔ {sym}: {reason}")
            return None

        # ⑥ الأصل
        underlying = self._make_underlying(sym)
        _t_qualify = time.time()   # [DIAG] 5a: qualify underlying
        q = _ib(self.ib.qualifyContracts, underlying)
        _EXEC_LAT.record("5a_ibkr_qualify_underlying_ms", (time.time()-_t_qualify)*1000)
        self._log(f"[LATENCY] {sym}: qualify_underlying={( time.time()-_t_qualify)*1000:.0f}ms")
        if not q:
            self._reject(f"❌ {sym}: qualify فشل")
            return None
        underlying = q[0]

        # ⑦ سعر الأصل (مع cache 60 ثانية لتسريع الطلبات المتكررة)
        _pc = self._price_cache.get(sym)
        if _pc and time.time() - _pc[0] < self._PRICE_TTL:
            u_price = _pc[1]
            self._log(f"📈 {sym}: ${u_price:.2f} (من كاش)")
        else:
            _t_price = time.time()   # [DIAG] 5b: get underlying price
            u_price = self._get_price(underlying)
            _EXEC_LAT.record("5b_ibkr_get_price_ms", (time.time()-_t_price)*1000)
            self._log(f"[LATENCY] {sym}: get_price={( time.time()-_t_price)*1000:.0f}ms")
            if not u_price:
                self._reject(f"❌ {sym}: لا سعر للأصل")
                return None
            self._price_cache[sym] = (time.time(), u_price)
            self._log(f"📈 {sym}: ${u_price:.2f}")

        # ⑨ الميزانية
        # chains تُجلب داخل select_best_option_contract (مع cache 15 دقيقة)
        budget   = round(bal * self.cfg.trade_pct, 2)
        min_cost = self.cfg.min_contract_cost   # $70
        max_cost = self.cfg.max_contract_cost   # $160

        if budget < min_cost:
            self._reject(
                f"❌ {sym}: ميزانية ${budget:.0f} أقل من الحد الأدنى ${min_cost:.0f} — أضف رصيداً"
            )
            return None

        self._log(
            f"  💼 ميزانية=${budget:.0f} | سعر_أصل=${u_price:.0f} | "
            f"نطاق_عقد=${min_cost:.0f}-${max_cost:.0f}"
        )

        # ⑩ البحث عن أفضل عقد — مصدر واحد: select_best_option_contract
        cand = self.select_best_option_contract(
            symbol           = sym,
            direction        = direction,
            underlying_price = u_price,
            mode             = "live" if not self.cfg.dry_run else "paper",
        )
        if not cand:
            self._reject(f"❌ {sym}: لا عقد صالح (u_price=${u_price:.0f}) — تخطي")
            return None

        opt_c   = cand["contract"]
        # premium للتنفيذ = ask | mid للتقييم
        premium = cand.get("ask") or cand.get("mid") or 0.0
        strike  = cand["strike"]
        expiry  = cand["expiry"]

        contract_cost = round(premium * 100, 2)
        self._log(f"🎯 {sym}: strike={strike} exp={expiry} @ ${premium:.2f} (${contract_cost:.0f}/عقد)")

        # ── logging Greeks ──────────────────────────────────────────
        _greeks_dict = {
            "delta": cand.get("delta"), "theta": cand.get("theta"),
            "iv":    cand.get("iv"),    "spread_pct": cand.get("spread_pct"),
        }
        _iv_s  = f"{cand['iv']*100:.0f}%"        if cand.get('iv')         else 'N/A'
        _sp_s  = f"{cand['spread_pct']*100:.1f}%" if cand.get('spread_pct') else 'N/A'
        self._log(
            f"  📊 Greeks: Δ={cand.get('delta')} θ={cand.get('theta')} "
            f"IV={_iv_s} Spread={_sp_s}"
        )

        # ⑪ عدد العقود
        qty  = max(1, min(int(budget / (premium * 100)), self.cfg.max_contracts))
        cost = premium * qty * 100
        self._log(f"📦 {qty} عقد × ${premium:.2f} = ${cost:.0f}")

        # ⑫ SL/TP — نسب من premium مباشرة (الأدق للأوبشن قصير المدى)
        # ATR 15m صغير جداً (~0.18%) لا يكفي لتحريك الأوبشن بشكل ملحوظ
        # الحل: نستخدم نسب مجربة من premium:
        #   SL  = -35% من premium (خسارة $52 على عقد $150)
        #   TP1 = +80% من premium (ربح $120 على عقد $150)
        #   TP2 = +200% من premium (ربح $300 على عقد $150)
        # R:R=2.3:1 | break-even WR=30% | نظامنا WR=47% ✅
        #
        # لكن إذا المحلل أرسل نسبة سهم >= 1% نستخدمها (ترند قوي = حركة كبيرة)
        _stock_ref = entry_stock_price if entry_stock_price > 0 else u_price
        _use_analyzer = False
        if sl_price > 0 and tp1_price > 0 and _stock_ref > 0 and premium > 0:
            sl_stock_pct  = abs(_stock_ref - sl_price)  / _stock_ref
            tp1_stock_pct = abs(tp1_price  - _stock_ref) / _stock_ref
            tp2_stock_pct = abs(tp2_price  - _stock_ref) / _stock_ref if tp2_price > 0 else tp1_stock_pct * 2.5
            # فقط إذا حركة السهم >= 1% (ترند قوي يستحق استخدامه)
            if tp1_stock_pct >= 0.01:
                leverage = 2.5
                sl  = round(max(0.01, premium * (1 - sl_stock_pct  * leverage)), 2)
                tp1 = round(premium * (1 + tp1_stock_pct * leverage), 2)
                tp2 = round(premium * (1 + tp2_stock_pct * leverage), 2)
                # ── ضمان الحد الأدنى لمسافة SL (لا يقل الفرق عن 15% من العلاوة) ──
                # إذا sl_stock_pct صغيرة جداً → sl يقترب من entry → stop يطلق فوراً
                # الحل: SL لا يتجاوز 85% من premium (فارق ≥ 15% دائماً)
                _sl_ceiling = round(premium * 0.85, 2)
                if sl > _sl_ceiling:
                    sl = _sl_ceiling
                    self._log(
                        f"  ⚠️ SL قُلِّص من الحد الأقصى: {sl:.2f} "
                        f"(sl_stock_pct={sl_stock_pct*100:.2f}% صغيرة جداً → كان سيساوي entry)"
                    )
                _use_analyzer = True
                self._log(f"  📐 SL/TP من المحلل (حركة {tp1_stock_pct*100:.1f}%): SL=${sl:.2f} TP1=${tp1:.2f} TP2=${tp2:.2f}")

        if not _use_analyzer:
            # النسب المثلى للأوبشن قصير المدى (مجربة على backtest 8 سنوات)
            sl  = round(max(0.01, premium * (1 - self.cfg.stop_loss_pct)),   2)
            tp1 = round(premium * (1 + self.cfg.take_profit_1_pct), 2)
            tp2 = round(premium * (1 + self.cfg.take_profit_2_pct), 2)
            self._log(f"  📐 SL/TP premium-based: SL=${sl:.2f}(-{self.cfg.stop_loss_pct*100:.0f}%) TP1=${tp1:.2f}(+{self.cfg.take_profit_1_pct*100:.0f}%) TP2=${tp2:.2f}(+{self.cfg.take_profit_2_pct*100:.0f}%)")

        trade_id = str(uuid.uuid4())[:8]

        # ⑬ تنفيذ
        if self.cfg.dry_run:
            fill = premium
            self._log(f"🧪 DRY RUN: {sym} {direction} {strike} {expiry} @ ${fill:.2f}")
        else:
            fill = self._place_and_fill(opt_c, qty, premium)
            if not fill:
                self._reject(f"❌ {sym}: لم يُملأ الأمر")
                return None
            # أعد حساب SL/TP بناءً على سعر الـ fill الفعلي مع الحفاظ على النسب من المحلل
            fill_ratio = fill / premium if premium > 0 else 1.0
            sl  = round(sl  * fill_ratio, 2)
            tp1 = round(tp1 * fill_ratio, 2)
            tp2 = round(tp2 * fill_ratio, 2)
            sl  = max(0.01, sl)
            self._log(f"✅ {sym}: FILLED @ ${fill:.2f} | SL=${sl:.2f} TP1=${tp1:.2f} TP2=${tp2:.2f}")

        # ⑭ تسجيل
        pos = {
            "id":               trade_id,
            "symbol":           sym,
            "opt_type":         direction,
            "right":            right,
            "strike":           strike,
            "expiry":           expiry,
            "entry_premium":    fill,
            "trade_context":          trade_context or {},   # session/regime/tags
            "greeks_snapshot":       cand.get("greeks") or {},
            "contract_score":        getattr(cand.get("contract_score"), "score_total", None),
            "contract_score_obj":    cand.get("contract_score"),   # ContractScore كامل للـ outcome
            "all_scored_contracts":  cand.get("all_scored_contracts") or [],
            "current_price":    fill,
            "stop_loss":        sl,
            "take_profit":      tp1,
            "take_profit_2":    tp2,
            "highest":          fill,
            "contracts":        qty,
            "cost":             fill * qty * 100,
            "tp_phase":         0,
            "status":           "open",
            "opened_at":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            # ── Trailing Stop 8% Ratchet tracking ──
            "last_trail_at":    fill,   # نقطة البداية = سعر الدخول
            "trail_count":      0,      # عدد مرات تحريك الـ SL
            "opt_contract":     opt_c,
            "pct":              pct,
            "balance_at_entry": bal,
        }
        with self._lock:
            self.open_positions[trade_id] = pos
        self.ledger.on_open()  # ✅ إصلاح: on_open() وحدها تدير العداد — حُذف len() المكرر

        self._log(f"📋 SL=${sl:.2f} | TP1=${tp1:.2f} | TP2=${tp2:.2f}")
        self._log(f"📊 {self.ledger.summary()}")
        return trade_id

    # ─── مراقبة الصفقات ──────────────────────────────────────────

    def manage_positions(self) -> List[dict]:
        with self._lock:
            positions = list(self.open_positions.values())
        closed = []
        for pos in positions:
            try:
                r = self._check_exit(pos)
                if r: closed.append(r)
            except Exception as e:
                self._log(f"⚠ مراقبة {pos.get('symbol')}: {e}")
        return closed

    def _check_exit(self, pos: dict) -> Optional[dict]:
        opt_c = pos.get("opt_contract")
        if not opt_c: return None
        current = self._get_price(opt_c)
        if not current: return None

        tid   = pos["id"]
        entry = pos["entry_premium"]
        phase = pos["tp_phase"]
        high  = max(pos.get("highest", entry), current)

        # ── آخر نقطة trail (نبدأ من entry إذا لم تُسجَّل بعد) ──
        last_trail_at = pos.get("last_trail_at", entry)
        trail_count   = pos.get("trail_count", 0)

        with self._lock:
            if tid in self.open_positions:
                self.open_positions[tid].update({
                    "highest": high,
                    "current_price": current,
                })

        sl       = pos["stop_loss"]
        tp1_price = pos["take_profit"]
        tp2_price = pos["take_profit_2"]

        # ══════════════════════════════════════════════════════════
        # Trailing Stop 8% Ratchet
        # ─────────────────────────────────────────────────────────
        # كل ما ارتفع الأوبشن 8% من آخر نقطة trail:
        #   SL_جديد = highest × 85%    (لا ينخفض أبداً)
        # ══════════════════════════════════════════════════════════
        gain_from_last = (high - last_trail_at) / last_trail_at if last_trail_at > 0 else 0

        if gain_from_last >= self.cfg.trail_step_pct:
            new_sl = round(high * self.cfg.trail_floor_pct, 2)
            if new_sl > sl:
                sl           = new_sl
                trail_count += 1
                gain_total   = round((high - entry) / entry * 100, 1)
                self._log(
                    f"🔺 Trail#{trail_count} {pos['symbol']}: "
                    f"+{gain_from_last*100:.1f}% → SL=${sl:.2f} "
                    f"(+{gain_total:.1f}% من الدخول)"
                )
                with self._lock:
                    if tid in self.open_positions:
                        self.open_positions[tid].update({
                            "stop_loss":     sl,
                            "last_trail_at": high,
                            "trail_count":   trail_count,
                        })
            else:
                # SL لن ينخفض — فقط حدّث last_trail_at لإعادة العدّ
                with self._lock:
                    if tid in self.open_positions:
                        self.open_positions[tid]["last_trail_at"] = high

        # ── فحص SL ────────────────────────────────────────────
        if current <= sl:
            reason = f"trailing_stop_8pct#{trail_count}" if trail_count > 0 else "stop_loss"
            return self._close(tid, current, reason)

        # ── فحص TP2 → إغلاق كامل ─────────────────────────────
        if current >= tp2_price:
            self._log(f"🏆 TP2: {pos['symbol']} @ ${current:.2f} — إغلاق كامل")
            return self._close(tid, current, "take_profit_2")

        # ── فحص TP1 → SL إلى Breakeven ───────────────────────
        if phase == 0 and current >= tp1_price:
            new_sl = max(sl, round(entry * 1.00, 2))   # breakeven
            with self._lock:
                if tid in self.open_positions:
                    self.open_positions[tid].update({
                        "tp_phase":      1,
                        "stop_loss":     new_sl,
                        "last_trail_at": current,   # أعد نقطة البداية من TP1
                    })
            self._log(f"🎯 TP1: {pos['symbol']} @ ${current:.2f} — SL → Breakeven ${new_sl:.2f}")

        return None

    def _close(self, tid: str, exit_price: float, reason: str) -> Optional[dict]:
        with self._lock:
            pos = self.open_positions.pop(tid, None)
        if not pos: return None  # ✅ إصلاح: حُذف len() المكرر — on_close() تطرح -1

        qty = pos["contracts"]
        opt_c = pos.get("opt_contract")

        # ── تنفيذ أمر الإغلاق وانتظار التأكيد ──────────────
        actual_exit = exit_price
        if not self.cfg.dry_run and opt_c:
            try:
                order = MarketOrder("SELL", qty)
                order.tif = "DAY"
                trade = _ib(self.ib.placeOrder, opt_c, order)
                if trade:
                    # انتظر حتى 30 ثانية للحصول على تأكيد الإغلاق
                    deadline = time.time() + 30
                    while time.time() < deadline:
                        time.sleep(0.5)
                        try:
                            status = getattr(trade.orderStatus, "status", "")
                            fill_p = getattr(trade.orderStatus, "avgFillPrice", 0)
                            if status == "Filled" and fill_p and float(fill_p) > 0:
                                actual_exit = float(fill_p)
                                self._log(f"  ✅ إغلاق مؤكد @ ${actual_exit:.2f}")
                                break
                            if status in ("Cancelled", "Inactive", "ApiCancelled"):
                                self._log(f"  ⚠ أمر الإغلاق رُفض: {status}")
                                break
                        except Exception:
                            pass
                    else:
                        # timeout — أكمل بالسعر المرجعي وأبلغ
                        self._log(f"  ⚠ timeout انتظار إغلاق {pos['symbol']} — سجّل بسعر ${actual_exit:.2f}")
            except Exception as e:
                self._log(f"⚠ إغلاق: {e}")

        pnl  = round((actual_exit - pos["entry_premium"]) * qty * 100, 2)
        pnl_pct = round((actual_exit - pos["entry_premium"]) / pos["entry_premium"] * 100, 2) if pos["entry_premium"] > 0 else 0
        self.ledger.on_close(pnl)
        self.safety.record_outcome(pnl_pct)
        sign = "+" if pnl >= 0 else ""
        icon = "🎯" if "profit" in reason else "🛑"
        self._log(f"{icon} {pos['symbol']} {reason} @ ${actual_exit:.2f} | PnL={sign}${pnl:.2f} ({pnl_pct:+.1f}%)")

        # ── Realized Outcome Tracking ──────────────────────────────
        # يُحسب وقت الاحتفاظ
        holding_minutes = None
        try:
            opened_at = datetime.strptime(pos.get("opened_at",""), "%Y-%m-%d %H:%M:%S")
            holding_minutes = int((datetime.now() - opened_at).total_seconds() / 60)
        except Exception:
            pass

        # MFE/MAE من highest/lowest المسجل في manage_positions
        entry  = pos["entry_premium"]
        mfe    = round((pos.get("highest", entry) - entry) / entry * 100, 2) if entry > 0 else None
        # MAE: أسوأ سعر وصله العقد (نُخمّنه من SL إذا ضُرب)
        mae    = round((pos.get("stop_loss", entry) - entry) / entry * 100, 2) if "stop_loss" in pos and entry > 0 else None

        # تحديث ContractScore بالـ outcome للـ research اللاحق
        cs = pos.get("contract_score_obj")  # قد يكون None إذا لم يُحفظ
        if cs and isinstance(cs, ContractScore):
            cs.realized_pnl     = pnl
            cs.realized_pnl_pct = pnl_pct
            cs.mfe              = mfe
            cs.mae              = mae
            cs.holding_minutes  = holding_minutes
            cs.exit_reason      = reason

        # Outcome snapshot للـ research
        outcome_snapshot = {
            "realized_pnl":     pnl,
            "realized_pnl_pct": pnl_pct,
            "mfe_pct":          mfe,
            "mae_pct":          mae,
            "holding_minutes":  holding_minutes,
            "exit_reason":      reason,
            "exit_price":       actual_exit,
            "entry_premium":    entry,
            "quality_score":    pos.get("contract_score"),   # للـ correlation لاحقاً
        }
        self._log(
            f"  📊 Outcome: PnL={pnl_pct:+.1f}% | "
            f"MFE={mfe:+.1f}% | "
            f"Hold={holding_minutes}min | "
            f"quality_score={pos.get('contract_score','N/A')}"
        )

        closed = {
            **pos,
            "exit_price":      actual_exit,
            "exit_reason":     reason,
            "pnl":             pnl,
            "pnl_pct":         pnl_pct,
            "closed_at":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "outcome":         outcome_snapshot,
            "mfe_pct":         mfe,
            "mae_pct":         mae,
            "holding_minutes": holding_minutes,
        }
        self.closed_positions[tid] = closed
        return closed

    # ─── جلب بيانات السوق ────────────────────────────────────────

    def _get_price(self, contract) -> Optional[float]:
        """
        يجلب سعر الأصل — Historical أولاً (سريع + لا يحتاج اشتراك)
        ثم reqMktData كـ fallback فقط.
        الهدف: < 1 ثانية في الحالة العادية.
        """
        # ① Historical أولاً — الأسرع والأكثر موثوقية في بيئة Paper/TWS
        try:
            bars = _ib(
                self.ib.reqHistoricalData, contract,
                endDateTime="", durationStr="1 D",
                barSizeSetting="5 mins", whatToShow="TRADES",
                useRTH=False, formatDate=1, keepUpToDate=False,
            )
            if bars:
                p = float(bars[-1].close)
                if p > 0:
                    return p
        except Exception:
            pass

        # ② fallback: reqMktData مع timeout قصير (1.5s فقط)
        if self._market_data_available:
            for mdt in (3, 4):
                try:
                    _ib(self.ib.reqMarketDataType, mdt)
                    tk = _ib(self.ib.reqMktData, contract, "", True, False)
                    if not tk:
                        continue
                    for _ in range(5):      # 5 × 0.3s = 1.5s max
                        time.sleep(0.3)
                        p = _ticker_price(tk)
                        if p:
                            try: _ib(self.ib.cancelMktData, contract)
                            except: pass
                            return p
                    try: _ib(self.ib.cancelMktData, contract)
                    except: pass
                except Exception:
                    pass
        return None


    # ══════════════════════════════════════════════════════════════════
    # المصدر الوحيد لاختيار العقد — select_best_option_contract
    # ══════════════════════════════════════════════════════════════════

    def select_best_option_contract(
        self,
        symbol: str,
        direction: str,
        underlying_price: float = 0.0,
        mode: str = "paper",          # "paper" | "live"
    ) -> Optional[dict]:
        """
        المصدر الوحيد لاختيار عقد الأوبشن.

        الخوارزمية:
        1. جلب سعر الأصل (إذا لم يُمرَّر)
        2. reqSecDefOptParams  → expirations + strikes (cached 15min)
        3. فلترة expirations: DTE 0-2 فقط — لا fallback
        4. اختيار 6 strikes حول ATM حسب الاتجاه
        5. qualifyContracts للمرشحين فقط
        6. reqMktData → bid/ask حقيقي فقط
        7. فلتر تكلفة ديناميكي
        8. اختيار الأفضل بالـ ATM + spread

        Returns:
            dict: {contract, expiry, strike, right, bid, ask, mid,
                   spread_pct, dte, delta, theta, iv, cost, reason}
            None: إذا لم يُوجد عقد صالح
        """
        from datetime import date as _date
        sym   = symbol.upper().strip()
        right = "C" if direction.upper() in ("CALL", "C") else "P"
        _t0_sel = time.time()   # [DIAG] measurement 4: contract selection start

        # ══ ⚡ FAST PATH: عقد جاهز من الـ prefetch cache ══════════════
        cached_ready = self._ready_contracts.get(sym, {}).get(right)
        if cached_ready:
            age = time.time() - cached_ready.get("_ts", 0)
            if age < self._READY_TTL:
                opt = cached_ready.get("contract")
                if opt:
                    # تحديث السعر فقط (3 ثواني) — العقد جاهز مسبقاً
                    fresh_q = self._fetch_option_quote_fast(opt)
                    if fresh_q and fresh_q.get("bid") and fresh_q.get("ask"):
                        result = dict(cached_ready)
                        result.update(fresh_q)
                        # لا ترجع أي عقد من الكاش إلا إذا بقي داخل النطاق الثابت 70-160 دولار وDTE 0-2
                        _cache_ask  = result.get("ask") or result.get("mid") or 0
                        _cache_cost = round(float(_cache_ask) * 100, 2) if _cache_ask else 0.0
                        _cache_dte  = result.get("dte", -1)
                        if not (CONTRACT_COST_MIN <= _cache_cost <= CONTRACT_COST_MAX):
                            self._log(
                                f"  ⏭ {sym}: cache contract cost=${_cache_cost:.0f} "
                                f"خارج النطاق ${CONTRACT_COST_MIN:.0f}-${CONTRACT_COST_MAX:.0f} — تجاهل الكاش"
                            )
                        elif _cache_dte > 2:
                            self._log(
                                f"  ⏭ {sym}: cache DTE={_cache_dte} > 2 — تجاهل الكاش"
                            )
                        else:
                            result["cost"] = _cache_cost
                            result["_ts"] = time.time()
                            result["reason"] = f"⚡ من الـ cache (عمره {age:.0f}s) | bid={result['bid']} ask={result['ask']}"
                            self._log(
                                f"  ⚡ {sym} {direction}: CACHE HIT — "
                                f"strike={result.get('strike')} "
                                f"bid=${result.get('bid')} ask=${result.get('ask')} "
                                f"cost=${_cache_cost:.0f} "
                                f"(cache age={age:.0f}s)"
                            )
                            _sel_ms = (time.time() - _t0_sel) * 1000
                            _EXEC_LAT.record("4a_contract_fast_path_ms", _sel_ms)
                            self._log(f"[LATENCY] {sym}: contract_select_FAST={_sel_ms:.0f}ms")
                            return result
                    # السعر انتهت صلاحيته لكن العقد لا يزال صالحاً
                    self._log(f"  🔄 {sym}: cache عقد جيد لكن سعر قديم — سيُجلب سعر جديد")

        # ══ SLOW PATH: بحث كامل مع lock ═══════════════════════════════
        if sym not in self._sym_locks:
            self._sym_locks[sym] = threading.Lock()
        _lock = self._sym_locks[sym]
        if not _lock.acquire(blocking=False):
            self._log(f"  ⏳ {sym}: جارٍ جلب عقد من thread آخر — skip")
            # ارجع الـ cache القديم إن وجد (أفضل من nothing)
            if cached_ready and cached_ready.get("ask"):
                _old_cost = round(float(cached_ready.get("ask") or cached_ready.get("mid") or 0) * 100, 2)
                _old_dte  = cached_ready.get("dte", -1)
                if CONTRACT_COST_MIN <= _old_cost <= CONTRACT_COST_MAX and _old_dte <= 2:
                    self._log(f"  ♻ {sym}: استخدام cache قديم داخل النطاق ${_old_cost:.0f} DTE={_old_dte} (age={time.time()-cached_ready.get('_ts',0):.0f}s)")
                    return cached_ready
                self._log(f"  ⏭ {sym}: cache قديم خارج النطاق cost=${_old_cost:.0f} DTE={_old_dte} — رفض")
            return None
        try:
            result = self._select_best_option_contract_inner(
                sym, direction, right, underlying_price, mode
            )
            # خزّن في ready_cache للاستخدام القادم
            if result:
                result["_ts"] = time.time()
                if sym not in self._ready_contracts:
                    self._ready_contracts[sym] = {}
                self._ready_contracts[sym][right] = result
            _sel_ms = (time.time() - _t0_sel) * 1000
            _cat = "4b_contract_slow_found_ms" if result else "4c_contract_slow_MISS_ms"
            _EXEC_LAT.record(_cat, _sel_ms)
            self._log(f"[LATENCY] {sym}: contract_select_SLOW={_sel_ms:.0f}ms {'✅' if result else '❌'}")
            return result
        finally:
            _lock.release()

    def _select_best_option_contract_inner(
        self, sym, direction, right, underlying_price, mode
    ) -> Optional[dict]:
        """
        FAST MODE — هدف: اختيار عقد في < 3 ثواني
        ① chains cached  ② 6 strikes OTM  ③ batch qualify
        ④ batch reqMktData (2.5s)  ⑤ اختيار الأفضل
        """
        t0 = time.time()
        hard_deadline = t0 + 3.0
        sep = "=" * 55
        self._log(f"\n{sep}")
        self._log(f"[EXEC-TIMER] 🔍 بدء اختيار عقد: {sym} {direction.upper()} | mode={mode}")

        exchange = "CBOE" if sym in TRUE_INDICES else "SMART"

        # ① سعر الأصل (مع cache 60 ثانية)
        if underlying_price <= 0:
            _pc = self._price_cache.get(sym)
            if _pc and time.time() - _pc[0] < self._PRICE_TTL:
                underlying_price = _pc[1]
                self._log(f"  💰 {sym}: سعر من كاش=${underlying_price:.2f}")
            else:
                underlying = self._make_underlying(sym)
                q = _ib(self.ib.qualifyContracts, underlying)
                if q:
                    underlying_price = self._get_price(q[0]) or 0.0
                if underlying_price > 0:
                    self._price_cache[sym] = (time.time(), underlying_price)
        if underlying_price <= 0:
            self._log(f"  ❌ {sym}: لا سعر للأصل")
            return None

        cost_min, cost_max = _calc_cost_range(underlying_price)
        self._log(
            f"  📈 {sym}: سعر=${underlying_price:.2f} | "
            f"نطاق التكلفة ديناميكي: ${cost_min:.0f}–${cost_max:.0f}"
        )
        max_spread_pct = 0.50  # spread ≤ 50%

        # ② Chains (cached 30 min)
        chains_data = self._get_chains_cached(sym)
        if not chains_data:
            self._log(f"  ❌ {sym}: لا option chains")
            return None
        expirations = chains_data["expirations"]
        all_strikes  = chains_data["strikes"]
        self._log(
            f"[EXEC-TIMER] chains: {time.time()-t0:.2f}s | "
            f"{len(expirations)} expiry, {len(all_strikes)} strikes"
        )

        def _try_expiry(dte_min, dte_max) -> Optional[dict]:
            expiry_info = self._select_expiry(expirations, dte_min=dte_min, dte_max=dte_max)
            if not expiry_info:
                return None
            expiry, dte = expiry_info
            self._log(f"  📅 expiry={expiry} DTE={dte}")

            # ③ 6 strikes OTM
            strike_cands = self._select_strikes_otm(all_strikes, underlying_price, right, n=6)
            if not strike_cands:
                return None
            self._log(
                f"  🎯 {len(strike_cands)} strikes: "
                f"{[int(s) for s in strike_cands]}"
            )

            # ④ بناء عقود خام + qualify من الكاش أو batch
            raw_contracts = []
            cached_ids    = []   # (idx, conId) لما هو مكتشب
            for sk in strike_cands:
                cache_key = f"{sym}_{expiry}_{sk:.1f}_{right}"
                qc = self._qualified_cache.get(cache_key)
                if qc and (time.time() - qc[0]) < self._QUALIFIED_TTL:
                    opt = Option(sym, expiry, sk, right, exchange)
                    opt.conId = qc[1]
                    raw_contracts.append(opt)
                    cached_ids.append(cache_key)
                else:
                    raw_contracts.append(Option(sym, expiry, sk, right, exchange))
                    cached_ids.append(cache_key)

            # batch qualify (مرة واحدة لكل العقود)
            t_q = time.time()
            qualified = _ib(self.ib.qualifyContracts, *raw_contracts) or []
            self._log(
                f"[EXEC-TIMER] qualifyContracts batch {len(raw_contracts)}→{len(qualified)} "
                f"في {time.time()-t_q:.2f}s"
            )
            if not qualified:
                return None

            # خزّن في qualified_cache
            for opt in qualified:
                if opt.conId:
                    ck = f"{sym}_{expiry}_{opt.strike:.1f}_{right}"
                    self._qualified_cache[ck] = (time.time(), opt.conId)

            # ⑤ batch reqMktData — جرّب snapshot أولاً ثم streaming
            t_mkt = time.time()
            tickers = []

            # إذا كنا نعرف أن اشتراكات البيانات غير متاحة → تخطى مباشرة للـ fallback
            if not self._market_data_available:
                self._log("  ⚡ تخطي reqMktData (لا اشتراكات متاحة) → fallback مباشر")
            else:
                # --- محاولة snapshot=True (أسرع وتعمل مع delayed بدون اشتراك streaming) ---
                try:
                    _ib(self.ib.reqMarketDataType, 3)
                    snap_tickers = []
                    for opt in qualified:
                        try:
                            tk = _ib(self.ib.reqMktData, opt, "", True, False)   # snapshot=True
                            if tk:
                                snap_tickers.append((opt, tk))
                        except Exception:
                            pass
                    if snap_tickers:
                        snap_deadline = time.time() + 0.5  # 0.5s — سريع، نعرف أنه قد يفشل
                        while time.time() < snap_deadline:
                            time.sleep(0.1)
                            ready = sum(
                                1 for _, t in snap_tickers
                                if _v(getattr(t, "bid",  None)) or _v(getattr(t, "ask",  None))
                                or _v(getattr(t, "last", None)) or _v(getattr(t, "close",None))
                            )
                            if ready >= 1:
                                break
                        snap_got = any(
                            _v(getattr(t, "bid",  None)) or _v(getattr(t, "ask",  None))
                            or _v(getattr(t, "last", None)) or _v(getattr(t, "close",None))
                            for _, t in snap_tickers
                        )
                        if snap_got:
                            tickers = snap_tickers
                            self._log(
                                f"[EXEC-TIMER] reqMktData(snapshot) batch {len(tickers)} عقد "
                                f"في {time.time()-t_mkt:.2f}s ✅"
                            )
                except Exception:
                    pass

                # إذا فشل snapshot → جرّب streaming (مع mdt loop)
                if not tickers:
                    # وقت انتظار قصير — إذا فشل mdt=3 سريعاً نتخطى البقية
                    mdt_wait = {3: 1.0, 4: 0.7, 1: 0.4}

                    for mdt in (3, 4, 1):
                        if time.time() >= hard_deadline:
                            self._log("  ⏱ TIMEOUT_OPTION_SELECTION قبل تجربة كل أنواع بيانات السوق")
                            break
                        try:
                            _ib(self.ib.reqMarketDataType, mdt)
                        except Exception:
                            pass

                        tickers = []
                        for opt in qualified:
                            try:
                                tk = _ib(self.ib.reqMktData, opt, "", False, False)
                                if tk:
                                    tickers.append((opt, tk))
                            except Exception:
                                pass

                        if not tickers:
                            continue

                        wait_sec = min(mdt_wait.get(mdt, 1.0), max(0.1, hard_deadline - time.time()))
                        deadline_mkt = time.time() + wait_sec
                        while time.time() < deadline_mkt:
                            time.sleep(0.1)
                            ready = sum(
                                1 for _, t in tickers
                                if _v(getattr(t, "bid",   None))
                                or _v(getattr(t, "ask",   None))
                                or _v(getattr(t, "last",  None))
                                or _v(getattr(t, "close", None))
                            )
                            if ready >= 1:
                                break

                        got_data = any(
                            _v(getattr(t, "bid",   None)) or
                            _v(getattr(t, "ask",   None)) or
                            _v(getattr(t, "last",  None)) or
                            _v(getattr(t, "close", None))
                            for _, t in tickers
                        )
                        if got_data:
                            self._log(
                                f"[EXEC-TIMER] reqMktData(mdt={mdt}) batch {len(tickers)} عقد "
                                f"في {time.time()-t_mkt:.2f}s ✅"
                            )
                            break
                        else:
                            for opt, _ in tickers:
                                try: _ib(self.ib.cancelMktData, opt)
                                except Exception: pass
                            tickers = []
                            self._log(f"  ⚠ mdt={mdt}: لا بيانات بعد {wait_sec:.1f}s — جرّب التالي")

                    # إذا فشلت كل المحاولات → عطّل reqMktData للبقية
                    if not tickers:
                        self._market_data_available = False
                        self._log("  ⚠ reqMktData: لا اشتراكات — عطّل للجلسة، fallback→Historical")

            self._log(
                f"[EXEC-TIMER] reqMktData batch {time.time()-t_mkt:.2f}s"
            )

            # ⑥ اقرأ الأسعار وألغِ الاشتراكات
            candidates = []

            if tickers:
                for opt, tk in tickers:
                    bid   = getattr(tk, "bid",   None); bid   = bid   if _v(bid)   else None
                    ask   = getattr(tk, "ask",   None); ask   = ask   if _v(ask)   else None
                    try:
                        _ib(self.ib.cancelMktData, opt)
                    except Exception:
                        pass

                    if not (bid and ask):
                        self._log(f"    ❌ strike={int(opt.strike)}: لا bid/ask")
                        continue

                    mid        = (bid + ask) / 2
                    spread_pct = (ask - bid) / mid if mid else None
                    cost       = round(ask * 100, 2)
                    sp_str     = f"{spread_pct*100:.0f}%" if spread_pct is not None else "N/A"
                    self._log(f"    strike={int(opt.strike)} bid={bid} ask={ask} cost=${cost:.0f} spread={sp_str}")

                    if spread_pct is not None and spread_pct > max_spread_pct:
                        self._log(f"      ⏭ spread {sp_str} > {max_spread_pct*100:.0f}%"); continue
                    if not (cost_min <= cost <= cost_max):
                        self._log(f"      ⏭ cost ${cost:.0f} خارج النطاق"); continue

                    candidates.append({
                        "contract":   opt, "expiry": expiry, "strike": opt.strike,
                        "right":      right, "bid": round(bid,2), "ask": round(ask,2),
                        "mid":        round(mid,2), "spread_pct": round(spread_pct,4) if spread_pct else None,
                        "dte":        dte, "delta": None, "theta": None, "iv": None,
                        "cost":       cost,
                    })

            # ── Fallback: reqHistoricalData(BID_ASK) — أول نجاح يكفي ────
            # استراتيجية "early exit": أول عقد يمر الفلاتر → return فوراً
            # يوفّر 80% من الوقت (1 استدعاء بدلاً من 6)
            if not candidates:
                self._log("  🔄 [hist] BID_ASK fallback — أول عقد ناجح يكفي ...")
                t_hist = time.time()
                for opt in qualified[:5]:           # بحد أقصى 5 strikes
                    if time.time() - t_hist > 5.0:  # 5s hard cap
                        self._log("  ⏱ hist timeout"); break
                    try:
                        bars = _ib(
                            self.ib.reqHistoricalData, opt,
                            endDateTime="", durationStr="2 D",
                            barSizeSetting="1 hour", whatToShow="BID_ASK",
                            useRTH=True, formatDate=1, keepUpToDate=False,
                        )
                        if not bars:
                            continue
                        b   = bars[-1]
                        bid = float(b.open);  ask = float(b.close)
                        if not (_v(bid) and _v(ask)): continue
                        mid        = (bid + ask) / 2
                        spread_pct = (ask - bid) / mid if mid else None
                        cost       = round(ask * 100, 2)
                        sp_str     = f"{spread_pct*100:.0f}%" if spread_pct else "N/A"
                        self._log(
                            f"    [hist] {int(opt.strike)} bid={bid:.2f} ask={ask:.2f} "
                            f"${cost:.0f} spread={sp_str} ({time.time()-t_hist:.1f}s)"
                        )
                        if spread_pct is not None and spread_pct > max_spread_pct:
                            continue
                        if not (cost_min <= cost <= cost_max):
                            continue
                        candidates.append({
                            "contract":   opt, "expiry": expiry, "strike": opt.strike,
                            "right":      right, "bid": round(bid,2), "ask": round(ask,2),
                            "mid":        round(mid,2),
                            "spread_pct": round(spread_pct,4) if spread_pct else None,
                            "dte":        dte, "delta": None, "theta": None, "iv": None,
                            "cost":       cost,
                        })
                        break   # ← أول عقد صالح → نخرج فوراً
                    except Exception:
                        pass
                _hist_ms = (time.time() - t_hist) * 1000
                self._log(f"  [hist] {len(candidates)} عقد في {_hist_ms:.0f}ms")
                _EXEC_LAT.record("5e_ibkr_hist_fallback_ms", _hist_ms)   # [DIAG]

            if not candidates:
                return None

            # ⑦ اختيار الأفضل: أقرب ATM → أضيق spread
            candidates.sort(key=lambda c: (
                abs(c["strike"] - underlying_price),
                c["spread_pct"] if c["spread_pct"] is not None else 1.0
            ))
            best = candidates[0]
            sp_str = f"{best['spread_pct']*100:.1f}%" if best.get('spread_pct') else 'N/A'
            best["reason"] = (
                f"أفضل من {len(candidates)} عقد | spread={sp_str}"
            )

            self._log(f"\n  {'✅ '*5}")
            self._log(f"  ✅ العقد المختار لـ {sym}")
            self._log(f"     Direction  : {direction.upper()}")
            self._log(f"     Underlying : ${underlying_price:.2f}")
            self._log(f"     Expiry     : {expiry} (DTE={dte})")
            self._log(f"     Strike     : {best['strike']:.0f}")
            self._log(f"     Bid        : ${best['bid']}")
            self._log(f"     Ask        : ${best['ask']}  ← سعر التنفيذ")
            self._log(f"     Spread     : {sp_str}")
            self._log(f"     Cost/ctrct : ${best['cost']:.0f}")
            self._log(
                f"[EXEC-TIMER] إجمالي اختيار العقد: {time.time()-t0:.2f}s"
            )
            self._log(f"  {'✅ '*5}")
            self._log(f"{sep}\n")
            return best

        # DTE صارم: 0-2 فقط — لا fallback لDTE أعلى
        result = _try_expiry(0, 2)
        if not result:
            self._log(f"  ❌ {sym}: لا عقد في DTE 0-2 بنطاق ${cost_min:.0f}–${cost_max:.0f} — رفض الصفقة")
            self._log(f"{sep}\n")
        return result

    # ── مساعدات select_best_option_contract ─────────────────────────

    def _get_chains_cached(self, symbol: str) -> Optional[dict]:
        """reqSecDefOptParams مع caching 15 دقيقة."""
        now = time.time()
        cached = self._chain_cache.get(symbol)
        if cached and now - cached[0] < self._CHAIN_TTL:
            self._log(f"  📦 {symbol}: chains من الكاش (عمره {now-cached[0]:.0f}ث)")
            return cached[1]

        # جلب جديد
        sym = symbol.upper()
        underlying = self._make_underlying(sym)
        q = _ib(self.ib.qualifyContracts, underlying)
        if not q:
            return None
        con_id = q[0].conId
        sec_type = "IND" if sym in TRUE_INDICES else "STK"

        chains = _ib(self.ib.reqSecDefOptParams, sym, "", sec_type, con_id) or []
        if not chains:
            chains = _ib(self.ib.reqSecDefOptParams, sym, "", sec_type, 0) or []
        if not chains:
            return None

        today = datetime.now().strftime("%Y%m%d")
        expirations = sorted({
            exp for chain in chains
            for exp in getattr(chain, "expirations", [])
            if exp >= today
        })
        # ── فقط strikes صحيحة (integers) من reqSecDefOptParams ──
        # نتجنب النصف-دولار (722.5, 717.5) التي تسبب spread واسع وعرض مكرر
        all_strikes = sorted({
            float(s) for chain in chains
            for s in getattr(chain, "strikes", [])
            if isinstance(s, (int, float)) and float(s) % 1.0 == 0.0
        })

        data = {"expirations": expirations, "strikes": all_strikes}
        self._chain_cache[symbol] = (now, data)
        self._log(f"  🔄 {symbol}: chains مُحدَّثة ({len(expirations)} تاريخ, {len(all_strikes)} strike صحيح)")
        return data

    def _select_expiry(self, expirations: list,
                       dte_min: int = 14, dte_max: int = 45) -> Optional[Tuple[str, int]]:
        """اختيار أقرب expiry في نطاق DTE المطلوب."""
        from datetime import date as _date
        today = _date.today()
        candidates = []
        for exp_str in expirations:
            try:
                exp = _date(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
                dte = (exp - today).days
                if dte_min <= dte <= dte_max:
                    candidates.append((dte, exp_str))
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1], candidates[0][0]

    def _select_strikes(self, all_strikes: list,
                        u_price: float, right: str, n: int = 10) -> list:
        """
        اختيار أقرب n strikes من ATM.
        CALL: يفضّل ATM وفوق السعر قليلاً (0 - 5% OTM)
        PUT:  يفضّل ATM وتحت السعر قليلاً
        """
        if not all_strikes:
            return []

        # أقرب n strike بغض النظر عن الاتجاه
        by_dist = sorted(all_strikes, key=lambda s: abs(s - u_price))
        candidates = by_dist[:n]

        # رتّب: CALL من أصغر إلى أكبر (ATM أولاً)، PUT من أكبر إلى أصغر
        if right == "C":
            candidates.sort()
        else:
            candidates.sort(reverse=True)

        return candidates

    def _select_strikes_otm(self, all_strikes: list,
                            u_price: float, right: str, n: int = 6) -> list:
        """
        Fast Mode — 6 strikes في اتجاه OTM:
        CALL: ATM, ATM+1, ATM+2, ATM+3, ATM+4, ATM+5 (رخيص)
        PUT:  ATM, ATM-1, ATM-2, ATM-3, ATM-4, ATM-5 (رخيص)
        """
        if not all_strikes:
            return []
        strikes = sorted(all_strikes)
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - u_price))
        if right == "C":
            candidates = strikes[atm_idx: atm_idx + n]
        else:
            start = max(0, atm_idx - n + 1)
            candidates = list(reversed(strikes[start: atm_idx + 1]))
        return candidates[:n]

    def _get_option_quote_cached(self, contract) -> Optional[dict]:
        """reqMktData مع caching 30 ثانية. يُرجع bid/ask/Greeks."""
        key = f"{getattr(contract, 'conId', '')}_{getattr(contract, 'lastTradeDateOrContractMonth', '')}_{getattr(contract, 'strike', '')}_{getattr(contract, 'right', '')}"
        now = time.time()
        cached = self._quote_cache.get(key)
        if cached and now - cached[0] < self._QUOTE_TTL:
            return cached[1]

        quote = self._fetch_option_quote(contract)
        if quote:
            self._quote_cache[key] = (now, quote)
        return quote

    def _fetch_option_quote(self, contract) -> Optional[dict]:
        """يجلب bid/ask/Greeks من IBKR بأنواع بيانات متعددة."""
        for mdt in (1, 3, 4, 2):
            try:
                _ib(self.ib.reqMarketDataType, mdt)
                tk = _ib(self.ib.reqMktData, contract, "106,100,101", False, False)
                if not tk:
                    continue

                bid = ask = delta = theta = iv = last_p = close_p = None

                for _ in range(30):          # 30 × 0.25s = 7.5 ثانية
                    time.sleep(0.25)
                    bid    = getattr(tk, "bid",   None)
                    ask    = getattr(tk, "ask",   None)
                    last_p = getattr(tk, "last",  None)
                    close_p= getattr(tk, "close", None)

                    for src_name in ("modelGreeks", "bidGreeks", "askGreeks"):
                        src = getattr(tk, src_name, None)
                        if not src: continue
                        dv  = getattr(src, "delta",      None)
                        tv  = getattr(src, "theta",      None)
                        ivv = getattr(src, "impliedVol", None)
                        try:
                            if dv  is not None and not math.isnan(float(dv)):  delta = abs(float(dv))
                            if tv  is not None and not math.isnan(float(tv)):  theta = float(tv)
                            if ivv is not None and not math.isnan(float(ivv)): iv    = float(ivv)
                        except Exception:
                            pass

                    # نكفي بـ bid/ask أو last/close
                    has_price = (
                        (_v(bid) and _v(ask)) or
                        (_v(bid) and bid > 0) or
                        (_v(ask) and ask > 0) or
                        _v(last_p) or _v(close_p)
                    )
                    if has_price:
                        break

                try: _ib(self.ib.cancelMktData, contract)
                except: pass

                # استخرج bid/ask نهائيان
                b_f = float(bid)   if _v(bid)    else None
                a_f = float(ask)   if _v(ask)    else None
                l_f = float(last_p) if _v(last_p) else None
                c_f = float(close_p)if _v(close_p)else None

                # mid
                if b_f and a_f:
                    mid = (b_f + a_f) / 2
                elif a_f:
                    mid = a_f
                elif b_f:
                    mid = b_f
                elif l_f:
                    mid = l_f
                    b_f = a_f = l_f   # نستخدم last كـ fallback للسعر فقط
                elif c_f:
                    mid = c_f
                    b_f = a_f = c_f
                else:
                    continue   # لا سعر — جرب data type آخر

                spread_pct = None
                if b_f and a_f and mid and mid > 0:
                    spread_pct = (a_f - b_f) / mid

                return {
                    "bid":        round(b_f, 2) if b_f else None,
                    "ask":        round(a_f, 2) if a_f else None,
                    "mid":        round(mid,  2),
                    "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
                    "delta":      round(delta, 3) if delta is not None else None,
                    "theta":      round(theta, 4) if theta is not None else None,
                    "iv":         round(iv,    4) if iv    is not None else None,
                }

            except Exception:
                pass

        return None

    def _fetch_option_quote_fast(self, contract) -> Optional[dict]:
        """
        نسخة سريعة من _fetch_option_quote — timeout 3 ثواني فقط.
        تُستخدم عند وجود إشارة جاهزة (cache hit) لتحديث السعر فقط.
        """
        for mdt in (1, 3):   # live أولاً ثم delayed — لا نجرب كل الأنواع
            try:
                _ib(self.ib.reqMarketDataType, mdt)
                tk = _ib(self.ib.reqMktData, contract, "106,100,101", False, False)
                if not tk:
                    continue

                bid = ask = delta = theta = iv = None
                for _ in range(12):          # 12 × 0.25s = 3 ثواني فقط
                    time.sleep(0.25)
                    bid = getattr(tk, "bid", None)
                    ask = getattr(tk, "ask", None)

                    for src_name in ("modelGreeks", "bidGreeks", "askGreeks"):
                        src = getattr(tk, src_name, None)
                        if not src: continue
                        try:
                            dv  = getattr(src, "delta",      None)
                            tv  = getattr(src, "theta",      None)
                            ivv = getattr(src, "impliedVol", None)
                            if dv  is not None and not math.isnan(float(dv)):  delta = abs(float(dv))
                            if tv  is not None and not math.isnan(float(tv)):  theta = float(tv)
                            if ivv is not None and not math.isnan(float(ivv)): iv    = float(ivv)
                        except Exception:
                            pass

                    if _v(bid) and _v(ask):
                        break

                try: _ib(self.ib.cancelMktData, contract)
                except: pass

                b_f = float(bid) if _v(bid) else None
                a_f = float(ask) if _v(ask) else None
                if not (b_f or a_f):
                    continue

                mid = (b_f + a_f) / 2 if (b_f and a_f) else (a_f or b_f)
                spread_pct = (a_f - b_f) / mid if (b_f and a_f and mid) else None
                return {
                    "bid":        round(b_f, 2) if b_f else None,
                    "ask":        round(a_f, 2) if a_f else None,
                    "mid":        round(mid,  2) if mid else None,
                    "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
                    "delta":      round(delta, 3) if delta is not None else None,
                    "theta":      round(theta, 4) if theta is not None else None,
                    "iv":         round(iv,    4) if iv    is not None else None,
                }
            except Exception:
                pass
        return None

    def prefetch_ready_contract(
        self,
        symbol: str,
        direction: str,
        underlying_price: float = 0.0,
        mode: str = "paper",
    ) -> None:
        """
        يُستدعى من prefetch_loop (background thread) لتجهيز العقد مسبقاً.
        نتيجة تُخزَّن في _ready_contracts[sym][right] جاهزة لأي إشارة قادمة.
        """
        sym   = symbol.upper().strip()
        right = "C" if direction.upper() in ("CALL", "C") else "P"

        # تخطّ إذا الكاش حديث (أقل من 50 ثانية)
        existing = self._ready_contracts.get(sym, {}).get(right)
        if existing and time.time() - existing.get("_ts", 0) < 50:
            return

        if sym not in self._sym_locks:
            self._sym_locks[sym] = threading.Lock()
        _lock = self._sym_locks[sym]
        if not _lock.acquire(blocking=False):
            return   # thread آخر يعمل على نفس الرمز
        try:
            result = self._select_best_option_contract_inner(
                sym, direction, right, underlying_price, mode
            )
            if result:
                result["_ts"] = time.time()
                if sym not in self._ready_contracts:
                    self._ready_contracts[sym] = {}
                self._ready_contracts[sym][right] = result
                self._log(
                    f"  ✅ [Prefetch] {sym} {direction}: "
                    f"strike={result.get('strike')} "
                    f"bid=${result.get('bid')} ask=${result.get('ask')} — جاهز ⚡"
                )
            else:
                self._log(f"  ⚠ [Prefetch] {sym} {direction}: لم يُوجد عقد صالح")
        except Exception as _e:
            self._log(f"  ❌ [Prefetch] {sym} {direction}: {_e}")
        finally:
            _lock.release()

    def _get_option_price(self, contract) -> Optional[float]:
        """يجلب mid price للأوبشن — بسيط وسريع."""
        for mdt in (3, 4, 2, 1):
            try:
                _ib(self.ib.reqMarketDataType, mdt)
                tk = _ib(self.ib.reqMktData, contract, "", False, False)
                if not tk:
                    continue

                bid = ask = last_p = None
                for _ in range(8):
                    time.sleep(0.3)
                    bid   = getattr(tk, "bid",   None)
                    ask   = getattr(tk, "ask",   None)
                    last_p = getattr(tk, "last", None) or getattr(tk, "close", None)
                    if _v(bid) or _v(ask) or _v(last_p):
                        break

                try: _ib(self.ib.cancelMktData, contract)
                except: pass

                if _v(bid) and _v(ask):
                    return round((float(bid) + float(ask)) / 2, 2)
                if _v(ask):
                    return round(float(ask), 2)
                if _v(bid):
                    return round(float(bid), 2)
                if _v(last_p):
                    return round(float(last_p), 2)

            except Exception:
                pass

        # fallback: historical للأوبشن
        try:
            bars = _ib(
                self.ib.reqHistoricalData, contract,
                endDateTime="", durationStr="2 D",
                barSizeSetting="1 hour", whatToShow="TRADES",
                useRTH=False, formatDate=1, keepUpToDate=False,
            )
            if bars:
                p = float(bars[-1].close)
                if p > 0:
                    return round(p, 2)
        except Exception:
            pass

        return None

    def _place_and_fill(self, contract, qty: int, ref_price: float,
                          spread_at_entry: float = 0.0) -> Optional[float]:
        """
        إرسال أمر شراء مع 3 مراحل تصاعدية:
          1) Limit @ ask+0.05  — انتظار 6s
          2) Limit @ ask+0.10  — انتظار 5s
          3) Market Order       — انتظار 5s (fallback مضمون)
        ref_price = ask من select_best_option_contract.
        """
        t0 = time.time()
        ask_price = round(ref_price + 0.05, 2)

        self._log(
            f"[EXEC-TIMER] order_sent: {contract.symbol} "
            f"strike={contract.strike} ask=${ask_price:.2f} qty={qty}"
        )

        try:
            order = LimitOrder("BUY", qty, ask_price)
            order.tif = "DAY"
            trade = _ib(self.ib.placeOrder, contract, order)
            if not trade:
                self._log("❌ placeOrder: لم يُرجع trade")
                return None
        except Exception as e:
            self._log(f"❌ placeOrder: {e}")
            return None

        def _get_status():
            try:
                return getattr(trade.orderStatus, "status", "")
            except Exception:
                return ""

        def _get_fill():
            try:
                return getattr(trade.orderStatus, "avgFillPrice", 0) or 0
            except Exception:
                return 0

        def _wait(seconds) -> Optional[float]:
            end = time.time() + seconds
            while time.time() < end:
                time.sleep(0.2)
                st = _get_status()
                fp = _get_fill()
                if st == "Filled" and _v(fp):
                    return float(fp)
                if st in ("Cancelled", "Inactive", "ApiCancelled"):
                    return -1.0
            return None

        # ── مرحلة 1: Limit @ ask+0.05 — 6 ثواني ─────────────────────
        result = _wait(6)
        if result and result > 0:
            _fill_ms = (time.time() - t0) * 1000
            self._log(f"[EXEC-TIMER] filled (L1) في {_fill_ms/1000:.1f}s @ ${result:.2f}")
            _EXEC_LAT.record("7_order_fill_L1_ms", _fill_ms)   # [DIAG]
            self.safety.record_fill(ref_price, result, _fill_ms / 1000, spread_at_entry)
            return result
        if result == -1.0:
            self._log("[EXEC-TIMER] order cancelled (L1)")
            return None

        # ── مرحلة 2: Limit @ ask+0.10 — 5 ثواني ─────────────────────
        adjusted = round(ref_price + 0.10, 2)
        self._log(f"[EXEC-TIMER] L1 لم يُنفَّذ — تعديل إلى ${adjusted:.2f}")
        try:
            order2 = LimitOrder("BUY", qty, adjusted)
            order2.tif = "DAY"
            _ib(self.ib.placeOrder, contract, order2)
        except Exception as e:
            self._log(f"⚠ تعديل L2 فشل: {e}")

        result = _wait(5)
        if result and result > 0:
            _fill_ms = (time.time() - t0) * 1000
            self._log(f"[EXEC-TIMER] filled (L2) في {_fill_ms/1000:.1f}s @ ${result:.2f}")
            _EXEC_LAT.record("7_order_fill_L2_ms", _fill_ms)   # [DIAG]
            self.safety.record_fill(ref_price, result, _fill_ms / 1000, spread_at_entry)
            return result
        if result == -1.0:
            self._log("[EXEC-TIMER] order cancelled (L2)")
            return None

        # ── مرحلة 3: Market Order — fallback مضمون ───────────────────
        self._log("[EXEC-TIMER] L2 لم يُنفَّذ — تحويل إلى Market Order")
        try:
            _ib(self.ib.cancelOrder, trade.order)
        except Exception:
            pass
        try:
            mkt_order = MarketOrder("BUY", qty)
            mkt_order.tif = "DAY"
            mkt_trade = _ib(self.ib.placeOrder, contract, mkt_order)
            if not mkt_trade:
                self._log("❌ Market Order: لم يُرجع trade")
                return None
            mkt_end = time.time() + 5
            while time.time() < mkt_end:
                time.sleep(0.2)
                st = getattr(mkt_trade.orderStatus, "status", "")
                fp = getattr(mkt_trade.orderStatus, "avgFillPrice", 0) or 0
                if st == "Filled" and _v(fp):
                    actual = float(fp)
                    _fill_ms = (time.time() - t0) * 1000
                    self._log(f"[EXEC-TIMER] filled (MKT) في {_fill_ms/1000:.1f}s @ ${actual:.2f}")
                    _EXEC_LAT.record("7_order_fill_MKT_ms", _fill_ms)   # [DIAG]
                    self.safety.record_fill(ref_price, actual, _fill_ms / 1000, spread_at_entry)
                    return actual
            self._log(f"[EXEC-TIMER] timeout نهائي بعد {time.time()-t0:.1f}s")
            _EXEC_LAT.record("7_order_fill_TIMEOUT_ms", (time.time()-t0)*1000)  # [DIAG]
            try:
                _ib(self.ib.cancelOrder, mkt_trade.order)
            except Exception:
                pass
        except Exception as e:
            self._log(f"❌ Market Order فشل: {e}")
        return None

    # ─── مساعدات ─────────────────────────────────────────────────

    def _make_underlying(self, symbol: str):
        if symbol in TRUE_INDICES:
            return Index(symbol, "CBOE", "USD")
        return Stock(symbol, "SMART", "USD")

    def _has_open(self, symbol: str) -> bool:
        return any(
            p["symbol"] == symbol and p["status"] == "open"
            for p in self.open_positions.values()
        )

    def get_open_positions(self) -> List[dict]:
        with self._lock:
            return [
                {k: v for k, v in pos.items() if k != "opt_contract"}
                for pos in self.open_positions.values()
            ]

    def emergency_flatten(self, reason: str = "يدوي") -> List[dict]:
        """
        إغلاق كل الصفقات المفتوحة فوراً.
        يُستدعى عند: disconnect / daily loss / أمر يدوي.
        """
        self.safety.trigger_emergency_stop(reason)
        self._log(f"🚨 Emergency Flatten: {reason}")
        with self._lock:
            tids = list(self.open_positions.keys())
        closed = []
        for tid in tids:
            pos = self.open_positions.get(tid)
            if not pos: continue
            opt_c = pos.get("opt_contract")
            price = 0.0
            if opt_c:
                price = self._get_price(opt_c) or 0.0
            result = self._close(tid, price, f"emergency_flatten:{reason}")
            if result:
                closed.append(result)
                self._log(f"  ✅ أُغلقت: {pos.get('symbol')} @ ${price:.2f}")
        self._log(f"🚨 Emergency Flatten: أُغلقت {len(closed)} صفقة")
        return closed

    def get_kpi_report(self) -> dict:
        """تقرير KPI — يُستدعى من trading_app للعرض"""
        return self.safety.kpi_summary()

    def get_stats(self) -> dict:
        return {
            "open_trades":  self.ledger.open_trades,
            "daily_trades": self.ledger.daily_trades,
            "daily_pnl":    self.ledger.daily_pnl,
            "cons_losses":  self.ledger.consecutive_losses,
            "total_closed": len(self.closed_positions),
        }
