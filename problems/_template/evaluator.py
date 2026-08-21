"""Evaluator for <your problem>. This is the ONLY place the framework learns your feasibility
rules and objective. Implement validate() and nothing else is required.

Contract:
    validate(instance_path: str, solution_path: str) -> dict with keys
        feasible   : bool   -- did the solution satisfy every HARD constraint?
        cost       : float | None  -- scalar objective (None when infeasible)
        components : dict[str, float]  -- one entry per name in problem.yaml `components`
                                          (lower is better; they should sum-relate to `cost`)
    Optional keys: violations (list[str] for debugging), raw (any extra detail).

Both paths are opaque files the framework passes between the solver and you. Parse them however
your problem defines them (JSON below is just a common choice). For a problem backed by an
external checker binary, shell out here and parse its output into the same dict.
"""
from __future__ import annotations

import json


def validate(instance_path: str, solution_path: str) -> dict:
    with open(instance_path, encoding="utf-8") as f:
        instance = json.load(f)
    try:
        with open(solution_path, encoding="utf-8") as f:
            solution = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"feasible": False, "cost": None, "components": {},
                "violations": [f"unreadable solution: {exc}"]}

    violations: list[str] = []

    # --- 1. HARD constraints -> feasibility -------------------------------------------------
    # TODO: check every hard rule; append a message to `violations` for each breach.
    # e.g. if some_rule_broken(instance, solution): violations.append("rule X broken")

    feasible = not violations
    if not feasible:
        return {"feasible": False, "cost": None, "components": {}, "violations": violations}

    # --- 2. SOFT costs -> components (must match problem.yaml `components`) ------------------
    components = {
        # "TermA": ...,
        # "TermB": ...,
    }
    cost = float(sum(components.values()))

    return {"feasible": True, "cost": cost, "components": components}


if __name__ == "__main__":
    import sys
    print(json.dumps(validate(sys.argv[1], sys.argv[2]), indent=2))
