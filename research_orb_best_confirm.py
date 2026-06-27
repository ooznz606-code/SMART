
import csv
from pathlib import Path

import backtest_current_bc_orb as bt
import smart_analyzer_bridge_orb as orb

OUT_DIR = Path("reports")
OUT_DIR.mkdir(exist_ok=True)

ORIG = {
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
    for t in sorted(trades, key=lambda x: (x.get("date", ""), x.get("time", ""), x.get("symbol", ""))):
        eq += t.get("R", 0)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)

    wr = w / max(1, w + l) * 100
    return dict(name=name, N=len(trades), W=w, L=l, BE=be, WR=round(wr,1), PF=round(pf,2), TotalR=round(total,2), MaxDD=round(dd,2))

def key(t):
    return (
        str(t.get("date", "")),
        str(t.get("time", "")),
        str(t.get("symbol", "")),
        str(t.get("direction", t.get("dir", ""))),
    )

def norm(t):
    return {
        "date": t.get("date",""),
        "time": t.get("time",""),
        "symbol": t.get("symbol",""),
        "direction": t.get("direction", t.get("dir","")),
        "outcome": t.get("outcome",""),
        "R": t.get("R",0),
        "score": t.get("score",""),
        "adx": t.get("adx",""),
        "rvol": t.get("rvol",""),
        "bias": t.get("bias",""),
        "entry": t.get("entry",""),
        "sl": t.get("sl",""),
        "tp": t.get("tp",""),
    }

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

def run_current():
    restore()
    return bt.backtest_orb()

def run_best():
    restore()
    orb.SESS_BRK_END = 420          # 12:00 ET
    orb.ORB_EMA20_DIST_MIN = 1.2    # instead of 1.95
    orb.ORB_EXCLUDED = frozenset(set(orb.ORB_EXCLUDED) | {"LLY"})
    trades = bt.backtest_orb()
    restore()
    return trades

def main():
    current = run_current()
    best = run_best()

    s1 = stats("CURRENT_BASELINE", current)
    s2 = stats("BEST_CANDIDATE_end12_ema1.2_exclude_LLY", best)

    cur_keys = {key(t): t for t in current}
    best_keys = {key(t): t for t in best}

    added = [norm(best_keys[k]) for k in sorted(best_keys.keys() - cur_keys.keys())]
    removed = [norm(cur_keys[k]) for k in sorted(cur_keys.keys() - best_keys.keys())]
    common = [norm(best_keys[k]) for k in sorted(best_keys.keys() & cur_keys.keys())]

    write_csv(OUT_DIR / "orb_confirm_summary.csv", [s1, s2])
    write_csv(OUT_DIR / "orb_confirm_added_trades.csv", added)
    write_csv(OUT_DIR / "orb_confirm_removed_trades.csv", removed)
    write_csv(OUT_DIR / "orb_confirm_common_trades.csv", common)

    print("="*90)
    print("ORB BEST CANDIDATE CONFIRMATION")
    print("="*90)

    for s in [s1, s2]:
        print(
            f"{s['name']:<42} "
            f"N={s['N']:3d} W={s['W']:2d} L={s['L']:2d} BE={s['BE']:2d} "
            f"WR={s['WR']:5.1f}% PF={s['PF']:5.2f} "
            f"TotalR={s['TotalR']:6.2f} MaxDD={s['MaxDD']:4.2f}"
        )

    print("\nADDED TRADES:")
    if not added:
        print("none")
    for t in added:
        print(f"+ {t['date']} {t['time']} {t['symbol']} {t['direction']} R={t['R']} outcome={t['outcome']} score={t['score']} adx={t['adx']} rvol={t['rvol']} bias={t['bias']}")

    print("\nREMOVED TRADES:")
    if not removed:
        print("none")
    for t in removed:
        print(f"- {t['date']} {t['time']} {t['symbol']} {t['direction']} R={t['R']} outcome={t['outcome']} score={t['score']} adx={t['adx']} rvol={t['rvol']} bias={t['bias']}")

    print("\nSaved:")
    print(" - reports\\orb_confirm_summary.csv")
    print(" - reports\\orb_confirm_added_trades.csv")
    print(" - reports\\orb_confirm_removed_trades.csv")
    print(" - reports\\orb_confirm_common_trades.csv")

    print("\nDecision:")
    print("If added trades are clean and removed trades are weak/LLY, candidate is ready for wider validation, not immediate live adoption.")

if __name__ == "__main__":
    try:
        main()
    finally:
        restore()
