"""Evolver agent: execute an EVOLVE or CROSSOVER decision -> a candidate solver.
Problem-agnostic; prompt assembly delegated to prompts.py; lock-proof candidate workspace.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .base import run_agent
from . import prompts
from .builder import load_genome, stage_workspace


def _setup_candidate(species: str, problem, donor: str | None = None, self_test_time: int = 20) -> Path:
    base = problem.species_dir / species
    cand = base / "_candidate"
    if cand.exists():
        shutil.rmtree(cand, ignore_errors=True)
    if cand.exists():
        # canonical workspace locked (orphaned handle); use a fresh uniquely-named one.
        n = 2
        while (base / f"_candidate__{n}").exists():
            n += 1
        cand = base / f"_candidate__{n}"
    stage_workspace(cand, problem, self_test_time)
    shutil.copy2(base / problem.config.solver_entry, cand / problem.config.solver_entry)
    if donor:
        shutil.copy2(problem.species_dir / donor / problem.config.solver_entry, cand / "donor_solve.py")
    return cand


def execute_decision(decision, population, problem, *, model: str = "opus",
                     self_test_time: int = 20, timeout: int = 2400) -> dict:
    species = decision.species
    genome = load_genome(species, problem)
    target_diag = population.get(species, {})
    donor_diag = population.get(decision.donor) if decision.donor else None

    cand = _setup_candidate(species, problem,
                            donor=decision.donor if decision.operation == "crossover" else None,
                            self_test_time=self_test_time)

    if decision.operation == "crossover":
        prompt = prompts.crossover_prompt(species, genome, decision, target_diag, donor_diag,
                                          problem, self_test_time)
    else:
        prompt = prompts.evolve_prompt(species, genome, decision, target_diag, donor_diag,
                                       problem, self_test_time)

    print(f"[exec] {decision.operation.upper()} on {species} (target={decision.target_component}"
          + (f", donor={decision.donor}" if decision.donor else "") + ")")
    champ_bytes = (problem.species_dir / species / problem.config.solver_entry).read_bytes()
    rc, rate_limited, elapsed = run_agent(cand, prompt, model=model, timeout=timeout)
    solve_py = cand / problem.config.solver_entry
    changed = solve_py.exists() and solve_py.read_bytes() != champ_bytes
    return {"species": species, "operation": decision.operation,
            "target_component": decision.target_component, "donor": decision.donor,
            "candidate_dir": str(cand), "agent_returncode": rc, "rate_limited": rate_limited,
            "agent_elapsed_s": round(elapsed, 1),
            "solve_py_exists": solve_py.exists(), "candidate_changed": bool(changed)}
