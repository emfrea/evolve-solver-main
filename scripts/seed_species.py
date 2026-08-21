"""Cold-start: create the initial island population for a problem. Either invent N species with
the ideator agent, or build named seed genomes from problems/<name>/seeds/*.json. Each species is
built (agentic solve.py), locally gated, then (unless --skip-remote) fully evaluated so it lands in
the population with a diagnostics.json the scheduler can read.

    python scripts/seed_species.py --problem toy --count 2            # ideator invents 2
    python scripts/seed_species.py --problem toy --names toy-ffd toy-balance   # build named seeds
    python scripts/seed_species.py --problem toy --names toy-ffd --skip-remote # build + gate only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framework.problem import load_problem
from framework import diagnostics
from framework.agents.builder import build_species
from framework.agents.ideator import propose_new_species
from framework.compute import get_backend
from scripts.build_and_eval import local_gate


def _seed_names_from_dir(problem) -> list[str]:
    seeds = problem.config.root / "seeds"
    return sorted(p.stem for p in seeds.glob("*.json")) if seeds.exists() else []


def _stage_named_seed(name: str, problem) -> None:
    """Copy a seeds/<name>.json genome into runs/<p>/proposals/ so build_species can load it."""
    src = problem.config.root / "seeds" / f"{name}.json"
    if not src.exists():
        return
    dst = problem.run_root / "proposals" / f"{name}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _build_one(name: str, problem, backend, args, seed, time_limit, gate_time) -> None:
    print(f"\n=== SEED {name} ===")
    build_species(name, problem, model=args.model, self_test_time=args.self_test_time,
                  timeout=args.build_timeout)
    sp_dir = problem.species_dir / name
    solve_py = sp_dir / problem.config.solver_entry
    if not solve_py.exists():
        print(f"[skip] {name}: no solver produced."); return

    gate = local_gate(solve_py, problem, gate_time)
    feas_n = sum(1 for r in gate.values() if r.get("feasible"))
    print(f"  gate: {feas_n}/{len(problem.gate)} feasible")
    if feas_n == 0:
        print(f"[skip] {name}: failed gate."); return
    if args.skip_remote:
        print(f"[ok] {name}: built + gated (--skip-remote)."); return

    if not backend.reachable():
        print("[stop] backend unreachable."); return
    backend.deploy(name, sp_dir)
    sols = backend.run(name, sp_dir, instances=problem.instances, seed=seed,
                       time_limit=time_limit, threads=problem.config.compute.get("threads", 4))
    if sols is None:
        print(f"[skip] {name}: eval failed."); return
    diag = diagnostics.build_diagnostics(sp_dir, problem, solutions_dir=sols)
    per_inst = {s: diag["per_instance"].get(s, {}).get("total") for s in problem.instances}
    (sp_dir / "results.json").write_text(json.dumps(
        {"species": name, "seed": seed, "feasible_count": diag["feasible_count"],
         "total_cost": diag["total_cost"], "per_instance": per_inst}, indent=2), encoding="utf-8")
    print(f"[ok] {name}: {diag['feasible_count']}/{problem.config.n_instances}  {diag['total_cost']:,.0f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True)
    ap.add_argument("--names", nargs="*", help="explicit seed names (built from seeds/<name>.json if present)")
    ap.add_argument("--count", type=int, default=0, help="invent this many species with the ideator")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--self-test-time", type=int, default=20)
    ap.add_argument("--gate-time", type=int, default=None)
    ap.add_argument("--build-timeout", type=int, default=2400)
    ap.add_argument("--skip-remote", action="store_true")
    args = ap.parse_args()

    problem = load_problem(args.problem)
    comp = problem.config.compute
    seed = comp.get("seed", 57)
    time_limit = comp.get("time_limit", 60)
    gate_time = args.gate_time if args.gate_time is not None else max(2, min(time_limit, 20))
    backend = get_backend(problem)

    names = list(args.names or [])
    if not names and args.count == 0:
        names = _seed_names_from_dir(problem)
        if names:
            print(f"[seed] no --names/--count; using seeds/ dir: {names}")

    existing = []
    for name in names:
        _stage_named_seed(name, problem)
        _build_one(name, problem, backend, args, seed, time_limit, gate_time)
        existing.append(name)

    for i in range(args.count):
        ctx = (f"Existing species (be ALGORITHMICALLY DISTINCT): {', '.join(existing)}"
               if existing else "")
        try:
            proposal = propose_new_species(problem, model=args.model, extra_context=ctx,
                                           existing_names=existing)
        except Exception as exc:
            print(f"[ideator] failed on invention {i+1}: {exc}"); continue
        name = proposal.get("name", f"species-{i+1}")
        (problem.run_root / "proposals").mkdir(parents=True, exist_ok=True)
        (problem.run_root / "proposals" / f"{name}.json").write_text(json.dumps(proposal, indent=2),
                                                                     encoding="utf-8")
        _build_one(name, problem, backend, args, seed, time_limit, gate_time)
        existing.append(name)

    print(f"\n[seed] done. population dir: {problem.species_dir}")


if __name__ == "__main__":
    main()
