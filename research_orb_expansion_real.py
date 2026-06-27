
"""
ORB Expansion Research REAL
Uses the real production ORB engine:
smart_analyzer_bridge_orb.scan_orb_live()
Research only. No production code changes. No orders.
"""

import csv
import copy
import traceback
from pathlib import Path

import backtest_current_bc_orb as bt
import smart_analyzer_bridge_orb as orb

OUT_DIR = Path("reports")
OUT_DIR.mkdir(exist_ok=True)

BASELINE = {
    "N": 14,
    "WR": 61.5,
    "PF": 2.88,
    "TotalR": 9.40,
    "MaxDD": 3.20,
}

PARAMS_ORIGINAL = {
    "ORB_ADX_MIN": orb.ORB_ADX_MIN,
    "ORB_RVOL_MIN": orb.ORB_RVOL_MIN,
    "ORB_RANGE_ATR_MIN": orb.ORB_RANGE_ATR_MIN,
    "ORB_EMA20_DIST_MIN": orb.ORB_EMA20_DIST_MIN,
    "ORB_BREAK_DIST_MIN": orb.ORB_BREAK_DIST_MIN,
    "TOP_N_DAY": orb.TOP_N_DAY,
    "ORB_MAX_DIR_PER_DAY": orb.ORB_MAX_DIR_PER_DAY,
    "ORB_EXCLUDED": orb.ORB_EXCLUDED,
    "SESS_BRK_END": orb.SESS_BRK_END,
}


def restore():
    for k, v in PARAMS_ORIGINAL.items():
        setattr(orb, k, v)


def stats(name, trades):
    n = len(trades)
    w = sum(1 for t in trades if t.get("R", 0) > 0)
    l = sum(1 for t in trades if t.get("R", 0) < 0)
    be = sum(1 for t in trades if t.get("R", 0) == 0)

    gross_win = sum(t.get("R", 0) for t in trades if t.get("R", 0) > 0)
    gross_loss = abs(sum(t.get("R", 0) for t in trades if t.get("R", 0) < 0))
    pf = gross_win / gross_loss if gross_loss else (999.0 if gross_win > 0 else 0.0)

    total = sum(t.get("R", 0) for t in trades)

    eq = 0
    peak = 0
    maxdd = 0
    for t in sorted(trades, key=lambda x: (x.get("date", ""), x.get("time", ""))):
        eq += t.get("R", 0)
        peak = max(peak, eq)
        maxdd = max(maxdd, peak - eq)

    wr = w / max(1, w + l) * 100

    return {
        "name": name,
        "N": n,
        "W": w,
        "L": l,
        "BE": be,
        "WR": round(wr, 1),
        "PF": round(pf, 2),
        "TotalR": round(total, 2),
        "MaxDD": round(maxdd, 2),
    }


def run_real_orb_with_cfg(cfg):
    restore()

    orb.ORB_ADX_MIN = cfg.get("ADX", orb.ORB_ADX_MIN)
    orb.ORB_RVOL_MIN = cfg.get("RVOL", orb.ORB_RVOL_MIN)
    orb.ORB_RANGE_ATR_MIN = cfg.get("ORB_RANGE", orb.ORB_RANGE_ATR_MIN)
    orb.ORB_EMA20_DIST_MIN = cfg.get("EMA_DIST", orb.ORB_EMA20_DIST_MIN)
    orb.ORB_BREAK_DIST_MIN = cfg.get("BREAK_DIST", orb.ORB_BREAK_DIST_MIN)
    orb.TOP_N_DAY = cfg.get("TOP_N_DAY", orb.TOP_N_DAY)
    orb.ORB_MAX_DIR_PER_DAY = cfg.get("MAX_DIR_DAY", orb.ORB_MAX_DIR_PER_DAY)

    if "EXCLUDED" in cfg:
        orb.ORB_EXCLUDED = frozenset(cfg["EXCLUDED"])

    if "BRK_END" in cfg:
        orb.SESS_BRK_END = cfg["BRK_END"]

    trades = bt.backtest_orb()
    restore()
    return trades


def pass_rule(s):
    return (
        s["PF"] >= 2.5
        and s["MaxDD"] <= 3.0
        and s["N"] >= BASELINE["N"]
    )


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return

    fields = []
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    print("=" * 90)
    print("REAL ORB EXPANSION RESEARCH")
    print("Uses smart_analyzer_bridge_orb.scan_orb_live + current backtest_orb path")
    print("=" * 90)

    scenarios = []

    base_excl = set(orb.ORB_EXCLUDED)

    # Baseline
    scenarios.append(("BASELINE_REAL_CURRENT", {}))

    # Symbol exclusion studies
    for sym in sorted(["LLY", "TSLA", "SPY", "AAPL", "AMD", "AVGO", "COST", "GOOGL", "UBER", "NVDA", "META", "MSFT", "QQQ"]):
        scenarios.append((f"exclude_add_{sym}", {"EXCLUDED": base_excl | {sym}}))

    # Remove each currently excluded symbol
    for sym in sorted(base_excl):
        scenarios.append((f"allow_back_{sym}", {"EXCLUDED": base_excl - {sym}}))

    # Time window studies: SESS_BRK_END minutes from midnight ET
    # current 390 = 11:30 ET
    for name, end_min in [
        ("end_10_30", 330),
        ("end_11_00", 360),
        ("end_11_30_current", 390),
        ("end_12_00", 420),
        ("end_14_30", 570),
    ]:
        scenarios.append((name, {"BRK_END": end_min}))

    # Threshold studies
    for adx in [24, 26, 28, 30, 32, 35]:
        scenarios.append((f"adx_{adx}", {"ADX": float(adx)}))

    for rvol in [1.1, 1.2, 1.3, 1.4, 1.5, 1.7]:
        scenarios.append((f"rvol_{rvol}", {"RVOL": float(rvol)}))

    for rng in [1.2, 1.5, 1.8, 2.0, 2.2, 2.5]:
        scenarios.append((f"orb_range_{rng}", {"ORB_RANGE": float(rng)}))

    for ema in [1.2, 1.5, 1.75, 1.95, 2.1, 2.3]:
        scenarios.append((f"ema_dist_{ema}", {"EMA_DIST": float(ema)}))

    for brk in [0.02, 0.03, 0.05, 0.08, 0.10]:
        scenarios.append((f"break_dist_{brk}", {"BREAK_DIST": float(brk)}))

    # Combined promising scenarios
    scenarios.append(("combo_end12_ema1.2", {"BRK_END": 420, "EMA_DIST": 1.2}))
    scenarios.append(("combo_end12_exclude_LLY", {"BRK_END": 420, "EXCLUDED": base_excl | {"LLY"}}))
    scenarios.append(("combo_ema1.2_exclude_LLY", {"EMA_DIST": 1.2, "EXCLUDED": base_excl | {"LLY"}}))
    scenarios.append(("combo_end12_ema1.2_exclude_LLY", {"BRK_END": 420, "EMA_DIST": 1.2, "EXCLUDED": base_excl | {"LLY"}}))

    # Daily management
    for topn in [1, 2, 3, 4, 5]:
        scenarios.append((f"top_n_day_{topn}", {"TOP_N_DAY": topn}))

    for maxdir in [1, 2, 3]:
        scenarios.append((f"max_dir_day_{maxdir}", {"MAX_DIR_DAY": maxdir}))

    rows = []

    for idx, (name, cfg) in enumerate(scenarios, 1):
        print(f"[{idx:03d}/{len(scenarios):03d}] {name} ...", end=" ")
        try:
            trades = run_real_orb_with_cfg(cfg)
            s = stats(name, trades)
            s.update(cfg)
            s["PASS"] = pass_rule(s)
            rows.append(s)
            print(f"N={s['N']} WR={s['WR']} PF={s['PF']} TotalR={s['TotalR']} MaxDD={s['MaxDD']} PASS={s['PASS']}")
        except Exception as e:
            print("ERROR:", e)
            rows.append({"name": name, "ERROR": str(e)})

    restore()

    clean = [r for r in rows if "ERROR" not in r]
    clean.sort(key=lambda r: (r.get("PASS", False), r.get("PF", 0), r.get("TotalR", 0), -r.get("MaxDD", 999), r.get("N", 0)), reverse=True)

    write_csv(OUT_DIR / "orb_expansion_real_results.csv", clean)
    write_csv(OUT_DIR / "orb_expansion_real_errors.csv", [r for r in rows if "ERROR" in r])

    print("\n" + "=" * 90)
    print("TOP REAL ORB SCENARIOS")
    print("=" * 90)

    for r in clean[:30]:
        print(
            f"PASS={r.get('PASS')} | {r['name']:<28} "
            f"N={r['N']:3d} W={r['W']:2d} L={r['L']:2d} BE={r['BE']:2d} "
            f"WR={r['WR']:5.1f}% PF={r['PF']:5.2f} "
            f"TotalR={r['TotalR']:6.2f} MaxDD={r['MaxDD']:4.2f}"
        )

    print("\nSaved:")
    print(" - reports\\orb_expansion_real_results.csv")
    print(" - reports\\orb_expansion_real_errors.csv")

    print("\nDecision rule:")
    print("Do NOT modify production ORB unless a scenario beats baseline with PF>=2.5, MaxDD<=3R, and N>=14.")


if __name__ == "__main__":
    try:
        main()
    finally:
        restore()
