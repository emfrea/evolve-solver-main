"""Debug: print the scheduler's per-species decisions for a problem (no execution).

    python scripts/run_scheduler.py --problem toy
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framework.problem import load_problem
from framework.scheduler import load_population, load_history, decide_operation, select_next


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True)
    args = ap.parse_args()
    problem = load_problem(args.problem)
    pop = load_population(problem)
    hist = load_history(problem)
    if not pop:
        print(f"  no population under {problem.species_dir}"); return
    print(f"  population ({len(pop)} species), {len(hist)} history entries:")
    for sp in sorted(pop):
        d = decide_operation(sp, pop, hist, problem)
        print(f"    {sp:22s} -> {d.operation:11s} {d.target_component or ''}"
              + (f"  donor={d.donor}" if d.donor else "") + f"   prize={d.prize:,.1f}")
    nxt = select_next(pop, hist, problem)
    print(f"  select_next -> {nxt.operation} {nxt.species} {nxt.target_component or ''}")


if __name__ == "__main__":
    main()
