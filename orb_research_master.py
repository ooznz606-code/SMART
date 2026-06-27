from pathlib import Path
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "docs" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

PHASES = [
    {
        "name": "Phase 7B Robustness",
        "script": "phase7b_robustness.py",
        "required_outputs": [
            "docs/results/phase7b_robustness.csv",
            "docs/results/phase7b_robustness.md",
        ],
    },
    {
        "name": "Phase 8 Combination Validation",
        "script": "phase8_combination_validation.py",
        "required_outputs": [
            "docs/results/phase8_combination_validation.csv",
            "docs/results/phase8_combination_validation.md",
        ],
        "optional": True,
    },
    {
        "name": "Phase 9 OOS Validation",
        "script": "phase9_oos_validation.py",
        "required_outputs": [
            "docs/results/phase9_walkforward.csv",
            "docs/results/phase9_walkforward.md",
        ],
        "optional": True,
    },
    {
        "name": "Phase 10 Final Recommendation",
        "script": "phase10_recommendation.py",
        "required_outputs": [
            "docs/results/phase10_recommendation.md",
        ],
        "optional": True,
    },
]

def run_phase(phase):
    script = ROOT / phase["script"]

    if not script.exists():
        if phase.get("optional"):
            print(f"⚠️  SKIP {phase['name']}: script not found: {phase['script']}")
            return "SKIPPED"
        print(f"❌ BLOCKER: required script missing: {phase['script']}")
        return "FAILED"

    print("\n" + "=" * 90)
    print(f"RUNNING: {phase['name']}")
    print("=" * 90)

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    log_path = RESULTS / f"{script.stem}_runlog.txt"
    log_path.write_text(
        "STDOUT:\n" + proc.stdout + "\n\nSTDERR:\n" + proc.stderr,
        encoding="utf-8",
    )

    print(proc.stdout[-4000:] if proc.stdout else "")
    if proc.stderr.strip():
        print("\nSTDERR:")
        print(proc.stderr[-2000:])

    if proc.returncode != 0:
        print(f"❌ FAILED: {phase['name']} returncode={proc.returncode}")
        print(f"Log: {log_path}")
        return "FAILED"

    missing = []
    for out in phase.get("required_outputs", []):
        if not (ROOT / out).exists():
            missing.append(out)

    if missing:
        print(f"⚠️  COMPLETED but missing expected outputs:")
        for m in missing:
            print(f"   - {m}")
        return "WARN"

    print(f"✅ DONE: {phase['name']}")
    return "DONE"

def main():
    print("=" * 90)
    print("ORB RESEARCH MASTER RUNNER")
    print("=" * 90)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Mode: research only. No production files are modified by this runner.")
    print("Dataset expected: chart_data_research/")
    print("=" * 90)

    statuses = []

    for phase in PHASES:
        status = run_phase(phase)
        statuses.append((phase["name"], status))
        if status == "FAILED":
            print("\nSTOPPING because a required phase failed.")
            break

    summary = ["# ORB Research Master Summary", ""]
    summary.append(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    summary.append("")
    summary.append("| Phase | Status |")
    summary.append("|------|--------|")
    for name, status in statuses:
        summary.append(f"| {name} | {status} |")

    out = RESULTS / "orb_research_master_summary.md"
    out.write_text("\n".join(summary), encoding="utf-8")

    print("\n" + "=" * 90)
    print("MASTER SUMMARY")
    print("=" * 90)
    for name, status in statuses:
        print(f"{status:8s} | {name}")
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    main()
