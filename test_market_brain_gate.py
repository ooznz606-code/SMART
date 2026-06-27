"""
Unit tests for market_brain_gate.py — Phase 12A Production Brain Gate.

Rules under test (first match wins):
  1. Any input None          → ALLOW / DATA_MISSING_SAFE_ALLOW
  2. regime == BEAR          → BLOCK / BEAR_REGIME
  3. orb_range_avg_atr < 2.5 → BLOCK / ORB_TOO_TIGHT
  4. rr >= 1.20 AND brd > 60 → BLOCK / EXPANSION_OVEREXTENDED
  5. Otherwise               → ALLOW / NORMAL_ALLOW
"""
import csv
import os
import tempfile
import unittest

import market_brain_gate as bg

# ── helpers ───────────────────────────────────────────────────────────────────

class _LogRedirect:
    """Context manager: redirect gate log to a temp file for test isolation."""
    def __enter__(self):
        self._orig = bg.LOG_PATH
        self._tmp = tempfile.mkdtemp()
        bg.LOG_PATH = os.path.join(self._tmp, 'gate_test.csv')
        return bg.LOG_PATH

    def __exit__(self, *_):
        bg.LOG_PATH = self._orig


def _rows(log_path):
    with open(log_path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


# Convenience: full valid inputs that produce NORMAL_ALLOW
_GOOD = dict(market_regime='BULL', spy_range_ratio=1.05,
             orb_range_avg_atr=3.0, breadth_pct=55.0)


# ── Rule 1: any input missing → ALLOW / DATA_MISSING_SAFE_ALLOW ──────────────

class TestRule1MissingData(unittest.TestCase):

    def _check_missing(self, **overrides):
        """Assert that evaluate() returns ALLOW / DATA_MISSING_SAFE_ALLOW."""
        kw = dict(_GOOD)
        kw.update(overrides)
        with _LogRedirect():
            v, r = bg.evaluate(**kw)
        self.assertEqual(v, bg.VERDICT_ALLOW)
        self.assertEqual(r, bg.REASON_MISSING)

    def test_all_none(self):
        self._check_missing(market_regime=None, spy_range_ratio=None,
                            orb_range_avg_atr=None, breadth_pct=None)

    def test_regime_none(self):
        self._check_missing(market_regime=None)

    def test_spy_range_ratio_none(self):
        self._check_missing(spy_range_ratio=None)

    def test_orb_range_avg_atr_none(self):
        self._check_missing(orb_range_avg_atr=None)

    def test_breadth_pct_none(self):
        self._check_missing(breadth_pct=None)

    def test_bear_regime_plus_missing_other_allows(self):
        """BEAR regime but spy_range_ratio missing → Rule 1 fires before Rule 2 → ALLOW."""
        self._check_missing(market_regime='BEAR', spy_range_ratio=None)

    def test_tight_orb_plus_missing_regime_allows(self):
        """orb_range_avg_atr < 2.5 but regime missing → Rule 1 fires before Rule 3 → ALLOW."""
        self._check_missing(market_regime=None, orb_range_avg_atr=1.0)

    def test_overextended_plus_missing_breadth_allows(self):
        """rr >= 1.20 but breadth_pct missing → Rule 1 fires before Rule 4 → ALLOW."""
        self._check_missing(spy_range_ratio=1.50, breadth_pct=None)


# ── Rule 2: market_regime == BEAR → BLOCK / BEAR_REGIME ─────────────────────

class TestRule2BearRegime(unittest.TestCase):

    def _check_bear(self, **overrides):
        kw = dict(_GOOD)
        kw.update(overrides)
        with _LogRedirect():
            v, r = bg.evaluate(**kw)
        self.assertEqual(v, bg.VERDICT_BLOCK)
        self.assertEqual(r, bg.REASON_BEAR)

    def test_bear_with_all_good_other_inputs(self):
        self._check_bear(market_regime='BEAR')

    def test_bear_overrides_tight_orb(self):
        """BEAR fires before ORB_TOO_TIGHT (Rule 2 before Rule 3)."""
        self._check_bear(market_regime='BEAR', orb_range_avg_atr=1.0)

    def test_bear_overrides_overextended(self):
        """BEAR fires before EXPANSION_OVEREXTENDED (Rule 2 before Rule 4)."""
        self._check_bear(market_regime='BEAR', spy_range_ratio=1.50, breadth_pct=80.0)

    def test_bear_all_worst_case(self):
        self._check_bear(market_regime='BEAR', spy_range_ratio=0.50,
                         orb_range_avg_atr=0.5, breadth_pct=5.0)

    def test_bull_does_not_block(self):
        with _LogRedirect():
            v, _ = bg.evaluate(**{**_GOOD, 'market_regime': 'BULL'})
        self.assertEqual(v, bg.VERDICT_ALLOW)

    def test_neutral_does_not_block(self):
        with _LogRedirect():
            v, _ = bg.evaluate(**{**_GOOD, 'market_regime': 'NEUTRAL'})
        self.assertEqual(v, bg.VERDICT_ALLOW)


# ── Rule 3: orb_range_avg_atr < 2.5 → BLOCK / ORB_TOO_TIGHT ────────────────

class TestRule3OrbTooTight(unittest.TestCase):

    def _check_orb_block(self, orb):
        kw = {**_GOOD, 'orb_range_avg_atr': orb}
        with _LogRedirect():
            v, r = bg.evaluate(**kw)
        self.assertEqual(v, bg.VERDICT_BLOCK)
        self.assertEqual(r, bg.REASON_ORB_TIGHT)

    def test_orb_zero_blocks(self):
        self._check_orb_block(0.0)

    def test_orb_below_threshold_blocks(self):
        self._check_orb_block(2.49)

    def test_orb_well_below_threshold_blocks(self):
        self._check_orb_block(1.0)

    def test_orb_at_threshold_allows(self):
        """2.5 is NOT < 2.5 → should not trigger Rule 3."""
        kw = {**_GOOD, 'orb_range_avg_atr': 2.5}
        with _LogRedirect():
            v, r = bg.evaluate(**kw)
        self.assertEqual(v, bg.VERDICT_ALLOW)
        self.assertEqual(r, bg.REASON_NORMAL)

    def test_orb_above_threshold_allows(self):
        kw = {**_GOOD, 'orb_range_avg_atr': 2.51}
        with _LogRedirect():
            v, _ = bg.evaluate(**kw)
        self.assertEqual(v, bg.VERDICT_ALLOW)

    def test_orb_strong_allows(self):
        kw = {**_GOOD, 'orb_range_avg_atr': 5.0}
        with _LogRedirect():
            v, _ = bg.evaluate(**kw)
        self.assertEqual(v, bg.VERDICT_ALLOW)

    def test_rule3_does_not_fire_before_rule2(self):
        """BEAR + tight ORB → Rule 2 fires (BEAR_REGIME), not Rule 3."""
        kw = {**_GOOD, 'market_regime': 'BEAR', 'orb_range_avg_atr': 1.0}
        with _LogRedirect():
            v, r = bg.evaluate(**kw)
        self.assertEqual(r, bg.REASON_BEAR)


# ── Rule 4: rr >= 1.20 AND breadth > 60 → BLOCK / EXPANSION_OVEREXTENDED ────

class TestRule4ExpansionOverextended(unittest.TestCase):

    def _check_overextended(self, rr, brd):
        kw = {**_GOOD, 'spy_range_ratio': rr, 'breadth_pct': brd}
        with _LogRedirect():
            v, r = bg.evaluate(**kw)
        self.assertEqual(v, bg.VERDICT_BLOCK)
        self.assertEqual(r, bg.REASON_OVEREXTENDED)

    def _check_not_overextended(self, rr, brd):
        kw = {**_GOOD, 'spy_range_ratio': rr, 'breadth_pct': brd}
        with _LogRedirect():
            v, _ = bg.evaluate(**kw)
        self.assertEqual(v, bg.VERDICT_ALLOW)

    def test_both_conditions_met_blocks(self):
        self._check_overextended(1.20, 60.1)

    def test_rr_above_threshold_and_breadth_well_above_blocks(self):
        self._check_overextended(1.50, 80.0)

    def test_rr_exactly_at_threshold_and_breadth_above_blocks(self):
        """rr == 1.20 satisfies >= 1.20."""
        self._check_overextended(1.20, 61.0)

    def test_rr_just_above_threshold_blocks(self):
        self._check_overextended(1.201, 65.0)

    def test_rr_below_threshold_does_not_block(self):
        """rr < 1.20 → Rule 4 not triggered even with high breadth."""
        self._check_not_overextended(1.19, 80.0)

    def test_breadth_exactly_at_60_does_not_block(self):
        """breadth == 60 is NOT > 60 → Rule 4 not triggered."""
        self._check_not_overextended(1.25, 60.0)

    def test_breadth_below_60_does_not_block(self):
        self._check_not_overextended(1.25, 59.9)

    def test_neither_condition_met_allows(self):
        self._check_not_overextended(1.05, 55.0)

    def test_rule4_does_not_fire_before_rule3(self):
        """Tight ORB + overextended → Rule 3 fires (ORB_TOO_TIGHT), not Rule 4."""
        kw = {**_GOOD, 'orb_range_avg_atr': 1.0,
              'spy_range_ratio': 1.30, 'breadth_pct': 70.0}
        with _LogRedirect():
            v, r = bg.evaluate(**kw)
        self.assertEqual(r, bg.REASON_ORB_TIGHT)


# ── Rule 5: normal allow ──────────────────────────────────────────────────────

class TestRule5NormalAllow(unittest.TestCase):

    def test_healthy_bull_day_allows(self):
        with _LogRedirect():
            v, r = bg.evaluate('BULL', 1.05, 3.5, 55.0)
        self.assertEqual(v, bg.VERDICT_ALLOW)
        self.assertEqual(r, bg.REASON_NORMAL)

    def test_neutral_typical_allows(self):
        with _LogRedirect():
            v, r = bg.evaluate('NEUTRAL', 0.95, 4.0, 48.0)
        self.assertEqual(v, bg.VERDICT_ALLOW)
        self.assertEqual(r, bg.REASON_NORMAL)

    def test_high_breadth_but_rr_below_threshold_allows(self):
        """breadth > 60 but rr < 1.20 → Rule 4 not met → ALLOW."""
        with _LogRedirect():
            v, r = bg.evaluate('BULL', 1.10, 3.0, 70.0)
        self.assertEqual(v, bg.VERDICT_ALLOW)
        self.assertEqual(r, bg.REASON_NORMAL)

    def test_high_rr_but_breadth_not_above_60_allows(self):
        """rr >= 1.20 but breadth == 60 → Rule 4 not met → ALLOW."""
        with _LogRedirect():
            v, r = bg.evaluate('BULL', 1.30, 3.0, 60.0)
        self.assertEqual(v, bg.VERDICT_ALLOW)
        self.assertEqual(r, bg.REASON_NORMAL)

    def test_orb_exactly_at_threshold_allows(self):
        with _LogRedirect():
            v, r = bg.evaluate('BULL', 1.05, 2.5, 50.0)
        self.assertEqual(v, bg.VERDICT_ALLOW)
        self.assertEqual(r, bg.REASON_NORMAL)


# ── Logging ───────────────────────────────────────────────────────────────────

class TestLogging(unittest.TestCase):

    def test_each_call_writes_one_row(self):
        with _LogRedirect() as log_path:
            bg.evaluate('BULL',    1.05, 3.5, 55.0, date='2026-06-26')
            bg.evaluate('BEAR',    1.05, 3.5, 55.0, date='2026-06-26')
            bg.evaluate('NEUTRAL', 1.05, 1.0, 55.0, date='2026-06-26')
            rows = _rows(log_path)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['verdict'], bg.VERDICT_ALLOW)
        self.assertEqual(rows[1]['verdict'], bg.VERDICT_BLOCK)
        self.assertEqual(rows[2]['verdict'], bg.VERDICT_BLOCK)

    def test_log_has_all_required_columns(self):
        with _LogRedirect() as log_path:
            bg.evaluate('BULL', 1.05, 3.5, 55.0, date='2026-06-26')
            with open(log_path, newline='', encoding='utf-8') as f:
                cols = csv.DictReader(f).fieldnames or []
        for col in bg.LOG_HEADER:
            self.assertIn(col, cols, f'Missing column: {col}')

    def test_reason_written_to_log(self):
        with _LogRedirect() as log_path:
            bg.evaluate('BEAR', 1.05, 3.5, 55.0, date='2026-06-26')
            rows = _rows(log_path)
        self.assertEqual(rows[0]['reason'], bg.REASON_BEAR)

    def test_missing_inputs_logged_as_empty_strings(self):
        with _LogRedirect() as log_path:
            bg.evaluate(None, None, None, None, date='2026-06-26')
            rows = _rows(log_path)
        self.assertEqual(rows[0]['market_regime'], '')
        self.assertEqual(rows[0]['spy_range_ratio'], '')
        self.assertEqual(rows[0]['orb_range_avg_atr'], '')
        self.assertEqual(rows[0]['breadth_pct'], '')

    def test_date_field_written(self):
        with _LogRedirect() as log_path:
            bg.evaluate('BULL', 1.05, 3.5, 55.0, date='2026-06-26')
            rows = _rows(log_path)
        self.assertEqual(rows[0]['date'], '2026-06-26')

    def test_no_date_arg_writes_today(self):
        with _LogRedirect() as log_path:
            bg.evaluate('BULL', 1.05, 3.5, 55.0)
            rows = _rows(log_path)
        self.assertRegex(rows[0]['date'], r'^\d{4}-\d{2}-\d{2}$')

    def test_all_four_reason_constants_log_correctly(self):
        with _LogRedirect() as log_path:
            bg.evaluate(None,      1.05, 3.5, 55.0)   # MISSING
            bg.evaluate('BEAR',    1.05, 3.5, 55.0)   # BEAR_REGIME
            bg.evaluate('BULL',    1.05, 1.0, 55.0)   # ORB_TOO_TIGHT
            bg.evaluate('BULL',    1.25, 3.5, 65.0)   # EXPANSION_OVEREXTENDED
            bg.evaluate('BULL',    1.05, 3.5, 55.0)   # NORMAL_ALLOW
            rows = _rows(log_path)
        self.assertEqual(rows[0]['reason'], bg.REASON_MISSING)
        self.assertEqual(rows[1]['reason'], bg.REASON_BEAR)
        self.assertEqual(rows[2]['reason'], bg.REASON_ORB_TIGHT)
        self.assertEqual(rows[3]['reason'], bg.REASON_OVEREXTENDED)
        self.assertEqual(rows[4]['reason'], bg.REASON_NORMAL)


# ── Return types ──────────────────────────────────────────────────────────────

class TestReturnTypes(unittest.TestCase):

    def test_returns_two_string_tuple(self):
        with _LogRedirect():
            result = bg.evaluate('BULL', 1.05, 3.5, 55.0)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], str)
        self.assertIsInstance(result[1], str)

    def test_verdict_is_one_of_two_constants(self):
        with _LogRedirect():
            v, _ = bg.evaluate('BULL', 1.05, 3.5, 55.0)
        self.assertIn(v, (bg.VERDICT_ALLOW, bg.VERDICT_BLOCK))

    def test_verdict_string_values(self):
        self.assertEqual(bg.VERDICT_ALLOW, 'ALLOW_ORB')
        self.assertEqual(bg.VERDICT_BLOCK, 'BLOCK_ORB')

    def test_reason_string_values(self):
        self.assertEqual(bg.REASON_MISSING,      'DATA_MISSING_SAFE_ALLOW')
        self.assertEqual(bg.REASON_BEAR,         'BEAR_REGIME')
        self.assertEqual(bg.REASON_ORB_TIGHT,    'ORB_TOO_TIGHT')
        self.assertEqual(bg.REASON_OVEREXTENDED, 'EXPANSION_OVEREXTENDED')
        self.assertEqual(bg.REASON_NORMAL,       'NORMAL_ALLOW')


if __name__ == '__main__':
    unittest.main(verbosity=2)
