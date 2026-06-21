# -*- coding: utf-8 -*-
"""
smart_analyzer_bridge_bc.py  --  B+C Analyzer Bridge (Paper + Grade-A Live Gate)
==================================================================================
PAPER MODE by default.  Live execution only when ENABLE_LIVE_BC=True in
trading_app.py AND ANALYZER_MODE="BC".

Signal routing rules (enforced here AND re-enforced inside each check):
  Grade A  (Trend + High RVI)  -> live execution when ENABLE_LIVE_BC=True
  Grade B  (Trend + Med  RVI)  -> display only, always
  Grade C  (Trend + Low  RVI)  -> display only, always
  Grade D  (Range)             -> display only, always; Range regime blocked at two points

UI controls respected via the existing _on_analyzer_trade_signal pipeline:
  - contract quantity      (ExecutionEngine.cfg)
  - max open positions     (checked here + again in pipeline)
  - existing position check (checked here + again in pipeline)
  - balance check          (pipeline)
  - 300-second cooldown    (pipeline)

Do NOT modify analyzer_x2.py.
Do NOT modify execution.py.
Do NOT remove smart_analyzer_bridge_x1.py.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

# -- Production analyzer (signal detection only) ------------------------------
try:
    from analyzer_bc_core import (
        scan_symbol,
        select_daily,
        load_symbol_candles,
        SYMBOLS as _BC_SYMBOLS,
        ATR_THRESHOLD,
        TOP_N_DAILY,
        CHART_DIR as _BC_CHART_DIR,
    )
except Exception as _e:
    raise ImportError(f"smart_analyzer_bridge_bc: cannot import analyzer_bc_core: {_e}")

# -- Market-state infrastructure (read-only, for RVI/ADX) ---------------------
from analyzer_bc_core import _market_state_from, MIN_HISTORY

from analyzer_x2 import Candle   # Candle type only -- analyzer_x2 is NOT invoked


# -- Module-level constants ---------------------------------------------------

# SAFETY DEFAULT -- actual value injected from trading_app.py at runtime
# via MarketAnalyzerEngine(ib, enable_live_bc=ENABLE_LIVE_BC).
# This constant is a last-resort fallback; it never overrides the constructor arg.
ENABLE_LIVE_BC = False

SCAN_INTERVAL_SEC    = 60
SIGNAL_LOOKBACK_DAYS = 2        # show signals from last N calendar days
DATA_STALE_MIN       = 30       # warn if chart JSON older than this many minutes

# RVI thresholds -- derived from 8-month B+C+ATR backtest pool
RVI_LO = 0.640
RVI_HI = 0.942
ADX_TREND_MIN = 25.0

# ── Execution quality gate (Grade A signals only) ─────────────────────────────
# These thresholds further filter Grade A signals before live routing.
# B / C / D remain display-only regardless.
EXEC_RANK_MIN: float    = 75.0          # minimum rank_score for execution
EXEC_BLOCKED_SYMS: set  = {"AAPL"}     # symbols blocked from live execution (temporary)

CHART_DIR = _BC_CHART_DIR
SYMBOLS   = list(_BC_SYMBOLS)

SESSION_CUTOFF = 525  # session_min >= 525 => at or after 14:15 ET; block execution


# -- Execution quality gate ---------------------------------------------------

def passes_exec_gate(sig: Dict) -> tuple:
    """
    Returns (True, "") when a Grade A signal clears all live-execution quality gates.
    Returns (False, reason_str) for any failure.

    Gates (all must pass):
      1. rank_score >= EXEC_RANK_MIN (75)
      2. rvi_bucket == "High"
      3. regime == "Trend"
      4. atr_pct <= ATR_THRESHOLD (0.52)
      5. symbol not in EXEC_BLOCKED_SYMS  (currently {"AAPL"})
      6. session_min < SESSION_CUTOFF (no trades at or after 14:15 ET)
    """
    rank = sig.get("rank_score", 0.0)
    if rank < EXEC_RANK_MIN:
        return False, f"rank_score={rank:.1f} < {EXEC_RANK_MIN}"

    bkt = sig.get("rvi_bucket", "")
    if bkt != "High":
        return False, f"rvi_bucket={bkt!r} (need High)"

    regime = sig.get("regime", "")
    if regime != "Trend":
        return False, f"regime={regime!r} (need Trend)"

    atr_pct = sig.get("atr_pct", 999.0)
    if atr_pct > ATR_THRESHOLD:
        return False, f"atr_pct={atr_pct:.3f} > {ATR_THRESHOLD}"

    sym = sig.get("symbol", "")
    if sym in EXEC_BLOCKED_SYMS:
        return False, f"{sym} temporarily blocked"

    sm = sig.get("session_min", SESSION_CUTOFF)
    if sm >= SESSION_CUTOFF:
        return False, f"session_min={sm} >= {SESSION_CUTOFF} (after 14:15 ET)"

    return True, ""


# -- RVI helpers --------------------------------------------------------------

def _session_cum(c15: List[Candle]) -> Dict:
    r: Dict = defaultdict(dict)
    for c in c15:
        sm = (c.timestamp.hour - 9) * 60 + c.timestamp.minute - 30
        if sm < 240 or sm >= 525:
            continue
        off = (sm - 240) // 15
        d   = c.timestamp.date()
        r[d][off] = r[d].get(off - 1, 0.0) + c.volume
    return dict(r)


def _atr20(c15: List[Candle], idx: int) -> float:
    trs = []
    for j in range(max(1, idx - 400), idx):
        hi = c15[j].high; lo = c15[j].low; pc = c15[j - 1].close
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _compute_rvi(
    c15: List[Candle],
    bar_idx: int,
    session_min: int,
    atr: float,
    scum: Dict,
) -> tuple:
    """Return (rvi, vol_ratio, atr_ratio)."""
    a20 = _atr20(c15, bar_idx)
    ar  = atr / a20 if a20 > 1e-9 else 1.0
    if session_min >= 240:
        off = (session_min - 240) // 15
        d   = c15[bar_idx].timestamp.date()
        vn  = scum.get(d, {}).get(off, 0.0)
        pr  = sorted(x for x in scum if x < d and off in scum[x])[-20:]
        av  = sum(scum[x][off] for x in pr) / len(pr) if pr else vn
        vr  = vn / av if av > 1e-9 else 1.0
    else:
        vr = 1.0
    return round(ar * vr, 4), round(vr, 4), round(ar, 4)


def _rvi_bucket(rv: float) -> str:
    if rv <= RVI_LO:  return "Low"
    if rv <= RVI_HI:  return "Medium"
    return "High"


def _grade(regime: str, bucket: str) -> str:
    if regime == "Range":
        return "D"
    return {"High": "A", "Medium": "B", "Low": "C"}.get(bucket, "?")


def _enrich(signals: List[Dict], symbol: str, c15: List[Candle]) -> None:
    """Add rvi, rvi_bucket, adx, regime, grade to each signal in-place."""
    scum   = _session_cum(c15)
    ts_map = {c.timestamp: i for i, c in enumerate(c15)}
    for sig in signals:
        if sig.get("symbol") != symbol:
            continue
        birth_dt = sig.get("birth_ts")
        if not isinstance(birth_dt, datetime):
            sig.update(rvi=1.0, rvi_bucket="Medium", adx=0.0, regime="Unknown", grade="?")
            continue
        bar_idx = ts_map.get(birth_dt)
        if bar_idx is None or bar_idx < MIN_HISTORY:
            sig.update(rvi=1.0, rvi_bucket="Medium", adx=0.0, regime="Unknown", grade="?")
            continue
        setup  = c15[max(0, bar_idx - 220): bar_idx + 1]
        market = _market_state_from(setup, c15[bar_idx].close)
        atr    = market.atr_14
        adx    = market.adx
        sm     = sig.get("session_min", 0)
        rvi, vr, ar = _compute_rvi(c15, bar_idx, sm, atr, scum)
        bkt    = _rvi_bucket(rvi)
        regime = "Trend" if adx >= ADX_TREND_MIN else "Range"
        sig["rvi"]        = rvi
        sig["vol_ratio"]  = vr
        sig["atr_ratio"]  = ar
        sig["rvi_bucket"] = bkt
        sig["adx"]        = round(adx, 1)
        sig["regime"]     = regime
        sig["grade"]      = _grade(regime, bkt)


# -- Paper + live-gated bridge ------------------------------------------------

class BCPaperBridge:
    """
    Scans chart_data every SCAN_INTERVAL_SEC seconds.
    Logs all signals.  Routes Grade A -> execution only when _enable_live=True.
    Never touches ExecutionEngine directly -- uses _on_analyzer_trade_signal pipeline.
    """

    def __init__(
        self,
        app,
        log_fn=None,
        enable_live_bc: bool = False,
    ):
        self.app          = app
        self._log         = log_fn or print
        self._enable_live = enable_live_bc
        self._running     = False
        self._thread: Optional[threading.Thread] = None
        self._seen:   Set[str] = set()              # signal keys logged this session
        self._active_signals: Dict[str, Any] = {}   # symbol -> {id, ts}; cleaned by app
        self._cycle   = 0

    # -- Thread management ----------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name="BCPaperBridge"
        )
        self._thread.start()
        mode = "LIVE (Grade A only)" if self._enable_live else "PAPER ONLY"
        self._log(f"[BC Bridge] started -- {mode}")

    def stop(self) -> None:
        self._running = False
        self._log("[BC Bridge] stopped")

    def wait(self, ms: int = 3000) -> None:
        if self._thread:
            self._thread.join(ms / 1000.0)

    def _run(self) -> None:
        for _ in range(10):          # brief startup delay for tv_datafeed
            if not self._running:
                return
            time.sleep(1)
        while self._running:
            try:
                self._scan()
            except Exception as exc:
                self._log(f"[BC Bridge] scan error: {exc}")
            for _ in range(SCAN_INTERVAL_SEC):
                if not self._running:
                    return
                time.sleep(1)

    # -- Scan cycle -----------------------------------------------------------

    def _scan(self) -> None:
        self._cycle += 1
        all_sigs: List[Dict] = []
        cutoff = (datetime.now() - timedelta(days=SIGNAL_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

        for sym in SYMBOLS:
            if not self._running:
                return

            json_path = os.path.join(CHART_DIR, f"{sym}_15m.json")
            if not os.path.exists(json_path):
                self._log(f"[BC] {sym}: no chart_data JSON -- skip")
                continue
            age_min = (time.time() - os.path.getmtime(json_path)) / 60.0
            if age_min > DATA_STALE_MIN:
                self._log(f"[BC] {sym}: chart data {age_min:.0f}min stale (tv_datafeed running?)")
                continue

            try:
                data = load_symbol_candles(sym, CHART_DIR)
            except Exception as exc:
                self._log(f"[BC] {sym}: load error: {exc}")
                continue
            if data is None:
                continue
            c15, c1h = data

            try:
                sigs = scan_symbol(sym, c15, c1h)
            except Exception as exc:
                self._log(f"[BC] {sym}: scan error: {exc}")
                continue

            _enrich(sigs, sym, c15)
            all_sigs.extend(sigs)

        selected = select_daily(all_sigs, TOP_N_DAILY)
        recent   = [s for s in selected if s.get("date", "") >= cutoff]

        for sig in recent:
            key = (f"{sig['symbol']}|{sig['direction']}|"
                   f"{sig['date']}|{sig.get('birth_time', '')}")
            if key not in self._seen:
                self._seen.add(key)
                self._log_signal(sig)

        if self._cycle == 1 or self._cycle % 10 == 0:
            self._log_summary(recent)

    # -- Signal logging -------------------------------------------------------

    def _log_signal(self, sig: Dict) -> None:
        grade  = sig.get("grade", "?")
        regime = sig.get("regime", "?")

        # Gate: Grade A must also clear the execution quality gate
        if self._enable_live and grade == "A":
            _gate_ok, _gate_reason = passes_exec_gate(sig)
            is_live = _gate_ok
        else:
            is_live      = False
            _gate_ok     = False
            _gate_reason = ""

        if is_live:
            tag = "-> LIVE EXECUTION"
        elif self._enable_live and grade == "A" and not _gate_ok:
            tag = f"DISPLAY ONLY [gate: {_gate_reason}]"
        else:
            tag = "PAPER ONLY"

        self._log(
            f"[PAPER / BC EXPERIMENTAL]  "
            f"{sig['symbol']} {sig['direction']} "
            f"@ ${sig.get('entry_price', 0):.2f}  "
            f"Stop=${sig.get('stop_price', 0):.2f}  "
            f"TP1=${sig.get('tp1', 0):.2f}  |  "
            f"ATR%={sig.get('atr_pct', 0):.3f}  "
            f"RVI={sig.get('rvi', 0):.3f} ({sig.get('rvi_bucket', '?')})  "
            f"Regime={regime}  "
            f"Grade={grade}  "
            f"Score={sig.get('rank_score', 0):.1f}  |  "
            f"Date={sig.get('date', '')} {sig.get('birth_time', '')}  "
            f"|  {tag}"
        )

        if is_live:
            self._route_to_execution(sig)

    def _log_summary(self, recent: List[Dict]) -> None:
        if not recent:
            self._log(
                f"[BC] Cycle #{self._cycle}: 0 signals "
                f"(ATR<={ATR_THRESHOLD} + Top-{TOP_N_DAILY}/day + last {SIGNAL_LOOKBACK_DAYS}d)"
            )
            return
        latest     = max(s["date"] for s in recent)
        today_sigs = [s for s in recent if s["date"] == latest]
        by_grade: Dict[str, int] = defaultdict(int)
        for s in today_sigs:
            by_grade[s.get("grade", "?")] += 1
        gstr = "  ".join(f"Grade-{g}={n}" for g, n in sorted(by_grade.items()))
        mode = "LIVE-GATED" if self._enable_live else "PAPER ONLY"
        self._log(
            f"[BC] Cycle #{self._cycle}  {latest}: "
            f"{len(today_sigs)} selected (Top-{TOP_N_DAILY}/day)  "
            f"{gstr}  |  {mode}"
        )

    # -- Execution routing (Grade A only) -------------------------------------

    def _route_to_execution(self, sig: Dict) -> None:
        """
        Route a Grade A Trend+HighRVI signal to the live execution pipeline.
        All guards must pass:
          1. ENABLE_LIVE_BC=True        (checked by caller; re-checked here)
          2. Grade A                    (checked by caller; re-checked here)
          3. Trend regime               (checked by caller; re-checked here)
          4. passes_exec_gate()         (rank_score/RVI/ATR/symbol/time)
          5. Symbol not in _active_signals
          6. Symbol has no open position
          7. Max open positions not reached
          8. _on_analyzer_trade_signal exists on app
        """
        symbol         = sig["symbol"]
        direction_exec = "CALL" if sig["direction"] == "LONG" else "PUT"
        grade          = sig.get("grade", "?")
        regime         = sig.get("regime", "")

        # Hard safety re-checks (defense in depth)
        if not self._enable_live:
            self._log(f"[BC] {symbol}: ENABLE_LIVE_BC=False -- aborted")
            return
        if grade != "A":
            self._log(f"[BC] {symbol}: Grade={grade} -- execution requires Grade A")
            return
        if regime == "Range":
            self._log(f"[BC] {symbol}: Range regime -- blocked from execution")
            return

        # Quality gate re-check (also checked in _log_signal -- defense in depth)
        _gate_ok, _gate_reason = passes_exec_gate(sig)
        if not _gate_ok:
            self._log(f"[BC] {symbol}: quality gate blocked -- {_gate_reason}")
            return

        # Wall-clock time guard: catch cases where scan runs after 14:15 ET
        _now    = datetime.utcnow()
        _now_sm = (_now.hour - 9) * 60 + _now.minute - 30
        if _now_sm >= SESSION_CUTOFF:
            self._log(f"[BC] {symbol}: after 14:15 ET (wall_sm={_now_sm}) -- no execution")
            return

        # Active signal guard
        if symbol in self._active_signals:
            self._log(f"[BC] {symbol}: execution already pending -- skip")
            return

        # Open position / max positions checks (respect UI controls)
        eng = getattr(self.app, "_exec_engine", None)
        if eng:
            engine_open = getattr(eng, "open_positions", {})
            open_syms   = {v.get("symbol", "") for v in engine_open.values()
                           if isinstance(v, dict)}
            if symbol in open_syms:
                self._log(f"[BC] {symbol}: position already open -- skip")
                return
            cfg     = getattr(eng, "cfg", None)
            max_pos = int(getattr(cfg, "max_open_trades", 3))
            if len(engine_open) >= max_pos:
                self._log(f"[BC] {symbol}: max positions ({max_pos}) reached -- skip")
                return

        # Populate signal cache (same keys as X1 bridge, read by _on_analyzer_trade_signal)
        if not hasattr(self.app, "_analyzer_signal_cache"):
            self.app._analyzer_signal_cache = {}
        self.app._analyzer_signal_cache[symbol] = {
            "symbol":           symbol,
            "sl":               sig.get("stop_price", 0),
            "tp1":              sig.get("tp1", 0),
            "tp2":              None,
            "entry_price":      sig.get("entry_price", 0),
            "underlying_price": sig.get("entry_price", 0),
            "grade":            "A",
            "source":           "BCPaperBridge_GradeA",
            "rvi":              sig.get("rvi", 0),
            "rvi_bucket":       sig.get("rvi_bucket", ""),
            "regime":           sig.get("regime", ""),
            "atr_pct":          sig.get("atr_pct", 0),
        }

        # Mark active; _on_analyzer_trade_signal will pop this on failure
        # (because trading_app sets _smart_bridge = _analyzer._bridge in BC mode)
        sig_id = f"{symbol}-{int(time.time())}"
        self._active_signals[symbol] = {"id": sig_id, "ts": time.time()}

        confidence = min(99, max(60, int(sig.get("rank_score", 70))))

        self._log(
            f"[BC LIVE / GRADE A]  {symbol} {direction_exec}  "
            f"Entry=${sig.get('entry_price', 0):.2f}  "
            f"SL=${sig.get('stop_price', 0):.2f}  "
            f"TP1=${sig.get('tp1', 0):.2f}  "
            f"RVI={sig.get('rvi', 0):.3f}  "
            f"Score={sig.get('rank_score', 0):.1f}  "
            f"confidence={confidence}%  ->  execution"
        )

        # Dispatch to execution pipeline
        if not hasattr(self.app, "_on_analyzer_trade_signal"):
            self._log(f"[BC] {symbol}: _on_analyzer_trade_signal not found -- abort")
            self._active_signals.pop(symbol, None)
            return

        try:
            self.app._on_analyzer_trade_signal(symbol, direction_exec, confidence)
        except Exception as exc:
            self._log(f"[BC] {symbol}: dispatch error: {exc}")
            self._active_signals.pop(symbol, None)
            return

        # 10-second fallback: release lock if execution pipeline drops signal silently
        t = threading.Timer(10.0, self._verify_signal_result, args=(symbol, sig_id))
        t.daemon = True
        t.start()

    def _verify_signal_result(self, symbol: str, sig_id: str) -> None:
        """Release _active_signals lock if no open position appeared after 10 s."""
        try:
            current = self._active_signals.get(symbol)
            if current is None:
                return
            stored_id = current.get("id") if isinstance(current, dict) else None
            if stored_id != sig_id:
                return  # a newer signal has claimed this slot
            eng = getattr(self.app, "_exec_engine", None)
            if eng and hasattr(eng, "open_positions"):
                has_trade = any(
                    p.get("symbol") == symbol
                    for p in eng.open_positions.values()
                    if isinstance(p, dict)
                )
                if has_trade:
                    return  # trade confirmed -- leave active
            self._active_signals.pop(symbol, None)
            self._log(f"[BC] {symbol}: no trade confirmed after 10s -- lock released")
        except Exception as exc:
            self._active_signals.pop(symbol, None)
            self._log(f"[BC] {symbol}: verify error: {exc}")


# -- Public interface (mirrors smart_analyzer_bridge_x1.MarketAnalyzerEngine) -

class MarketAnalyzerEngine:
    """
    Drop-in replacement for the X1 MarketAnalyzerEngine interface.
    Used by trading_app.py when ANALYZER_MODE = "BC".

    Pass enable_live_bc=True (from trading_app.ENABLE_LIVE_BC) to allow
    Grade A signals to reach the execution pipeline.  Default is False.
    """

    class _Sig:
        def __init__(self):
            self._cbs: list = []

        def connect(self, fn) -> None:
            self._cbs.append(fn)

        def emit(self, *args) -> None:
            for cb in list(self._cbs):
                try:
                    cb(*args)
                except Exception:
                    pass

    def __init__(self, ib=None, parent=None, enable_live_bc: bool = False):
        self._ib             = ib
        self._app            = None
        self._enable_live_bc = enable_live_bc
        self._bridge: Optional[BCPaperBridge] = None
        self.log_msg         = self._Sig()
        self.trade_signal    = self._Sig()    # never emitted -- routing is direct
        self.profile_updated = self._Sig()    # never emitted

    def set_app(self, app, exec_engine=None) -> None:
        self._app = app
        # exec_engine is accepted for interface compatibility only.
        # BCPaperBridge uses _on_analyzer_trade_signal (not exec_engine directly).
        self._bridge = BCPaperBridge(
            app=app,
            log_fn=self.log_msg.emit,
            enable_live_bc=self._enable_live_bc,
        )

    def start(self) -> None:
        self.log_msg.emit("[BC Bridge] ============================================")
        mode = "LIVE-GATED (Grade A + exec quality gate)" if self._enable_live_bc else "PAPER ONLY"
        self.log_msg.emit(f"[BC Bridge]  MODE: {mode}")
        self.log_msg.emit(f"[BC Bridge]  Engine: B+C + ATR<={ATR_THRESHOLD} + Top-{TOP_N_DAILY}/day")
        self.log_msg.emit(f"[BC Bridge]  Symbols: {len(SYMBOLS)}  |  Scan: {SCAN_INTERVAL_SEC}s")
        self.log_msg.emit("[BC Bridge]  Grade A=Trend+HighRVI  B/C/D=display only")
        if self._enable_live_bc:
            blocked = ", ".join(sorted(EXEC_BLOCKED_SYMS)) or "none"
            self.log_msg.emit(
                f"[BC Bridge]  Exec gate: score>={EXEC_RANK_MIN}  RVI=High  Trend  "
                f"ATR<={ATR_THRESHOLD}  blocked={blocked}  before 14:15 ET"
            )
        else:
            self.log_msg.emit("[BC Bridge]  Set ENABLE_LIVE_BC=True to enable Grade A execution")
        self.log_msg.emit("[BC Bridge] ============================================")
        if self._bridge:
            self._bridge.start()
        else:
            self.log_msg.emit("[BC Bridge] ERROR: bridge not initialised (call set_app first)")

    def stop(self) -> None:
        if self._bridge:
            self._bridge.stop()

    def quit(self) -> None:
        self.stop()

    def wait(self, ms: int = 3000) -> None:
        if self._bridge:
            self._bridge.wait(ms)

    def register_trade(self, *args, **kwargs) -> None:
        pass

    def remove_trade(self, *args, **kwargs) -> None:
        pass

    def get_cooldown_status(self, symbol: str) -> dict:
        return {"active": False}


# -- Aliases ------------------------------------------------------------------
BCBridge               = BCPaperBridge
BCMarketAnalyzerEngine = MarketAnalyzerEngine


if __name__ == "__main__":
    print("=== smart_analyzer_bridge_bc self-test ===")
    print(f"SYMBOLS ({len(SYMBOLS)}): {SYMBOLS}")
    print(f"ATR_THRESHOLD : {ATR_THRESHOLD}")
    print(f"TOP_N_DAILY   : {TOP_N_DAILY}")
    print(f"CHART_DIR     : {CHART_DIR}")
    print(f"SCAN_INTERVAL : {SCAN_INTERVAL_SEC}s")
    print(f"RVI thresholds: Low<{RVI_LO}  Med {RVI_LO}-{RVI_HI}  High>{RVI_HI}")
    print(f"ENABLE_LIVE_BC (module default): {ENABLE_LIVE_BC}")

    class _FakeApp:
        class _FakeEngine:
            open_positions = {}
            class cfg:
                max_open_trades = 3
        _exec_engine = _FakeEngine()
        _analyzer_signal_cache: dict = {}
        def _on_analyzer_trade_signal(self, sym, d, pct):
            print(f"  [dispatch] {sym} {d} {pct}%")

    # Test paper mode
    eng = MarketAnalyzerEngine(enable_live_bc=False)
    eng.set_app(_FakeApp())
    assert eng._bridge is not None
    assert eng._bridge._enable_live is False
    assert isinstance(eng._bridge._active_signals, dict)

    dummy_sig = {
        "symbol": "META", "direction": "LONG", "grade": "A",
        "regime": "Trend", "rvi": 1.2, "rvi_bucket": "High",
        "entry_price": 590.0, "stop_price": 580.0, "tp1": 608.2,
        "atr_pct": 0.41, "rank_score": 78.0, "date": "2026-06-16",
        "birth_time": "14:00",
    }
    eng._bridge._route_to_execution(dummy_sig)
    assert "META" not in eng._bridge._active_signals, "Grade A should be blocked in paper mode"
    print("  Grade A blocked when ENABLE_LIVE_BC=False: OK")

    # Test live mode propagation
    eng2 = MarketAnalyzerEngine(enable_live_bc=True)
    eng2.set_app(_FakeApp())
    assert eng2._bridge._enable_live is True
    print("  ENABLE_LIVE_BC=True propagated correctly: OK")

    # Test Range regime block
    range_sig = dict(dummy_sig, regime="Range")
    eng2._bridge._route_to_execution(range_sig)
    assert "META" not in eng2._bridge._active_signals, "Range should be blocked even in live mode"
    print("  Range regime blocked in live mode: OK")

    print("Self-test passed.")
