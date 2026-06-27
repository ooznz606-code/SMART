
import csv
from pathlib import Path

import backtest_current_bc_orb as bt
import smart_analyzer_bridge_orb as orb

OUT_DIR = Path("reports")
OUT_DIR.mkdir(exist_ok=True)

ORIG = {
    "ORB_EXCLUDED": orb.ORB_EXCLUDED,
    "SESS_BRK_END": orb.SESS_BRK_END,
    "ORB_EMA20_DIST_MIN": orb.ORB_EMA20_DIST_MIN,
}

def restore():
    for k, v in ORIG.items():
        setattr(orb, k, v)

def stats(name, trades):
    w = sum(1 for t in trades if t.get("R", 0) > 0)
    l = sum(1 for t in trades if t.get("R", 0) < 0)
    be = sum(1 for t in trades if t.get("R", 0) == 0)
    gw = sum(t.get("R", 0) for t in trades if t.get("R", 0) > 0)
    gl = abs(sum(t.get("R", 0) for t in trades if t.get("R", 0) < 0))
    pf = gw / gl if gl else 999
    total = sum(t.get("R", 0) for t in trades)

    eq = peak = dd = 0
    for t in sorted(trades, key=lambda x: (x.get("date",""), x.get("time",""), x.get("symbol",""))):
        eq += t.get("R", 0)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)

    wr = w / max(1, w + l) * 100
    return dict(name=name, N=len(trades), W=w, L=l, BE=be, WR=round(wr,1), PF=round(pf,2), TotalR=round(total,2), MaxDD=round(dd,2))

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

def all_chart_symbols():
    syms = set()
    for f in Path("chart_data").glob("*_5m.json"):
        sym = f.stem.rsplit("_", 1)[0].upper()
        if sym != "TEST":
            syms.add(sym)
    return sorted(syms)

def run_with_excluded(excluded, use_best_filters=True):
    restore()
    orb.ORB_EXCLUDED = frozenset(excluded)

    if use_best_filters:
        orb.SESS_BRK_END = 420
        orb.ORB_EMA20_DIST_MIN = 1.2

    trades = bt.backtest_orb()
    restore()
    return trades

def delta(row, base):
    row["dN"] = row["N"] - base["N"]
    row["dWR"] = round(row["WR"] - base["WR"], 1)
    row["dPF"] = round(row["PF"] - base["PF"], 2)
    row["dTotalR"] = round(row["TotalR"] - base["TotalR"], 2)
    row["dMaxDD"] = round(row["MaxDD"] - base["MaxDD"], 2)

    row["Tier"] = "C"
    if row["TotalR"] > base["TotalR"] and row["PF"] >= base["PF"] and row["MaxDD"] <= 3.0:
        row["Tier"] = "A"
    elif row["TotalR"] >= base["TotalR"] and row["MaxDD"] <= 3.2:
        row["Tier"] = "B"
    return row

def main():
    print("="*90)
    print("ORB SYMBOL OPTIMIZER ? REAL ENGINE")
    print("Uses real scan_orb_live through backtest_orb")
    print("Best filter candidate only in research: end=12:00, EMA_DIST=1.2")
    print("="*90)

    all_syms = all_chart_symbols()
    current_excluded = set(orb.ORB_EXCLUDED)
    current_allowed = [s for s in all_syms if s not in current_excluded]

    base_trades = run_with_excluded(current_excluded, use_best_filters=True)
    base = stats("BASE_BEST_FILTERS_CURRENT_SYMBOLS", base_trades)

    print(f"All chart symbols: {len(all_syms)}")
    print(f"Current allowed: {len(current_allowed)}")
    print(f"Current excluded: {len(current_excluded)}")
    print(f"Base: N={base['N']} WR={base['WR']} PF={base['PF']} TotalR={base['TotalR']} MaxDD={base['MaxDD']}")

    single_rows = []

    candidates = sorted(current_excluded)
    print("\nPHASE 1 ? allow back one excluded symbol at a time")
    for i, sym in enumerate(candidates, 1):
        excluded = set(current_excluded) - {sym}
        trades = run_with_excluded(excluded, use_best_filters=True)
        s = stats(f"allow_{sym}", trades)
        s["symbol"] = sym
        s["action"] = "ALLOW_BACK_ONE"
        delta(s, base)
        single_rows.append(s)
        print(f"[{i:02d}/{len(candidates):02d}] {sym:7s} Tier={s['Tier']} N={s['N']} PF={s['PF']} TotalR={s['TotalR']} MaxDD={s['MaxDD']} dR={s['dTotalR']} dDD={s['dMaxDD']}")

    single_rows.sort(key=lambda r: (r["Tier"]=="A", r["dTotalR"], r["dPF"], -r["dMaxDD"], r["dN"]), reverse=True)

    tier_a = [r["symbol"] for r in single_rows if r["Tier"] == "A"]

    print("\nPHASE 2 ? cumulative add Tier A symbols")
    cumulative_rows = []
    excluded = set(current_excluded)
    for i, sym in enumerate(tier_a, 1):
        excluded.discard(sym)
        trades = run_with_excluded(excluded, use_best_filters=True)
        s = stats(f"cumulative_top_{i}", trades)
        s["added_symbols"] = ",".join(tier_a[:i])
        s["last_added"] = sym
        delta(s, base)
        cumulative_rows.append(s)
        print(f"[{i:02d}] +{sym:7s} N={s['N']} WR={s['WR']} PF={s['PF']} TotalR={s['TotalR']} MaxDD={s['MaxDD']} Tier={s['Tier']}")

    write_csv(OUT_DIR / "orb_symbol_single_add.csv", single_rows)
    write_csv(OUT_DIR / "orb_symbol_cumulative_add.csv", cumulative_rows)
    write_csv(OUT_DIR / "orb_symbol_optimizer_base.csv", [base])

    print("\nTOP SINGLE SYMBOLS:")
    for r in single_rows[:20]:
        print(f"{r['symbol']:7s} Tier={r['Tier']} N={r['N']:3d} WR={r['WR']:5.1f}% PF={r['PF']:5.2f} TotalR={r['TotalR']:6.2f} MaxDD={r['MaxDD']:4.2f} dR={r['dTotalR']:+.2f} dDD={r['dMaxDD']:+.2f}")

    print("\nBEST CUMULATIVE:")
    if cumulative_rows:
        best = sorted(cumulative_rows, key=lambda r: (r["Tier"]=="A", r["TotalR"], r["PF"], -r["MaxDD"]), reverse=True)[0]
        print(f"Added: {best['added_symbols']}")
        print(f"N={best['N']} WR={best['WR']} PF={best['PF']} TotalR={best['TotalR']} MaxDD={best['MaxDD']}")
    else:
        print("No Tier A symbols found.")

    print("\nSaved:")
    print(" - reports\\orb_symbol_single_add.csv")
    print(" - reports\\orb_symbol_cumulative_add.csv")
    print(" - reports\\orb_symbol_optimizer_base.csv")

if __name__ == "__main__":
    try:
        main()
    finally:
        restore()
