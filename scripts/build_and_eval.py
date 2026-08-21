"""Full per-species pipeline: build (agentic) -> local gate -> backend eval -> diagnose -> score.

    python scripts/build_and_eval.py --problem toy --species toy-greedy
    python scripts/build_and_eval.py --problem toy --species toy-greedy --skip-build   # reuse solve.py
    python scripts/build_and_eval.py --problem toy --species toy-greedy --skip-remote   # build+gate only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framework.problem import load_problem
from framework import runner, diagnostics
from framework.compute import get_backend
from framework.agents.builder import build_species


def local_gate(solve_py: Path, problem, time_limit: int) -> dict:
    """Re-run the gate instances to confirm feasibility before spending a full eval."""
    results = {}
    for stem in problem.gate:
        out = solve_py.parent / f"_gate_{stem}.{problem.config.inst_ext}"
        print(f"    [gate] {stem} ({time_limit}s)...", flush=True)
        rc, _o, err = runner.run_solver(solve_py, problem.instance_path(stem), out, problem,
                                        seed=42, time_limit=time_limit, threads=1)
        if rc != 0 or not out.exists():
            results[stem] = {"ok": False, "feasible": False, "error": (err or "")[:200]}
            continue
        v = problem.validate(stem, out)
        results[stem] = {"ok": True, "feasible": v.get("feasible"), "cost": v.get("cost")}
        out.unlink(missing_ok=True)
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True)
    ap.add_argument("--species", required=True)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--self-test-time", type=int, default=20)
    ap.add_argument("--gate-time", type=int, default=None)
    ap.add_argument("--build-timeout", type=int, default=2400)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--time-limit", type=int, default=None)
    ap.add_argument("--skip-remote", action="store_true", help="build + gate only")
    ap.add_argument("--skip-build", action="store_true", help="reuse existing solve.py")
    args = ap.parse_args()

    problem = load_problem(args.problem)
    comp = problem.config.compute
    seed = args.seed if args.seed is not None else comp.get("seed", 57)
    time_limit = args.time_limit if args.time_limit is not None else comp.get("time_limit", 60)
    gate_time = args.gate_time if args.gate_time is not None else max(2, min(time_limit, 20))

    sp_dir = problem.species_dir / args.species
    solve_py = sp_dir / problem.config.solver_entry

    if not args.skip_build:
        res = build_species(args.species, problem, model=args.model,
                            self_test_time=args.self_test_time, timeout=args.build_timeout)
        if not res["solve_py_exists"]:
            print("[FAIL] build produced no solver."); sys.exit(1)
    elif not solve_py.exists():
        print(f"[FAIL] --skip-build but no {solve_py}"); sys.exit(1)

    print(f"\n=== GATE: {args.species} ({gate_time}s on {problem.gate}) ===")
    gate = local_gate(solve_py, problem, gate_time)
    feas_n = sum(1 for r in gate.values() if r.get("feasible"))
    print(f"  gate: {feas_n}/{len(problem.gate)} feasible  {gate}")
    if feas_n == 0:
        print("[STOP] no gate instance feasible."); sys.exit(2)
    if args.skip_remote:
        print("[done] --skip-remote: build + gate complete."); return

    backend = get_backend(problem)
    if not backend.reachable():
        print("[STOP] compute backend not reachable."); sys.exit(3)
    backend.deploy(args.species, sp_dir)
    sols = backend.run(args.species, sp_dir, instances=problem.instances, seed=seed,
                       time_limit=time_limit, threads=comp.get("threads", 4))
    if sols is None:
        print("[FAIL] eval failed."); sys.exit(4)

    diag = diagnostics.build_diagnostics(sp_dir, problem, solutions_dir=sols)
    per_inst = {s: diag["per_instance"].get(s, {}).get("total") for s in problem.instances}
    (sp_dir / "results.json").write_text(json.dumps(
        {"species": args.species, "seed": seed, "feasible_count": diag["feasible_count"],
         "total_cost": diag["total_cost"], "per_instance": per_inst}, indent=2), encoding="utf-8")
    print(f"\n=== SCORE: {args.species} ===")
    print(f"  feasible: {diag['feasible_count']}/{problem.config.n_instances}  "
          f"total: {diag['total_cost']:,.0f}  components: {diag['component_totals']}")


if __name__ == "__main__":
    main()
