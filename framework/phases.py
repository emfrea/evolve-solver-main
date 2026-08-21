"""Marker-delimited structural phases -- the units of recombination.

A problem may declare, in problem.yaml:

    phases:
      - id: P1
        name: construction
        responsibility: "build the initial solution (owns Overfill)"
        boundary: "in: instance -> out: a feasible-or-repairable assignment"
        components: [Overfill]
      - id: P2
        ...

Every solver then organises its single file into blocks delimited EXACTLY by

    # ==== PHASE P1 construction START ====
    ...
    # ==== PHASE P1 construction END ====

Crossover extracts the donor's block by its markers and asks the evolver to transplant that
logic into the recipient, reconciling the boundary hand-off. This replaces "locate the
section informally": the markers make the splice interface explicit and checkable
(`has_all_phases`). Problem-agnostic: phase ids/names/responsibilities come from the config.
"""
from __future__ import annotations

import re
from typing import Optional


def marker_start(pid: str, name: str) -> str:
    return f"# ==== PHASE {pid} {name} START ===="


def marker_end(pid: str, name: str) -> str:
    return f"# ==== PHASE {pid} {name} END ===="


def extract_phase(text: str, pid: str, name: str) -> Optional[str]:
    """The block between the phase's markers (markers excluded), or None if absent."""
    pat = re.compile(re.escape(marker_start(pid, name)) + r"\n(.*?)\n" + re.escape(marker_end(pid, name)),
                     re.DOTALL)
    m = pat.search(text)
    return m.group(1) if m else None


def has_all_phases(text: str, phases: list[dict]) -> bool:
    return all(extract_phase(text, p["id"], p["name"]) is not None for p in phases)


def phase_for_component(phases: list[dict], component: str) -> Optional[dict]:
    for p in phases:
        if component in (p.get("components") or []):
            return p
    return None


def phase_label(p: dict) -> str:
    return f"{p['id']} {p['name']}"


def contract_block(phases: list[dict]) -> str:
    """Prompt text stating the phase contract (for the builder and the evolvers)."""
    if not phases:
        return ""
    rows = []
    for p in phases:
        comps = ", ".join(p.get("components") or []) or "-"
        rows.append(f"  {p['id']} {p['name']:<22} {p.get('responsibility', '')}  [owns: {comps}]")
        if p.get("boundary"):
            rows.append(f"      boundary: {p['boundary']}")
    ex = phases[0]
    return "\n".join([
        "The solver file MUST be organised into these self-contained, marker-delimited phases,",
        "each a function called in order, reading only its documented boundary input and returning",
        "its boundary output (keep them decoupled so any ONE can be replaced wholesale by a graft):",
        *rows,
        "Delimit each phase EXACTLY like this (the crossover matches on these lines verbatim):",
        f"    {marker_start(ex['id'], ex['name'])}",
        "    ...",
        f"    {marker_end(ex['id'], ex['name'])}",
    ])
