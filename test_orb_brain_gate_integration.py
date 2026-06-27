"""
test_orb_brain_gate_integration.py — Brain Gate wiring integration tests.

Tests:
  1. ALLOW_ORB does not suppress ORB signal emission.
  2. BLOCK_ORB suppresses ORB signal emission.
  3. Brain Gate failure (exception) defaults to ALLOW (signal goes through).
  4. BC signal path does not reference Brain Gate.
"""

import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import smart_analyzer_bridge_orb as orb_mod
from smart_analyzer_bridge_orb import (
    ORBDailyBridge,
    _BRAIN_GATE_ALLOW,
    _BRAIN_GATE_BLOCK,
    _check_brain_gate,
    _bg_breadth,
    _bg_market_regime,
    _bg_orb_range_atr,
    _bg_spy_range_ratio,
)

# ── shared fixture ────────────────────────────────────────────────────────────

FAKE_TODAY = datetime.utcnow().strftime("%Y-%m-%d")

FAKE_SIGNAL = dict(
    symbol='AMZN', direction='LONG', date=FAKE_TODAY,
    entry_ts=datetime.utcnow(),
    entry_price=190.0, stop_price=185.0, tp1=200.0,
    adx=35.0, rvol=2.0, bias='BULL', atr=3.0, score=70.0,
)


def _make_bridge():
    return ORBDailyBridge(MagicMock(), log_fn=lambda _: None, enable_live_orb=False)


def _run_scan(bridge, *, gate_verdict=None, gate_reason='test', raise_in_gate=False):
    """
    Run bridge._scan() with minimal mocking.
    Injects FAKE_SIGNAL for AMZN and controls the brain gate outcome.
    Returns mock_emit so callers can assert call counts.
    """
    now_ts = time.time()

    gate_patch = (
        patch('smart_analyzer_bridge_orb._brain_gate',
              **{'evaluate.side_effect': RuntimeError('gate crashed')})
        if raise_in_gate else
        patch('smart_analyzer_bridge_orb._check_brain_gate',
              return_value=(gate_verdict, gate_reason))
    )

    with patch('smart_analyzer_bridge_orb._LIVE_SYMBOLS', ['AMZN', 'SPY', 'QQQ']), \
         patch('smart_analyzer_bridge_orb.os.path.exists', return_value=True), \
         patch('smart_analyzer_bridge_orb.os.path.getmtime', return_value=now_ts - 60), \
         patch('smart_analyzer_bridge_orb.time') as mock_time, \
         patch('smart_analyzer_bridge_orb.load_symbol_candles',
               return_value=([MagicMock()],)), \
         patch('smart_analyzer_bridge_orb._build_bias', return_value={}), \
         patch('smart_analyzer_bridge_orb.scan_orb_live',
               side_effect=lambda sym, bars, bias: [FAKE_SIGNAL] if sym == 'AMZN' else []), \
         patch.object(bridge, '_is_signal_fresh', return_value=True), \
         patch.object(bridge, '_emit_signal') as mock_emit, \
         gate_patch:
        mock_time.time.return_value = now_ts
        mock_time.sleep = lambda _: None
        # Ensure _BRAIN_GATE_AVAILABLE is True for raise_in_gate path
        if raise_in_gate:
            orb_mod._BRAIN_GATE_AVAILABLE = True
        bridge._scan()

    return mock_emit


# ── Test 1 & 2: scan-level gate/emit behaviour ───────────────────────────────

class TestOrbScanGateBehaviour(unittest.TestCase):

    def test_allow_orb_does_not_suppress_emission(self):
        """Gate ALLOW_ORB → _emit_signal is called for the candidate."""
        bridge = _make_bridge()
        mock_emit = _run_scan(bridge, gate_verdict=_BRAIN_GATE_ALLOW, gate_reason='NORMAL_ALLOW')
        mock_emit.assert_called_once_with(FAKE_SIGNAL)

    def test_block_orb_suppresses_emission(self):
        """Gate BLOCK_ORB → _emit_signal is NOT called."""
        bridge = _make_bridge()
        mock_emit = _run_scan(bridge, gate_verdict=_BRAIN_GATE_BLOCK, gate_reason='BEAR_REGIME')
        mock_emit.assert_not_called()

    def test_block_logs_suppression_reason(self):
        """Gate BLOCK_ORB → a BLOCK_ORB log line is written."""
        log_lines = []
        bridge = ORBDailyBridge(MagicMock(), log_fn=log_lines.append, enable_live_orb=False)
        _run_scan(bridge, gate_verdict=_BRAIN_GATE_BLOCK, gate_reason='ORB_TOO_TIGHT')
        block_logs = [l for l in log_lines if 'BLOCK_ORB' in l]
        self.assertTrue(block_logs, "Expected at least one BLOCK_ORB log line")
        self.assertIn('ORB_TOO_TIGHT', block_logs[0])

    def test_no_candidates_skips_gate(self):
        """Gate is NOT evaluated when there are no fresh candidates."""
        bridge = _make_bridge()
        now_ts = time.time()

        with patch('smart_analyzer_bridge_orb._LIVE_SYMBOLS', ['AMZN']), \
             patch('smart_analyzer_bridge_orb.os.path.exists', return_value=True), \
             patch('smart_analyzer_bridge_orb.os.path.getmtime', return_value=now_ts - 60), \
             patch('smart_analyzer_bridge_orb.time') as mock_time, \
             patch('smart_analyzer_bridge_orb.load_symbol_candles',
                   return_value=([MagicMock()],)), \
             patch('smart_analyzer_bridge_orb._build_bias', return_value={}), \
             patch('smart_analyzer_bridge_orb.scan_orb_live', return_value=[]), \
             patch('smart_analyzer_bridge_orb._check_brain_gate') as mock_gate, \
             patch.object(bridge, '_emit_signal'):
            mock_time.time.return_value = now_ts
            mock_time.sleep = lambda _: None
            bridge._scan()

        mock_gate.assert_not_called()


# ── Test 3: gate failure defaults to ALLOW ───────────────────────────────────

class TestGateFailureDefaultsToAllow(unittest.TestCase):

    def test_gate_exception_returns_allow(self):
        """_check_brain_gate catches evaluate() exceptions and returns ALLOW."""
        with patch.object(orb_mod, '_brain_gate') as mock_bg:
            saved = orb_mod._BRAIN_GATE_AVAILABLE
            orb_mod._BRAIN_GATE_AVAILABLE = True
            try:
                mock_bg.evaluate.side_effect = RuntimeError('gate crashed')
                v, r = _check_brain_gate({}, {}, FAKE_TODAY)
            finally:
                orb_mod._BRAIN_GATE_AVAILABLE = saved
        self.assertEqual(v, _BRAIN_GATE_ALLOW)

    def test_gate_unavailable_returns_allow(self):
        """When brain gate module not importable, _check_brain_gate returns ALLOW."""
        saved = orb_mod._BRAIN_GATE_AVAILABLE
        orb_mod._BRAIN_GATE_AVAILABLE = False
        try:
            v, r = _check_brain_gate({}, {}, FAKE_TODAY)
        finally:
            orb_mod._BRAIN_GATE_AVAILABLE = saved
        self.assertEqual(v, _BRAIN_GATE_ALLOW)

    def test_gate_crash_in_scan_still_emits(self):
        """Brain gate evaluate() crashes → _check_brain_gate safe ALLOW → signal emitted."""
        bridge = _make_bridge()
        mock_emit = _run_scan(bridge, raise_in_gate=True)
        mock_emit.assert_called_once()

    def test_check_brain_gate_never_raises(self):
        """_check_brain_gate must not raise under any condition."""
        saved = orb_mod._BRAIN_GATE_AVAILABLE
        orb_mod._BRAIN_GATE_AVAILABLE = True
        try:
            with patch.object(orb_mod, '_brain_gate') as mock_bg:
                mock_bg.evaluate.side_effect = Exception('anything')
                try:
                    _check_brain_gate({'junk': 'data'}, {}, FAKE_TODAY)
                except Exception as e:
                    self.fail(f"_check_brain_gate raised unexpectedly: {e}")
        finally:
            orb_mod._BRAIN_GATE_AVAILABLE = saved


# ── Test 4: BC path untouched ─────────────────────────────────────────────────

class TestBcPathUntouched(unittest.TestCase):

    def test_brain_gate_not_in_bridge_bc_source(self):
        """smart_analyzer_bridge_bc.py must not import or reference market_brain_gate."""
        with open('smart_analyzer_bridge_bc.py', encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('market_brain_gate', src,
                         "market_brain_gate should not be referenced in BC bridge")
        self.assertNotIn('brain_gate', src,
                         "brain_gate should not be referenced in BC bridge")

    def test_brain_gate_not_in_execution_source(self):
        """execution.py must not import or reference market_brain_gate."""
        with open('execution.py', encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('market_brain_gate', src)

    def test_orb_bridge_check_fn_exists(self):
        """_check_brain_gate is a module-level function in the ORB bridge."""
        self.assertTrue(callable(_check_brain_gate))

    def test_brain_gate_constants_defined(self):
        """ALLOW/BLOCK string constants are present and correct."""
        self.assertEqual(orb_mod._BRAIN_GATE_ALLOW, 'ALLOW_ORB')
        self.assertEqual(orb_mod._BRAIN_GATE_BLOCK, 'BLOCK_ORB')


# ── _check_brain_gate unit tests ──────────────────────────────────────────────

class TestCheckBrainGateFunction(unittest.TestCase):
    """Direct tests of the _check_brain_gate module function."""

    def _with_gate(self, verdict, reason):
        """Patch brain gate to return a fixed verdict, call _check_brain_gate."""
        saved = orb_mod._BRAIN_GATE_AVAILABLE
        orb_mod._BRAIN_GATE_AVAILABLE = True
        try:
            with patch.object(orb_mod, '_brain_gate') as mock_bg:
                mock_bg.evaluate.return_value = (verdict, reason)
                return _check_brain_gate({}, {}, FAKE_TODAY)
        finally:
            orb_mod._BRAIN_GATE_AVAILABLE = saved

    def test_allow_verdict_propagated(self):
        v, _ = self._with_gate(_BRAIN_GATE_ALLOW, 'NORMAL_ALLOW')
        self.assertEqual(v, _BRAIN_GATE_ALLOW)

    def test_block_verdict_propagated(self):
        v, r = self._with_gate(_BRAIN_GATE_BLOCK, 'BEAR_REGIME')
        self.assertEqual(v, _BRAIN_GATE_BLOCK)
        self.assertEqual(r, 'BEAR_REGIME')

    def test_reason_propagated(self):
        _, r = self._with_gate(_BRAIN_GATE_ALLOW, 'ORB_TOO_TIGHT')
        self.assertEqual(r, 'ORB_TOO_TIGHT')

    def test_all_verdicts_are_strings(self):
        v, r = self._with_gate(_BRAIN_GATE_ALLOW, 'x')
        self.assertIsInstance(v, str)
        self.assertIsInstance(r, str)


# ── bg_* helper unit tests ────────────────────────────────────────────────────

class TestBgHelpers(unittest.TestCase):
    """Sanity checks for the brain gate input derivation helpers."""

    def test_bg_market_regime_empty_map(self):
        self.assertIsNone(_bg_market_regime({}, {}))

    def test_bg_market_regime_no_spy(self):
        self.assertIsNone(_bg_market_regime({'AMZN': []}, {}))

    def test_bg_spy_range_ratio_empty(self):
        self.assertIsNone(_bg_spy_range_ratio([], FAKE_TODAY))

    def test_bg_orb_range_atr_empty(self):
        self.assertIsNone(_bg_orb_range_atr({}, FAKE_TODAY))

    def test_bg_breadth_empty(self):
        self.assertIsNone(_bg_breadth({}))

    def test_bg_breadth_excluded_symbols_ignored(self):
        """ORB_EXCLUDED symbols do not contribute to breadth."""
        bars = [MagicMock(close=200.0)] * 25
        c15_map = {'SPY': bars, 'AAPL': bars}  # both excluded/index
        self.assertIsNone(_bg_breadth(c15_map))


if __name__ == '__main__':
    unittest.main(verbosity=2)
