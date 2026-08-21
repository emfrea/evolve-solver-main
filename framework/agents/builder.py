"""Builder agent: turn a genome into a fresh solver via an agentic build + local self-test.
Problem-agnostic: the workspace, docs, gate instances, and CLI all come from the Problem.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .base import run_agent, AgentError
from . import prompts
from ..genome import genome_from_dict, proposal_to_genome


def load_genome(name: str, problem):
    """Resolve a species name to a genome: ideator proposal (runs/<p>/proposals/<name>.json)
    first, else a committed seed (problems/<p>/seeds/<name>.json)."""
    prop = problem.run_root / "proposals" / f"{name}.json"
    if prop.exists():
        return proposal_to_genome(json.loads(prop.read_text(encoding="utf-8")), problem)
    seed = problem.config.root / "seeds" / f"{name}.json"
    if seed.exists():
        return genome_from_dict(json.loads(seed.read_text(encoding="utf-8")), problem)
    raise AgentError(f"unknown species '{name}': no proposal or seed found.")


def stage_workspace(ws: Path, problem, self_test_time: int) -> None:
    """Copy the problem's evaluator + gate instances + a rendered check.py into `ws`."""
    ws.mkdir(parents=True, exist_ok=True)
    shutil.copy2(problem.config.root / "evaluator.py", ws / "evaluator.py")
    inst_dst = ws / "instances"
    inst_dst.mkdir(exist_ok=True)
    for stem in problem.gate:
        shutil.copy2(problem.instance_path(stem), inst_dst / f"{stem}.{problem.config.inst_ext}")
    (ws / "check.py").write_text(prompts.render_check_script(problem, self_test_time), encoding="utf-8")


def build_species(name: str, problem, *, model: str = "opus", self_test_time: int = 20,
                  timeout: int = 2400) -> dict:
    genome = load_genome(name, problem)
    ws = problem.species_dir / name
    stage_workspace(ws, problem, self_test_time)
    (ws / "genome.json").write_text(json.dumps(genome.to_dict(), indent=2), encoding="utf-8")

    print(f"[build] species '{name}' key={genome.species_key()}  ws={ws}")
    prompt = prompts.build_prompt(name, genome, problem, self_test_time)
    rc, rate_limited, elapsed = run_agent(ws, prompt, model=model, timeout=timeout)
    solve_py = ws / problem.config.solver_entry
    return {"species": name, "workspace": str(ws), "agent_returncode": rc,
            "rate_limited": rate_limited, "agent_elapsed_s": round(elapsed, 1),
            "solve_py_exists": solve_py.exists(),
            "solve_py": str(solve_py) if solve_py.exists() else None}
