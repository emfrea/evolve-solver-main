"""Smoke-test a problem's evaluator against a solution file, or against a trivial/empty solution
to confirm it fails closed (infeasible, not crash). Use while writing evaluator.py::validate.

    # check the contract shape on a real solution you produced:
    python scripts/smoke_evaluator.py --problem <name> --instance inst01 --solution path/to/sol.json

    # check it rejects garbage without crashing (writes a tiny empty-ish solution):
    python scripts/smoke_evaluator.py --problem <name> --instance inst01 --empty
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framework.problem import load_problem

REQUIRED = {"feasible", "cost", "components"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True)
    ap.add_argument("--instance", required=True, help="instance stem (from instances.all)")
    ap.add_argument("--solution", help="path to a solution file to validate")
    ap.add_argument("--empty", action="store_true", help="validate a trivial {} solution (fail-closed test)")
    args = ap.parse_args()

    p = load_problem(args.problem)
    if args.instance not in p.instances:
        print(f"[FAIL] '{args.instance}' not in instances.all"); return 1

    if args.empty:
        sol = ROOT / "runs" / p.name / "_smoke_empty.json"
        sol.parent.mkdir(parents=True, exist_ok=True)
        sol.write_text("{}", encoding="utf-8")
        sol_path = sol
    elif args.solution:
        sol_path = Path(args.solution)
        if not sol_path.exists():
            print(f"[FAIL] solution not found: {sol_path}"); return 1
    else:
        print("[FAIL] pass --solution <path> or --empty"); return 1

    try:
        out = p.validate(args.instance, sol_path)
    except Exception as exc:
        print(f"[FAIL] validate() raised {type(exc).__name__}: {exc} "
              f"(it must fail closed, returning feasible=False, not crash)")
        return 2

    print(json.dumps(out, indent=2))
    errs = []
    if not isinstance(out, dict) or not REQUIRED <= set(out):
        errs.append(f"return must be a dict with keys >= {REQUIRED}")
    else:
        if out["feasible"]:
            comp_keys = set(out.get("components", {}))
            missing = set(p.components) - comp_keys
            if missing:
                errs.append(f"feasible result missing component keys: {missing}")
            if out.get("cost") is None:
                errs.append("feasible result has cost=None")
        else:
            if out.get("cost") is not None:
                errs.append("infeasible result should have cost=None")
    if errs:
        print("\n[FAIL] contract violations:")
        for e in errs:
            print(f"  - {e}")
        return 3
    print(f"\n[OK] evaluator returns a valid contract dict ({'feasible' if out['feasible'] else 'infeasible'}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
