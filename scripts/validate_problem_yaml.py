"""Validate a problem's problem.yaml (+ evaluator importability + genome + docs) without running
anything. Run this after editing problems/<name>/. Exit 0 = ready to seed.

    python scripts/validate_problem_yaml.py --problem <name>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True)
    args = ap.parse_args()

    from framework.problem import load_problem  # imported late so errors are reported cleanly
    try:
        p = load_problem(args.problem)
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1

    c = p.config
    warn = []
    # non-fatal quality checks
    for stem in p.instances:
        if not p.instance_path(stem).exists():
            warn.append(f"instance file missing: {p.instance_path(stem)}")
    if c.genome_style == "structured" and not c.genome_spec:
        warn.append(f"structured genome but genome file empty/missing: {c.genome_file}")
    missing_splice = set(c.components) - set(c.splice)
    if missing_splice:
        warn.append(f"components with no splice entry (crossover can't target them): {missing_splice}")
    for doc in ("problem_description.md", "inputs_outputs.md", "solver_contract.md"):
        if doc not in c.docs:
            warn.append(f"missing doc (agents build blind without it): docs/{doc}")

    print(f"[OK] problem '{p.name}' loads and passes schema validation.")
    print(f"     instances: {len(p.instances)}  gate: {p.gate}  components: {p.components}")
    print(f"     genome: {c.genome_style}  fitness: {c.fitness_metric}  compute: {c.compute.get('backend')}")
    if warn:
        print("\n[warnings] (non-fatal, but fix before a real campaign):")
        for w in warn:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
