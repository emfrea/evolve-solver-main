"""Evaluator for toy-binpack (pure Python, no external binary).

validate(instance_path, solution_path) -> {feasible, cost, components, violations, raw}

Instance JSON:  {"bins": B, "capacity": C, "max_color_per_bin": K,
                 "items": [{"id": str, "size": int, "color": str, "weight": float}, ...]}
Solution JSON:  {"assignment": {item_id: bin_index}}

FEASIBILITY (hard): every item assigned to a valid bin index in [0, B); no bin's total size
exceeds capacity C. (A solver that overpacks or drops items is infeasible.)

OBJECTIVE (minimize), three components:
  Overfill      = sum over bins of max(0, load - SOFT_TARGET*C) * OVERFILL_PENALTY
  ColorConflict = sum over bins of max(0, distinct_colors - K) * COLOR_WEIGHT
  Imbalance     = variance of bin loads * IMBALANCE_WEIGHT
"""
from __future__ import annotations

import json

SOFT_TARGET = 0.75      # fraction of capacity above which Overfill accrues
OVERFILL_PENALTY = 10.0
COLOR_WEIGHT = 100.0
IMBALANCE_WEIGHT = 1.0


def validate(instance_path: str, solution_path: str) -> dict:
    inst = json.load(open(instance_path, encoding="utf-8"))
    try:
        sol = json.load(open(solution_path, encoding="utf-8"))
    except Exception as exc:
        return {"feasible": False, "cost": None, "components": {}, "violations": None,
                "raw": f"bad solution file: {exc}"}

    B = int(inst["bins"])
    C = float(inst["capacity"])
    K = int(inst["max_color_per_bin"])
    items = {it["id"]: it for it in inst["items"]}
    assignment = sol.get("assignment", {}) or {}

    violations = 0
    # every item assigned to a valid bin
    for iid in items:
        b = assignment.get(iid)
        if b is None or not isinstance(b, int) or b < 0 or b >= B:
            violations += 1
    # no unknown items
    for iid in assignment:
        if iid not in items:
            violations += 1

    loads = [0.0] * B
    colors = [set() for _ in range(B)]
    for iid, it in items.items():
        b = assignment.get(iid)
        if isinstance(b, int) and 0 <= b < B:
            loads[b] += float(it["size"])
            colors[b].add(it["color"])
    # capacity is a HARD constraint
    for b in range(B):
        if loads[b] > C + 1e-9:
            violations += 1

    if violations:
        return {"feasible": False, "cost": None, "components": {}, "violations": violations,
                "raw": f"{violations} hard violation(s) (invalid assignment or over capacity)"}

    soft = SOFT_TARGET * C
    overfill = sum(max(0.0, loads[b] - soft) for b in range(B)) * OVERFILL_PENALTY
    color_conflict = sum(max(0, len(colors[b]) - K) for b in range(B)) * COLOR_WEIGHT
    mean = sum(loads) / B if B else 0.0
    imbalance = (sum((x - mean) ** 2 for x in loads) / B) * IMBALANCE_WEIGHT if B else 0.0

    components = {"Overfill": round(overfill, 4), "ColorConflict": round(color_conflict, 4),
                  "Imbalance": round(imbalance, 4)}
    cost = round(sum(components.values()), 4)
    return {"feasible": True, "cost": cost, "components": components, "violations": 0,
            "raw": f"feasible; cost={cost} {components}"}
