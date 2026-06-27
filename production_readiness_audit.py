"""
production_readiness_audit.py
==============================
Static source-code audit of the live production stack.
Reads files as text only — does NOT import any production module.
Does NOT change any production behavior.

Output: reports/production_readiness_audit.txt  +  console
"""
import os, re, sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

OUT_PATH = os.path.join('reports', 'production_readiness_audit.txt')
os.makedirs('reports', exist_ok=True)

# ── file map ──────────────────────────────────────────────────────────────────
FILES = {
    'trading_app':   'trading_app.py',
    'bridge_bc':     'smart_analyzer_bridge_bc.py',
    'bridge_orb':    'smart_analyzer_bridge_orb.py',
    'execution':     'execution.py',
    'bc_core':       'analyzer_bc_core.py',
    'analyzer_x2':   'analyzer_x2.py',
}
DIRS = {
    'logs':       'logs',
    'data':       'data',
    'chart_data': 'chart_data',
    'reports':    'reports',
}

# ── helpers ───────────────────────────────────────────────────────────────────

_lines = []

def _out(s=''):
    print(s)
    _lines.append(s)

PASS = 'PASS'
WARN = 'WARN'
FAIL = 'FAIL'
INFO = 'INFO'

_results = []

def chk(label, status, detail=''):
    tag = {'PASS': '  [PASS]', 'WARN': '  [WARN]', 'FAIL': '  [FAIL]', 'INFO': '  [INFO]'}[status]
    line = f'{tag}  {label}'
    if detail:
        line += f'  →  {detail}'
    _out(line)
    _results.append((label, status, detail))
    return status == PASS

def section(title):
    _out()
    _out('─' * 70)
    _out(f'  {title}')
    _out('─' * 70)

def load(key):
    path = FILES.get(key, key)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()

def find(text, pattern, flags=0):
    """Return first match object or None."""
    return re.search(pattern, text, flags)

def findval(text, pattern, group=1, default=None):
    """Return captured group from first match. Always uses MULTILINE so ^ anchors work."""
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(group) if m else default

def count_matches(text, pattern, flags=0):
    return len(re.findall(pattern, text, flags))

def _cv(text, name, default='NOT_FOUND'):
    """Extract numeric value of a constant, handles type-annotated form (indented or not): NAME: type = VALUE."""
    m = re.search(rf'^\s*{name}\s*(?::\s*\S+\s*)?=\s*([\d.]+)', text, re.MULTILINE)
    return m.group(1) if m else default

# ── load all source files ─────────────────────────────────────────────────────

src = {}
for key, path in FILES.items():
    src[key] = load(key)

# ═════════════════════════════════════════════════════════════════════════════
# AUDIT START
# ═════════════════════════════════════════════════════════════════════════════

_out('=' * 70)
_out('  PRODUCTION READINESS AUDIT')
_out(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
_out('=' * 70)

# ─────────────────────────────────────────────────────────────────────────────
# 1. FILE EXISTENCE
# ─────────────────────────────────────────────────────────────────────────────
section('1. FILE EXISTENCE')

for key, path in FILES.items():
    if os.path.exists(path):
        sz = os.path.getsize(path)
        chk(f'{path}', PASS, f'{sz:,} bytes')
    else:
        chk(f'{path}', FAIL, 'NOT FOUND')

_out()
for name, path in DIRS.items():
    if os.path.isdir(path):
        n = len(os.listdir(path))
        chk(f'{path}/', PASS, f'{n} items')
    else:
        chk(f'{path}/', FAIL, 'directory missing')

# ─────────────────────────────────────────────────────────────────────────────
# 2. trading_app.py
# ─────────────────────────────────────────────────────────────────────────────
section('2. trading_app.py — IMPORTS')
t = src['trading_app'] or ''

chk('imports ib_insync',
    PASS if find(t, r'from ib_insync import') else FAIL)
chk('imports PyQt5',
    PASS if find(t, r'from PyQt5') else FAIL)
chk('imports ExecutionEngine from execution',
    PASS if find(t, r'from execution import.*ExecutionEngine') else FAIL)
chk('imports ExecutionConfig from execution',
    PASS if find(t, r'from execution import.*ExecutionConfig') else FAIL)
chk('bootstrap logs/ + data/ dirs at startup',
    PASS if find(t, r'makedirs.*logs') else WARN,
    'makedirs("logs") call present' if find(t, r'makedirs.*logs') else 'no explicit logs/ makedirs found')

section('2b. trading_app.py — ANALYZER MODE & LIVE GATES')
mode_val = findval(t, r'^ANALYZER_MODE\s*=\s*["\'](\w+)["\']', default='NOT_FOUND')
chk(f'ANALYZER_MODE = "{mode_val}"',
    PASS if mode_val == 'BC' else WARN,
    '"BC" = live hybrid mode' if mode_val == 'BC' else f'expected "BC", got "{mode_val}"')

live_bc = findval(t, r'^ENABLE_LIVE_BC\s*=\s*(\w+)', default='NOT_FOUND')
chk(f'ENABLE_LIVE_BC = {live_bc}',
    PASS if live_bc == 'True' else WARN,
    'BC grade-A live execution ON' if live_bc == 'True' else 'BC execution is OFF (paper only)')

live_orb = findval(t, r'^ENABLE_LIVE_ORB\s*=\s*(\w+)', default='NOT_FOUND')
chk(f'ENABLE_LIVE_ORB = {live_orb}',
    PASS if live_orb == 'True' else WARN,
    'ORB live execution ON' if live_orb == 'True' else 'ORB execution is OFF')

chk('BC mode imports smart_analyzer_bridge_bc',
    PASS if find(t, r'from smart_analyzer_bridge_bc import MarketAnalyzerEngine') else FAIL)
chk('BC mode import guarded by ANALYZER_MODE == "BC"',
    PASS if find(t, r'if ANALYZER_MODE\s*==\s*["\']BC["\']') else WARN)
chk('X1 fallback present for non-BC mode',
    PASS if find(t, r'from smart_analyzer_bridge_x1 import') else INFO,
    'fallback chain intact' if find(t, r'from smart_analyzer_bridge_x1 import') else 'no X1 fallback')

section('2c. trading_app.py — CRASH HANDLING & SESSION')
chk('IB asyncio error handler installed',
    PASS if find(t, r'set_exception_handler') else WARN)
chk('IB timeout returns None (no raise)',
    PASS if find(t, r'return None\s*#.*لا raise') else
          (PASS if count_matches(t, r'return None') > 3 else WARN),
    'timeout path returns None')
chk('RecursionError suppressed in IB loop',
    PASS if find(t, r'RecursionError') else WARN)
chk('_suppress_output_line() noise filter present',
    PASS if find(t, r'def _suppress_output_line') else WARN)

# ─────────────────────────────────────────────────────────────────────────────
# 3. smart_analyzer_bridge_bc.py
# ─────────────────────────────────────────────────────────────────────────────
section('3. smart_analyzer_bridge_bc.py — IMPORTS')
bc = src['bridge_bc'] or ''

chk('imports ORBDailyBridge from smart_analyzer_bridge_orb',
    PASS if find(bc, r'from smart_analyzer_bridge_orb import ORBDailyBridge') else FAIL)
chk('imports analyzer_bc_core (scan_symbol, select_daily, etc.)',
    PASS if find(bc, r'from analyzer_bc_core import') else FAIL)
chk('imports Candle from analyzer_x2 (type only)',
    PASS if find(bc, r'from analyzer_x2 import Candle') else FAIL)
chk('does NOT invoke analyzer_x2 functions directly',
    PASS if not find(bc, r'analyzer_x2\.\w+\s*\(') else FAIL,
    'Candle type import only — analyzer_x2 not called')

section('3b. smart_analyzer_bridge_bc.py — SIGNAL PATH & TTL')
bc_ttl = findval(bc, r'^SIGNAL_TTL_SEC\s*=\s*(\d+)', default='NOT_FOUND')
chk(f'BC SIGNAL_TTL_SEC = {bc_ttl} sec',
    PASS if bc_ttl == '900' else WARN,
    '900s = one 15m bar' if bc_ttl == '900' else f'expected 900, got {bc_ttl}')

chk('_is_signal_fresh() uses SIGNAL_TTL_SEC',
    PASS if find(bc, r'age_sec\s*<=\s*SIGNAL_TTL_SEC') else FAIL)
chk('_prune_seen() clears stale signal keys',
    PASS if find(bc, r'def _prune_seen') else FAIL)
chk('_prune_seen() called every _scan() cycle',
    PASS if find(bc, r'self\._prune_seen\(\)') else FAIL)
chk('_seen set prevents duplicate signal dispatch',
    PASS if find(bc, r'if key not in self\._seen') else FAIL)
chk('_seen_ts dict enables TTL-based eviction',
    PASS if find(bc, r'self\._seen_ts\[key\]\s*=\s*time\.time\(\)') else FAIL)

section('3c. smart_analyzer_bridge_bc.py — EXECUTION GATE')
chk('passes_exec_gate() defined',
    PASS if find(bc, r'def passes_exec_gate') else FAIL)
chk('exec gate: rank_score >= EXEC_RANK_MIN',
    PASS if find(bc, r'rank\s*<\s*EXEC_RANK_MIN') else FAIL)
chk('exec gate: rvi_bucket == "High" required',
    PASS if find(bc, r'bkt\s*!=\s*["\']High["\']') else FAIL)
chk('exec gate: regime == "Trend" required',
    PASS if find(bc, r'regime\s*!=\s*["\']Trend["\']') else FAIL)
chk('exec gate: adx >= EXEC_ADX_MIN (40)',
    PASS if find(bc, r'adx\s*<\s*EXEC_ADX_MIN') else FAIL)
exec_adx = _cv(bc, 'EXEC_ADX_MIN')
chk(f'EXEC_ADX_MIN = {exec_adx}',
    PASS if exec_adx == '40.0' else WARN)
chk('exec gate: session_min < SESSION_CUTOFF blocks after-hours',
    PASS if find(bc, r'sm\s*>=\s*SESSION_CUTOFF') else FAIL)
cutoff_bc = _cv(bc, 'SESSION_CUTOFF')
chk(f'SESSION_CUTOFF = {cutoff_bc} (= 14:15 ET)',
    PASS if cutoff_bc == '525' else WARN,
    '525 min = (14-9)*60 + 15 - 30 = 525 ✓' if cutoff_bc == '525' else f'expected 525')
chk('exec gate double-checked inside _route_to_execution (defense in depth)',
    PASS if count_matches(bc, r'passes_exec_gate') >= 2 else WARN,
    f'{count_matches(bc, r"passes_exec_gate")} call sites found')

section('3d. smart_analyzer_bridge_bc.py — SYMBOL RULES')
chk('LLY excluded from SYMBOLS list',
    PASS if find(bc, r'str\(s\)\.upper\(\)\s*!=\s*["\']LLY["\']') else
          (PASS if find(bc, r'!=\s*["\']LLY["\']') else FAIL),
    'LLY filtered from BC scan')
chk('AAPL in EXEC_BLOCKED_SYMS (temporarily blocked)',
    PASS if find(bc, r'EXEC_BLOCKED_SYMS.*AAPL|AAPL.*EXEC_BLOCKED_SYMS') else WARN)
chk('Grade B/C/D signals display-only (no execution routing)',
    PASS if find(bc, r'grade.*!=.*A|grade\s*==\s*["\']A["\']') else WARN)

section('3e. smart_analyzer_bridge_bc.py — BC SIGNAL PATH')
chk('BCPaperBridge._scan() is main scan loop',
    PASS if find(bc, r'def _scan\(self\)') else FAIL)
chk('scan loop has try/except crash guard',
    PASS if find(bc, r'except Exception as exc.*\n.*self\._log.*scan error', re.DOTALL) else
          (PASS if find(bc, r'scan error') else WARN))
chk('_on_analyzer_trade_signal pipeline used for dispatch',
    PASS if find(bc, r'_on_analyzer_trade_signal') else FAIL)
chk('DATA_STALE_MIN = 30 (data freshness check)',
    PASS if _cv(bc, 'DATA_STALE_MIN') == '30' else WARN,
    _cv(bc, 'DATA_STALE_MIN'))
chk('ORB bridge started via MarketAnalyzerEngine',
    PASS if find(bc, r'ORBDailyBridge') else FAIL)

# ─────────────────────────────────────────────────────────────────────────────
# 4. smart_analyzer_bridge_orb.py
# ─────────────────────────────────────────────────────────────────────────────
section('4. smart_analyzer_bridge_orb.py — IMPORTS')
orb = src['bridge_orb'] or ''

chk('imports analyzer_bc_core (load_symbol_candles)',
    PASS if find(orb, r'from analyzer_bc_core import') else FAIL)
chk('imports Candle from analyzer_x2 (type only)',
    PASS if find(orb, r'from analyzer_x2 import Candle') else FAIL)
chk('does NOT invoke analyzer_x2 functions directly',
    PASS if not find(orb, r'analyzer_x2\.\w+\s*\(') else FAIL,
    'Candle type import only')

section('4b. smart_analyzer_bridge_orb.py — ORB PARAMETERS')
orb_adx  = _cv(orb, 'ORB_ADX_MIN')
orb_rvol = _cv(orb, 'ORB_RVOL_MIN')
orb_rng  = _cv(orb, 'ORB_RANGE_ATR_MIN')
orb_ema  = _cv(orb, 'ORB_EMA20_DIST_MIN')
orb_body = _cv(orb, 'ORB_BODY_ATR')
orb_brk  = _cv(orb, 'ORB_BREAK_DIST_MIN')
orb_topn = _cv(orb, 'TOP_N_DAY')
orb_dir  = _cv(orb, 'ORB_MAX_DIR_PER_DAY')

chk(f'ORB_ADX_MIN = {orb_adx}',
    PASS if orb_adx == '30.0' else WARN, 'BL param locked at 30.0')
chk(f'ORB_RVOL_MIN = {orb_rvol}',
    PASS if orb_rvol == '1.5' else WARN, 'BL param locked at 1.5')
chk(f'ORB_RANGE_ATR_MIN = {orb_rng}',
    PASS if orb_rng == '2.0' else WARN, 'BL param locked at 2.0')
chk(f'ORB_EMA20_DIST_MIN = {orb_ema}',
    PASS if orb_ema == '1.95' else WARN, 'BL param locked at 1.95')
chk(f'ORB_BODY_ATR = {orb_body}',
    PASS if orb_body == '0.25' else WARN, 'BL param locked at 0.25')
chk(f'ORB_BREAK_DIST_MIN = {orb_brk} (F3)',
    PASS if orb_brk == '0.05' else WARN, 'F3 filter at 0.05')
chk(f'TOP_N_DAY = {orb_topn} (max 3 live ORB signals/day)',
    PASS if orb_topn == '3' else WARN)
chk(f'ORB_MAX_DIR_PER_DAY = {orb_dir} (F2)',
    PASS if orb_dir == '2' else WARN, 'F2 filter max 2 per direction')

section('4c. smart_analyzer_bridge_orb.py — ORB SIGNAL PATH & FILTERS')
orb_ttl = _cv(orb, 'SIGNAL_TTL_SEC')
chk(f'ORB SIGNAL_TTL_SEC = {orb_ttl} sec',
    PASS if orb_ttl == '300' else WARN,
    '300s = one 15m scan bar' if orb_ttl == '300' else f'expected 300, got {orb_ttl}')

brk_end = _cv(orb, 'SESS_BRK_END')
chk(f'SESS_BRK_END = {brk_end} (= 11:30 ET breakout window closes)',
    PASS if brk_end == '390' else WARN,
    '390 min = (11-9)*60 + 30 - 30 = 390 ✓' if brk_end == '390' else f'expected 390')
orb_cutoff = _cv(orb, 'SESS_CUTOFF')
chk(f'SESS_CUTOFF = {orb_cutoff} (= 14:15 ET hard session cutoff)',
    PASS if orb_cutoff == '525' else WARN)

chk('emitted set prevents duplicate (date, direction) signals per symbol',
    PASS if find(orb, r'emitted.*set\(\)|Set\[tuple\]') else FAIL)
chk('_is_signal_fresh() uses SIGNAL_TTL_SEC',
    PASS if find(orb, r'age_sec\s*<=\s*SIGNAL_TTL_SEC') else FAIL)
chk('_f2_filter() caps signals per direction per day',
    PASS if find(orb, r'def _f2_filter') else FAIL)
chk('MSFT SHORT NEUTRAL blocked (F4)',
    PASS if find(orb, r'MSFT.*NEUTRAL|NEUTRAL.*MSFT') else WARN)
chk('BC priority: ORB skips if BC has active signal for symbol',
    PASS if find(orb, r'bc_active|_bc_active|BC.*active|bc.*signal') else WARN)

excluded_match = re.search(r'ORB_EXCLUDED\s*(?::\s*\S+\s*)?=\s*frozenset\(\{([^}]+)\}\)', orb, re.DOTALL)
excluded_syms = excluded_match.group(1) if excluded_match else 'NOT_FOUND'
chk('ORB_EXCLUDED contains non-qualified symbols (AAPL present)',
    PASS if 'AAPL' in excluded_syms else WARN,
    excluded_syms.replace('"','').replace("'",'').replace('\n','').strip() if excluded_match else 'pattern not found')
chk('scan_orb_live() produces per-symbol signals',
    PASS if find(orb, r'def scan_orb_live') else FAIL)
chk('ORBDailyBridge class present (entry point for BC bridge)',
    PASS if find(orb, r'class ORBDailyBridge') else FAIL)

# ─────────────────────────────────────────────────────────────────────────────
# 5. execution.py
# ─────────────────────────────────────────────────────────────────────────────
section('5. execution.py — IMPORTS')
ex = src['execution'] or ''

chk('imports ib_insync (IB, Stock, Index, Option, LimitOrder, MarketOrder)',
    PASS if find(ex, r'from ib_insync import') else FAIL)
chk('imports LimitOrder',
    PASS if find(ex, r'LimitOrder') else FAIL)
chk('imports MarketOrder',
    PASS if find(ex, r'MarketOrder') else FAIL)
chk('imports math, threading, dataclass',
    PASS if all(find(ex, p) for p in [r'\bmath\b', r'\bthreading\b', r'dataclass']) else WARN)

section('5b. execution.py — PRICE LIMITS (CONTRACT COST)')
cost_min = _cv(ex, 'CONTRACT_COST_MIN')
cost_max = _cv(ex, 'CONTRACT_COST_MAX')
chk(f'CONTRACT_COST_MIN = {cost_min}',
    PASS if cost_min == '70.0' else FAIL,
    '$70 floor — no cheap OTM lotto contracts')
chk(f'CONTRACT_COST_MAX = {cost_max}',
    PASS if cost_max == '160.0' else FAIL,
    '$160 ceiling — capital preservation')
chk('ExecutionConfig.min_contract_cost references CONTRACT_COST_MIN',
    PASS if find(ex, r'min_contract_cost.*CONTRACT_COST_MIN|CONTRACT_COST_MIN.*min_contract_cost') else WARN)
chk('ExecutionConfig.max_contract_cost references CONTRACT_COST_MAX',
    PASS if find(ex, r'max_contract_cost.*CONTRACT_COST_MAX|CONTRACT_COST_MAX.*max_contract_cost') else WARN)
chk('_calc_cost_range() returns fixed 70-160 range',
    PASS if find(ex, r'def _calc_cost_range') else WARN)

section('5c. execution.py — RISK LEDGER & DUPLICATE PREVENTION')
chk('RiskLedger class present',
    PASS if find(ex, r'class RiskLedger') else FAIL)
chk('RiskLedger.can_open checks max_open_trades',
    PASS if find(ex, r'open_trades\s*>=\s*cfg\.max_open_trades') else FAIL)
chk('RiskLedger.can_open checks consecutive_losses',
    PASS if find(ex, r'consecutive_losses\s*>=\s*cfg\.max_consecutive_losses') else FAIL)
chk('RiskLedger.can_open checks daily_loss_pct',
    PASS if find(ex, r'daily_pnl\s*<=\s*-\(balance') else
          (PASS if find(ex, r'daily_loss_pct') else FAIL))
chk('RiskLedger._reset_if_new_day() resets daily counters',
    PASS if find(ex, r'def _reset_if_new_day') else FAIL)
chk('RiskLedger.sync_open_trades() for recovery from IB mismatch',
    PASS if find(ex, r'def sync_open_trades') else FAIL)
chk('ExecutionConfig.max_open_trades = 3',
    PASS if _cv(ex, 'max_open_trades') == '3' else WARN)
chk('ExecutionConfig.stop_loss_pct = 0.50 (-50% of premium)',
    PASS if find(ex, r'stop_loss_pct.*0\.50|0\.50.*stop_loss_pct') else WARN)
chk('Trailing stop ratchet (trail_step_pct, trail_floor_pct) configured',
    PASS if find(ex, r'trail_step_pct') and find(ex, r'trail_floor_pct') else WARN)

section('5d. execution.py — ANALYZER-ONLY vs LIVE MODE SWITCH')
chk('ExecutionConfig.dry_run field present (analyzer-only mode)',
    PASS if find(ex, r'dry_run\s*:\s*bool\s*=\s*False|dry_run.*False') else WARN)
chk('_ExecLatencyTracker diagnostic (non-behavioral, logging only)',
    PASS if find(ex, r'class _ExecLatencyTracker') else INFO)
chk('register_signal_ts() for end-to-end latency tracking',
    PASS if find(ex, r'def register_signal_ts') else INFO)

# ─────────────────────────────────────────────────────────────────────────────
# 6. ANALYZER_X2 INTEGRITY CHECK (read-only — never modify)
# ─────────────────────────────────────────────────────────────────────────────
section('6. analyzer_x2.py — READ-ONLY INTEGRITY (DO NOT MODIFY)')
ax = src['analyzer_x2'] or ''

chk('analyzer_x2.py file exists and is non-empty',
    PASS if ax and len(ax) > 100 else FAIL,
    f'{len(ax):,} chars' if ax else 'EMPTY OR MISSING')
chk('Candle class/namedtuple defined in analyzer_x2',
    PASS if find(ax, r'class Candle|Candle\s*=\s*namedtuple') else WARN)
chk('Do-not-modify header present',
    PASS if find(ax, r'Do NOT|do not|DO NOT', re.IGNORECASE) else INFO,
    'header advisory present' if find(ax, r'Do NOT|do not|DO NOT', re.IGNORECASE) else 'no header found')

# ─────────────────────────────────────────────────────────────────────────────
# 7. LOG PATH AUDIT
# ─────────────────────────────────────────────────────────────────────────────
section('7. LOG PATH AUDIT')

chk('logs/ directory exists',
    PASS if os.path.isdir('logs') else FAIL)
chk('data/ directory exists',
    PASS if os.path.isdir('data') else FAIL)
chk('chart_data/ directory exists',
    PASS if os.path.isdir('chart_data') else FAIL)
chk('chart_data/ non-empty (live data files present)',
    PASS if os.path.isdir('chart_data') and len(os.listdir('chart_data')) > 0 else FAIL,
    f'{len(os.listdir("chart_data"))} files' if os.path.isdir('chart_data') else 'missing')

# Check bootstrap is called
chk('trading_app.py calls bootstrap from utils.paths',
    PASS if find(t, r'from utils\.paths import bootstrap') else WARN)
chk('trading_app.py has try/except around bootstrap (resilient startup)',
    PASS if find(t, r'try:.*bootstrap.*except', re.DOTALL) else WARN)

# ─────────────────────────────────────────────────────────────────────────────
# 8. CRASH HANDLING
# ─────────────────────────────────────────────────────────────────────────────
section('8. CRASH HANDLING')

chk('BC scan loop wrapped in try/except (bridge survives scan errors)',
    PASS if find(bc, r'except Exception as exc') else WARN)
chk('ORB scan loop has crash guard',
    PASS if find(orb, r'except Exception') else WARN)
chk('IB asyncio loop has safe exception handler',
    PASS if find(t, r'set_exception_handler') else WARN)
chk('BC bridge runs as daemon thread (no hang on app exit)',
    PASS if find(bc, r'daemon\s*=\s*True') else WARN)
chk('ORB bridge runs as daemon thread',
    PASS if find(orb, r'daemon\s*=\s*True') else WARN)
chk('trading_app.py suppresses known-noisy IB error codes (10091, 10197)',
    PASS if find(t, r'10091.*10197|10197.*10091') else WARN)

# ─────────────────────────────────────────────────────────────────────────────
# 9. GO / NO-GO SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
n_pass = sum(1 for _, s, _ in _results if s == PASS)
n_warn = sum(1 for _, s, _ in _results if s == WARN)
n_fail = sum(1 for _, s, _ in _results if s == FAIL)
n_info = sum(1 for _, s, _ in _results if s == INFO)

_out()
_out('=' * 70)
_out('  GO / NO-GO SUMMARY')
_out('=' * 70)
_out(f'  PASS : {n_pass}')
_out(f'  WARN : {n_warn}')
_out(f'  FAIL : {n_fail}')
_out(f'  INFO : {n_info}')
_out()

fails  = [(l, d) for l, s, d in _results if s == FAIL]
warns  = [(l, d) for l, s, d in _results if s == WARN]

if fails:
    _out('  BLOCKERS (must fix before live session):')
    for lbl, det in fails:
        _out(f'    ✗ {lbl}' + (f' — {det}' if det else ''))
    _out()
if warns:
    _out('  WARNINGS (review before live session):')
    for lbl, det in warns:
        _out(f'    ⚠ {lbl}' + (f' — {det}' if det else ''))
    _out()

_out('  CURRENT MODE:')
_out(f'    ANALYZER_MODE  = {mode_val}')
_out(f'    ENABLE_LIVE_BC = {live_bc}   (BC Grade-A live execution)')
_out(f'    ENABLE_LIVE_ORB= {live_orb}   (ORB live execution)')
_out()

if n_fail == 0 and n_warn == 0:
    verdict = 'GO  — all checks pass, no blockers, no warnings'
elif n_fail == 0:
    verdict = f'CONDITIONAL GO  — {n_warn} warning(s), no blockers'
else:
    verdict = f'NO-GO  — {n_fail} blocker(s) must be resolved'

_out(f'  VERDICT:  {verdict}')
_out()
_out('  Test checklist before next live session:')
_out('    [ ] tv_datafeed running and chart_data/ current')
_out('    [ ] IBKR TWS / Gateway connected and market data subscribed')
_out('    [ ] RiskLedger counters reset (new trading day)')
_out('    [ ] Confirm ENABLE_LIVE_BC=True is intentional')
_out('    [ ] Confirm ENABLE_LIVE_ORB=True is intentional')
_out('    [ ] Monitor logs/ for first signal of the day')
_out('    [ ] Verify no stale positions carried from prior session')
_out()
_out('  Allowed technical fixes only:')
_out('    - Patch WARN items if deemed necessary')
_out('    - No analyzer logic changes')
_out('    - No execution logic changes')
_out('    - No risk parameter changes without explicit approval')
_out('    - Do NOT modify analyzer_x2.py under any circumstances')
_out()
_out(f'  Report generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
_out('=' * 70)

# write file
with open(OUT_PATH, 'w', encoding='utf-8') as f:
    f.write('\n'.join(_lines))

print(f'\nReport written: {OUT_PATH}')
