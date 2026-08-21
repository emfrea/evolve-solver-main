"""Per-component cost diagnostics for a species -- problem-agnostic.

Given a directory of the species' evaluated solutions (sol_<stem>.<ext>, one per instance),
validate each via the problem's evaluator and sum per-component costs. The result feeds the
scheduler: a species' bottleneck is the component where it is most behind the population best.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def find_solutions_dir(species_dir: Path) -> Optional[Path]:
    """Default local layout: species_dir/eval_solutions/ (the compute backend may return
    a different dir explicitly)."""
    for c in (species_dir / "eval_solutions", species_dir / "solutions"):
        if c.exists() and any(c.glob("sol_*")):
            return c
    return None


def build_diagnostics(species_dir: Path, problem, solutions_dir: Path | None = None,
                      save: bool = True) -> dict:
    """Validate the species' solutions and sum per-component costs over problem.instances."""
    species_dir = Path(species_dir)
    sols = Path(solutions_dir) if solutions_dir else find_solutions_dir(species_dir)
    components = problem.components
    component_totals = {c: 0.0 for c in components}
    per_instance: dict[str, dict] = {}
    feasible = 0

    if sols is None:
        result = {"species": species_dir.name, "feasible_count": 0, "total_cost": 0.0,
                  "component_totals": component_totals, "per_instance": {},
                  "error": "no solutions found"}
    else:
        ext = problem.config.inst_ext
        for stem in problem.instances:
            sol = sols / f"sol_{stem}.{ext}"
            if not sol.exists():
                per_instance[stem] = {"feasible": False}
                continue
            res = problem.validate(stem, sol)
            if not res.get("feasible"):
                per_instance[stem] = {"feasible": False}
                continue
            feasible += 1
            comps = {c: float(res.get("components", {}).get(c, 0.0)) for c in components}
            per_instance[stem] = {"feasible": True, "total": float(res.get("cost", sum(comps.values()))),
                                  "components": comps}
            for c in components:
                component_totals[c] += comps[c]
        result = {
            "species": species_dir.name,
            "feasible_count": feasible,
            "total_cost": sum(component_totals.values()),
            "component_totals": component_totals,
            "per_instance": per_instance,
        }
    if save:
        species_dir.mkdir(parents=True, exist_ok=True)
        (species_dir / "diagnostics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
