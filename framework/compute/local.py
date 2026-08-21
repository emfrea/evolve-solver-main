"""LocalBackend: run the solver over the instance set locally (the default).

No HPC, no network -- reachable() is always True so the operational layer's
reachability-wait loop is a harmless no-op locally.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import ComputeBackend
from .. import runner


class LocalBackend(ComputeBackend):
    def reachable(self) -> bool:
        return True

    def deploy(self, name: str, solver_dir: Path) -> bool:
        return True

    def run(self, name: str, solver_dir: Path, *, instances, seed: int,
            time_limit: int, threads: int) -> Optional[Path]:
        solver_dir = Path(solver_dir)
        solve_py = solver_dir / self.problem.config.solver_entry
        if not solve_py.exists():
            print(f"    [local] no {solve_py}")
            return None
        out_dir = solver_dir / "eval_solutions"
        print(f"    [local] evaluating {name} on {len(instances)} instance(s) "
              f"(time_limit={time_limit}s)...", flush=True)
        runner.evaluate_solver(solve_py, self.problem, instances=instances,
                               seed=seed, time_limit=time_limit, threads=threads, workdir=out_dir)
        return out_dir
