# -*- coding: utf-8 -*-
"""
trading_app.py — الواجهة الكاملة + البوت النظيف
الواجهة الأصلية + محرك تنفيذ جديد نظيف 100%
لا patches، لا قيم وهمية.
تشغيل: python trading_app.py
"""
import time, sys, asyncio, threading, os
from datetime import datetime, timedelta

# ── مسار التطبيق (يعمل سواء كـ .py عادي أو EXE مجمّع بـ PyInstaller) ──────
def _app_dir() -> str:
    """
    يعيد مجلد البيانات القابلة للكتابة:
    • frozen EXE  → %APPDATA%\SmartTrader
    • dev script  → مجلد trading_app.py
    """
    if getattr(sys, "frozen", False):
        _ad = os.environ.get("APPDATA", os.path.expanduser("~"))
        _d  = os.path.join(_ad, "SmartTrader")
        os.makedirs(_d, exist_ok=True)
        return _d
    return os.path.dirname(os.path.abspath(__file__))

# ── Bootstrap: إنشاء المجلدات والملفات الافتراضية عند أول تشغيل ─────────────
try:
    from utils.paths import bootstrap as _bootstrap
    _bootstrap()
except Exception:
    # fallback إذا لم تُتوفر utils أثناء dev
    os.makedirs(os.path.join(_app_dir(), "chart_data"), exist_ok=True)
    os.makedirs(os.path.join(_app_dir(), "logs"),       exist_ok=True)
    os.makedirs(os.path.join(_app_dir(), "data"),       exist_ok=True)

# ── يجب ضبط هذه الخصائص قبل أي import لـ PyQt5 widgets أو pyqtgraph ──
import PyQt5.QtCore as _qtcore_pre
_app_pre = _qtcore_pre.QCoreApplication.instance()
if _app_pre is None:
    _qtcore_pre.QCoreApplication.setAttribute(_qtcore_pre.Qt.AA_ShareOpenGLContexts, True)
    _qtcore_pre.QCoreApplication.setAttribute(_qtcore_pre.Qt.AA_EnableHighDpiScaling, True)
    _qtcore_pre.QCoreApplication.setAttribute(_qtcore_pre.Qt.AA_UseHighDpiPixmaps, True)
    try:
        from PyQt5.QtWidgets import QApplication as _QApp_pre
        _QApp_pre.setHighDpiScaleFactorRoundingPolicy(
            _qtcore_pre.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass

from PyQt5.QtCore import Qt, QCoreApplication
from PyQt5.QtWidgets import *
from PyQt5.QtWidgets import QSplitter, QDialog, QSpinBox, QDoubleSpinBox, QTextEdit
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import pyqtgraph as pg

try:
    from PyQt5.QtCore import qInstallMessageHandler
    _SUPPRESS = (
        'Timers can only be used',
        'Cannot queue arguments of type',
        'QVector<int>',
        'qRegisterMetaType',
    )
    def _qt_msg(mode, ctx, msg):
        m = str(msg)
        if any(s in m for s in _SUPPRESS):
            return
        try: sys.stderr.write(m + '\n')
        except: pass
    qInstallMessageHandler(_qt_msg)
except: pass

from ib_insync import IB, Stock, Index, Option, MarketOrder, LimitOrder, util
import logging as _logging

class _IBFilter(_logging.Filter):
    def filter(self, r):
        return not any(x in r.getMessage() for x in
            ('No reqId','Error 10091','Error 10197',
             'Part of requested market data','No market data during'))
_logging.getLogger('ib_insync.wrapper').addFilter(_IBFilter())

# ── البوت الجديد X1 ────────────────────────────────────────────────
# المصدر الأساسي الآن هو X1:
#   trading_app.py → smart_analyzer_bridge_x1.py → analyzer_x1.py
# fallback موجود فقط لحماية الواجهة إذا نسيت نسخ ملفات X1 بجانب البرنامج.

# ── Analyzer mode switch ──────────────────────────────────────────────────────
# "X2" = production (default — X1 bridge → analyzer_x2, unchanged behaviour)
# "BC" = experimental paper-only (B+C signals displayed, NO live execution)
# DEFAULT MUST REMAIN "X2". Do not change without explicit authorization.
ANALYZER_MODE = "BC"

# Grade A (Trend + High RVI) live execution gate for BC mode.
# Requires ANALYZER_MODE="BC".  False = paper/display only (safe default).
ENABLE_LIVE_BC  = True

# ORB Daily Engine live execution gate (Opening Range Breakout).
# Requires ANALYZER_MODE="BC".  Shares execution pipeline with B+C.
ENABLE_LIVE_ORB = True

if ANALYZER_MODE == "BC":
    from smart_analyzer_bridge_bc import MarketAnalyzerEngine
    if ENABLE_LIVE_BC:
        print('[App] ✅ BC LIVE-GATED MODE — Grade A only — REAL EXECUTION ENABLED')
    else:
        print('[App] ⚠️  BC PAPER MODE — signals only — NO LIVE ORDERS')
    if ENABLE_LIVE_ORB:
        print('[App] ✅ ORB DAILY ENGINE — ADX>=30 RVOL>=1.5x — REAL EXECUTION ENABLED')
    else:
        print('[App] ⚠️  ORB DISPLAY ONLY — no live ORB orders')
else:
    try:
        from smart_analyzer_bridge_x1 import MarketAnalyzerEngine
        print('[App] ✅ X1 محمّل (smart_analyzer_bridge_x1 → analyzer_x1)')
    except Exception as _x1_import_error:
        from smart_analyzer_bridge import MarketAnalyzerEngine
        print(f'[App] ⚠️ تعذر تحميل X1، تم الرجوع للمحلل القديم: {_x1_import_error}')
from execution import ExecutionEngine, ExecutionConfig

# ── Config ────────────────────────────────────────────────────────
import types as _t, sys as _s
_cfg = _t.ModuleType("config")
_cfg.COMMISSION_PER_CONTRACT = 0.65
_cfg.SLIPPAGE_TICKS = 1
_cfg.MIN_TICK = 0.01
_cfg.REGULATORY_FEE = 0.0
_cfg.TRUE_INDICES = {"SPX","XSP","NDX","VIX","RUT"}
_cfg.INDEX_SYMBOLS = {"SPX","XSP","NDX","VIX","RUT","SPY","QQQ","IWM"}
_cfg.INDEX_VWAP_SYMBOLS = ["SPX","XSP","SPY","QQQ","IWM"]
_cfg.INDEX_ETF_SYMBOLS = ["SPY","QQQ","IWM"]
_cfg.QQQ_SYMBOLS = ["QQQ","NDX"]

# ═══════════════════════════════════════════════════════════════
# X1 — قائمة الرموز المركزية الوحيدة للمسح والتحليل
# لا تغيّر الرموز في أماكن متفرقة؛ عدّل هذه القائمة فقط.
# AVGO و COST معطلان (win rate منخفض جداً)
# ═══════════════════════════════════════════════════════════════
X1_SCAN_SYMBOLS = [
    'QQQ', 'MSFT', 'META', 'AMZN', 'GOOGL', 'SPY', 'NVDA', 'NFLX',
]  # Phase1: replaced TLT/IWM (no X2 profile, always rejected) with NVDA/NFLX (profiled in analyzer_x2)
_cfg.MOMENTUM_SYMBOLS = list(X1_SCAN_SYMBOLS)
_cfg.GAP_FILL_SYMBOLS = list(X1_SCAN_SYMBOLS)
_s.modules["config"] = _cfg

from config import COMMISSION_PER_CONTRACT, SLIPPAGE_TICKS, MIN_TICK, REGULATORY_FEE, INDEX_SYMBOLS
TRUE_INDICES = _cfg.TRUE_INDICES

PRE_MARKET_OPTION_SYMBOLS = set(X1_SCAN_SYMBOLS)

try:
    _LEARNING_AVAILABLE = False
    from learning_system import BotLearningSystem
    _LEARNING_AVAILABLE = True
except ImportError:
    pass


# ===================================================
_ib_loop: asyncio.AbstractEventLoop = None
_ib_thread: threading.Thread = None

def _suppress_output_line(msg) -> bool:
    """يُخفت الرسائل المتوقعة غير الحرجة من IB thread"""
    _noisy = ('Task was destroyed', 'was never retrieved',
              'No reqId', 'Error 10091', 'Error 10197',
              'reqHistoricalData', 'reqMktData',
              "'NoneType' object has no attribute 'last'",
              "'NoneType' object has no attribute 'bid'")
    s = str(msg)
    return any(x in s for x in _noisy)

def _start_ib_loop():
    """تشغيل asyncio event loop في thread منفصل خاص بـ IB"""
    global _ib_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        import nest_asyncio
        nest_asyncio.apply(loop)
    except ImportError:
        pass

    # منع RecursionError: Task.__del__ → logger.error → RecursionError
    # نستبدل exception handler الافتراضي بواحد آمن يستخدم print فقط
    def _safe_handler(loop, context):
        msg = context.get('message', '')
        exc = context.get('exception')
        if isinstance(exc, RecursionError):
            return
        if 'Task was destroyed' in msg or 'was never retrieved' in msg:
            return
        try:
            sys.stderr.write(f'[IB] {msg}\n')
        except Exception:
            pass

    loop.set_exception_handler(_safe_handler)
    _ib_loop = loop
    loop.run_forever()

def get_ib_loop() -> asyncio.AbstractEventLoop:
    """الحصول على IB event loop (ينشئه إذا لم يكن موجوداً)"""
    global _ib_loop, _ib_thread
    if _ib_loop is None or not _ib_loop.is_running():
        _ib_thread = threading.Thread(target=_start_ib_loop, daemon=True)
        _ib_thread.start()
        import time
        while _ib_loop is None or not _ib_loop.is_running():
            time.sleep(0.05)
    return _ib_loop

def run_in_ib_thread(func, *args, **kwargs):
    """
    Execute an ib_insync call safely from any thread.
    ✅ لا يُسقط البوت أبداً — يُعيد None عند أي خطأ أو timeout
    """
    import inspect, asyncio as _aio
    loop = get_ib_loop()

    try:
        running = _aio.get_running_loop()
    except RuntimeError:
        running = None

    if running is loop:
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as _e:
            if not _suppress_output_line(_e): print(f'[IB] direct call error: {_e}')
            return None

    async def _coro():
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    future = asyncio.run_coroutine_threadsafe(_coro(), loop)
    try:
        return future.result(timeout=20)
    except asyncio.TimeoutError:
        try: future.cancel()
        except Exception: pass
        if not _suppress_output_line(getattr(func, '__name__', str(func))): print(f'[IB] timeout: {getattr(func, "__name__", str(func))}')
        return None   # ✅ لا raise — البوت يستمر
    except Exception as _e:
        if not _suppress_output_line(_e): print(f'[IB] error: {_e}')
        return None   # ✅ لا raise

def run_in_ib_thread_long(func, *args, timeout=120, **kwargs):
    """نفس run_in_ib_thread لكن بـ timeout أطول — للبيانات التاريخية الكبيرة"""
    import inspect, asyncio as _aio
    loop = get_ib_loop()
    try:
        running = _aio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        try: return func(*args, **kwargs)
        except Exception as _e:
            if not _suppress_output_line(_e): print(f'[IB_long] error: {_e}')
            return None
    async def _coro():
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result
    future = asyncio.run_coroutine_threadsafe(_coro(), loop)
    try:
        return future.result(timeout=timeout)
    except asyncio.TimeoutError:
        try: future.cancel()
        except Exception: pass
        if not _suppress_output_line(getattr(func,'__name__',str(func))): print(f'[IB_long] timeout: {getattr(func,"__name__",str(func))}')
        return None
    except Exception as _e:
        if not _suppress_output_line(_e): print(f'[IB_long] error: {_e}')
        return None

def ib_pump(seconds=0.05):
    """Pump the IB event loop — لا يُسقط البوت عند أي خطأ"""
    loop = get_ib_loop()
    async def _sleep():
        await asyncio.sleep(seconds)
    future = asyncio.run_coroutine_threadsafe(_sleep(), loop)
    try:
        future.result(timeout=10)
    except Exception:
        pass  # ✅ تجاهل أي خطأ في pump


def _safe_float(v, default=0.0):
    try:
        f = float(v)
        return f if f > 0 else default
    except Exception:
        return default


def _resolve_account_balance(ib=None, account=None, app=None, engine=None):
    """
    مصدر موحّد ونظيف للرصيد:
    1) رصيد الـ ExecutionEngine إن كان صالحاً
    2) accountSummary من IBKR (NetLiquidation ثم AvailableFunds ثم TotalCashValue)
    3) accountValues
    4) رصيد الواجهة المخزَّن سابقاً
    """
    # 1) من الـ engine نفسه
    if engine is not None:
        try:
            bal = _safe_float(engine._get_balance())
            if bal > 0:
                return bal
        except Exception:
            pass

    # 2) من accountSummary
    if ib is not None:
        summary = None
        try:
            if account:
                summary = run_in_ib_thread(ib.accountSummary, account)
            if not summary:
                summary = run_in_ib_thread(ib.accountSummary)
        except Exception:
            summary = None
        if summary:
            for tag in ('NetLiquidation', 'AvailableFunds', 'TotalCashValue'):
                for item in summary:
                    try:
                        if getattr(item, 'tag', '') == tag:
                            bal = _safe_float(getattr(item, 'value', 0))
                            if bal > 0:
                                return bal
                    except Exception:
                        pass

        # 3) من accountValues
        try:
            vals = run_in_ib_thread(ib.accountValues)
        except Exception:
            vals = None
        if vals:
            for tag in ('NetLiquidation', 'AvailableFunds', 'TotalCashValue'):
                for item in vals:
                    try:
                        if getattr(item, 'tag', '') == tag:
                            bal = _safe_float(getattr(item, 'value', 0))
                            if bal > 0:
                                return bal
                    except Exception:
                        pass

    # 4) fallback من الواجهة
    if app is not None:
        bal = _safe_float(getattr(app, 'account_balance', 0.0))
        if bal > 0:
            return bal

    return 0.0


def _patch_engine_balance(engine, ib=None, account=None, app=None, risk_manager=None, log_fn=None):
    """يجعل ExecutionEngine يستخدم رصيداً حقيقياً حتى لو أرجع 0."""
    if engine is None:
        return
    original_get_balance = getattr(engine, '_get_balance', None)
    if not callable(original_get_balance):
        return

    def _wrapped_get_balance(*args, **kwargs):
        bal = 0.0
        try:
            bal = _safe_float(original_get_balance(*args, **kwargs))
        except Exception:
            bal = 0.0
        if bal <= 0:
            bal = _resolve_account_balance(ib=ib, account=account, app=app)
            if bal > 0 and log_fn:
                try:
                    log_fn(f"💰 fallback balance=${bal:,.0f}")
                except Exception:
                    pass
        if bal > 0 and risk_manager is not None:
            try:
                risk_manager.update_from_balance(bal)
            except Exception:
                pass
        if bal > 0 and app is not None:
            try:
                app.account_balance = bal
            except Exception:
                pass
        return bal

    engine._get_balance = _wrapped_get_balance



# ══════════════════════════════════════════════════════════
# تكاليف التداول — مستوردة من config.py (المصدر الموحّد)
# ══════════════════════════════════════════════════════════

# ── Embedded config fallback ────────────────────────────────────────────────
try:
    import config as _cfg_check  # noqa: F401
except Exception:
    import sys as _sys, types as _types
    _cfg = _types.ModuleType("config")
    _cfg.COMMISSION_PER_CONTRACT = 0.65
    _cfg.SLIPPAGE_TICKS = 1
    _cfg.MIN_TICK = 0.01
    _cfg.REGULATORY_FEE = 0.0

    _cfg.TRUE_INDICES = {"SPX", "XSP", "NDX", "VIX", "RUT"}
    _cfg.INDEX_SYMBOLS = {"SPX", "XSP", "NDX", "VIX", "RUT", "SPY", "QQQ", "IWM"}
    _cfg.INDEX_VWAP_SYMBOLS = ["SPX", "XSP", "SPY", "QQQ", "IWM"]
    _cfg.INDEX_ETF_SYMBOLS = ["SPY", "QQQ", "IWM"]
    _cfg.QQQ_SYMBOLS = ["QQQ", "NDX"]
    _cfg.MOMENTUM_SYMBOLS = list(X1_SCAN_SYMBOLS)
    _cfg.GAP_FILL_SYMBOLS = list(X1_SCAN_SYMBOLS)

    _sys.modules["config"] = _cfg
# ────────────────────────────────────────────────────────────────────────────

from config import (
    COMMISSION_PER_CONTRACT, SLIPPAGE_TICKS, MIN_TICK,
    REGULATORY_FEE,
)

def _calc_real_entry_cost(ask_price: float, contracts: int) -> dict:
    """تكلفة الدخول الحقيقية: Ask + Slippage + Commission"""
    slip        = SLIPPAGE_TICKS * MIN_TICK
    entry_price = round(ask_price + slip, 2)
    commission  = COMMISSION_PER_CONTRACT * contracts
    regulatory  = REGULATORY_FEE * contracts
    total       = entry_price * 100 * contracts + commission + regulatory
    return {'price': entry_price, 'commission': commission,
            'slip': slip * 100 * contracts, 'total': total}

def _calc_real_exit_proceeds(bid_price: float, contracts: int) -> dict:
    """عائد الخروج الحقيقي: Bid - Slippage - Commission"""
    slip       = SLIPPAGE_TICKS * MIN_TICK
    exit_price = max(MIN_TICK, round(bid_price - slip, 2))
    commission = COMMISSION_PER_CONTRACT * contracts
    regulatory = REGULATORY_FEE * contracts
    total      = exit_price * 100 * contracts - commission - regulatory
    return {'price': exit_price, 'commission': commission,
            'slip': slip * 100 * contracts, 'total': total}

def _calc_historical_iv(closes: list, window: int = 20) -> float:
    """IV من التقلب التاريخي الفعلي — أدق من افتراض 30% ثابت"""
    import math as _m
    if len(closes) < window + 1:
        return 0.25
    rets = [_m.log(closes[i] / closes[i-1]) for i in range(len(closes)-window, len(closes)) if closes[i-1] > 0]
    if not rets:
        return 0.25
    mean = sum(rets) / len(rets)
    var  = sum((r - mean)**2 for r in rets) / len(rets)
    return max(0.10, min(_m.sqrt(var) * _m.sqrt(252), 1.50))

def _bs_option_price(stock: float, strike: float, days: int, iv: float, right: str) -> float:
    """Black-Scholes — يُستخدم فقط عند عدم توفر bid/ask حقيقي"""
    import math as _m
    T = max(days, 1) / 365.0; r = 0.045
    try:
        d1 = (_m.log(stock/strike) + (r + 0.5*iv**2)*T) / (iv*_m.sqrt(T))
        d2 = d1 - iv*_m.sqrt(T)
        N  = lambda x: 0.5*(1 + _m.erf(x/_m.sqrt(2)))
        p  = stock*N(d1) - strike*_m.exp(-r*T)*N(d2) if right=='C' else strike*_m.exp(-r*T)*N(-d2) - stock*N(-d1)
        return max(MIN_TICK, round(p, 2))
    except Exception:
        return 0.10

PRE_MARKET_OPTION_SYMBOLS = set(X1_SCAN_SYMBOLS)

# ── رموز الليل/المساء 20:15–03:59 EST ───────────────────────────────
# CBOE Global Trading Hours: SPX/XSP تبدأ 20:15 EST
# ETFs الكبرى (SPY/QQQ) تبدأ 04:00 EST فقط — Evening = CBOE symbols
PRE_MARKET_EVENING_SYMBOLS = ['SPX']  # 20:00–03:59 EST: CBOE فقط

# ── رموز أوائل الصباح 04:00–09:29 EST ──────────────────────────────
# ETFs والأسهم الكبرى تُتداول من 04:00 EST
# SPX للتحليل → ينفَّذ على XSP | SPY/QQQ/AAPL/MSFT تُنفَّذ مباشرة
PRE_MARKET_MORNING_SYMBOLS = list(X1_SCAN_SYMBOLS)

def _make_order(action, qty, lmt_price=None, force_outside_rth=False):
    """
    بناء أمر تداول آمن.
    - إذا أُعطي lmt_price صالح (> 0) → LimitOrder
    - إذا لم يُعطَ سعر              → MarketOrder
    ✅ SELL: DAY (لا GTC) — يمنع تراكم الأوامر المعلقة في IBKR
    ✅ outsideRth=True للإغلاق دائماً
    """
    from ib_insync import LimitOrder, MarketOrder as _MktOrder
    import builtins as _b
    from datetime import datetime as _dtnow
    _pre_flag = getattr(_b, '_TRADING_PRE_MARKET', False)
    try:
        import pytz as _ptz
        _now_e = _dtnow.now(_ptz.timezone('US/Eastern'))
        _h, _m = _now_e.hour, _now_e.minute
        _regular_hours = (_h > 9 or (_h == 9 and _m >= 30)) and _h < 16
    except Exception:
        _regular_hours = True

    _has_price = lmt_price is not None and lmt_price > 0
    if _has_price:
        order = LimitOrder(action, qty, round(lmt_price, 2))
    else:
        order = _MktOrder(action, qty)

    # ✅ SELL: DAY — ينتهي في نهاية الجلسة تلقائياً (لا يتراكم)
    # BUY في Pre-Market: GTC | Regular: DAY
    if action.upper() == 'SELL':
        order.tif = 'DAY'
    elif force_outside_rth:
        order.tif = 'GTC'
    else:
        order.tif = 'GTC' if (_pre_flag and not _regular_hours) else 'DAY'

    # ✅ SELL و force دائماً outsideRth=True
    if force_outside_rth or action.upper() == 'SELL':
        order.outsideRth = True
    else:
        _pre = _pre_flag or (not _regular_hours)
        order.outsideRth = bool(_pre)
        if order.tif == 'GTC':
            order.outsideRth = True

    return order

# INDEX_SYMBOLS و TRUE_INDICES — مستوردة من config.py (المصدر الموحّد)
from config import INDEX_SYMBOLS, TRUE_INDICES as _TRUE_INDICES

import math as _math

def _valid(v):
    """يتحقق أن القيمة رقم صحيح (ليست None أو nan أو سالبة)"""
    try:
        return v is not None and not _math.isnan(float(v)) and float(v) > 0
    except Exception:
        return False

# _TRUE_INDICES مستورد من config.py أعلاه (as _TRUE_INDICES)
# ETFs مثل SPY/QQQ/IWM هي STK على SMART — ليست IND

def _make_contract(symbol):
    sym = symbol.upper().strip()
    if sym in _TRUE_INDICES:
        return Index(sym, 'CBOE', 'USD')
    return Stock(sym, 'SMART', 'USD')

def _option_exchange(symbol):
    """بورصة الأوبشن — CBOE للمؤشرات الحقيقية، SMART للباقي"""
    return 'CBOE' if symbol.upper().strip() in _TRUE_INDICES else 'SMART'

def _what_to_show(symbol, bar_size="intraday"):
    """SPX/XSP intraday = MIDPOINT | Daily = TRADES (IBKR لا يدعم MIDPOINT daily للمؤشرات)"""
    if symbol.upper().strip() in _TRUE_INDICES:
        return "TRADES" if bar_size == "daily" else "MIDPOINT"
    return "TRADES"

def _is_rth_now():
    try:
        import pytz as _ptz
        _now = datetime.now(_ptz.timezone('US/Eastern'))
        if _now.weekday() >= 5:
            return False
        hhmm = _now.hour * 100 + _now.minute
        return 930 <= hhmm < 1600
    except Exception:
        return False



def safe_price(tk):
    if tk is None:
        return None
    for attr in ('last', 'close', 'bid', 'ask'):
        try:
            v = getattr(tk, attr, None)
            if _valid(v):
                return v
        except Exception:
            pass
    return None

def req_mkt_data_safe(ib, contract, generic_ticks='', snapshot=True, regulatory_snapshot=False):
    """
    نسخة هادئة من طلب بيانات السوق:
      - خارج الجلسة: Delayed-Frozen فقط
      - داخل الجلسة: Delayed أولاً ثم Live عند الحاجة
      - Snapshot افتراضياً لتقليل الضوضاء والطلبات المستمرة
    يُعيد (ticker, data_type_used)
    """
    import time as _time
    if contract is None:
        return None, None

    def _try(mdt, snap=True):
        try:
            run_in_ib_thread(ib.reqMarketDataType, mdt)
        except Exception:
            pass
        ib_pump(0.03)
        tk = run_in_ib_thread(ib.reqMktData, contract, generic_ticks, snap, regulatory_snapshot)
        if tk is None:
            return None, mdt
        for _ in range(6):
            _time.sleep(0.08)
            ib_pump(0.03)
            bid = getattr(tk, 'bid', None)
            ask = getattr(tk, 'ask', None)
            last = getattr(tk, 'last', None)
            close = getattr(tk, 'close', None)
            if _valid(bid) or _valid(ask) or _valid(last) or _valid(close):
                return tk, mdt
        try:
            run_in_ib_thread(ib.cancelMktData, contract)
        except Exception:
            pass
        return None, mdt

    if not _is_rth_now():
        # خارج السوق: جرب كل الأنواع للحصول على آخر سعر
        for _mdt in (4, 3, 2):
            tk, mdt = _try(_mdt, snap=True)
            if tk is not None:
                return tk, mdt
        return None, 4

    tk, mdt = _try(3, snap=True)
    if tk is not None:
        return tk, mdt

    tk, mdt = _try(1, snap=True)
    return tk, mdt

# ===================================================
# ادارة المخاطر
# ===================================================

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        d = prices[-i] - prices[-i - 1]
        (gains if d > 0 else losses).append(abs(d))
    ag = sum(gains) / period if gains else 0.001
    al = sum(losses) / period if losses else 0.001
    return round(100 - 100 / (1 + ag / al), 2) if al else 100.0

def calc_ema(prices, period):
    if len(prices) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return round(ema, 4)

def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    trs = [max(highs[-i] - lows[-i],
               abs(highs[-i] - closes[-i - 1]),
               abs(lows[-i]  - closes[-i - 1])) for i in range(1, period + 1)]
    return round(sum(trs) / period, 4)

def calc_bollinger(prices, period=20, mult=2.0):
    if len(prices) < period:
        return None, None, None
    w   = prices[-period:]
    ma  = sum(w) / period
    std = (sum((p - ma) ** 2 for p in w) / period) ** 0.5
    return round(ma, 4), round(ma + mult * std, 4), round(ma - mult * std, 4)

def calc_volume_ratio(volumes, period=20):
    if len(volumes) < period + 1:
        return 1.0
    avg = sum(volumes[-period - 1:-1]) / period
    return volumes[-1] / avg if avg > 0 else 1.0

def calc_macd_hist(prices):
    if len(prices) < 35: return None
    k12,k26,k9 = 2/13, 2/27, 2/10
    e12 = sum(prices[:12])/12
    e26 = sum(prices[:26])/26
    for p in prices[12:26]: e12 = p*k12 + e12*(1-k12)
    ms = []
    for p in prices[26:]:
        e12 = p*k12 + e12*(1-k12)
        e26 = p*k26 + e26*(1-k26)
        ms.append(e12 - e26)
    if len(ms) < 9: return None
    sig = sum(ms[:9])/9
    for m in ms[9:]: sig = m*k9 + sig*(1-k9)
    return round(ms[-1]-sig, 4)

def calc_adx(highs, lows, closes, period=14):
    """حساب مؤشر ADX لقياس قوة الاتجاه"""
    n = len(highs)
    if n < period * 2 + 2:
        return None
    tr_list, pdm_list, mdm_list = [], [], []
    for i in range(1, n):
        h, l, cp = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - cp), abs(l - cp))
        up   = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        tr_list.append(tr)
        pdm_list.append(up   if up > down and up > 0 else 0)
        mdm_list.append(down if down > up and down > 0 else 0)

    def wilder(data, p):
        s = sum(data[:p])
        out = [s]
        for x in data[p:]:
            s = s - s / p + x
            out.append(s)
        return out

    tr_s  = wilder(tr_list,  period)
    pdm_s = wilder(pdm_list, period)
    mdm_s = wilder(mdm_list, period)

    dx_list = []
    for i in range(len(tr_s)):
        if tr_s[i] == 0:
            continue
        pdi = 100 * pdm_s[i] / tr_s[i]
        mdi = 100 * mdm_s[i] / tr_s[i]
        denom = pdi + mdi
        dx_list.append(100 * abs(pdi - mdi) / denom if denom > 0 else 0)

    if len(dx_list) < period:
        return None
    return round(sum(dx_list[-period:]) / period, 2)


def calc_vwap(closes, highs, lows, volumes):
    """VWAP = مجموع (typical_price × volume) / مجموع volume"""
    if not volumes or len(closes) < 2: return closes[-1] if closes else 0
    tp  = [(h+l+c)/3 for h,l,c in zip(highs, lows, closes)]
    num = sum(t*v for t,v in zip(tp, volumes))
    den = sum(volumes)
    return num/den if den > 0 else closes[-1]


# ═══════════════════════════════════════════════════════════
# كشف مناطق الانعكاس — Support/Resistance + Supply/Demand
# ═══════════════════════════════════════════════════════════

def calc_pivot_points(high, low, close):
    """
    Pivot Points الكلاسيكية:
    PP = (H + L + C) / 3
    R1, R2, R3 = مقاومة
    S1, S2, S3 = دعم
    """
    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    s1 = 2 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low  - 2 * (high - pp)
    return {
        'pp': round(pp, 2),
        'r1': round(r1, 2), 'r2': round(r2, 2), 'r3': round(r3, 2),
        's1': round(s1, 2), 's2': round(s2, 2), 's3': round(s3, 2),
    }

def calc_sr_levels(highs, lows, closes, lookback=50, tolerance=0.003):
    """
    كشف مناطق الدعم والمقاومة الحقيقية:
    - يبحث عن قمم وقيعان محلية (swing highs/lows)
    - يجمع المناطق القريبة في منطقة واحدة
    - يُرجب المناطق حسب عدد مرات اللمس (أقوى = أكثر لمساً)
    """
    n = min(len(highs), lookback)
    h = highs[-n:]; l = lows[-n:]; c = closes[-n:]

    swing_highs = []
    swing_lows  = []

    # ابحث عن swing highs/lows (3 شمعات يميناً ويساراً)
    for i in range(3, n - 3):
        # Swing High: أعلى من 3 شمعات على كل جانب
        if h[i] == max(h[i-3:i+4]):
            swing_highs.append(h[i])
        # Swing Low: أدنى من 3 شمعات على كل جانب
        if l[i] == min(l[i-3:i+4]):
            swing_lows.append(l[i])

    # اجمع المناطق القريبة
    def cluster(levels, tol):
        if not levels: return []
        levels = sorted(levels)
        clusters = []
        group = [levels[0]]
        for lv in levels[1:]:
            if (lv - group[0]) / group[0] <= tol:
                group.append(lv)
            else:
                clusters.append({
                    'price':   round(sum(group) / len(group), 2),
                    'touches': len(group),
                    'strength': min(5, len(group)),
                })
                group = [lv]
        if group:
            clusters.append({
                'price':   round(sum(group) / len(group), 2),
                'touches': len(group),
                'strength': min(5, len(group)),
            })
        return clusters

    res_zones = cluster(swing_highs, tolerance)
    sup_zones = cluster(swing_lows,  tolerance)

    # رتّب حسب القوة
    res_zones.sort(key=lambda x: x['strength'], reverse=True)
    sup_zones.sort(key=lambda x: x['strength'], reverse=True)

    return res_zones[:5], sup_zones[:5]


def calc_supply_demand(highs, lows, opens, closes, lookback=80):
    """
    كشف مناطق Supply/Demand الحقيقية:
    Supply Zone = منطقة بدأ منها انخفاض قوي (شمعة هبوطية كبيرة)
    Demand Zone = منطقة بدأ منها ارتفاع قوي (شمعة صعودية كبيرة)
    """
    n = min(len(closes), lookback)
    h = highs[-n:]; l = lows[-n:]
    o = opens[-n:]; c = closes[-n:]

    supply_zones = []
    demand_zones = []

    avg_body = sum(abs(c[i]-o[i]) for i in range(n)) / n if n > 0 else 1

    for i in range(2, n - 2):
        body    = abs(c[i] - o[i])
        is_bear = c[i] < o[i]
        is_bull = c[i] > o[i]

        # شمعة قوية = جسم أكبر من 1.5× المتوسط
        if body < avg_body * 1.5:
            continue

        if is_bear:
            # Supply Zone = القمة التي انطلق منها الهبوط
            top    = max(o[i], c[i])
            bottom = min(o[i], c[i])
            supply_zones.append({
                'top':    round(top, 2),
                'bottom': round(bottom, 2),
                'mid':    round((top + bottom) / 2, 2),
                'strength': round(body / avg_body, 1),
                'idx':    i,
            })

        elif is_bull:
            # Demand Zone = القاع الذي انطلق منه الصعود
            top    = max(o[i], c[i])
            bottom = min(o[i], c[i])
            demand_zones.append({
                'top':    round(top, 2),
                'bottom': round(bottom, 2),
                'mid':    round((top + bottom) / 2, 2),
                'strength': round(body / avg_body, 1),
                'idx':    i,
            })

    # أقوى 4 مناطق فقط
    supply_zones.sort(key=lambda x: x['strength'], reverse=True)
    demand_zones.sort(key=lambda x: x['strength'], reverse=True)
    return supply_zones[:4], demand_zones[:4]


def detect_reversal_signals(highs, lows, opens, closes, volumes):
    """
    يكشف إشارات انعكاس قوية:
    - Pin Bar  (Hammer / Shooting Star)
    - Doji
    - Double Top / Bottom
    - RSI Divergence
    يُعيد قائمة إشارات مع قوتها
    """
    signals = []
    if len(closes) < 20:
        return signals

    c = closes; h = highs; l = lows; o = opens

    # ── Pin Bar (Hammer / Shooting Star) ──────────────
    for i in [-1, -2]:
        body   = abs(c[i] - o[i])
        full   = h[i] - l[i]
        if full == 0: continue
        upper  = h[i] - max(c[i], o[i])
        lower  = min(c[i], o[i]) - l[i]
        body_r = body / full

        if body_r < 0.3:
            if lower > upper * 2 and lower > full * 0.5:
                signals.append({
                    'type':     'Hammer',
                    'price':    c[i],
                    'idx':      i,
                    'direction':'bullish',
                    'strength': 3,
                    'desc':     f'🔨 Hammer @ ${c[i]:.2f}',
                })
            elif upper > lower * 2 and upper > full * 0.5:
                signals.append({
                    'type':     'ShootingStar',
                    'price':    c[i],
                    'idx':      i,
                    'direction':'bearish',
                    'strength': 3,
                    'desc':     f'⭐ Shooting Star @ ${c[i]:.2f}',
                })

    # ── Doji ─────────────────────────────────────────
    if len(c) >= 2:
        body  = abs(c[-1] - o[-1])
        full  = h[-1] - l[-1]
        if full > 0 and body / full < 0.1:
            signals.append({
                'type':     'Doji',
                'price':    c[-1],
                'idx':      -1,
                'direction':'neutral',
                'strength': 2,
                'desc':     f'✚ Doji @ ${c[-1]:.2f} (تردد)',
            })

    # ── Double Top ────────────────────────────────────
    if len(h) >= 20:
        recent_highs = h[-20:]
        top1_idx = recent_highs.index(max(recent_highs))
        remaining = recent_highs[top1_idx+3:]
        if remaining:
            top2 = max(remaining)
            top1 = recent_highs[top1_idx]
            if abs(top2 - top1) / top1 < 0.015:  # 1.5% تقريب
                signals.append({
                    'type':     'DoubleTop',
                    'price':    top1,
                    'idx':      -1,
                    'direction':'bearish',
                    'strength': 4,
                    'desc':     f'🔺 Double Top @ ${top1:.2f}',
                })

    # ── Double Bottom ─────────────────────────────────
    if len(l) >= 20:
        recent_lows = l[-20:]
        bot1_idx = recent_lows.index(min(recent_lows))
        remaining = recent_lows[bot1_idx+3:]
        if remaining:
            bot2 = min(remaining)
            bot1 = recent_lows[bot1_idx]
            if abs(bot2 - bot1) / bot1 < 0.015:
                signals.append({
                    'type':     'DoubleBottom',
                    'price':    bot1,
                    'idx':      -1,
                    'direction':'bullish',
                    'strength': 4,
                    'desc':     f'🔻 Double Bottom @ ${bot1:.2f}',
                })

    return signals


def detect_engulfing(opens, closes):
    """كشف شمعة الابتلاع (Engulfing) — دالة مستقلة"""
    if len(opens) < 2 or len(closes) < 2:
        return None
    o1, c1 = opens[-2], closes[-2]   # الشمعة السابقة
    o2, c2 = opens[-1], closes[-1]   # الشمعة الحالية
    # Bullish: شمعة حمراء ثم خضراء تبتلعها
    if c1 < o1 and c2 > o2 and c2 > o1 and o2 < c1:
        return 'bullish'
    # Bearish: شمعة خضراء ثم حمراء تبتلعها
    if c1 > o1 and c2 < o2 and c2 < o1 and o2 > c1:
        return 'bearish'
    return None



# ══════════════════════════════════════════════════════════════════
# استيراد الاستراتيجية من ملف منفصل
# عدّل strategy.py لتغيير منطق التداول
# ══════════════════════════════════════════════════════════════════
import importlib, sys, os as _os



def _load_strategy():
    """
    Analyzer-only neutral strategy layer.
    لا يحمّل أي strategy.py خارجي ولا يضيف أي منطق قرار قديم.
    موجود فقط لتوافق الواجهة القديمة مع البوت الجديد.
    """
    import types as _types

    mod = _types.SimpleNamespace()

    class _NeutralStrategy:
        MIN_SCORE = 999
        _backtest_mode = True   # ✅ يُعلم البوت أن BACKTEST_MODE مدعوم
        def __init__(self, *args, **kwargs):
            self._sym = ''
        def analyze(self, *args, **kwargs): return None
        def analyze_market(self, *args, **kwargs): return None
        def generate_signal(self, *args, **kwargs): return None
        def detect_regime(self, *args, **kwargs): return 'neutral'
        def get_market_bias(self, *args, **kwargs): return {'bias': 'neutral', 'pct': 0}
        def score_symbol_adaptive(self, *args, **kwargs): return None
        def check_entry_adaptive(self, *args, **kwargs): return (False, 'analyzer-only')
        def regime_summary(self, *args, **kwargs): return 'Analyzer-only mode'
        def on_trade_result(self, *args, **kwargs): return None
        def record_trade_result(self, *args, **kwargs): return None   # ✅ إصلاح AttributeError
        def find_active_zone(self, *args, **kwargs): return None
        def entry_confirmation(self, *args, **kwargs): return False
        def find_order_blocks(self, *args, **kwargs): return []
        def calc_supply_demand(self, *args, **kwargs): return ([], [])
        def calc_sr_levels(self, *args, **kwargs): return ([], [])
        def validate_signal(self, *args, **kwargs): return False
        def should_trade(self, *args, **kwargs): return False
        def get_stop_loss(self, *args, **kwargs): return None
        def get_take_profit(self, *args, **kwargs): return None
        def get_targets(self, *args, **kwargs): return None
        def is_index(self, *args, **kwargs): return False

    mod.ZoneTraderStrategy = _NeutralStrategy
    mod.IndexStrategy = _NeutralStrategy
    mod.StockStrategy = _NeutralStrategy
    mod.SmartStrategyPro = _NeutralStrategy
    mod.IndexVWAPStrategy = _NeutralStrategy
    mod.MomentumStrategy = _NeutralStrategy
    mod.GapFillStrategy = _NeutralStrategy
    mod.get_strategy = lambda sym: _NeutralStrategy()
    mod.calc_sr_levels = lambda *a, **k: ([], [])
    mod.calc_supply_demand = lambda *a, **k: ([], [])
    mod.find_active_zone = lambda *a, **k: None
    mod.entry_confirmation = lambda *a, **k: False
    print('[Analyzer-Only] ✅ strategy layer disabled inside old UI file')
    return mod


_strategy_module   = _load_strategy()
ZoneTraderStrategy = _strategy_module.ZoneTraderStrategy
# ✅ اجعله متاحاً عبر builtins لـ _index_analyze
import builtins as _blt_reg
_blt_reg._strategy_module_ref = _strategy_module

# ══════════════════════════════════════════════════════════════════
# ✅ دالة موحدة لفحص الـ Zone — تُستخدم في كل الاستراتيجيات
# تمنع الدخول العشوائي من جميع المسارات
# ══════════════════════════════════════════════════════════════════
def _require_zone(opens_1h, highs_1h, lows_1h, closes_1h, price, direction):
    """
    يتحقق من وجود Order Block أو S&D zone حقيقية.
    ✅ صارم: السعر يجب أن يكون داخل الـ zone أو على بُعد ≤ 0.5% منها فقط.
    يُعيد (zone_dict, score_bonus) أو (None, 0).
    """
    if not closes_1h or len(closes_1h) < 15:
        return None, 0
    if not opens_1h or not highs_1h or not lows_1h:
        return None, 0
    try:
        _sm = getattr(__import__('builtins'), '_strategy_module_ref', None)
        if not _sm:
            return None, 0
        _find_ob  = getattr(_sm, 'zr_find_order_blocks', None)
        _find_sd  = getattr(_sm, 'zr_find_sd_zones', None)
        _find_act = getattr(_sm, 'zr_find_active_zone', None)
        if not (_find_ob and _find_sd and _find_act):
            return None, 0

        _dir = 'bullish' if direction == 'CALL' else 'bearish'
        _ob  = _find_ob(opens_1h, highs_1h, lows_1h, closes_1h)
        _sd  = _find_sd(highs_1h, lows_1h, closes_1h)

        # ✅ أولاً: السعر داخل zone مباشرة (tolerance 0.8%)
        _zone = _find_act(price, _ob, _sd, _dir)

        # ✅ ثانياً: Fallback ضيق جداً — 0.5% فقط (كان 1.5%)
        if not _zone:
            _best = None
            _best_dist = 999
            for _z in (_ob + _sd):
                _t = _z.get('type', '')
                if direction == 'CALL' and _t not in ('bullish', 'demand'): continue
                if direction == 'PUT'  and _t not in ('bearish', 'supply'): continue
                _d = abs(price - _z['mid']) / max(price, 0.01)
                # ✅ صارم: 0.5% فقط بدل 1.5%
                if _d < 0.005 and _d < _best_dist:
                    _best = _z
                    _best_dist = _d
            _zone = _best

        if not _zone:
            return None, 0

        # bonus حسب القرب
        _dist = abs(price - _zone['mid']) / max(price, 0.01)
        if   _dist < 0.002: _bonus = 4
        elif _dist < 0.004: _bonus = 3
        elif _dist < 0.008: _bonus = 2
        else:               _bonus = 1

        return _zone, _bonus
    except Exception:
        return None, 0
SmartStrategyPro   = ZoneTraderStrategy

# ZoneTraderStrategy فقط
_AITrader = None
_ai_trader_available = False

# استراتيجيات متخصصة
try:
    IndexStrategy = _strategy_module.IndexStrategy
    StockStrategy = _strategy_module.StockStrategy
    _get_strategy  = _strategy_module.get_strategy
except AttributeError:
    IndexStrategy = ZoneTraderStrategy
    StockStrategy = ZoneTraderStrategy
    _get_strategy  = lambda sym: ZoneTraderStrategy()

class TradeMemory:
    """
    ذاكرة دائمة بين الجلسات — تُحفظ في risk_memory.json
    تُستخدم من RiskManager لتحسين القرارات تلقائياً:
    - تتبع win_rate لكل رمز
    - تتبع أداء كل ساعة (EST)
    - تتبع أداء كل regime
    - تعلّم من أنماط الخسارة المتكررة
    """
    MEMORY_FILE = 'risk_memory.json'

    def __init__(self):
        self._data = {
            'symbols':  {},   # {sym: {wins, losses, total_pnl, avg_score}}
            'hours':    {},   # {hour_str: {wins, losses}}
            'regimes':  {},   # {regime: {wins, losses, total_pnl}}
            'patterns': {},   # {pattern_key: {wins, losses}}
            'session':  {'date': '', 'pnl': 0.0, 'trades': 0},
        }
        self._load()

    def _path(self):
        import os
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), self.MEMORY_FILE)

    def _load(self):
        try:
            import json, os
            if os.path.exists(self._path()):
                with open(self._path(), 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    for k in self._data:
                        if k in saved:
                            self._data[k] = saved[k]
        except Exception as e:
            print(f'[Memory] تحميل: {e}')

    def _save(self):
        try:
            import json
            with open(self._path(), 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[Memory] حفظ: {e}')

    def record(self, symbol: str, pnl: float, hour_est: int,
               regime: str = 'normal', score: float = 0,
               why_tags: list = None):
        """سجّل نتيجة صفقة وحدّث الذاكرة"""
        win = pnl > 0

        # أداء الرمز
        s = self._data['symbols'].setdefault(symbol, {'wins':0,'losses':0,'total_pnl':0.0,'scores':[]})
        s['wins' if win else 'losses'] += 1
        s['total_pnl'] = round(s['total_pnl'] + pnl, 2)
        s.setdefault('scores', []).append(score)
        if len(s['scores']) > 50: s['scores'] = s['scores'][-50:]

        # أداء الساعة
        h = self._data['hours'].setdefault(str(hour_est), {'wins':0,'losses':0})
        h['wins' if win else 'losses'] += 1

        # أداء الـ regime
        r = self._data['regimes'].setdefault(regime, {'wins':0,'losses':0,'total_pnl':0.0})
        r['wins' if win else 'losses'] += 1
        r['total_pnl'] = round(r['total_pnl'] + pnl, 2)

        # أنماط الـ why tags
        for tag in (why_tags or []):
            tag = tag.strip()[:30]
            if tag:
                p = self._data['patterns'].setdefault(tag, {'wins':0,'losses':0})
                p['wins' if win else 'losses'] += 1

        self._save()

    def symbol_score_boost(self, symbol: str) -> int:
        """
        تعديل الـ score بناءً على تاريخ الرمز:
        win_rate > 60% → +2 | < 35% → -2 | < 25% → -4
        """
        s = self._data['symbols'].get(symbol)
        if not s: return 0
        total = s['wins'] + s['losses']
        if total < 5: return 0           # بيانات غير كافية
        wr = s['wins'] / total
        if wr >= 0.65: return +3
        if wr >= 0.55: return +1
        if wr <= 0.25: return -4
        if wr <= 0.35: return -2
        return 0

    def hour_score_boost(self, hour_est: int) -> int:
        """تعديل الـ score بناءً على أداء هذه الساعة تاريخياً"""
        h = self._data['hours'].get(str(hour_est))
        if not h: return 0
        total = h['wins'] + h['losses']
        if total < 4: return 0
        wr = h['wins'] / total
        if wr >= 0.65: return +2
        if wr <= 0.30: return -3
        return 0

    def regime_score_boost(self, regime: str) -> int:
        """تعديل الـ score بناءً على أداء الـ regime تاريخياً"""
        r = self._data['regimes'].get(regime)
        if not r: return 0
        total = r['wins'] + r['losses']
        if total < 5: return 0
        wr = r['wins'] / total
        if wr >= 0.60: return +1
        if wr <= 0.30: return -2
        return 0

    def is_symbol_blocked(self, symbol: str) -> bool:
        """هل الرمز سيء جداً؟ (win_rate < 20% + ≥ 10 صفقات)"""
        s = self._data['symbols'].get(symbol)
        if not s: return False
        total = s['wins'] + s['losses']
        if total < 10: return False
        return (s['wins'] / total) < 0.20

    def get_symbol_stats(self, symbol: str) -> dict:
        s = self._data['symbols'].get(symbol, {})
        total = s.get('wins', 0) + s.get('losses', 0)
        return {
            'total': total,
            'win_rate': round(s.get('wins', 0) / total * 100, 1) if total else 0,
            'total_pnl': s.get('total_pnl', 0),
            'avg_score': round(sum(s.get('scores', [0])) / max(1, len(s.get('scores', [1]))), 1),
        }

    def reset_session(self, date_str: str):
        self._data['session'] = {'date': date_str, 'pnl': 0.0, 'trades': 0}
        self._save()

    def summary(self) -> str:
        lines = ['=== Trade Memory ===']
        for sym, s in sorted(self._data['symbols'].items()):
            total = s['wins'] + s['losses']
            if total < 2: continue
            wr = s['wins'] / total * 100
            lines.append(f'  {sym}: {total} صفقة | WR={wr:.0f}% | PnL=${s["total_pnl"]:+.0f}')
        return '\n'.join(lines)


class RiskManager:
    def __init__(self, max_open_trades=2):
        # ── لا قيم ثابتة — كل شيء يُحسب من الرصيد الحقيقي ──────────
        # يُحدَّث عند أول قراءة رصيد من IBKR في update_from_balance()
        self.max_daily_loss    = 0.0   # يُحسب: balance × loss_pct
        self.max_option_cost   = 0.0   # يُحسب: balance × cost_pct
        self.max_position_size = 0.50
        self.max_open_trades   = max_open_trades
        self.max_daily_trades  = 20
        self.daily_pnl         = 0.0
        self.daily_trades      = 0
        self.daily_losses      = 0
        self.open_trades       = 0
        self.trade_log         = []
        self.active_positions  = {}
        # نسب مئوية يتحكم فيها المستخدم من الواجهة
        self.loss_pct  = 0.10   # 10% خسارة يومية — يُغيَّر من الواجهة
        self.cost_pct  = 0.50   # 50% حجم صفقة — يُغيَّر من الواجهة
        # ── نطاق تكلفة العقد المقبول ──────────────────────
        self.min_contract_cost = 50.0
        self.max_contract_cost = 150.0
        # ── تتبع الخسائر المتتالية والـ Expectancy ─────────
        self.consecutive_loss  = 0
        self._wins_pnl         = []
        self._losses_pnl       = []
        # ── ذاكرة دائمة ────────────────────────────────────
        self.memory            = TradeMemory()
        # ── Dynamic Risk Control ────────────────────────────
        self._peak_balance     = 0.0
        self._drawdown_pct     = 0.0
        self._balance_set      = False  # هل الرصيد الحقيقي تم تعيينه؟
        # ── Thread safety ───────────────────────────────────
        self._lock             = __import__('threading').Lock()

    def update_from_balance(self, balance: float):
        """
        يُحدَّث عند كل قراءة رصيد من IBKR.
        يحسب جميع الحدود تلقائياً من الرصيد الفعلي.
        """
        if balance <= 0:
            return
        # ✅ تكيّف cost_pct مع حجم الرصيد — رصيد صغير يحتاج نسبة أعلى
        # لكن لا نُغيِّر cost_pct إذا عيَّنه المستخدم يدوياً (> 0.05)
        if balance < 500 and self.cost_pct > 0.30:
            # رصيد صغير: استخدم 90% كحد أقصى لتتمكن من الدخول
            _eff_cost = min(self.cost_pct, 0.90)
        else:
            _eff_cost = self.cost_pct
        self.max_daily_loss  = round(balance * self.loss_pct, 2)
        self.max_option_cost = round(balance * _eff_cost, 2)
        # ✅ min_contract_cost ديناميكي — يتكيف مع الرصيد
        if balance < 300:
            self.min_contract_cost = 10.0   # رصيد صغير جداً
        elif balance < 1000:
            self.min_contract_cost = 20.0   # رصيد متوسط
        else:
            self.min_contract_cost = 50.0   # رصيد طبيعي
        if balance > self._peak_balance:
            self._peak_balance = balance
        if self._peak_balance > 0:
            self._drawdown_pct = (self._peak_balance - balance) / self._peak_balance
        self._balance_set = True

    def can_trade(self, account_balance, symbol=None, direction=None):
        """فحص إدارة المخاطر — يشمل الذاكرة الدائمة + Dynamic Drawdown Control"""
        # تحديث الحدود إذا لم يُعيَّن بعد أو تغيّر الرصيد
        if not self._balance_set or account_balance != getattr(self, '_last_checked_balance', 0):
            self.update_from_balance(account_balance)
            self._last_checked_balance = account_balance

        # ── تحديث Peak Balance و Drawdown ──────────────────────────
        if account_balance > 0:
            if account_balance > self._peak_balance:
                self._peak_balance = account_balance
            if self._peak_balance > 0:
                self._drawdown_pct = (self._peak_balance - account_balance) / self._peak_balance

        # ① حد الخسارة اليومية
        _daily_limit = account_balance * self.loss_pct if account_balance > 0 else self.max_daily_loss
        if self.daily_pnl <= -_daily_limit:
            return False, f"حد الخسارة اليومية (${_daily_limit:,.0f} = {self.loss_pct*100:.0f}% من الرصيد)"

        # ② حد الصفقات اليومية
        if self.daily_trades >= self.max_daily_trades:
            return False, f"حد {self.max_daily_trades} صفقة يومياً"

        # ③ خسائر متتالية — يتوقف بعد 5
        if self.consecutive_loss >= 5:
            return False, "5 خسائر متتالية — إيقاف حتى الغد"

        # ④ حد الصفقات المفتوحة
        if self.open_trades >= self.max_open_trades:
            return False, f"الحد الأقصى {self.max_open_trades} صفقات مفتوحة"

        # ⑤ Dynamic Drawdown Control — يُخفف الحجم تلقائياً عند الـ drawdown
        # drawdown > 8% → لا صفقات جديدة (حماية الرأسمال)
        if self._drawdown_pct > 0.08:
            return False, f"Drawdown {self._drawdown_pct*100:.1f}% > 8% — حماية الرأسمال"

        if symbol and direction:
            # Strict execution mode:
            # RiskManager يحمي فقط ولا ينسخ منطق الاستراتيجية
            if symbol in self.active_positions:
                return False, f"{symbol} مفتوح بالفعل"

        return True, "ok"

    def memory_score_boost(self, symbol: str, hour_est: int, regime: str = 'normal') -> int:
        """
        تعديل الـ score من الذاكرة الدائمة:
        أداء الرمز + أداء الساعة + أداء الـ regime
        يُستدعى من scan loop قبل قرار الدخول
        """
        boost = 0
        boost += self.memory.symbol_score_boost(symbol)
        boost += self.memory.hour_score_boost(hour_est)
        boost += self.memory.regime_score_boost(regime)
        return boost

    def session_win_rate(self, window: int = 10) -> float:
        """
        Win rate حقيقي لآخر N صفقة في الجلسة الحالية (rolling).
        window=10: آخر 10 صفقات فقط — حساس للأداء الحالي
        ✅ إصلاح: كان يخلط بين قائمتي الربح والخسارة — أصبح يحسب صحيحاً
        """
        total = len(self._wins_pnl) + len(self._losses_pnl)
        if total < 3:
            return 0.5
        # نأخذ عدد الأرباح في آخر window صفقة بشكل تقريبي صحيح
        _recent_wins = len(self._wins_pnl[-window:])
        _recent_loss = len(self._losses_pnl[-max(0, window - len(self._wins_pnl)):])
        _total_recent = min(total, window)
        if _total_recent == 0:
            return 0.5
        return _recent_wins / _total_recent

    def dynamic_cost_pct(self, base_cost_pct: float) -> float:
        """
        يُخفض حجم الصفقة عند Drawdown أو خسائر متتالية:
        drawdown 4-8%:       خصم 25%
        drawdown > 8%:       خصم 50%
        consecutive_loss 2:  خصم 30%
        consecutive_loss 3+: خصم 50%
        """
        adj = base_cost_pct
        if self._drawdown_pct > 0.08:
            adj *= 0.50
        elif self._drawdown_pct > 0.04:
            adj *= 0.75
        if self.consecutive_loss >= 3:
            adj *= 0.50
        elif self.consecutive_loss >= 2:
            adj *= 0.70
        return round(max(adj, base_cost_pct * 0.20), 4)

    def adaptive_position_size(self, base_cost_pct: float) -> float:
        """Strict mode: لا تغيير ديناميكي على حجم الصفقة خارج حماية drawdown الأساسية."""
        return round(base_cost_pct, 4)

    def adaptive_min_score(self, base_min_score: int) -> int:
        """Strict mode: الحد الأدنى للسكور يأتي من الاستراتيجية فقط."""
        return int(base_min_score)

    def calc_contracts(self, account_balance, option_premium):
        """
        عدد العقود = cost_pct من الرصيد ÷ تكلفة العقد
        ✅ يتكيف مع أي رصيد — يعيد 1 على الأقل إذا كان الرصيد يكفي
        """
        if option_premium <= 0 or account_balance <= 0:
            return 0
        per_contract = option_premium * 100
        if per_contract <= 0:
            return 0
        # ✅ إصلاح: لا نرفض بناءً على min/max — نحسب فقط
        budget = account_balance * self.cost_pct
        budget_n = int(budget / per_contract)
        # إذا الميزانية لا تكفي لعقد واحد لكن الرصيد الكلي يكفي → عقد واحد
        if budget_n <= 0 and account_balance >= per_contract:
            budget_n = 1
        ABSOLUTE_MAX = getattr(self, '_max_contracts_override', 1)
        return min(max(0, budget_n), ABSOLUTE_MAX)

    def register(self, trade):
        with self._lock:
            self.open_trades  += 1
            self.daily_trades += 1
            self.trade_log.append(trade)
            sym = trade.get('symbol')
            if sym:
                self.active_positions[sym] = trade.get('opt_type')

    def close(self, pnl, symbol=None, hour_est: int = -1,
              regime: str = 'normal', score: float = 0, why_tags: list = None):
        with self._lock:
            self.open_trades = max(0, self.open_trades - 1)
            self.daily_pnl  += pnl
            if pnl < 0:
                self.daily_losses    += 1
                self.consecutive_loss += 1
                self._losses_pnl.append(pnl)
            else:
                self.consecutive_loss = 0
                self._wins_pnl.append(pnl)
            if symbol and symbol in self.active_positions:
                del self.active_positions[symbol]
        # ✅ سجّل في الذاكرة الدائمة (خارج اللوك لأنها تكتب ملف)
        if symbol:
            self.memory.record(symbol, pnl, hour_est, regime, score, why_tags or [])

    def get_expectancy(self) -> float:
        """Expectancy = E[ربح] — أهم مقياس للاستراتيجية"""
        total = len(self._wins_pnl) + len(self._losses_pnl)
        if total == 0:
            return 0.0
        wr  = len(self._wins_pnl) / total
        avg_w = sum(self._wins_pnl) / len(self._wins_pnl) if self._wins_pnl else 0
        avg_l = sum(self._losses_pnl) / len(self._losses_pnl) if self._losses_pnl else 0
        return round(wr * avg_w + (1 - wr) * avg_l, 2)

    def add_pnl(self, pnl: float):
        """تحديث daily_pnl بأمان من أي thread (للـ partial closes)"""
        with self._lock:
            self.daily_pnl += pnl

    def reset_daily(self):
        with self._lock:
            self.daily_pnl        = 0.0
            self.open_trades      = 0
            self.daily_trades     = 0
            self.daily_losses     = 0
            self.consecutive_loss = 0
            self.active_positions = {}


# ===================================================
# المؤشرات الفنية
# ===================================================

class PositionManager:
    def __init__(self):
        self.positions = {}   # key = trade_id
        self._lock = __import__('threading').Lock()
        self._closing = set()  # ✅ IDs التي يجري إغلاقها — منع التكرار

    def add(self, trade_id, data):
        with self._lock:
            self.positions[trade_id] = data

    def get(self, trade_id):
        with self._lock:
            return self.positions.get(trade_id)

    def remove(self, trade_id):
        with self._lock:
            self._closing.discard(trade_id)
            return self.positions.pop(trade_id, None)

    def get_all(self):
        with self._lock:
            return list(self.positions.values())

    def count(self):
        with self._lock:
            return len(self.positions)

    def mark_closing(self, trade_id) -> bool:
        """
        ✅ احجز الـ position للإغلاق — thread-safe.
        يُعيد True إذا نجح (أول استدعاء).
        يُعيد False إذا كان thread آخر يغلقه بالفعل.
        """
        with self._lock:
            if trade_id in self._closing:
                return False
            self._closing.add(trade_id)
            return True

    def unmark_closing(self, trade_id):
        """رفع الحجز عند الفشل أو الإغلاق الجزئي"""
        with self._lock:
            self._closing.discard(trade_id)

    def check_exits(self, trade_id, current_premium):
        from datetime import datetime as _dt2
        # دائماً EST — الجهاز بالسعودية UTC+3
        try:
            import pytz as _pytz
            _est_tz  = _pytz.timezone('US/Eastern')
            _now_est = _dt2.now(_est_tz)
        except Exception:
            from datetime import timezone, timedelta
            _now_est = _dt2.now(timezone(timedelta(hours=-5)))
        _now_naive = _now_est.replace(tzinfo=None)

        with self._lock:
            pos = self.positions.get(trade_id)
            if not pos: return None

            entry = pos.get('entry_premium', 0)
            if entry <= 0: return None

            # ⏳ GRACE PERIOD — لا خروج أول 5 دقائق (لكن SL يعمل دائماً)
            _entry_dt_str = pos.get('entry_datetime', '')
            _in_grace = False
            if _entry_dt_str:
                try:
                    _et = _dt2.strptime(_entry_dt_str, '%Y-%m-%d %H:%M:%S')
                    _elapsed_sec = (_now_naive - _et).total_seconds()
                    if _elapsed_sec < 300:
                        _in_grace = True
                except Exception:
                    pass

            # SL يعمل حتى في Grace Period
            if _in_grace:
                if current_premium > pos.get('highest', entry):
                    pos['highest'] = current_premium
                _saved_sl = pos.get('stop_loss', 0)
                _strat_g  = pos.get('strategy_type', 'Stock')
                _sl_pct_g = 0.75 if _strat_g == 'Index' else 0.65
                _sl_emergency = _saved_sl if _saved_sl > 0 else round(entry * _sl_pct_g, 2)
                if current_premium <= _sl_emergency:
                    return 'stop_loss'
                return None

            # ── Expiry Exit ───────────────────────────────────────
            expiry = pos.get('expiry', '')
            if expiry:
                try:
                    exp_dt = _dt2.strptime(str(expiry).replace('/','-')[:10]
                                           .replace('-',''), '%Y%m%d')
                    dte = (exp_dt.date() - _now_est.date()).days
                    if dte < 0:
                        return 'expiry_exit'
                    if dte == 0 and _now_est.hour >= 15 and _now_est.minute >= 30:
                        return 'expiry_exit'
                except Exception:
                    pass

            # ── Timeout Exit ──────────────────────────────────────
            if _entry_dt_str:
                try:
                    et = _dt2.strptime(_entry_dt_str, '%Y-%m-%d %H:%M:%S')
                    elapsed_hours = (_now_naive - et).total_seconds() / 3600
                    if elapsed_hours > 4 and pos.get('tp_phase', 0) == 0:
                        return 'timeout_exit'
                    if elapsed_hours > 6:
                        return 'timeout_exit'
                except Exception:
                    pass

            # ── SL حسب نوع الاستراتيجية ──────────────────────────
            _strat = pos.get('strategy_type', 'Stock')
            _sl_pct = 0.75 if _strat in ('Index','Index-ATR') else 0.65

            _saved_tp = pos.get('take_profit', 0)
            if _saved_tp > 0:
                tp1 = _saved_tp
            else:
                tp1 = round(entry * (1.20 if 'Index' in _strat else 1.25), 2)

            _tp_ratio  = pos.get('tp_ratio', 1.50)
            _saved_tp2 = pos.get('take_profit_2', 0)
            tp2   = _saved_tp2 if _saved_tp2 > 0 else round(tp1 * max(1.20, (_tp_ratio / 1.25)), 2)
            phase = pos.get('tp_phase', 0)

            if current_premium > pos.get('highest', entry):
                pos['highest'] = current_premium
            highest = pos.get('highest', entry)

            _saved_sl = pos.get('stop_loss', 0)

            # ══════════════════════════════════════════════════════
            # منطق وقف الخسارة:
            #   Phase 0 (قبل TP1): SL ثابت عند المستوى الأولي فقط
            #   Phase 1 (بعد TP1): SL = Breakeven، ثم 8% Ratchet
            #   Phase 2 (بعد TP2): SL = TP2 × 98%
            # ══════════════════════════════════════════════════════
            _TRAIL_STEP  = 0.08   # 8%  — خطوة التحريك
            _TRAIL_FLOOR = 0.85   # 85% — SL عند 85% من highest

            _original_sl = round(entry * _sl_pct, 2)
            if _saved_sl > 0 and _saved_sl < entry:
                _original_sl = _saved_sl

            _last_trail_at = pos.get('last_trail_at', entry)
            _trail_count   = pos.get('trail_count', 0)

            # ── TP1 → SL إلى Breakeven (يُفحص أولاً) ─────────────
            if phase == 0 and current_premium >= tp1:
                _be = round(entry, 2)
                pos['tp_phase']      = 1
                pos['stop_loss']     = max(_saved_sl if _saved_sl > 0 else _original_sl, _be)
                pos['last_trail_at'] = current_premium
                return 'tp1'

            # ── 8% Ratchet — يعمل فقط بعد TP1 (phase >= 1) ────────
            if phase >= 1:
                _gain_from_last = (highest - _last_trail_at) / _last_trail_at if _last_trail_at > 0 else 0
                if _gain_from_last >= _TRAIL_STEP:
                    _new_sl = round(highest * _TRAIL_FLOOR, 2)
                    _cur_sl = _saved_sl if _saved_sl > 0 else _original_sl
                    if _new_sl > _cur_sl:
                        _trail_count += 1
                        pos['stop_loss']     = _new_sl
                        pos['last_trail_at'] = highest
                        pos['trail_count']   = _trail_count
                        _saved_sl = _new_sl
                    else:
                        pos['last_trail_at'] = highest

            # ── حساب dynamic_sl الفعلي ────────────────────────────
            dynamic_sl = _saved_sl if _saved_sl > 0 else _original_sl
            dynamic_sl = max(dynamic_sl, pos.get('stop_loss', 0))
            pos['stop_loss'] = dynamic_sl

            # ── فحص SL ─────────────────────────────────────────────
            if current_premium <= dynamic_sl:
                return 'trailing_stop_8pct' if _trail_count > 0 else 'stop_loss'

            # ── TP2 → إغلاق كامل ──────────────────────────────────
            if phase >= 1 and current_premium >= tp2:
                pos['tp_phase']  = 2
                pos['stop_loss'] = max(round(tp2 * 0.98, 2), pos.get('stop_loss', 0))
                return 'tp2'

            return None


# ===================================================

# 🔬 BacktestEngine — محاكي أوبشن احترافي
# ===================================================

# ══════════════════════════════════════════════════════════
# استراتيجية أوبشن احترافية — تحلل Greeks + IV + DTE
# ══════════════════════════════════════════════════════════


class BacktestEngine:
    OPTION_IV_DEFAULT = 0.30

    def __init__(self, strategy):
        self.strategy  = strategy
        self.results   = []
        self.equity    = []
        self.log       = []

    @staticmethod
    def estimate_option_premium(price, days_to_exp, iv=0.30):
        # استخدم BS مع IV حقيقي بدل الثابت 0.30
        return _bs_option_price(price, price, days_to_exp, iv, 'C')

    def fetch_real_data(self, ib, symbol, duration="3 M", bar_size="5 mins"):
        """
        جلب بيانات حقيقية من IBKR
        duration: "1 M" | "3 M" | "6 M" | "1 Y"
        bar_size: "5 mins" | "15 mins" | "1 hour"
        """
        contract = _make_contract(symbol)
        try:
            run_in_ib_thread(ib.qualifyContracts, contract)
        except Exception as e:
            raise RuntimeError(f"تعذر qualify {symbol}: {e}")

        bars = run_in_ib_thread(
            ib.reqHistoricalData,
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
        )
        if not bars:
            raise RuntimeError(f"لا بيانات لـ {symbol} — تحقق من الاتصال")

        opens_  = [b.open  for b in bars]
        closes  = [b.close for b in bars]
        highs   = [b.high  for b in bars]
        lows    = [b.low   for b in bars]
        volumes = [int(b.volume) for b in bars]
        dates   = [b.date  for b in bars]
        return opens_, closes, highs, lows, volumes, dates

    def generate_sample_data(self, bars=400, seed=42):
        """بيانات وهمية كـ fallback فقط"""
        import random, math
        random.seed(seed)
        price  = 450.0
        closes, highs, lows, opens_, volumes = [], [], [], [], []
        for i in range(bars):
            ret   = random.gauss(0.0002, 0.008)
            price = max(price * math.exp(ret), 10)
            spread = price * random.uniform(0.001, 0.005)
            o = price * (1 + random.gauss(0, 0.001))
            h = max(o, price) + random.uniform(0, spread)
            l = min(o, price) - random.uniform(0, spread)
            opens_.append(round(o, 2)); closes.append(round(price, 2))
            highs.append(round(h, 2));  lows.append(round(l, 2))
            volumes.append(int(random.uniform(50_000, 300_000)))
        return opens_, closes, highs, lows, volumes

    def run(self, opens_5m, closes_5m, highs_5m, lows_5m, volumes_5m,
            initial_balance=10_000, risk_pct=0.01, days_to_exp=7,
            progress_cb=None):
        import math
        self.results = []; self.equity = [(0, initial_balance)]; self.log = []
        balance = initial_balance
        daily_trades = 0; last_day = 0
        win_count = 0; loss_count = 0
        max_dd_peak = initial_balance; max_dd = 0.0
        total_bars = len(closes_5m); LOOKBACK = 60
        _cnt = {'regime': 0, 'bias': 0, 'score': 0, 'entry': 0, 'bs': 0}

        # بناء الإطارات الأعلى من 5m
        def _resample(o, h, l, c, v, factor):
            ro, rh, rl, rc, rv = [], [], [], [], []
            for i in range(0, len(c) - factor + 1, factor):
                ro.append(o[i]); rh.append(max(h[i:i+factor]))
                rl.append(min(l[i:i+factor])); rc.append(c[i+factor-1])
                rv.append(sum(v[i:i+factor]))
            return ro, rh, rl, rc, rv

        o15,h15,l15,c15,v15 = _resample(opens_5m,highs_5m,lows_5m,closes_5m,volumes_5m,3)
        o1h,h1h,l1h,c1h,_   = _resample(opens_5m,highs_5m,lows_5m,closes_5m,volumes_5m,12)
        od, hd, ld, cd,  _   = _resample(opens_5m,highs_5m,lows_5m,closes_5m,volumes_5m,78)

        for i in range(LOOKBACK, total_bars, 3):
            if progress_cb:
                progress_cb(int((i - LOOKBACK) / (total_bars - LOOKBACK) * 100))
            day_idx = i // 78
            if day_idx != last_day:
                daily_trades = 0; last_day = day_idx
            if daily_trades >= 3:
                continue

            c = closes_5m[:i+1]; h = highs_5m[:i+1]
            l = lows_5m[:i+1];   v = volumes_5m[:i+1]
            op = opens_5m[:i+1]
            price = closes_5m[i]

            i15=i//3; i1h=i//12; id_=i//78
            _c15=c15[:i15+1]; _h15=h15[:i15+1]; _l15=l15[:i15+1]; _o15=o15[:i15+1]; _v15=v15[:i15+1]
            _c1h=c1h[:i1h+1]; _h1h=h1h[:i1h+1]; _l1h=l1h[:i1h+1]; _o1h=o1h[:i1h+1]
            _cd=cd[:id_+1];   _hd=hd[:id_+1];   _ld=ld[:id_+1];   _od=od[:id_+1]

            score_data = None
            if len(_cd)>=30 and len(_c1h)>=20 and len(_c15)>=20:
                try:
                    prev_h=_hd[-2] if len(_hd)>=2 else None
                    prev_l=_ld[-2] if len(_ld)>=2 else None
                    prev_c=_cd[-2] if len(_cd)>=2 else None
                    zr = self.strategy.analyze(
                        _od,_hd,_ld,_cd, _o1h,_h1h,_l1h,_c1h,
                        _o15,_h15,_l15,_c15,_v15, price,
                        prev_day_high=prev_h, prev_day_low=prev_l, prev_day_close=prev_c)
                    if zr: score_data = {**zr, 'zr_confirmed': True}
                except Exception: pass

            if score_data is None:
                regime = self.strategy.detect_regime(c,h,l,v)
                self.strategy.current_regime = regime
                if regime == 'super_choppy_skip': _cnt['regime']+=1; continue
                bias = self.strategy.get_market_bias(c,h,l,v)
                if bias == 'no_trade': _cnt['bias']+=1; continue
                if bias == 'neutral': bias = 'bullish'
                score_data = self.strategy.score_symbol_adaptive(c,h,l,v,bias)
                if score_data is None: _cnt['score']+=1; continue

            direction = score_data['direction']
            score     = score_data.get('score', 0)
            regime    = score_data.get('regime', 'normal')
            reasons   = score_data.get('why', '').split(' | ')
            self.strategy.current_regime = regime

            confirmed,_,entry_why = self.strategy.check_entry_adaptive(
                op[-20:],c[-20:],h[-20:],l[-20:],v[-20:],direction)
            if not confirmed: _cnt['entry']+=1; continue

            _atr = calc_atr(h[-20:],l[-20:],c[-20:],5)
            # ── IV حقيقي من التقلب التاريخي بدل الثابت 30% ──
            _iv_real = _calc_historical_iv(c[-40:]) if len(c) >= 40 else 0.25
            premium = _bs_option_price(price, price, max(days_to_exp,0), _iv_real,
                                       'C' if direction=='CALL' else 'P')
            if premium <= 0: continue

            risk_budget = balance * risk_pct
            contracts   = max(1, min(int(risk_budget/(premium*100)), 5))

            exit_bar=None; exit_premium=None; exit_reason=None
            max_future = min(i+78, total_bars-1)
            tp1_p=round(premium*1.25,2); tp2_p=round(premium*2.00,2)
            sl_init=round(premium*0.70,2)
            phase=0; highest=premium; partial_pnl=0.0; rem=contracts

            for j in range(i+1, max_future):
                pct = (closes_5m[j]-price)/price
                if direction=='PUT': pct=-pct
                lev = min(15, max(8, 365/max(days_to_exp,1)))
                new_p = max(round(premium*(1+pct*lev),2), 0.01)
                if new_p>highest: highest=new_p
                dyn_sl = sl_init if phase==0 else (max(premium,round(highest*0.82,2)) if phase==1 else max(tp1_p,round(highest*0.85,2)))
                if new_p<=dyn_sl:
                    exit_bar=j; exit_premium=dyn_sl
                    exit_reason=['stop_loss','trailing_tp2','trailing_tp3'][phase]; break
                if phase==0 and new_p>=tp1_p:
                    sell=max(1,rem//2); partial_pnl+=(tp1_p-premium)*sell*100
                    rem-=sell; phase=1
                    if rem<=0: exit_bar=j; exit_premium=tp1_p; exit_reason='tp1'; break
                elif phase==1 and new_p>=tp2_p:
                    sell=max(1,rem//2); partial_pnl+=(tp2_p-premium)*sell*100
                    rem-=sell; phase=2
                    if rem<=0: exit_bar=j; exit_premium=tp2_p; exit_reason='tp2'; break

            if exit_bar is None:
                j=max_future-1
                pct=(closes_5m[j]-price)/price
                if direction=='PUT': pct=-pct
                lev=min(15,max(8,365/max(days_to_exp,1)))
                exit_premium=max(round(premium*(1+pct*lev),2),0.01)
                exit_reason='timeout'; exit_bar=j

            # ── PnL مع التكاليف الحقيقية (Spread + Commission + Slippage) ──
            _entry_info = _calc_real_entry_cost(premium, contracts)
            _exit_info  = _calc_real_exit_proceeds(exit_premium, rem)
            # partial closes أيضاً تحتاج تكاليف
            _partial_comm = COMMISSION_PER_CONTRACT * (contracts - rem) * 2 if contracts > rem else 0
            pnl = round(_exit_info['total'] - _entry_info['total'] + partial_pnl - _partial_comm, 2)
            _gross_pnl = round((exit_premium-premium)*rem*100+partial_pnl, 2)
            _cost_drag  = round(_gross_pnl - pnl, 2)
            balance = round(balance+pnl, 2)
            self.strategy.record_trade_result(pnl, regime)
            if pnl>=0: win_count+=1
            else:      loss_count+=1
            if balance>max_dd_peak: max_dd_peak=balance
            dd=max_dd_peak-balance
            if dd>max_dd: max_dd=dd
            daily_trades+=1
            self.equity.append((i,balance))
            self.results.append({
                'entry_bar':i, 'exit_bar':exit_bar, 'symbol':'BT',
                'direction':direction, 'entry_price':price,
                'entry_premium':premium, 'exit_premium':exit_premium,
                'contracts':contracts, 'pnl':pnl,
                'gross_pnl': _gross_pnl, 'cost_drag': _cost_drag,
                'exit_reason':exit_reason,
                'regime':regime, 'score':score,
                'why':' | '.join(reasons[:6]),
                'balance_after':balance, 'phase_reached':phase,
            })
            self.log.append(
                f"Bar {i:4d} | {direction:4s} | {regime:8s} | "
                f"{premium:.2f}->{exit_premium:.2f} | Net:{pnl:+.0f} | Gross:{_gross_pnl:+.0f} | Cost:${_cost_drag:.0f} | {exit_reason}")

        # ── إرجاع النتائج ─────────────────────────────────────
        total_trades = win_count + loss_count
        win_rate     = (win_count / total_trades * 100) if total_trades else 0
        total_pnl    = round(balance - initial_balance, 2)
        gross_profit = sum(t['pnl'] for t in self.results if t['pnl'] > 0)
        gross_loss   = abs(sum(t['pnl'] for t in self.results if t['pnl'] < 0))
        profit_factor= round(gross_profit / gross_loss, 2) if gross_loss else 99.0
        wins_list    = [t['pnl'] for t in self.results if t['pnl'] > 0]
        loss_list    = [t['pnl'] for t in self.results if t['pnl'] < 0]
        avg_win      = round(sum(wins_list)/len(wins_list), 2) if wins_list else 0
        avg_loss     = round(sum(loss_list)/len(loss_list), 2) if loss_list else 0
        max_dd_pct   = round(max_dd / max_dd_peak * 100, 1) if max_dd_peak else 0
        total_return = round(total_pnl / initial_balance * 100, 1)
        # Expectancy — المقياس الأهم
        expectancy   = round((win_rate/100)*avg_win + (1-win_rate/100)*avg_loss, 2)
        total_cost_drag = sum(t.get('cost_drag',0) for t in self.results)
        import math as _m
        if len(self.results) > 1:
            rets   = [t['pnl'] for t in self.results]
            mean_r = sum(rets)/len(rets)
            std_r  = _m.sqrt(sum((r-mean_r)**2 for r in rets)/len(rets)) or 1
            sharpe = round(mean_r / std_r * _m.sqrt(252), 2)
        else:
            sharpe = 0.0
        print(f'[BT] انتهى: {total_trades} صفقة | Balance=${balance:,.0f} | Expectancy=${expectancy:+.2f}')
        return {
            'total_trades':   total_trades,  'win_count':      win_count,
            'loss_count':     loss_count,    'win_rate':       win_rate,
            'total_pnl':      total_pnl,     'total_return':   total_return,
            'max_dd_pct':     max_dd_pct,    'profit_factor':  profit_factor,
            'sharpe':         sharpe,        'avg_win':        avg_win,
            'avg_loss':       avg_loss,      'final_balance':  balance,
            'expectancy':     expectancy,    'total_cost_drag':round(total_cost_drag,2),
            'equity_curve':   self.equity,
            'trades':         self.results,
            'log':            self.log,
            'data_source':    '✅ IBKR حقيقي + تكاليف فعلية — ZoneTrader',
        }
    # ──────────────────────────────────────────────────────────
    def run_real(self, ib, symbol, stock_bars, dates,
                 initial_balance=10_000, max_cost=100,
                 progress_cb=None):
        """
        باك تيست حقيقي بالكامل:
        - stock_bars: قائمة BarData من IBKR (open/high/low/close/volume/date)
        - يجلب سعر الأوبشن الفعلي (bid/ask) من IBKR لكل إشارة
        - يطبق نظام 3 أهداف + SL متحرك على الأسعار الحقيقية
        """
        import math, traceback as _tb

        opens_  = [b.open   for b in stock_bars]
        closes  = [b.close  for b in stock_bars]
        highs   = [b.high   for b in stock_bars]
        lows    = [b.low    for b in stock_bars]
        volumes = [int(b.volume) for b in stock_bars]
        bar_dates = [str(b.date) for b in stock_bars]

        self.results = []; self.equity = [(0, initial_balance)]; self.log = []
        balance = initial_balance
        daily_trades = 0; last_day = 0
        win_count = 0; loss_count = 0
        max_dd_peak = initial_balance; max_dd = 0.0
        total_bars = len(closes); LOOKBACK = 60
        exch = _option_exchange(symbol)
        _opt_cache = {}  # cache: (expiry,strike,right) → (premium, hist_fwd)

        print(f'[BT] بدأ التحليل: {total_bars} شمعة (LOOKBACK={LOOKBACK})')
        _cnt = {'regime':0,'bias':0,'score':0,'entry':0,'bs':0}
        for i in range(LOOKBACK, total_bars):
            if progress_cb:
                progress_cb(int((i - LOOKBACK) / (total_bars - LOOKBACK) * 100))
            if i % 200 == 0:
                print(f'[BT] Bar {i}/{total_bars} | صفقات: {win_count+loss_count} | balance=${balance:,.0f}')

            day_idx = i // 78
            if day_idx != last_day:
                daily_trades = 0; last_day = day_idx
            if daily_trades >= 2:
                continue

            c  = closes[:i+1]; h = highs[:i+1]
            l  = lows[:i+1];   v = volumes[:i+1]
            op = opens_[:i+1]

            # ── فحص الاستراتيجية ──────────────────────────
            regime = self.strategy.detect_regime(c, h, l, v)
            self.strategy.current_regime = regime
            if regime == 'super_choppy_skip':
                _cnt['regime']+=1; continue

            bias = self.strategy.get_market_bias(c, h, l, v)
            if bias == 'no_trade':
                _cnt['bias']+=1; continue
            if bias == 'neutral':
                bias = 'bullish'  # افتراضي عند التعادل

            score_data = self.strategy.score_symbol_adaptive(c, h, l, v, bias)
            if score_data is None:
                _cnt['score']+=1; continue

            direction = score_data['direction']
            price     = closes[i]
            right     = 'C' if direction == 'CALL' else 'P'

            # استخدم آخر 20 شمعة فقط للتأكيد (أحدث البيانات)
            confirmed, e_score, entry_why = self.strategy.check_entry_adaptive(
                op[-20:], c[-20:], h[-20:], l[-20:], v[-20:], direction)
            if not confirmed:
                _cnt['entry']+=1; continue

            # ── Black-Scholes مع IV من التقلب التاريخي ────────
            try:
                bar_date = bar_dates[i]
                # استخرج التاريخ (YYYYMMDD)
                date_str = str(bar_date).replace('-','').replace(' ','')[:8]

                # حساب step وstrike ATM
                # SPX/XSP يستخدم step=5 دائماً
                if symbol in ('SPX', 'XSP'):  step = 5
                elif price > 3000:             step = 25
                elif price > 1000:             step = 10
                elif price > 500:              step = 5
                elif price > 200:              step = 5
                elif price > 100:              step = 2.5
                else:                          step = 1
                atm = round(price / step) * step

                # 0DTE: نفس يوم الإشارة
                from datetime import datetime as _dt, timedelta as _td
                sig_dt = _dt.strptime(date_str, '%Y%m%d')
                if sig_dt.weekday() < 5:
                    expiry = date_str
                else:
                    expiry = (sig_dt + _td(days=7 - sig_dt.weekday())).strftime('%Y%m%d')

                # ── Black-Scholes مع IV من التقلب التاريخي ────────
                # IBKR لا يحتفظ بأسعار عقود منتهية أكثر من 60 يوم
                import math as _math

                # IV من التقلب اليومي لآخر 20 يوم
                if len(c) >= 20:
                    _rets = [_math.log(c[k]/c[k-1]) for k in range(max(1,len(c)-20), len(c)) if c[k-1]>0]
                    _iv = (_math.sqrt(sum(r**2 for r in _rets)/max(len(_rets),1)) * _math.sqrt(252)) if _rets else 0.20
                    _iv = max(0.12, min(_iv, 0.80))
                else:
                    _iv = 0.20

                _T  = 1.0 / 365.0  # 0-2 DTE: يوم واحد
                _r  = 0.045

                def _bs(S, K, T, r, sig, tp):
                    if T <= 0: return max(0.01, (S-K if tp=='C' else K-S))
                    try:
                        d1 = (_math.log(S/K) + (r+0.5*sig**2)*T) / (sig*_math.sqrt(T))
                        d2 = d1 - sig*_math.sqrt(T)
                        N  = lambda x: 0.5*(1+_math.erf(x/_math.sqrt(2)))
                        p  = S*N(d1) - K*_math.exp(-r*T)*N(d2) if tp=='C' else K*_math.exp(-r*T)*N(-d2) - S*N(-d1)
                        return max(0.01, round(p, 2))
                    except: return 0.01

                # ابحث عن strike تحت max_cost
                premium = None; strike_used = None; opt_contract = None
                direction_sign = 1 if right == 'C' else -1
                for n in range(0, 40):
                    _st   = round(atm + n * step * direction_sign, 2)
                    _ckey = (date_str[:6], _st, right)  # cache بالشهر
                    if _ckey in _opt_cache:
                        premium, strike_used = _opt_cache[_ckey]
                        opt_contract = _ckey
                        break
                    _p = _bs(price, _st, _T, _r, _iv, right)
                    if _p * 100 <= max_cost:
                        premium = _p
                        strike_used = _st
                        opt_contract = _ckey
                        _opt_cache[_ckey] = (_p, _st)
                        break

                if premium is None or premium <= 0:
                    _cnt['bs']+=1
                    self.log.append(f"Bar {i} | {direction} | لا عقد BS ≤ ${max_cost} IV={_iv:.0%}")
                    continue
                print(f'[BT] ✅ إشارة Bar{i} {direction} K={strike_used} P=${premium:.2f} IV={_iv:.0%}')

                contracts = 1  # عقد واحد فقط لكل صفقة

                # ── محاكاة الأيام التالية بـ BS على أسعار السهم الحقيقية ───
                _future_prices = closes[i+1 : min(i+79, total_bars)]
                future_bars = []
                for _fi, _fp in enumerate(_future_prices):
                    _T_rem = max(0.001, _T - _fi/(78*365))
                    _op = _bs(_fp, strike_used, _T_rem, _r, _iv, right)
                    future_bars.append(type('B', (), {'open':_op,'close':_op,'date':str(i+_fi)})())


            except Exception as fe:
                self.log.append(f"Bar {i} | IBKR error: {fe}")
                continue

            # ── محاكاة الخروج على الأسعار الحقيقية ───────────
            tp1_p  = round(premium * 1.25, 2)
            tp2_p  = round(tp1_p * 1.20, 2)       # ✅ TP2 = TP1 × 1.20 (متسق مع execute_trade)
            sl_p   = round(premium * 0.70, 2)  # -30% — موحَّد مع strategy.calc_adaptive_stops
            phase  = 0; highest = premium
            partial_pnl = 0.0; rem = contracts
            exit_premium = None; exit_reason = None

            for fb in future_bars:
                cur = round((fb.open + fb.close) / 2, 2)
                if cur <= 0: continue
                if cur > highest: highest = cur

                # SL متحرك — متسق مع check_exits
                if phase == 0:
                    dyn_sl = sl_p
                elif phase == 1:
                    dyn_sl = max(premium, round(highest * 0.82, 2))
                else:
                    dyn_sl = max(tp1_p, round(highest * 0.85, 2))

                if cur <= dyn_sl:
                    exit_premium = dyn_sl
                    exit_reason  = ['stop_loss','trailing_tp2','trailing_tp3'][phase]
                    break

                if phase == 0 and cur >= tp1_p:
                    sell = max(1, rem // 2)
                    partial_pnl += (tp1_p - premium) * sell * 100
                    rem -= sell; phase = 1
                    if rem <= 0:
                        exit_premium = tp1_p; exit_reason = 'tp1'; break

                elif phase == 1 and cur >= tp2_p:
                    sell = max(1, rem // 2)
                    partial_pnl += (tp2_p - premium) * sell * 100
                    rem -= sell; phase = 2
                    if rem <= 0:
                        exit_premium = tp2_p; exit_reason = 'tp2'; break

            if exit_premium is None:
                fb = future_bars[-1]
                exit_premium = max(round((fb.open + fb.close) / 2, 2), 0.01)
                exit_reason  = 'timeout'

            pnl = round((exit_premium - premium) * rem * 100 + partial_pnl, 2)
            balance = round(balance + pnl, 2)
            self.strategy.record_trade_result(pnl, regime)

            if pnl >= 0: win_count += 1
            else:        loss_count += 1
            if balance > max_dd_peak: max_dd_peak = balance
            dd = max_dd_peak - balance
            if dd > max_dd: max_dd = dd

            daily_trades += 1
            self.equity.append((i, balance))
            self.results.append({
                'entry_bar': i, 'symbol': symbol,
                'direction': direction, 'right': right,
                'entry_date': bar_dates[i],
                'entry_price': price, 'strike': strike_used,
                'expiry': expiry,
                'entry_premium': premium,
                'exit_premium': exit_premium,
                'contracts': contracts, 'pnl': pnl,
                'exit_reason': exit_reason,
                'phase_reached': phase,
                'regime': regime,
                'score': score_data.get('score', 0),
                'why': " | ".join(entry_why),
                'balance_after': balance,
                'data_type': 'REAL_IBKR',
            })
            self.log.append(
                f"{bar_dates[i]} | {direction:4s} K={strike_used} Exp={expiry} | "
                f"${premium:.2f}→${exit_premium:.2f} | PnL:{pnl:+.0f} | {exit_reason}")

        total_trades  = win_count + loss_count
        win_rate      = (win_count / total_trades * 100) if total_trades else 0
        total_pnl     = round(balance - initial_balance, 2)
        gross_profit  = sum(t['pnl'] for t in self.results if t['pnl'] > 0)
        gross_loss    = abs(sum(t['pnl'] for t in self.results if t['pnl'] < 0))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0
        wins_list     = [t['pnl'] for t in self.results if t['pnl'] > 0]
        loss_list     = [t['pnl'] for t in self.results if t['pnl'] < 0]
        avg_win       = round(sum(wins_list)/len(wins_list), 2) if wins_list else 0
        avg_loss      = round(sum(loss_list)/len(loss_list), 2) if loss_list else 0
        max_dd_pct    = round(max_dd / max_dd_peak * 100, 1) if max_dd_peak else 0
        total_return  = round(total_pnl / initial_balance * 100, 1)
        if len(self.results) > 1:
            rets   = [t['pnl'] for t in self.results]
            mean_r = sum(rets)/len(rets)
            std_r  = (sum((r-mean_r)**2 for r in rets)/len(rets))**0.5 or 1
            sharpe = round(mean_r / std_r * (252**0.5), 2)
        else:
            sharpe = 0.0
        return {
            'total_trades':  total_trades,  'win_count':    win_count,
            'loss_count':    loss_count,    'win_rate':     win_rate,
            'total_pnl':     total_pnl,     'total_return': total_return,
            'max_dd_pct':    max_dd_pct,    'profit_factor':profit_factor,
            'sharpe':        sharpe,        'avg_win':      avg_win,
            'avg_loss':      avg_loss,      'final_balance':balance,
            'equity_curve':  self.equity,
            'trades':        self.results,
            'log':           self.log,
            'data_source':   f'✅ IBKR حقيقي — {symbol} bid/ask تاريخي',
        }

        # (كود مكرر غير قابل للوصول — حُذف)

    def generate_sample_data(self, bars=3000, seed=42):
        """
        بيانات وهمية واقعية — 3000 شمعة 5m ≈ سنة كاملة
        تضمن > 30 يوم Daily لـ analyze()
        """
        import random, math as _m
        random.seed(seed)
        price = 450.0; trend = 0.0002
        c, h, l, o, v = [], [], [], [], []
        for i in range(bars):
            if i % 200 == 0:
                trend = random.choice([0.0003, -0.0002, 0.0001, -0.0003, 0.0004])
            ret = trend + random.gauss(0, 0.007)
            price = max(price * _m.exp(ret), 10)
            sp = price * random.uniform(0.001, 0.004)
            op = price * (1 + random.gauss(0, 0.001))
            h.append(round(max(op, price) + random.uniform(0, sp), 2))
            l.append(round(min(op, price) - random.uniform(0, sp), 2))
            o.append(round(op, 2)); c.append(round(price, 2))
            v.append(int(random.uniform(50_000, 300_000)))
        return o, c, h, l, v

    def run_real_mtf(self, sym,
                     opens_5m, highs_5m, lows_5m, closes_5m, volumes_5m, dates_5m,
                     opens_d,  highs_d,  lows_d,  closes_d,
                     opens_1h, highs_1h, lows_1h, closes_1h,
                     initial_balance=10_000, max_cost=500,
                     progress_cb=None):
        """
        ══════════════════════════════════════════════════════════════
        الباك تست الحقيقي — يستخدم analyze() الكاملة مع MTF حقيقي
        ══════════════════════════════════════════════════════════════
        المدخلات:
          - 5m/15m: للتنفيذ والمحاكاة
          - 1H:     للـ Order Blocks
          - Daily:  للـ Market Structure
        المخرجات: نفس dict الموحّد مع _update_ui()
        """
        import math as _m

        self.results = []; self.equity = [(0, initial_balance)]; self.log = []
        balance = initial_balance
        win_count = loss_count = 0
        max_dd_peak = initial_balance; max_dd = 0.0
        daily_trades = 0; last_day_key = ''

        # تحديد step السعر لـ Black-Scholes ATM strike
        def _get_step(p):
            if p > 3000: return 25
            if p > 1000: return 10
            if p > 500:  return 5
            if p > 100:  return 2.5
            return 1

        # Black-Scholes بسيط
        def _bs(S, K, T, r, sig, right):
            if T <= 0: return max(0.01, (S-K if right=='C' else K-S))
            try:
                d1 = (_m.log(S/K) + (r+0.5*sig**2)*T) / (sig*_m.sqrt(T))
                d2 = d1 - sig*_m.sqrt(T)
                N  = lambda x: 0.5*(1+_m.erf(x/_m.sqrt(2)))
                p  = S*N(d1) - K*_m.exp(-r*T)*N(d2) if right=='C' else K*_m.exp(-r*T)*N(-d2) - S*N(-d1)
                return max(0.01, round(p, 2))
            except:
                return 0.01

        n5  = len(closes_5m)
        n1h = len(closes_1h)
        nd  = len(closes_d)

        # ── حساب النسب الحقيقية بين الإطارات ──────────────────────────
        RATIO_1H = max(1, round(n5 / max(n1h, 1)))
        RATIO_D  = max(1, round(n5 / max(nd,  1)))

        # ── LOOKBACK: ابدأ بعد توفر 30 Daily + 20 1H + 20 15m ──────────
        LOOKBACK_5M = max(60, RATIO_D * 60)  # 60 يوم: context كافٍ للـ Market Structure
        if LOOKBACK_5M >= n5:
            print(f'[BT-MTF] ⚠ بيانات غير كافية: n5={n5} LOOKBACK={LOOKBACK_5M}')
            LOOKBACK_5M = max(60, n5 // 4)

        # ── RiskManager + Memory مخصص للباك تست ──────────────────
        # منفصل عن risk_manager الـ live — لا يؤثر على الذاكرة الحقيقية
        class _BtRisk:
            """نسخة مخففة من RiskManager للباك تست"""
            def __init__(self):
                self._wins = []; self._losses = []
                self.consecutive_loss = 0
                self._peak = 0.0; self._dd_pct = 0.0
                self._sym_hist = {}   # {sym: [pnl, ...]}
                self._hr_hist  = {}   # {hour: [win/loss, ...]}
            def update_balance(self, bal):
                if bal > self._peak: self._peak = bal
                self._dd_pct = (self._peak - bal)/self._peak if self._peak > 0 else 0
            def record(self, sym, pnl, hour, regime):
                win = pnl > 0
                if win:
                    self._wins.append(pnl); self.consecutive_loss = 0
                else:
                    self._losses.append(pnl); self.consecutive_loss += 1
                self._sym_hist.setdefault(sym, []).append(pnl)
                self._hr_hist.setdefault(hour, []).append(win)
            def session_wr(self, w=10):
                recent = (self._wins + self._losses)[-w:]
                if len(recent) < 3: return 0.5
                wins = sum(1 for p in self._wins[-w:] if p in recent)
                return wins / len(recent)
            def adaptive_min_score(self, base):
                wr = self.session_wr()
                total = len(self._wins) + len(self._losses)
                if total < 3: return base
                if wr < 0.40: return base + 3
                if wr < 0.50: return base + 2
                if wr > 0.65: return max(base - 1, 5)
                return base
            def adaptive_contracts(self, base_c):
                wr = self.session_wr()
                total = len(self._wins) + len(self._losses)
                if total < 3: scale = 1.0
                elif wr > 0.70: scale = 1.30
                elif wr > 0.60: scale = 1.10
                elif wr < 0.40: scale = 0.50
                elif wr < 0.50: scale = 0.70
                else:           scale = 1.0
                # Drawdown protection
                if self._dd_pct > 0.08: scale = 0.0   # وقف
                elif self._dd_pct > 0.04: scale *= 0.75
                # Consecutive losses
                if self.consecutive_loss >= 2: scale *= 0.70
                if self.consecutive_loss >= 3: scale = 0.0  # وقف
                return max(0, round(base_c * scale))
            def sym_boost(self, sym):
                h = self._sym_hist.get(sym, [])
                if len(h) < 5: return 0
                wr = sum(1 for p in h if p > 0) / len(h)
                if wr > 0.65: return +2
                if wr < 0.30: return -3
                if wr < 0.40: return -1
                return 0
            def hour_boost(self, hour):
                h = self._hr_hist.get(hour, [])
                if len(h) < 4: return 0
                wr = sum(h)/len(h)
                if wr > 0.65: return +1
                if wr < 0.30: return -2
                return 0
            def can_trade(self):
                if self._dd_pct > 0.08: return False, 'Drawdown > 8%'
                if self.consecutive_loss >= 3: return False, '3 خسائر متتالية'
                return True, 'ok'

        _bt_risk = _BtRisk()
        _bt_risk.update_balance(initial_balance)

        self.results = []; self.equity = [(0, initial_balance)]; self.log = []
        balance = initial_balance
        win_count = loss_count = 0
        max_dd_peak = initial_balance; max_dd = 0.0
        daily_trades = 0; last_day_key = ''
        _cnt = {'no_daily': 0, 'no_1h': 0, 'no_signal': 0,
                'no_premium': 0, 'daily_limit': 0, 'risk_block': 0, 'ok': 0}
        print(f'[BT-MTF] {sym}: n5={n5} n1h={n1h} nd={nd} LOOKBACK={LOOKBACK_5M} ratio_1h≈{RATIO_1H} ratio_d≈{RATIO_D}')

        for i in range(LOOKBACK_5M, n5, 3):  # كل 15 دقيقة = 3 شمعات 5m
            if progress_cb and i % 50 == 0:
                progress_cb(int((i - LOOKBACK_5M) / max(n5 - LOOKBACK_5M, 1) * 100))

            # ── حد يومي 3 صفقات ──────────────────────────────────
            day_key = dates_5m[i][:10] if i < len(dates_5m) else str(i // 78)
            if day_key != last_day_key:
                daily_trades = 0; last_day_key = day_key
            # حد يومي مخصص لكل رمز
            _sym_now = getattr(self.strategy, '_sym', sym)
            if _sym_now in ('QQQ', 'QID', 'PSQ'):
                _daily_max = 1   # QQQ: صفقة واحدة فقط يومياً
            elif _sym_now in ('SPY', 'DIA', 'IWM'):
                _daily_max = 2   # ETFs: صفقتان يومياً
            else:
                _daily_max = 3   # الباقي: 3 صفقات
            if daily_trades >= _daily_max:
                _cnt['daily_limit'] += 1; continue

            price = closes_5m[i]
            if price <= 0: continue

            # ── مؤشرات Daily ─────────────────────────────────────
            id_ = min(i // RATIO_D, nd - 1)
            if id_ < 30:
                _cnt['no_daily'] += 1; continue

            _od = opens_d[:id_+1]; _hd = highs_d[:id_+1]
            _ld = lows_d[:id_+1];  _cd = closes_d[:id_+1]
            prev_h = _hd[-2] if len(_hd) >= 2 else None
            prev_l = _ld[-2] if len(_ld) >= 2 else None
            prev_c = _cd[-2] if len(_cd) >= 2 else None

            # ── مؤشرات 1H ────────────────────────────────────────
            i1h = min(i // RATIO_1H, n1h - 1) if n1h > 0 else 0
            if i1h < 20 or n1h == 0:
                _cnt['no_1h'] += 1; continue

            _o1h = opens_1h[:i1h+1]; _h1h = highs_1h[:i1h+1]
            _l1h = lows_1h[:i1h+1];  _c1h = closes_1h[:i1h+1]

            # ── مؤشرات 15m: مباشرة أو resample ──────────────────────
            # إذا البيانات هي 15m مباشرة (RATIO_1H≈4): استخدمها مباشرة
            # إذا هي 5m (RATIO_1H≈12): resample ×3
            _is_15m_direct = (RATIO_1H <= 5)  # 15m مباشرة: 1H/15m = 4
            if _is_15m_direct:
                # البيانات هي 15m مباشرة — لا حاجة لـ resample
                _o15 = opens_5m[:i+1]
                _h15 = highs_5m[:i+1]
                _l15 = lows_5m[:i+1]
                _c15 = closes_5m[:i+1]
                _v15 = volumes_5m[:i+1]
            else:
                # resample من 5m إلى 15m
                i15 = i // 3
                _o15 = [opens_5m[j]  for j in range(0, min(i,n5-1), 3)][:i15+1]
                _h15 = [max(highs_5m[j:j+3])  for j in range(0, min(i,n5-3), 3)][:i15+1]
                _l15 = [min(lows_5m[j:j+3])   for j in range(0, min(i,n5-3), 3)][:i15+1]
                _c15 = [closes_5m[j+2 if j+2 < n5 else j] for j in range(0, min(i,n5-3), 3)][:i15+1]
                _v15 = [sum(volumes_5m[j:j+3]) for j in range(0, min(i,n5-3), 3)][:i15+1]

            if len(_c15) < 20:
                _cnt['no_signal'] += 1; continue

            # ── SmartDayTradingAnalyzer — الباك تست ──────────────
            # نستخدم نفس المحلل الحي مباشرة بدون wrapper
            if not hasattr(self, '_bt_analyzer'):
                try:
                    from analyzer import SmartDayTradingAnalyzer as _SDTA
                    from analyzer import MarketState, OptionData, Candle
                    from datetime import datetime as _dt
                    self._bt_analyzer = _SDTA()
                    self._bt_analyzer_classes = (MarketState, OptionData, Candle, _dt)
                except Exception as _bte:
                    self._bt_analyzer = None
                    print(f'[BT] خطأ في تحميل SmartDayTradingAnalyzer: {_bte}')

            if not hasattr(self, '_debug_reasons'):
                self._debug_reasons = {}
            if sym not in self._debug_reasons:
                self._debug_reasons[sym] = {}

            zr = None
            if self._bt_analyzer is not None and len(_c15) >= 55:
                try:
                    MarketState, OptionData, Candle, _dt = self._bt_analyzer_classes
                    import math as _m2

                    # بناء الشموع
                    _candles = []
                    for _ci in range(len(_c15)):
                        _candles.append(Candle(
                            open=_o15[_ci], high=_h15[_ci],
                            low=_l15[_ci], close=_c15[_ci],
                            volume=_v15[_ci],
                            timestamp=_dt.now()  # الباك تست لا يحتاج وقتاً دقيقاً
                        ))

                    # ATR بسيط
                    _atr_vals = [max(_h15[k]-_l15[k], abs(_c15[k]-_c15[k-1])) for k in range(1, len(_c15))]
                    _atr14 = sum(_atr_vals[-14:]) / 14 if len(_atr_vals) >= 14 else price * 0.01

                    # EMA بسيط
                    def _bt_ema(arr, p):
                        k = 2/(p+1); e = arr[0]
                        for v in arr[1:]: e = v*k + e*(1-k)
                        return e
                    _ema50  = _bt_ema(_c15, 50) if len(_c15) >= 50 else _c15[-1]
                    _ema200 = _bt_ema(_c15, 200) if len(_c15) >= 200 else _c15[-1]

                    # ADX تقريبي
                    _adx = 20.0
                    if len(_c15) >= 28:
                        _ups = [max(_h15[i]-_h15[i-1],0) for i in range(1,len(_h15))]
                        _dns = [max(_l15[i-1]-_l15[i],0) for i in range(1,len(_l15))]
                        _pdm = sum(_ups[-14:]) / max(sum(_atr_vals[-14:]),1e-9)
                        _mdm = sum(_dns[-14:]) / max(sum(_atr_vals[-14:]),1e-9)
                        _dx = abs(_pdm-_mdm)/max(_pdm+_mdm,1e-9)*100
                        _adx = _dx

                    # حجم
                    _avg_vol = sum(_v15[-20:]) / 20 if len(_v15) >= 20 else (_v15[-1] or 1)

                    # IV تقريبي
                    _iv_bt = 0.25
                    if len(_c15) >= 21:
                        _rts = [_m2.log(_c15[k]/_c15[k-1]) for k in range(len(_c15)-20,len(_c15)) if _c15[k-1]>0]
                        if _rts:
                            _mv = sum(r**2 for r in _rts)/len(_rts)
                            _iv_bt = max(0.15, min(_m2.sqrt(_mv)*_m2.sqrt(252), 1.0))

                    _mkt = MarketState(
                        vix=18.0, adx=_adx,
                        volume=_v15[-1] if _v15 else _avg_vol,
                        avg_volume_20=_avg_vol,
                        ema_50=_ema50, ema_200=_ema200,
                        price=price, atr_14=_atr14,
                        news_risk='LOW'
                    )
                    _opt = OptionData(
                        delta=0.55, gamma=0.08, theta=-0.12, vega=0.35,
                        iv_current=_iv_bt, iv_percentile=min(int(_iv_bt*150), 69),
                        bid=2.90, ask=3.10, last_price=3.0,
                        open_interest=1000, volume=300,
                        days_to_expiry=28, strike=price, option_type='CALL'
                    )
                    _sig = self._bt_analyzer.analyze(
                        sym, _mkt, _candles[:-26], _candles, _opt
                    )
                    from analyzer import TradeSignal as _TS
                    if isinstance(_sig, _TS) and _sig.confidence_score >= 68:
                        zr = {
                            'direction': _sig.direction.value,
                            'regime': 'normal',
                            'score': int(_sig.confidence_score),
                            'why': ' | '.join(_sig.reasons_for_entry[:3]),
                            '_from_smart_analyzer': True,
                        }
                    elif not isinstance(_sig, _TS):
                        _rk = (_sig.rejection_reasons[0].reason if _sig.rejection_reasons else 'rejected')[:45]
                        self._debug_reasons[sym][_rk] = self._debug_reasons[sym].get(_rk, 0) + 1
                except Exception as _ae:
                    _ex_key = 'EX:' + str(_ae)[:50]
                    self._debug_reasons[sym][_ex_key] = self._debug_reasons[sym].get(_ex_key, 0) + 1
                    if self._debug_reasons[sym].get(_ex_key, 0) == 1:
                        print(f'[BT-ERR] {sym}: {_ae}')

            # fallback: strategy.analyze() القديم
            if zr is None:
                self.strategy._sym = sym
                try:
                    zr = self.strategy.analyze(
                        _od, _hd, _ld, _cd,
                        _o1h, _h1h, _l1h, _c1h,
                        _o15, _h15, _l15, _c15, _v15,
                        price,
                        prev_day_high=prev_h, prev_day_low=prev_l, prev_day_close=prev_c)
                except Exception:
                    pass

            if zr is None:
                _cnt['no_signal'] += 1; continue

            direction = zr['direction']   # CALL | PUT
            right     = 'C' if direction == 'CALL' else 'P'
            regime    = zr.get('regime', 'normal')
            score     = zr.get('score', 0)
            why       = zr.get('why', '')

            # ── Risk Control: can_trade? ──────────────────────────
            _can, _reason = _bt_risk.can_trade()
            if not _can:
                _cnt['risk_block'] += 1; continue

            # الحد الأدنى للثقة
            _from_sa = zr.get('_from_smart_analyzer', False)
            if _from_sa:
                _adaptive_min = 68   # حد SmartDayTradingAnalyzer
            else:
                _base_min = getattr(self.strategy, 'MIN_SCORE', 10)
                _adaptive_min = _bt_risk.adaptive_min_score(_base_min)

            # ── Memory boost: رمز + ساعة ──────────────────────────
            try:
                _hr = int(day_key[11:13]) if len(day_key) > 10 else 10
            except Exception:
                _hr = 10
            _score_boost = _bt_risk.sym_boost(sym) + _bt_risk.hour_boost(_hr)
            _final_score  = score + _score_boost

            if _final_score < _adaptive_min:
                _cnt['no_signal'] += 1; continue

            # ── Black-Scholes: سعر الأوبشن ───────────────────────
            if len(closes_5m) >= 20:
                # ✅ نافذة 60 شمعة 15m = ~2 أسبوع → IV أكثر دقة
                _iv_window = min(60, i)
                _rets = [_m.log(closes_5m[k]/closes_5m[k-1])
                         for k in range(max(1, i-_iv_window), i+1) if closes_5m[k-1] > 0]
                _iv_raw = (_m.sqrt(sum(r**2 for r in _rets)/max(len(_rets),1)) * _m.sqrt(252)) if _rets else 0.20
                # ✅ IV حقيقي: الحد الأدنى 0.18 (indexes) أو 0.20 (stocks)
                # IV من 15m قصيرة يُقلّل Implied Volatility الحقيقي
                _iv_floor = 0.18 if sym in getattr(self.strategy.__class__, '_INDEX_SYMS',
                    {'SPX','XSP','SPY','QQQ','IWM','NDX'}) else 0.20
                _iv = max(_iv_floor, min(_iv_raw, 0.80))
            else:
                _iv = 0.22

            step  = _get_step(price)
            atm   = round(price / step) * step
            _T    = 7.0 / 365.0
            _r    = 0.045

            # ابحث عن strike بـ premium ≤ max_cost/100
            premium = None; strike_used = atm
            for n in range(0, 20):
                _st = round(atm + n * step * (1 if right=='C' else -1), 2)
                _p  = _bs(price, _st, _T, _r, _iv, right)
                if _p * 100 <= max_cost:
                    premium = _p; strike_used = _st; break

            if not premium or premium <= 0:
                _cnt['no_premium'] += 1; continue

            contracts = max(1, min(3, int((balance * 0.05) / (premium * 100))))
            # ── Adaptive Position Size من أداء الجلسة ────────────
            contracts = _bt_risk.adaptive_contracts(contracts)
            if contracts <= 0:
                _cnt['risk_block'] += 1; continue
            # ── Warm-up: أول 20 صفقة بعقد واحد فقط ────────────────
            # يحمي رأس المال خلال warm-up period
            if (win_count + loss_count) < 20:
                contracts = 1
            _cnt['ok'] += 1

            # ── SL/TP متوازن — RR=1.5 مع partial close ──────────────
            # بـ WR طبيعي للاستراتيجية (40-50%): Expectancy إيجابي
            _iv_adj = max(_iv, 0.15)
            # ── SL/TP حسب IV — RR ≥ 1.5 دائماً ─────────────────
            if _iv_adj > 0.35:        # IV عالي (>35%)
                sl   = round(premium * 0.60, 2)   # SL -40%
                tp1  = round(premium * 1.60, 2)   # TP1 +60%  RR=1.5
                tp2  = round(premium * 2.20, 2)   # TP2 +120%
            elif _iv_adj > 0.20:      # IV متوسط (20-35%)
                sl   = round(premium * 0.60, 2)   # SL -40%
                tp1  = round(premium * 1.60, 2)   # TP1 +60%  RR=1.5
                tp2  = round(premium * 2.20, 2)   # TP2 +120%
            else:                     # IV منخفض (<20%)
                sl   = round(premium * 0.60, 2)   # SL -40%
                tp1  = round(premium * 1.60, 2)   # TP1 +60%  RR=1.5
                tp2  = round(premium * 2.20, 2)   # TP2 +120%
            phase = 0; highest = premium
            partial_pnl = 0.0; rem = contracts
            exit_premium = None; exit_reason = None

            future_end = min(i + 156, n5)  # يومان تداول (78×2)
            for j in range(i+1, future_end):
                fp  = closes_5m[j]
                pct = (fp - price) / price if price > 0 else 0
                if direction == 'PUT': pct = -pct
                lev = min(15, max(8, 365 / max(7, 1)))
                cur = max(_bs(fp, strike_used, max(_T - (j-i)/(78*365), 0.001), _r, _iv, right), 0.01)
                if cur > highest: highest = cur

                dyn_sl = sl if phase == 0 else (
                    max(premium * 1.05, round(highest * 0.82, 2)) if phase == 1
                    else max(tp1 * 1.05,  round(highest * 0.85, 2)))

                if cur <= dyn_sl:
                    exit_premium = dyn_sl
                    exit_reason  = ['stop_loss', 'trailing_tp2', 'trailing_tp3'][phase]; break

                if phase == 0 and cur >= tp1:
                    sell = max(1, rem // 2)
                    partial_pnl += (tp1 - premium) * sell * 100
                    rem -= sell; phase = 1
                    if rem <= 0: exit_premium = tp1; exit_reason = 'tp1'; break

                elif phase == 1 and cur >= tp2:
                    sell = max(1, rem // 2)
                    partial_pnl += (tp2 - premium) * sell * 100
                    rem -= sell; phase = 2
                    if rem <= 0: exit_premium = tp2; exit_reason = 'tp2'; break

            if exit_premium is None:
                fp = closes_5m[min(i+77, n5-1)]
                exit_premium = max(_bs(fp, strike_used, 0.001, _r, _iv, right), 0.01)
                exit_reason  = 'timeout'

            pnl = round((exit_premium - premium) * rem * 100 + partial_pnl, 2)
            balance = round(balance + pnl, 2)
            self.strategy.record_trade_result(pnl, regime)

            if pnl >= 0: win_count += 1
            else:        loss_count += 1
            if balance > max_dd_peak: max_dd_peak = balance
            dd = max_dd_peak - balance
            if dd > max_dd: max_dd = dd
            daily_trades += 1

            self.equity.append((i, balance))
            self.results.append({
                'entry_bar': i, 'symbol': sym,
                'direction': direction, 'entry_date': day_key,
                'entry_price': price, 'strike': strike_used,
                'entry_premium': premium, 'exit_premium': exit_premium,
                'contracts': contracts, 'pnl': pnl,
                'exit_reason': exit_reason, 'phase_reached': phase,
                'regime': regime, 'score': score,
                'why': why[:80], 'balance_after': balance,
                'data_type': 'MTF_REAL',
                'iv': round(_iv, 3),
            })
            self.log.append(
                f"{day_key} | {direction:4s} K={strike_used} "
                f"IV={_iv:.0%} | ${premium:.2f}→${exit_premium:.2f} "
                f"| PnL:{pnl:+.0f} | {exit_reason} | Score:{score}")

            if _cnt['ok'] % 10 == 0:
                print(f'[BT-MTF] {_cnt["ok"]} صفقة | balance=${balance:,.0f} | '
                      f'WR={win_count/max(win_count+loss_count,1)*100:.0f}%')

        # ── ملخص ──────────────────────────────────────────────────
        # طباعة أسباب الرفض للـ debug
        if hasattr(self, '_debug_reasons') and sym in self._debug_reasons:
            reasons = sorted(self._debug_reasons[sym].items(), key=lambda x: -x[1])[:5]
            print(f'[BT-DEBUG] {sym} — أسباب no_signal:')
            for reason, count in reasons:
                print(f'  [{count:4d}x] {reason}')

        _bt_mode_ok = getattr(self.strategy, '_backtest_mode', None) is not None
        print(f'[BT-MTF] انتهى: {_cnt} | _backtest_mode_support={_bt_mode_ok}')
        total = win_count + loss_count
        wr    = win_count / total * 100 if total else 0
        total_pnl = round(balance - initial_balance, 2)
        gp = sum(t['pnl'] for t in self.results if t['pnl'] > 0)
        gl = abs(sum(t['pnl'] for t in self.results if t['pnl'] < 0))
        pf = round(gp / gl, 2) if gl else 99.0
        wl = [t['pnl'] for t in self.results if t['pnl'] > 0]
        ll = [t['pnl'] for t in self.results if t['pnl'] < 0]
        aw = round(sum(wl)/len(wl), 2) if wl else 0
        al = round(sum(ll)/len(ll), 2) if ll else 0
        mdd_pct = round(max_dd / max_dd_peak * 100, 1) if max_dd_peak else 0
        if len(self.results) > 1:
            rs  = [t['pnl'] for t in self.results]
            mr  = sum(rs)/len(rs)
            sr  = (_m.sqrt(sum((r-mr)**2 for r in rs)/len(rs)) or 1)
            shp = round(mr / sr * _m.sqrt(252), 2)
        else:
            shp = 0.0
        exp = round(wr/100*aw + (1-wr/100)*al, 2) if total else 0
        print(f'[BT-MTF] انتهى: {total} صفقة | Balance=${balance:,.0f} | Expectancy=${exp:+.2f}')

        return {
            'total_trades': total, 'win_count': win_count,
            'loss_count': loss_count, 'win_rate': round(wr, 1),
            'total_pnl': total_pnl,
            'total_return': round(total_pnl / initial_balance * 100, 1),
            'max_dd_pct': mdd_pct, 'profit_factor': pf,
            'sharpe': shp, 'avg_win': aw, 'avg_loss': al,
            'final_balance': balance, 'expectancy': exp,
            'equity_curve': self.equity, 'trades': self.results,
            'log': self.log, 'filter_counts': _cnt,
        }


# ===================================================
# 📊 PerformanceDashboard — لوحة الأداء الاحترافية
# ===================================================

class PerformanceDashboard(QDialog):
    # signals آمنة للتواصل من thread → UI
    sig_btn_text    = pyqtSignal(str)
    sig_btn_enable  = pyqtSignal(bool)
    sig_progress    = pyqtSignal(int)
    sig_status      = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_app = parent  # احفظ reference صريح
        self.setWindowTitle("📊 Performance Dashboard — تحليل الأداء الحقيقي + تكاليف")
        _scr_pd = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1380, _scr_pd.width()  - 80),
                    min(820,  _scr_pd.height() - 80))
        self.move(_scr_pd.x() + 40, _scr_pd.y() + 30)
        self.setStyleSheet("""
            QDialog,QWidget { background-color:#111a26; }
            QLabel  { color:#c8d6e5; font-size:12px; }
            QGroupBox {
                color:#c8d6e5; border:1px solid #0fbcf9;
                border-radius:6px; margin-top:10px; font-size:12px;
                font-weight:bold; background-color:#162030; padding:6px;
            }
            QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 5px; }
            QTableWidget {
                background:#162030; color:#c8d6e5;
                gridline-color:#1e2d3d; border:none; font-size:11px;
            }
            QTableWidget::item:selected { background:#0fbcf9; color:#0a1628; }
            QHeaderView::section {
                background:#0a1628; color:#0fbcf9;
                border:1px solid #1e2d3d; padding:3px; font-size:11px;
            }
            QPushButton {
                background:#0fbcf9; color:#0a1628; border:none;
                padding:7px 14px; border-radius:4px; font-size:12px; font-weight:bold;
            }
            QPushButton:hover { background:#4bcffa; }
            QPushButton:disabled { background:#1e2d3d; color:#485460; }
            QProgressBar {
                background:#1e2d3d; border-radius:3px; height:18px;
                text-align:center; font-size:11px;
            }
            QProgressBar::chunk { background:#0fbcf9; border-radius:3px; }
            QTextEdit {
                background:#0a1628; color:#05c46b;
                border:1px solid #1e2d3d;
                font-family:Consolas,monospace; font-size:10px;
            }
            QSpinBox, QDoubleSpinBox {
                background:#162030; color:#c8d6e5;
                border:1px solid #0fbcf9; border-radius:4px; padding:4px;
            }
            QComboBox {
                background:#162030; color:#c8d6e5;
                border:1px solid #0fbcf9; border-radius:4px; padding:4px;
            }
            QComboBox QAbstractItemView { background:#162030; color:#c8d6e5;
                selection-background-color:#0fbcf9; }
            QSplitter::handle { background:#1e2d3d; }
        """)
        self.engine    = BacktestEngine(SmartStrategyPro())
        self.bt_result = None
        # ✅ cache للاستراتيجيات في الباك تست — instance واحد لكل رمز يحفظ state
        self._bt_strategy_cache: dict = {}
        self._build_ui()

    def _build_ui(self):
        ml = QVBoxLayout(self)
        ml.setSpacing(8); ml.setContentsMargins(10, 10, 10, 10)

        # ── شريط الأدوات ─────────────────────────────────
        top = QHBoxLayout()
        ttl = QLabel("🔬 نظام الباك‑تست الاحترافي — Adaptive Strategy")
        ttl.setStyleSheet("color:#0fbcf9; font-size:17px; font-weight:bold;")
        top.addWidget(ttl); top.addStretch()

        top.addWidget(QLabel("الرصيد:"))
        self.bal_spin = QSpinBox()
        self.bal_spin.setRange(1000, 500_000)
        self.bal_spin.setSingleStep(1000)
        self.bal_spin.setValue(10_000)
        self.bal_spin.setPrefix("$")
        top.addWidget(self.bal_spin)

        top.addWidget(QLabel("مخاطرة%:"))
        self.risk_spin = QDoubleSpinBox()
        self.risk_spin.setRange(0.5, 5.0)
        self.risk_spin.setSingleStep(0.5)
        self.risk_spin.setValue(1.0)
        self.risk_spin.setSuffix("%")
        top.addWidget(self.risk_spin)

        top.addWidget(QLabel("أيام:"))
        self.days_spin = QSpinBox()
        self.days_spin.setRange(1, 30)
        self.days_spin.setValue(7)
        top.addWidget(self.days_spin)

        top.addWidget(QLabel("حد العقد:"))
        self.cost_spin = QSpinBox()
        self.cost_spin.setRange(50, 5000)
        self.cost_spin.setSingleStep(50)
        self.cost_spin.setValue(100)
        self.cost_spin.setPrefix("$")
        top.addWidget(self.cost_spin)

        top.addWidget(QLabel("الرمز:"))
        self.sym_combo = QComboBox()
        for s in ['SPX', 'XSP', 'SPY', 'QQQ', 'IWM', 'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'TSLA', 'META', 'AMD', 'INTC', 'QCOM', 'AVGO', 'TSM', 'MU', 'AMAT', 'LRCX', 'KLAC', 'SMCI', 'NFLX', 'CRM', 'ADBE', 'NOW', 'SNOW', 'PLTR', 'UBER', 'SHOP', 'MELI', 'CRWD', 'JPM', 'BAC', 'GS', 'MS', 'V', 'MA', 'AXP', 'BRK B', 'C', 'WFC', 'XOM', 'CVX', 'OXY', 'SLB', 'GLD', 'JNJ', 'UNH', 'PFE', 'ABBV', 'WMT', 'COST', 'HD', 'TGT', 'MCD', 'XLK', 'XLF', 'XLE', 'ARKK']:
            self.sym_combo.addItem(s)
        top.addWidget(self.sym_combo)

        top.addWidget(QLabel("المدة:"))
        self.dur_combo = QComboBox()
        for lbl, val in [("شهر","1 M"),("3 أشهر","3 M"),("6 أشهر","6 M"),("سنة","1 Y")]:
            self.dur_combo.addItem(lbl, val)
        self.dur_combo.setCurrentIndex(1)  # 3 أشهر افتراضي
        top.addWidget(self.dur_combo)

        top.addWidget(QLabel("الشمعة:"))
        self.barsz_combo = QComboBox()
        for lbl, val in [("5 دقائق","5 mins"),("15 دقيقة","15 mins"),("ساعة","1 hour")]:
            self.barsz_combo.addItem(lbl, val)
        top.addWidget(self.barsz_combo)

        top.addWidget(QLabel("شمعات (وهمي):"))
        self.bars_combo = QComboBox()
        for lbl, val in [("400 (شهر)", 400), ("800 (2 شهر)", 800),
                          ("1200 (3 شهر)", 1200), ("2000 (6 شهر)", 2000)]:
            self.bars_combo.addItem(lbl, val)
        top.addWidget(self.bars_combo)

        self.run_btn = QPushButton("▶ باك‑تست رمز واحد")
        self.run_btn.setStyleSheet(
            "background:#05c46b; color:#0a1628; font-size:13px; font-weight:bold; padding:8px 20px;")
        self.run_btn.clicked.connect(self._run_backtest)
        top.addWidget(self.run_btn)

        # ── زر التدريب العميق: كل الرموز → 5000 صفقة حقيقية ──
        self.deep_train_btn = QPushButton("🎓 تدريب عميق — كل الرموز (IBKR)")
        self.deep_train_btn.setStyleSheet(
            "background:#a29bfe; color:#0a1628; font-size:13px; font-weight:bold; padding:8px 20px;")
        self.deep_train_btn.setToolTip(
            "يجلب سنة كاملة من IBKR لكل الـ 32 رمز\n"
            "يشغّل الاستراتيجية الكاملة (ZoneTrader 4 طبقات)\n"
            "يستهدف 5000+ صفقة حقيقية\n"
            "يحفظ الدروس في bot_memory.json تلقائياً\n"
            "الوقت المتوقع: 20-40 دقيقة")
        self.deep_train_btn.clicked.connect(self._run_deep_training)
        top.addWidget(self.deep_train_btn)
        ml.addLayout(top)

        # ربط signals آمنة بعد إنشاء الـ widgets
        self.sig_btn_text.connect(self.run_btn.setText)
        self.sig_btn_enable.connect(self.run_btn.setEnabled)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        ml.addWidget(self.progress)
        self.sig_progress.connect(self.progress.setValue)
        self.sig_status.connect(self._on_status)  # ✅ إصلاح startTimer

        # شريط تقدم التدريب العميق
        self.deep_progress = QProgressBar()
        self.deep_progress.setValue(0)
        self.deep_progress.setStyleSheet(
            "QProgressBar{background:#1e2d3d;border-radius:3px;height:14px;}"
            "QProgressBar::chunk{background:#a29bfe;border-radius:3px;}")
        self.deep_progress.setVisible(False)
        ml.addWidget(self.deep_progress)

        # مصدر البيانات + حالة التدريب
        self.data_src_lbl = QLabel("⚪ لم يبدأ الباك تيست بعد")
        self.data_src_lbl.setStyleSheet("color:#8395a7; font-size:11px;")
        ml.addWidget(self.data_src_lbl)

        self.deep_status_lbl = QLabel("")
        self.deep_status_lbl.setStyleSheet("color:#a29bfe; font-size:11px; font-weight:bold;")
        self.deep_status_lbl.setVisible(False)
        ml.addWidget(self.deep_status_lbl)

        splitter = QSplitter(Qt.Horizontal)

        # ── اليسار ───────────────────────────────────────
        left_w = QWidget(); left_v = QVBoxLayout(left_w); left_v.setSpacing(6)

        # بطاقات الإحصائيات
        cg = QGroupBox("📈 لوحة الأداء الإحصائية")
        cgrid = QGridLayout(); cgrid.setSpacing(8)
        self._cards = {}
        defs = [
            ("total_trades", "إجمالي الصفقات", "#0fbcf9",  0, 0),
            ("expectancy",   "Expectancy $/صفقة","#05c46b", 0, 1),
            ("total_pnl",    "صافي الربح $",   "#05c46b",  0, 2),
            ("total_return", "العائد %",        "#ffb62e",  0, 3),
            ("max_dd_pct",   "Max Drawdown %",  "#ff5e57",  1, 0),
            ("profit_factor","Profit Factor",   "#a29bfe",  1, 1),
            ("sharpe",       "Sharpe Ratio",    "#0fbcf9",  1, 2),
            ("win_rate",     "Win Rate %",      "#05c46b",  1, 3),
            ("avg_win",      "متوسط الربح $",  "#05c46b",  2, 0),
            ("avg_loss",     "متوسط خسارة $",  "#ff5e57",  2, 1),
            ("win_count",    "صفقات رابحة",    "#05c46b",  2, 2),
            ("loss_count",   "صفقات خاسرة",   "#ff5e57",  2, 3),  # ✅ إصلاح
            ("final_balance","الرصيد النهائي", "#0fbcf9",  3, 0),
        ]
        for key, lbl, clr, row, col in defs:
            card = QWidget()
            card.setStyleSheet("background:#0a1628; border-radius:6px;")
            cv = QVBoxLayout(card); cv.setSpacing(2); cv.setContentsMargins(8,6,8,6)
            l1 = QLabel(lbl); l1.setStyleSheet("color:#8395a7; font-size:10px;")
            l2 = QLabel("--"); l2.setStyleSheet(f"color:{clr}; font-size:16px; font-weight:bold;")
            cv.addWidget(l1); cv.addWidget(l2)
            cgrid.addWidget(card, row, col)
            self._cards[key] = l2
        cg.setLayout(cgrid); left_v.addWidget(cg)

        # جدول النظام التكيفي
        rg = QGroupBox("🌐 أداء الاستراتيجية حسب نظام السوق")
        rv = QVBoxLayout()
        self.regime_tbl = QTableWidget()
        self.regime_tbl.setColumnCount(6)
        self.regime_tbl.setHorizontalHeaderLabels(
            ["النظام", "صفقات", "ربح", "خسارة", "WR%", "PnL $"])
        self.regime_tbl.setMaximumHeight(155)
        self.regime_tbl.horizontalHeader().setStretchLastSection(True)
        rv.addWidget(self.regime_tbl)
        rg.setLayout(rv); left_v.addWidget(rg)

        # جدول الصفقات
        tg = QGroupBox("📋 سجل الصفقات المحاكاة")
        tv = QVBoxLayout()
        self.trades_tbl = QTableWidget()
        self.trades_tbl.setColumnCount(13)
        self.trades_tbl.setHorizontalHeaderLabels([
            "التاريخ", "اتجاه", "سعر السهم", "Strike", "Expiry",
            "Premium دخول", "Premium خروج", "عقود",
            "PnL صافي$", "تكاليف$", "سبب الخروج", "نظام", "Score"])
        self.trades_tbl.horizontalHeader().setStretchLastSection(True)
        tv.addWidget(self.trades_tbl)
        tg.setLayout(tv); left_v.addWidget(tg)

        splitter.addWidget(left_w)

        # ── اليمين: الرسوم البيانية ───────────────────────
        right_w = QWidget(); right_v = QVBoxLayout(right_w); right_v.setSpacing(6)

        # Equity Curve
        eq_g = QGroupBox("📈 منحنى الأسهم — Equity Curve")
        eq_v = QVBoxLayout()
        self.eq_chart = pg.PlotWidget()
        self.eq_chart.setBackground('#0a1628')
        self.eq_chart.showGrid(x=True, y=True, alpha=0.15)
        self.eq_chart.setLabel('left', 'الرصيد $', color='#8395a7', size='9pt')
        self.eq_chart.setLabel('bottom', 'Bar',     color='#8395a7', size='9pt')
        self.eq_chart.setMinimumHeight(230)
        eq_v.addWidget(self.eq_chart); eq_g.setLayout(eq_v); right_v.addWidget(eq_g)

        # Drawdown
        dd_g = QGroupBox("📉 Drawdown من القمة")
        dd_v = QVBoxLayout()
        self.dd_chart = pg.PlotWidget()
        self.dd_chart.setBackground('#0a1628')
        self.dd_chart.showGrid(x=True, y=True, alpha=0.15)
        self.dd_chart.setMinimumHeight(160)
        dd_v.addWidget(self.dd_chart); dd_g.setLayout(dd_v); right_v.addWidget(dd_g)

        # PnL Distribution
        dist_g = QGroupBox("📊 توزيع الصفقات — PnL Distribution")
        dist_v = QVBoxLayout()
        self.dist_chart = pg.PlotWidget()
        self.dist_chart.setBackground('#0a1628')
        self.dist_chart.showGrid(x=False, y=True, alpha=0.15)
        self.dist_chart.setMinimumHeight(150)
        dist_v.addWidget(self.dist_chart)
        dist_g.setLayout(dist_v); right_v.addWidget(dist_g)

        # سجل
        log_g = QGroupBox("📝 سجل التداول")
        lv = QVBoxLayout()
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(110)
        lv.addWidget(self.log_box); log_g.setLayout(lv); right_v.addWidget(log_g)

        splitter.addWidget(right_w)
        splitter.setSizes([680, 680])
        ml.addWidget(splitter)

    def _run_backtest(self):
        if getattr(self, '_bt_running', False):
            return
        self._bt_running = True
        self.run_btn.setEnabled(False)
        self.progress.setValue(0)
        bal  = self.bal_spin.value()
        days = self.days_spin.value()
        bars = self.bars_combo.currentData()

        self._bt_stop = False  # flag للإيقاف الآمن

        def _worker():
            try:
                sym        = self.sym_combo.currentText()
                # ✅ نفس الاستراتيجية التي يستخدمها البوت الحي
                _idx_syms  = getattr(_strategy_module, 'INDEX_SYMBOLS', INDEX_SYMBOLS)
                _use_index = sym in _idx_syms
                # QQQ وNDX: Order Blocks أفضل من Mean Reversion
                # لأنها تتبع أسهم تقنية وليس سوق متنوع
                # ✅ strategy cache — instance واحد لكل رمز يحفظ state بين runs الباك تست
                # يختار تلقائياً: IndexVWAP / Momentum / GapFill / Index / Stock
                try:
                    if sym not in self._bt_strategy_cache:
                        self._bt_strategy_cache[sym] = _get_strategy(sym)
                        self._bt_strategy_cache[sym]._sym = sym
                    _bt_strat = self._bt_strategy_cache[sym]
                    _strat_name = type(_bt_strat).__name__
                except Exception:
                    if sym not in self._bt_strategy_cache:
                        self._bt_strategy_cache[sym] = IndexStrategy() if _use_index else StockStrategy()
                    _bt_strat = self._bt_strategy_cache[sym]
                    _strat_name = type(_bt_strat).__name__

                eng       = BacktestEngine(_bt_strat)
                eng.strategy._sym = sym
                _min_used = _bt_strat.MIN_SCORE
                print(f'[BT] {sym}: {_strat_name}(MIN={_min_used}) — cache=✅')
                dur        = self.dur_combo.currentData()
                bar_sz     = self.barsz_combo.currentData()
                parent_app = getattr(self, '_parent_app', self.parent())
                ib         = getattr(parent_app, 'ib', None)
                connected  = getattr(parent_app, 'connected', False)

                # ══════════════════════════════════════════════════════
                # الأولوية 1: ملف الشارت المحفوظ
                # chart_data/{sym}_{tf}.json — يُحفظ تلقائياً عند فتح الشارت
                # ══════════════════════════════════════════════════════
                import json as _json, os as _os
                _chart_dir  = _os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)), 'chart_data')
                _chart_5m   = _os.path.join(_chart_dir, f'{sym}_5m.json')
                _chart_15m  = _os.path.join(_chart_dir, f'{sym}_15m.json')
                _chart_1h   = _os.path.join(_chart_dir, f'{sym}_1H.json')
                _chart_d    = _os.path.join(_chart_dir, f'{sym}_1D.json')

                def _load_chart_file(path):
                    if not _os.path.exists(path): return None
                    with open(path, 'r', encoding='utf-8') as _f:
                        d = _json.load(_f)
                    return d

                def _to_lists_ibkr(bars_list):
                    o,h,l,c,v,dates=[],[],[],[],[],[]
                    for b in bars_list:
                        o.append(float(b.open)); h.append(float(b.high))
                        l.append(float(b.low));  c.append(float(b.close))
                        v.append(int(getattr(b,'volume',10000)))
                        dates.append(str(b.date))
                    return o,h,l,c,v,dates

                # ── محاولة قراءة ملف الشارت ──────────────────────────
                _d5m = _load_chart_file(_chart_5m) or _load_chart_file(_chart_15m)
                _d1h = _load_chart_file(_chart_1h)
                _dd  = _load_chart_file(_chart_d)

                if _d5m and _dd and len(_d5m.get('closes',[])) >= 100:
                    # ✅ المسار 1: ملف الشارت المحفوظ — نفس البيانات التي يراها البوت
                    _tf_used = _d5m.get('tf', bar_sz)
                    self.sig_btn_text.emit(
                        f'📂 {sym}: قراءة ملف الشارت ({_tf_used})...')
                    print(f'[BT] {sym}: قراءة من chart_data/{sym}_{_tf_used}.json')

                    o5   = _d5m['opens'];  h5 = _d5m['highs']
                    l5   = _d5m['lows'];   c5 = _d5m['closes']
                    v5   = _d5m.get('volumes', [10000]*len(c5))
                    dt5  = _d5m.get('times',   [str(i) for i in range(len(c5))])

                    od  = _dd['opens'];  hd = _dd['highs']
                    ld  = _dd['lows'];   cd = _dd['closes']
                    # أنشئ dates_d من اسم الملف
                    dt_d = _dd.get('times', [str(i) for i in range(len(cd))])

                    if _d1h and len(_d1h.get('closes',[])) >= 20:
                        o1h = _d1h['opens']; h1h = _d1h['highs']
                        l1h = _d1h['lows'];  c1h = _d1h['closes']
                    else:
                        # resample من 5m إذا لا يوجد ملف 1H
                        def _rs(o,h,l,c,v,f):
                            ro,rh,rl,rc,rv=[],[],[],[],[]
                            for i in range(0,len(c)-f+1,f):
                                ro.append(o[i]); rh.append(max(h[i:i+f]))
                                rl.append(min(l[i:i+f])); rc.append(c[i+f-1])
                                rv.append(sum(v[i:i+f]))
                            return ro,rh,rl,rc,rv
                        o1h,h1h,l1h,c1h,_ = _rs(o5,h5,l5,c5,v5,12)
                        self.sig_btn_text.emit(
                            f'⚠ {sym}: لا يوجد ملف 1H — تم resample من 5m')

                    self.sig_btn_text.emit(
                        f'⏳ {sym}: {len(c5)} شمعة {_tf_used} | '
                        f'{len(cd)} يوم | {len(c1h)} ساعة — يحلل...')
                    print(f'[BT] {sym}: bars={len(c5)} daily={len(cd)} 1h={len(c1h)}')

                    result = eng.run_real_mtf(
                        sym=sym,
                        opens_5m=o5, highs_5m=h5, lows_5m=l5,
                        closes_5m=c5, volumes_5m=v5, dates_5m=dt5,
                        opens_d=od,  highs_d=hd,  lows_d=ld,  closes_d=cd,
                        opens_1h=o1h, highs_1h=h1h, lows_1h=l1h, closes_1h=c1h,
                        initial_balance=bal, max_cost=max(50.0, min(bal * 0.05, 200.0)),
                        progress_cb=lambda p: self.sig_progress.emit(p),
                    )
                    _saved_at = _d5m.get('saved_at', '?')
                    result['data_source'] = (
                        f'📂 ملف الشارت — {sym} ({_tf_used}) | '
                        f'حُفظ: {_saved_at} | '
                        f'{len(c5)} شمعة'
                    )

                elif ib and connected:
                    # ✅ المسار 2: IBKR — 15m حقيقية (نفس ما يراه البوت)
                    _wts      = _what_to_show(sym)
                    _wts_d    = _what_to_show(sym, bar_size="daily")
                    _contract = _make_contract(sym)

                    # ── Daily (2Y) — للـ Market Structure ──────────────
                    self.sig_btn_text.emit(f'⏳ {sym}: جلب Daily (2Y)...')
                    bars_d = run_in_ib_thread_long(
                        ib.reqHistoricalData, _contract,
                        endDateTime='', durationStr='2 Y',
                        barSizeSetting='1 day',
                        whatToShow=_wts_d, useRTH=True, formatDate=1, timeout=120)
                    if not bars_d or len(bars_d) < 30:
                        # fallback: جرب MIDPOINT للمؤشرات
                        bars_d = run_in_ib_thread_long(
                            ib.reqHistoricalData, _contract,
                            endDateTime='', durationStr='2 Y',
                            barSizeSetting='1 day',
                            whatToShow="MIDPOINT", useRTH=True, formatDate=1, timeout=120)
                    if not bars_d or len(bars_d) < 30:
                        raise RuntimeError(f'{sym}: Daily bars غير كافية')

                    # ── 1H (6M) — للـ Order Blocks ─────────────────────
                    self.sig_btn_text.emit(f'⏳ {sym}: جلب 1H (6M)...')
                    bars_1h = run_in_ib_thread_long(
                        ib.reqHistoricalData, _contract,
                        endDateTime='', durationStr='6 M',
                        barSizeSetting='1 hour',
                        whatToShow=_wts, useRTH=False, formatDate=1, timeout=120)
                    if (not bars_1h or len(bars_1h) < 10) and _wts == "MIDPOINT":
                        bars_1h = run_in_ib_thread_long(
                            ib.reqHistoricalData, _contract,
                            endDateTime='', durationStr='6 M',
                            barSizeSetting='1 hour',
                            whatToShow="TRADES", useRTH=False, formatDate=1, timeout=120)

                    # ── 15m (6M) — للـ Entry Confirmation ──────────────
                    self.sig_btn_text.emit(f'⏳ {sym}: جلب 15m (6M)...')
                    bars_15m = run_in_ib_thread_long(
                        ib.reqHistoricalData, _contract,
                        endDateTime='', durationStr='6 M',
                        barSizeSetting='15 mins',
                        whatToShow=_wts, useRTH=False, formatDate=1, timeout=300)
                    # fallback 1: TRADES بدل MIDPOINT للمؤشرات
                    if (not bars_15m or len(bars_15m) < 60) and _wts == "MIDPOINT":
                        self.sig_btn_text.emit(f'⚠ {sym}: MIDPOINT فشل — جرب TRADES...')
                        bars_15m = run_in_ib_thread_long(
                            ib.reqHistoricalData, _contract,
                            endDateTime='', durationStr='6 M',
                            barSizeSetting='15 mins',
                            whatToShow="TRADES", useRTH=False, formatDate=1, timeout=300)
                    # fallback 2: bar_sz من UI
                    if not bars_15m or len(bars_15m) < 60:
                        self.sig_btn_text.emit(f'⚠ {sym}: 15m فشل — جلب {bar_sz} ({dur})...')
                        bars_15m = run_in_ib_thread_long(
                            ib.reqHistoricalData, _contract,
                            endDateTime='', durationStr=dur,
                            barSizeSetting=bar_sz,
                            whatToShow=_wts, useRTH=False, formatDate=1, timeout=300)
                    if not bars_15m or len(bars_15m) < 60:
                        raise RuntimeError(f'{sym}: intraday bars غير كافية')

                    o5,h5,l5,c5,v5,dates5 = _to_lists_ibkr(bars_15m)
                    od,hd,ld,cd,_,_       = _to_lists_ibkr(bars_d)
                    o1h,h1h,l1h,c1h,_,_  = _to_lists_ibkr(bars_1h) if bars_1h else ([],[],[],[],[],[])

                    self.sig_btn_text.emit(
                        f'⏳ {sym}: {len(c5)} 15m | {len(cd)} يوم | {len(c1h)} ساعة...')
                    print(f'[BT] {sym}: bars={len(c5)} daily={len(cd)} 1h={len(c1h)}')

                    result = eng.run_real_mtf(
                        sym=sym,
                        opens_5m=o5, highs_5m=h5, lows_5m=l5,
                        closes_5m=c5, volumes_5m=v5, dates_5m=dates5,
                        opens_d=od,  highs_d=hd,  lows_d=ld,  closes_d=cd,
                        opens_1h=o1h, highs_1h=h1h, lows_1h=l1h, closes_1h=c1h,
                        initial_balance=bal, max_cost=max(50.0, min(bal * 0.05, 200.0)),
                        progress_cb=lambda p: self.sig_progress.emit(p),
                    )
                    result['data_source'] = (
                        f'✅ IBKR حقيقي MTF — {sym} | {len(c5)} شمعة {bar_sz}')

                else:
                    # ✅ المسار 3: بيانات وهمية محسّنة
                    self.sig_btn_text.emit('⚠ غير متصل + لا ملف شارت — بيانات وهمية (3000 شمعة)...')
                    o5,c5,h5,l5,v5 = eng.generate_sample_data(bars=3000)

                    def _rs(o,h,l,c,v,f):
                        ro,rh,rl,rc,rv=[],[],[],[],[]
                        for i in range(0,len(c)-f+1,f):
                            ro.append(o[i]);rh.append(max(h[i:i+f]))
                            rl.append(min(l[i:i+f]));rc.append(c[i+f-1])
                            rv.append(sum(v[i:i+f]))
                        return ro,rh,rl,rc,rv

                    o1h,h1h,l1h,c1h,_ = _rs(o5,h5,l5,c5,v5,12)
                    od, hd, ld, cd, _  = _rs(o5,h5,l5,c5,v5,78)
                    dates5 = [str(i) for i in range(len(c5))]

                    result = eng.run_real_mtf(
                        sym='SIM',
                        opens_5m=o5, highs_5m=h5, lows_5m=l5,
                        closes_5m=c5, volumes_5m=v5, dates_5m=dates5,
                        opens_d=od, highs_d=hd, lows_d=ld, closes_d=cd,
                        opens_1h=o1h, highs_1h=h1h, lows_1h=l1h, closes_1h=c1h,
                        initial_balance=bal, max_cost=max(50.0, min(bal * 0.05, 200.0)),
                        progress_cb=lambda p: self.sig_progress.emit(p),
                    )
                    result['data_source'] = '⚠ بيانات وهمية (3000 شمعة)'

                self.sig_progress.emit(100)
                self.bt_result = result
                self.engine    = eng

            except Exception as e:
                import traceback; traceback.print_exc()
                self.bt_result = {'error': f'{type(e).__name__}: {e}'}
            finally:
                import gc; gc.collect()  # تحرير الذاكرة بعد الباك تست
                self._bt_running = False
                self.sig_btn_text.emit('▶ تشغيل الباك‑تست')
                self.sig_btn_enable.emit(True)
                self.sig_status.emit('__update_ui__')

        threading.Thread(target=_worker, daemon=True).start()


    @pyqtSlot(str)
    def _on_status(self, msg):
        """يستقبل رسائل الحالة بأمان على الـ UI thread"""
        if msg == '__update_ui__':
            self._update_ui()
        elif hasattr(self, 'data_src_lbl'):
            self.data_src_lbl.setText(msg)

    @pyqtSlot()
    def _update_ui(self):
        from PyQt5.QtCore import QTimer
        # تأجيل بسيط لتجنب Recursive repaint
        QTimer.singleShot(50, self._do_update_ui)

    def _do_update_ui(self):
        r = self.bt_result
        if not r:
            return
        if r.get("error"):
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "خطأ", f"فشل الباك تيست:\n{r['error']}")
            return
        # عرض مصدر البيانات
        if hasattr(self, "data_src_lbl"):
            self.data_src_lbl.setText(r.get("data_source", ""))
            cl = "#05c46b" if "حقيقية" in r.get("data_source","") else "#ffb62e"
            self.data_src_lbl.setStyleSheet(f"color:{cl}; font-size:11px;")

        def _clr(v): return "#05c46b" if v >= 0 else "#ff5e57"

        self._cards['total_trades'].setText(str(r.get('total_trades', 0)))
        wr = r.get('win_rate', 0)
        self._cards['win_rate'].setText(f"{wr:.1f}%")
        self._cards['win_rate'].setStyleSheet(
            f"color:{'#05c46b' if wr>=52 else '#ffb62e' if wr>=45 else '#ff5e57'}; font-size:16px; font-weight:bold;")
        pnl = r.get('total_pnl', 0)
        self._cards['total_pnl'].setText(f"${pnl:+,.0f}")
        self._cards['total_pnl'].setStyleSheet(
            f"color:{_clr(pnl)}; font-size:16px; font-weight:bold;")
        self._cards['total_return'].setText(f"{r.get('total_return', 0):+.1f}%")
        self._cards['max_dd_pct'].setText(f"{r.get('max_dd_pct', 0):.1f}%")
        pf = r.get('profit_factor', 0)
        self._cards['profit_factor'].setText(f"{pf:.2f}")
        self._cards['profit_factor'].setStyleSheet(
            f"color:{'#05c46b' if pf>=1.3 else '#ffb62e' if pf>=1 else '#ff5e57'};"
            "font-size:16px; font-weight:bold;")
        self._cards['sharpe'].setText(f"{r.get('sharpe', 0):.2f}")
        self._cards['avg_win'].setText(f"${r.get('avg_win', 0):+,.0f}")
        self._cards['avg_loss'].setText(f"${r.get('avg_loss', 0):+,.0f}")
        self._cards['win_count'].setText(str(r.get('win_count', 0)))
        self._cards['loss_count'].setText(str(r.get('loss_count', 0)))
        self._cards['final_balance'].setText(f"${r.get('final_balance', 0):,.0f}")

        # ── Expectancy (المقياس الأهم) ────────────────────────
        exp = r.get('expectancy', 0)
        if hasattr(self, '_cards') and 'expectancy' in self._cards:
            self._cards['expectancy'].setText(f"${exp:+.2f}/صفقة")
            self._cards['expectancy'].setStyleSheet(
                f"color:{'#05c46b' if exp>0 else '#ff5e57'}; font-size:16px; font-weight:bold;")

        # ── تحذير التكاليف ────────────────────────────────────
        cost_drag = r.get('total_cost_drag', 0)
        if cost_drag > 0 and hasattr(self, 'data_src_lbl'):
            gross = sum(t.get('gross_pnl', t.get('pnl',0)) for t in r.get('trades',[]))
            cost_pct = abs(cost_drag / gross * 100) if gross != 0 else 0
            warn = cost_pct > 40
            cost_msg = (f"{'⚠️' if warn else '✅'} تكاليف التداول الفعلية: ${cost_drag:,.0f}"
                        f" ({cost_pct:.0f}% من الأرباح الإجمالية)"
                        f"{'  — مرتفعة جداً!' if warn else ''}")
            src = r.get('data_source','')
            self.data_src_lbl.setText(f"{src} | {cost_msg}")
            self.data_src_lbl.setStyleSheet(
                f"color:{'#ff5e57' if warn else '#05c46b'}; font-size:11px;")

        # Equity Curve
        self.eq_chart.clear()
        eq = r['equity_curve']
        if len(eq) > 1:
            xs = [e[0] for e in eq]; ys = [e[1] for e in eq]
            base_line = self.eq_chart.plot(xs, [ys[0]]*len(xs),
                pen=pg.mkPen(None))
            eq_line = self.eq_chart.plot(xs, ys,
                pen=pg.mkPen('#0fbcf9', width=2), name="Equity")
            fill = pg.FillBetweenItem(base_line, eq_line,
                brush=pg.mkBrush(15, 188, 249, 30))
            self.eq_chart.addItem(fill)

        # Drawdown
        self.dd_chart.clear()
        if len(eq) > 1:
            ys = [e[1] for e in eq]; xs = [e[0] for e in eq]
            peak = ys[0]; dds = []
            for y in ys:
                if y > peak: peak = y
                dds.append(peak - y)
            self.dd_chart.plot(xs, dds,
                pen=pg.mkPen('#ff5e57', width=1.5),
                fillLevel=0, brush=pg.mkBrush(255, 94, 87, 40))

        # PnL Distribution
        self.dist_chart.clear()
        trades = r['trades']
        if trades:
            pnls   = [t['pnl'] for t in trades]
            bucket = max(10, int(abs(max(pnls, key=abs)) / 8))
            mn, mx = int(min(pnls)) - bucket, int(max(pnls)) + bucket
            bins   = list(range(mn, mx + bucket, bucket))
            counts = [0] * (len(bins)-1)
            for p in pnls:
                for k in range(len(bins)-1):
                    if bins[k] <= p < bins[k+1]:
                        counts[k] += 1; break
            xc  = [(bins[k]+bins[k+1])/2 for k in range(len(counts))]
            brs = [pg.mkBrush('#05c46b' if x>=0 else '#ff5e57') for x in xc]
            self.dist_chart.addItem(
                pg.BarGraphItem(x=xc, height=counts, width=bucket*0.8, brushes=brs))

        # Regime table
        regime_data = self.engine.strategy.regime_summary()
        # ✅ إذا رجع string (Analyzer-only mode) حوّله لقائمة فارغة
        if not isinstance(regime_data, list):
            regime_data = []
        self.regime_tbl.setRowCount(len(regime_data))
        rclrs = {'trending':'#0fbcf9','volatile':'#ffb62e',
                 'normal':'#05c46b','choppy':'#8395a7'}
        for i, rd in enumerate(regime_data):
            if not isinstance(rd, dict): continue
            for col, val in enumerate([
                rd['regime'], str(rd['total']), str(rd['wins']),
                str(rd['losses']), f"{rd['wr']:.0f}%", f"${rd['pnl']:+,.0f}"
            ]):
                item = QTableWidgetItem(val)
                item.setForeground(QBrush(QColor(rclrs.get(rd['regime'],'#c8d6e5'))))
                self.regime_tbl.setItem(i, col, item)
        self.regime_tbl.resizeColumnsToContents()

        # Trades table — يدعم بيانات حقيقية وبيانات وهمية
        self.trades_tbl.setRowCount(len(trades))
        for i, t in enumerate(trades):
            pc = '#05c46b' if t['pnl'] >= 0 else '#ff5e57'
            dc = '#0fbcf9' if t['direction'] == 'CALL' else '#ff5e57'
            is_real = t.get('data_type') == 'REAL_IBKR'
            date_val   = str(t.get('entry_date', t.get('entry_bar', '')))
            strike_val = f"{t['strike']:,.0f}" if is_real and t.get('strike') else '--'
            expiry_val = t.get('expiry', '--') if is_real else '--'
            cost_drag  = t.get('cost_drag', 0)
            for col, val in enumerate([
                date_val,
                t['direction'],
                f"${t.get('entry_price', 0):,.0f}",
                strike_val,
                expiry_val,
                f"${t['entry_premium']:.2f}",
                f"${t['exit_premium']:.2f}",
                str(t.get('contracts', 1)),
                f"${t['pnl']:+.0f}",
                f"${cost_drag:.0f}" if cost_drag else '--',
                t.get('exit_reason', '--'),
                t.get('regime', '--'),
                str(t.get('score', 0)),
            ]):
                item = QTableWidgetItem(val)
                if col == 8:
                    item.setForeground(QBrush(QColor(pc)))
                elif col == 9 and cost_drag > 0:
                    item.setForeground(QBrush(QColor('#ffb62e')))
                elif col == 1:
                    item.setForeground(QBrush(QColor(dc)))
                else:
                    item.setForeground(QBrush(QColor('#c8d6e5')))
                self.trades_tbl.setItem(i, col, item)
        self.trades_tbl.resizeColumnsToContents()

        # Log
        self.log_box.setPlainText("\n".join(r['log'][-120:]))
        self.progress.setValue(100)
        self.run_btn.setEnabled(True)
        # ── ملخص في أسفل السجل ──────────────────────────
        exp  = r.get('expectancy', 0)
        cost = r.get('total_cost_drag', 0)
        self.log_box.append(
            f"\n{'═'*55}\n"
            f"✅ الباك تست انتهى — {r['total_trades']} صفقة\n"
            f"Expectancy: ${exp:+.2f}/صفقة  |  "
            f"PF: {r.get('profit_factor', 0):.2f}  |  "
            f"WR: {r.get('win_rate', 0):.1f}%\n"
            f"تكاليف التداول الفعلية: ${cost:,.0f}  |  "
            f"{'⚠️ مرتفعة!' if cost > abs(r.get('total_pnl', 0)) * 0.4 else '✅ معقولة'}\n"
            f"{'═'*55}"
        )

        # ── تغذية Learning System بنتائج الباك تست ──────────────
        trades = r.get('trades', [])
        sym    = self.sym_combo.currentText()
        if trades and _LEARNING_AVAILABLE:
            threading.Thread(
                target=self._feed_learning,
                args=(trades, sym),
                daemon=True
            ).start()

    def _feed_learning(self, trades, symbol):
        """يغذّي Learning System بصفقات الباك تست ويحفظ في bot_memory.json"""
        try:
            learning = BotLearningSystem()
            fed = 0
            for t in trades:
                pnl       = t.get('pnl', 0)
                direction = t.get('direction', 'CALL')
                why       = t.get('why', '')
                regime    = t.get('regime', 'normal')
                score     = t.get('score', 0)
                entry_bar = t.get('entry_bar', 0)
                bar_in_day = entry_bar % 78
                est_hour   = 9 + int(bar_in_day * 5 / 60)
                learning.record_trade({
                    'symbol':         symbol,
                    'opt_type':       direction,
                    'score':          score,
                    'pnl':            pnl,
                    'exit_reason':    t.get('exit_reason', ''),
                    'entry_datetime': f'2024-01-01 {est_hour:02d}:00:00',
                    'regime':         regime,
                    'why':            why,
                })
                fed += 1
            learning.save()
            print(f'[Learning] ✅ تم تغذية {fed} صفقة من باك تست {symbol} → bot_memory.json')
            # تحديث label في الواجهة
            QMetaObject.invokeMethod(
                self.data_src_lbl, 'setText',
                Qt.QueuedConnection,
                Q_ARG(str, f'✅ {self.data_src_lbl.text()} | 🧠 Learning: +{fed} صفقة محفوظة'))
        except Exception as e:
            print(f'[Learning] خطأ: {e}')

    # ══════════════════════════════════════════════════════════════
    # 🎓 التدريب العميق — كل الرموز × سنة كاملة → 5000+ صفقة
    # ══════════════════════════════════════════════════════════════
    def _run_deep_training(self):
        """يجلب سنة كاملة من IBKR لكل الـ 32 رمز ويولّد 5000+ صفقة حقيقية"""
        if not self._parent_app or not getattr(self._parent_app, 'connected', False):
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, '⚠', 'يجب الاتصال بـ IBKR أولاً')
            return
        if getattr(self, '_deep_training', False):
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, '⏳', 'التدريب جارٍ بالفعل...')
            return

        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, '🎓 تدريب عميق',
            'سيجلب سنة كاملة من IBKR لكل الـ 32 رمز\n'
            'ويشغّل الاستراتيجية الكاملة على كل شمعة\n'
            'الهدف: 5000+ صفقة حقيقية\n\n'
            'الوقت المتوقع: 20-40 دقيقة\n'
            'النتائج تُحفظ في bot_memory.json تلقائياً\n\n'
            'هل تريد البدء؟',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._deep_training = True
        self.deep_train_btn.setEnabled(False)
        self.deep_train_btn.setText('⏳ جاري التدريب العميق...')
        self.deep_progress.setValue(0)
        self.deep_progress.setVisible(True)
        self.deep_status_lbl.setVisible(True)
        self.deep_status_lbl.setText('🔄 بدء التدريب العميق...')

        # ── أوقف كل الـ timers لتفادي ازدحام طلبات IBKR ──────
        _parent = self._parent_app
        if _parent:
            _parent._any_training_running = True
            for _tmr in ['options_refresh_timer', 'trades_refresh_timer',
                         'refresh_timer', 'bookmap_timer', 'chart_refresh_timer']:
                t = getattr(_parent, _tmr, None)
                if t and t.isActive():
                    t.stop()

        SYMBOLS = [
            'SPY','QQQ','IWM','AAPL','MSFT','NVDA','GOOGL','AMZN',
            'TSLA','META','AMD','AVGO','QCOM','NFLX','CRM','ADBE',
            'JPM','BAC','GS','V','MA','XOM','CVX','UNH','JNJ',
            'WMT','COST','HD','XLK','XLF','XLE'
        ]

        ib  = self._parent_app.ib
        bal = self.bal_spin.value()

        def _deep_worker():
            try:
                learning      = BotLearningSystem()
                total_trades  = 0
                total_wins    = 0
                total_pnl     = 0.0
                all_results   = []
                errors        = []
                total_syms    = len(SYMBOLS)

                for si, sym in enumerate(SYMBOLS):
                    if not getattr(self, '_deep_training', False):
                        break

                    sym_pct = int(si / total_syms * 100)
                    QMetaObject.invokeMethod(self.deep_progress, 'setValue',
                        Qt.QueuedConnection, Q_ARG(int, sym_pct))
                    QMetaObject.invokeMethod(self.deep_status_lbl, 'setText',
                        Qt.QueuedConnection,
                        Q_ARG(str, f'[{si+1}/{total_syms}] 📥 {sym} — جلب سنة كاملة...'))

                    try:
                        # جلب سنة كاملة بشمعات 5m من IBKR
                        contract = _make_contract(sym)
                        try:
                            run_in_ib_thread(ib.qualifyContracts, contract)
                        except Exception:
                            pass

                        # جلب البيانات مع timeout طويل — يجرب سنة ثم 6 أشهر ثم 3 أشهر
                        bars = None
                        for _dur, _tout in [('1 Y', 120), ('6 M', 90), ('3 M', 60)]:
                            try:
                                bars = run_in_ib_thread_long(
                                    ib.reqHistoricalData,
                                    contract,
                                    endDateTime='', durationStr=_dur,
                                    barSizeSetting='5 mins', whatToShow='TRADES',
                                    useRTH=True, formatDate=1, keepUpToDate=False,
                                    timeout=_tout,
                                )
                                if bars and len(bars) >= 200:
                                    break
                                bars = None
                            except Exception:
                                bars = None
                                import time as _t; _t.sleep(2)
                                continue

                        if not bars or len(bars) < 200:
                            errors.append(f'{sym}: فشل جلب البيانات ({len(bars) if bars else 0} شمعة)')
                            continue

                        QMetaObject.invokeMethod(self.deep_status_lbl, 'setText',
                            Qt.QueuedConnection,
                            Q_ARG(str,
                                f'[{si+1}/{total_syms}] 🔬 {sym} — '
                                f'{len(bars):,} شمعة — يحلل بالاستراتيجية الكاملة...'))

                        # انتظار قصير بين الرموز لتجنب rate limit في IBKR
                        import time as _t2; _t2.sleep(3)

                        # شغّل الباك تست — نفس الاستراتيجية كالبوت الحي
                        _idx2    = getattr(_strategy_module, 'INDEX_SYMBOLS', INDEX_SYMBOLS)
                        _strat2  = IndexStrategy() if sym in _idx2 else StockStrategy()
                        engine   = BacktestEngine(_strat2)
                        opens_  = [b.open   for b in bars]
                        closes_ = [b.close  for b in bars]
                        highs_  = [b.high   for b in bars]
                        lows_   = [b.low    for b in bars]
                        volumes_= [int(b.volume) for b in bars]

                        try:
                            result = engine.run(
                                opens_, closes_, highs_, lows_, volumes_,
                                initial_balance=bal,
                                risk_pct=0.01,
                                days_to_exp=7,
                                progress_cb=None,
                            )
                        except Exception as _re:
                            errors.append(f'{sym}: خطأ في التحليل — {str(_re)[:50]}')
                            continue

                        if not result or not isinstance(result, dict):
                            errors.append(f'{sym}: نتيجة فارغة')
                            continue

                        result['symbol'] = sym

                        sym_trades = result.get('trades', [])
                        if not sym_trades:
                            errors.append(f'{sym}: لا إشارات')
                            continue

                        sym_wins = sum(1 for t in sym_trades if t['pnl'] > 0)
                        sym_pnl  = sum(t['pnl'] for t in sym_trades)
                        sym_wr   = sym_wins / len(sym_trades) * 100

                        # غذّي Learning بكل صفقة
                        for t in sym_trades:
                            pnl_t     = t.get('pnl', 0)
                            direction = t.get('direction', 'CALL')
                            why       = t.get('why', '')
                            regime    = t.get('regime', 'normal')
                            score     = t.get('score', 0)
                            entry_bar = t.get('entry_bar', 0)
                            bar_in_day = entry_bar % 78
                            est_hour   = 9 + int(bar_in_day * 5 / 60)
                            learning.record_trade({
                                'symbol':         sym,
                                'opt_type':       direction,
                                'score':          score,
                                'pnl':            pnl_t,
                                'exit_reason':    t.get('exit_reason', ''),
                                'entry_datetime': f'2024-01-01 {est_hour:02d}:00:00',
                                'regime':         regime,
                                'why':            why,
                            })

                        total_trades += len(sym_trades)
                        total_wins   += sym_wins
                        total_pnl    += sym_pnl
                        all_results.append({
                            'symbol': sym,
                            'trades': len(sym_trades),
                            'wr':     sym_wr,
                            'pnl':    sym_pnl,
                        })

                        # حفظ تدريجي كل 5 رموز
                        if (si + 1) % 5 == 0:
                            learning.save()
                            print(f'[DeepTrain] حفظ تدريجي — {total_trades} صفقة حتى الآن')

                        QMetaObject.invokeMethod(self.deep_status_lbl, 'setText',
                            Qt.QueuedConnection,
                            Q_ARG(str,
                                f'[{si+1}/{total_syms}] ✅ {sym} — '
                                f'{len(sym_trades)} صفقة | {sym_wr:.0f}% نجاح | ${sym_pnl:+,.0f} '
                                f'| إجمالي: {total_trades} صفقة'))

                    except Exception as e:
                        errors.append(f'{sym}: {str(e)[:80]}')
                        import traceback; traceback.print_exc()
                        continue

                # حفظ نهائي
                learning.save()
                print(f'[DeepTrain] ✅ انتهى — {total_trades} صفقة محفوظة في bot_memory.json')

                # بناء ملخص النتائج
                wr_total = (total_wins / total_trades * 100) if total_trades else 0
                lines = [
                    '═══ نتائج التدريب العميق ═══',
                    f'📊 {total_trades:,} صفقة حقيقية',
                    f'🎯 {wr_total:.1f}% نسبة نجاح',
                    f'💰 ${total_pnl:+,.0f} PnL الكلي',
                    f'✅ تم حفظ الدروس في bot_memory.json',
                    '',
                    '── أداء كل رمز ──',
                ]
                for r in sorted(all_results, key=lambda x: x['pnl'], reverse=True):
                    icon = '⭐' if r['wr'] >= 55 else ('⚠' if r['wr'] < 45 else '✅')
                    lines.append(
                        f"{icon} {r['symbol']:6s} {r['trades']:4d} صفقة | "
                        f"{r['wr']:.0f}% | ${r['pnl']:+,.0f}")
                if errors:
                    lines.append(f'\n⚠ أخطاء ({len(errors)}):')
                    for e in errors[:8]:
                        lines.append(f'  • {e}')

                summary = '\n'.join(lines)
                QMetaObject.invokeMethod(self, '_on_deep_training_done',
                    Qt.QueuedConnection, Q_ARG(str, summary))

            except Exception as e:
                import traceback; traceback.print_exc()
                QMetaObject.invokeMethod(self, '_on_deep_training_done',
                    Qt.QueuedConnection, Q_ARG(str, f'❌ خطأ: {e}'))

        threading.Thread(target=_deep_worker, daemon=True).start()

    @pyqtSlot(str)
    def _on_deep_training_done(self, summary):
        """يُستدعى عند انتهاء التدريب العميق"""
        self._deep_training = False
        self.deep_train_btn.setEnabled(True)
        self.deep_train_btn.setText('🎓 تدريب عميق — كل الرموز (IBKR)')
        self.deep_progress.setValue(100)
        self.deep_status_lbl.setText('✅ اكتمل التدريب العميق — bot_memory.json محدَّث')

        # ── أعد تشغيل الـ timers ─────────────────────────────
        _parent = self._parent_app
        if _parent:
            _parent._any_training_running = False
            for _tmr, _interval in [
                ('options_refresh_timer', 15000),
                ('trades_refresh_timer',  5000),
                ('refresh_timer',         30000),
                ('bookmap_timer',         15000),
                ('chart_refresh_timer',   30000),
            ]:
                t = getattr(_parent, _tmr, None)
                if t and not t.isActive():
                    t.start(_interval)

        dlg = QDialog(self)
        dlg.setWindowTitle('🎓 نتائج التدريب العميق')
        _scr_dlg = QApplication.primaryScreen().availableGeometry()
        dlg.resize(min(500, _scr_dlg.width()  - 80),
                   min(580, _scr_dlg.height() - 80))
        dlg.setStyleSheet('background:#0a1628;color:#c8d6e5;')
        lay = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setStyleSheet(
            'background:#0d2137;color:#05c46b;border:none;'
            'font-size:12px;font-family:Consolas;')
        txt.setPlainText(summary)
        lay.addWidget(txt)
        b = QPushButton('إغلاق')
        b.setStyleSheet(
            'background:#a29bfe;color:#0a1628;padding:8px;'
            'border-radius:4px;font-weight:bold;font-size:13px;')
        b.clicked.connect(dlg.close)
        lay.addWidget(b)
        dlg.exec_()


# ===================================================
# البوت الرئيسي - تداول تلقائي كامل
# ===================================================

# ══════════════════════════════════════════════════════════════════
# SmartEntryEngine — Strike الذكي + Confidence Score + Expected Value
# ══════════════════════════════════════════════════════════════════

class MonitorThread(QThread):
    signal_update  = pyqtSignal(dict)
    signal_close   = pyqtSignal(dict)
    signal_log     = pyqtSignal(str)

    INTERVAL_SEC   = 5   # كل 5 ثوان — استجابة سريعة لـ SL/TP

    def __init__(self, ib, position_manager, risk_manager, strategy, auto_execute=True):
        super().__init__()
        self.ib               = ib
        self.position_manager = position_manager
        self.risk_manager     = risk_manager
        self.strategy         = strategy
        self.auto_execute     = auto_execute
        self.running          = False

    def run(self):
        self.running = True
        while self.running:
            try:
                positions = self.position_manager.get_all()
                if positions:
                    self.signal_log.emit(f"📡 مراقبة {len(positions)} صفقة مفتوحة...")
                    for pos in positions:
                        if not self.running: break
                        self._check_one(pos)
            except Exception as e:
                print(f"[Monitor] Error: {e}")

            # انتظر 30 ثانية قابلة للإيقاف
            for _ in range(self.INTERVAL_SEC * 2):
                if not self.running: break
                self.msleep(500)

    def _get_current_price(self, opt_c, entry_premium=None, pos_id=None):
        """جلب السعر الحالي — Live ثم Historical ثم MIDPOINT"""
        current = None

        # ✅ تأهيل العقد أولاً — يضمن conId صحيح لطلب البيانات
        try:
            _q = run_in_ib_thread(self.ib.qualifyContracts, opt_c)
            if _q: opt_c = _q[0]
        except Exception: pass

        # ① Live bid/ask — generic_ticks فارغ لتجنب Error 354
        try:
            ticker = req_mkt_data_safe(self.ib, opt_c, '')[0]
            _last_fallback = None
            for _ in range(20):
                time.sleep(0.1); ib_pump(0.05)
                _b = getattr(ticker,'bid',0) or 0
                _a = getattr(ticker,'ask',0) or 0
                _l = getattr(ticker,'last',0) or 0
                if _b > 0 and _a > 0 and _a <= _b * 4.0:
                    current = round((_b + _a) / 2, 2); break
                if _l > 0:
                    _last_fallback = _l
            if not current and _last_fallback:
                current = float(_last_fallback)
            try: run_in_ib_thread(self.ib.cancelMktData, opt_c)
            except Exception: pass
        except Exception: pass

        # ② Historical MIDPOINT (يعمل بعد ساعات السوق)
        if not current or current <= 0:
            for _show in ['MIDPOINT', 'TRADES']:
                try:
                    _hist = run_in_ib_thread(
                        self.ib.reqHistoricalData, opt_c,
                        endDateTime='', durationStr='1 D',
                        barSizeSetting='5 mins', whatToShow=_show,
                        useRTH=False, formatDate=1, keepUpToDate=False)
                    if _hist:
                        _b = _hist[-1]
                        current = round((_b.open + _b.close) / 2, 2)
                        if current > 0: break
                except Exception: pass

        return current

    def _check_one(self, pos):
        try:
            opt_c = pos.get('opt_contract')
            if not opt_c: return

            _entry_chk = pos.get('entry_premium', 0)
            _pid_chk   = pos.get('id', '?')
            current = self._get_current_price(opt_c, _entry_chk, _pid_chk)

            # ✅ فلتر أمان — يرفض الارتفاعات الوهمية فقط
            if current and _entry_chk and _entry_chk > 0:
                _chg = (current - _entry_chk) / _entry_chk * 100
                # ✅ إصلاح: رفض انخفاض >99% — بيانات متأخرة وهمية
                if _chg < -99:
                    print(f"[SANITY-MON] {_pid_chk}: رفض انخفاض وهمي ${current:.4f} ({_chg:.0f}%)")
                    current = None
                elif _chg > 0:
                    if _chg > 150:
                        print(f"[SANITY-MON] {_pid_chk}: رفض ارتفاع وهمي ${current:.2f} (+{_chg:.0f}%)")
                        current = None
                    elif _chg > 60:
                        from datetime import datetime as _dt4
                        _edt = pos.get('entry_datetime', '')
                        _ela = 9999
                        if _edt:
                            try:
                                _ela = (_dt4.now() - _dt4.strptime(_edt, '%Y-%m-%d %H:%M:%S')).total_seconds()/60
                            except Exception: pass
                        if _ela < 15:
                            print(f"[SANITY-MON] {_pid_chk}: تجاهل ارتفاع مشبوه ${current:.2f} (+{_chg:.0f}%)")
                            current = None

            if not current or current <= 0:
                # لا نستخدم entry_premium كبديل — هذا يمنع SL من العمل
                # بدلاً من ذلك: نتحقق من SL المحفوظ مع الصفقة
                _saved_sl = pos.get('stop_loss', 0)
                _entry_p  = pos.get('entry_premium', 0)
                if _saved_sl > 0 and _entry_p > 0:
                    print(f"[Monitor] {pos.get('symbol','?')}: لا سعر حالي — انتظار")
                return  # لا سعر = لا فحص

            entry   = pos.get('entry_premium', 0)

            # ✅ sync entry من IBKR في أول دورة (MonitorThread)
            if not pos.get('_entry_synced') and entry > 0:
                try:
                    _pf2 = run_in_ib_thread(self.ib.portfolio)
                    for _pi in (_pf2 or []):
                        _pc = getattr(_pi, 'contract', None)
                        if not _pc: continue
                        if (getattr(_pc,'symbol','') == pos.get('symbol','') and
                            getattr(_pc,'right','') == ('C' if pos.get('opt_type','')=='CALL' else 'P') and
                            abs(float(getattr(_pc,'strike',0) or 0) - float(pos.get('strike',0) or 0)) < 0.5):
                            _avg2 = float(getattr(_pi,'averageCost',0) or 0)
                            # ✅ ذكي: averageCost > 10 = premium×100
                            if _avg2 > 10.0:
                                _ib_e = round(_avg2 / 100.0, 4)
                            else:
                                _ib_e = round(_avg2, 4)
                            if not (0.01 <= _ib_e <= 50.0):
                                _ib_e = 0
                            if _ib_e > 0:
                                pos['entry_premium'] = _ib_e
                                entry = _ib_e
                                _st2 = pos.get('strategy_type','Stock')
                                if pos.get('stop_loss',0) <= 0:
                                    pos['stop_loss'] = round(entry*(0.75 if 'Index' in _st2 else 0.65),2)
                            pos['_entry_synced'] = True
                            break
                except Exception: pass
                if not pos.get('_entry_synced'):
                    pos['_entry_synced'] = True
            pnl_usd = round((current - entry) * pos.get('contracts', 1) * 100, 2)
            pnl_pct = round((current - entry) / entry * 100, 2) if entry > 0 else 0.0
            _strat_fb = pos.get('strategy_type','Stock')
            _st_mon2 = pos.get('strategy_type','Stock')
            tp1_p   = pos.get('take_profit', round(entry * (1.20 if _st_mon2=='Index' else 1.25), 2))
            tp2_p   = pos.get('take_profit_2', round(tp1_p * 1.20, 2))
            sym     = pos.get('symbol', '')

            # ── فحص الخروج أولاً (يُحدِّث pos['stop_loss'] و pos['tp_phase']) ──
            exit_reason = self.position_manager.check_exits(pos['id'], current)

            # ── اقرأ SL و phase بعد check_exits (يشمل الـ trailing المُحدَّث) ──
            sl      = pos.get('stop_loss', round(entry * (0.75 if _strat_fb=='Index' else 0.65), 2))
            phase   = pos.get('tp_phase', 0)
            phase_lbl = ['TP1↑', 'TP2↑', 'Trail🔄'][min(phase, 2)]

            # ✅ تشخيص دائم: اطبع السعر الحالي مقابل SL
            self.signal_log.emit(
                f"📊 {sym} curr=${current:.2f} entry=${entry:.2f} "
                f"SL=${sl:.2f} {'🔴 SL!' if current<=sl else '✅'} "
                f"phase={phase}"
            )

            # ── emit تحديث للجدول بالقيم المُحدَّثة ──
            # ✅ تأكد أن current_price محدّث في position_manager أيضاً
            _pm_ref = self.position_manager.get(pos.get('id', ''))
            if _pm_ref is not None:
                _pm_ref['current_price'] = current
                _pm_ref['stop_loss']    = sl
                _pm_ref['tp_phase']     = phase
            self.signal_update.emit({
                'id':          pos.get('id'),
                'current':     current,
                'pnl_pct':     pnl_pct,
                'pnl_usd':     pnl_usd,
                'stop_loss':   sl,
                'tp1':         tp1_p,
                'tp2':         tp2_p,
                'phase_lbl':   phase_lbl,
                'contracts':   pos.get('contracts', 0),
                'pnl_abs':     abs(pnl_usd),
                'pnl_sign':    '+' if pnl_usd >= 0 else '-',
            })

            self.signal_log.emit(
                f"📊 {sym} ${current:.2f} "
                f"{'🟢' if pnl_usd>=0 else '🔴'}{pnl_pct:+.1f}% (${pnl_usd:+.0f}) "
                f"SL=${sl:.2f} {phase_lbl}"
            )

            if exit_reason:
                print(f"[EXIT] {sym} {exit_reason} @ ${current:.2f} PnL=${pnl_usd:+.0f}")
                self.signal_log.emit(
                    f"{'🎯' if exit_reason.startswith('tp') else '🛑'} "
                    f"{sym} {exit_reason.upper()} @ ${current:.2f} PnL=${pnl_usd:+.0f}"
                )
                self._do_close(pos, current, exit_reason, pnl_usd)

        except Exception as e:
            print(f"[Monitor] _check_one error: {e}")

    def _do_close(self, pos, exit_price, reason, pnl_usd):
        # ✅ منع التنفيذ المزدوج مع AutoTradingBot
        _pos_id = pos.get('id')
        if _pos_id and not self.position_manager.mark_closing(_pos_id):
            print(f"[Monitor] ⏭ {pos.get('symbol')} — يُغلق بالفعل من AutoTradingBot، تجاهل")
            return
        try:
            total_contracts = pos.get('contracts', 1)
            entry           = pos.get('entry_premium', 0)
            opt_c           = pos.get('opt_contract')

            if reason == 'tp1':
                sell_qty  = max(1, total_contracts // 2)
                pos['contracts'] -= sell_qty
                # SL رُفع بالفعل في check_exits → نضمن أنه لا يقل عن breakeven
                if entry > 0:
                    _cur_sl = pos.get('stop_loss', 0)
                    pos['stop_loss'] = max(_cur_sl, round(entry * 1.00, 2))
                partial   = pos['contracts'] > 0
            elif reason == 'tp2':
                sell_qty  = max(1, total_contracts // 2)
                pos['contracts'] -= sell_qty
                # SL رُفع بالفعل في check_exits → نضمن أنه لا يقل عن TP1
                if entry > 0:
                    _cur_sl  = pos.get('stop_loss', 0)
                    _tp1_val = pos.get('take_profit', round(entry * 1.25, 2))
                    pos['stop_loss'] = max(_cur_sl, round(_tp1_val, 2))
                partial   = pos['contracts'] > 0
            else:
                sell_qty  = total_contracts
                partial   = False

            # تنفيذ أمر البيع — انتظر Fill قبل المتابعة
            ibkr_filled = not self.auto_execute  # simulate mode = نعتبره filled
            if self.auto_execute and opt_c and sell_qty > 0:
                try:
                    _q = run_in_ib_thread(self.ib.qualifyContracts, opt_c)
                    if _q: opt_c = _q[0]
                except Exception: pass
                order = _make_order('SELL', sell_qty)
                try:
                    _tr = run_in_ib_thread(self.ib.placeOrder, opt_c, order)
                    # انتظر Fill حتى 30 ثانية
                    for _w in range(60):
                        time.sleep(0.5); ib_pump(0.05)
                        try:
                            _cs = run_in_ib_thread(lambda t=_tr: t.orderStatus.status)
                            _cp = run_in_ib_thread(lambda t=_tr: t.orderStatus.avgFillPrice)
                            if _cs == 'Filled':
                                if _cp and _cp > 0:
                                    exit_price = float(_cp)
                                ibkr_filled = True
                                self.signal_log.emit(
                                    f"✅ {pos.get('symbol')} {reason.upper()} FILLED @ ${exit_price:.2f}")
                                break
                            elif _cs in ('Cancelled', 'Inactive', 'ApiCancelled'):
                                self.signal_log.emit(f"⚠ {pos.get('symbol')} أمر {_cs}")
                                break
                        except Exception:
                            pass
                    if not ibkr_filled:
                        self.signal_log.emit(
                            f"❌ {pos.get('symbol')} {reason.upper()} timeout — الصفقة لا تزال مفتوحة")
                except Exception as _oe:
                    print(f"[Monitor] Sell error: {_oe}")

            if not ibkr_filled:
                if _pos_id:
                    self.position_manager.unmark_closing(_pos_id)
                return

            # ✅ إصلاح: PnL صحيح للـ Spread (معكوس) وللـ Single Option
            _is_spread_mon = pos.get('is_spread', False) and pos.get('long_contract') is not None
            if _is_spread_mon:
                # Spread: ربحنا عند انخفاض القيمة (short premium)
                pnl = round((entry - exit_price) * sell_qty * 100, 2)
            else:
                pnl = round((exit_price - entry) * sell_qty * 100, 2)

            self.signal_close.emit({
                **pos,
                'exit_price':  exit_price,
                'exit_reason': reason,
                'pnl':         pnl,
                'contracts':   sell_qty,
                'exit_time':   datetime.now().strftime("%H:%M:%S"),
            })

            if not partial or pos.get('contracts', 0) <= 0:
                self.risk_manager.close(pnl, symbol=pos.get('symbol'))
                if hasattr(self.strategy, 'record_trade_result'):
                    self.strategy.record_trade_result(pnl)
                self.position_manager.remove(pos['id'])
                # ✅ مزامنة ExecutionEngine.ledger بعد إغلاق MonitorThread
                try:
                    _eng = getattr(self, '_exec_engine', None)
                    if _eng is not None:
                        import threading as _thr2
                        with _eng._lock:
                            _eng.ledger.open_trades = len(_eng.open_positions)
                except Exception:
                    pass
            else:
                self.risk_manager.add_pnl(pnl)
                _pos_ref = self.position_manager.get(pos['id'])
                if _pos_ref:
                    _pos_ref['contracts'] = pos.get('contracts', 0)
                if _pos_id:
                    self.position_manager.unmark_closing(_pos_id)

        except Exception as e:
            print(f"[Monitor] _do_close error: {e}")
            if _pos_id:
                self.position_manager.unmark_closing(_pos_id)

    def stop(self):
        self.running = False


# ===================================================
# UI Signals
# ===================================================

class UIUpdater(QObject):
    update_price    = pyqtSignal(float)
    update_chart    = pyqtSignal(dict)
    update_rsi      = pyqtSignal(float)
    update_options  = pyqtSignal(list)
    update_cash     = pyqtSignal(float)
    update_expiries = pyqtSignal(list)
    update_analysis = pyqtSignal(dict)
    show_status     = pyqtSignal(str)
    update_trade    = pyqtSignal(dict)
    update_live_bar = pyqtSignal(dict)   # OHLC لحظي للشارت
    update_indicators = pyqtSignal(dict) # RSI,ADX,EMA,BB لحظي
    update_clock    = pyqtSignal(str)    # وقت السوق


# ===================================================
# التطبيق الرئيسي
# ===================================================

# ══════════════════════════════════════════════════════════════════════
# 📊 ProChartWidget — شارت احترافي مدمج مع تحكم كامل

def _calc_ema_series(prices, period):
    """يحسب سلسلة EMA كاملة"""
    if len(prices) < period:
        return []
    k = 2/(period+1)
    ema = sum(prices[:period])/period
    result = [None]*(period-1) + [ema]
    for p in prices[period:]:
        ema = p*k + ema*(1-k)
        result.append(ema)
    return [v for v in result if v is not None]


def _calc_rsi_series(prices, period=14):
    """يحسب سلسلة RSI كاملة (Wilder smoothing)"""
    if len(prices) < period + 2:
        return []
    deltas  = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains   = [max(d, 0.0) for d in deltas]
    losses  = [abs(min(d, 0.0)) for d in deltas]
    avg_g   = sum(gains[:period]) / period
    avg_l   = sum(losses[:period]) / period
    result  = [None] * (period + 1)
    rs      = avg_g / avg_l if avg_l else 100.0
    result.append(round(100 - 100 / (1 + rs), 2))
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i])  / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs    = avg_g / avg_l if avg_l else 100.0
        result.append(round(100 - 100 / (1 + rs), 2))
    return [v for v in result if v is not None]


def _calc_macd_series(prices, fast=12, slow=26, signal=9):
    """يحسب سلاسل MACD: (hist_list, macd_line_list, signal_line_list)"""
    if len(prices) < slow + signal + 1:
        return [], [], []
    kf = 2 / (fast + 1); ks = 2 / (slow + 1); kg = 2 / (signal + 1)
    ema_f = sum(prices[:fast]) / fast
    ema_s = sum(prices[:slow]) / slow
    for p in prices[fast:slow]:
        ema_f = p * kf + ema_f * (1 - kf)
    macd_vals = []
    for p in prices[slow:]:
        ema_f = p * kf + ema_f * (1 - kf)
        ema_s = p * ks + ema_s * (1 - ks)
        macd_vals.append(ema_f - ema_s)
    if len(macd_vals) < signal:
        return [], [], []
    sig_val = sum(macd_vals[:signal]) / signal
    sig_line = [sig_val]
    for m in macd_vals[signal:]:
        sig_val = m * kg + sig_val * (1 - kg)
        sig_line.append(sig_val)
    macd_line = macd_vals[signal - 1:]
    hist_line = [round(m - s, 6) for m, s in zip(macd_line, sig_line)]
    macd_line = [round(v, 6) for v in macd_line]
    sig_line  = [round(v, 6) for v in sig_line]
    return hist_line, macd_line, sig_line


# 📊 ProChartWidget — شارت احترافي
# يُحمَّل من pro_chart_js.py (نفس المجلد)

# ══ ProChartWidget ══
# pro_chart_js.py — شارت احترافي بمستوى TradingView الكامل
# ══════════════════════════════════════════════════════════════════════════════
# v3 — الميزات الجديدة:
#   ✅ سعر أقصى اليمين على محور السعر (TV-style price tag)
#   ✅ موقت الشمعة على محور السعر (⏱ MM:SS)
#   ✅ تحكم كامل بعرض الشمعة (🕯+ 🕯-)
#   ✅ ماكس zoom محدود (5 → 500 شمعة) + wheel zoom
#   ✅ رسم الشموع بدقة TV (body + wick صحيح)
#   ✅ محور سعر أيمن مع padding للـ tags
#   ✅ كل ميزات v2 السابقة
# ══════════════════════════════════════════════════════════════════════════════

import numpy as np
import pyqtgraph as pg
from datetime import datetime as _dt, timedelta as _td

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QSizePolicy,
    QPushButton, QLabel, QFrame, QSplitter
)
from PyQt5.QtCore  import Qt, pyqtSignal, pyqtSlot, QTimer, QRectF, QThread
from PyQt5.QtGui   import QFont, QColor, QPainter, QPen, QBrush, QFontMetrics

# ── ألوان TradingView ─────────────────────────────────────────────────────────
C = {
    'bg':'#131722','bg2':'#1e222d','bg3':'#2a2e39','bg4':'#363a45',
    'txt':'#d1d4dc','txt2':'#787b86','axis':'#2a2e39',
    'bull':'#26a69a','bear':'#ef5350',
    'vbull':'#1a3d38','vbear':'#3d1a1a',
    'cross':'#758696','price_ln':'#2962ff',
    'e9':'#f7c948','e21':'#e040fb','e200':'#3179f5','vwap':'#ff9800',
    'bb_mid':'#2196f3','bb_band':'#162236',
    'r':'#ef5350','s':'#26a69a','pp':'#f9ca24',
    'entry':'#2962ff','sl':'#ef5350','tp':'#26a69a',
    'fib':['#ef5350','#ff9800','#f9ca24','#26a69a','#2196f3','#9c27b0'],
    'pre':'#1a2540','reg':'#0d1a12','ah':'#1f1a0d',
    'timer_bg':'#1e3a1e',
}
TF_SECONDS       = {'1m':60,'5m':300,'15m':900,'1H':3600,'4H':14400,'1D':86400}
FIB_LEVELS       = [0.0,0.236,0.382,0.5,0.618,0.786,1.0]
CANDLE_W_DEFAULT = 0.75
CANDLE_W_MIN     = 0.10
CANDLE_W_MAX     = 0.95
CANDLE_W_STEP    = 0.05
ZOOM_BARS_MIN    = 5
ZOOM_BARS_MAX    = 500
ZOOM_STEP        = 0.25
RIGHT_MARGIN     = 8


def _mk(color, width=1, style=Qt.SolidLine, alpha=255):
    c = QColor(color); c.setAlpha(alpha)
    return pg.mkPen(c, width=width, style=style)

def _br(color, alpha=255):
    c = QColor(color); c.setAlpha(alpha)
    return pg.mkBrush(c)

def _fmt_p(v):
    if v is None: return '---'
    if v > 100:   return f'{v:.2f}'
    if v > 1:     return f'{v:.3f}'
    return f'{v:.4f}'

def _fmt_v(v):
    if not v: return '0'
    if v>=1e9: return f'{v/1e9:.1f}B'
    if v>=1e6: return f'{v/1e6:.1f}M'
    if v>=1e3: return f'{v/1e3:.0f}K'
    return f'{int(v)}'

def _wicks_arr(mask, xs, H, L):
    idx = np.where(mask)[0]
    if not len(idx): return np.array([]),np.array([])
    xa=np.empty(len(idx)*3); xa[2::3]=np.nan
    ya=np.empty(len(idx)*3); ya[2::3]=np.nan
    xa[0::3]=xa[1::3]=xs[idx]
    ya[0::3]=L[idx]; ya[1::3]=H[idx]
    return xa,ya


# ══════════════════════════════════════════════════════════════════════════════
# Axes
# ══════════════════════════════════════════════════════════════════════════════
class _PriceAxis(pg.AxisItem):
    def tickStrings(self,values,scale,spacing):
        return [_fmt_p(v) for v in values]

class _TimeAxis(pg.AxisItem):
    def __init__(self,**kw):
        super().__init__(**kw); self._lbl={}
    def tickStrings(self,values,scale,spacing):
        return [self._lbl.get(int(v),'') for v in values]
    def set_labels(self,d):
        self._lbl=d; self.update()


# ══════════════════════════════════════════════════════════════════════════════
# Price Tag — TV-style label على محور السعر
# ══════════════════════════════════════════════════════════════════════════════
class _PriceTag(pg.GraphicsObject):
    def __init__(self, color='#2962ff', font_size=9):
        super().__init__()
        self._color = color; self._fs = font_size
        self._main = ''; self._sub = ''
        self.setZValue(100)

    def set_text(self, main, sub=''):
        self._main = main; self._sub = sub; self.update()

    def paint(self, p, *args):
        if not self._main: return
        f  = QFont('Consolas', self._fs, QFont.Bold); p.setFont(f)
        fm = QFontMetrics(f)
        mw = fm.horizontalAdvance(self._main) + 10
        sw = fm.horizontalAdvance(self._sub)  + 8 if self._sub else 0
        h  = fm.height() + 4; y0 = -h//2

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(self._color)))
        p.drawRect(0, y0, mw, h)
        p.setPen(QPen(QColor('#ffffff')))
        p.drawText(5, y0 + fm.ascent() + 1, self._main)

        if self._sub:
            p.setBrush(QBrush(QColor(C['bg4'])))
            p.setPen(Qt.NoPen)
            p.drawRect(mw, y0, sw, h)
            p.setPen(QPen(QColor(C['txt2'])))
            p.setFont(QFont('Consolas', self._fs-1))
            p.drawText(mw+4, y0 + fm.ascent() + 1, self._sub)

    def boundingRect(self):
        return QRectF(0,-10,120,20)


# ══════════════════════════════════════════════════════════════════════════════
# شريط الأدوات
# ══════════════════════════════════════════════════════════════════════════════
class _ToolBar(QFrame):
    sig_tf       = pyqtSignal(str)
    sig_ind      = pyqtSignal(str,bool)
    sig_panel    = pyqtSignal(str,bool)
    sig_tool     = pyqtSignal(str)
    sig_clear    = pyqtSignal()
    sig_candle_w = pyqtSignal(int)
    sig_zoom     = pyqtSignal(int)
    sig_yzoom    = pyqtSignal(int)   # ✅ جديد: +1 تكبير Y / -1 تصغير / 0 reset
    sig_pan      = pyqtSignal(int)   # ✅ جديد: -1 يسار / +1 يمين

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setMinimumWidth(0)
        self.setStyleSheet(
            f'QFrame{{background:{C["bg2"]};border-bottom:1px solid {C["bg3"]};}} '
            f'QPushButton{{background:transparent;color:{C["txt2"]};border:none;'
            f'padding:1px 6px;border-radius:3px;font:10px Consolas;min-width:18px;}} '
            f'QPushButton:hover{{background:{C["bg3"]};color:{C["txt"]};}} '
            f'QPushButton:checked{{background:{C["price_ln"]};color:#fff;}} ')
        from PyQt5.QtWidgets import QHBoxLayout
        row=QHBoxLayout(self); row.setContentsMargins(4,0,4,0); row.setSpacing(2)

        self.lbl_sym=QLabel('---'); self.lbl_price=QLabel('---'); self.lbl_chg=QLabel('')
        self.lbl_sym.setStyleSheet(f'color:{C["txt"]};font:bold 12px Consolas;')
        self.lbl_price.setStyleSheet(f'color:{C["txt"]};font:bold 11px Consolas;')
        self.lbl_chg.setStyleSheet('font:bold 9px Consolas;padding:0 3px;border-radius:2px;')
        for w in [self.lbl_sym,self.lbl_price,self.lbl_chg]: row.addWidget(w)
        row.addWidget(self._sep())

        self._tf_btns={}
        for tf in ['1m','5m','15m','1H','4H','1D']:
            b=QPushButton(tf if tf!='1D' else 'D'); b.setCheckable(True); b.setChecked(tf=='15m')
            b.clicked.connect(lambda _,t=tf: self.sig_tf.emit(t))
            self._tf_btns[tf]=b; row.addWidget(b)
        row.addWidget(self._sep())

        self._ind_btns={}
        for k,lbl,clr in [('e9','E9',C['e9']),('e21','E21',C['e21']),('e200','E200',C['e200']),
                           ('vwap','VW',C['vwap']),('bb','BB',C['bb_mid']),('pv','PV',C['pp']),('sd','SD',C['r'])]:
            b=QPushButton(lbl); b.setCheckable(True); b.setChecked(True)
            b.setStyleSheet(f'QPushButton{{color:{clr};border:1px solid {C["bg3"]};padding:0 4px;border-radius:2px;font:9px Consolas;}}'
                            f'QPushButton:hover{{background:{C["bg3"]};}}'
                            f'QPushButton:checked{{border-color:{clr};}}'
                            f'QPushButton:!checked{{color:{C["txt2"]};}}')
            b.clicked.connect(lambda chk,key=k: self.sig_ind.emit(key,chk))
            self._ind_btns[k]=b; row.addWidget(b)
        row.addWidget(self._sep())

        for k,lbl in [('rsi','RSI'),('macd','MACD')]:
            b=QPushButton(lbl); b.setCheckable(True)
            b.clicked.connect(lambda chk,key=k: self.sig_panel.emit(key,chk))
            row.addWidget(b)
        row.addWidget(self._sep())

        self._draw_btns={}
        for k,ico,tip in [('hline','—','خط أفقي'),('line','╱','خط مائل'),
                           ('rect','▭','مستطيل'),('fib','Fib','Fibonacci')]:
            b=QPushButton(ico); b.setCheckable(True); b.setToolTip(tip)
            b.setStyleSheet(f'QPushButton{{color:{C["txt2"]};border:none;padding:1px 5px;font:11px Consolas;}}'
                            f'QPushButton:hover{{background:{C["bg3"]};color:{C["txt"]};border-radius:3px;}}'
                            f'QPushButton:checked{{background:{C["bg4"]};color:{C["txt"]};border-radius:3px;}}')
            b.clicked.connect(lambda chk,key=k: self._on_draw(key,chk))
            self._draw_btns[k]=b; row.addWidget(b)
        xb=QPushButton('✕'); xb.clicked.connect(self.sig_clear.emit); row.addWidget(xb)
        row.addWidget(self._sep())

        bs=(f'QPushButton{{color:{C["txt2"]};border:none;padding:1px 5px;font:10px Consolas;}}'
            f'QPushButton:hover{{background:{C["bg3"]};color:{C["txt"]};border-radius:3px;}}')
        bm=QPushButton('🕯−'); bp=QPushButton('🕯+')
        self.lbl_cw=QLabel(f'{int(CANDLE_W_DEFAULT*100)}%')
        self.lbl_cw.setStyleSheet(f'color:{C["txt2"]};font:9px Consolas;min-width:28px;')
        bm.setStyleSheet(bs); bp.setStyleSheet(bs)
        bm.clicked.connect(lambda:self.sig_candle_w.emit(-1))
        bp.clicked.connect(lambda:self.sig_candle_w.emit(+1))
        bm.setToolTip('تصغير الشمعة'); bp.setToolTip('تكبير الشمعة')
        for w in [bm,self.lbl_cw,bp]: row.addWidget(w)
        row.addWidget(self._sep())

        bzi=QPushButton('+'); bzo=QPushButton('−'); brs=QPushButton('⊞')
        self.lbl_zoom=QLabel('80')
        self.lbl_zoom.setStyleSheet(f'color:{C["txt2"]};font:9px Consolas;min-width:26px;')
        for b in [bzi,bzo,brs]: b.setStyleSheet(bs)
        bzi.setToolTip('Zoom in (X)'); bzo.setToolTip('Zoom out (X)'); brs.setToolTip('Reset')
        bzi.clicked.connect(lambda:self.sig_zoom.emit(+1))
        bzo.clicked.connect(lambda:self.sig_zoom.emit(-1))
        brs.clicked.connect(lambda:self.sig_zoom.emit(0))
        for w in [bzi,self.lbl_zoom,bzo,brs]: row.addWidget(w)
        row.addWidget(self._sep())

        # ── تحكم Y (تكبير/تصغير السعر) ────────────────────────────
        lbl_y=QLabel('Y:'); lbl_y.setStyleSheet(f'color:{C["txt2"]};font:9px Consolas;')
        byi=QPushButton('▲'); byo=QPushButton('▼'); byr=QPushButton('↕')
        for b in [byi,byo,byr]: b.setStyleSheet(bs)
        byi.setToolTip('تضييق نطاق السعر (تكبير Y)')
        byo.setToolTip('توسيع نطاق السعر (تصغير Y)')
        byr.setToolTip('إعادة Y تلقائياً')
        byi.clicked.connect(lambda: self.sig_yzoom.emit(+1))
        byo.clicked.connect(lambda: self.sig_yzoom.emit(-1))
        byr.clicked.connect(lambda: self.sig_yzoom.emit(0))
        for w in [lbl_y,byi,byo,byr]: row.addWidget(w)
        row.addWidget(self._sep())

        # ── تحكم Pan (تحريك يمين/يسار) ─────────────────────────────
        bpl=QPushButton('◀'); bpr=QPushButton('▶')
        for b in [bpl,bpr]: b.setStyleSheet(bs)
        bpl.setToolTip('تحريك يساراً'); bpr.setToolTip('تحريك يميناً')
        bpl.clicked.connect(lambda: self.sig_pan.emit(-1))
        bpr.clicked.connect(lambda: self.sig_pan.emit(+1))
        row.addWidget(bpl); row.addWidget(bpr)
        row.addWidget(self._sep())

        self.lbl_ohlc=QLabel(''); self.lbl_ohlc.setStyleSheet(f'color:{C["txt"]};font:9px Consolas;')
        row.addWidget(self.lbl_ohlc,stretch=1)
        self.lbl_sess=QLabel(''); self.lbl_sess.setStyleSheet('font:bold 8px Consolas;padding:1px 3px;border-radius:2px;')
        row.addWidget(self.lbl_sess)

    def _sep(self):
        s=QFrame(); s.setFrameShape(QFrame.VLine)
        s.setStyleSheet(f'background:{C["bg3"]};'); s.setFixedWidth(1); return s

    def _on_draw(self,key,checked):
        if checked:
            for k,b in self._draw_btns.items():
                if k!=key: b.setChecked(False)
            self.sig_tool.emit(key)
        else: self.sig_tool.emit('')

    def set_tf(self,tf):
        for k,b in self._tf_btns.items(): b.setChecked(k==tf)
    def update_candle_w(self,w): self.lbl_cw.setText(f'{int(w*100)}%')
    def update_zoom(self,n): self.lbl_zoom.setText(str(n))

    def update_price(self,sym,price,open_p):
        self.lbl_sym.setText(sym or '---'); self.lbl_price.setText(_fmt_p(price))
        if open_p and open_p>0 and price:
            chg=(price-open_p)/open_p*100; s='+' if chg>=0 else ''
            col=C['bull'] if chg>=0 else C['bear']
            self.lbl_chg.setText(f'{s}{chg:.2f}%')
            self.lbl_chg.setStyleSheet(f'font:bold 9px Consolas;padding:0 3px;border-radius:2px;background:{col}28;color:{col};')

    def update_ohlc(self,o,h,l,c,v):
        bull=c>=o; col=C['bull'] if bull else C['bear']
        chg=(c-o)/o*100 if o else 0; s='+' if chg>=0 else ''
        self.lbl_ohlc.setText(f'O:{_fmt_p(o)}  H:{_fmt_p(h)}  L:{_fmt_p(l)}  C:{_fmt_p(c)}  {s}{chg:.2f}%  V:{_fmt_v(v or 0)}')
        self.lbl_ohlc.setStyleSheet(f'color:{col};font:9px Consolas;')

    def update_session(self):
        from datetime import datetime,timezone,timedelta
        now=datetime.now(timezone.utc); off=-4 if 3<=now.month<=11 else -5
        et=now+timedelta(hours=off); mins=et.hour*60+et.minute; day=et.weekday()
        if day>=5:              txt,col='WEEKEND',C['bear']
        elif 9*60+30<=mins<16*60: txt,col='● REGULAR',C['bull']
        elif 16*60<=mins<20*60:   txt,col='● AFTER-H',C['vwap']
        elif mins>=20*60 or mins<9*60+30: txt,col='● PRE-MKT',C['price_ln']
        else:                   txt,col='CLOSED',C['bear']
        self.lbl_sess.setText(txt)
        self.lbl_sess.setStyleSheet(f'font:bold 8px Consolas;padding:1px 3px;border-radius:2px;background:{col}28;color:{col};')


# ══════════════════════════════════════════════════════════════════════════════
# ProChartWidget
# ══════════════════════════════════════════════════════════════════════════════
class ProChartWidget(QWidget):
    sig_crosshair = pyqtSignal(int,float)
    data_ready    = pyqtSignal(object)
    sig_set_data_ui = pyqtSignal(object, int)
    sig_push_price_ui = pyqtSignal(float)
    sig_draw_trade_ui = pyqtSignal(object)
    sig_set_trade_plan_ui = pyqtSignal(object)
    sig_clear_trade_ui = pyqtSignal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        self._opens=[]; self._highs=[]; self._lows=[]; self._closes=[]
        self._volumes=[]; self._times=[]; self._n=0
        self._ema9=[]; self._ema21=[]; self._ema200=[]
        self._vwap=0.0; self._vwap_arr=[]
        self._bb_mid=[]; self._bb_up=[]; self._bb_dn=[]
        self._rsi=[]; self._macd_h=[]; self._macd_l=[]; self._macd_s=[]
        self._pivots={}; self._res_zones=[]; self._sup_zones=[]; self._ob=[]; self._sd_zones=[]
        self._live_price=0.0; self._open_price=0.0; self._sym=''
        self._tf_seconds=900
        self._live_items={}; self._trade_items=[]; self._plan_items=[]; self._analysis_plan=None; self._draw_items=[]
        self._draw_mode=''; self._draw_start=None; self._last_bars={}
        self._candle_w=CANDLE_W_DEFAULT; self._zoom_bars=80; self._zoom_locked=False
        self._y_scale=1.0   # ✅ تحكم Y: 1=تلقائي، <1=أضيق، >1=أوسع
        self._show={k:True for k in ['e9','e21','e200','vwap','bb','pv','sd']}
        self._panels={'rsi':False,'macd':False}
        self._price_plot=None
        self._build_ui()
        self.data_ready.connect(lambda bars: self.set_data(bars))
        self.sig_set_data_ui.connect(self._set_data_ui)
        self.sig_push_price_ui.connect(self._push_price_ui)
        self.sig_draw_trade_ui.connect(self._draw_trade_ui)
        self.sig_set_trade_plan_ui.connect(self._set_trade_plan_ui)
        self.sig_clear_trade_ui.connect(self._clear_trade_ui)
        self._timer=QTimer(); self._timer.timeout.connect(self._tick)

    def showEvent(self,e):
        super().showEvent(e)
        if not self._timer.isActive(): self._timer.start(500)
    def hideEvent(self,e):
        super().hideEvent(e); self._timer.stop()

    # ── build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        pg.setConfigOptions(antialias=True,useOpenGL=False)
        from PyQt5.QtWidgets import QVBoxLayout
        main=QVBoxLayout(self); main.setContentsMargins(0,0,0,0); main.setSpacing(0)
        self._toolbar=_ToolBar()
        self._toolbar.sig_tf.connect(self._on_tf)
        self._toolbar.sig_ind.connect(self._on_ind)
        self._toolbar.sig_panel.connect(self._on_panel)
        self._toolbar.sig_tool.connect(self._on_tool)
        self._toolbar.sig_clear.connect(self.clear_drawings)
        self._toolbar.sig_candle_w.connect(self._on_candle_w)
        self._toolbar.sig_zoom.connect(self._on_zoom)
        self._toolbar.sig_yzoom.connect(self._on_yzoom)   # ✅ Y zoom
        self._toolbar.sig_pan.connect(self._on_pan)        # ✅ Pan
        self._y_scale = 1.0   # مضاعف نطاق Y
        main.addWidget(self._toolbar)
        self._splitter=QSplitter(Qt.Vertical)
        self._splitter.setHandleWidth(2)
        self._splitter.setStyleSheet(f'QSplitter::handle{{background:{C["bg3"]};}}')
        main.addWidget(self._splitter,stretch=1)

        self._tax_p=_TimeAxis(orientation='bottom'); self._tax_v=_TimeAxis(orientation='bottom')
        self._tax_r=_TimeAxis(orientation='bottom'); self._tax_m=_TimeAxis(orientation='bottom')

        self._pp=pg.PlotWidget(axisItems={'right':_PriceAxis(orientation='right'),'bottom':self._tax_p})
        self._setup_pw(self._pp,min_h=250)
        self._pp.showAxis('right'); self._pp.hideAxis('left')
        ax=self._pp.getAxis('right'); ax.setWidth(68); ax.setPen(_mk(C['axis'])); ax.setTextPen(_mk(C['txt2'])); ax.setStyle(tickLength=-5)
        self._pp.getAxis('bottom').setStyle(showValues=False)
        self._pp.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self._price_plot=self._pp.getPlotItem()

        self._vp=pg.PlotWidget(axisItems={'bottom':self._tax_v}); self._setup_pw(self._vp,max_h=65); self._vp.setXLink(self._pp)
        self._rp=pg.PlotWidget(axisItems={'bottom':self._tax_r}); self._setup_pw(self._rp,max_h=90); self._rp.setXLink(self._pp); self._rp.setYRange(0,100,padding=0); self._rp.hide()
        self._mp=pg.PlotWidget(axisItems={'bottom':self._tax_m}); self._setup_pw(self._mp,max_h=80); self._mp.setXLink(self._pp); self._mp.hide()
        for pw in [self._pp,self._vp,self._rp,self._mp]: self._splitter.addWidget(pw)
        self._splitter.setSizes([400,65,0,0])

        # عناصر ثابتة
        self._price_line=pg.InfiniteLine(angle=0,movable=False,pen=_mk(C['price_ln'],1,Qt.DashLine,180))
        self._price_tag=_PriceTag(C['price_ln'],9)   # سعر حي — أزرق
        self._close_tag=_PriceTag(C['bg4'],9)         # آخر إغلاق — رمادي
        self._timer_tag=_PriceTag(C['timer_bg'],8)    # موقت — أخضر داكن
        for it in [self._price_line,self._price_tag,self._close_tag,self._timer_tag]:
            self._pp.addItem(it,ignoreBounds=True)

        cp=_mk(C['cross'],1,Qt.DashLine,160)
        self._ch_v=pg.InfiniteLine(angle=90,movable=False,pen=cp)
        self._ch_h=pg.InfiniteLine(angle=0, movable=False,pen=cp)
        self._ch_v2=pg.InfiniteLine(angle=90,movable=False,pen=cp)
        self._ch_v3=pg.InfiniteLine(angle=90,movable=False,pen=cp)
        self._ch_v4=pg.InfiniteLine(angle=90,movable=False,pen=cp)
        self._ch_lbl=pg.TextItem('',color=C['txt'],anchor=(0,0))
        self._ch_lbl.setFont(QFont('Consolas',9))
        for it in [self._ch_v,self._ch_h,self._ch_lbl]: self._pp.addItem(it,ignoreBounds=True)
        self._vp.addItem(self._ch_v2,ignoreBounds=True)
        self._rp.addItem(self._ch_v3,ignoreBounds=True)
        self._mp.addItem(self._ch_v4,ignoreBounds=True)

        self._pp.scene().sigMouseMoved.connect(self._on_mouse_move)
        self._pp.scene().sigMouseClicked.connect(self._on_mouse_click)
        self._pp.getViewBox().wheelEvent=self._on_wheel

    def _setup_pw(self,pw,min_h=0,max_h=0):
        pw.setBackground(C['bg']); pw.showGrid(x=True,y=True,alpha=0.06)
        for ax in ('left','right','bottom'):
            try: pw.getAxis(ax).setPen(_mk(C['axis'])); pw.getAxis(ax).setTextPen(_mk(C['txt2']))
            except: pass
        if min_h: pw.setMinimumHeight(min_h)
        if max_h: pw.setMaximumHeight(max_h)

    def _re_add_fixed(self):
        for it in [self._price_line,self._price_tag,self._close_tag,self._timer_tag,
                   self._ch_v,self._ch_h,self._ch_lbl]:
            self._pp.addItem(it,ignoreBounds=True)
        self._vp.addItem(self._ch_v2,ignoreBounds=True)
        self._rp.addItem(self._ch_v3,ignoreBounds=True)
        self._mp.addItem(self._ch_v4,ignoreBounds=True)

    # ── set_data ──────────────────────────────────────────────────────────────
    def set_data(self,bars:dict,max_bars:int=500):
        if QThread.currentThread() != self.thread():
            self.sig_set_data_ui.emit(dict(bars or {}), int(max_bars))
            return
        self._set_data_ui(bars, max_bars)

    @pyqtSlot(object, int)
    def _set_data_ui(self,bars:dict,max_bars:int=500):
        closes=bars.get('closes',[]); 
        if not closes: return
        self._last_bars=bars
        n0=max(0,len(closes)-max_bars)
        def _s(k,fb=None):
            v=bars.get(k,fb or []); return list(v[n0:]) if v else []
        self._opens=_s('opens',closes); self._highs=_s('highs',closes)
        self._lows=_s('lows',closes);   self._closes=list(closes[n0:])
        self._volumes=_s('volumes') or _s('vols') or [0]*len(self._closes)
        self._times=_s('times'); self._n=len(self._closes)
        self._ema9=_s('ema9'); self._ema21=_s('ema21'); self._ema200=_s('ema200')
        self._vwap=float(bars.get('vwap',0) or 0); self._vwap_arr=_s('vwap_arr')
        self._bb_mid=_s('bb_mid'); self._bb_up=_s('bb_up'); self._bb_dn=_s('bb_dn')
        self._rsi=_s('rsi'); self._macd_h=_s('macd_hist'); self._macd_l=_s('macd_line'); self._macd_s=_s('macd_sig')
        self._pivots=bars.get('pivots',{}); self._res_zones=bars.get('res_zones',[]); self._sup_zones=bars.get('sup_zones',[]); self._ob=bars.get('order_blocks',[]); self._sd_zones=bars.get('sd_zones',[])
        self._sym=bars.get('symbol',bars.get('sym','')); self._open_price=float(bars.get('open_price',0) or 0)
        if bars.get('price',0)>0: self._live_price=float(bars['price'])

        if self._times:
            step=max(1,self._n//8)
            lbl={i:str(t)[-5:] for i,t in enumerate(self._times) if i%step==0}
            for ax in [self._tax_p,self._tax_v,self._tax_r,self._tax_m]: ax.set_labels(lbl)

        try:
            _vr=self._pp.getViewBox().viewRange(); _xr,_yr=_vr[0],_vr[1]
            _had=bool(self._live_items) and self._zoom_locked
        except: _xr=_yr=None; _had=False

        for pw in [self._pp,self._vp,self._rp,self._mp]: pw.clear()
        self._live_items={}; self._trade_items=[]; self._plan_items=[]
        self._re_add_fixed()

        n=self._n; xs=np.arange(n,dtype=float)
        o=np.array(self._opens,dtype=float); h=np.array(self._highs,dtype=float)
        l=np.array(self._lows,dtype=float);  c=np.array(self._closes,dtype=float)
        v=np.array(self._volumes,dtype=float)

        self._draw_sessions(n)
        if self._show['sd']:   self._draw_zones(n)
        if self._show['bb'] and len(self._bb_up)==n: self._draw_bb(n,xs)
        if self._show['e200'] and self._ema200: self._draw_ema(self._ema200,C['e200'],2,True,n)
        if self._show['e21']  and self._ema21:  self._draw_ema(self._ema21, C['e21'], 1,False,n)
        if self._show['e9']   and self._ema9:   self._draw_ema(self._ema9,  C['e9'],  1,False,n)
        if self._show['vwap']: self._draw_vwap(n)
        if self._show['pv']:   self._draw_pivots(n)

        # شموع تاريخية
        if n>1:
            hist=n-1; cw=self._candle_w
            bull=c[:hist]>=o[:hist]; bear=~bull
            bx,by=_wicks_arr(bull,xs[:hist],h[:hist],l[:hist])
            rx,ry=_wicks_arr(bear,xs[:hist],h[:hist],l[:hist])
            if len(bx): self._pp.plot(bx,by,pen=_mk(C['bull'],1),connect='finite')
            if len(rx): self._pp.plot(rx,ry,pen=_mk(C['bear'],1),connect='finite')
            bi=np.where(bull)[0]; ri=np.where(bear)[0]
            if len(bi): self._pp.addItem(pg.BarGraphItem(x=xs[bi],height=(c-o)[bi],y0=o[bi],width=cw,brush=_br(C['bull'],200),pen=_mk(C['bull'])))
            if len(ri): self._pp.addItem(pg.BarGraphItem(x=xs[ri],height=(c-o)[ri],y0=o[ri],width=cw,brush=_br(C['bear'],200),pen=_mk(C['bear'])))

        self._draw_live_candle(self._opens[-1],self._highs[-1],self._lows[-1],self._closes[-1],n-1)
        self._draw_volume(v,o,c,xs,n)
        if self._panels['rsi']:  self._draw_rsi(n)
        if self._panels['macd']: self._draw_macd(n)

        if _had and _xr:
            try: self._pp.setXRange(_xr[0],_xr[1],padding=0); self._pp.setYRange(_yr[0],_yr[1],padding=0)
            except: pass
        else: self._set_range()

        for item in self._draw_items:
            try: self._pp.addItem(item)
            except: pass

        if self._analysis_plan:
            try: self.draw_analysis_plan(self._analysis_plan)
            except Exception: pass

        if self._live_price>0: self._update_price_line(self._live_price)
        self._toolbar.update_price(self._sym,self._live_price,self._open_price)

    # ── رسم فرعي ─────────────────────────────────────────────────────────────
    def _draw_sessions(self, n):
        """Draw session background bands. Groups consecutive same-session bars into
        one LinearRegionItem per span instead of one per bar (was O(n) addItem calls)."""
        if not self._times: return
        spans = []          # (x_start, x_end, color)
        cur_col   = None
        cur_start = 0
        for i, t in enumerate(self._times):
            ts = str(t)[-5:]
            try:
                hh, mm = int(ts[:2]), int(ts[3:5])
                mins = hh * 60 + mm
                if   mins < 9 * 60 + 30: col = C['pre']
                elif mins < 16 * 60:     col = C['reg']
                elif mins < 20 * 60:     col = C['ah']
                else:                    col = None
            except:
                col = None
            if col != cur_col:
                if cur_col is not None and cur_start < i:
                    spans.append((cur_start, i, cur_col))
                cur_col   = col
                cur_start = i
        if cur_col is not None and cur_start < n:
            spans.append((cur_start, n, cur_col))
        for x0, x1, col in spans:
            r = pg.LinearRegionItem(
                [x0 - 0.5, x1 - 0.5],
                orientation='vertical', movable=False,
                brush=_br(col, 70), pen=pg.mkPen(None)
            )
            self._pp.addItem(r, ignoreBounds=True)

    def _draw_zones(self, n):
        """
        رسم S&D Zones + Order Blocks على الشارت.
        البيانات تأتي من bars_data الذي يُحسَب في trader_final.
        """
        price = self._live_price if self._live_price > 0 else (self._closes[-1] if self._closes else 0)
        x_left = max(0, n - 300)  # امتد للخلف بما يكفي

        # ── 1) S&D Zones (demand = أخضر، supply = أحمر) ─────────────────────
        sd = self._sd_zones or []
        for z in sd[:12]:
            ztype  = z.get('type', '')
            is_dem = 'demand' in ztype
            t = float(z.get('top',    0))
            b = float(z.get('bottom', 0))
            if not t or not b or t == b: continue

            fresh    = z.get('fresh', True)
            strength = float(z.get('strength', 2.0))
            bos_ok   = z.get('bos_confirmed', False)
            touches  = z.get('touches', 0)

            base_clr = '#26a69a' if is_dem else '#ef5350'  # تطابق ألوان TV
            alpha    = 40 if fresh else 16
            brd_w    = 1.2 if fresh else 0.6
            brd_al   = 150 if fresh else 70

            # المنطقة
            r = pg.LinearRegionItem(
                [min(t,b), max(t,b)], orientation='horizontal',
                movable=False,
                brush=_br(base_clr, alpha),
                pen=_mk(base_clr, brd_w, alpha=brd_al))
            self._pp.addItem(r, ignoreBounds=True)

            # خط حافة المنطقة (الحافة الأقرب للسعر)
            edge = b if is_dem else t
            self._pp.plot([x_left, n+10], [edge, edge],
                pen=_mk(base_clr, 0.7, Qt.DashLine, alpha=90))

            # Label
            mid   = (t + b) / 2
            ltype = 'D' if is_dem else 'S'
            star  = '★' if fresh else '○'
            bos   = ' ✓' if bos_ok else ''
            tch   = f' T{touches}' if touches else ''
            lbl   = f' {ltype}{star} {strength:.1f}{bos}{tch}'
            lb = pg.TextItem(lbl, color=base_clr, anchor=(0, 0.5))
            lb.setFont(QFont('Consolas', 7, QFont.Bold))
            lb.setPos(x_left + 1, mid)
            self._pp.addItem(lb)

        # ── 2) Order Blocks (demand_ob = أخضر فاتح، supply_ob = أحمر فاتح) ──
        obs = self._ob or []
        for z in obs[:10]:
            ztype  = z.get('type', '')
            is_dem = 'demand' in ztype
            t = float(z.get('top',    0))
            b = float(z.get('bottom', 0))
            if not t or not b or t == b: continue

            fresh    = z.get('fresh', True)
            strength = float(z.get('strength', 2.2))
            bos_ok   = z.get('bos_confirmed', False)
            touches  = z.get('touches', 0)

            # Order Blocks: لون مختلف قليلاً عن S&D للتمييز
            base_clr = '#00e5a0' if is_dem else '#ff6b6b'
            alpha    = 45 if fresh else 18
            brd_w    = 1.5 if fresh else 0.7
            brd_al   = 200 if fresh else 100

            r = pg.LinearRegionItem(
                [min(t,b), max(t,b)], orientation='horizontal',
                movable=False,
                brush=_br(base_clr, alpha),
                pen=_mk(base_clr, brd_w, alpha=brd_al))
            self._pp.addItem(r, ignoreBounds=True)

            # خط الحافة
            edge = b if is_dem else t
            self._pp.plot([x_left, n+12], [edge, edge],
                pen=_mk(base_clr, 0.9, Qt.SolidLine, alpha=110))

            # Label (على يمين الشارت)
            mid    = (t + b) / 2
            arrow  = '▲' if is_dem else '▼'
            ob_lbl = 'DOB' if is_dem else 'SOB'
            bos    = '✓' if bos_ok else ''
            tch    = f'T{touches}' if touches else ''
            lbl    = f' {ob_lbl}{arrow}{bos} {strength:.1f} {tch}'
            lb = pg.TextItem(lbl, color=base_clr, anchor=(0, 0.5))
            lb.setFont(QFont('Consolas', 8, QFont.Bold))
            lb.setPos(max(0, n - 8), mid)
            self._pp.addItem(lb)

        # ── 3) Fallback: res/sup zones العامة إذا لم يوجد sd/ob ─────────────
        if not sd and not obs:
            for zones, clr, al in [(self._res_zones, C['r'], 28),
                                   (self._sup_zones, C['s'], 28)]:
                for i, z in enumerate(zones[:5]):
                    t = z.get('top',    z[0] if isinstance(z,(list,tuple)) else 0)
                    b = z.get('bottom', z[1] if isinstance(z,(list,tuple)) else 0)
                    if not t or not b: continue
                    r = pg.LinearRegionItem([min(t,b),max(t,b)], orientation='horizontal',
                        movable=False, brush=_br(clr, max(8,al-i*4)),
                        pen=_mk(clr, 0.6, alpha=60))
                    self._pp.addItem(r, ignoreBounds=True)

    def _draw_bb(self,n,xs):
        fill=pg.FillBetweenItem(pg.PlotDataItem(xs,self._bb_up),pg.PlotDataItem(xs,self._bb_dn),brush=_br(C['bb_band'],60))
        self._pp.addItem(fill)
        self._pp.plot(xs,self._bb_up,pen=_mk(C['bb_mid'],0.8,Qt.DashLine))
        self._pp.plot(xs,self._bb_mid,pen=_mk(C['bb_mid'],1))
        self._pp.plot(xs,self._bb_dn,pen=_mk(C['bb_mid'],0.8,Qt.DashLine))

    def _draw_ema(self,arr,clr,w,dash,n):
        if not arr: return
        m=len(arr); xs=np.arange(n-m,n,dtype=float)
        self._pp.plot(xs,list(arr),pen=_mk(clr,w,Qt.DashLine if dash else Qt.SolidLine))

    def _draw_vwap(self,n):
        if self._vwap_arr and len(self._vwap_arr)==n:
            xs=np.arange(n,dtype=float); self._pp.plot(xs,self._vwap_arr,pen=_mk(C['vwap'],1.5,Qt.DashLine))
            t=pg.TextItem(f'VWAP {_fmt_p(self._vwap_arr[-1])}',color=C['vwap'],anchor=(0,1)); t.setFont(QFont('Consolas',7)); t.setPos(n-2,self._vwap_arr[-1]); self._pp.addItem(t)
        elif self._vwap>0:
            self._pp.plot([0,n],[self._vwap,self._vwap],pen=_mk(C['vwap'],1.5,Qt.DashLine))
            t=pg.TextItem(f'VWAP {_fmt_p(self._vwap)}',color=C['vwap'],anchor=(0,1)); t.setFont(QFont('Consolas',7)); t.setPos(n-2,self._vwap); self._pp.addItem(t)

    def _draw_pivots(self,n):
        for k,clr,lbl,al in [('r3',C['r'],'R3',50),('r2',C['r'],'R2',75),('r1',C['r'],'R1',110),
                              ('pp',C['pp'],'PP',180),('s1',C['s'],'S1',110),('s2',C['s'],'S2',75),('s3',C['s'],'S3',50)]:
            v=self._pivots.get(k)
            if not v: continue
            self._pp.plot([0,n],[v,v],pen=_mk(clr,0.8,Qt.DotLine,al))
            t=pg.TextItem(f'{lbl} {_fmt_p(v)}',color=clr,anchor=(1,0.5)); t.setFont(QFont('Consolas',7)); t.setPos(0,v); self._pp.addItem(t)

    def _draw_volume(self,v,o,c,xs,n):
        if not len(v): return
        bull=c>=o; bi=np.where(bull)[0]; ri=np.where(~bull)[0]
        if len(bi): self._vp.addItem(pg.BarGraphItem(x=xs[bi],height=v[bi],y0=0,width=self._candle_w,brush=_br(C['vbull']),pen=_mk(C['bull'],0.5)))
        if len(ri): self._vp.addItem(pg.BarGraphItem(x=xs[ri],height=v[ri],y0=0,width=self._candle_w,brush=_br(C['vbear']),pen=_mk(C['bear'],0.5)))
        if n>=20:
            ma=np.convolve(v,np.ones(20)/20,mode='valid'); self._vp.plot(list(range(19,n)),list(ma),pen=_mk(C['e9'],1))

    def _draw_rsi(self,n):
        if not self._rsi: return
        self._rp.addItem(pg.LinearRegionItem([70,100],orientation='horizontal',movable=False,brush=_br(C['bear'],18),pen=pg.mkPen(None)))
        self._rp.addItem(pg.LinearRegionItem([0,30],orientation='horizontal',movable=False,brush=_br(C['bull'],18),pen=pg.mkPen(None)))
        for lvl,clr in [(70,C['bear']),(50,'#787878'),(30,C['bull'])]: self._rp.plot([0,n],[lvl,lvl],pen=_mk(clr,0.6,Qt.DashLine))
        xs=np.arange(len(self._rsi),dtype=float)
        valid=[(xs[i],self._rsi[i]) for i in range(len(self._rsi)) if self._rsi[i] is not None]
        if valid:
            vx,vy=zip(*valid); self._rp.plot(list(vx),list(vy),pen=_mk(C['e9'],1))
        self._rp.setYRange(0,100,padding=0)

    def _draw_macd(self,n):
        if not self._macd_h: return
        xs=np.arange(len(self._macd_h),dtype=float); mh=np.array(self._macd_h,dtype=float)
        bi=np.where(mh>=0)[0]; ri=np.where(mh<0)[0]
        if len(bi): self._mp.addItem(pg.BarGraphItem(x=xs[bi],height=mh[bi],y0=0,width=min(0.7,self._candle_w),brush=_br(C['bull'],180),pen=_mk(C['bull'],0.5)))
        if len(ri): self._mp.addItem(pg.BarGraphItem(x=xs[ri],height=mh[ri],y0=0,width=min(0.7,self._candle_w),brush=_br(C['bear'],180),pen=_mk(C['bear'],0.5)))
        if self._macd_l:
            _vl=[(xs[i],self._macd_l[i]) for i in range(len(self._macd_l)) if self._macd_l[i] is not None]
            if _vl: _vx,_vy=zip(*_vl); self._mp.plot(list(_vx),list(_vy),pen=_mk('#2196f3',1))
        if self._macd_s:
            _vs=[(xs[i],self._macd_s[i]) for i in range(len(self._macd_s)) if self._macd_s[i] is not None]
            if _vs: _sx,_sy=zip(*_vs); self._mp.plot(list(_sx),list(_sy),pen=_mk(C['bear'],1))

    def _draw_live_candle(self,o,h,l,c,idx):
        clr=C['bull'] if c>=o else C['bear']; cw=self._candle_w
        if self._live_items.get('ok'):
            self._live_items['wick'].setData([idx,idx],[l,h],pen=_mk(clr,1))
            self._live_items['body'].setOpts(x=[idx],height=[c-o],y0=[o],brushes=[_br(clr,200)],pens=[_mk(clr)])
        else:
            wick=self._pp.plot([idx,idx],[l,h],pen=_mk(clr,1))
            body=pg.BarGraphItem(x=[idx],height=[c-o],y0=[o],width=cw,brushes=[_br(clr,200)],pens=[_mk(clr)])
            self._pp.addItem(body); self._live_items={'ok':True,'wick':wick,'body':body}
        self._live_items.update({'o':o,'h':h,'l':l,'c':c})

    def _set_range(self):
        n=self._n
        if not n: return
        w=min(self._zoom_bars,n); vh=self._highs[-w:]; vl=self._lows[-w:]
        lp=self._live_price if self._live_price>0 else self._closes[-1]
        mg=max((max(vh)-min(vl))*0.06,lp*0.003)
        # ✅ تطبيق _y_scale: أكبر من 1 = نطاق أوسع، أصغر = أضيق
        ys = getattr(self, '_y_scale', 1.0)
        extra = (max(vh)-min(vl)) * (ys - 1.0) * 0.5
        self._pp.setYRange(min(vl)-mg-extra, max(vh)+mg+extra, padding=0)
        self._pp.setXRange(n-w-1,n+RIGHT_MARGIN,padding=0)
        if self._volumes:
            vv=self._volumes[-w:]; self._vp.setYRange(0,max(vv)*1.25 if vv else 1,padding=0)

    def _on_yzoom(self, direction: int):
        """تحكم Y: +1 تضييق (تكبير) / -1 توسيع (تصغير) / 0 reset تلقائي"""
        if direction == 0:
            self._y_scale = 1.0
        elif direction > 0:
            self._y_scale = max(0.1, getattr(self, '_y_scale', 1.0) * 0.75)
        else:
            self._y_scale = min(8.0, getattr(self, '_y_scale', 1.0) * 1.35)
        self._set_range()

    def _on_pan(self, direction: int):
        """تحريك الشارت: -1 يسار / +1 يمين بنسبة 20% من النطاق"""
        try:
            vb = self._pp.getViewBox()
            xr = vb.viewRange()[0]
            step = (xr[1] - xr[0]) * 0.2
            new_l = xr[0] + direction * step
            new_r = xr[1] + direction * step
            # لا تتجاوز حدود البيانات
            new_l = max(-1, new_l)
            new_r = min(self._n + RIGHT_MARGIN + 5, new_r)
            self._pp.setXRange(new_l, new_r, padding=0)
            self._zoom_locked = True
        except Exception:
            pass

    # ── Price tags ────────────────────────────────────────────────────────────
    def _get_x_right(self):
        try: return self._pp.getViewBox().viewRange()[0][1]
        except: return self._n+RIGHT_MARGIN

    def _update_price_line(self,price:float):
        self._price_line.setPos(price)
        xr=self._get_x_right()
        self._price_tag.setPos(xr,price)
        self._price_tag.set_text(_fmt_p(price))
        if self._closes:
            lc=self._closes[-1]
            if abs(lc-price)>0.005:
                self._close_tag.setPos(xr,lc); self._close_tag.set_text(_fmt_p(lc))
            else: self._close_tag.set_text('')

    def _update_tags_x(self):
        if not self._closes: return
        price=self._live_price if self._live_price>0 else self._closes[-1]
        xr=self._get_x_right()
        self._price_tag.setPos(xr,price)
        if self._closes: self._close_tag.setPos(xr,self._closes[-1])
        self._timer_tag.setPos(xr,price)

    # ── تحديث لحظي ────────────────────────────────────────────────────────────
    def push_price(self,price:float):
        if QThread.currentThread() != self.thread():
            self.sig_push_price_ui.emit(float(price or 0))
            return
        self._push_price_ui(price)

    @pyqtSlot(float)
    def _push_price_ui(self,price:float):
        if price>0: self._live_price=price
        if self._closes and self._live_price>0: self._update_live(self._live_price)

    def update_live(self,price:float): self.push_price(price)

    def _update_live(self,price:float):
        if not self._closes: return
        idx=self._n-1; o=self._opens[-1]
        h=max(self._highs[-1],price); l=min(self._lows[-1],price)
        self._draw_live_candle(o,h,l,price,idx)
        self._update_price_line(price)
        self._toolbar.update_price(self._sym,price,self._open_price)

    def _tick(self):
        if self._live_price>0 and self._closes:
            self._check_new_candle(); self._update_live(self._live_price)
        self._update_countdown(); self._update_tags_x(); self._toolbar.update_session()

    def _get_cs(self):
        now=_dt.now(); tf=self._tf_seconds
        if tf>=86400: return now.replace(hour=0,minute=0,second=0,microsecond=0)
        if tf>=3600:
            hp=tf//3600; ch=(now.hour//hp)*hp; return now.replace(hour=ch,minute=0,second=0,microsecond=0)
        mp=max(1,tf//60); cm=(now.minute//mp)*mp; return now.replace(minute=cm,second=0,microsecond=0)

    def _check_new_candle(self):
        if not self._closes or self._live_price<=0: return
        try:
            cs=self._get_cs()
            if not hasattr(self,'_cur_cs'): self._cur_cs=cs; return
            if cs>self._cur_cs: self._cur_cs=cs; self._close_candle()
        except: pass

    def _close_candle(self):
        p=self._live_price
        self._closes[-1]=p; self._highs[-1]=max(self._highs[-1],p); self._lows[-1]=min(self._lows[-1],p)
        idx=self._n-1; o,h,l,c=self._opens[-1],self._highs[-1],self._lows[-1],self._closes[-1]
        clr=C['bull'] if c>=o else C['bear']
        if self._live_items.get('ok'):
            try: self._pp.removeItem(self._live_items['wick']); self._pp.removeItem(self._live_items['body'])
            except: pass
        self._live_items={}
        self._pp.plot([idx,idx],[l,h],pen=_mk(clr,1))
        self._pp.addItem(pg.BarGraphItem(x=[idx],height=[c-o],y0=[o],width=self._candle_w,brushes=[_br(clr)],pens=[_mk(clr)]))
        self._opens.append(p); self._highs.append(p); self._lows.append(p); self._closes.append(p)
        self._volumes.append(0); self._times.append(_dt.now().strftime('%H:%M')); self._n+=1
        self._draw_live_candle(p,p,p,p,self._n-1)
        if not self._zoom_locked:
            try:
                vb=self._pp.getViewBox(); xr=vb.viewRange()[0]
                if xr[1]>=self._n-3: self._pp.setXRange(self._n-self._zoom_bars-1,self._n+RIGHT_MARGIN,padding=0)
            except: pass

    def _update_countdown(self):
        if not self._closes: return
        try:
            now=_dt.now(); tf=self._tf_seconds
            if tf>=86400: nx=(now+_td(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
            elif tf>=3600:
                hp=tf//3600; ch=(now.hour//hp)*hp; nx=now.replace(hour=ch,minute=0,second=0,microsecond=0)+_td(hours=hp)
            else:
                mp=max(1,tf//60); cm=(now.minute//mp)*mp; nx=now.replace(minute=cm,second=0,microsecond=0)+_td(minutes=mp)
            rem=max(0,(nx-now).total_seconds()); m,s=int(rem//60),int(rem%60)
            col=C['bear'] if rem<30 else C['timer_bg']
            self._timer_tag._color=col
            # ضع الموقت أسفل السعر قليلاً
            price=self._live_price if self._live_price>0 else self._closes[-1]
            xr=self._get_x_right()
            # أوجد range السعر لتحديد الإزاحة
            try:
                yr=self._pp.getViewBox().viewRange()[1]
                offset=(yr[1]-yr[0])*0.018
            except: offset=0
            self._timer_tag.setPos(xr, price-offset)
            self._timer_tag.set_text(f'⏱{m:02d}:{s:02d}')
        except: pass

    # ── Zoom & Candle Width ────────────────────────────────────────────────────
    def _on_candle_w(self,direction:int):
        new_w=max(CANDLE_W_MIN,min(CANDLE_W_MAX, self._candle_w+direction*CANDLE_W_STEP))
        if abs(new_w-self._candle_w)<0.001: return
        self._candle_w=new_w; self._toolbar.update_candle_w(new_w)
        if self._last_bars: self.set_data(self._last_bars)

    def _on_zoom(self,direction:int):
        if direction==0:
            self._zoom_bars=80; self._zoom_locked=False
            self._y_scale=1.0   # ✅ إعادة Y أيضاً
            self._set_range()
        else:
            factor=(1-ZOOM_STEP) if direction>0 else (1+ZOOM_STEP)
            new_z=int(self._zoom_bars*factor)
            new_z=max(ZOOM_BARS_MIN,min(ZOOM_BARS_MAX,new_z))
            if new_z==self._zoom_bars: return
            self._zoom_bars=new_z; self._zoom_locked=True
            self._toolbar.update_zoom(new_z)
            if self._n: self._pp.setXRange(self._n-new_z-1,self._n+RIGHT_MARGIN,padding=0)

    def _on_wheel(self,ev):
        try:
            delta = ev.angleDelta().y()
        except AttributeError:
            try:
                delta = ev.delta()
            except AttributeError:
                delta = 0
        if delta==0: return
        self._on_zoom(+1 if delta>0 else -1)
        try:
            ev.accept()
        except Exception:
            pass

    # ── Crosshair ────────────────────────────────────────────────────────────
    def _on_mouse_move(self,pos):
        vb=self._pp.getPlotItem().getViewBox()
        if not self._pp.sceneBoundingRect().contains(pos):
            for ln in (self._ch_v,self._ch_h,self._ch_v2,self._ch_v3,self._ch_v4): ln.setPos(-99999)
            self._ch_lbl.setText(''); return
        pt=vb.mapSceneToView(pos); x,y=pt.x(),pt.y(); xi=int(round(x))
        for ln in (self._ch_v,self._ch_v2,self._ch_v3,self._ch_v4): ln.setPos(x)
        self._ch_h.setPos(y)
        if 0<=xi<self._n:
            o,h,l,c=self._opens[xi],self._highs[xi],self._lows[xi],self._closes[xi]
            v=self._volumes[xi] if xi<len(self._volumes) else 0
            self._toolbar.update_ohlc(o,h,l,c,v)
            chg=c-o; pct=chg/o*100 if o else 0; arrow='▲' if chg>=0 else '▼'
            txt=f'O:{_fmt_p(o)}  H:{_fmt_p(h)}  L:{_fmt_p(l)}  C:{_fmt_p(c)}  {arrow}{abs(chg):.2f} ({pct:+.2f}%)'
        else: txt=_fmt_p(y)
        self._ch_lbl.setText(txt)
        self._ch_lbl.setPos(vb.viewRect().left()+0.5,vb.viewRect().top())
        self.sig_crosshair.emit(xi,y); self._update_tags_x()

    # ── أدوات الرسم ────────────────────────────────────────────────────────────
    def _on_mouse_click(self,event):
        if not self._draw_mode: return
        pos=event.scenePos()
        if not self._pp.sceneBoundingRect().contains(pos): return
        vb=self._pp.getPlotItem().getViewBox(); pt=vb.mapSceneToView(pos); x,y=pt.x(),pt.y()
        if self._draw_mode=='hline':
            it=pg.InfiniteLine(pos=y,angle=0,movable=True,pen=_mk(C['e9'],1.5)); self._pp.addItem(it); self._draw_items.append(it)
        elif self._draw_mode=='line':
            if self._draw_start is None:
                self._draw_start=(x,y); dot=pg.ScatterPlotItem([x],[y],symbol='o',size=6,brush=_br(C['price_ln'])); self._pp.addItem(dot); self._draw_items.append(dot)
            else:
                x0,y0=self._draw_start; it=pg.PlotDataItem([x0,x],[y0,y],pen=_mk(C['price_ln'],1.5)); self._pp.addItem(it); self._draw_items.append(it); self._draw_start=None
        elif self._draw_mode=='rect':
            if self._draw_start is None: self._draw_start=(x,y)
            else:
                x0,y0=self._draw_start; it=pg.LinearRegionItem([min(y0,y),max(y0,y)],orientation='horizontal',movable=True,brush=_br('#8b00ff',25),pen=_mk(C['e21'],1.5)); self._pp.addItem(it); self._draw_items.append(it); self._draw_start=None
        elif self._draw_mode=='fib':
            if self._draw_start is None: self._draw_start=(x,y)
            else:
                x0,y0=self._draw_start; rng=y0-y
                for i,lvl in enumerate(FIB_LEVELS):
                    fv=y+rng*lvl; clr=C['fib'][i%len(C['fib'])]
                    ln=pg.InfiniteLine(pos=fv,angle=0,movable=False,pen=_mk(clr,0.8,Qt.DashLine))
                    lb=pg.TextItem(f' {lvl*100:.1f}% {_fmt_p(fv)}',color=clr,anchor=(0,1)); lb.setFont(QFont('Consolas',7)); lb.setPos(min(x0,x),fv)
                    self._pp.addItem(ln); self._draw_items.append(ln)
                    self._pp.addItem(lb); self._draw_items.append(lb)
                self._draw_start=None

    def set_draw_mode(self,mode:str):
        self._draw_mode=mode; self._draw_start=None
        self._pp.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)

    def clear_drawings(self):
        for it in self._draw_items:
            try: self._pp.removeItem(it)
            except: pass
        self._draw_items=[]; self._draw_start=None

    # ── رسم الصفقات ────────────────────────────────────────────────────────────
    def draw_trade(self,trade:dict):
        if QThread.currentThread() != self.thread():
            self.sig_draw_trade_ui.emit(dict(trade or {}))
            return
        self._draw_trade_ui(trade)

    @pyqtSlot(object)
    def _draw_trade_ui(self,trade:dict):
        self.clear_trade()
        if not self._closes: return
        oe=trade.get('entry_price',trade.get('entry',0)); os_=trade.get('stop_loss',trade.get('sl',0)); ot=trade.get('take_profit',trade.get('tp',0))
        if not (oe and os_ and ot): return
        direc=trade.get('direction','CALL'); sym=trade.get('symbol',''); contr=trade.get('contracts',1); strike=trade.get('strike',0); expiry=trade.get('expiry',''); is_long=direc=='CALL'
        sp=trade.get('entry_stock_price',0)
        if not sp: sp=self._live_price if self._live_price>0 else self._closes[-1]
        slp=(os_-oe)/oe; tpp=(ot-oe)/oe
        stp=sp*(1+tpp*0.3) if is_long else sp*(1-tpp*0.3)
        ssp=sp*(1+slp*0.3) if is_long else sp*(1-slp*0.3)
        n=self._n; items=[]
        def _ln(y,clr,w=1.5,dash=False): return self._pp.plot([n-1,n+14],[y,y],pen=_mk(clr,w,Qt.DashLine if dash else Qt.SolidLine))
        def _lb(txt,y,clr,anc=(0,0.5)):
            t=pg.TextItem(txt,color=clr,anchor=anc); t.setFont(QFont('Consolas',8,QFont.Bold)); t.setPos(n+1,y); self._pp.addItem(t); return t
        def _rg(y0,y1,clr,a=20):
            r=pg.LinearRegionItem([min(y0,y1),max(y0,y1)],orientation='horizontal',movable=False,brush=_br(clr,a),pen=pg.mkPen(None)); self._pp.addItem(r); return r
        items.append(_ln(sp,C['entry'],2))
        ef=f"{expiry[4:6]}/{expiry[6:]}" if len(expiry)>=8 else expiry
        items.append(_lb(f' ▶ {sym} {direc}  K:{strike}  {ef}  @${oe:.2f}×{contr} ',sp,C['bg'],(0,1.5)))
        pl=pg.TextItem(f' ${_fmt_p(sp)} ',color='#fff',anchor=(0,0.5),fill=_br(C['entry'])); pl.setFont(QFont('Consolas',9,QFont.Bold)); pl.setPos(n+1,sp); self._pp.addItem(pl); items.append(pl)
        items.append(_ln(ssp,C['sl'],dash=True)); items.append(_lb(f' ✕ SL ${os_:.2f} ({slp*100:+.0f}%) ',ssp,C['sl'],(0,0)))
        items.append(_ln(stp,C['tp'],dash=True)); items.append(_lb(f' ✓ TP ${ot:.2f} (+{tpp*100:.0f}%) ',stp,C['tp'],(0,1)))
        items.append(_rg(sp,stp,C['tp'] if is_long else C['bear'])); items.append(_rg(sp,ssp,C['sl'],12))
        rng=abs(stp-sp); dot=pg.ScatterPlotItem([n-1],[sp+(-1 if is_long else 1)*rng*0.015],symbol='t1' if is_long else 't',size=18,brush=_br(C['entry']),pen=_mk('#fff',1.5))
        self._pp.addItem(dot); items.append(dot); self._trade_items=items
        ap=self._highs[-50:]+self._lows[-50:]+[ssp,stp,sp]; pad=(max(ap)-min(ap))*0.06
        self._pp.setYRange(min(ap)-pad,max(ap)+pad,padding=0)

    def clear_trade(self):
        if QThread.currentThread() != self.thread():
            self.sig_clear_trade_ui.emit()
            return
        self._clear_trade_ui()

    @pyqtSlot()
    def _clear_trade_ui(self):
        for it in self._trade_items:
            try: self._pp.removeItem(it)
            except: pass
        self._trade_items=[]


    def clear_analysis_plan(self):
        for it in self._plan_items:
            try: self._pp.removeItem(it)
            except: pass
        self._plan_items=[]


    def draw_analysis_plan(self, plan:dict):
        self.clear_analysis_plan()
        self._analysis_plan = plan or None
        if not self._analysis_plan or not self._closes:
            return
        entry = float(plan.get('entry_price', 0) or 0)
        stop  = float(plan.get('stop_price', 0) or 0)
        target= float(plan.get('target_price', 0) or 0)
        direction = str(plan.get('direction', 'CALL')).upper()
        zone = plan.get('zone') or {}
        score = plan.get('score', 0)
        why = plan.get('why', [])
        if isinstance(why, str):
            why = [w.strip() for w in why.split('|') if w.strip()]
        if not (entry and stop and target):
            return
        n=self._n; items=[]
        x0=max(0, n-35); x1=n+18
        def _hline(y, clr, txt, dash=False):
            ln=self._pp.plot([x0,x1],[y,y],pen=_mk(clr,1.4,Qt.DashLine if dash else Qt.SolidLine,200))
            lb=pg.TextItem(txt,color=clr,anchor=(0,0.5)); lb.setFont(QFont('Consolas',8,QFont.Bold)); lb.setPos(n+1,y)
            self._pp.addItem(lb); items.extend([ln,lb])
        ztop = float(zone.get('top', 0) or 0)
        zbot = float(zone.get('bottom', 0) or 0)
        if ztop and zbot:
            zr=pg.LinearRegionItem([min(ztop,zbot), max(ztop,zbot)], orientation='horizontal', movable=False,
                                   brush=_br(C['s'] if direction=='CALL' else C['r'], 35),
                                   pen=_mk(C['s'] if direction=='CALL' else C['r'], 1.0, alpha=120))
            self._pp.addItem(zr); items.append(zr)
            zlbl=pg.TextItem(f"ZONE {min(ztop,zbot):.2f} → {max(ztop,zbot):.2f}", color=C['txt'], anchor=(0,1))
            zlbl.setFont(QFont('Consolas',7,QFont.Bold)); zlbl.setPos(x0+1, max(ztop,zbot)); self._pp.addItem(zlbl); items.append(zlbl)
        _hline(entry, C['entry'], f'ENTRY {entry:.2f}')
        _hline(stop, C['sl'], f'STOP {stop:.2f}', True)
        _hline(target, C['tp'], f'TARGET {target:.2f}', True)
        _why_text = '\n'.join(list(why)[:5])
        info = pg.TextItem(f"{direction} | Score {score}\n{_why_text}", color=C['txt'], anchor=(0,0))
        info.setFont(QFont('Consolas',8))
        info.setPos(x0+1, max(entry, stop, target, ztop or entry) + max(abs(target-stop)*0.08, entry*0.002, 0.05))
        self._pp.addItem(info); items.append(info)
        self._plan_items = items


    def set_trade_plan(self, plan:dict):
        if QThread.currentThread() != self.thread():
            self.sig_set_trade_plan_ui.emit(dict(plan or {}))
            return
        self._set_trade_plan_ui(plan)

    @pyqtSlot(object)
    def _set_trade_plan_ui(self, plan:dict):
        self.draw_analysis_plan(plan)

    # ── Toolbar handlers ────────────────────────────────────────────────────────
    def _on_tf(self,tf): self._tf_seconds=TF_SECONDS.get(tf,900); self._toolbar.set_tf(tf)
    def _on_ind(self,k,chk):
        self._show[k]=chk
        if self._last_bars: self.set_data(self._last_bars)
    def _on_panel(self,k,chk):
        self._panels[k]=chk; pw=self._rp if k=='rsi' else self._mp; pw.setVisible(chk)
        sizes=self._splitter.sizes(); idx=2 if k=='rsi' else 3; sizes[idx]=90 if chk else 0
        self._splitter.setSizes(sizes)
        if self._last_bars: self.set_data(self._last_bars)
    def _on_tool(self,tool): self.set_draw_mode(tool)

    # ── API aliases ─────────────────────────────────────────────────────────────
    def set_timeframe(self,tf): self._on_tf(tf)
    def update_from_tf_data(self,b,lp=None):
        self.set_data(b)
        if lp and lp>0: self.push_price(lp)
    def update_from_trade(self,t): self.draw_trade(t)
    def reset_view(self): self._zoom_bars=80; self._zoom_locked=False; self._set_range()
    def goto_end(self):
        if self._n: self._pp.setXRange(self._n-self._zoom_bars-1,self._n+RIGHT_MARGIN,padding=0)

class TradingApp(QMainWindow):
    chart_data_ready      = pyqtSignal(object)
    signal_close_trade_ui = pyqtSignal(dict)   # إغلاق من UI monitor
    signal_new_trade_ui   = pyqtSignal(dict)   # ✅ صفقة من IBKR sync
    signal_datafeed_stopped = pyqtSignal(str)  # DataFeed توقف — للعرض في main thread
    signal_datafeed_exited  = pyqtSignal()     # DataFeed انتهى — تحديث UI في main thread


    def _build_chart_plan_from_score(self, score_data: dict, symbol: str = '') -> dict | None:
        if not score_data:
            return None
        plan = dict(score_data.get('analysis_plan') or {})
        if not plan:
            entry = float(score_data.get('entry_price') or score_data.get('price') or 0)
            stop  = float(score_data.get('stop_price') or 0)
            target= float(score_data.get('target_price') or 0)
            if not (entry and stop and target):
                return None
            plan = {
                'direction': score_data.get('direction', ''),
                'entry_price': entry,
                'stop_price': stop,
                'target_price': target,
                'zone': score_data.get('zone') or score_data.get('zr_zone') or {},
                'score': score_data.get('score', 0),
                'why': score_data.get('signals') or score_data.get('why', ''),
            }
        plan['symbol'] = symbol or score_data.get('symbol', '')
        return plan


    def _sync_trade_plan_to_chart(self, score_data: dict | None = None, symbol: str = '', executed: bool = False):
        plan = self._build_chart_plan_from_score(score_data or getattr(self, '_last_analysis_score_data', None), symbol)
        if not plan:
            return
        if executed:
            plan = {**plan, 'why': list(plan.get('why', [])) + ['EXECUTION_READY✅'] if isinstance(plan.get('why', []), list) else plan.get('why', '')}
        self._last_analysis_plan = plan
        if getattr(self, '_pro_chart', None) and hasattr(self._pro_chart, 'set_trade_plan'):
            try:
                self._pro_chart.set_trade_plan(plan)
            except Exception:
                pass
        _wc = getattr(self, '_chart_win_chart', None)
        if _wc and hasattr(_wc, 'set_trade_plan'):
            try:
                _wc.set_trade_plan(plan)
            except Exception:
                pass

    def __init__(self):
        super().__init__()
        # تهيئة IB event loop أولاً
        loop = get_ib_loop()
        # إنشاء IB داخل الـ loop الخاص به
        async def _create_ib():
            return IB()
        future = asyncio.run_coroutine_threadsafe(_create_ib(), loop)
        self.ib = future.result(timeout=10)
        self._ib_loop = loop
        self.connected       = False
        self.current_symbol  = "SPY"
        self.auto_bot        = None
        self._ui_indicators  = {}   # مؤشرات الواجهة — يقرأ منها البوت
        self.account         = None
        self.current_price   = 0.0   # 0 = لم يُجلب بعد — يمنع طلب strikes خاطئة
        self.account_balance = 0.0
        self._client_id      = int(datetime.now().strftime("%H%M%S")) % 9000 + 1000
        self.strategy        = SmartStrategyPro()
        if ANALYZER_MODE == "BC":
            from smart_analyzer_bridge_bc import SYMBOLS as _bc_init_syms
            self.auto_bot_scan_symbols = list(_bc_init_syms)
        else:
            self.auto_bot_scan_symbols = list(X1_SCAN_SYMBOLS)

        self.risk_manager    = RiskManager(max_open_trades=3)
        # ✅ تأكيد القيم الافتراضية الآمنة
        self.risk_manager.loss_pct       = 0.10   # 10% خسارة يومية
        self.risk_manager.cost_pct       = 0.50   # 50% حجم الصفقة
        self.risk_manager.max_daily_trades = 20    # حد يومي
        # max_daily_loss و max_option_cost تُحسب تلقائياً من الرصيد في update_from_balance()
        self.position_manager = PositionManager()

        self.ui_updater = UIUpdater()
        self.ui_updater.update_price.connect(self._on_price)
        # self.ui_updater.update_chart — لم يعد مستخدماً (pro_chart.py يتولى الشارت)
        self.ui_updater.update_rsi.connect(self._on_rsi)
        self.ui_updater.update_options.connect(self._on_options)
        self.ui_updater.update_cash.connect(self._on_cash)
        self.ui_updater.update_expiries.connect(self._on_expiries)
        self.ui_updater.update_analysis.connect(self._on_analysis)
        self.ui_updater.show_status.connect(self.statusBar().showMessage)
        self.ui_updater.update_trade.connect(self._on_update_trade)
        self.ui_updater.update_indicators.connect(self._on_live_indicators)
        self.ui_updater.update_clock.connect(self._on_clock)

        self.initUI()
        # ربط signal الشارت
        self.chart_data_ready.connect(self._apply_chart_data)
        self.signal_close_trade_ui.connect(self._on_close_trade)
        self.signal_new_trade_ui.connect(self._on_new_trade)
        self.signal_datafeed_stopped.connect(self._on_datafeed_stopped)
        self.signal_datafeed_exited.connect(self._on_datafeed_exited)

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._refresh_display)
        self.refresh_timer.start(30000)   # رصيد من IBKR كل 30 ثانية

        # إحصائيات محلية (PnL, صفقات) — تتحدث كل 2 ثانية بدون IBKR
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self._refresh_local_stats)
        self.stats_timer.start(2000)

        self.price_timer = QTimer()
        self.price_timer.timeout.connect(self._refresh_price_only)
        self.price_timer.start(1000)      # سعر حي كل ثانية

        self.daily_reset_timer = QTimer()
        self.daily_reset_timer.timeout.connect(self._daily_reset)
        self.daily_reset_timer.start(60000)

        # تحديث تلقائي لجدول الأوبشن كل 10 ثواني
        self.options_refresh_timer = QTimer()
        self.options_refresh_timer.timeout.connect(self._auto_refresh_options)
        self.options_refresh_timer.start(15000)  # 15s — أقل من وقت التنفيذ

        # تحديث تلقائي للصفقات المفتوحة كل 5 ثواني (سعر + PnL)
        self.trades_refresh_timer = QTimer()
        self.trades_refresh_timer.timeout.connect(self._auto_refresh_open_trades)
        self.trades_refresh_timer.start(5000)   # 5s — يمنع تراكم threads

        # ── Streaming Price Timer (2 ثانية) ──────────
        self._streaming_tickers = {}   # {symbol: ticker}
        self._opt_streaming     = {}   # {trade_id: ticker}
        self._options_cache_dict = {}  # {symbol: [rows]} — multi-symbol options cache للبوت
        self.price_stream_timer = QTimer()
        self.price_stream_timer.timeout.connect(self._read_streaming_prices)
        self.price_stream_timer.start(1000)
        self._last_reset_day = datetime.now().date()

        self._set_fallback_dates()
        # لا نستدعي update_options_table هنا — سيتم بعد الاتصال

        # ── تحديث الشارت كل 5 ثوان (السعر فوري، الرسم كل 5 دقائق) ──
        self.chart_refresh_timer = QTimer()
        self.chart_refresh_timer.timeout.connect(self._auto_refresh_chart)
        self.chart_refresh_timer.start(1000)   # كل ثانية — السعر حي

        # ── تحميل الشارت عند الفتح (ملفات محفوظة أو بعد الاتصال) ──
        QTimer.singleShot(500, lambda: threading.Thread(
            target=self._fetch_chart,
            args=(self.current_symbol, '15m'),
            daemon=True).start())

        # ── مراقبة SL/TP مستقلة — تبدأ بعد ثانية من فتح النافذة ─────────
        self._ui_sl_timer = QTimer()
        self._ui_sl_timer.timeout.connect(self._ui_check_sl_tp)
        QTimer.singleShot(1000, lambda: self._ui_sl_timer.start(5000))

        # ── [PROFILING] UI freeze detector + log flood counters ───────────
        self._prof_last_hb     = time.perf_counter()
        self._prof_last_action = 'init'
        self._prof_log_cnt     = 0;  self._prof_log_sec    = int(time.time())
        self._prof_insert_cnt  = 0;  self._prof_insert_sec = int(time.time())
        self._prof_hb_timer    = QTimer()
        self._prof_hb_timer.timeout.connect(self._prof_heartbeat)
        self._prof_hb_timer.start(500)

    def _prof_heartbeat(self):
        """[PROFILING] Fires every 500 ms. Logs if UI thread was blocked >1500 ms."""
        now = time.perf_counter()
        elapsed_ms = (now - self._prof_last_hb) * 1000
        if elapsed_ms > 1500:
            print(f'[UI FREEZE] blocked {elapsed_ms:.0f}ms  last_action={self._prof_last_action}', flush=True)
        self._prof_last_hb = now

    def _ui_check_sl_tp(self):
        """مراقبة SL/TP من الواجهة — تعمل دائماً بغض النظر عن البوت"""
        if not self.connected: return
        positions = self.position_manager.get_all()
        if not positions: return
        # لا تتعارض مع AutoTradingBot إذا كان شغالاً
        if hasattr(self, 'auto_bot') and self.auto_bot and self.auto_bot.isRunning():
            return
        def _run():
            for pos in list(positions):
                try:
                    opt_c = pos.get('opt_contract')
                    if not opt_c: continue
                    # جلب السعر الحالي
                    current = None
                    try:
                        tk = req_mkt_data_safe(self.ib, opt_c, '')[0]
                        import time as _t
                        for _ in range(15):
                            _t.sleep(0.1); ib_pump(0.05)
                            _b = getattr(tk,'bid',0) or 0
                            _a = getattr(tk,'ask',0) or 0
                            _l = getattr(tk,'last',0) or 0
                            if _b>0 and _a>0 and _a<=_b*4:
                                current = round((_b+_a)/2, 2); break
                            if _l>0: current = float(_l)
                        try: run_in_ib_thread(self.ib.cancelMktData, opt_c)
                        except Exception: pass
                    except Exception: pass
                    if not current or current <= 0: continue
                    # تحديث الجدول — اقرأ SL/phase من position_manager مباشرة
                    _pm_pos = self.position_manager.get(pos.get('id'))
                    _src    = _pm_pos if _pm_pos else pos
                    entry = _src.get('entry_premium', 0)
                    if entry > 0:
                        _contracts_src = _src.get('contracts', 1)
                        pnl_usd = round((current - entry) * _contracts_src * 100, 2)
                        pnl_pct = (current - entry) / entry * 100
                        _phase_src = _src.get('tp_phase', 0)
                        _tp1_src   = _src.get('take_profit',
                            round(entry * (1.20 if _src.get('strategy_type','Stock')=='Index' else 1.25), 2))
                        self.ui_updater.update_trade.emit({
                            'id':        pos.get('id'),
                            'current':   current,
                            'pnl_pct':   pnl_pct,
                            'pnl_usd':   pnl_usd,
                            'stop_loss': _src.get('stop_loss', 0),   # ✅ من المصدر الحي
                            'tp1':       _tp1_src,
                            'tp2':       _src.get('take_profit_2', round(_tp1_src * 1.20, 2)),
                            'phase_lbl': ['', 'TP1↑', 'TP2↑', 'Trail🔄'][min(_phase_src, 3)],
                            'contracts': _contracts_src,
                            'pnl_abs':   abs(pnl_usd),
                            'pnl_sign':  '+' if pnl_usd >= 0 else '-',
                        })
                    # فحص SL/TP
                    # فحص SL/TP
                    reason = self.position_manager.check_exits(pos['id'], current)
                    if reason:
                        # اقرأ PnL من المصدر الحي بعد تحديث check_exits
                        _pm2 = self.position_manager.get(pos['id']) or pos
                        pnl  = round((current - _pm2.get('entry_premium', 0)) *
                                     _pm2.get('contracts', 1) * 100, 2)
                        print(f"[UI-SL] {pos.get('symbol')} {reason} @ ${current:.2f} SL=${_pm2.get('stop_loss',0):.2f}")

                        # ── أرسل SL المحدَّث للـ UI فوراً قبل الإغلاق ──────
                        _new_sl2   = _pm2.get('stop_loss', 0)
                        _new_ph2   = _pm2.get('tp_phase', 0)
                        _new_tp1_2 = _pm2.get('take_profit', 0)
                        _new_tp2_2 = _pm2.get('take_profit_2', round(_new_tp1_2 * 1.20, 2))
                        if _new_sl2 > 0:
                            self.ui_updater.update_trade.emit({
                                'id':        pos.get('id'),
                                'current':   current,
                                'pnl_usd':   pnl,
                                'pnl_pct':   (pnl / (_pm2.get('entry_premium',1) * _pm2.get('contracts',1) * 100) * 100),
                                'stop_loss': _new_sl2,
                                'tp1':       _new_tp1_2,
                                'tp2':       _new_tp2_2,
                                'phase_lbl': ['', 'TP1↑', 'TP2↑', 'Trail🔄'][min(_new_ph2, 3)],
                                'contracts': _pm2.get('contracts', 1),
                                'pnl_abs':   abs(pnl),
                                'pnl_sign':  '+' if pnl >= 0 else '-',
                            })

                        # تنفيذ أمر البيع
                        if self.auto_exec_cb.isChecked() and opt_c:
                            try:
                                # ✅ qualify قبل البيع — ضروري لـ XSP و SPX
                                try:
                                    _q = run_in_ib_thread(self.ib.qualifyContracts, opt_c)
                                    if _q: opt_c = _q[0]
                                except Exception: pass
                                _ord = _make_order('SELL', pos.get('contracts',1))
                                run_in_ib_thread(self.ib.placeOrder, opt_c, _ord)
                            except Exception as _se:
                                print(f"[UI-SL] Sell error: {_se}")
                        # أغلق في الواجهة
                        self.risk_manager.close(pnl, symbol=pos.get('symbol'))
                        self.position_manager.remove(pos['id'])
                        self.signal_close_trade_ui.emit({
                            **pos, 'exit_price': current,
                            'exit_reason': reason, 'pnl': pnl,
                            'contracts': pos.get('contracts',1),
                            'exit_time': datetime.now().strftime("%H:%M:%S"),
                        })
                except Exception as _e:
                    print(f"[UI-SL] error: {_e}")
        import threading as _th
        _th.Thread(target=_run, daemon=True).start()

    # -----------------------------------------------
    def _auto_refresh_chart(self):
        """تحديث الشارت — السعر كل ثانية، بيانات كاملة كل 30 ثانية"""
        # [PROFILING]
        self._prof_last_action = '_auto_refresh_chart'
        _prof_arc_t0 = time.perf_counter()
        if not self.connected or not self._pro_chart:
            return
        sym = getattr(self, 'current_symbol', None)
        if not sym:
            return
        # ── تحديث السعر الحي فوراً (كل مرة) ──────────────────────────────
        live = getattr(self, 'current_price', None)
        if live and live > 0:
            _prof_pp_t0 = time.perf_counter()
            self._pro_chart.push_price(live)
            _prof_pp_ms = (time.perf_counter() - _prof_pp_t0) * 1000
            if _prof_pp_ms > 50:
                print(f'[PROF SLOW] push_price: {_prof_pp_ms:.1f}ms', flush=True)
        # ── تحديث بيانات كاملة كل 30 ثانية ──────────────────────────────
        _now  = datetime.now()
        _last = getattr(self, '_last_full_chart_refresh', None)
        if not _last or (_now - _last).total_seconds() > 30:
            self._last_full_chart_refresh = _now
            threading.Thread(
                target=self._fetch_chart, args=(sym,), daemon=True).start()
        # [PROFILING] log if _auto_refresh_chart itself is slow
        _prof_arc_ms = (time.perf_counter() - _prof_arc_t0) * 1000
        if _prof_arc_ms > 50:
            print(f'[PROF SLOW] _auto_refresh_chart: {_prof_arc_ms:.1f}ms', flush=True)

    def _set_fallback_dates(self):
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        today = datetime.now().date()
        d, n = today - timedelta(days=1), 0
        while n < 10:
            d += timedelta(days=1)
            # Wed=2 و Fri=4 هي أيام انتهاء صلاحية الـ options الشائعة
            # >= today يشمل اليوم نفسه إذا كان Fri أو Wed
            if d.weekday() in (2, 4) and d >= today:
                raw = d.strftime("%Y%m%d")
                self.expiry_combo.addItem(d.strftime("%Y-%m-%d (%a)"), userData=raw)
                n += 1
        self.expiry_combo.blockSignals(False)

    def _auto_refresh_options(self):
        """تحديث تلقائي لجدول الأوبشن إذا كنا متصلين"""
        if self.connected and not getattr(self, '_any_training_running', False):
            self.update_options_table()

    def fetch_options_for_bot(self, symbol: str, expiry: str = "") -> None:
        """
        يجلب بيانات الأوبشن لأي رمز في الخلفية ويخزنها في _options_cache_dict.
        يستخدمه البوت لرموز المسح غير المعروضة في الواجهة.
        """
        if not self.connected:
            return
        symbol = symbol.upper().strip()
        # لا نجلب إذا البيانات حديثة (أقل من 5 دقائق)
        cached = self._options_cache_dict.get(symbol)
        age_key = f"_options_cache_time_{symbol}"
        last_fetch = getattr(self, age_key, 0)
        if cached and (time.time() - last_fetch) < 300:
            return  # حديثة — لا داعي للتحديث

        def _run(sym=symbol, exp=expiry):
            try:
                import time as _t
                # جلب السعر الحالي — بدون qualifyContracts لتجنب timeout
                c_tmp = _make_contract(sym)
                run_in_ib_thread(self.ib.reqMarketDataType, 4)
                tk_tmp = req_mkt_data_safe(self.ib, c_tmp, '')[0]
                prc = 0.0
                for _ in range(15):
                    ib_pump(0.1)
                    v = run_in_ib_thread(lambda t=tk_tmp: t.last or t.close or ((t.bid+t.ask)/2 if t.bid and t.ask else None))
                    if v and _valid(v):
                        prc = float(v); break
                try:
                    run_in_ib_thread(self.ib.cancelMktData, c_tmp)
                except Exception:
                    pass
                if not prc or prc <= 0:
                    return
                run_in_ib_thread(self.ib.reqMarketDataType, 1)

                # تحديد expiry — 0DTE أو 1DTE فقط
                from datetime import timedelta as _td, date as _date
                import pytz as _pytz_opt
                _est_opt = _pytz_opt.timezone('US/Eastern')
                _now_opt = datetime.now(_est_opt)

                # ⛔ حظر فتح صفقات من 3:00 PM ET فصاعداً (خطر 0DTE)
                if _now_opt.hour >= 15:
                    self._on_bot_scan_signal(
                        f"  ⛔ {sym}: حظر 0DTE — الوقت بعد 3:00 PM ET ({_now_opt.strftime('%H:%M')})"
                    )
                    return

                _today = _now_opt.date()
                exp = ""
                # 0DTE: نفس اليوم إذا السوق مفتوح وقبل 3PM
                if _today.weekday() < 5:  # أيام عمل
                    exp = _today.strftime("%Y%m%d")
                # إذا اليوم ليس يوم عمل أو 0DTE غير متاح → غداً أو بعد غد
                if not exp:
                    for _off in range(1, 4):
                        _nd = _today + _td(days=_off)
                        if _nd.weekday() < 5:
                            exp = _nd.strftime("%Y%m%d")
                            break

                # حساب strikes
                if prc > 500:   step = 5
                elif prc > 100: step = 2.5
                else:           step = 1
                base = round(prc / step) * step
                strikes = [base + i * step for i in range(-4, 5)]
                exch = _option_exchange(sym)
                rows = []
                for strike in strikes:
                    row = {"strike": strike}
                    for right, bid_k, ask_k in [("C", "call_bid", "call_ask"), ("P", "put_bid", "put_ask")]:
                        try:
                            opt = Option(sym, exp, float(strike), right, exch)
                            tk = run_in_ib_thread(self.ib.reqMktData, opt, "", False, False)
                            if not tk:
                                continue
                            bid = ask = None
                            for _ in range(8):
                                ib_pump(0.15)
                                b = getattr(tk, 'bid', None)
                                a = getattr(tk, 'ask', None)
                                if _valid(b) and _valid(a):
                                    bid, ask = float(b), float(a); break
                            try:
                                run_in_ib_thread(self.ib.cancelMktData, opt)
                            except Exception:
                                pass
                            if bid and ask:
                                row[bid_k] = f"{bid:.2f}"
                                row[ask_k] = f"{ask:.2f}"
                        except Exception:
                            pass
                    if row.get("call_bid") or row.get("put_bid"):
                        rows.append(row)
                if rows:
                    # احسب DTE من الـexpiry المستخدم
                    _dte_stored = 28
                    try:
                        from datetime import date as _d2
                        _ed = _d2(int(exp[:4]), int(exp[4:6]), int(exp[6:8]))
                        _dte_stored = max(0, (_ed - _d2.today()).days)
                    except Exception:
                        pass
                    # خزّن rows + dte معاً
                    self._options_cache_dict[sym] = {"rows": rows, "dte": _dte_stored, "expiry": exp}
                    setattr(self, age_key, time.time())
                    print(f"[OptionsBot] {sym}: {len(rows)} strike | DTE={_dte_stored} | exp={exp}")
            except Exception as e:
                print(f"[OptionsBot] ❌ {sym}: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _auto_refresh_open_trades(self):
        """يُشغّل streaming للصفقات المفتوحة إذا لم تكن تعمل بعد"""
        if not self.connected: return
        threading.Thread(target=self._start_opt_streaming, daemon=True).start()

    def _start_opt_streaming(self):
        """ابدأ streaming لكل صفقة مفتوحة لم تُسجَّل بعد"""
        if not self.connected: return
        try:
            positions = self.position_manager.get_all()
            if not hasattr(self, '_closing_trades'):
                self._closing_trades = set()
            for pos in positions:
                tid = pos.get('id')
                if tid in self._opt_streaming: continue  # يعمل بالفعل
                if tid in self._closing_trades: continue  # جاري إغلاقها
                opt_c = pos.get('opt_contract')
                if not opt_c: continue
                try:
                    tk = req_mkt_data_safe(self.ib, opt_c, '')[0]
                    self._opt_streaming[tid] = (tk, pos)
                except Exception: pass
        except Exception as e:
            print(f'[Stream] opt start error: {e}')

    def _start_symbol_streaming(self, symbol):
        """ابدأ streaming مباشر لرمز معين"""
        if not self.connected: return
        if symbol in self._streaming_tickers: return
        def _run():
            try:
                c = _make_contract(symbol)
                try: run_in_ib_thread(self.ib.qualifyContracts, c)
                except Exception: pass
                # generic_ticks فارغ = bid/ask/last فقط = لا يحتاج اشتراك خاص
                tk = req_mkt_data_safe(self.ib, c, '')[0]
                self._streaming_tickers[symbol] = tk
            except Exception as e:
                print(f'[Stream] {symbol} error: {e}')
        threading.Thread(target=_run, daemon=True).start()

    def _stop_symbol_streaming(self, symbol):
        """أوقف streaming رمز معين"""
        tk = self._streaming_tickers.pop(symbol, None)
        if tk and self.connected:
            try:
                c = _make_contract(symbol)
                # تحقق أن الطلب مسجّل فعلاً قبل الإلغاء
                _req_map = getattr(self.ib, '_reqId2Contract', {}) or {}
                _ticker_map = getattr(self.ib.client, '_reqId2Ticker', {}) or {}
                _has_req = any(True for _ in [_req_map, _ticker_map]
                               if isinstance(_, dict) and len(_) > 0)
                if _has_req or tk:
                    run_in_ib_thread(self.ib.cancelMktData, c)
            except Exception: pass

    def _read_streaming_prices(self):
        """يُقرأ كل 2 ثانية من الـ streaming — يعمل في Pre-Market وبعد الإغلاق"""
        if not self.connected: return

        # ── أسعار الرموز (الشارت) + مؤشرات لحظية ────────
        sym = getattr(self, 'current_symbol', None)
        if sym and sym in self._streaming_tickers:
            tk = self._streaming_tickers[sym]
            try:
                vals = run_in_ib_thread(
                    lambda t=tk: (t.last, t.bid, t.ask, t.close,
                                  getattr(t,'halted',None)))
                last, bid, ask, close, halted = vals
                price = None
                # ترتيب الأولوية: Last → Mid(bid+ask) → Close
                if _valid(last) and last > 0:
                    price = last
                elif _valid(bid) and _valid(ask) and bid > 0 and ask > 0:
                    price = round((bid + ask) / 2, 4)
                elif _valid(bid) and bid > 0:
                    price = bid
                elif _valid(ask) and ask > 0:
                    price = ask
                elif _valid(close) and close > 0:
                    price = close
                if price and price > 0:
                    self.current_price = float(price)
                    self.ui_updater.update_price.emit(float(price))

                    # ── تحديث المؤشرات لحظياً ──────────────
                    _closes = getattr(self, '_last_closes', [])
                    if _closes:
                        _closes[-1] = price   # آخر سعر حقيقي
                        _highs  = getattr(self, '_last_highs', _closes)
                        _lows   = getattr(self, '_last_lows',  _closes)
                        _vols   = getattr(self, '_last_volumes', [10000]*len(_closes))
                        _rsi  = calc_rsi(_closes)
                        _adx  = calc_adx(_highs, _lows, _closes, 14)
                        _e9   = calc_ema(_closes, 9)
                        _e21  = calc_ema(_closes, 21)
                        try:
                            _strat = getattr(self, 'strategy', None) or _strategy_module
                            _bias = _strat.get_market_bias(
                                _closes, _highs, _lows, _vols)
                            _reg  = _strat.detect_regime(
                                _closes, _highs, _lows, _vols)
                        except Exception:
                            _bias, _reg = 'neutral', 'normal'
                        self.ui_updater.update_indicators.emit({
                            'rsi': _rsi, 'adx': _adx,
                            'ema9': _e9, 'ema21': _e21,
                            'bias': _bias, 'regime': _reg,
                        })

                    # ── ساعة السوق ──────────────────────────
                    try:
                        import pytz
                        from datetime import datetime as _dtt
                        _now = _dtt.now(pytz.timezone('US/Eastern'))
                    except Exception:
                        from datetime import datetime as _dtt, timezone, timedelta
                        _utc = _dtt.now(timezone.utc)
                        _off = -4 if 3 <= _utc.month <= 11 else -5
                        _now = _utc.astimezone(timezone(timedelta(hours=_off)))
                    self.ui_updater.update_clock.emit(
                        _now.strftime('%H:%M:%S EST'))
            except Exception: pass

        # ── أسعار الصفقات المفتوحة ──────────────────────
        try:
            dead = []
            for tid, (tk, pos) in list(self._opt_streaming.items()):
                # تحقق أن الصفقة لا تزال مفتوحة
                if not self.position_manager.get(tid):
                    dead.append(tid); continue
                try:
                    vals = run_in_ib_thread(
                        lambda t=tk: ((getattr(t, 'last', None), getattr(t, 'bid', None), getattr(t, 'ask', None), getattr(t, 'rtTime', None)) if t is not None else (None, None, None, None)))
                    last, bid, ask, rt_time = vals
                    price = None
                    # ✅ أولوية: Bid+Ask معاً مع فحص spread معقول
                    if _valid(bid) and _valid(ask) and ask <= bid * 3.0:
                        price = round((bid+ask)/2, 3)
                    elif _valid(last):
                        price = last
                    if not price or price <= 0: continue

                    entry = pos.get('entry_premium', 0)

                    # ✅ فلتر أمان — يرفض الارتفاعات الوهمية فقط
                    # الانخفاضات تُقبل دائماً (SL يجب أن يعمل)
                    if entry and entry > 0:
                        _chg_pct = (price - entry) / entry * 100

                        # رفض فقط إذا ارتفع > 150% (وهمي صعوداً)
                        if _chg_pct > 150:
                            continue

                        # رفض في أول 15 دقيقة إذا ارتفع > 60% (Delayed صعودي)
                        if _chg_pct > 60:
                            from datetime import datetime as _dt5
                            _edt5 = pos.get('entry_datetime', '')
                            _ela5 = 9999
                            if _edt5:
                                try:
                                    _ela5 = (_dt5.now() - _dt5.strptime(
                                        _edt5, '%Y-%m-%d %H:%M:%S')).total_seconds() / 60
                                except Exception: pass
                            if _ela5 < 15:
                                continue
                        # ❗ الانخفاضات لا تُرفض أبداً — SL يجب أن يعمل

                        # تحقق Bid/Ask منطقي مقارنة بالسعر — spread لا يتجاوز 50%
                        if _valid(bid) and _valid(ask):
                            _spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
                            if _spread_pct > 50:
                                # spread واسع جداً = بيانات غير موثوقة
                                if _valid(last) and last > 0:
                                    price = last  # استخدم Last بدل Mid
                                else:
                                    continue

                    # ── اقرأ دائماً من position_manager مباشرة ──────────
                    # pos من الـ iterator قد يكون قديماً — _pos_live هو المصدر الحقيقي
                    # هذا هو الإصلاح الجذري لعدم ظهور وقف الخسارة المتحرك في الـ UI
                    _pos_live = self.position_manager.get(tid)
                    if not _pos_live:
                        continue

                    contracts  = _pos_live.get('contracts', 1)
                    pnl_usd    = round((price - entry) * contracts * 100, 2)
                    pnl_pct    = round((price - entry) / entry * 100, 1) if entry else 0
                    _strat_fb2 = _pos_live.get('strategy_type', 'Stock')
                    # ✅ sl/phase/tp يُقرآن من _pos_live (محدَّث بـ check_exits) لا من pos القديم
                    sl        = _pos_live.get('stop_loss',
                                    round(entry * (0.75 if _strat_fb2=='Index' else 0.65), 2))
                    tp1       = _pos_live.get('take_profit',
                                    round(entry * (1.20 if _strat_fb2=='Index' else 1.25), 2))
                    tp2       = _pos_live.get('take_profit_2', round(tp1 * 1.20, 2))
                    phase     = _pos_live.get('tp_phase', 0)
                    phase_lbl = ['', 'TP1↑', 'TP2↑', 'Trail🔄'][min(phase, 3)]

                    self.ui_updater.update_trade.emit({
                        'id':        tid,
                        'current':   price,
                        'pnl_usd':   pnl_usd,
                        'pnl_pct':   pnl_pct,
                        'stop_loss': sl,
                        'tp1':       tp1,
                        'tp2':       tp2,
                        'phase_lbl': phase_lbl,
                        'contracts': contracts,
                        'pnl_sign':  '+' if pnl_usd >= 0 else '-',
                        'pnl_abs':   abs(pnl_usd),
                    })

                    # ── فحص SL و TP من الـ streaming ──────────────
                    _sl_live   = _pos_live.get('stop_loss', 0)
                    _entry_live= _pos_live.get('entry_premium', entry)
                    _strat_live_tp = _pos_live.get('strategy_type','Stock')
                    _tp1_live  = _pos_live.get('take_profit',
                        round(_entry_live * (1.20 if _strat_live_tp=='Index' else 1.25), 2))
                    _tp2_live  = _pos_live.get('take_profit_2',
                        round(_tp1_live * 1.20, 2))
                    _phase_live= _pos_live.get('tp_phase', 0)

                    # إذا لم يُحسب SL بعد — احسبه الآن
                    if _sl_live <= 0 and _entry_live > 0:
                        _strat_live = _pos_live.get('strategy_type', 'Stock')
                        _sl_pct_live = 0.75 if _strat_live == 'Index' else 0.65
                        _sl_live = round(_entry_live * _sl_pct_live, 2)
                        _pos_live['stop_loss'] = _sl_live

                    _need_check = (
                        (_sl_live > 0 and price <= _sl_live) or      # SL
                        (_phase_live == 0 and price >= _tp1_live) or  # TP1
                        (_phase_live == 1 and price >= _tp2_live)     # TP2
                    )
                    if _need_check:
                        print(f"[EXIT-CHK] {pos.get('symbol','?')} price={price:.2f} "
                              f"sl={_sl_live:.2f} tp1={_tp1_live:.2f} phase={_phase_live}")
                        # للـ SL: لا ننتظر check_exits — ننفذ مباشرة
                        _is_sl = _sl_live > 0 and price <= _sl_live
                        _exit = self.position_manager.check_exits(tid, price)
                        # إذا check_exits منعه بسبب Grace Period لكنه SL حقيقي → نفذ
                        if not _exit and _is_sl:
                            _exit = 'stop_loss'
                            print(f"[SL-FORCE] {pos.get('symbol','?')} forcing SL @ ${price:.2f}")
                        if _exit and tid not in getattr(self, '_sl_executing', set()):
                            if not hasattr(self, '_sl_executing'):
                                self._sl_executing = set()
                            self._sl_executing.add(tid)
                            _is_tp = _exit.startswith('tp')
                            _icon  = '🎯' if _is_tp else '🛑'
                            print(f"[EXIT-STREAM] {pos.get('symbol','?')} {_exit} @ ${price:.2f} PnL=${pnl_usd:+.0f}")
                            import threading as _thr
                            def _stream_close(p=pos, pr=price, ex=_exit, pu=pnl_usd, _tid=tid):
                                try:
                                    opt_c  = p.get('opt_contract')
                                    long_c = p.get('long_contract')
                                    _is_sp = p.get('is_spread', False)
                                    if ex in ('tp1', 'tp2'):
                                        qty = max(1, p.get('contracts', 1) // 2)
                                    else:
                                        qty = p.get('contracts', 1)

                                    _actual_price = pr
                                    if opt_c and qty > 0 and self.connected:
                                        # ✅ إصلاح: انتظر Fill مؤكد + أغلق كلا الـ leg للـ Spread
                                        if _is_sp and long_c is not None:
                                            _buy_ord  = _make_order('BUY',  qty)
                                            _sell_ord = _make_order('SELL', qty)
                                            _buy_tr   = run_in_ib_thread(self.ib.placeOrder, opt_c,  _buy_ord)
                                            _sell_tr  = run_in_ib_thread(self.ib.placeOrder, long_c, _sell_ord)
                                            _b_done = _s_done = False
                                            for _ww in range(40):
                                                time.sleep(0.5); ib_pump(0.05)
                                                try:
                                                    _bs2 = run_in_ib_thread(lambda t=_buy_tr:  t.orderStatus.status)
                                                    _ss2 = run_in_ib_thread(lambda t=_sell_tr: t.orderStatus.status)
                                                    _bp2 = run_in_ib_thread(lambda t=_buy_tr:  t.orderStatus.avgFillPrice)
                                                    if _bs2 == 'Filled': _b_done = True
                                                    if _ss2 == 'Filled': _s_done = True
                                                    if _bp2 and _bp2 > 0: _actual_price = float(_bp2)
                                                    if _b_done and _s_done: break
                                                except Exception: pass
                                        else:
                                            _ord = _make_order('SELL', qty)
                                            _close_tr = run_in_ib_thread(self.ib.placeOrder, opt_c, _ord)
                                            for _ww in range(40):
                                                time.sleep(0.5); ib_pump(0.05)
                                                try:
                                                    _cs2 = run_in_ib_thread(lambda t=_close_tr: t.orderStatus.status)
                                                    _cp2 = run_in_ib_thread(lambda t=_close_tr: t.orderStatus.avgFillPrice)
                                                    if _cs2 == 'Filled':
                                                        if _cp2 and _cp2 > 0: _actual_price = float(_cp2)
                                                        break
                                                    elif _cs2 in ('Cancelled','Inactive'): break
                                                except Exception: pass

                                    entry_p = p.get('entry_premium', 0)
                                    _pnl = round((_actual_price - entry_p) * qty * 100, 2)
                                    if ex in ('tp1', 'tp2') and p.get('contracts', 1) - qty > 0:
                                        self.risk_manager.add_pnl(_pnl)
                                        _pos_ref = self.position_manager.get(_tid)
                                        if _pos_ref:
                                            _pos_ref['contracts'] -= qty
                                    else:
                                        self.position_manager.remove(_tid)
                                        self.risk_manager.close(_pnl, symbol=p.get('symbol',''))
                                    self.signal_close_trade_ui.emit({
                                        **p,
                                        'exit_price':  _actual_price,
                                        'exit_reason': ex,
                                        'pnl':         _pnl,
                                        'contracts':   qty,
                                        'exit_time':   datetime.now().strftime('%H:%M:%S')
                                    })
                                    self.signal_scan_update.emit(
                                        f"{'🎯' if ex.startswith('tp') else '🛑'} "
                                        f"{p.get('symbol','')} {ex.upper()} @ ${_actual_price:.2f} PnL=${_pnl:+.0f}")
                                except Exception as _ce:
                                    print(f"[EXIT-STREAM] خطأ: {_ce}")
                                finally:
                                    self._sl_executing.discard(_tid)
                                    if hasattr(self, '_closing_trades'):
                                        self._closing_trades.discard(_tid)
                            # سجّل الصفقة كـ "جاري إغلاقها" لمنع إعادة streaming
                            if not hasattr(self, '_closing_trades'):
                                self._closing_trades = set()
                            self._closing_trades.add(tid)
                            _thr.Thread(target=_stream_close, daemon=True).start()
                            dead.append(tid)  # أوقف streaming فوراً
                except Exception: pass

            for tid in dead:
                # أوقف streaming الصفقات المغلقة
                item = self._opt_streaming.pop(tid, None)
                if item:
                    try:
                        opt_c = item[1].get('opt_contract')
                        if opt_c:
                            run_in_ib_thread(self.ib.cancelMktData, opt_c)
                    except Exception: pass
        except Exception as e:
            print(f'[Stream] read error: {e}')

    def _daily_reset(self):
        today = datetime.now().date()
        if today != self._last_reset_day:
            self.risk_manager.reset_daily()
            self._last_reset_day = today
            self._refresh_stats_labels()



    def _on_loss_pct_changed(self, val):
        """نسبة الخسارة اليومية تغيّرت → حدّث RiskManager وأظهر القيمة بالدولار"""
        self.risk_manager.loss_pct = val / 100.0
        if hasattr(self, 'auto_bot') and self.auto_bot:
            self.auto_bot.risk_manager.loss_pct = val / 100.0
        bal = self.account_balance or 0
        if bal > 0:
            dollar = bal * val / 100
            self.loss_val_label.setText(f"= ${dollar:,.0f}")
            self.risk_manager.update_from_balance(bal)
        else:
            self.loss_val_label.setText(f"= {val}% من الرصيد")

    def _on_cost_pct_changed(self, val):
        """نسبة تكلفة الصفقة تغيّرت → حدّث RiskManager وأظهر القيمة بالدولار"""
        self.risk_manager.cost_pct = val / 100.0
        self.risk_manager.max_position_size = val / 100.0
        if hasattr(self, 'auto_bot') and self.auto_bot:
            self.auto_bot.risk_manager.cost_pct       = val / 100.0
            self.auto_bot.risk_manager.max_position_size = val / 100.0
        bal = self.account_balance or 0
        if bal > 0:
            dollar = bal * val / 100
            self.cost_val_label.setText(f"= ${dollar:,.0f}")
            self.risk_manager.update_from_balance(bal)
            # ✅ احسب عدد العقود بناءً على النطاق $50-$100
            _max_c_50  = int(dollar / 50)   # بسعر $50 للعقد
            _max_c_100 = int(dollar / 100)  # بسعر $100 للعقد
            if hasattr(self, 'budget_label'):
                self.budget_label.setText(
                    f"💼 ميزانية: ${dollar:,.0f} ({val:.0f}%) | نطاق $50-$100 | "
                    f"{_max_c_100}-{_max_c_50} عقد")
        else:
            self.cost_val_label.setText(f"= {val}% من الرصيد")
            if hasattr(self, 'budget_label'):
                self.budget_label.setText(f"💼 ميزانية الصفقة: {val:.0f}% من الرصيد")

    def _on_loss_changed(self, val):
        self._on_loss_pct_changed(val)

    def _on_cost_changed(self, val):
        self._on_cost_pct_changed(val)

    def _on_size_changed(self, val):
        # ✅ يُحيل مباشرة لـ _on_cost_pct_changed — لا تعارض
        self._on_cost_pct_changed(val)

    def _show_learning_report(self):
        bot=getattr(self,'auto_bot',None)
        learning = getattr(bot, 'learning', None) if bot else None
        if not bot or not learning:
            QMessageBox.information(self,'🧠','البوت غير مشغَّل أو learning_system.py غير موجود'); return
        bot.learning = learning  # ضمان التوافق
        s=learning.get_summary()
        exp  = s.get('expectancy', 0)
        pf   = s.get('profit_factor', 0)
        exp_warn = s.get('expectancy_warning', False)
        lines=[
            '═══ تقرير التعلم الذاتي ═══',
            f'📊 {s["total_trades"]} صفقة مسجَّلة',
            f'🎯 Win Rate: {s["win_rate"]:.0%}',
            f'💰 PnL الكلي: ${s["total_pnl"]:+.0f}',
            f'📈 Expectancy: ${exp:+.2f}/صفقة  {"⚠️ سلبي! راجع الاستراتيجية" if exp_warn else "✅"}',
            f'⚖️ Profit Factor: {pf:.2f}  {"✅" if pf >= 1.3 else "⚠️ أقل من 1.3"}',
            f'📊 متوسط ربح: ${s.get("avg_win",0):+.2f}  |  متوسط خسارة: ${s.get("avg_loss",0):+.2f}',
            '',
        ]
        if s['lessons']:
            lines.append('📚 الدروس المستخلصة:')
            for l in s['lessons'][-12:]: lines.append(f'  • {l}')
        if s['blocked_syms']: lines.append(f'\n🚫 رموز محظورة مؤقتاً: {", ".join(s["blocked_syms"])}')
        if s['blocked_hours']: lines.append(f'⏰ ساعات خاسرة: {s["blocked_hours"]}')
        if s['signal_weights']:
            lines.append('\n⚖️ أوزان الإشارات:')
            for sig,w in sorted(s['signal_weights'].items(),key=lambda x:-abs(x[1])): lines.append(f'  {"✅" if w>0 else "❌"} {sig}: {w:+d}')
        if s['symbol_min_scores']:
            lines.append('\n📈 حد Score لكل رمز:')
            for sym,mn in sorted(s['symbol_min_scores'].items()): lines.append(f'  {sym}: min={mn}')
        dlg=QDialog(self); dlg.setWindowTitle('🧠 تقرير التعلم الذاتي')
        _scr_lr = QApplication.primaryScreen().availableGeometry()
        dlg.resize(min(520, _scr_lr.width()  - 80),
                   min(600, _scr_lr.height() - 80))
        dlg.setStyleSheet('background:#0a1628;color:#c8d6e5;')
        lay=QVBoxLayout(dlg); txt=QTextEdit(); txt.setReadOnly(True)
        clr = '#ff5e57' if exp_warn else '#05c46b'
        txt.setStyleSheet(f'background:#0d2137;color:{clr};border:none;font-size:11px;font-family:Consolas;')
        txt.setPlainText('\n'.join(lines)); lay.addWidget(txt)
        b=QPushButton('إغلاق'); b.setStyleSheet('background:#1e2d3d;color:#c8d6e5;padding:5px;border-radius:4px;')
        b.clicked.connect(dlg.close); lay.addWidget(b); dlg.exec_()

    def _on_maxtrades_changed(self, val):
        self.risk_manager.max_open_trades = val
        self._refresh_stats_labels()

    def _on_maxdaily_changed(self, val):
        self.risk_manager.max_daily_trades = val
        if hasattr(self,'auto_bot') and self.auto_bot:
            self.auto_bot.risk_manager.max_daily_trades = val
        self._refresh_stats_labels()

    def _manual_reset(self):
        self.risk_manager.reset_daily()
        self._last_reset_day = datetime.now().date()
        self.pnl_label.setText("PnL اليوم: $0.00")
        self.pnl_label.setStyleSheet("color:#05c46b; font-size:13px; font-weight:bold;")
        self._refresh_stats_labels()
        self.statusBar().showMessage("تم إعادة تعيين إحصائيات اليوم")

    def _sync_ui_to_risk(self):
        """مزامنة الواجهة ← risk_manager (بدون إطلاق signals)"""
        rm = self.risk_manager
        _pairs = [
            ('cost_pct_spin', round(rm.cost_pct * 100, 1)),
            ('loss_pct_spin', round(rm.loss_pct * 100, 1)),
        ]
        for attr, val in _pairs:
            w = getattr(self, attr, None)
            if w and abs(w.value() - val) > 0.05:
                w.blockSignals(True)
                w.setValue(val)
                w.blockSignals(False)
        for attr, val in [('maxtrades_spin', rm.max_open_trades),
                          ('maxdaily_spin',  rm.max_daily_trades)]:
            w = getattr(self, attr, None)
            if w and w.value() != val:
                w.blockSignals(True)
                w.setValue(val)
                w.blockSignals(False)

    def _refresh_stats_labels(self):
        bal = self.account_balance or 0
        # حدّث القيم بالدولار بجانب النسب
        if hasattr(self, 'loss_val_label') and bal > 0:
            self.loss_val_label.setText(f"= ${bal * self.risk_manager.loss_pct:,.0f}")
        if hasattr(self, 'cost_val_label') and bal > 0:
            self.cost_val_label.setText(f"= ${bal * self.risk_manager.cost_pct:,.0f}")
        # حدّث عداد الصفقات
        if hasattr(self, 'maxtrades_spin'):
            self.maxtrades_spin.blockSignals(True)
            self.maxtrades_spin.setValue(self.risk_manager.max_open_trades)
            self.maxtrades_spin.blockSignals(False)
        if hasattr(self, 'open_trades_label'):
            self.open_trades_label.setText(
                f"صفقات مفتوحة: {self.risk_manager.open_trades}/{self.risk_manager.max_open_trades}")

    # -----------------------------------------------
    # Slots
    # -----------------------------------------------
    def _on_price(self, p):
        self.current_price = p
        self.price_label.setText(f"${p:,.2f}")
        # تحديث الشارت الرئيسي
        if hasattr(self, '_pro_chart') and self._pro_chart:
            if hasattr(self._pro_chart, 'push_price'):
                self._pro_chart.push_price(p)
            else:
                self._pro_chart.update_live(p)
        # تحديث شارت النافذة المستقلة
        _wc = getattr(self, '_chart_win_chart', None)
        if _wc and hasattr(_wc, 'push_price'):
            _wc.push_price(p)

    def _update_reversal_panel(self, res_z, sup_z, supply_z, demand_z, pivots, rev_sigs, price):
        """تحديث لوحة مناطق الانعكاس بجانب الشارت"""
        rows = []

        # ── Pivot Points ─────────────────────────────
        pp = pivots.get('pp', 0)
        for key, label, clr in [
            ('r3','R3 مقاومة','#ff5e57'),('r2','R2 مقاومة','#ff5e57'),
            ('r1','R1 مقاومة','#ff5e57'),('pp','PP محور','#f9ca24'),
            ('s1','S1 دعم','#05c46b'),  ('s2','S2 دعم','#05c46b'),
            ('s3','S3 دعم','#05c46b'),
        ]:
            val = pivots.get(key, 0)
            if not val: continue
            dist = abs(price - val) / price * 100 if price else 0
            arrow = '▲' if val > price else '▼'
            rows.append((label, f"${val:.2f}", f"{dist:.1f}%", arrow, clr))

        # ── S/R Levels ───────────────────────────────
        for z in res_z[:2]:
            p = z['price']; s = z['strength']
            dist = abs(price - p) / price * 100 if price else 0
            rows.append((f"مقاومة ({s}x)", f"${p:.2f}", f"{dist:.1f}%", "🔴", '#ff5e57'))
        for z in sup_z[:2]:
            p = z['price']; s = z['strength']
            dist = abs(price - p) / price * 100 if price else 0
            rows.append((f"دعم ({s}x)", f"${p:.2f}", f"{dist:.1f}%", "🟢", '#05c46b'))

        # ── Supply/Demand ─────────────────────────────
        for z in supply_z[:1]:
            dist = abs(price - z['mid']) / price * 100 if price else 0
            rows.append(("Supply Zone", f"${z['mid']:.2f}", f"{dist:.1f}%", "⬛", '#fd79a8'))
        for z in demand_z[:1]:
            dist = abs(price - z['mid']) / price * 100 if price else 0
            rows.append(("Demand Zone", f"${z['mid']:.2f}", f"{dist:.1f}%", "⬜", '#55efc4'))

        # ── إشارات الانعكاس ───────────────────────────
        for sig in rev_sigs[:2]:
            d = sig.get('direction','neutral')
            clr = '#05c46b' if d=='bullish' else '#ff5e57' if d=='bearish' else '#f9ca24'
            rows.append((sig.get('type',''), f"${sig.get('price',0):.2f}", "🔔", sig.get('desc','')[:15], clr))

        # ── اعرض في الجدول ───────────────────────────
        for ri, row_lbls in enumerate(self._rev_rows):
            if ri < len(rows):
                name, price_s, strength, ntype, clr = rows[ri]
                row_lbls[0].setText(name);     row_lbls[0].setStyleSheet(f"color:{clr};font-size:10px;font-weight:bold;")
                row_lbls[1].setText(price_s);  row_lbls[1].setStyleSheet(f"color:{clr};font-size:10px;")
                row_lbls[2].setText(strength); row_lbls[2].setStyleSheet("color:#b2bec3;font-size:10px;")
                row_lbls[3].setText(str(ntype));row_lbls[3].setStyleSheet(f"color:{clr};font-size:10px;")
            else:
                for lb in row_lbls:
                    lb.setText(""); lb.setStyleSheet("color:#2d3436;font-size:10px;")

    def _on_clock(self, time_str):
        """تحديث ساعة السوق في الـ status bar"""
        self.statusBar().showMessage(f"🕐 {time_str}", 1500)

    def _on_live_indicators(self, d):
        """تحديث جميع المؤشرات لحظياً"""
        try:
            rsi = d.get('rsi')
            adx = d.get('adx')
            ema9  = d.get('ema9')
            ema21 = d.get('ema21')
            bias  = d.get('bias', '')
            regime = d.get('regime', '')

            if rsi is not None:
                c = "#ff5e57" if rsi > 70 else "#05c46b" if rsi < 30 else "#0fbcf9"
                if hasattr(self,'rsi_label'):
                    self.rsi_label.setText(f"RSI: {rsi:.1f}")
                    self.rsi_label.setStyleSheet(f"color:{c};font-size:11px;font-weight:bold;")

            if adx is not None:
                ac = "#05c46b" if adx >= 25 else "#f9ca24" if adx >= 15 else "#ff5e57"
                if hasattr(self,'adx_disp_label'):
                    self.adx_disp_label.setText(f"ADX: {adx:.0f}")
                    self.adx_disp_label.setStyleSheet(f"color:{ac};font-size:10px;font-weight:bold;")

            if ema9 and ema21:
                trend = "▲ CALL" if ema9 > ema21 else "▼ PUT"
                tc    = "#05c46b" if ema9 > ema21 else "#ff5e57"
                if hasattr(self,'ema9_label'):
                    self.ema9_label.setText(f"EMA9:{ema9:.2f} {trend}")
                    self.ema9_label.setStyleSheet(f"color:{tc};font-size:10px;")
                if hasattr(self,'ema21_label'):
                    self.ema21_label.setText(f"EMA21:{ema21:.2f}")
                    self.ema21_label.setStyleSheet("color:#a29bfe;font-size:10px;")

            if bias:
                bc = "#05c46b" if bias=='bullish' else "#ff5e57" if bias=='bearish' else "#636e72"
                bl = "📈 CALL" if bias=='bullish' else "📉 PUT" if bias=='bearish' else "◼ محايد"
                if hasattr(self,'bias_disp_label'):
                    self.bias_disp_label.setText(bl)
                    self.bias_disp_label.setStyleSheet(f"color:{bc};font-size:11px;font-weight:bold;")

            if regime:
                rc = "#05c46b" if regime=='trending' else "#f9ca24" if regime=='range'                      else "#ff5e57" if 'choppy' in regime else "#0fbcf9"
                if hasattr(self,'regime_label'):
                    self.regime_label.setText(f"Regime: {regime.upper()}")
                    self.regime_label.setStyleSheet(f"color:{rc};font-size:10px;font-weight:bold;")
        except Exception: pass

    def _on_update_trade(self, d):
        """تحديث سعر وPnL و SL بدون تخريب عمود TP1 أو العقود"""
        try:
            tid = d.get('id')
            if not tid:
                return
            for row in range(self.open_trades_table.rowCount()):
                item = self.open_trades_table.item(row, 0)
                if item and item.data(Qt.UserRole) == tid:
                    price = float(d.get('current', 0) or 0)
                    pnl_usd = float(d.get('pnl_usd', 0) or 0)
                    pnl_pct = float(d.get('pnl_pct', 0) or 0)
                    sl = float(d.get('stop_loss', 0) or 0)
                    tp1 = d.get('tp1', None)
                    contracts = d.get('contracts', d.get('qty', None))
                    phase = str(d.get('phase_lbl', '') or '')
                    sign = d.get('pnl_sign', '+' if pnl_usd >= 0 else '-')
                    pnl_abs = float(d.get('pnl_abs', abs(pnl_usd)) or abs(pnl_usd))
                    clr = "#05c46b" if pnl_usd >= 0 else "#ff5e57"

                    if self.open_trades_table.item(row, 5):
                        self.open_trades_table.item(row, 5).setText(f"${price:.2f}")
                    if self.open_trades_table.item(row, 6):
                        self.open_trades_table.item(row, 6).setText(f"{sign}${pnl_abs:.2f} ({pnl_pct:+.1f}%)")
                        self.open_trades_table.item(row, 6).setForeground(QBrush(QColor(clr)))
                    if self.open_trades_table.item(row, 7):
                        # عرض SL + المرحلة بوضوح
                        _phase_txt = f" {phase}" if phase else ""
                        self.open_trades_table.item(row, 7).setText(f"${sl:.2f}{_phase_txt}")
                        # لون يتغير حسب المرحلة
                        if 'TP2' in phase or 'Trail' in phase:
                            self.open_trades_table.item(row, 7).setForeground(QBrush(QColor("#05c46b")))
                        elif 'TP1' in phase:
                            self.open_trades_table.item(row, 7).setForeground(QBrush(QColor("#f9ca24")))

                    if tp1 is not None and self.open_trades_table.item(row, 8):
                        try:
                            self.open_trades_table.item(row, 8).setText(f"${float(tp1):.2f}")
                        except Exception:
                            pass
                    if phase and self.open_trades_table.item(row, 8):
                        self.open_trades_table.item(row, 8).setToolTip(f"Stage: {phase}")

                    if contracts is not None and self.open_trades_table.item(row, 9):
                        try:
                            self.open_trades_table.item(row, 9).setText(str(int(contracts)))
                        except Exception:
                            pass
                    break
        except Exception:
            pass

    def _on_rsi(self, rsi):
        c = "#ff5e57" if rsi > 70 else "#05c46b" if rsi < 30 else "#0fbcf9"
        self.rsi_label.setText(f"RSI: {rsi:.1f}")
        self.rsi_label.setStyleSheet(f"color:{c}; font-size:11px; font-weight:bold;")

    def _update_pm_morning_symbols(self):
        """تحديث قائمة رموز Morning Pre-Market عند تغيير الاختيار"""
        selected = [sym for sym, cb in self._pm_morning_cbs.items() if cb.isChecked()]
        if not selected:
            selected = ['SPX']  # لا تترك القائمة فارغة
        self._pm_morning_symbols = selected

    def _on_cash(self, cash):
        self.account_balance = cash
        self.cash_label.setText(f"${cash:,.2f}")
        self.risk_manager.update_from_balance(cash)
        # ── تحديث engine.balance مباشرة في كل مرة ────────────
        for _eng in [getattr(self, '_exec_engine', None),
                     getattr(getattr(self, 'auto_bot', None), '_exec_engine', None)]:
            if _eng is not None:
                _eng.balance = cash
        self.pnl_label.setText(
            f"PnL اليوم: ${self.risk_manager.daily_pnl:+.2f}"
        )
        pnl_color = "#05c46b" if self.risk_manager.daily_pnl >= 0 else "#ff5e57"
        self.pnl_label.setStyleSheet(f"color:{pnl_color}; font-weight:bold;")
        self.open_trades_label.setText(
            f"صفقات مفتوحة: {self.risk_manager.open_trades}/{self.risk_manager.max_open_trades}"
        )
        self._refresh_stats_labels()
        # ✅ مزامنة الواجهة مع risk_manager عند أول قراءة رصيد
        if not getattr(self, '_ui_synced_once', False):
            self._ui_synced_once = True
            self._sync_ui_to_risk()

    def _on_expiries(self, dates):
        if not dates:
            return
        today = datetime.now().date()
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        future_dates = []
        for raw in sorted(set(dates))[:30]:
            try:
                dt = datetime.strptime(raw, "%Y%m%d").date()
                if dt >= today:  # يشمل اليوم نفسه
                    future_dates.append((raw, dt))
            except Exception:
                pass
        if not future_dates:
            self.expiry_combo.blockSignals(False)
            self._set_fallback_dates()
            return
        for raw, dt in future_dates[:15]:
            display = dt.strftime("%Y-%m-%d (%a)")
            self.expiry_combo.addItem(display, userData=raw)
        self.expiry_combo.blockSignals(False)
        self.update_options_table()

    def _on_analysis(self, res):
        sig    = res.get('signal') or res.get('direction')
        cs     = res.get('call_score', 0) or 0
        ps     = res.get('put_score',  0) or 0
        adx    = res.get('adx',  0) or 0
        rsi    = res.get('rsi',  0) or 0
        regime = res.get('regime', res.get('market_type', 'normal'))
        bias   = res.get('market_bias', '')
        mtype  = res.get('market_type', '')

        # ── إشارة رئيسية ──────────────────────────────
        if sig == 'CALL':
            sc, cl = "#05c46b", f"✅ CALL  ({cs:.0f} نقطة)"
        elif sig == 'PUT':
            sc, cl = "#ff5e57", f"✅ PUT  ({ps:.0f} نقطة)"
        elif regime == 'super_choppy_skip':
            sc, cl = "#ff5e57", f"🚫 لا تداول — ADX={adx:.0f} (أقل من 8)"
        elif mtype == 'range':
            rsi_need = 'RSI < 30 للـ CALL' if bias=='bullish' else 'RSI > 70 للـ PUT'
            sc, cl = "#ffb62e", f"⏸ Range Mode — يحتاج {rsi_need}"
        elif mtype == 'trend':
            sc, cl = "#ffb62e", f"⏳ Trend — يحتاج VWAP + EMA + BB"
        elif cs > 0 or ps > 0:
            dominant = 'CALL' if cs >= ps else 'PUT'
            dom_sc   = cs if dominant == 'CALL' else ps
            sc, cl   = "#ffb62e", f"⏳ {dominant} ({dom_sc:.0f} — لم تكتمل)"
        else:
            sc, cl = "#8395a7", f"⏸ انتظار — ADX={adx:.0f} RSI={rsi:.0f}"

        self.strat_label.setText(cl)
        self.strat_label.setStyleSheet(f"color:{sc}; font-size:14px; font-weight:bold;")

        # ── Regime ──────────────────────────────────
        regime_map = {
            'trending': ('📈 TREND',  '#05c46b'),
            'range':    ('↔ RANGE',   '#a29bfe'),
            'volatile': ('🌪 VOLATILE','#ff5e57'),
            'choppy':   ('💤 CHOPPY', '#8395a7'),
            'normal':   ('⚖ NORMAL',  '#0fbcf9'),
        }
        rtxt, rclr = regime_map.get(regime, ('⏳ --', '#8395a7'))
        self.regime_label.setText(rtxt)
        self.regime_label.setStyleSheet(f"color:{rclr}; font-size:10px; font-weight:bold;")

        # ── Market Type (Trend/Range/Weak) ──────────
        mtype_map = {'trend':'Trend Mode','range':'Range Mode','weak_trend':'Weak Trend'}
        mt_txt = mtype_map.get(mtype, mtype or '--')
        self.market_type_label.setText(mt_txt)
        mclr = '#05c46b' if mtype=='trend' else '#a29bfe' if mtype=='range' else '#8395a7'
        self.market_type_label.setStyleSheet(f"color:{mclr}; font-size:10px;")

        # ── ADX | RSI | Bias ──────────────────────
        adx_clr = '#05c46b' if adx>=25 else '#ffb62e' if adx>=15 else '#ff5e57'
        rsi_clr = '#ff5e57' if rsi>70 else '#05c46b' if rsi<30 else '#0fbcf9'
        bias_clr= '#05c46b' if bias=='bullish' else '#ff5e57' if bias=='bearish' else '#8395a7'
        self.adx_disp_label.setText(f"ADX:{adx:.0f}")
        self.adx_disp_label.setStyleSheet(f"color:{adx_clr}; font-size:10px; font-weight:bold;")
        self.rsi_disp_label.setText(f"RSI:{rsi:.0f}")
        self.rsi_disp_label.setStyleSheet(f"color:{rsi_clr}; font-size:10px; font-weight:bold;")
        self.bias_disp_label.setText(f"{'📈' if bias=='bullish' else '📉' if bias=='bearish' else '⏸'} {bias.upper() if bias else '--'}")
        self.bias_disp_label.setStyleSheet(f"color:{bias_clr}; font-size:10px; font-weight:bold;")

        # ── تحديث Greeks من نتيجة التحليل ──────────────
        _gk = res.get('greeks', {})
        if _gk and hasattr(self, 'greeks_delta'):
            _d  = _gk.get('delta', 0)
            _gm = _gk.get('gamma', 0)
            _th = _gk.get('theta', 0)
            _vg = _gk.get('vega',  0)
            _iv = _gk.get('iv',    0)
            _dt = _gk.get('dte',   0)

            # ألوان حسب القيم
            _d_clr  = '#05c46b' if 0.35 <= _d <= 0.50 else '#ffb62e' if 0.20 <= _d <= 0.70 else '#ff5e57'
            _th_clr = '#05c46b' if abs(_th) < 0.02 else '#ffb62e' if abs(_th) < 0.05 else '#ff5e57'
            _iv_clr = '#05c46b' if _iv < 0.30 else '#ffb62e' if _iv < 0.60 else '#ff5e57'

            self.greeks_delta.setText(f"Δ Delta: {_d:.3f}")
            self.greeks_delta.setStyleSheet(f"color:{_d_clr}; font-size:10px; font-weight:bold;")
            self.greeks_gamma.setText(f"Γ Gamma: {_gm:.4f}")
            self.greeks_theta.setText(f"Θ Theta: {_th:.4f}")
            self.greeks_theta.setStyleSheet(f"color:{_th_clr}; font-size:10px; font-weight:bold;")
            self.greeks_vega.setText(f"V Vega: {_vg:.4f}")
            self.greeks_iv.setText(f"IV: {_iv*100:.0f}%")
            self.greeks_iv.setStyleSheet(f"color:{_iv_clr}; font-size:10px; font-weight:bold;")
            _src = _gk.get('source', '')
            _src_txt = "" if _src == 'ibkr' else " ~تقديري"
            self.greeks_dte.setText(f"DTE: {_dt}{_src_txt}")

            # حكم على العقد
            _ok = (0.20 <= _d <= 0.70) and (_iv < 1.5)
            self.greeks_verdict.setText("✅ مقبول" if _ok else "⛔ مرفوض")
            self.greeks_verdict.setStyleSheet(f"color:{'#05c46b' if _ok else '#ff5e57'}; font-size:9px;")

        # ── CALL bar ──────────────────────────────
        # score max = 8 نقاط عادةً
        c_pct = min(100, int(cs / 8 * 100))
        self.call_score_bar.setValue(c_pct)
        self.call_score_lbl.setText(f"{cs:.0f} نقطة")
        call_why = res.get('call_why', '') or (res.get('why','') if sig=='CALL' else '')
        self.call_why_label.setText(call_why[:80] if call_why else '--')

        # ── PUT bar ───────────────────────────────
        p_pct = min(100, int(ps / 8 * 100))
        self.put_score_bar.setValue(p_pct)
        self.put_score_lbl.setText(f"{ps:.0f} نقطة")
        put_why = res.get('put_why', '') or (res.get('why','') if sig=='PUT' else '')
        self.put_why_label.setText(put_why[:80] if put_why else '--')

        # ── EMA | ATR | RSI labels ─────────────────
        ema9  = res.get('ema9')
        ema21 = res.get('ema21')
        atr   = res.get('atr')
        if ema9  is not None: self.ema9_label.setText(f"EMA9:{ema9:.2f}")
        if ema21 is not None: self.ema21_label.setText(f"EMA21:{ema21:.2f}")
        if atr   is not None: self.atr_label.setText(f"ATR:{atr:.3f}")
        self.rsi_label.setText(f"RSI:{rsi:.1f}")
        self.rsi_label.setStyleSheet(f"color:{rsi_clr}; font-size:9px; font-weight:bold;")

        # ── Why (السبب الكامل) ───────────────────
        why = res.get('why', '')
        if not why:
            if regime == 'super_choppy_skip':
                why = f"ADX={adx:.0f} < 8 → سوق بلا اتجاه (ساعات ما قبل السوق؟)"
            elif adx:
                adx_status = '✅ ADX جاهز' if adx>=25 else f'⚠ ADX={adx:.0f} < 25 (Range)'
                htf = res.get('htf_bias','')
                why = f"{adx_status} | RSI:{rsi:.0f} | Bias:{bias.upper() if bias else '--'}"
                if htf and htf != 'range_mode':
                    why += f" | HTF:{htf}"
        self.why_label.setText(why[:140] if why else '')

    def _on_options(self, rows):
        # ── cache للـBridge: بيانات حقيقية من IBKR ────────────────
        sym = getattr(self, "current_symbol", "")
        self._live_options_cache = list(rows)                          # backwards compat
        self._live_options_cache_sym = sym
        # multi-symbol dict — البوت يقرأ منه لأي رمز
        if not hasattr(self, "_options_cache_dict"):
            self._options_cache_dict = {}
        if sym and rows:
            self._options_cache_dict[sym.upper()] = list(rows)
        # ترتيب: PUT Bid | PUT Ask | Strike (وسط) | CALL Bid | CALL Ask
        self.options_table.setRowCount(len(rows))
        self.options_table.setColumnCount(5)
        self.options_table.setHorizontalHeaderLabels(
            ["PUT Ask", "PUT Bid", "Strike", "CALL Bid", "CALL Ask"]
        )
        # توسيع عمود Strike في المنتصف
        self.options_table.setColumnWidth(2, 90)
        for i, row in enumerate(rows):
            s    = row['strike']
            step = 25 if self.current_price > 1000 else 5
            atm  = abs(s - self.current_price) <= step / 2

            # col0 = PUT Ask
            pa_item = QTableWidgetItem(row.get('put_ask', '--'))
            pa_item.setTextAlignment(Qt.AlignCenter)
            pa_item.setForeground(QBrush(QColor("#ff5e57")))
            self.options_table.setItem(i, 0, pa_item)

            # col1 = PUT Bid
            pb_item = QTableWidgetItem(row.get('put_bid', '--'))
            pb_item.setTextAlignment(Qt.AlignCenter)
            pb_item.setForeground(QBrush(QColor("#ff5e57")))
            self.options_table.setItem(i, 1, pb_item)

            # col2 = Strike (وسط — مميز باللون)
            si = QTableWidgetItem(f"{s:,.2f}")
            si.setTextAlignment(Qt.AlignCenter)
            if atm:
                si.setBackground(QBrush(QColor("#0fbcf9")))
                si.setForeground(QBrush(QColor("#0a1628")))
                si.setFont(__import__("PyQt5.QtGui", fromlist=["QFont"]).QFont("", 10, 75))
            else:
                si.setForeground(QBrush(QColor("#f9ca24")))
            self.options_table.setItem(i, 2, si)

            # col3 = CALL Bid
            cb_item = QTableWidgetItem(row.get('call_bid', '--'))
            cb_item.setTextAlignment(Qt.AlignCenter)
            cb_item.setForeground(QBrush(QColor("#05c46b")))
            self.options_table.setItem(i, 3, cb_item)

            # col4 = CALL Ask
            ca_item = QTableWidgetItem(row.get('call_ask', '--'))
            ca_item.setTextAlignment(Qt.AlignCenter)
            ca_item.setForeground(QBrush(QColor("#05c46b")))
            self.options_table.setItem(i, 4, ca_item)

        self.options_table.resizeColumnsToContents()
        self.options_table.setColumnWidth(2, 90)  # Strike أوسع

    # -----------------------------------------------
    # UI
    # -----------------------------------------------
    def initUI(self):
        self.setWindowTitle("🤖 Auto Options Trader Pro")
        self.setMinimumSize(1024, 700)
        self.showMaximized()
        _scr           = QApplication.primaryScreen().availableGeometry()
        _sw            = _scr.width()
        self._ui_scale = max(0.78, min(1.0, _sw / 1920.0))
        self.setStyleSheet("""
            QMainWindow,QWidget { background-color:#111a26; }
            QLabel  { color:#c8d6e5; font-size:12px; }
            QGroupBox {
                color:#c8d6e5; border:1px solid #0fbcf9;
                border-radius:6px; margin-top:10px;
                font-size:12px; font-weight:bold;
                background-color:#162030; padding:4px;
            }
            QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 5px; }
            QTableWidget {
                background:#162030; color:#c8d6e5;
                gridline-color:#1e2d3d; border:none; font-size:11px;
            }
            QTableWidget::item         { padding:3px; }
            QTableWidget::item:selected { background:#0fbcf9; color:#0a1628; }
            QHeaderView::section {
                background:#0a1628; color:#0fbcf9;
                border:1px solid #1e2d3d; padding:3px; font-size:11px;
            }
            QPushButton {
                background:#0fbcf9; color:#0a1628; border:none;
                padding:7px 14px; border-radius:4px;
                font-size:12px; font-weight:bold;
            }
            QPushButton:hover    { background:#4bcffa; }
            QPushButton:disabled { background:#1e2d3d; color:#485460; }
            QListWidget {
                background:#162030; color:#c8d6e5;
                border:1px solid #1e2d3d; border-radius:4px; font-size:11px;
            }
            QListWidget::item          { padding:3px 6px; border-bottom:1px solid #1e2d3d; }
            QListWidget::item:selected { background:#0fbcf9; color:#0a1628; }
            QComboBox {
                background:#162030; color:#c8d6e5;
                border:1px solid #0fbcf9; border-radius:4px; padding:4px;
            }
            QComboBox QAbstractItemView {
                background:#162030; color:#c8d6e5;
                selection-background-color:#0fbcf9;
            }
            QLineEdit {
                background:#162030; color:#c8d6e5;
                border:1px solid #0fbcf9; border-radius:4px; padding:4px;
            }
            QProgressBar {
                background:#1e2d3d; border-radius:3px; height:12px;
                text-align:center; font-size:10px; color:#c8d6e5;
            }
            QProgressBar::chunk { border-radius:3px; }
            QTabWidget::pane  { border:1px solid #0fbcf9; background:#162030; }
            QTabBar::tab {
                background:#0a1628; color:#8395a7; padding:6px 14px;
                border:1px solid #1e2d3d; border-bottom:none;
            }
            QTabBar::tab:selected { background:#162030; color:#0fbcf9; }
            QScrollBar:vertical   { background:#0a1628; width:8px; border-radius:4px; }
            QScrollBar::handle:vertical { background:#0fbcf9; border-radius:4px; }
            QScrollArea { border:none; background:#111a26; }
        """)

        _left   = self._build_left()
        _center = self._build_center()
        _right  = self._build_right()

        c = QWidget()
        self.setCentralWidget(c)

        # ── Single dashboard on all screens ──────────────────────────
        # لا يوجد وضع Tabs للابتوب. نفس عرض الديسكتوب دائماً:
        # يسار تحكم + وسط شارت + يمين تحليل، مع Scroll لليسار واليمين.
        _left_scr = QScrollArea()
        _left_scr.setWidget(_left)
        _left_scr.setWidgetResizable(True)
        _left_scr.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        _left_scr.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        _left_scr.setFrameShape(QFrame.NoFrame)
        _left_scr.setStyleSheet(
            "QScrollArea{border:none;background:#111a26;}"
            "QScrollArea > QWidget{background:#111a26;}"
        )

        _spl = QSplitter(Qt.Horizontal)
        _spl.addWidget(_left_scr)
        _spl.addWidget(_center)
        _spl.addWidget(_right)
        _spl.setStretchFactor(0, 0)
        _spl.setStretchFactor(1, 1)
        _spl.setStretchFactor(2, 0)
        _spl.setChildrenCollapsible(False)
        _spl.setHandleWidth(4)
        _spl.setStyleSheet(
            "QSplitter::handle{background:#1e2d3d;border-radius:2px;}"
        )

        # مقاسات أعمدة متكيفة لكل الشاشات
        if _sw <= 1366:       # 1366×768  laptop
            _left_w, _right_w = 230, 240
        elif _sw <= 1400:     # small desktop
            _left_w, _right_w = 240, 250
        elif _sw <= 1600:     # 1600×900
            _left_w, _right_w = 255, 265
        elif _sw <= 1920:     # 1920×1080 Full HD
            _left_w = max(260, int(_sw * 0.15))
            _right_w = max(270, int(_sw * 0.15))
        else:                 # 2K / 4K
            _left_w = max(300, int(_sw * 0.14))
            _right_w = max(300, int(_sw * 0.14))

        _center_w = max(520, _sw - _left_w - _right_w - 24)
        _left_scr.setMinimumWidth(220)
        _right.setMinimumWidth(230)
        _center.setMinimumWidth(500)
        _spl.setSizes([_left_w, _center_w, _right_w])

        ml = QHBoxLayout(c)
        ml.setContentsMargins(4, 4, 4, 4)
        ml.setSpacing(0)
        ml.addWidget(_spl)

        sb = self.statusBar()
        sb.setStyleSheet("background:#0a1628; color:#0fbcf9; font-size:11px;")
        sb.showMessage("جاهز - قم بالاتصال بـ IBKR لبدء التداول التلقائي")

    # -----------------------------------------------
    def _lbl(self, text, color="#8395a7", size=10, bold=False):
        l = QLabel(text)
        fw = "bold" if bold else "normal"
        _s = max(9, int(size * getattr(self, '_ui_scale', 1.0)))
        l.setStyleSheet(f"color:{color}; font-size:{_s}px; font-weight:{fw};")
        return l

    def _ind_lbl(self, text, color):
        l = QLabel(text)
        _s = max(9, int(11 * getattr(self, '_ui_scale', 1.0)))
        l.setStyleSheet(
            f"color:{color}; font-size:{_s}px; font-weight:bold;"
            f"background:#0a1628; padding:2px 6px; border-radius:3px;"
        )
        return l

    # -----------------------------------------------
    # -----------------------------------------------
    def _build_left(self):
        p = QWidget(); p.setMinimumWidth(260)
        v = QVBoxLayout(p); v.setSpacing(5)

        def _ss(c):  # spin style shortcut
            return (f"QDoubleSpinBox,QSpinBox{{background:#0a1628;color:{c};"
                    f"border:1px solid {c};border-radius:3px;padding:2px 4px;"
                    f"font-size:11px;font-weight:bold;}}"
                    f"QDoubleSpinBox::up-button,QDoubleSpinBox::down-button,"
                    f"QSpinBox::up-button,QSpinBox::down-button"
                    f"{{width:16px;background:#1e2d3d;border:none;}}")

        # ═══ الاتصال ════════════════════════════════════════════
        g = QGroupBox("🔌 الاتصال بـ IBKR")
        gl = QVBoxLayout(); gl.setSpacing(4)
        self.connect_btn = QPushButton("اتصال بـ IBKR")
        self.connect_btn.clicked.connect(self.connect_ibkr)
        gl.addWidget(self.connect_btn)
        _cr = QHBoxLayout()
        self.status_label = QLabel("● غير متصل")
        self.status_label.setStyleSheet("color:#ff5e57;font-weight:bold;font-size:11px;")
        _cr.addWidget(self.status_label); _cr.addStretch()
        self.cash_label = QLabel("$--")
        self.cash_label.setStyleSheet("color:#0fbcf9;font-weight:bold;font-size:14px;")
        _cr.addWidget(self.cash_label)
        gl.addLayout(_cr)
        g.setLayout(gl); v.addWidget(g)

        # ═══ إحصائيات اليوم ═════════════════════════════════════
        g2 = QGroupBox("📊 اليوم")
        g2l = QVBoxLayout(); g2l.setSpacing(3)
        _pr = QHBoxLayout()
        self.pnl_label = QLabel("PnL: $0.00")
        self.pnl_label.setStyleSheet("color:#05c46b;font-size:13px;font-weight:bold;")
        _pr.addWidget(self.pnl_label); _pr.addStretch()
        self.open_trades_label = QLabel("0/2 صفقات")
        self.open_trades_label.setStyleSheet("color:#a29bfe;font-size:11px;font-weight:bold;")
        _pr.addWidget(self.open_trades_label)
        g2l.addLayout(_pr)
        _sep = QFrame(); _sep.setFrameShape(QFrame.HLine)
        _sep.setStyleSheet("color:#1e2d3d;"); g2l.addWidget(_sep)
        g2l.addWidget(self._lbl("إدارة المخاطر", "#0fbcf9", 10, True))
        # خسارة / يوم
        _r1 = QHBoxLayout(); _r1.setSpacing(4)
        _r1.addWidget(self._lbl("خسارة/يوم:", "#ff5e57", 10))
        self.loss_pct_spin = QDoubleSpinBox()
        self.loss_pct_spin.setRange(1.0,20.0); self.loss_pct_spin.setSingleStep(0.5)
        self.loss_pct_spin.setValue(10.0); self.loss_pct_spin.setSuffix("%"); self.loss_pct_spin.setDecimals(1)
        self.loss_pct_spin.setStyleSheet(_ss("#ff5e57")); self.loss_pct_spin.setFixedWidth(72)
        self.loss_pct_spin.valueChanged.connect(self._on_loss_pct_changed)
        self.loss_val_label = QLabel("=$--"); self.loss_val_label.setStyleSheet("color:#ff5e57;font-size:10px;")
        _r1.addWidget(self.loss_pct_spin); _r1.addWidget(self.loss_val_label); _r1.addStretch()
        g2l.addLayout(_r1)
        # حد / صفقة
        _r2 = QHBoxLayout(); _r2.setSpacing(4)
        _r2.addWidget(self._lbl("حد/صفقة:", "#ffb62e", 10))
        self.cost_pct_spin = QDoubleSpinBox()
        self.cost_pct_spin.setRange(1.0,100.0); self.cost_pct_spin.setSingleStep(1.0)
        self.cost_pct_spin.setValue(50.0); self.cost_pct_spin.setSuffix("%"); self.cost_pct_spin.setDecimals(1)
        self.cost_pct_spin.setStyleSheet(_ss("#ffb62e")); self.cost_pct_spin.setFixedWidth(72)
        self.cost_pct_spin.valueChanged.connect(self._on_cost_pct_changed)
        self.cost_val_label = QLabel("=$--"); self.cost_val_label.setStyleSheet("color:#ffb62e;font-size:10px;")
        _r2.addWidget(self.cost_pct_spin); _r2.addWidget(self.cost_val_label); _r2.addStretch()
        g2l.addLayout(_r2)
        # مفتوح + يومي في صف
        _r3 = QHBoxLayout(); _r3.setSpacing(6)
        _r3.addWidget(self._lbl("مفتوحة:", "#a29bfe", 10))
        self.maxtrades_spin = QSpinBox()
        self.maxtrades_spin.setRange(1,10); self.maxtrades_spin.setValue(self.risk_manager.max_open_trades)
        self.maxtrades_spin.setStyleSheet(_ss("#a29bfe")); self.maxtrades_spin.setFixedWidth(48)
        self.maxtrades_spin.valueChanged.connect(self._on_maxtrades_changed)
        _r3.addWidget(self.maxtrades_spin)
        _r3.addWidget(self._lbl("يومي:", "#ffd32a", 10))
        self.maxdaily_spin = QSpinBox()
        self.maxdaily_spin.setRange(1,20); self.maxdaily_spin.setValue(self.risk_manager.max_daily_trades)
        self.maxdaily_spin.setStyleSheet(_ss("#ffd32a")); self.maxdaily_spin.setFixedWidth(48)
        self.maxdaily_spin.valueChanged.connect(self._on_maxdaily_changed)
        _r3.addWidget(self.maxdaily_spin); _r3.addStretch()
        g2l.addLayout(_r3)
        # أزرار صف
        _br = QHBoxLayout(); _br.setSpacing(4)
        _rst = QPushButton("🔄 تعيين اليوم")
        _rst.setStyleSheet("background:#162030;color:#8395a7;border:1px solid #1e2d3d;border-radius:3px;padding:4px;font-size:10px;")
        _rst.clicked.connect(self._manual_reset)
        _br.addWidget(_rst)
        g2l.addLayout(_br)
        g2.setLayout(g2l); v.addWidget(g2)

        # توافق كود قديم
        self.loss_spin = self.loss_pct_spin
        self.cost_spin = self.cost_pct_spin

        # ═══ DataFeed ════════════════════════════════════════════
        gdf = QGroupBox("📡 البيانات الحية (TradingView)")
        gdfl = QVBoxLayout(); gdfl.setSpacing(4)
        self.datafeed_btn = QPushButton("▶ تشغيل DataFeed")
        self.datafeed_btn.setStyleSheet(
            "background:#0fbcf9;color:#0a1628;font-size:12px;font-weight:bold;padding:8px;")
        self.datafeed_btn.clicked.connect(self.toggle_datafeed)
        gdfl.addWidget(self.datafeed_btn)
        _dfs = QHBoxLayout()
        self.datafeed_status_lbl = QLabel("⏸ متوقف")
        self.datafeed_status_lbl.setStyleSheet("color:#8395a7;font-size:10px;")
        _dfs.addWidget(self.datafeed_status_lbl); _dfs.addStretch()
        self.datafeed_cycle_lbl = QLabel("")
        self.datafeed_cycle_lbl.setStyleSheet("color:#636e72;font-size:9px;")
        _dfs.addWidget(self.datafeed_cycle_lbl)
        gdfl.addLayout(_dfs)
        _dfi = QHBoxLayout()
        _dfi.addWidget(self._lbl("تحديث كل:", "#8395a7", 10))
        self.datafeed_interval_spin = QSpinBox()
        self.datafeed_interval_spin.setRange(15, 300)
        self.datafeed_interval_spin.setValue(60)
        self.datafeed_interval_spin.setSuffix(" ث")
        self.datafeed_interval_spin.setStyleSheet(
            "QSpinBox{background:#0a1628;color:#0fbcf9;border:1px solid #0fbcf9;"
            "border-radius:3px;padding:2px;font-size:10px;font-weight:bold;}"
            "QSpinBox::up-button{width:14px;background:#1e2d3d;border:none;}"
            "QSpinBox::down-button{width:14px;background:#1e2d3d;border:none;}")
        self.datafeed_interval_spin.setFixedWidth(65)
        _dfi.addWidget(self.datafeed_interval_spin); _dfi.addStretch()
        gdfl.addLayout(_dfi)
        gdf.setLayout(gdfl); v.addWidget(gdf)
        self._datafeed_proc = None

        # ═══ البوت التلقائي ══════════════════════════════════════
        g3 = QGroupBox("🤖 البوت التلقائي")
        g3l = QVBoxLayout(); g3l.setSpacing(4)
        self.bot_btn = QPushButton("▶ تشغيل البوت")
        self.bot_btn.clicked.connect(self.toggle_bot)
        self.bot_btn.setEnabled(False)
        self.bot_btn.setStyleSheet("background:#05c46b;color:#0a1628;font-size:13px;font-weight:bold;padding:10px;")
        g3l.addWidget(self.bot_btn)
        _sl = QHBoxLayout()
        self.bot_status_label = QLabel("⏸ متوقف")
        self.bot_status_label.setStyleSheet("color:#8395a7;font-size:11px;")
        _sl.addWidget(self.bot_status_label); _sl.addStretch()
        self.scan_label = QLabel("--")
        self.scan_label.setStyleSheet("color:#636e72;font-size:9px;")
        _sl.addWidget(self.scan_label)
        g3l.addLayout(_sl)
        _opts = QHBoxLayout()
        self.auto_exec_cb = QCheckBox("تنفيذ تلقائي")
        self.auto_exec_cb.setChecked(True)
        self.auto_exec_cb.setStyleSheet("color:#ffb62e;font-size:11px;")
        self.pre_market_cb = QCheckBox("🌅 Pre-Market")
        self.pre_market_cb.setChecked(True)
        self.pre_market_cb.setStyleSheet("color:#0fbcf9;font-size:11px;")
        self.pre_market_cb.setToolTip("🌙 Evening (20–04 EST): SPX/XSP فقط\n🌅 Morning (04–09:30 EST): ETFs + أسهم")
        _opts.addWidget(self.auto_exec_cb); _opts.addWidget(self.pre_market_cb)
        g3l.addLayout(_opts)

        # ── عدد العقود ─────────────────────────────────────────
        _cl = QHBoxLayout(); _cl.setSpacing(6)
        _cl.addWidget(self._lbl("📋 عدد العقود:", "#c8d6e5", 11))
        self.contracts_spin = QSpinBox()
        self.contracts_spin.setRange(1, 10)
        self.contracts_spin.setValue(1)
        self.contracts_spin.setFixedWidth(60)
        self.contracts_spin.setStyleSheet(
            "QSpinBox{background:#0a1628;color:#05c46b;border:1px solid #0fbcf9;"
            "border-radius:4px;padding:3px;font-size:13px;font-weight:bold;}"
            "QSpinBox::up-button,QSpinBox::down-button{width:18px;background:#162030;border:none;}"
        )
        self.contracts_spin.setToolTip("عدد العقود لكل صفقة (افتراضي: 1)\n0DTE — عقد واحد يساوي 100 سهم")
        self.contracts_spin.valueChanged.connect(self._on_contracts_changed)
        _cl.addWidget(self.contracts_spin)
        _cl.addStretch()
        g3l.addLayout(_cl)
        _dte_row = QHBoxLayout(); _dte_row.setContentsMargins(0,0,0,0); _dte_row.setSpacing(4)
        _dte_l = self._lbl("DTE: 0–2", "#8395a7", 9)
        _dte_l.setLayoutDirection(Qt.LeftToRight)
        _cut_l = self._lbl("⛔ حظر بعد 3PM", "#ff9f43", 9)
        _cut_l.setLayoutDirection(Qt.RightToLeft)
        _dte_row.addWidget(_dte_l); _dte_row.addStretch(); _dte_row.addWidget(_cut_l)
        g3l.addLayout(_dte_row)
        g3l.addWidget(self._lbl("⚠ للحساب الحقيقي فقط", "#ff5e57", 9))
        g3.setLayout(g3l); v.addWidget(g3)

        # ═══ رموز المسح ═════════════════════════════════════════
        g4 = QGroupBox("🔍 رموز المسح")
        g4l = QVBoxLayout(); g4l.setSpacing(3)
        self.watchlist_widget = QListWidget()
        self.watchlist_widget.setMinimumHeight(100)
        self.watchlist_widget.setStyleSheet(
            "QListWidget{background:#0a1628;color:#c8d6e5;font-size:11px;border:1px solid #1e2d3d;}"
            "QListWidget::item:selected{background:#0fbcf9;color:#0a1628;}")
        self.watchlist_widget.itemClicked.connect(self.select_from_list)
        for _s in self.auto_bot_scan_symbols:
            self.watchlist_widget.addItem(_s)
        g4l.addWidget(self.watchlist_widget)
        _ar = QHBoxLayout(); _ar.setSpacing(3)
        self.watchlist_input = QLineEdit()
        self.watchlist_input.setPlaceholderText("رمز جديد...")
        self.watchlist_input.setStyleSheet("background:#162030;color:#c8d6e5;border:1px solid #0fbcf9;border-radius:3px;padding:3px;font-size:11px;")
        self.watchlist_input.returnPressed.connect(self._add_watchlist_symbol)
        _ab = QPushButton("➕"); _ab.setMaximumWidth(26); _ab.clicked.connect(self._add_watchlist_symbol)
        _db = QPushButton("🗑"); _db.setMaximumWidth(26); _db.clicked.connect(self._del_watchlist_symbol)
        _ar.addWidget(self.watchlist_input); _ar.addWidget(_ab); _ar.addWidget(_db)
        g4l.addLayout(_ar)
        g4.setLayout(g4l); v.addWidget(g4)

        ar = QGroupBox("⚙️ النظام التكيفي")
        arv = QVBoxLayout(); arv.setSpacing(2)
        self.regime_lbl = QLabel("⏳ انتظار البيانات...")
        self.regime_lbl.setStyleSheet("color:#8395a7;font-size:11px;font-weight:bold;")
        self.regime_lbl.setWordWrap(True)
        arv.addWidget(self.regime_lbl)
        self.adapt_params_lbl = QLabel("")
        self.adapt_params_lbl.setStyleSheet("color:#636e72;font-size:10px;")
        self.adapt_params_lbl.setWordWrap(True)
        arv.addWidget(self.adapt_params_lbl)
        ar.setLayout(arv); v.addWidget(ar)

        v.addStretch()
        return p

    def _build_center(self):
        p = QWidget()
        v = QVBoxLayout(p); v.setSpacing(6)

        # شريط الرمز
        tr = QHBoxLayout()
        tr.addWidget(self._lbl("الرمز:", "#8395a7"))
        self.symbol_input = QLineEdit("SPY")
        self.symbol_input.setMaximumWidth(80)
        self.symbol_input.returnPressed.connect(self.change_symbol)
        tr.addWidget(self.symbol_input)
        lb = QPushButton("تحميل")
        lb.setMaximumWidth(60); lb.clicked.connect(self.change_symbol)
        tr.addWidget(lb)
        self.price_label = QLabel("$--")
        self.price_label.setStyleSheet("color:#0fbcf9; font-size:22px; font-weight:bold;")
        tr.addWidget(self.price_label)
        tr.addStretch()
        # زر فتح الشارت في نافذة مستقلة
        _chart_btn = QPushButton("📊 فتح الشارت")
        _chart_btn.setStyleSheet(
            "QPushButton{background:#1a2744;color:#0fbcf9;border:1px solid #0fbcf9;"
            "border-radius:4px;padding:4px 12px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#0fbcf9;color:#0a1628;}")
        _chart_btn.clicked.connect(self._open_chart_window)
        tr.addWidget(_chart_btn)
        v.addLayout(tr)

        # ══════════════════════════════════════════════
        # 📊 ProChart — pro_chart_js.py
        # ══════════════════════════════════════════════
        try:
            self._pro_chart = ProChartWidget(self)
            v.addWidget(self._pro_chart, stretch=1)
            self.chart = self._pro_chart._price_plot
            print("[Chart] ✅ ProChartWidget جاهز")
        except Exception as _ce:
            import traceback; traceback.print_exc()
            print(f"[Chart] ❌ {_ce}")
            _fallback = pg.PlotWidget()
            _fallback.setBackground('#131722')
            v.addWidget(_fallback, stretch=1)
            self._pro_chart = None
            self.chart = _fallback
        self._bm_rows  = []
        self._rev_rows = []


        # Tabs - اوبشن / صفقات مفتوحة / تاريخ
        tabs = QTabWidget()

        # Tab 1: الاوبشن
        opt_tab = QWidget()
        otv = QVBoxLayout(opt_tab)
        er = QHBoxLayout()
        er.addWidget(self._lbl("تاريخ الانتهاء:"))
        self.expiry_combo = QComboBox()
        self.expiry_combo.setMinimumWidth(175)
        self.expiry_combo.currentIndexChanged.connect(self.update_options_table)
        er.addWidget(self.expiry_combo)
        rb = QPushButton("تحديث التواريخ")
        rb.setMaximumWidth(125)
        rb.clicked.connect(lambda: self._fetch_expiries_for(self.current_symbol))
        er.addWidget(rb)
        er.addStretch()
        otv.addLayout(er)
        self.options_status = QLabel("انتظار الاتصال...")
        self.options_status.setStyleSheet("color:#ffb62e; font-size:10px;")
        otv.addWidget(self.options_status)
        self.options_table = QTableWidget()
        self.options_table.setMinimumHeight(180)
        otv.addWidget(self.options_table)
        # Tab 2: الصفقات المفتوحة
        open_tab = QWidget()
        ov = QVBoxLayout(open_tab)
        self.open_trades_table = QTableWidget()
        self.open_trades_table.setColumnCount(13)
        self.open_trades_table.setHorizontalHeaderLabels([
            "الرمز","النوع","Strike","Expiry",
            "Entry","الآن","PnL$","SL","TP1","عقود","التكلفة","استراتيجية","خروج"
        ])
        self.open_trades_table.horizontalHeader().setStretchLastSection(True)
        ov.addWidget(self.open_trades_table)

        # Tab 3: التاريخ
        hist_tab = QWidget()
        hv = QVBoxLayout(hist_tab)
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(8)
        self.history_table.setHorizontalHeaderLabels([
            "الوقت","الرمز","النوع","Entry","Exit","سبب الخروج","PnL","Score"
        ])
        self.history_table.horizontalHeader().setStretchLastSection(True)
        hv.addWidget(self.history_table)
        self._load_history_from_json()

        tabs.addTab(opt_tab, "📋 عقود الاوبشن")
        tabs.addTab(open_tab, "📂 صفقات مفتوحة")
        tabs.addTab(hist_tab, "📜 تاريخ الصفقات")

        v.addWidget(tabs)
        return p

    # -----------------------------------------------
    # -----------------------------------------------
    def _build_right(self):
        p = QWidget(); p.setMinimumWidth(260)
        v = QVBoxLayout(p); v.setSpacing(5)

        # ═══ تحليل الاستراتيجية ══════════════════════════════════
        sg = QGroupBox("🧠 تحليل الاستراتيجية")
        sv = QVBoxLayout(); sv.setSpacing(4)

        # إشارة رئيسية
        self.strat_label = QLabel("انتظار إشارة")
        self.strat_label.setStyleSheet("color:#8395a7;font-size:15px;font-weight:bold;")
        self.strat_label.setAlignment(Qt.AlignCenter)
        sv.addWidget(self.strat_label)

        # Regime + Mode
        _r1 = QWidget(); _l1 = QHBoxLayout(_r1); _l1.setContentsMargins(0,0,0,0)
        self.regime_label = QLabel("Regime: --")
        self.regime_label.setStyleSheet("color:#8395a7;font-size:10px;font-weight:bold;")
        self.market_type_label = QLabel("Mode: --")
        self.market_type_label.setStyleSheet("color:#8395a7;font-size:10px;")
        _l1.addWidget(self.regime_label); _l1.addStretch(); _l1.addWidget(self.market_type_label)
        sv.addWidget(_r1)

        # ADX + RSI + Bias
        _r2 = QWidget(); _l2 = QHBoxLayout(_r2); _l2.setContentsMargins(0,0,0,0)
        self.adx_disp_label  = QLabel("ADX: --")
        self.adx_disp_label.setStyleSheet("color:#a29bfe;font-size:10px;font-weight:bold;")
        self.rsi_disp_label  = QLabel("RSI: --")
        self.rsi_disp_label.setStyleSheet("color:#0fbcf9;font-size:10px;font-weight:bold;")
        self.bias_disp_label = QLabel("Bias: --")
        self.bias_disp_label.setStyleSheet("color:#ffb62e;font-size:10px;font-weight:bold;")
        _l2.addWidget(self.adx_disp_label); _l2.addWidget(self.rsi_disp_label); _l2.addWidget(self.bias_disp_label)
        sv.addWidget(_r2)

        _sep = QFrame(); _sep.setFrameShape(QFrame.HLine); _sep.setStyleSheet("color:#1e2d3d;"); sv.addWidget(_sep)

        # CALL score
        _ch = QWidget(); _chl = QHBoxLayout(_ch); _chl.setContentsMargins(0,0,0,0)
        _chl.addWidget(self._lbl("CALL", "#05c46b", 10, True))
        self.call_score_lbl = QLabel("0 نقطة")
        self.call_score_lbl.setStyleSheet("color:#05c46b;font-size:10px;")
        _chl.addStretch(); _chl.addWidget(self.call_score_lbl)
        sv.addWidget(_ch)
        self.call_score_bar = QProgressBar()
        self.call_score_bar.setMaximum(100); self.call_score_bar.setValue(0)
        self.call_score_bar.setMaximumHeight(8); self.call_score_bar.setTextVisible(False)
        self.call_score_bar.setStyleSheet("QProgressBar{background:#1e2d3d;border-radius:4px;}QProgressBar::chunk{background:#05c46b;border-radius:4px;}")
        sv.addWidget(self.call_score_bar)
        self.call_why_label = QLabel("--")
        self.call_why_label.setStyleSheet("color:#05c46b;font-size:9px;"); self.call_why_label.setWordWrap(True)
        sv.addWidget(self.call_why_label)

        # PUT score
        _ph = QWidget(); _phl = QHBoxLayout(_ph); _phl.setContentsMargins(0,0,0,0)
        _phl.addWidget(self._lbl("PUT", "#ff5e57", 10, True))
        self.put_score_lbl = QLabel("0 نقطة")
        self.put_score_lbl.setStyleSheet("color:#ff5e57;font-size:10px;")
        _phl.addStretch(); _phl.addWidget(self.put_score_lbl)
        sv.addWidget(_ph)
        self.put_score_bar = QProgressBar()
        self.put_score_bar.setMaximum(100); self.put_score_bar.setValue(0)
        self.put_score_bar.setMaximumHeight(8); self.put_score_bar.setTextVisible(False)
        self.put_score_bar.setStyleSheet("QProgressBar{background:#1e2d3d;border-radius:4px;}QProgressBar::chunk{background:#ff5e57;border-radius:4px;}")
        sv.addWidget(self.put_score_bar)
        self.put_why_label = QLabel("--")
        self.put_why_label.setStyleSheet("color:#ff5e57;font-size:9px;"); self.put_why_label.setWordWrap(True)
        sv.addWidget(self.put_why_label)

        _sep2 = QFrame(); _sep2.setFrameShape(QFrame.HLine); _sep2.setStyleSheet("color:#1e2d3d;"); sv.addWidget(_sep2)

        # EMA + ATR + RSI + Why
        _r3 = QWidget(); _l3 = QHBoxLayout(_r3); _l3.setContentsMargins(0,0,0,0)
        self.ema9_label  = QLabel("EMA9: --");  self.ema9_label.setStyleSheet("color:#8395a7;font-size:9px;")
        self.ema21_label = QLabel("EMA21: --"); self.ema21_label.setStyleSheet("color:#8395a7;font-size:9px;")
        _l3.addWidget(self.ema9_label); _l3.addWidget(self.ema21_label)
        sv.addWidget(_r3)
        _r4 = QWidget(); _l4 = QHBoxLayout(_r4); _l4.setContentsMargins(0,0,0,0)
        self.atr_label = QLabel("ATR: --"); self.atr_label.setStyleSheet("color:#8395a7;font-size:9px;")
        self.rsi_label = QLabel("RSI: --"); self.rsi_label.setStyleSheet("color:#0fbcf9;font-size:9px;")
        _l4.addWidget(self.atr_label); _l4.addStretch(); _l4.addWidget(self.rsi_label)
        sv.addWidget(_r4)
        self.why_label = QLabel("")
        self.why_label.setStyleSheet("color:#8395a7;font-size:9px;"); self.why_label.setWordWrap(True)
        sv.addWidget(self.why_label)

        _sep3 = QFrame(); _sep3.setFrameShape(QFrame.HLine); _sep3.setStyleSheet("color:#1e2d3d;"); sv.addWidget(_sep3)

        # Greeks
        _gk_title = QLabel("📊 Greeks العقد")
        _gk_title.setStyleSheet("color:#a29bfe;font-size:10px;font-weight:bold;")
        sv.addWidget(_gk_title)
        _gk1 = QWidget(); _gl1 = QHBoxLayout(_gk1); _gl1.setContentsMargins(0,0,0,0)
        self.greeks_delta = QLabel("Δ Delta: --"); self.greeks_delta.setStyleSheet("color:#05c46b;font-size:10px;font-weight:bold;")
        self.greeks_gamma = QLabel("Γ Gamma: --"); self.greeks_gamma.setStyleSheet("color:#0fbcf9;font-size:10px;font-weight:bold;")
        _gl1.addWidget(self.greeks_delta); _gl1.addStretch(); _gl1.addWidget(self.greeks_gamma)
        sv.addWidget(_gk1)
        _gk2 = QWidget(); _gl2 = QHBoxLayout(_gk2); _gl2.setContentsMargins(0,0,0,0)
        self.greeks_theta = QLabel("Θ Theta: --"); self.greeks_theta.setStyleSheet("color:#ff5e57;font-size:10px;font-weight:bold;")
        self.greeks_vega  = QLabel("V Vega: --");  self.greeks_vega.setStyleSheet("color:#ffb62e;font-size:10px;font-weight:bold;")
        _gl2.addWidget(self.greeks_theta); _gl2.addStretch(); _gl2.addWidget(self.greeks_vega)
        sv.addWidget(_gk2)
        _gk3 = QWidget(); _gl3 = QHBoxLayout(_gk3); _gl3.setContentsMargins(0,0,0,0)
        self.greeks_iv  = QLabel("IV: --");  self.greeks_iv.setStyleSheet("color:#a29bfe;font-size:10px;font-weight:bold;")
        self.greeks_dte = QLabel("DTE: --"); self.greeks_dte.setStyleSheet("color:#8395a7;font-size:10px;font-weight:bold;")
        self.greeks_verdict = QLabel(""); self.greeks_verdict.setStyleSheet("color:#05c46b;font-size:9px;")
        _gl3.addWidget(self.greeks_iv); _gl3.addWidget(self.greeks_dte); _gl3.addStretch(); _gl3.addWidget(self.greeks_verdict)
        sv.addWidget(_gk3)

        sg.setLayout(sv); v.addWidget(sg)

        # ═══ إشارات البوت ════════════════════════════════════════
        # تمتد للأسفل وتملأ المساحة فوق التحذير
        ag = QGroupBox("📡 إشارات البوت")
        av = QVBoxLayout()
        av.setContentsMargins(8, 8, 8, 8)
        av.setSpacing(4)

        self.signals_list = QListWidget()
        self.signals_list.setMinimumHeight(260)
        self.signals_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        av.addWidget(self.signals_list)
        ag.setLayout(av)
        ag.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v.addWidget(ag, 1)

        # ═══ تحذير ════════════════════════════════════════════════
        # ثابت أسفل الزاوية اليمنى
        wg = QGroupBox("⚠️ تحذير")
        wg.setMaximumHeight(92)
        wg.setMinimumHeight(78)
        wg.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        wv = QVBoxLayout()
        wv.setContentsMargins(8, 6, 8, 6)
        wv.setSpacing(0)

        wl = QLabel("التداول التلقائي ينطوي على مخاطر حقيقية.\nالاستراتيجية لا تضمن الربح.\nاختبر أولاً بحساب Paper.")
        wl.setStyleSheet("color:#ff5e57;font-size:10px;")
        wl.setWordWrap(True)
        wl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        wv.addWidget(wl)
        wg.setLayout(wv)
        v.addWidget(wg, 0)

        scroll = QScrollArea()
        scroll.setWidget(p)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setMinimumWidth(260)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:#111a26;}"
            "QScrollArea > QWidget{background:#111a26;}")
        return scroll

    def connect_ibkr(self):
        # [PROFILING]
        self._prof_last_action = 'connect_ibkr'
        self.statusBar().showMessage("جاري الاتصال بـ IBKR...")
        self.connect_btn.setEnabled(False)
        def _run():
            ports = [7496, 7497, 4001, 4002]
            connected_port = None
            last_err = None
            for port in ports:
                try:
                    self.ui_updater.show_status.emit(f"جاري الاتصال على port {port}...")
                    run_in_ib_thread(self.ib.connect, '127.0.0.1', port,
                                     clientId=self._client_id, readonly=False, timeout=10)
                    connected_port = port
                    break
                except Exception as e:
                    last_err = e
                    try: run_in_ib_thread(self.ib.disconnect)
                    except Exception: pass
                    time.sleep(0.5)

            if not connected_port:
                self.ui_updater.show_status.emit(
                    f"فشل الاتصال — تأكد أن TWS أو IB Gateway شغّال\n({last_err})")
                QMetaObject.invokeMethod(self.connect_btn, "setEnabled",
                                         Qt.QueuedConnection, Q_ARG(bool, True))
                return

            # ── كتم أخطاء IBKR غير الحرجة ─────────────────────────────
            _SUPPRESS = {10092, 10167, 10168, 354, 300, 2157, 2158, 2104, 2106, 2108, 2109}
            def _ib_error_handler(reqId, errorCode, errorString, contract):
                if errorCode in _SUPPRESS:
                    return
                if errorCode in (10091, 10197):
                    return
                if errorCode >= 1000:
                    print(f"[IB] Error {errorCode}, reqId {reqId}: {errorString[:80]}")
            try:
                # ✅ إصلاح: لا lambda في run_in_ib_thread (يسبب timeout)
                self.ib.errorEvent.connect(_ib_error_handler)
            except Exception:
                pass

            # ── تعيين run_in_ib_thread للمحلل فوراً بعد الاتصال ───────
            try:
                import execution as _exec_mod_conn
                _exec_mod_conn.set_ib_thread_fn(run_in_ib_thread)
            except Exception as _ma_e:
                print(f"[IBKR] تعيين IB_THREAD_FN: {_ma_e}")

            # ── إعداد ما بعد الاتصال ───────────────────────────────────
            try:
                self.connected        = True
                self._connected_port  = connected_port
                _is_paper             = connected_port in (7497, 4002)
                self._paper_mode      = _is_paper
                _mode_label      = "📄 PAPER" if _is_paper else "🔴 LIVE"
                print(f"[IBKR] متصل على port {connected_port} — وضع: {_mode_label}")
                self.ui_updater.show_status.emit(f"✅ متصل — {_mode_label} (port {connected_port})")

                # ── حارس الأمان: Live Trading Guard ────────────────────
                _dry_run_active = False
                try:
                    _exec_cfg = getattr(getattr(self, '_exec_engine', None), 'cfg', None)
                    if _exec_cfg is not None:
                        _dry_run_active = bool(_exec_cfg.dry_run)
                except Exception:
                    pass
                if not _dry_run_active and connected_port != 7496:
                    _msg = (
                        f"⛔ رفض التشغيل الحقيقي: dry_run=False لكن المنفذ ليس 7496 "
                        f"(متصل على port {connected_port} — {_mode_label})"
                    )
                    print(f"[SAFETY] {_msg}")
                    self.ui_updater.show_status.emit(_msg)
                    # تعطيل ExecutionEngine إذا كان موجوداً
                    try:
                        _eng = getattr(self, '_exec_engine', None)
                        if _eng and hasattr(_eng, 'safety'):
                            _eng.safety.trigger_emergency_stop(
                                f"port={connected_port} ≠ 7496 مع dry_run=False"
                            )
                    except Exception:
                        pass
                # ───────────────────────────────────────────────────────

                try:
                    run_in_ib_thread(self.ib.reqMarketDataType, 3)
                except Exception:
                    pass

                # جلب الحساب — retry حتى 15 ثانية
                try:
                    accounts = []
                    for _attempt in range(15):
                        accounts = run_in_ib_thread(self.ib.managedAccounts)
                        if accounts:
                            break
                        print(f"[IBKR] انتظار managedAccounts... ({_attempt+1}/15)")
                        time.sleep(1.0)
                    if accounts:
                        self.account = accounts[0]
                        print(f"[IBKR] حساب: {self.account}")
                    else:
                        print("[IBKR] ⚠️ managedAccounts فارغ بعد 15 ثانية")
                except Exception as _ae:
                    print(f"[IBKR] خطأ جلب الحساب: {_ae}")

                # جلب الرصيد — NetLiquidation هو القيمة الكاملة للمحفظة
                if self.account:
                    try:
                        _bal_found = False
                        for item in run_in_ib_thread(self.ib.accountSummary, self.account):
                            if item.tag == 'NetLiquidation':
                                self.ui_updater.update_cash.emit(float(item.value))
                                _bal_found = True
                                break
                        # fallback: TotalCashValue إذا لم يُجد NetLiquidation
                        if not _bal_found:
                            for item in run_in_ib_thread(self.ib.accountSummary, self.account):
                                if item.tag == 'TotalCashValue':
                                    self.ui_updater.update_cash.emit(float(item.value))
                                    break
                    except Exception:
                        pass

            except Exception as e:
                print(f"[IBKR] خطأ بعد الاتصال: {e}")
                import traceback; traceback.print_exc()
                # حتى لو فشل جزء — الاتصال نجح → نُفعّل الواجهة
                self.connected = True

            # ✅ استدعاء _on_connected دائماً بعد الاتصال الناجح
            QMetaObject.invokeMethod(self, "_on_connected", Qt.QueuedConnection)
            time.sleep(0.5)
            try:
                self._load_symbol(self.current_symbol)
            except Exception as _le:
                print(f"[IBKR] خطأ تحميل الرمز: {_le}")

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot()
    def _on_connected(self):
        # ✅ إلغاء أي أوامر معلقة من جلسة سابقة
        try:
            import threading as _thr_c
            def _cancel_stale():
                import time as _tc
                _tc.sleep(1.0)  # انتظر استقرار الاتصال
                try:
                    _open = run_in_ib_thread(self.ib.openOrders)
                    if _open:
                        for _o in _open:
                            try:
                                run_in_ib_thread(self.ib.cancelOrder, _o.order)
                                print(f"[Connect] 🗑 أُلغي أمر قديم: {_o.order.orderId} {getattr(getattr(_o,'contract',None),'symbol','?')}")
                            except Exception: pass
                        print(f"[Connect] ✅ أُلغيت {len(_open)} أوامر معلقة")
                except Exception as _ce:
                    print(f"[Connect] cancelStale error: {_ce}")
            _thr_c.Thread(target=_cancel_stale, daemon=True).start()
        except Exception: pass

        _bot_was_running = (hasattr(self, 'auto_bot') and
                            self.auto_bot and self.auto_bot.isRunning())
        if _bot_was_running:
            print('[Bot] Reconnect — البوت لا يزال شغالاً، لا إعادة تشغيل')
        # ابدأ بـ Live ثم Delayed-Frozen كـ fallback تلقائي
        try:
            run_in_ib_thread(self.ib.reqMarketDataType, 1)
            import time as _t; _t.sleep(0.2)
            run_in_ib_thread(self.ib.reqMarketDataType, 4)
        except Exception:
            try: run_in_ib_thread(self.ib.reqMarketDataType, 3)
            except Exception: pass
        self.status_label.setText("● متصل")
        self.status_label.setStyleSheet("color:#05c46b; font-weight:bold;")
        self.connect_btn.setText(f"✅ متصل (ID:{self._client_id})")
        self.connect_btn.setEnabled(False)
        self.bot_btn.setEnabled(True)
        self.bot_btn.setStyleSheet(
            "background:#05c46b; color:#0a1628; font-size:13px; font-weight:bold; padding:10px;")
        self.bot_status_label.setText("⏸ جاهز — اضغط للتشغيل")
        # عنوان النافذة
        _is_paper = getattr(self, '_paper_mode', False)
        _suffix   = " [📄 PAPER TRADING]" if _is_paper else " [🔴 LIVE TRADING]"
        self.setWindowTitle(f"IBKR Options Bot{_suffix}")
        self.statusBar().showMessage("✅ تم الاتصال — البوت جاهز للتشغيل")
        # ── تحميل الشارت فوراً عند الاتصال ──────────────────────────────
        self._last_full_chart_refresh = None
        sym = getattr(self, 'current_symbol', 'SPY')
        threading.Thread(target=self._fetch_chart, args=(sym,), daemon=True).start()
        # ── مزامنة الصفقات المفتوحة من IBKR ─────────────────────────────
        threading.Thread(target=self._sync_ibkr_positions, daemon=True).start()
        # ── جلب الرصيد بعد الاتصال (مع تأخير للاستقرار) ─────────────────
        threading.Thread(target=self._fetch_balance_on_connect, daemon=True).start()

    def _fetch_balance_on_connect(self):
        """
        يجلب الرصيد من IBKR — يدعم TWS (7496/7497) و IB Gateway (4001/4002).
        IB Gateway أبطأ من TWS ويحتاج reqAccountSummary صريح كـ fallback.
        """
        import asyncio as _aio

        acct        = getattr(self, 'account', '') or ''
        conn_port   = getattr(self, '_connected_port', 0)
        is_gateway  = conn_port in (4001, 4002)
        wait_init   = 3.0 if is_gateway else 2.0   # Gateway يحتاج وقت أطول للاستقرار
        wait_after  = 5.0 if is_gateway else 3.0

        async def _bal_coro():
            import asyncio as _a2

            # ① تأكّد من الحساب
            _acct = acct
            try:
                if not _acct:
                    accts = self.ib.managedAccounts()
                    if accts:
                        _acct = accts[0]
                        self.account = _acct
                print(f"[Balance] port={conn_port} acct={_acct or '?'} gateway={is_gateway}")
            except Exception as _e:
                print(f"[Balance] managedAccounts: {_e}")

            await _a2.sleep(wait_init)

            # ② subscribe: reqAccountUpdates(account='') — يأخذ account string فقط
            try:
                self.ib.reqAccountUpdates(_acct)
                print(f"[Balance] reqAccountUpdates('{_acct}') ✓")
            except Exception as _e:
                print(f"[Balance] reqAccountUpdates: {_e}")

            await _a2.sleep(wait_after)

            # ③ دالة استخراج الرصيد
            def _extract(vals):
                nl = tc = af = None
                for v in (vals or []):
                    tag = getattr(v, 'tag', '')
                    cur = getattr(v, 'currency', 'USD')
                    val = _safe_float(getattr(v, 'value', 0))
                    if cur and cur not in ('USD', 'BASE', ''):
                        continue
                    if tag == 'NetLiquidation' and val > 0: nl = val
                    if tag == 'TotalCashValue'  and val > 0: tc = val
                    if tag == 'AvailableFunds'  and val > 0: af = val
                return nl or tc or af

            # ④ محاولة accountValues (مُعبأ بـ reqAccountUpdates)
            bal = _extract(self.ib.accountValues(_acct))
            if bal and bal > 0:
                print(f"[Balance] accountValues ✓")
                return bal

            # ⑤ IB Gateway: reqAccountSummary صريح (مختلف عن accountSummary())
            if is_gateway:
                try:
                    print("[Balance] Gateway fallback: reqAccountSummary...")
                    req_id = self.ib.client.getReqId()
                    tags   = ("NetLiquidation,TotalCashValue,AvailableFunds"
                              ",GrossPositionValue,BuyingPower")
                    self.ib.client.reqAccountSummary(req_id, "All", tags)
                    await _a2.sleep(4.0)
                    bal = _extract(self.ib.accountSummary(_acct) if _acct
                                   else self.ib.accountSummary())
                    if bal and bal > 0:
                        print(f"[Balance] reqAccountSummary ✓")
                        return bal
                except Exception as _e:
                    print(f"[Balance] reqAccountSummary fallback: {_e}")

            # ⑥ accountSummary من الـcache (TWS عادةً)
            try:
                items = self.ib.accountSummary(_acct) if _acct \
                        else self.ib.accountSummary()
                bal = _extract(items)
                if bal and bal > 0:
                    print(f"[Balance] accountSummary cache ✓")
                    return bal
            except Exception:
                pass

            # ⑦ انتظار أخير وإعادة المحاولة
            await _a2.sleep(5.0)
            bal = _extract(self.ib.accountValues())
            if bal:
                print(f"[Balance] retry accountValues ✓")
            else:
                print("[Balance] ⚠️ فارغ — تأكد: IB Gateway → Configure → API → Read-Only=OFF")
            return bal

        # شغّل الـcoroutine داخل IB event loop
        loop = get_ib_loop()
        future = _aio.run_coroutine_threadsafe(_bal_coro(), loop)
        try:
            bal = future.result(timeout=40)
            if bal and bal > 0:
                self.account_balance = bal
                self.ui_updater.update_cash.emit(bal)
                print(f"[Balance] ✅ ${bal:,.2f}")
            else:
                print("[Balance] ⚠️ لم يُجلب الرصيد — تحقق من إعدادات IB Gateway API")
        except Exception as _e:
            print(f"[Balance] خطأ: {_e}")

    def _sync_ibkr_positions(self):
        """
        يجلب الصفقات المفتوحة من IBKR عند الاتصال ويعرضها في جدول Live.
        يُشغَّل مرة واحدة بعد الاتصال.
        """
        import time as _ts2
        _ts2.sleep(2.0)  # انتظر استقرار الاتصال
        try:
            _pf = run_in_ib_thread(self.ib.portfolio)
            if not _pf:
                return
            from datetime import datetime as _dt_sync
            _now_str = _dt_sync.now().strftime("%Y-%m-%d %H:%M:%S")
            _loaded = 0
            for _item in _pf:
                try:
                    _c   = getattr(_item, 'contract', None)
                    _pos = getattr(_item, 'position', 0)
                    if not _c or not _pos or _pos <= 0:
                        continue
                    # فقط أوبشن (نوع الأصل = OPT)
                    if getattr(_c, 'secType', '') not in ('OPT',):
                        continue

                    _sym    = getattr(_c, 'symbol', '')
                    _right  = getattr(_c, 'right', '')
                    _strike = float(getattr(_c, 'strike', 0) or 0)
                    _expiry = getattr(_c, 'lastTradeDateOrContractMonth', '') or getattr(_c, 'lastTradeDate', '')
                    _avg    = float(getattr(_item, 'averageCost', 0) or 0)
                    _mkt    = float(getattr(_item, 'marketPrice', 0) or 0)
                    _unreal = float(getattr(_item, 'unrealizedPNL', 0) or 0)

                    # احسب entry_premium من averageCost
                    if _avg > 10.0:
                        _entry = round(_avg / 100.0, 4)
                    elif _avg > 0:
                        _entry = round(_avg, 4)
                    else:
                        _entry = 0

                    if _entry <= 0:
                        continue

                    # احسب current price من marketPrice
                    if _mkt > 0:
                        _current = round(_mkt, 4)
                    else:
                        _current = _entry

                    _opt_type = 'CALL' if _right == 'C' else 'PUT'
                    _expiry_fmt = _expiry[:8] if len(_expiry) >= 8 else _expiry

                    # بناء trade_info
                    _tid = f"{_sym}_{_opt_type}_{_expiry_fmt}_{int(_strike)}_IBKR"

                    # لا تضيف إذا موجود بالفعل
                    if self.position_manager.get(_tid):
                        continue

                    # SL/TP افتراضي
                    _sl_pct = 0.75 if _sym in ('XSP','SPX','NDX') else 0.65
                    _tp_pct = 1.20 if _sym in ('XSP','SPX','NDX') else 1.25

                    _trade_info = {
                        'id':             _tid,
                        'symbol':         _sym,
                        'opt_type':       _opt_type,
                        'spread_type':    '',
                        'strike':         _strike,
                        'expiry':         _expiry_fmt,
                        'entry_premium':  _entry,
                        'stop_loss':      round(_entry * _sl_pct, 2),
                        'take_profit':    round(_entry * _tp_pct, 2),
                        'highest':        max(_entry, _current),
                        'contracts':      int(_pos),
                        'cost':           round(_entry * 100 * int(_pos), 2),
                        'score':          0,
                        'why':            'IBKR sync',
                        'strategy_type':  'Index' if _sym in ('XSP','SPX','NDX') else 'Stock',
                        'time':           _dt_sync.now().strftime("%H:%M:%S"),
                        'entry_datetime': _now_str,
                        'status':         'open',
                        'tp_phase':       0,
                        'take_profit_2':  round(_entry * _tp_pct * 1.20, 2),
                        'opt_contract':   _c,
                        'long_contract':  None,
                        'is_spread':      False,
                        'spread_data':    None,
                        'regime':         'normal',
                        'tp_ratio':       1.8,
                        'entry_stock_price': 0,
                        '_entry_synced':  True,
                        '_from_ibkr':     True,
                    }

                    self.position_manager.add(_tid, _trade_info)
                    # لا نستدعي risk_manager.register للصفقات اليدوية من IBKR
                    # حتى لا تمنع البوت من فتح صفقات جديدة
                    # self.risk_manager.register(_trade_info)
                    # أضف للجدول عبر signal
                    self.signal_new_trade_ui.emit(_trade_info)
                    _loaded += 1
                    print(f"[Sync] ✅ {_sym} {_opt_type} {_strike} entry=${_entry:.4f} ×{int(_pos)}")

                except Exception as _ie:
                    print(f"[Sync] خطأ: {_ie}")

            if _loaded > 0:
                print(f"[Sync] ✅ تم تحميل {_loaded} صفقة من IBKR")
            else:
                print("[Sync] لا صفقات أوبشن مفتوحة في IBKR")
        except Exception as _se:
            print(f"[Sync] خطأ عام: {_se}")

    # -----------------------------------------------
    # البوت
    # -----------------------------------------------
    def _on_datafeed_stopped(self, err_msg: str):
        """يُستدعى في main thread عند توقف DataFeed"""
        self.datafeed_btn.setText("▶ تشغيل DataFeed")
        self.datafeed_btn.setStyleSheet(
            "background:#0fbcf9;color:#0a1628;font-size:12px;font-weight:bold;padding:8px;")
        self.datafeed_status_lbl.setText("⚠️ توقف — أعد التشغيل")
        self.datafeed_status_lbl.setStyleSheet("color:#ff5e57;font-size:10px;")
        self.datafeed_cycle_lbl.setText("")
        QMessageBox.warning(self, '⚠️ DataFeed توقف',
            f'توقف tv_datafeed.py.\n\nالسبب:\n{err_msg}\n\n'
            'تأكد من:\n'
            '1. pip install websocket-client\n'
            '2. config.txt يحتوي username/password\n'
            '3. الاتصال بالإنترنت')

    def _on_datafeed_exited(self):
        """يُستدعى في main thread عندما يخرج DataFeed thread بشكل طبيعي."""
        self._datafeed_thread     = None
        self._datafeed_stop_event = None
        self.datafeed_btn.setText("▶ تشغيل DataFeed")
        self.datafeed_btn.setStyleSheet(
            "background:#0fbcf9;color:#0a1628;font-size:12px;font-weight:bold;padding:8px;")
        self.datafeed_status_lbl.setText("⏸ متوقف")
        self.datafeed_status_lbl.setStyleSheet("color:#8395a7;font-size:10px;")
        self.datafeed_cycle_lbl.setText("")

    def toggle_datafeed(self):
        """تشغيل/إيقاف DataFeed كـ Thread داخلي — بدون عمليات خارجية"""
        import threading as _thr

        _stop_ev = getattr(self, '_datafeed_stop_event', None)
        _thread  = getattr(self, '_datafeed_thread', None)

        # ── إيقاف ────────────────────────────────────────────────
        if _thread is not None and _thread.is_alive():
            if _stop_ev:
                _stop_ev.set()
            _tv = getattr(self, '_datafeed_tv', None)
            if _tv is not None:
                try:
                    _tv.close_active()
                except Exception:
                    pass
            _thread.join(timeout=3)
            if not _thread.is_alive():
                # Thread exited cleanly — safe to clear refs
                self._datafeed_thread     = None
                self._datafeed_stop_event = None
                self.datafeed_btn.setText("▶ تشغيل DataFeed")
                self.datafeed_btn.setStyleSheet(
                    "background:#0fbcf9;color:#0a1628;font-size:12px;font-weight:bold;padding:8px;")
                self.datafeed_status_lbl.setText("⏸ متوقف")
                self.datafeed_status_lbl.setStyleSheet("color:#8395a7;font-size:10px;")
                self.datafeed_cycle_lbl.setText("")
            else:
                # Thread still alive after join — keep ref so start is blocked
                print("[DataFeed] stop signal sent — thread still running, start blocked")
                self.datafeed_status_lbl.setText("⏳ جاري الإيقاف...")
                self.datafeed_status_lbl.setStyleSheet("color:#f9ca24;font-size:10px;")
            return

        # ── تشغيل ────────────────────────────────────────────────
        # Guard: refuse to start while a previous thread is still alive
        if _thread is not None and _thread.is_alive():
            print("[DataFeed] start refused — previous thread still alive")
            return

        interval = self.datafeed_interval_spin.value()

        syms_to_fetch = list(getattr(self, "auto_bot_scan_symbols", X1_SCAN_SYMBOLS))

        stop_event = _thr.Event()
        self._datafeed_stop_event = stop_event

        def _run_datafeed():
            try:
                from tv_datafeed import TVDataFeed
                import time as _t, os as _os, json as _json

                tv   = TVDataFeed()
                self._datafeed_tv = tv
                tfs  = ["5", "15", "60"]
                bars = 500

                _iv_path   = _os.path.join(_app_dir(), "iv_cache.json")
                iv_history = {}
                if _os.path.exists(_iv_path):
                    try:
                        with open(_iv_path) as _f:
                            iv_history = _json.load(_f).get("iv_history", {})
                    except Exception:
                        pass

                _DAILY = {"1D","1d"}
                _ptfs  = [t for t in tfs if t not in _DAILY]
                cycle  = 0

                while not stop_event.is_set():
                    cycle += 1
                    try:
                        if cycle == 1:
                            tv.fetch_all(symbols=syms_to_fetch,
                                         timeframes=tfs, bars_override=bars,
                                         stop_event=stop_event)
                        else:
                            tv.fetch_partial(symbols=syms_to_fetch,
                                             timeframes=_ptfs,
                                             partial_bars=50, keep=2000,
                                             stop_event=stop_event)
                        if not stop_event.is_set() and cycle % 4 == 1:
                            iv_now = tv.get_iv_all(syms_to_fetch, stop_event=stop_event)
                            if not stop_event.is_set():
                                for s, v in iv_now.items():
                                    iv_history.setdefault(s, []).append(v)
                                    iv_history[s] = iv_history[s][-260:]
                                tv.save_iv_cache(iv_now, iv_history)
                    except Exception:
                        pass
                    stop_event.wait(timeout=interval)

            except Exception as _e:
                try:
                    self.signal_datafeed_stopped.emit(str(_e))
                except Exception:
                    pass
            finally:
                self._datafeed_tv = None
                try:
                    self.signal_datafeed_exited.emit()
                except Exception:
                    pass

        self._datafeed_thread = _thr.Thread(
            target=_run_datafeed, daemon=True, name="DataFeed-Manual")
        self._datafeed_thread.start()

        self.datafeed_btn.setText("⏹ إيقاف DataFeed")
        self.datafeed_btn.setStyleSheet(
            "background:#ff5e57;color:#fff;font-size:12px;font-weight:bold;padding:8px;")
        self.datafeed_status_lbl.setText(f"🟢 يعمل — تحديث كل {interval}ث")
        self.datafeed_status_lbl.setStyleSheet(
            "color:#05c46b;font-size:10px;font-weight:bold;")
        self.datafeed_cycle_lbl.setText(f"{len(syms_to_fetch)} رمز")

    def _on_contracts_changed(self, value: int):
        """يُحدّث عدد العقود في كل مكان فور تغييره من الواجهة."""
        self._contracts_override = value
        engine = getattr(self, '_exec_engine', None)
        if engine is not None:
            engine._max_contracts_override = value
            # execution.py — ExecCfg.max_contracts
            cfg = getattr(engine, 'cfg', None)
            if cfg is not None:
                try: cfg.max_contracts = value
                except Exception: pass
            # RiskManager
            rm = getattr(engine, 'risk_manager', None) or getattr(engine, '_risk_mgr', None)
            if rm is not None:
                rm._max_contracts_override = value
        self._on_bot_scan_signal(f"📋 عدد العقود: {value} عقد لكل صفقة")

    def toggle_bot(self):
        # ── guard ضد double-click ──────────────────────────
        if getattr(self, '_bot_toggling', False):
            return

        # ── تحقق من الاتصال قبل كل شيء ───────────────────
        if not self.connected or not self.ib:
            QMessageBox.warning(self, '⚠️ غير متصل',
                'يجب الاتصال بـ IBKR أولاً قبل تشغيل البوت.\n\n'
                'اضغط زر "اتصال بـ IBKR" في الأعلى.')
            return

        if not self.account:
            QMessageBox.warning(self, '⚠️ لا يوجد حساب',
                'لم يُحدَّد حساب IBKR بعد.\n'
                'تأكد من اكتمال الاتصال.')
            return

        self._bot_toggling = True
        self.bot_btn.setEnabled(False)
        try:
            self._do_toggle_bot()
        except Exception as _te:
            import traceback; traceback.print_exc()
            self.bot_status_label.setText(f"❌ خطأ: {str(_te)[:60]}")
            QMessageBox.critical(self, '❌ خطأ في البوت',
                f'حدث خطأ أثناء تشغيل البوت:\n{str(_te)[:200]}')
        finally:
            self._bot_toggling = False
            _stopping = getattr(self, '_bot_stopping_async', False)
            if not _stopping:
                self.bot_btn.setEnabled(True)

    @staticmethod
    def _get_execution_symbol(analysis_symbol: str) -> str:
        """
        SPY (التحليل) →
            ساعات رسمية  9:30-16:00 ET : ينفذ على SPX
            خارج الساعات              : ينفذ على XSP
        بقية الرموز → بدون تغيير
        """
        from datetime import datetime, timezone
        if analysis_symbol.upper() != "SPY":
            return analysis_symbol.upper()
        now = datetime.now(timezone.utc)
        minutes = now.hour * 60 + now.minute
        if 13 * 60 + 30 <= minutes < 20 * 60:
            return "SPX"
        return "XSP"

    def _on_analyzer_trade_signal(self, symbol: str, direction: str, pct: int):
        """يستقبل trade_signal من المحلل في main thread ويُنفذ في thread منفصل."""
        # [PROFILING]
        self._prof_last_action = f'_on_analyzer_trade_signal({symbol},{direction})'
        import time as _time

        # تحديد رمز التنفيذ
        exec_symbol = self._get_execution_symbol(symbol)
        if exec_symbol != symbol:
            self._safe_log(
                f"🔀 تحليل={symbol} → تنفيذ={exec_symbol} "
                f"({'ساعات رسمية SPX' if exec_symbol == 'SPX' else 'خارج الساعات XSP'})"
            )

        # Lock + cooldown لمنع التكرار
        if not hasattr(self, '_signal_lock'):
            import threading as _thr
            self._signal_lock = _thr.Lock()
            self._signal_cooldown: dict = {}

        with self._signal_lock:
            last_t = self._signal_cooldown.get(exec_symbol, 0)
            if _time.time() - last_t < 300:   # ✅ رُفع من 60 إلى 300 ثانية (5 دقائق)
                self._safe_log(
                    f"  ⏭ {exec_symbol} cooldown ({int(300-(_time.time()-last_t))}s)"
                )
                return
            self._signal_cooldown[exec_symbol] = _time.time()

        print(f"[SIGNAL] تحليل={symbol} تنفيذ={exec_symbol} {direction} {pct}%")
        self._safe_log(
            f"📨 إشارة: تحليل={symbol} | تنفيذ={exec_symbol} | {direction} {pct}%"
        )
        engine = getattr(self, '_exec_engine', None)
        if engine is None:
            self._safe_log("❌ ExecutionEngine غير مهيأ")
            return

        engine_open = getattr(engine, 'open_positions', {})
        bot_open_syms = {v.get('symbol','') for v in engine_open.values() if isinstance(v, dict)}
        if exec_symbol in bot_open_syms:
            self._safe_log(f"  ⏭ {exec_symbol} مفتوح بالفعل")
            return

        # نسخ cache الإشارة من رمز التحليل إلى رمز التنفيذ (SPY→SPX)
        if exec_symbol != symbol:
            _cache = getattr(self, '_analyzer_signal_cache', {})
            if symbol in _cache and exec_symbol not in _cache:
                _cache[exec_symbol] = dict(_cache[symbol])
                _cache[exec_symbol]['symbol'] = exec_symbol

        symbol = exec_symbol

        def _run():
            _bridge = getattr(self, '_smart_bridge', None)
            try:
                engine.set_log_fn(self._safe_log)

                # ── الرصيد مباشرة من account_balance ──────────
                bal = float(getattr(self, 'account_balance', 0.0) or 0.0)

                # إذا الرصيد = 0 حاول جلبه مباشرة من IBKR
                if bal <= 0:
                    try:
                        ib = getattr(self, 'ib', None)
                        if ib and getattr(ib, 'isConnected', lambda: False)():
                            vals = ib.accountValues()
                            for v in vals:
                                if v.tag in ('TotalCashValue', 'NetLiquidation', 'CashBalance') \
                                        and v.currency == 'USD':
                                    _b = float(v.value or 0)
                                    if _b > 0:
                                        bal = _b
                                        self.account_balance = bal
                                        break
                    except Exception:
                        pass

                if bal <= 0:
                    self._safe_log(
                        f"  ❌ {symbol}: رصيد = 0 — تحقق من اتصال IBKR وإعدادات الحساب"
                    )
                    # أزل الحجب من _active_signals حتى تتمكن إشارة قادمة من العمل
                    if _bridge:
                        _bridge._active_signals.pop(symbol, None)
                    return

                if bal > 0:
                    engine.balance = bal

                stats = engine.get_stats()
                self._safe_log(
                    f"  💰 رصيد=${bal:,.0f} | مفتوحة={stats['open_trades']} | "
                    f"يومي={stats['daily_trades']} | dry_run={engine.cfg.dry_run}"
                )

                # استرجع SL/TP من cache الإشارة الأخيرة
                _sig_cache = getattr(self, '_analyzer_signal_cache', {})
                _cached    = _sig_cache.get(symbol, {})
                _sl_price  = float(_cached.get('sl',   0) or 0)
                _tp1_price = float(_cached.get('tp1',  0) or 0)
                _tp2_price = float(_cached.get('tp2',  0) or 0)
                # underlying_price = سعر السهم وقت الإشارة (من البريدج)
                _entry_stk = float(
                    _cached.get('underlying_price', 0) or
                    _cached.get('entry_price', 0) or 0
                )

                self._safe_log(
                    f"  🚀 {symbol} {direction} {pct}% → execute_signal "
                    f"SL={_sl_price:.2f} TP1={_tp1_price:.2f} entry=${_entry_stk:.2f}"
                )

                trade_id = engine.execute_signal(
                    symbol, direction, pct, bal,
                    sl_price=_sl_price, tp1_price=_tp1_price,
                    tp2_price=_tp2_price, entry_stock_price=_entry_stk
                )
                print(f'[EXEC] نتيجة: {trade_id}')

                if trade_id:
                    self._safe_log(f"  ✅ نُفذت | ID={str(trade_id)[:8]}")
                    pos = engine.open_positions.get(trade_id, {})
                    if pos:
                        trade_info = {
                            'id':             trade_id,
                            'symbol':         pos.get('symbol', symbol),
                            'opt_type':       pos.get('opt_type', direction),
                            'strike':         pos.get('strike', 0),
                            'expiry':         pos.get('expiry', ''),
                            'entry_premium':  pos.get('entry_premium', 0),
                            'stop_loss':      pos.get('stop_loss', 0),
                            'take_profit':    pos.get('take_profit', 0),
                            'take_profit_2':  pos.get('take_profit_2', 0),
                            'highest':        pos.get('entry_premium', 0),
                            'contracts':      pos.get('contracts', 1),
                            'cost':           pos.get('cost', 0),
                            'time':           datetime.now().strftime('%H:%M:%S'),
                            'entry_datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'why':            f'{direction} {pct}%',
                            'score':          pct,
                            'strategy_type':  'ANALYZER',
                            'status':         'open',
                            'tp_phase':       0,
                            'opt_contract':   pos.get('opt_contract'),
                            'long_contract':  None,
                            'is_spread':      False,
                            'regime':         'normal',
                            'tp_ratio':       1.8,
                            'entry_stock_price': _entry_stk,
                            '_entry_synced':  True,
                        }
                        if self.auto_bot:
                            self.auto_bot.signal_new_trade.emit(trade_info)
                else:
                    reason = getattr(engine, "last_reject_reason", "") or "رُفضت"
                    self._safe_log(f"  ❌ {symbol}: {reason}")
                    # ← أزل الحجب حتى تتمكن إشارة قادمة من العمل بعد 5 دقائق
                    if _bridge:
                        _bridge._active_signals.pop(symbol, None)

            except Exception as e:
                import traceback
                self._safe_log(f"  ❌ {symbol}: استثناء: {e}")
                print(traceback.format_exc())
                if _bridge:
                    _bridge._active_signals.pop(symbol, None)

        import threading as _th
        _th.Thread(target=_run, daemon=True).start()

    def _do_toggle_bot(self):
        # [PROFILING]
        self._prof_last_action = '_do_toggle_bot:enter'
        _prof_dtb_t0 = time.perf_counter()
        # ── إيقاف ──────────────────────────────────────────
        if self.auto_bot and self.auto_bot.isRunning():
            self.bot_btn.setEnabled(False)
            self.bot_status_label.setText("⏳ جاري الإيقاف...")
            self.auto_bot.stop()
            if hasattr(self, 'monitor_thread') and self.monitor_thread:
                self.monitor_thread.stop()
            # ── إيقاف الجسر الذكي ──────────────────────────────
            if hasattr(self, '_smart_bridge') and self._smart_bridge:
                self._smart_bridge.stop()
                self._smart_bridge = None
            _bot_ref = self.auto_bot
            _mon_ref = getattr(self, 'monitor_thread', None)
            _cnt = [0]
            def _check():
                _cnt[0] += 1
                if not (_bot_ref and _bot_ref.isRunning()) and                    not (_mon_ref and _mon_ref.isRunning()):
                    self.auto_bot = None
                    self.monitor_thread = None
                    self.bot_status_label.setText("⏸ متوقف")
                    self.bot_btn.setText("▶ تشغيل البوت التلقائي")
                    self.bot_btn.setStyleSheet(
                        "background:#05c46b;color:#0a1628;font-size:13px;font-weight:bold;padding:10px;")
                    self.bot_btn.setEnabled(True)
                    self.statusBar().showMessage("البوت متوقف")
                    return
                if _cnt[0] >= 20:
                    for ref in [_bot_ref, _mon_ref]:
                        try:
                            if ref and ref.isRunning(): ref.terminate()
                        except: pass
                    self.auto_bot = None
                    self.monitor_thread = None
                    self.bot_btn.setEnabled(True)
                    return
                QTimer.singleShot(500, _check)
            QTimer.singleShot(300, _check)
            return

        # ── تشغيل ──────────────────────────────────────────
        _is_paper = getattr(self, '_paper_mode', False)
        _bal_str  = f"${self.account_balance:,.0f}" if self.account_balance else "غير محدد"
        _mode     = "📄 PAPER" if _is_paper else "🔴 LIVE"

        reply = QMessageBox.question(
            self, f"تشغيل البوت — {_mode}",
            f"الرصيد: {_bal_str}\n"
            f"حجم الصفقة: {self.risk_manager.cost_pct*100:.0f}%\n"
            f"حد الخسارة: {self.risk_manager.loss_pct*100:.0f}%\n\n"
            f"هل تريد البدء؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # ── ExecutionEngine ─────────────────────────────────
        from execution import ExecutionEngine, ExecutionConfig
        # اقرأ الإعدادات من الواجهة وحوّلها لـ fraction إذا لزم
        def _to_frac(v):
            v = float(v)
            return v / 100.0 if v > 1.0 else v

        _trade_pct = _to_frac(self.risk_manager.cost_pct)
        _loss_pct  = _to_frac(self.risk_manager.loss_pct)

        self._on_bot_scan_signal(
            f"⚙ إعدادات: حجم الصفقة={_trade_pct*100:.1f}% | "
            f"حد الخسارة={_loss_pct*100:.1f}% | "
            f"صفقات مفتوحة={self.risk_manager.max_open_trades}"
        )

        _ecfg = ExecutionConfig(
            dry_run                = False,
            min_signal_pct         = 36,    # يتوافق مع min_conf Group B (36%)
            trade_pct              = _trade_pct,
            daily_loss_pct         = _loss_pct,
            max_open_trades        = self.risk_manager.max_open_trades,
            max_daily_trades       = self.risk_manager.max_daily_trades,
            min_contract_cost      = 70.0,  # ✅ حد أدنى $70 للعقد
            max_contract_cost      = 160.0, # ✅ حد أقصى $160 للعقد
        )
        self._prof_last_action = '_do_toggle_bot:ExecutionEngine'
        self._exec_engine = ExecutionEngine(self.ib, _ecfg)

        # ✅ سجّل run_in_ib_thread
        import execution as _exec_mod
        _exec_mod.set_ib_thread_fn(run_in_ib_thread)

        # ✅ الرصيد مباشرة من account_balance الموجود في الواجهة
        _boot_bal = float(getattr(self, 'account_balance', 0.0) or 0.0)
        if _boot_bal > 0:
            self._exec_engine.balance = _boot_bal
            self._on_bot_scan_signal(f"💼 رصيد البوت: ${_boot_bal:,.0f}")

        # ── SmartDayTradingAnalyzer — المحلل الوحيد ─────────────
        # MarketAnalyzerEngine هنا هو واجهة التوافق الموجودة في smart_analyzer_bridge
        # تشغّل SmartDayTradingAnalyzer من analyzer.py مباشرة
        # In BC mode pass enable_live_bc so Grade A signals can reach execution.
        self._prof_last_action = '_do_toggle_bot:MarketAnalyzerEngine'
        _prof_mae_t0 = time.perf_counter()
        _analyzer = (
            MarketAnalyzerEngine(self.ib, enable_live_bc=ENABLE_LIVE_BC,
                                 enable_live_orb=ENABLE_LIVE_ORB)
            if ANALYZER_MODE == "BC" else MarketAnalyzerEngine(self.ib)
        )
        _analyzer.set_app(self, self._exec_engine)
        print(f'[PROF] MarketAnalyzerEngine+set_app: {(time.perf_counter()-_prof_mae_t0)*1000:.0f}ms', flush=True)
        self._prof_last_action = '_do_toggle_bot:set_app_done'
        self._on_bot_scan_signal("🧠 SmartDayTradingAnalyzer — المحلل الوحيد النشط")

        _syms = getattr(self, 'auto_bot_scan_symbols', None)
        if _syms:
            try: _analyzer.SCAN_SYMBOLS = list(_syms)
            except Exception: pass
        elif hasattr(_analyzer, 'SCAN_SYMBOLS'):
            try: self.auto_bot_scan_symbols = list(_analyzer.SCAN_SYMBOLS)
            except Exception: pass

        # ── AnalyzerSignalBot ────────────────────────────────────
        if not hasattr(self, '_last_analyzer_signal_key'):
            self._last_analyzer_signal_key = ""
            self._last_analyzer_signal_at = 0.0

        self.auto_bot = AnalyzerSignalBot(
            self.ib, self.account,
            self.risk_manager, self.position_manager,
            app=self
        )
        self.auto_bot._exec_engine = self._exec_engine
        _bal_now = float(getattr(self, 'account_balance', 0.0) or 0.0)
        if _bal_now > 0:
            self._exec_engine.balance = _bal_now
        self.auto_bot.analyzer = _analyzer

        # ── ربط الـ signals ──────────────────────────────────────
        _analyzer.log_msg.connect(self.auto_bot.signal_scan_update.emit)

        if not hasattr(self, '_analyzer_signal_cache'):
            self._analyzer_signal_cache = {}
        def _cache_signal(profile_dict):
            sym = profile_dict.get('symbol', '')
            if sym:
                self._analyzer_signal_cache[sym] = profile_dict
        _analyzer.profile_updated.connect(_cache_signal)
        _analyzer.trade_signal.connect(self._on_analyzer_trade_signal)

        self.auto_bot.signal_new_trade.connect(self._on_new_trade)
        self.auto_bot.signal_close_trade.connect(self._on_close_trade)
        self.auto_bot.signal_update_trade.connect(self._on_update_trade)
        self.auto_bot.signal_risk_alert.connect(self._on_risk_alert)
        self.auto_bot.signal_scan_update.connect(self.scan_label.setText)
        self.auto_bot.signal_scan_update.connect(self._on_bot_scan_signal)

        # ── مرجع موحّد للـ Trail Stop وإدارة الصفقات ────────────
        _analyzer._app = self
        self._market_analyzer = _analyzer   # يُستخدم في _on_new_trade و_on_close_trade

        self._prof_last_action = '_do_toggle_bot:analyzer.start'
        _prof_as_t0 = time.perf_counter()
        _analyzer.start()
        self.auto_bot.start()
        print(f'[PROF] analyzer.start+auto_bot.start: {(time.perf_counter()-_prof_as_t0)*1000:.0f}ms', flush=True)
        self._prof_last_action = '_do_toggle_bot:bots_started'
        # In BC mode expose the inner BCPaperBridge so _on_analyzer_trade_signal
        # can release _active_signals locks on execution failure or completion.
        self._smart_bridge = (
            _analyzer._bridge
            if ANALYZER_MODE == "BC" and getattr(_analyzer, "_bridge", None)
            else None  # X2 mode: no separate bridge -- all in _analyzer
        )

        # ── تشغيل محلل الأخبار — خارج UI thread لتجنب التجميد ──────────
        def _init_news_analyzer(app=self):
            try:
                from news_analyzer import get_news_analyzer
                _na = get_news_analyzer()
                _na.add_callback(lambda s: app._safe_log(s if isinstance(s, str) else s.to_signal()))
                _na.start()
                app._safe_log("📰 محلل الأخبار بدأ — يراقب قائمة X1 (14 رمز)")
            except Exception as _ne:
                app._safe_log(f"⚠️ محلل الأخبار: {_ne}")
        import threading as _thr_na
        _thr_na.Thread(target=_init_news_analyzer, daemon=True, name="NewsAnalyzerInit").start()

        # ── تشغيل tv_datafeed — مؤجّل لإتاحة رسم الـ UI أولاً ─────────
        QTimer.singleShot(0, self._start_tv_datafeed)

        self._prof_last_action = '_do_toggle_bot:MonitorThread'
        self.monitor_thread = MonitorThread(
            self.ib, self.position_manager,
            self.risk_manager, None, auto_execute=True
        )
        self.monitor_thread.signal_update.connect(self._on_update_trade)
        self.monitor_thread.signal_close.connect(self._on_close_trade)
        self.monitor_thread.signal_log.connect(self._on_bot_scan_signal)
        self.monitor_thread.start()

        self._on_bot_scan_signal(
            f"✅ البوت يعمل | محلل: SmartDayTradingAnalyzer | "
            f"العقد: ${self._exec_engine.cfg.min_contract_cost:.0f}-"
            f"${self._exec_engine.cfg.max_contract_cost:.0f} | Trail: 8% Ratchet"
        )

        self.bot_status_label.setText(f"▶ يعمل — {_mode}")
        self.bot_btn.setText("⏹ إيقاف البوت")
        self.bot_btn.setStyleSheet(
            "background:#ff5e57;color:white;font-size:13px;font-weight:bold;padding:10px;")
        self.statusBar().showMessage(f"🤖 البوت يعمل — {_mode}")
        print(f'[PROF] _do_toggle_bot total: {(time.perf_counter()-_prof_dtb_t0)*1000:.0f}ms', flush=True)
        self._prof_last_action = '_do_toggle_bot:done'
    def _open_backtest(self):
        """فتح نافذة الباك‑تست"""
        dlg = PerformanceDashboard(self)
        dlg.exec_()

    def _update_regime_display(self):
        """تحديث عرض النظام التكيفي في الواجهة"""
        regime = getattr(self.strategy, 'current_regime', 'normal')
        params = self.strategy.get_params() if hasattr(self.strategy, 'get_params') else {}
        regime_map = {
            'trending': ('📈 Trending — اتجاه قوي',  '#0fbcf9'),
            'volatile': ('🌪 Volatile — تذبذب عالٍ', '#ffb62e'),
            'normal':   ('⚖ Normal — طبيعي',         '#05c46b'),
            'range':    ('↔ Range — عرضي',            '#a29bfe'),
            'choppy':   ('💤 Choppy — ضعيف',          '#ff5e57'),
        }
        text, color = regime_map.get(regime, ('⏳ انتظار', '#8395a7'))
        if hasattr(self, 'regime_lbl'):
            self.regime_lbl.setText(text)
            self.regime_lbl.setStyleSheet(
                f"color:{color}; font-size:12px; font-weight:bold;")

        if not params or not hasattr(self, 'adapt_params_lbl'):
            return

        streak_txt = ""
        ws = getattr(self.strategy, '_win_streak',  0)
        ls = getattr(self.strategy, '_loss_streak', 0)
        if ws >= 3: streak_txt = f" | 🔥 {ws} انتصارات"
        elif ls >= 2: streak_txt = f" | ⚠ {ls} خسائر"

        # vol_mult اختياري — استخدم .get() بقيمة افتراضية
        vol_m  = params.get('vol_mult',  1.2)
        atr_m  = params.get('atr_mult',  1.0)
        tp_r   = params.get('tp_ratio',  1.8)
        sc_min = params.get('min_score', 3)
        max_c  = params.get('max_contracts', 2)

        self.adapt_params_lbl.setText(
            f"Score≥{sc_min} | ATR×{atr_m} | TP×{tp_r} | "
            f"MaxC={max_c}{streak_txt}"
        )

    def _safe_log(self, msg: str) -> None:
        """Thread-safe UI log. Routes through signal_scan_update (PyQt queued) from any thread."""
        asu = getattr(getattr(self, 'auto_bot', None), 'signal_scan_update', None)
        if asu is not None:
            asu.emit(msg)
        else:
            self._on_bot_scan_signal(msg)



    def _is_signal_log_line(self, text: str) -> bool:
        """Signal rows only. Normal logs remain untouched."""
        s = str(text or "")
        return (
            "[ORB Daily]" in s or
            "[ORB LIVE]" in s or
            "[PAPER / BC" in s or
            "[BC LIVE" in s or
            "Source: ORB" in s or
            "Source: B+C" in s
        )

    def _signal_log_ttl_sec(self, text: str) -> int:
        """UI-only lifetime for active signal rows."""
        s = str(text or "")
        if "[ORB" in s or "Source: ORB" in s:
            return 300
        if "[PAPER / BC" in s or "[BC LIVE" in s or "Source: B+C" in s:
            return 180
        return 240

    def _signal_log_key(self, text: str) -> str:
        """
        Stable key used to update/remove the previous visible row instead of
        leaving stale duplicate signals in the UI.
        """
        import re
        s = str(text or "")
        source = "ORB" if ("ORB" in s) else ("BC" if ("BC" in s or "B+C" in s) else "GEN")

        direction = ""
        for d in ("CALL", "PUT", "LONG", "SHORT"):
            if re.search(rf"\b{d}\b", s):
                direction = d
                break

        symbol = ""
        m = re.search(r"\]\s*([A-Z]{1,6})\b", s)
        if m:
            symbol = m.group(1)
        else:
            m = re.search(r"\b([A-Z]{1,6})\s+(CALL|PUT|LONG|SHORT)\b", s)
            if m:
                symbol = m.group(1)

        entry = ""
        m = re.search(r"@\s*\$?([0-9]+(?:\.[0-9]+)?)", s)
        if m:
            try:
                entry = f"{float(m.group(1)):.1f}"
            except Exception:
                entry = m.group(1)

        return f"{source}|{symbol}|{direction}|{entry}"

    def _prune_signal_log_items(self, force_legacy: bool = False):
        """
        Remove expired ORB/B+C signal rows from the visible signal panel.
        This is UI state only; it does not change strategy/execution.
        """
        try:
            now = time.time()
            for i in range(self.signals_list.count() - 1, -1, -1):
                item = self.signals_list.item(i)
                if not item:
                    continue

                txt = item.text()
                if not self._is_signal_log_line(txt):
                    continue

                ts = item.data(Qt.UserRole)
                if ts is None:
                    if force_legacy:
                        self.signals_list.takeItem(i)
                    continue

                try:
                    age = now - float(ts)
                except Exception:
                    age = 999999

                if age >= self._signal_log_ttl_sec(txt):
                    self.signals_list.takeItem(i)
        except Exception:
            pass

    def _remove_same_signal_key(self, key: str):
        """Remove older visible rows for the same signal key."""
        if not key:
            return
        try:
            for i in range(self.signals_list.count() - 1, -1, -1):
                item = self.signals_list.item(i)
                if not item:
                    continue
                old_key = item.data(Qt.UserRole + 1)
                txt = item.text()
                if old_key == key:
                    self.signals_list.takeItem(i)
                    continue
                if old_key is None and self._is_signal_log_line(txt):
                    if self._signal_log_key(txt) == key:
                        self.signals_list.takeItem(i)
        except Exception:
            pass

    def _ensure_signal_log_timer(self):
        """Periodic cleanup for expired active-signal rows."""
        if getattr(self, "_signal_log_ttl_timer_started", False):
            return
        try:
            self._signal_log_ttl_timer_started = True
            self._signal_log_ttl_timer = QTimer(self)
            self._signal_log_ttl_timer.timeout.connect(lambda: self._prune_signal_log_items(False))
            self._signal_log_ttl_timer.start(30000)
        except Exception:
            pass

    def _on_bot_scan_signal(self, msg):
        # [PROFILING] flood counter + slow detection
        _prof_bss_t0 = time.perf_counter()
        self._prof_last_action = f'_on_bot_scan_signal'
        _sec = int(time.time())
        if _sec != self._prof_log_sec:
            if self._prof_log_cnt > 20:
                print(f'[PROF LOG FLOOD] _on_bot_scan_signal: {self._prof_log_cnt} calls/sec', flush=True)
            self._prof_log_cnt = 0; self._prof_log_sec = _sec
        self._prof_log_cnt += 1
        if not msg:
            return

        self._ensure_signal_log_timer()

        important = ['🎯','🏆','🚀','⚠','❌','CALL','PUT','📨','⏭','💰','⛔','🌍',
                     '📈','📉','📦','📋','📥','🔎','🧪','🥇','↔','⊘',
                     'سكور','تأكيد','تنفيذ','انتهى','أمر','PnL','إشارة','رُفض','رصيد',
                     'يمسح','scanning','إشارات','إشارة','توافق','اتجاه','خسائر',
                     '📊','📈','📉','⚪','تحليل','ADX','✅','🔍','cooldown','نُفذت','DRY',
                     'FILLED','chains','strike','budget','سبريد','delta','qualify',
                     'Breakout','Pullback','Trend','SL=','TP1=','TP2=',
                     'ORB Daily','ORB LIVE','Source: B+C','Source: ORB']

        if not any(k in msg for k in important):
            return

        is_signal_line = self._is_signal_log_line(msg)

        if is_signal_line and not getattr(self, "_signal_log_legacy_cleaned", False):
            self._signal_log_legacy_cleaned = True
            self._prune_signal_log_items(force_legacy=True)

        self._prune_signal_log_items(force_legacy=False)

        if self.signals_list.count() > 0:
            if self.signals_list.item(0).text() == msg:
                return

        sig_key = ""
        if is_signal_line:
            sig_key = self._signal_log_key(msg)
            self._remove_same_signal_key(sig_key)

        _ins_sec = int(time.time())
        if _ins_sec != self._prof_insert_sec:
            if self._prof_insert_cnt > 20:
                print(f'[PROF LOG FLOOD] signals_list.insertItem: {self._prof_insert_cnt} inserts/sec', flush=True)
            self._prof_insert_cnt = 0; self._prof_insert_sec = _ins_sec
        self._prof_insert_cnt += 1

        self.signals_list.insertItem(0, msg)
        item = self.signals_list.item(0)

        if is_signal_line:
            item.setData(Qt.UserRole, time.time())
            item.setData(Qt.UserRole + 1, sig_key)

        if 'CALL' in msg or '🎯' in msg or '🏆' in msg or '🚀' in msg:
            color = "#05c46b"
        elif 'PUT' in msg:
            color = "#ff5e57"
        elif '⚠' in msg or '❌' in msg or 'انتهى' in msg:
            color = "#ffb62e"
        elif '📊' in msg:
            color = "#a29bfe"
        else:
            color = "#0fbcf9"

        item.setForeground(QBrush(QColor(color)))

        if self.signals_list.count() > 100:
            self.signals_list.takeItem(100)

        # [PROFILING] slow call detection
        _prof_bss_ms = (time.perf_counter() - _prof_bss_t0) * 1000
        if _prof_bss_ms > 30:
            print(f'[PROF SLOW] _on_bot_scan_signal: {_prof_bss_ms:.1f}ms  msg={msg[:50]!r}', flush=True)


    def _on_risk_alert(self, msg):
        self.signals_list.insertItem(0, msg)
        self.signals_list.item(0).setForeground(QBrush(QColor("#ffb62e")))

    def _on_new_trade(self, trade):
        """إضافة صفقة جديدة بشكل متسق مع الواجهة والتنفيذ"""
        t = dict(trade or {})
        tid = t.get('id', '')

        def _num(v, default=0.0):
            try:
                if v is None:
                    return float(default)
                if isinstance(v, (int, float)):
                    return float(v)
                return float(str(v).strip().replace('$', '').replace(',', ''))
            except Exception:
                return float(default)

        def _int(v, default=0):
            try:
                if v is None:
                    return int(default)
                if isinstance(v, int):
                    return v
                if isinstance(v, float):
                    return int(round(v))
                return int(round(float(str(v).strip().replace(',', ''))))
            except Exception:
                return int(default)

        symbol = str(t.get('symbol') or t.get('exec_symbol') or t.get('signal_symbol') or '').upper().strip()
        opt_type = str(t.get('opt_type') or t.get('type') or '').upper().strip()
        entry = _num(t.get('entry_premium', t.get('entry', 0)))
        stop_loss = _num(t.get('stop_loss', t.get('sl', 0)))
        strike = _num(t.get('strike', 0))
        expiry_raw = str(t.get('expiry', '') or '')
        cost = _num(t.get('cost', 0))
        qty = _int(t.get('contracts', t.get('qty', 0)))

        pos = self.position_manager.get(tid) if tid else None
        if pos:
            symbol = str(pos.get('symbol') or pos.get('exec_symbol') or pos.get('signal_symbol') or symbol).upper().strip()
            if not opt_type:
                opt_type = str(pos.get('opt_type') or pos.get('type') or '').upper().strip()
            if entry <= 0:
                entry = _num(pos.get('entry_premium', pos.get('entry', 0)))
            if stop_loss <= 0:
                stop_loss = _num(pos.get('stop_loss', pos.get('sl', 0)))
            if strike <= 0:
                strike = _num(pos.get('strike', 0))
            if not expiry_raw:
                expiry_raw = str(pos.get('expiry', '') or '')
            if cost <= 0:
                cost = _num(pos.get('cost', 0))
            if qty <= 0:
                qty = _int(pos.get('contracts', pos.get('qty', 0)))

        if qty <= 0 and entry > 0 and cost > 0:
            qty = max(1, int(round(cost / (entry * 100.0))))
        if qty <= 0:
            qty = 1
        # ✅ تأكد أن العقود لا تتجاوز 2
        qty = min(qty, 2)

        _index_syms = {'SPX', 'XSP', 'NDX', 'RUT', 'VIX'}
        is_idx = symbol in _index_syms

        tp1 = _num(t.get('take_profit', t.get('tp1', 0)))
        if pos and tp1 <= 0:
            tp1 = _num(pos.get('take_profit', pos.get('tp1', 0)))
        if tp1 <= 0 and entry > 0:
            tp1 = round(entry * (1.20 if is_idx else 1.25), 2)

        if cost <= 0 and entry > 0:
            cost = round(entry * qty * 100.0, 2)

        if len(expiry_raw) == 8 and expiry_raw.isdigit():
            expiry_disp = f"{expiry_raw[4:6]}/{expiry_raw[6:]}"
        else:
            expiry_disp = expiry_raw

        # normalize for later updates/manual exit
        t.update({
            'id': tid,
            'symbol': symbol,
            'opt_type': opt_type,
            'entry_premium': entry,
            'stop_loss': stop_loss,
            'strike': strike,
            'expiry': expiry_raw,
            'contracts': qty,
            'qty': qty,
            'take_profit': tp1,
            'tp1': tp1,
            'cost': cost,
        })
        if pos is not None:
            pos.update(t)

        # ✅ إضافة للـ position_manager حتى يراها MonitorThread
        if tid and not self.position_manager.get(tid):
            self.position_manager.add(tid, t)
        elif tid:
            _pm_pos = self.position_manager.get(tid)
            if _pm_pos is not None:
                _pm_pos.update(t)

        cl = "#05c46b" if opt_type == 'CALL' else "#ff5e57"

        msg = (
            f"[{t.get('time', '')}] {symbol} {opt_type} "
            f"Strike:{strike:,.0f} Exp:{expiry_disp} "
            f"@${entry:.2f} | عقود:{qty} | SL:${stop_loss:.2f} | TP:${tp1:.2f}"
        )
        self.signals_list.insertItem(0, msg)
        self.signals_list.item(0).setForeground(QBrush(QColor(cl)))
        self.signals_list.item(0).setToolTip(t.get('why', ''))
        if self.signals_list.count() > 50:
            self.signals_list.takeItem(50)

        r = self.open_trades_table.rowCount()
        self.open_trades_table.insertRow(r)

        strat_lbl = '📊 Index' if is_idx else '📈 Stock'
        vals = [
            symbol,
            opt_type,
            f"{strike:,.0f}",
            expiry_disp,
            f"${entry:.2f}",
            "...",
            "$0.00",
            f"${stop_loss:.2f}",
            f"${tp1:.2f}",
            str(qty),
            f"${cost:.0f}",
            strat_lbl,
        ]
        for col, val in enumerate(vals):
            item = QTableWidgetItem(str(val))
            item.setForeground(QBrush(QColor(cl)))
            item.setData(Qt.UserRole, tid)
            if col == 0:
                item.setData(Qt.UserRole + 1, t)
            self.open_trades_table.setItem(r, col, item)

        exit_btn = QPushButton("🚪 خروج")
        exit_btn.setStyleSheet(
            "background:#ff5e57; color:white; font-size:11px; "
            "font-weight:bold; padding:3px 8px; border-radius:4px;"
        )
        exit_btn.setToolTip(f"خروج يدوي فوري من {symbol} {opt_type}")

        def _make_exit_fn(tid_local=tid, trade_ref=t):
            def _do_exit():
                pos2 = self.position_manager.get(tid_local) if tid_local else None
                self._manual_exit_trade(pos2 or trade_ref)
            return _do_exit

        exit_btn.clicked.connect(_make_exit_fn())
        self.open_trades_table.setCellWidget(r, 12, exit_btn)

        self._refresh_local_stats()
        self._draw_trade_on_chart(t)

        # ── ① تسجيل في Trail Engine (8% Ratchet) ─────────────────
        _ma = getattr(self, '_market_analyzer', None)
        if _ma and tid and entry > 0 and stop_loss > 0:
            try:
                _ma.register_trade(
                    trade_id      = tid,
                    entry_premium = entry,
                    stop_loss     = stop_loss,
                    tp1           = tp1,
                    tp2           = pos.get("take_profit_2", round(tp1 * 2.0, 2)) if pos else round(tp1 * 2.0, 2),
                )
            except Exception as _te:
                print(f"[Trail Reg] {_te}")

        # ── ② تحقق من نطاق العقد $70-$160 ───────────────────────
        if entry > 0:
            _cost = entry * 100
            if not (70.0 <= _cost <= 160.0):
                self._on_bot_scan_signal(
                    f"⚠ {symbol}: تكلفة العقد ${_cost:.0f} خارج النطاق $70-$160 — راجع الـ strike"
                )

    def _draw_trade_on_chart(self, trade: dict):
        """يرسم الصفقة على الشارت الرئيسي والنافذة المستقلة"""
        # تأكد أن entry_stock_price موجود
        _stock_price = (trade.get('entry_stock_price') or
                        trade.get('price') or
                        getattr(self, 'current_price', 0) or 0)
        trade_data = {
            'direction':         trade.get('opt_type', 'CALL'),
            'entry_price':       float(trade.get('entry_premium', 0) or 0),
            'stop_loss':         float(trade.get('stop_loss', 0) or 0),
            'take_profit':       float(trade.get('take_profit', 0) or 0),
            'symbol':            str(trade.get('symbol', '')),
            'contracts':         int(trade.get('contracts', 1) or 1),
            'strike':            float(trade.get('strike', 0) or 0),
            'expiry':            str(trade.get('expiry', '')),
            'entry_stock_price': float(_stock_price),
        }

        # تحقق أن القيم صالحة قبل الإرسال
        if not trade_data['entry_price'] or not trade_data['stop_loss'] or not trade_data['take_profit']:
            print(f"[draw_trade] بيانات ناقصة: entry={trade_data['entry_price']} "
                  f"sl={trade_data['stop_loss']} tp={trade_data['take_profit']}")
            return

        # احفظ آخر صفقة — تُرسل للشارت الجديد عند فتحه
        self._last_trade_data = trade_data

        # الشارت الرئيسي
        if hasattr(self, '_pro_chart') and self._pro_chart:
            try: self._pro_chart.draw_trade(trade_data)
            except Exception as e: print(f"[draw_trade main] {e}")

        # النافذة المستقلة JS
        _wc = getattr(self, '_chart_win_chart', None)
        if _wc and hasattr(_wc, 'draw_trade'):
            try: _wc.draw_trade(trade_data)
            except Exception as e: print(f"[draw_trade win] {e}")

    def _manual_exit_trade(self, trade):
        """خروج يدوي مؤكد من IBKR: لا تُزال الصفقة من البرنامج حتى يتأكد الإغلاق من الوسيط"""
        from PyQt5.QtWidgets import QMessageBox

        def _num(v, default=0.0):
            try:
                if v is None:
                    return float(default)
                if isinstance(v, (int, float)):
                    return float(v)
                return float(str(v).strip().replace('$', '').replace(',', ''))
            except Exception:
                return float(default)

        def _int(v, default=0):
            try:
                if v is None:
                    return int(default)
                if isinstance(v, int):
                    return v
                if isinstance(v, float):
                    return int(round(v))
                return int(round(float(str(v).strip().replace(',', ''))))
            except Exception:
                return int(default)

        trade = dict(trade or {})
        tid = trade.get('id', '')
        pos_src = self.position_manager.get(tid) if tid else None
        src = dict(pos_src or {})
        src.update(trade)

        sym = str(src.get('symbol') or src.get('exec_symbol') or src.get('signal_symbol') or '').upper().strip()
        opt = str(src.get('opt_type') or src.get('type') or '').upper().strip()
        qty = _int(src.get('contracts', src.get('qty', 0)), 0)
        entry = _num(src.get('entry_premium', src.get('entry', 0)), 0)

        if not self.connected or not self.ib:
            QMessageBox.warning(self, 'غير متصل', 'يجب الاتصال بـ IBKR أولاً')
            return

        if tid and not self.position_manager.mark_closing(tid):
            QMessageBox.information(self, 'جارٍ الإغلاق', 'هذه الصفقة يُغلق عليها بالفعل...')
            return

        reply = QMessageBox.question(
            self, 'تأكيد الخروج',
            f'هل تريد الخروج اليدوي من {sym} {opt}؟\nسيتم البيع الآن من IBKR.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            if tid:
                self.position_manager.unmark_closing(tid)
            return

        target_row = -1
        for row in range(self.open_trades_table.rowCount()):
            item = self.open_trades_table.item(row, 0)
            if item and item.data(Qt.UserRole) == tid:
                target_row = row
                btn = self.open_trades_table.cellWidget(row, 12)
                if btn:
                    btn.setEnabled(False)
                    btn.setText('⏳ جارٍ...')
                break

        def _worker():
            try:
                live_contract = None
                live_qty = qty

                # حاول أخذ المركز الحقيقي من IBKR
                positions = run_in_ib_thread(self.ib.positions) or []
                src_strike = _num(src.get('strike', 0), 0)
                src_exp = str(src.get('expiry', '') or '')
                src_right = 'C' if opt.startswith('C') else 'P'

                for p in positions:
                    c = getattr(p, 'contract', None)
                    if not c:
                        continue
                    csym = str(getattr(c, 'symbol', '') or '').upper().strip()
                    if csym != sym:
                        continue
                    cstrike = _num(getattr(c, 'strike', 0), 0)
                    cexp = str(getattr(c, 'lastTradeDateOrContractMonth', '') or '')
                    cright = str(getattr(c, 'right', '') or '').upper().strip()
                    same_strike = abs(cstrike - src_strike) < 0.01 if src_strike > 0 else True
                    same_exp = (cexp == src_exp) if src_exp else True
                    same_right = (cright == src_right) if src_right else True
                    if same_strike and same_exp and same_right:
                        live_contract = c
                        live_qty = max(1, int(abs(getattr(p, 'position', live_qty) or live_qty)))
                        break

                # fallback على العقد المحفوظ
                if live_contract is None:
                    live_contract = src.get('opt_contract')

                if live_contract is None:
                    raise RuntimeError(f'تعذر تحديد العقد الحقيقي في IBKR لـ {sym}')

                q = run_in_ib_thread(self.ib.qualifyContracts, live_contract)
                if q:
                    live_contract = q[0]

                order = _make_order('SELL', live_qty, force_outside_rth=True)
                ib_trade = run_in_ib_thread(self.ib.placeOrder, live_contract, order)
                if not ib_trade:
                    raise RuntimeError('فشل إرسال أمر الخروج إلى IBKR')

                exit_price = 0.0
                filled = False
                deadline = time.time() + 35.0
                while time.time() < deadline:
                    time.sleep(0.5)
                    ib_pump(0.05)

                    try:
                        status = run_in_ib_thread(lambda t=ib_trade: getattr(t.orderStatus, 'status', ''))
                        avg_fill = run_in_ib_thread(lambda t=ib_trade: getattr(t.orderStatus, 'avgFillPrice', 0))
                        remaining = run_in_ib_thread(lambda t=ib_trade: getattr(t.orderStatus, 'remaining', 1))
                        if avg_fill and avg_fill > 0:
                            exit_price = float(avg_fill)
                        if status == 'Filled' or (status == 'Submitted' and remaining == 0):
                            filled = True
                            break
                    except Exception:
                        pass

                    # تحقق من المراكز الحية
                    still_open = False
                    positions2 = run_in_ib_thread(self.ib.positions) or []
                    for p2 in positions2:
                        c2 = getattr(p2, 'contract', None)
                        if c2 and getattr(c2, 'conId', None) == getattr(live_contract, 'conId', None):
                            if abs(int(getattr(p2, 'position', 0) or 0)) > 0:
                                still_open = True
                                break
                    if not still_open:
                        filled = True
                        break

                if not filled:
                    raise RuntimeError('الأمر لم يُغلق المركز في IBKR بعد')

                if exit_price <= 0:
                    exit_price = entry

                pnl = round((exit_price - entry) * live_qty * 100, 2)

                self.position_manager.remove(tid)
                self.risk_manager.close(pnl, symbol=sym)

                close_data = {
                    **src,
                    'symbol': sym,
                    'contracts': live_qty,
                    'qty': live_qty,
                    'exit_price': exit_price,
                    'exit_reason': 'manual_exit',
                    'pnl': pnl,
                    'exit_time': datetime.now().strftime('%H:%M:%S'),
                }
                self.signal_close_trade_ui.emit(close_data)
                self.signal_scan_update.emit(f'✋ خروج يدوي مؤكد {sym} @ ${exit_price:.2f} x{live_qty} PnL=${pnl:+.0f}')
            except Exception as e:
                import traceback; traceback.print_exc()
                self.signal_scan_update.emit(f'❌ خطأ خروج يدوي {sym}: {e}')
                if tid:
                    self.position_manager.unmark_closing(tid)
                if target_row >= 0:
                    btn = self.open_trades_table.cellWidget(target_row, 12)
                    if btn:
                        QMetaObject.invokeMethod(btn, 'setEnabled', Qt.QueuedConnection, Q_ARG(bool, True))
                        QMetaObject.invokeMethod(btn, 'setText', Qt.QueuedConnection, Q_ARG(str, '🚪 خروج'))

        threading.Thread(target=_worker, daemon=True).start()

    def signal_scan_update_ui(self, msg):
        """تحديث signals_list من أي thread"""
        self.signals_list.insertItem(0, msg)
        if self.signals_list.count() > 80:
            self.signals_list.takeItem(80)

    def _load_history_from_json(self):
        """تحميل تاريخ الصفقات المحفوظ عند فتح البرنامج"""
        try:
            import json, os
            _f = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_history.json')
            if not os.path.exists(_f):
                return
            with open(_f, 'r', encoding='utf-8') as fh:
                records = json.load(fh)
            reason_map = {
                'tp1': '🎯 TP1 (+25%)', 'tp2': '🎯 TP2 (+50%)',
                'trailing_tp2': '🔄 Trail TP1', 'trailing_tp3': '🔄 Trail TP2',
                'stop_loss': '❌ Stop Loss', 'take_profit': '✅ Take Profit',
            }
            for rec in records:
                pnl  = rec.get('pnl', 0)
                # إعادة حساب PnL إذا صفر
                if pnl == 0:
                    _e = rec.get("entry",0); _x = rec.get("exit",0); _c = rec.get("contracts",1)
                    pnl = round((_x - _e) * _c * 100, 2)
                cl   = "#05c46b" if pnl >= 0 else "#ff5e57"
                sign = "+" if pnl >= 0 else "-"
                r = self.history_table.rowCount()
                self.history_table.insertRow(r)
                rtext = reason_map.get(rec.get('reason',''), rec.get('reason','--'))
                vals = [
                    f"{rec.get('date','')} {rec.get('time','--')}",
                    rec.get('symbol',''), rec.get('opt_type',''),
                    f"${rec.get('entry',0):.2f}", f"${rec.get('exit',0):.2f}",
                    rtext, f"{sign}${abs(pnl):.2f}" if pnl != 0 else "$0.00",
                    f"{rec.get('score',0):.0f}/5"
                ]
                for col, val in enumerate(vals):
                    it = QTableWidgetItem(val)
                    it.setForeground(QBrush(QColor(cl)))
                    self.history_table.setItem(r, col, it)
            pass  # تحميل ناجح
        except Exception as e:
            print(f"[History] خطأ في التحميل: {e}")

    def _on_update_trade(self, data):
        """تحديث سعر الصفقة المفتوحة في الجدول في الوقت الحقيقي"""
        trade_id = data.get('id')
        current  = data.get('current', 0)
        pnl_usd  = data.get('pnl_usd', 0)
        pnl_pct  = data.get('pnl_pct', 0)
        sl       = data.get('stop_loss', 0)
        tp1      = data.get('tp1', 0)
        rem      = data.get('contracts', 0)
        phase_lbl= data.get('phase_lbl', '')
        cl = "#05c46b" if pnl_usd >= 0 else "#ff5e57"
        for row in range(self.open_trades_table.rowCount()):
            item = self.open_trades_table.item(row, 0)
            if item and item.data(Qt.UserRole) == trade_id:
                # col5=الآن, col6=PnL$, col7=SL, col8=TP1, col9=عقود
                def _set(c, v, color=None):
                    it = QTableWidgetItem(v)
                    if color: it.setForeground(QBrush(QColor(color)))
                    it.setData(Qt.UserRole, trade_id)
                    self.open_trades_table.setItem(row, c, it)
                from datetime import datetime as _dt2
                _ts = _dt2.now().strftime("%H:%M:%S")
                _set(5, f"${current:.2f} @{_ts}", "#f9ca24")
                _sign = data.get('pnl_sign', '+' if pnl_usd >= 0 else '-')
                _abs  = abs(pnl_usd)
                _set(6, f"{_sign}${_abs:.2f}  ({pnl_pct:+.1f}%)", cl)
                _set(7, f"${sl:.2f}", "#ff5e57")
                _set(8, f"${tp1:.2f} {phase_lbl}", "#05c46b")
                _set(9, str(rem))
                # حدّث PnL اليوم = realized + unrealized
                _realized = self.risk_manager.daily_pnl
                _pnl_now  = _realized + pnl_usd
                _clr = '#05c46b' if _pnl_now >= 0 else '#ff5e57'
                self.pnl_label.setText(f'PnL اليوم: ${_pnl_now:+.2f}')
                self.pnl_label.setStyleSheet(f'color:{_clr}; font-size:13px; font-weight:bold;')
                break

    def _on_close_trade(self, trade):
        """اغلاق صفقة — thread-safe عبر signal (يُستدعى دائماً في main thread)"""
        # إذا استُدعي من non-main thread → لا نُرسل signal مرة ثانية (يُسبب loop)
        # بدلاً من ذلك: الـ signal نفسه يضمن التنفيذ في main thread تلقائياً
        # (Qt::QueuedConnection — الـ default عند signal بين threads مختلفة)
        t = trade
        pnl = t.get('pnl', 0)
        # تأكد أن PnL محسوب صح: (exit-entry)*contracts*100
        if pnl == 0 and t.get("exit_price") and t.get("entry_premium"):
            pnl = round((t["exit_price"] - t["entry_premium"]) * t.get("contracts",1) * 100, 2)
        cl  = "#05c46b" if pnl >= 0 else "#ff5e57"
        pnl_sign = "+" if pnl >= 0 else "-"

        # حذف من _closing_trades
        _tid_close = t.get('id')
        if hasattr(self, '_closing_trades') and _tid_close:
            self._closing_trades.discard(_tid_close)
        # تأكد حذف من position_manager إذا لم يُحذف بعد
        if _tid_close and self.position_manager.get(_tid_close):
            self.position_manager.remove(_tid_close)
        # ── إزالة من Trail Engine ────────────────────────────────
        _ma_close = getattr(self, '_market_analyzer', None)
        if _ma_close and _tid_close:
            try: _ma_close.remove_trade(_tid_close)
            except Exception: pass
        # حذف من جدول المفتوحة
        for row in range(self.open_trades_table.rowCount()):
            item = self.open_trades_table.item(row, 0)
            if item and item.data(Qt.UserRole) == t.get('id'):
                self.open_trades_table.removeRow(row)
                break

        # اضافة للتاريخ
        r = self.history_table.rowCount()
        self.history_table.insertRow(0)
        reason_map = {
            'tp1':            '🎯 TP1 (+25%)',
            'tp2':            '🎯 TP2 (+50%)',
            'trailing_tp2':   '🔄 Trail بعد TP1',
            'trailing_tp3':   '🔄 Trail بعد TP2',
            'take_profit':    '✅ Take Profit',
            'stop_loss':      '❌ Stop Loss',
            'trailing_stop':  '🔄 Trailing Stop',
            'expiry_exit':    '⏰ Expiry خروج إجباري',
            'timeout_exit':   '⏱ Timeout +4h',
            'manual_exit':    '✋ خروج يدوي',
        }
        reason_text = reason_map.get(t.get('exit_reason'), t.get('exit_reason', '--'))
        vals = [
            t.get('exit_time', '--'),
            t['symbol'],
            t['opt_type'],
            f"${t['entry_premium']:.2f}",
            f"${t.get('exit_price', 0):.2f}",
            reason_text,
            f"{pnl_sign}${abs(pnl):.2f}" if pnl != 0 else "$0.00",
            f"{t.get('score', 0):.0f}/5",
        ]
        for col, val in enumerate(vals):
            item = QTableWidgetItem(val)
            item.setForeground(QBrush(QColor(cl)))
            self.history_table.setItem(0, col, item)

        # ── حفظ في ملف JSON للذاكرة الدائمة ───────────
        try:
            import json, os
            _f = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_history.json')
            _history = []
            if os.path.exists(_f):
                with open(_f, 'r', encoding='utf-8') as _fh:
                    _history = json.load(_fh)
            _record = {
                'date':        datetime.now().strftime('%Y-%m-%d'),
                'time':        t.get('exit_time', '--'),
                'symbol':      t['symbol'],
                'opt_type':    t['opt_type'],
                'strike':      str(t.get('strike', 0)),
                'expiry':      t.get('expiry', ''),
                'entry':       t.get('entry_premium', 0),
                'exit':        t.get('exit_price', 0),
                'pnl':         pnl,
                'contracts':   t.get('contracts', 0),
                'reason':      t.get('exit_reason', ''),
                'score':       t.get('score', 0),
            }
            _history.insert(0, _record)
            with open(_f, 'w', encoding='utf-8') as _fh:
                json.dump(_history[:500], _fh, ensure_ascii=False, indent=2)
        except Exception as _je:
            print(f"JSON save error: {_je}")

        # ── تحديث عرض إحصائيات اليوم ──────────────────────
        # PnL موجود بالفعل في risk_manager.close() - لا نضيفه مرة ثانية
        _dp = self.risk_manager.daily_pnl
        _dc = '#05c46b' if _dp >= 0 else '#ff5e57'
        self.pnl_label.setText(f"PnL اليوم: ${_dp:+.2f}")
        self.pnl_label.setStyleSheet(f"color:{_dc}; font-size:13px; font-weight:bold;")
        self.open_trades_label.setText(
            f"صفقات مفتوحة: {self.risk_manager.open_trades}/{self.risk_manager.max_open_trades}")

        # ✅ إصلاح: تحديث win/loss streak في الاستراتيجية بعد كل صفقة
        try:
            _regime = t.get('regime', 'normal')
            _score  = t.get('score', 0)
            _why    = [w.strip() for w in t.get('why', '').split('|') if w.strip()]
            # الساعة التي فُتحت فيها الصفقة
            try:
                import pytz as _rcpytz
                _h_close = __import__('datetime').datetime.now(
                    _rcpytz.timezone('US/Eastern')).hour
            except Exception:
                _h_close = -1
            if hasattr(self, 'auto_bot') and self.auto_bot:
                for _strat in [getattr(self.auto_bot, 'strategy', None),
                                getattr(self.auto_bot, '_index_strategy', None),
                                getattr(self.auto_bot, '_stock_strategy', None)]:
                    if _strat and hasattr(_strat, 'record_trade_result'):
                        _strat.record_trade_result(pnl, regime=_regime)
            if hasattr(self, 'strategy') and hasattr(self.strategy, 'record_trade_result'):
                self.strategy.record_trade_result(pnl, regime=_regime)
            # ✅ تحديث RiskManager.close() بالمعلومات الكاملة
            # (يُستدعى بالفعل من position_manager لكن نضمن memory update)
            self.risk_manager.memory.record(
                t.get('symbol', ''), pnl, _h_close, _regime, _score, _why)
        except Exception as _re:
            print(f"[record_trade] خطأ: {_re}")

        # ── تحديث فوري للرصيد من IBKR ────────────────────
        def _refresh_balance():
            try:
                if not self.connected or not self.account: return
                for item in run_in_ib_thread(self.ib.accountSummary, self.account):
                    if item.tag == 'TotalCashValue':
                        self.ui_updater.update_cash.emit(float(item.value))
                        break
            except Exception: pass
        threading.Thread(target=_refresh_balance, daemon=True).start()

        # حدّث الإحصائيات فوراً بعد إغلاق الصفقة
        self._refresh_local_stats()

        # ── امسح رسم الصفقة من الشارت ───────────────────
        if hasattr(self, '_pro_chart') and self._pro_chart:
            try: self._pro_chart.clear_trade()
            except Exception: pass
        _wc = getattr(self, '_chart_win_chart', None)
        if _wc:
            try: _wc.clear_trade()
            except Exception: pass
        # إذا لا تزال هناك صفقات مفتوحة → ارسم أول واحدة
        for pos in self.position_manager.get_all():
            self._draw_trade_on_chart(pos)
            break

        # اشارة
        msg = f"[{t.get('exit_time','--')}] {t['symbol']} {t['opt_type']} {reason_text} PnL:{pnl_sign}${abs(pnl):.2f}"
        self.signals_list.insertItem(0, msg)
        self.signals_list.item(0).setForeground(QBrush(QColor(cl)))

    # -----------------------------------------------
    # تغيير الرمز
    # -----------------------------------------------
    def _add_watchlist_symbol(self):
        sym = self.watchlist_input.text().upper().strip()
        if not sym: return
        _focus_fn = globals().get('_is_focus_symbol', lambda s: True)
        if not _focus_fn(sym):
            self.ui_updater.show_status.emit(f"⛔ {sym} خارج قائمة CLEAN ENGINE"); return
        existing = [self.watchlist_widget.item(i).text() for i in range(self.watchlist_widget.count())]
        if sym in existing:
            self.ui_updater.show_status.emit(f"{sym} موجود"); return
        self.watchlist_widget.addItem(sym)
        self.auto_bot_scan_symbols.append(sym)
        self.watchlist_input.clear()
        if self.auto_bot and self.auto_bot.isRunning():
            self.auto_bot.SCAN_SYMBOLS = [s for s in self.auto_bot_scan_symbols if _focus_fn(s)]
        self.ui_updater.show_status.emit(f"✅ {sym}")

    def _del_watchlist_symbol(self):
        sel = self.watchlist_widget.selectedItems()
        if not sel: return
        sym = sel[0].text()
        self.watchlist_widget.takeItem(self.watchlist_widget.row(sel[0]))
        if sym in self.auto_bot_scan_symbols: self.auto_bot_scan_symbols.remove(sym)
        if self.auto_bot and self.auto_bot.isRunning():
            _focus_fn = globals().get('_is_focus_symbol', lambda s: True)
            self.auto_bot.SCAN_SYMBOLS = [s for s in self.auto_bot_scan_symbols if _focus_fn(s)]
        self.ui_updater.show_status.emit(f"🗑 {sym}")

    def select_from_list(self, item):
        sym = item.text()
        self.symbol_input.setText(sym)
        self.current_symbol = sym
        self.price_label.setText("$--")
        self._set_fallback_dates()
        self._load_symbol(sym)

    def change_symbol(self):
        sym = self.symbol_input.text().upper().strip()
        if not sym:
            return
        self.current_symbol = sym
        self.price_label.setText("$--")
        self._set_fallback_dates()
        self._load_symbol(sym)

    @pyqtSlot(str)
    def _load_symbol(self, sym):
        old = getattr(self, "current_symbol", None)
        if old and old != sym:
            self._stop_symbol_streaming(old)
        self.current_symbol = sym
        self.current_price  = 0.0   # إعادة تعيين السعر للرمز الجديد
        self._start_symbol_streaming(sym)
        self._last_full_chart_refresh = None
        if hasattr(self, '_pro_chart') and self._pro_chart:
            if hasattr(self._pro_chart, 'set_timeframe'):
                self._pro_chart.set_timeframe('15m')
        threading.Thread(
            target=self._fetch_chart, args=(sym,), daemon=True).start()
        QTimer.singleShot(2000, self._refresh_bookmap)
        if self.connected:
            self._fetch_expiries_for(sym)
        else:
            self.update_options_table()

    # ═══════════════════════════════════════════════════════
    # ثلاثة فريمات: 1م | 15م | 1س
    # ═══════════════════════════════════════════════════════

    def _fetch_chart(self, symbol, tf='15m'):
        """جلب بيانات الشارت الرئيسي وتحديث pro_chart"""
        import time as _t, json as _json, os as _os
        for _ in range(20):
            if self._pro_chart:
                break
            _t.sleep(0.2)
        if not self._pro_chart:
            print("[Chart] _pro_chart غير موجود")
            return

        _chart_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'chart_data')

        # ── ✅ أولوية 1: chart_data/ من tv_datafeed.py ────────────────
        # أحدث وأدق من IBKR — يتحدث كل 15 ثانية
        _tf_map_tv = {'15m': '15m', '1H': '1H', '4H': '4H', '1D': '1D',
                      '5m': '5m', '1m': '1m'}
        _tv_tf   = _tf_map_tv.get(tf, '15m')
        _tv_path = _os.path.join(_chart_dir, f'{symbol}_{_tv_tf}.json')

        def _build_bars_data_from_file(path):
            """يقرأ ملف chart_data ويحسب المؤشرات"""
            try:
                with open(path, 'r', encoding='utf-8') as _f:
                    raw = _f.read()
                if not raw.strip():
                    return None
                _d = _json.loads(raw)
                closes  = _d.get('closes', [])
                if len(closes) < 20:
                    return None
                opens   = _d.get('opens',   closes)
                highs   = _d.get('highs',   closes)
                lows    = _d.get('lows',    closes)
                vols    = _d.get('volumes', [0]*len(closes))
                times   = _d.get('times',  ['']*len(closes))

                # ── حساب المؤشرات ────────────────────────────────────
                ema9   = _calc_ema_series(closes, 9)
                ema21  = _calc_ema_series(closes, 21)
                ema200 = _calc_ema_series(closes, 200)
                rsi_series = _calc_rsi_series(closes)
                # محاذاة RSI مع الأعمدة — pad من اليسار بـ None
                _rsi_pad = len(closes) - len(rsi_series)
                if _rsi_pad > 0:
                    rsi_series = [None] * _rsi_pad + rsi_series
                _macd_h, _macd_l, _macd_s = _calc_macd_series(closes)
                # محاذاة MACD مع الأعمدة — pad من اليسار بـ 0/None
                _macd_pad = len(closes) - len(_macd_h)
                if _macd_pad > 0 and _macd_h:
                    _macd_h = [0]   * _macd_pad + _macd_h
                    _macd_l = [None]* _macd_pad + _macd_l
                    _macd_s = [None]* _macd_pad + _macd_s
                vwap_v = calc_vwap(closes[-78:], highs[-78:],
                                   lows[-78:], vols[-78:])
                _ph = max(highs[-20:]) if len(highs) >= 20 else highs[-1]
                _pl = min(lows[-20:])  if len(lows)  >= 20 else lows[-1]
                pivots     = calc_pivot_points(_ph, _pl, closes[-1])
                res_z, sup_z         = calc_sr_levels(highs, lows, closes)
                supply_z, demand_z   = calc_supply_demand(
                    highs, lows, opens, closes)

                _obs_full = []; rev_sigs = []; _sd_zones_full = []
                try:
                    _obs_full = _strategy_module.zr_find_order_blocks(
                        opens, highs, lows, closes)
                    rev_sigs = [{'type': ob.get('type',''),
                                 'price': ob.get('mid', closes[-1]),
                                 'strength': ob.get('strength', 1),
                                 'top': ob.get('top', closes[-1]),
                                 'bottom': ob.get('bottom', closes[-1])}
                                for ob in _obs_full[:6]]
                except Exception:
                    pass
                try:
                    _sd_zones_full = _strategy_module.zr_find_sd_zones(
                        highs, lows, closes)
                except Exception:
                    pass

                return {
                    'symbol': symbol, 'tf': tf,
                    'opens': opens, 'highs': highs,
                    'lows':  lows,  'closes': closes,
                    'volumes': vols, 'times': times,
                    'ema9': ema9, 'ema21': ema21, 'ema200': ema200,
                    'rsi': rsi_series,
                    'macd_hist': _macd_h, 'macd_line': _macd_l, 'macd_sig': _macd_s,
                    'vwap': vwap_v, 'pivots': pivots,
                    'res_zones': res_z, 'sup_zones': sup_z,
                    'supply_zones': supply_z, 'demand_zones': demand_z,
                    'rev_sigs': rev_sigs, 'price': closes[-1],
                    'order_blocks': _obs_full,
                    'sd_zones':     _sd_zones_full,
                    'source': 'tv_datafeed',
                }
            except Exception as _e:
                print(f"[Chart] خطأ في قراءة {path}: {_e}")
                return None

        # حاول قراءة من tv_datafeed أولاً
        if _os.path.exists(_tv_path):
            _bars_tv = _build_bars_data_from_file(_tv_path)
            if _bars_tv:
                _age = _t.time() - _os.path.getmtime(_tv_path)
                # استخدم JSON إذا أحدث من 5 دقائق — حتى لو أقدم من 2 دقيقة
                # المؤشرات (EMA/RSI/MACD) دقيقة لأن الـ JSON يحتوي 2000 بار
                # السعر الحي يُحدَّث منفصلاً عبر push_price كل ثانية
                if _age < 300:
                    # تحديث آخر سعر بالسعر الحي إن كان متوفراً
                    _live = getattr(self, 'current_price', 0)
                    if _live and _live > 0 and _bars_tv.get('closes'):
                        _bars_tv['closes'][-1] = _live
                        _bars_tv['highs'][-1]  = max(_bars_tv['highs'][-1], _live)
                        _bars_tv['lows'][-1]   = min(_bars_tv['lows'][-1],  _live)
                        _bars_tv['price'] = _live
                    self.chart_data_ready.emit(_bars_tv)
                    if _bars_tv.get('closes'):
                        if not self.current_price or self.current_price <= 0:
                            self.current_price = _bars_tv['closes'][-1]
                    return
                else:
                    print(f"[Chart] tv_datafeed قديم ({_age:.0f}s) — سيُستخدم IBKR")

        # ── أولوية 2: إذا غير متصل → أي ملف محفوظ ───────────────────
        if not self.connected:
            _tf_files = [f'{symbol}_{tf}.json', f'{symbol}_15m.json',
                         f'{symbol}_5m.json']
            for _fname in _tf_files:
                _path = _os.path.join(_chart_dir, _fname)
                if _os.path.exists(_path):
                    _bars = _build_bars_data_from_file(_path)
                    if _bars:
                        self.chart_data_ready.emit(_bars)
                        return
            return
        _TF_MAP = {
            '1m':  ('1 min',   '2 D'),
            '5m':  ('5 mins',  '5 D'),
            '15m': ('15 mins', '10 D'),
            '1H':  ('1 hour',  '30 D'),
            '4H':  ('4 hours', '60 D'),
            '1D':  ('1 day',   '1 Y'),
        }
        bar_sz, dur = _TF_MAP.get(tf, ('15 mins', '10 D'))
        try:
            c = _make_contract(symbol)
            try: run_in_ib_thread(self.ib.qualifyContracts, c)
            except Exception: pass
            _wts = _what_to_show(symbol)
            bars = run_in_ib_thread_long(
                self.ib.reqHistoricalData, c,
                endDateTime='', durationStr=dur,
                barSizeSetting=bar_sz, whatToShow=_wts,
                useRTH=False, formatDate=1, keepUpToDate=False,
                timeout=60)
            if not bars:
                print(f"[Chart] لا بيانات: {symbol} tf={tf} wts={_wts}")
                return
            df = util.df(bars)
            closes  = df['close'].tolist()
            highs   = df['high'].tolist()
            lows    = df['low'].tolist()
            opens   = df['open'].tolist()
            vols    = df['volume'].tolist() if 'volume' in df.columns else [0]*len(closes)
            times   = [str(b.date) for b in bars]
            # حساب المؤشرات
            ema9   = _calc_ema_series(closes, 9)
            ema21  = _calc_ema_series(closes, 21)
            ema200 = _calc_ema_series(closes, 200)
            rsi_series = _calc_rsi_series(closes)
            _rsi_pad = len(closes) - len(rsi_series)
            if _rsi_pad > 0:
                rsi_series = [None] * _rsi_pad + rsi_series
            _macd_h, _macd_l, _macd_s = _calc_macd_series(closes)
            _macd_pad = len(closes) - len(_macd_h)
            if _macd_pad > 0 and _macd_h:
                _macd_h = [0]    * _macd_pad + _macd_h
                _macd_l = [None] * _macd_pad + _macd_l
                _macd_s = [None] * _macd_pad + _macd_s
            vwap   = calc_vwap(closes[-78:], highs[-78:], lows[-78:], vols[-78:])
            # Pivot Points
            _ph = max(highs[-20:]) if len(highs) >= 20 else highs[-1]
            _pl = min(lows[-20:])  if len(lows)  >= 20 else lows[-1]
            pivots = calc_pivot_points(_ph, _pl, closes[-1])
            # مناطق الانعكاس
            res_z, sup_z       = calc_sr_levels(highs, lows, closes)
            supply_z, demand_z = calc_supply_demand(highs, lows, opens, closes)
            # Order Blocks + Supply/Demand Zones من strategy.py
            _obs_full = []; _sd_zones_full = []
            try:
                _obs_full = _strategy_module.zr_find_order_blocks(opens, highs, lows, closes)
                rev_sigs  = [{'type': ob.get('type',''), 'price': ob.get('mid', closes[-1]),
                              'strength': ob.get('strength', 1), 'top': ob.get('top', closes[-1]),
                              'bottom': ob.get('bottom', closes[-1])} for ob in _obs_full[:6]]
            except Exception:
                rev_sigs = []
            try:
                _sd_zones_full = _strategy_module.zr_find_sd_zones(highs, lows, closes)
            except Exception:
                _sd_zones_full = []
            bars_data = {
                'symbol': symbol,
                'opens': opens, 'highs': highs, 'lows': lows,
                'closes': closes, 'volumes': vols, 'times': times,
                'ema9': ema9, 'ema21': ema21, 'ema200': ema200,
                'rsi': rsi_series,
                'macd_hist': _macd_h, 'macd_line': _macd_l, 'macd_sig': _macd_s,
                'vwap': vwap, 'pivots': pivots,
                'res_zones': res_z, 'sup_zones': sup_z,
                'supply_zones': supply_z, 'demand_zones': demand_z,
                'rev_sigs': rev_sigs, 'price': closes[-1],
                'order_blocks': _obs_full,
                'sd_zones':     _sd_zones_full,
            }

            # ✅ حفظ بيانات الشارت في ملف JSON للباك تست
            # يُتيح تشغيل الباك تست بدون اتصال IBKR
            try:
                import json as _json, os as _os
                _chart_dir  = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'chart_data')
                _os.makedirs(_chart_dir, exist_ok=True)
                _chart_file = _os.path.join(_chart_dir, f'{symbol}_{tf}.json')
                _save_data  = {
                    'symbol': symbol, 'tf': tf,
                    'saved_at': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'),
                    'opens': opens, 'highs': highs, 'lows': lows,
                    'closes': closes, 'volumes': vols, 'times': times,
                }
                with open(_chart_file, 'w', encoding='utf-8') as _f:
                    _json.dump(_save_data, _f, ensure_ascii=False)
            except Exception as _se:
                pass  # حفظ الملف اختياري — لا يوقف الشارت
            # تحديث current_price من آخر شمعة إذا لم يكن محدداً بعد
            if closes and (not self.current_price or self.current_price <= 0):
                self.current_price = closes[-1]
                self.ui_updater.update_price.emit(float(closes[-1]))

            # تحديث الشارت
            self.chart_data_ready.emit(bars_data)
            if self._pro_chart and hasattr(self._pro_chart, 'set_timeframe'):
                self._pro_chart.set_timeframe(tf)

            # ── تحديث لوحة تحليل الاستراتيجية فوراً ──────────────────────
            try:
                _rsi  = calc_rsi(closes[-50:]) if len(closes) >= 16 else None
                if _rsi is not None and (_rsi < 5 or _rsi > 95):
                    _rsi = None  # pre-market noise
                _adx  = calc_adx(highs[-50:], lows[-50:], closes[-50:], 14) if len(closes) >= 30 else None
                _atr  = calc_atr(highs[-20:], lows[-20:], closes[-20:], 14)
                _e9   = ema9[-1]  if ema9  else None
                _e21  = ema21[-1] if ema21 else None
                _regime = _strategy_module.detect_regime(
                    closes[-50:], highs[-50:], lows[-50:], vols[-50:]
                ) if hasattr(_strategy_module, 'detect_regime') else 'normal'
                _bias = _strategy_module.get_market_bias(
                    closes[-50:], highs[-50:], lows[-50:], vols[-50:]
                ) if hasattr(_strategy_module, 'get_market_bias') else 'neutral'

                # ── حساب call_score و put_score ──────────────────────────
                _call_score = 0
                _put_score  = 0
                _signal     = None
                try:
                    # EMA trend
                    if _e9 and _e21:
                        if _e9 > _e21:   _call_score += 3
                        else:             _put_score  += 3
                    # RSI
                    if _rsi:
                        if _rsi < 40:    _call_score += 2
                        elif _rsi > 60:  _put_score  += 2
                        elif _rsi < 50:  _call_score += 1
                        else:             _put_score  += 1
                    # ADX strength
                    if _adx and _adx >= 20:
                        if _e9 and _e21:
                            if _e9 > _e21: _call_score += 2
                            else:           _put_score  += 2
                    # VWAP
                    if vwap and closes:
                        if closes[-1] > vwap: _call_score += 2
                        else:                  _put_score  += 2
                    # Pivot
                    if pivots:
                        _pp = pivots.get('pp', 0)
                        if _pp and closes:
                            if closes[-1] > _pp: _call_score += 1
                            else:                 _put_score  += 1
                    # Regime
                    if _regime == 'super_choppy_skip':
                        _call_score = 0; _put_score = 0
                    # Signal
                    if _call_score >= 6 and _call_score > _put_score + 2:
                        _signal = 'CALL'
                    elif _put_score >= 6 and _put_score > _call_score + 2:
                        _signal = 'PUT'
                except Exception:
                    pass

                # market_type
                _mtype = 'trend' if (_adx or 0) >= 25 else 'range' if (_adx or 0) < 15 else 'normal'

                # why labels
                _call_why = []
                _put_why  = []
                if _e9 and _e21:
                    if _e9 > _e21: _call_why.append(f'EMA9>{_e21:.0f}')
                    else:          _put_why.append(f'EMA9<{_e21:.0f}')
                if _rsi:
                    if _rsi < 40:   _call_why.append(f'RSI={_rsi:.0f}↓')
                    elif _rsi > 60: _put_why.append(f'RSI={_rsi:.0f}↑')
                if vwap and closes:
                    if closes[-1] > vwap: _call_why.append('فوق VWAP')
                    else:                  _put_why.append('تحت VWAP')

                self.ui_updater.update_analysis.emit({
                    'rsi':         _rsi,
                    'adx':         _adx,
                    'atr':         _atr,
                    'ema9':        _e9,
                    'ema21':       _e21,
                    'regime':      _regime,
                    'bias':        _bias,
                    'call_score':  _call_score,
                    'put_score':   _put_score,
                    'signal':      _signal,
                    'direction':   _signal,
                    'market_type': _mtype,
                    'market_bias': _bias,
                    'call_why':    ' | '.join(_call_why),
                    'put_why':     ' | '.join(_put_why),
                    'why':         f"ADX={_adx:.0f} | RSI={_rsi:.0f} | {'فوق' if vwap and closes and closes[-1]>vwap else 'تحت'} VWAP" if _adx and _rsi else '',
                })
            except Exception:
                pass
        except Exception as e:
            print(f"[Chart] {e}")

    @pyqtSlot(object)
    def _draw_zones_on_chart(self, plot_widget, bars_data):
        """
        يرسم Order Blocks و Supply/Demand Zones على أي pyqtgraph PlotWidget.
        - demand_ob / demand  → مستطيل أخضر شفاف
        - supply_ob / supply  → مستطيل أحمر شفاف
        - Fresh zones أغمق، المستهلكة أفتح
        """
        if not plot_widget:
            return
        # احذف الزونات القديمة
        try:
            _old = getattr(plot_widget, '_zone_items', [])
            for _item in _old:
                try: plot_widget.removeItem(_item)
                except Exception: pass
            plot_widget._zone_items = []
        except Exception:
            plot_widget._zone_items = []

        all_zones = (
            list(bars_data.get('order_blocks', []) or []) +
            list(bars_data.get('sd_zones', []) or [])
        )
        if not all_zones:
            return

        n_bars  = len(bars_data.get('closes', []))
        _items  = []

        for z in all_zones:
            top    = z.get('top', 0)
            bottom = z.get('bottom', 0)
            if not top or not bottom or top <= bottom:
                continue
            ztype  = z.get('type', '')
            fresh  = z.get('fresh', True)
            strength = z.get('strength', 1.5)

            is_demand = 'demand' in ztype
            is_supply = 'supply' in ztype

            if is_demand:
                alpha  = 55 if fresh else 28
                color  = (0, 200, 100, alpha)   # أخضر
                border = pg.mkPen(color=(0, 200, 100, 160 if fresh else 80),
                                  width=0.8, style=Qt.SolidLine)
            elif is_supply:
                alpha  = 55 if fresh else 28
                color  = (255, 80, 80, alpha)    # أحمر
                border = pg.mkPen(color=(255, 80, 80, 160 if fresh else 80),
                                  width=0.8, style=Qt.SolidLine)
            else:
                continue

            # LinearRegionItem — يمتد بعرض الشارت كله
            region = pg.LinearRegionItem(
                values=[bottom, top],
                orientation='horizontal',
                movable=False,
                brush=pg.mkBrush(*color),
                pen=border,
            )
            region.setZValue(-5)   # تحت الشمعات

            # Label يظهر عند حافة اليمين
            label_text  = (
                f"{'D-OB' if 'demand_ob' in ztype else 'Demand'} "
                f"{bottom:.2f}–{top:.2f} "
                f"{'★' if fresh else '○'}"
            ) if is_demand else (
                f"{'S-OB' if 'supply_ob' in ztype else 'Supply'} "
                f"{bottom:.2f}–{top:.2f} "
                f"{'★' if fresh else '○'}"
            )
            label_color = '#00c864' if is_demand else '#ff5050'
            label = pg.TextItem(
                text=label_text,
                color=label_color,
                anchor=(1, 0.5),
            )
            label.setPos(n_bars, (top + bottom) / 2)
            label.setFont(__import__('PyQt5.QtGui', fromlist=['QFont']).QFont('Consolas', 8))
            label.setZValue(10)

            try:
                plot_widget.addItem(region)
                plot_widget.addItem(label)
                _items += [region, label]
            except Exception:
                pass

        plot_widget._zone_items = _items

    def _apply_chart_data(self, bars_data):
        """تطبيق البيانات على ProChart في main thread"""
        # [PROFILING]
        self._prof_last_action = '_apply_chart_data'
        _prof_acd_t0 = time.perf_counter()
        # ── حفظ بيانات الأعمدة لتحديث المؤشرات الجانبية بالسعر الحي ──
        try:
            _c = bars_data.get('closes', [])
            _h = bars_data.get('highs', _c)
            _l = bars_data.get('lows',  _c)
            _v = bars_data.get('volumes', [10000] * len(_c))
            if _c:
                self._last_closes  = list(_c)
                self._last_highs   = list(_h)
                self._last_lows    = list(_l)
                self._last_volumes = list(_v)
        except Exception:
            pass
        if self._pro_chart:
            # احفظ نطاق العرض الحالي قبل الرسم
            _xrange = None
            _yrange = None
            _had_data = bool(self._pro_chart._closes)
            try:
                _vb = self._pro_chart._price_plot.getViewBox()
                _xrange = _vb.viewRange()[0]
                _yrange = _vb.viewRange()[1]
            except Exception:
                pass

            _prof_sd_t0 = time.perf_counter()
            self._pro_chart.set_data(bars_data, max_bars=300)
            print(f'[PROF] set_data: {(time.perf_counter()-_prof_sd_t0)*1000:.0f}ms  bars={len(bars_data.get("closes",[]))}', flush=True)

            # ── رسم Order Blocks + S/D Zones ────────────────────────
            try:
                _plot = getattr(self._pro_chart, '_price_plot', None)
                if _plot:
                    self._draw_zones_on_chart(_plot, bars_data)
            except Exception as _ze:
                print(f"[Zones] {_ze}")

            # استعد نطاق العرض بعد الرسم إذا المستخدم حرّك الشارت
            if _had_data and _xrange:
                try:
                    n = self._pro_chart._n
                    # إذا المستخدم ما زال ينظر لآخر الشارت → لا تغيّر
                    # إذا حرّك للوراء → استعد موضعه
                    if _xrange[1] < n - 5:
                        self._pro_chart._price_plot.setXRange(
                            _xrange[0], _xrange[1], padding=0)
                        if _yrange:
                            self._pro_chart._price_plot.setYRange(
                                _yrange[0], _yrange[1], padding=0)
                except Exception:
                    pass

            live = getattr(self, 'current_price', None)
            if live and live > 0:
                self._pro_chart.update_live(live)
            try:
                self._sync_trade_plan_to_chart(symbol=bars_data.get('symbol', getattr(self, 'current_symbol', '')))
            except Exception:
                pass

        # رسم الصفقات المفتوحة على الشارت
        for pos in self.position_manager.get_all():
            try:
                if self._pro_chart:
                    self._pro_chart.draw_trade({
                        'direction':   pos.get('opt_type', 'CALL'),
                        'entry_price': pos.get('entry_premium', 0),
                        'stop_loss':   pos.get('stop_loss', 0),
                        'take_profit': pos.get('take_profit', 0),
                        'symbol':      pos.get('symbol', ''),
                        'contracts':   pos.get('contracts', 1),
                    })
                    break
            except Exception:
                pass

        # تحديث مناطق الانعكاس
        try:
            self._update_reversal_panel(
                bars_data.get('res_zones', []),
                bars_data.get('sup_zones', []),
                bars_data.get('supply_z',  []),
                bars_data.get('demand_z',  []),
                bars_data.get('pivots',    {}),
                bars_data.get('rev_sigs',  []),
                bars_data.get('price',     0),
            )
        except Exception as _re:
            print(f"[ReversePanel] {_re}")
        # [PROFILING]
        print(f'[PROF] _apply_chart_data total: {(time.perf_counter()-_prof_acd_t0)*1000:.0f}ms', flush=True)
        self._prof_last_action = '_apply_chart_data:done'

    def _open_chart_window(self):
        """فتح الشارت JavaScript في نافذة مستقلة"""
        if hasattr(self, '_chart_win') and self._chart_win and self._chart_win.isVisible():
            self._chart_win.raise_()
            self._chart_win.activateWindow()
            return

        sym = getattr(self, 'current_symbol', 'SPY')
        _JSChart = ProChartWidget

        win = QDialog(None)
        win.setWindowTitle(f"📊 {sym}")
        _scr_cw = QApplication.primaryScreen().availableGeometry()
        win.resize(min(1400, _scr_cw.width()  - 80),
                   min(860,  _scr_cw.height() - 80))
        win.setStyleSheet("background:#0b0e11; color:#eaecef;")
        win.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self._chart_win = win

        layout = QVBoxLayout(win)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── شريط أعلى: رمز + أزرار TF ──────────────────────────────────
        top = QHBoxLayout()
        top.setContentsMargins(6, 4, 6, 4)
        top.setSpacing(4)

        _sym_input = QLineEdit(sym)
        _sym_input.setMaximumWidth(80)
        _sym_input.setStyleSheet(
            "background:#1e2329;color:#eaecef;border:1px solid #2b3139;"
            "border-radius:3px;padding:2px 6px;font:bold 12px Consolas;")
        top.addWidget(_sym_input)

        _load_btn = QPushButton("⟳")
        _load_btn.setMaximumWidth(28)
        _load_btn.setStyleSheet(
            "background:#f0b90b;color:#000;border-radius:3px;"
            "font:bold 13px Consolas;padding:2px;")
        top.addWidget(_load_btn)
        top.addSpacing(8)

        _cur_tf = ['15m']
        _tf_btns = {}
        _btn_style = (
            "QPushButton{background:#1e2329;color:#848e9c;border:1px solid #2b3139;"
            "border-radius:3px;padding:2px 8px;font:bold 11px Consolas;min-width:32px;}"
            "QPushButton:checked{background:#f0b90b;color:#000;border-color:#f0b90b;}")

        for lbl, key in [('1m','1m'),('5m','5m'),('15m','15m'),
                          ('1H','1H'),('4H','4H'),('1D','1D')]:
            b = QPushButton(lbl)
            b.setCheckable(True)
            b.setChecked(key == '15m')
            b.setStyleSheet(_btn_style)
            top.addWidget(b)
            _tf_btns[key] = b

        top.addStretch()

        top_w = QWidget()
        top_w.setStyleSheet("background:#161a1e;border-bottom:1px solid #2b3139;")
        top_w.setLayout(top)
        layout.addWidget(top_w)

        # ── الشارت ───────────────────────────────────────────────────────
        chart = _JSChart(win)
        self._chart_win_chart = chart
        layout.addWidget(chart, stretch=1)

        # ── ربط data_ready لرسم الزونات بعد تحميل البيانات ──────────
        def _on_chart_win_data(data):
            try:
                _plot = getattr(chart, '_price_plot', None)
                if _plot:
                    self._draw_zones_on_chart(_plot, data)
            except Exception as _ze:
                print(f"[ChartWin Zones] {_ze}")
        try:
            chart.data_ready.connect(_on_chart_win_data)
        except Exception:
            pass

        # ── دالة التحميل ──────────────────────────────────────────────────
        def _load(s=None, tf=None):
            _s  = (s or _sym_input.text().upper().strip() or sym)
            _tf = tf or _cur_tf[0]
            _cur_tf[0] = _tf
            win.setWindowTitle(f"📊 {_s} — {_tf}")
            # تحديث أزرار TF
            for k, b in _tf_btns.items():
                b.setChecked(k == _tf)
            # أرسل TF للشارت JS (للعداد)
            if hasattr(chart, 'set_timeframe'):
                chart.set_timeframe(_tf)
            # جلب البيانات
            threading.Thread(
                target=self._fetch_chart_for_win,
                args=(chart, _s, _tf), daemon=True).start()

        # ── ربط الأزرار ───────────────────────────────────────────────────
        for key, btn in _tf_btns.items():
            btn.clicked.connect(lambda _, k=key: _load(tf=k))

        _load_btn.clicked.connect(lambda: _load())
        _sym_input.returnPressed.connect(lambda: _load())

        win.show()
        _load(sym, '15m')

        # ── إذا توجد صفقة مفتوحة → ارسمها فور تحميل الشارت ──────
        def _draw_pending():
            import time as _t; _t.sleep(2.5)  # انتظر تحميل الصفحة
            _td = getattr(self, '_last_trade_data', None)
            if _td and hasattr(chart, 'draw_trade'):
                try: chart.draw_trade(_td)
                except Exception: pass
        import threading as _th
        _th.Thread(target=_draw_pending, daemon=True).start()

    def _fetch_chart_for_win(self, chart_widget, sym, tf_key):
        """جلب بيانات لنافذة الشارت المستقلة وتحديثها"""
        _TF_MAP = {
            '1m':  ('1 min',   '2 D'),   '5m':  ('5 mins',  '5 D'),
            '15m': ('15 mins', '10 D'),  '1H':  ('1 hour',  '30 D'),
            '4H':  ('4 hours', '60 D'),  '1D':  ('1 day',   '1 Y'),
        }
        if not self.connected: return
        bar_sz, dur = _TF_MAP.get(tf_key, ('15 mins', '10 D'))
        try:
            c = _make_contract(sym)
            try: run_in_ib_thread(self.ib.qualifyContracts, c)
            except Exception: pass
            _wts = _what_to_show(sym)
            bars = run_in_ib_thread_long(
                self.ib.reqHistoricalData, c,
                endDateTime='', durationStr=dur,
                barSizeSetting=bar_sz, whatToShow=_wts,
                useRTH=False, formatDate=1, keepUpToDate=False, timeout=60)
            if not bars: return
            df = util.df(bars)
            closes = df['close'].tolist()
            highs  = df['high'].tolist()
            lows   = df['low'].tolist()
            opens  = df['open'].tolist()
            vols   = df['volume'].tolist() if 'volume' in df.columns else [0]*len(closes)
            times  = [str(b.date) for b in bars]
            ema9   = _calc_ema_series(closes, 9)
            ema21  = _calc_ema_series(closes, 21)
            ema200 = _calc_ema_series(closes, 200)
            vwap   = calc_vwap(closes[-78:], highs[-78:], lows[-78:], vols[-78:])
            _ph = max(highs[-20:]) if len(highs) >= 20 else highs[-1]
            _pl = min(lows[-20:])  if len(lows)  >= 20 else lows[-1]
            pivots = calc_pivot_points(_ph, _pl, closes[-1])
            _obs_win = []; _sd_win = []
            try:
                _obs_win = _strategy_module.zr_find_order_blocks(opens, highs, lows, closes)
            except Exception:
                pass
            try:
                _sd_win  = _strategy_module.zr_find_sd_zones(highs, lows, closes)
            except Exception:
                pass
            data = {'opens': opens, 'highs': highs, 'lows': lows,
                    'closes': closes, 'volumes': vols, 'times': times,
                    'ema9': ema9, 'ema21': ema21, 'ema200': ema200,
                    'vwap': vwap, 'pivots': pivots,
                    'order_blocks': _obs_win,
                    'sd_zones':     _sd_win}
            # تحديث في main thread
            if hasattr(chart_widget, 'set_timeframe'):
                chart_widget.set_timeframe(tf_key)
            chart_widget.data_ready.emit(data)
        except Exception as e:
            print(f"[ChartWin] {e}")

    def _refresh_bookmap(self):
        """Book Map محذوف — stub آمن"""
        pass

    @pyqtSlot(str)
    def _update_bookmap_ui(self, data_str):
        """Book Map محذوف — stub آمن"""
        pass

    # -----------------------------------------------
    # جلب البيانات
    # -----------------------------------------------
    def _fetch_expiries_for(self, symbol):
        QMetaObject.invokeMethod(
            self.options_status, "setText", Qt.QueuedConnection,
            Q_ARG(str, f"جاري جلب تواريخ {symbol} من IBKR...")
        )
        def _run(sym=symbol):
            # انتظر حتى يُجلب السعر (max 5 ثواني)
            import time as _t
            for _ in range(50):
                if getattr(self, 'current_price', 0) > 0:
                    break
                _t.sleep(0.1)
            try:
                c = _make_contract(sym)
                try: run_in_ib_thread(self.ib.qualifyContracts, c)
                except Exception: pass
                _con_id_exp = getattr(c, 'conId', 0)
                chains = None
                if _con_id_exp:
                    chains = run_in_ib_thread(self.ib.reqSecDefOptParams,
                        underlyingSymbol  = c.symbol,
                        futFopExchange    = "",
                        underlyingSecType = c.secType,
                        underlyingConId   = _con_id_exp
                    )
                # Fallback: تواريخ الجمعة إذا فشل reqSecDefOptParams
                if not chains:
                    from datetime import timedelta as _td2
                    _td_now = datetime.now()
                    _fb_dates = []
                    _fb_d = _td_now + _td2(days=1)
                    while len(_fb_dates) < 12:
                        if _fb_d.weekday() == 4:
                            _fb_dates.append(_fb_d.strftime('%Y%m%d'))
                        _fb_d += _td2(days=1)
                    class _FC2:
                        def __init__(self, exp): self.expirations = exp
                    chains = [_FC2(_fb_dates)]
                all_d = set()
                for ch in (chains or []):
                    all_d.update(ch.expirations)
                today = datetime.now().strftime("%Y%m%d")
                valid = sorted(d for d in all_d if d >= today)
                if valid:
                    self.ui_updater.update_expiries.emit(valid)
                    QMetaObject.invokeMethod(
                        self.options_status, "setText", Qt.QueuedConnection,
                        Q_ARG(str, f"تم جلب {len(valid)} تاريخ لـ {sym} ✓")
                    )
                else:
                    QMetaObject.invokeMethod(
                        self.options_status, "setText", Qt.QueuedConnection,
                        Q_ARG(str, f"لا تواريخ متاحة لـ {sym}")
                    )
                    self.update_options_table()
            except Exception as e:
                print(f"Expiry error {sym}: {e}")
                self.update_options_table()
        threading.Thread(target=_run, daemon=True).start()

    def update_options_table(self):
        # منع تكرار الطلبات المتزامنة
        if getattr(self, '_options_loading', False):
            return
        # لا تطلب أوبشن قبل جلب السعر الحقيقي
        if not self.current_price or self.current_price <= 0:
            return
        idx = self.expiry_combo.currentIndex()
        if idx < 0:
            return
        expiry = self.expiry_combo.itemData(idx) or self.expiry_combo.currentText().replace("-", "").replace(" ", "")[:8]

        # ── تحقق أن التاريخ ليس في الماضي ─────────────────────
        try:
            _exp_date = datetime.strptime(expiry[:8], '%Y%m%d').date()
            if _exp_date < datetime.now().date():
                self._set_fallback_dates()
                return
        except Exception:
            pass
        symbol = self.current_symbol
        price  = self.current_price
        exch   = _option_exchange(symbol)
        QMetaObject.invokeMethod(
            self.options_status, "setText", Qt.QueuedConnection,
            Q_ARG(str, f"جاري جلب عقود {symbol} - {expiry[:4]}/{expiry[4:6]}/{expiry[6:]}...")
        )
        def _run(sym=symbol, exp=expiry, prc=price, ex=exch):
            try:
                # جلب السعر الحالي من IB إذا كنا متصلين (لضمان دقة الـ strikes)
                if self.connected:
                    try:
                        c_tmp = _make_contract(sym)
                        try: run_in_ib_thread(self.ib.qualifyContracts, c_tmp)
                        except Exception: pass
                        run_in_ib_thread(self.ib.reqMarketDataType, 4)  # Delayed-Frozen
                        tk_tmp = req_mkt_data_safe(self.ib, c_tmp, '')[0]
                        for _ in range(15):
                            ib_pump(0.1)
                            fresh = run_in_ib_thread(lambda t=tk_tmp: t.last or t.close or ((t.bid+t.ask)/2 if t.bid and t.ask else None))
                            if fresh and _valid(fresh):
                                prc = fresh; break
                        run_in_ib_thread(self.ib.cancelMktData, c_tmp)
                        run_in_ib_thread(self.ib.reqMarketDataType, 1)
                    except Exception:
                        pass  # نستخدم prc الحالية إذا فشل

                # حساب الـ step والـ strikes بشكل صحيح لكل مدى سعري
                if prc > 3000:      # SPX, NDX
                    step = 25
                elif prc > 500:     # SPY, QQQ
                    step = 5
                elif prc > 100:     # أسهم متوسطة
                    step = 2.5
                elif prc > 20:      # أسهم رخيصة
                    step = 1
                elif prc > 5:       # أسهم رخيصة جداً
                    step = 0.5
                else:               # penny stocks - غالباً لا يوجد options
                    step = 0.5
                base    = round(prc / step) * step
                strikes = [round(base + (i * step), 2) for i in range(-10, 11)]
                # تأكد أن كل الـ strikes موجبة
                strikes = [s for s in strikes if s > 0]
                rows    = []
                # ── تقليل الـ strikes للأقرب فقط (±3 من ATM) ──
                strikes = strikes[max(0,len(strikes)//2-3):len(strikes)//2+4]

                for strike in strikes:
                    cb = ca = pb = pa = "--"
                    if self.connected:
                        try:
                            _tc = 'SPXW' if sym=='SPX' else ('XSP' if sym=='XSP' else '')
                            cc = Option(sym, exp, strike, 'C', ex, tradingClass=_tc) if _tc else Option(sym, exp, strike, 'C', ex)
                            pc = Option(sym, exp, strike, 'P', ex, tradingClass=_tc) if _tc else Option(sym, exp, strike, 'P', ex)

                            # تحقق من صحة العقد أولاً
                            try:
                                _qc = run_in_ib_thread(self.ib.qualifyContracts, cc, pc)
                                if not _qc or len(_qc) < 2:
                                    continue  # strike غير موجود في IBKR
                            except Exception:
                                continue  # تخطي strikes غير موجودة

                            # generic_ticks فارغ لتجنب Error 354
                            run_in_ib_thread(self.ib.reqMarketDataType, 3)
                            ct = req_mkt_data_safe(self.ib, cc, '')[0]
                            pt = req_mkt_data_safe(self.ib, pc, '')[0]

                            for _w in range(10):
                                ib_pump(0.1)
                                cv = run_in_ib_thread(lambda t=ct: ((getattr(t, 'bid', None), getattr(t, 'ask', None)) if t is not None else (None, None)))
                                pv = run_in_ib_thread(lambda t=pt: ((getattr(t, 'bid', None), getattr(t, 'ask', None)) if t is not None else (None, None)))
                                if (_valid(cv[0]) or _valid(cv[1])) and (_valid(pv[0]) or _valid(pv[1])):
                                    break

                            cv = run_in_ib_thread(lambda t=ct: ((getattr(t, 'bid', None), getattr(t, 'ask', None)) if t is not None else (None, None)))
                            pv = run_in_ib_thread(lambda t=pt: ((getattr(t, 'bid', None), getattr(t, 'ask', None)) if t is not None else (None, None)))

                            if _valid(cv[0]): cb = f"${cv[0]:.2f}"
                            if _valid(cv[1]): ca = f"${cv[1]:.2f}"
                            if _valid(pv[0]): pb = f"${pv[0]:.2f}"
                            if _valid(pv[1]): pa = f"${pv[1]:.2f}"

                            try: run_in_ib_thread(self.ib.cancelMktData, cc)
                            except Exception: pass
                            try: run_in_ib_thread(self.ib.cancelMktData, pc)
                            except Exception: pass
                            ib_pump(0.05)
                        except Exception:
                            pass
                    row = {'strike':strike,'call_bid':cb,'call_ask':ca,'put_bid':pb,'put_ask':pa}
                    if strike > prc:
                        row['call_color'] = "#05c46b"  # CALL OTM = أخضر
                        row['put_color']  = "#ff5e57"  # PUT ITM = أحمر
                    elif strike < prc:
                        row['call_color'] = "#05c46b"  # CALL ITM = أخضر
                        row['put_color']  = "#ff5e57"  # PUT OTM = أحمر
                    else:
                        row['atm'] = True  # ATM = أزرق
                    rows.append(row)
                self.ui_updater.update_options.emit(rows)
                from datetime import datetime as _dt
                _now = _dt.now().strftime("%H:%M:%S")
                QMetaObject.invokeMethod(
                    self.options_status, "setText", Qt.QueuedConnection,
                    Q_ARG(str, f"✅ {sym} آخر تحديث: {_now}")
                )
            except Exception as e:
                print(f"Options error: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def _refresh_price_only(self):
        """تحديث السعر اللحظي كل ثانية من الـ streaming"""
        if not self.connected: return
        if getattr(self, '_price_refreshing', False): return
        self._price_refreshing = True
        try:
            self._do_refresh_price()
        finally:
            self._price_refreshing = False

    def _do_refresh_price(self):
        if not self.connected: return
        # ── قراءة من streaming موجود (لا انتظار) ───────
        sym = getattr(self, 'current_symbol', None)
        if sym and sym in self._streaming_tickers:
            tk = self._streaming_tickers[sym]
            try:
                vals = run_in_ib_thread(
                    lambda t=tk: (t.last, t.bid, t.ask, t.close))
                last, bid, ask, close = vals
                price = None
                if _valid(last):                   price = last
                elif _valid(bid) and _valid(ask):  price = round((bid+ask)/2,4)
                elif _valid(close):                price = close
                if price:
                    self.current_price = float(price)
                    self.ui_updater.update_price.emit(float(price))
                    # ── تحديث ساعة السوق ──────────────
                    import pytz
                    _est = pytz.timezone('US/Eastern')
                    _now = __import__('datetime').datetime.now(_est)
                    self.ui_updater.update_clock.emit(_now.strftime('%H:%M:%S EST'))
            except Exception: pass
        elif sym:
            # ابدأ streaming إذا لم يكن موجوداً
            self._start_symbol_streaming(sym)

    def _refresh_local_stats(self):
        """تحديث الإحصائيات المحلية (PnL + صفقات) بدون اتصال IBKR"""
        try:
            rm = self.risk_manager
            # PnL اليوم
            pnl = rm.daily_pnl
            pnl_color = "#05c46b" if pnl >= 0 else "#ff5e57"
            self.pnl_label.setText(f"PnL اليوم: ${pnl:+.2f}")
            self.pnl_label.setStyleSheet(
                f"color:{pnl_color}; font-weight:bold; font-size:13px;")
            # صفقات مفتوحة
            self.open_trades_label.setText(
                f"صفقات مفتوحة: {rm.open_trades}/{rm.max_open_trades}")
            # ── ميزانية الصفقة من الرصيد الفعلي ─────────────
            bal = self.account_balance or 0
            if bal > 0 and hasattr(self, 'budget_label'):
                _trade_budget = bal * rm.cost_pct
                _max_c_50  = int(_trade_budget / 50)   # عقود بسعر $50
                _max_c_100 = int(_trade_budget / 100)  # عقود بسعر $100
                self.budget_label.setText(
                    f"💼 ميزانية: ${_trade_budget:,.0f} ({rm.cost_pct*100:.0f}%) | "
                    f"نطاق $50-$100 | {_max_c_100}-{_max_c_50} عقد")
                # ✅ مزامنة: تأكد أن الواجهة تعكس القيم الحقيقية
                if hasattr(self, 'cost_pct_spin'):
                    _ui_val = self.cost_pct_spin.value()
                    _real_val = round(rm.cost_pct * 100, 1)
                    if abs(_ui_val - _real_val) > 0.05:
                        self.cost_pct_spin.blockSignals(True)
                        self.cost_pct_spin.setValue(_real_val)
                        self.cost_pct_spin.blockSignals(False)
                if hasattr(self, 'loss_pct_spin'):
                    _ui_loss = self.loss_pct_spin.value()
                    _real_loss = round(rm.loss_pct * 100, 1)
                    if abs(_ui_loss - _real_loss) > 0.05:
                        self.loss_pct_spin.blockSignals(True)
                        self.loss_pct_spin.setValue(_real_loss)
                        self.loss_pct_spin.blockSignals(False)
            if bal > 0:
                if hasattr(self, 'loss_val_label'):
                    self.loss_val_label.setText(f"= ${bal * rm.loss_pct:,.0f}")
                if hasattr(self, 'cost_val_label'):
                    self.cost_val_label.setText(f"= ${bal * rm.cost_pct:,.0f}")
        except Exception:
            pass

    def _refresh_display(self):
        """تحديث الرصيد من IBKR (كل 30 ثانية)"""
        self._update_regime_display()
        if not self.connected or not self.account:
            return
        if getattr(self, '_balance_fetching', False):
            return
        self._balance_fetching = True
        def _bal():
            try:
                bal = None
                for item in run_in_ib_thread(self.ib.accountSummary, self.account):
                    if item.tag == 'NetLiquidation':
                        bal = float(item.value); break
                    if item.tag == 'TotalCashValue':
                        bal = float(item.value)
                if bal:
                    self.ui_updater.update_cash.emit(bal)
            except Exception:
                pass
            finally:
                self._balance_fetching = False
        threading.Thread(target=_bal, daemon=True).start()

    # ══════════════════════════════════════════════════════════════
    # tv_datafeed — تحديث تلقائي لبيانات الشارت في الخلفية
    # ══════════════════════════════════════════════════════════════
    def _start_tv_datafeed(self):
        """
        يشغّل tv_datafeed كـ Thread داخلي — بدون عمليات خارجية أو نوافذ.
        يعمل داخل نفس البرنامج سواء كـ .py أو .exe مجمّع.
        """
        # [PROFILING]
        self._prof_last_action = '_start_tv_datafeed'
        _prof_tvdf_t0 = time.perf_counter()
        import threading as _thr

        # منع تشغيل نسخة ثانية
        existing = getattr(self, '_tv_thread', None)
        if existing is not None and existing.is_alive():
            self._on_bot_scan_signal("⚠️ tv_datafeed يعمل مسبقاً — لا تشغيل مجدد")
            return

        # علامة الإيقاف
        self._tv_stop_event = _thr.Event()

        def _run():
            try:
                from tv_datafeed import TVDataFeed
                import time as _time, os as _os, json as _json

                _symbols  = list(getattr(self, "auto_bot_scan_symbols", X1_SCAN_SYMBOLS))
                _tfs      = ["15", "60", "1D"]
                _bars     = 500
                _interval = 60          # ثانية بين كل دورة
                _partial  = 50          # شموع في الدورات السريعة
                _keep     = 2000

                tv = TVDataFeed()       # يقرأ config.txt تلقائياً

                # تحميل iv_history المحفوظ
                _iv_path = _os.path.join(_app_dir(), "iv_cache.json")
                iv_history = {}
                if _os.path.exists(_iv_path):
                    try:
                        with open(_iv_path, "r") as _f:
                            _old = _json.load(_f)
                        iv_history = _old.get("iv_history", {})
                    except Exception:
                        pass

                _DAILY_TFS   = {"1D", "1d"}
                _partial_tfs = [t for t in _tfs if t not in _DAILY_TFS]
                _daily_tfs   = [t for t in _tfs if t in _DAILY_TFS]
                _last_daily  = 0.0
                _cycle       = 0

                self._safe_log("✅ tv_datafeed بدأ داخل البرنامج")

                while not self._tv_stop_event.is_set():
                    _cycle += 1
                    try:
                        if _cycle == 1:
                            # أول دورة: جلب كامل
                            tv.fetch_all(symbols=_symbols,
                                         timeframes=_tfs,
                                         bars_override=_bars)
                            _last_daily = _time.time()
                        else:
                            # دورات لاحقة: جلب جزئي سريع
                            if _partial_tfs:
                                tv.fetch_partial(symbols=_symbols,
                                                 timeframes=_partial_tfs,
                                                 partial_bars=_partial,
                                                 keep=_keep)
                            # تحديث 1D كل 4 ساعات
                            if _daily_tfs and (_time.time() - _last_daily) >= 4 * 3600:
                                tv.fetch_all(symbols=_symbols,
                                             timeframes=_daily_tfs,
                                             bars_override=_bars)
                                _last_daily = _time.time()

                        # تحديث IV كل 4 دورات
                        if _cycle % 4 == 1:
                            iv_now = tv.get_iv_all(_symbols)
                            for s, v in iv_now.items():
                                iv_history.setdefault(s, []).append(v)
                                iv_history[s] = iv_history[s][-260:]
                            tv.save_iv_cache(iv_now, iv_history)

                    except Exception as _ex:
                        # خطأ في دورة واحدة — لا يوقف الـ loop
                        pass

                    # انتظر مع مراقبة إشارة الإيقاف
                    self._tv_stop_event.wait(timeout=_interval)

            except ImportError:
                self._safe_log("❌ tv_datafeed: تعذّر تحميل الوحدة")
            except Exception as _e:
                self._safe_log(f"❌ tv_datafeed توقف: {_e}")

        self._tv_thread = _thr.Thread(target=_run, daemon=True, name="TVDataFeed")
        self._tv_thread.start()
        print(f'[PROF] _start_tv_datafeed (UI-thread setup): {(time.perf_counter()-_prof_tvdf_t0)*1000:.0f}ms', flush=True)
        self._prof_last_action = '_start_tv_datafeed:done'
        self._on_bot_scan_signal("🔄 tv_datafeed يُحمّل البيانات (أول دورة)...")

    def closeEvent(self, event):
        """إغلاق نظيف — أوقف كل الـ threads بدون تجميد"""
        # ── أوقف tv_datafeed thread (التلقائي) ──────────────────
        _stop_ev = getattr(self, '_tv_stop_event', None)
        if _stop_ev is not None:
            _stop_ev.set()
        _tv_thr = getattr(self, '_tv_thread', None)
        if _tv_thr is not None and _tv_thr.is_alive():
            _tv_thr.join(timeout=3)

        # ── أوقف DataFeed thread (اليدوي) ────────────────────────
        _df_stop = getattr(self, '_datafeed_stop_event', None)
        if _df_stop is not None:
            _df_stop.set()
        _df_thr = getattr(self, '_datafeed_thread', None)
        if _df_thr is not None and _df_thr.is_alive():
            _df_thr.join(timeout=3)
        self._datafeed_proc = None

        # ── أوقف البوت ─────────────────────────────────────────
        if self.auto_bot and self.auto_bot.isRunning():
            try:
                self.auto_bot.stop()
            except Exception:
                pass
            # انتظر حتى 3 ثوانٍ ثم أجبره على التوقف
            if not self.auto_bot.wait(3000):
                self.auto_bot.terminate()
                self.auto_bot.wait(1000)
            self.auto_bot = None

        # ── أوقف MonitorThread ─────────────────────────────────
        if hasattr(self, 'monitor_thread') and self.monitor_thread:
            if self.monitor_thread.isRunning():
                try:
                    self.monitor_thread.stop()
                except Exception:
                    pass
                if not self.monitor_thread.wait(2000):
                    self.monitor_thread.terminate()
                    self.monitor_thread.wait(1000)
            self.monitor_thread = None

        # ── أوقف streaming ─────────────────────────────────────
        try:
            if hasattr(self, '_streaming_tickers'):
                for sym in list(self._streaming_tickers.keys()):
                    try:
                        self._stop_symbol_streaming(sym)
                    except Exception:
                        pass
        except Exception:
            pass

        # ── أوقف IB event loop thread ──────────────────────────
        try:
            global _ib_loop, _ib_thread
            if _ib_loop and _ib_loop.is_running():
                _ib_loop.call_soon_threadsafe(_ib_loop.stop)
            if _ib_thread and _ib_thread.is_alive():
                _ib_thread.join(timeout=2.0)
        except Exception:
            pass

        # ── قطع اتصال IBKR في background thread (لا يُجمّد) ───
        if self.connected:
            try:
                import threading as _thr
                _thr.Thread(
                    target=lambda: self.ib.disconnect(),
                    daemon=True
                ).start()
            except Exception:
                pass

        event.accept()




# ══════════════════════════════════════════════════════════════════════════════
# V3 STRICT LIVE PATCH
# الهدف: البوت ينفذ خطة الاستراتيجية كما هي، وطبقة المخاطر للحماية فقط
# ══════════════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════
# AnalyzerSignalBot النظيف — يستخدم ExecutionEngine مباشرة
# ══════════════════════════════════════════════════════════════════
class AnalyzerSignalBot(QThread):
    signal_new_trade    = pyqtSignal(dict)
    signal_close_trade  = pyqtSignal(dict)
    signal_update_trade = pyqtSignal(dict)
    signal_risk_alert   = pyqtSignal(str)
    signal_scan_update  = pyqtSignal(str)
    _internal_signal    = pyqtSignal(str, str, int)

    def __init__(self, ib, account, risk_manager, position_manager, app=None):
        super().__init__()
        self.ib               = ib
        self.account          = account
        self.risk_manager     = risk_manager
        self.position_manager = position_manager
        self.app              = app
        self.running          = False
        self._exec_engine     = None
        self.analyzer         = None
        self._last_key        = ""
        self._lock            = threading.Lock()
        self._pending_symbols = set()   # رموز قيد التنفيذ — منع التكرار
        self._internal_signal.connect(self._on_signal_received, Qt.QueuedConnection)

    def stop(self):
        self.running = False
        if self.analyzer:
            try: self.analyzer.stop()
            except: pass

    def run(self):
        self.running = True
        self.signal_scan_update.emit("🧠 AnalyzerSignalBot بدأ")
        if self.analyzer is None:
            self.signal_scan_update.emit("❌ المحلل غير مهيأ")
            return
        self.signal_scan_update.emit(
            f"🔍 يمسح {len(self.analyzer.SCAN_SYMBOLS)} رمز: "
            f"{', '.join(self.analyzer.SCAN_SYMBOLS[:8])}..."
        )
        while self.running:
            self.msleep(1000)

    @pyqtSlot(str, str, int)
    def _queue_signal(self, symbol: str, direction: str, pct: int):
        key = f"{symbol}:{direction}:{pct}"
        with self._lock:
            if key == self._last_key:
                return
            self._last_key = key
        self.signal_scan_update.emit(f"📨 إشارة: {symbol} {direction} {pct}%")
        self._internal_signal.emit(symbol, direction, pct)

    @pyqtSlot(str, str, int)
    def _on_signal_received(self, symbol: str, direction: str, pct: int):
        # ✅ منع تنفيذ نفس الرمز مرتين في نفس الوقت
        with self._lock:
            if symbol in self._pending_symbols:
                self.signal_scan_update.emit(f"⏭ {symbol}: قيد التنفيذ بالفعل — تجاهل")
                return
            self._pending_symbols.add(symbol)
        t = threading.Thread(target=self._execute, args=(symbol, direction, pct), daemon=True)
        t.start()

    def _execute(self, symbol: str, direction: str, pct: int):
        try:
            self._execute_inner(symbol, direction, pct)
        finally:
            # ✅ دائماً أطلق القفل — سواء نجح أو فشل
            with self._lock:
                self._pending_symbols.discard(symbol)

    def _execute_inner(self, symbol: str, direction: str, pct: int):
        self.signal_scan_update.emit(f"🚀 تنفيذ: {symbol} {direction} ({pct}%)")
        engine = getattr(self, '_exec_engine', None)
        if engine is None:
            self.signal_scan_update.emit("  ❌ ExecutionEngine غير مهيأ")
            return
        try:
            # ── الرصيد: من app مباشرة ──────────────────────────
            bal = 0.0
            if self.app is not None:
                bal = float(getattr(self.app, 'account_balance', 0.0) or 0.0)
            if bal <= 0:
                bal = engine.balance  # من آخر تحديث
            if bal > 0:
                engine.balance = bal

            stats = engine.get_stats()
            print(f'[EXEC] رصيد={bal:.0f} مفتوحة={stats["open_trades"]}')
            self.signal_scan_update.emit(
                f"  💰 رصيد=${bal:,.0f} | مفتوحة={stats['open_trades']} | "
                f"يومي={stats['daily_trades']}"
            )
            # استرجع SL/TP من cache
            _app_ref   = getattr(self, 'app', None)
            _sig_cache = getattr(_app_ref, '_analyzer_signal_cache', {}) if _app_ref else {}
            _cached    = _sig_cache.get(symbol, {})
            _sl_p  = float(_cached.get('sl',  0) or 0)
            _tp1_p = float(_cached.get('tp1', 0) or 0)
            _tp2_p = float(_cached.get('tp2', 0) or 0)
            _ent_p = float(_cached.get('entry_price', 0) or 0)
            trade_id = engine.execute_signal(
                symbol, direction, pct, bal,
                sl_price=_sl_p, tp1_price=_tp1_p,
                tp2_price=_tp2_p, entry_stock_price=_ent_p
            )
            print(f'[EXEC] نتيجة: {trade_id}')
        except Exception as e:
            import traceback
            self.signal_scan_update.emit(f"  ❌ {e}")
            self.signal_scan_update.emit(f"  {traceback.format_exc().splitlines()[-1]}")
            return
        if not trade_id:
            reason = getattr(engine, "last_reject_reason", "") or "رُفضت"
            self.signal_scan_update.emit(f"  ❌ {reason}")
            return
        self.signal_scan_update.emit(f"  ✅ نُفذت | ID={str(trade_id)[:8]}")
        pos = engine.open_positions.get(trade_id, {})
        if pos:
            self.signal_new_trade.emit({
                'id':             trade_id,
                'symbol':         pos.get('symbol', symbol),
                'opt_type':       pos.get('opt_type', direction),
                'strike':         pos.get('strike', 0),
                'expiry':         pos.get('expiry', ''),
                'entry_premium':  pos.get('entry_premium', 0),
                'stop_loss':      pos.get('stop_loss', 0),
                'take_profit':    pos.get('take_profit', 0),
                'take_profit_2':  pos.get('take_profit_2', 0),
                'highest':        pos.get('entry_premium', 0),
                'contracts':      pos.get('contracts', 1),
                'cost':           pos.get('cost', 0),
                'time':           datetime.now().strftime('%H:%M:%S'),
                'entry_datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'why':            f'{direction} {pct}%',
                'score':          pct,
                'strategy_type':  'ANALYZER',
                'status':         'open',
                'tp_phase':       0,
                'opt_contract':   pos.get('opt_contract'),
                'long_contract':  None,
                'is_spread':      False,
                'regime':         'normal',
                'tp_ratio':       1.8,
                'entry_stock_price': 0,
                '_entry_synced':  True,
            })




# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    import logging as _lg
    _lg.getLogger('ib_insync').setLevel(_lg.CRITICAL)

    try:
        window = TradingApp()
        window.show()
        window.raise_()
        window.activateWindow()
        sys.exit(app.exec_())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n❌ خطأ: {e}")
        input("اضغط Enter للإغلاق...")