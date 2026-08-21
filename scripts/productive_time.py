"""Summarize productive (agent-working) time from a problem's evolution_log.json. Each step
records started_at/finished_at/duration_s (the clock starts AFTER the reachability wait, so
idle time spent waiting on compute is excluded). Useful for pacing quota/budget.

    python scripts/productive_time.py --problem toy
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framework.problem import load_problem


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True)
    args = ap.parse_args()
    problem = load_problem(args.problem)
    log_path = problem.evolution_log
    if not log_path.exists():
        print(f"no evolution_log at {log_path}"); return
    log = json.loads(log_path.read_text(encoding="utf-8"))
    if not log:
        print("evolution_log is empty."); return

    total = sum(r.get("duration_s", 0) or 0 for r in log)
    by_op = Counter()
    dur_by_op = Counter()
    promotions = 0
    for r in log:
        op = r.get("operation", "?")
        by_op[op] += 1
        dur_by_op[op] += r.get("duration_s", 0) or 0
        if r.get("promoted"):
            promotions += 1

    n = len(log)
    print(f"problem: {problem.name}")
    print(f"steps: {n}   promotions: {promotions} ({promotions/n*100:.0f}%)")
    print(f"productive time: {total/3600:.2f} h  ({total:,.0f} s)   avg {total/n:.0f} s/step")
    print("by operation:")
    for op in sorted(by_op):
        c = by_op[op]
        print(f"  {op:12s} {c:4d} steps   {dur_by_op[op]/3600:6.2f} h   avg {dur_by_op[op]/max(c,1):5.0f} s")
    first, last = log[0].get("started_at"), log[-1].get("finished_at")
    if first and last:
        print(f"span: {first} -> {last}")


if __name__ == "__main__":
    main()
