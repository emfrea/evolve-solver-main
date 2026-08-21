"""Run a species solver and evaluate it -- problem-agnostic.

The solver command is built from the problem's `solver.cli` template (named slots +
an optional [...] section). Validation is delegated to the problem's evaluator; this
module never parses instance or solution files.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _build_command(template: str, subs: dict, progress_log: Optional[str]) -> list[str]:
    """Fill a CLI template. `[...]` sections are included only when all their slots are set."""
    def fill_optional(m):
        seg = m.group(1)
        try:
            filled = seg.format(**subs)
        except KeyError:
            return ""
        return filled if progress_log else ""
    s = re.sub(r"\[([^\]]*)\]", fill_optional, template)
    s = s.format(**subs)
    return shlex.split(s, posix=False)


def run_solver(solve_py, instance_path, out_path, problem, *, seed=57, time_limit=60,
               threads=4, progress_log=None):
    subs = {
        "python": sys.executable, "solve": str(solve_py),
        "instance": str(instance_path), "output": str(out_path),
        "seed": seed, "time_limit": time_limit, "threads": threads,
        "progress_log": str(progress_log) if progress_log else "",
    }
    cmd = _build_command(problem.config.solver_cli, subs, progress_log)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def evaluate_solver(solve_py, problem, instances=None, *, seed=57, time_limit=60,
                    threads=4, workdir=None) -> dict:
    """Run the solver over `instances` (default: all), validate each via the problem, and
    return {stem: {feasible, cost, components, ...}}."""
    solve_py = Path(solve_py)
    instances = instances if instances is not None else problem.instances
    workdir = Path(workdir) if workdir else solve_py.parent / "eval_solutions"
    workdir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    for stem in instances:
        inst_path = problem.instance_path(stem)
        out_path = workdir / f"sol_{stem}.{problem.config.inst_ext}"
        rc, _out, err = run_solver(solve_py, inst_path, out_path, problem,
                                   seed=seed, time_limit=time_limit, threads=threads)
        if rc != 0 or not out_path.exists():
            results[stem] = {"feasible": False, "cost": None, "components": {},
                             "rc": rc, "error": (err or "")[:300]}
            continue
        v = problem.validate(stem, out_path)
        v["rc"] = rc
        results[stem] = v
    return results


def results_vector(results: dict, instances) -> list[Optional[float]]:
    """Per-instance feasible cost vector (None for infeasible), for fitness."""
    return [(results.get(i, {}).get("cost") if results.get(i, {}).get("feasible") else None)
            for i in instances]
