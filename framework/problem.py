"""The problem boundary: everything that makes the framework problem-SPECIFIC lives
behind this module. A problem is a folder problems/<name>/ containing:

    problem.yaml   -- declarative config (instances, components, splice map, solver CLI,
                      fitness metric, scheduler tunables, genome style, compute backend)
    evaluator.py   -- one function validate(instance_path, solution_path) -> dict
    genome.yaml    -- the design space (structured gene catalog OR free-form phase list)
    docs/*.md      -- the natural-language spec fed to the build/evolve agents
    instances/*    -- opaque benchmark instance files (the framework never parses them)
    seeds/*.json   -- optional seed genomes

load_problem(name) merges all of this into an in-memory Problem object that is threaded
through the scheduler, diagnostics, runner, agents and compute backend. The framework
core imports NO problem constant; it only reads fields off the Problem.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from . import limits as _limits

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBLEMS_DIR = REPO_ROOT / "problems"
RUNS_DIR = REPO_ROOT / "runs"

# Scheduler defaults (a problem may override any in its `scheduler:` block).
_SCHED_DEFAULTS = {
    "donor_gap": 0.15,
    "plateau_k": 2,
    "diversity_decay": 0.7,
    "infeasible_bonus": 1e9,
    "evolve_max_attempts": 2,
    "crossover_max_attempts": 2,
    "new_species_prize": 0.5,
    "abs_prize_floor": 1.0,
    "feas_repair_giveup": 3,
}
_DOC_ORDER = ["problem_description.md", "inputs_outputs.md", "solver_contract.md"]


@dataclass
class SpliceUnit:
    unit: str          # genome unit that owns the component (gene category or phase name)
    phase: str         # human description of the phase, used in crossover prompts


@dataclass
class ProblemConfig:
    name: str
    root: Path                       # problems/<name>/
    run_root: Path                   # runs/<name>/
    # instances (opaque files: <inst_dir>/<stem>.<ext>)
    instances: list[str]
    gate: list[str]
    inst_dir: Path
    inst_ext: str
    # objective decomposition
    components: list[str]
    splice: dict[str, SpliceUnit]
    # solver contract
    solver_cli: str
    solver_entry: str
    # fitness
    fitness_metric: str
    fitness_opts: dict
    # scheduler
    scheduler: dict
    n_instances: int
    full_feasible: int
    donor_min_feasible: int
    # genome
    genome_style: str                # "structured" | "freeform"
    genome_file: Path
    genome_spec: dict                # parsed genome.yaml
    # compute
    compute: dict
    # limits (config/limits.yaml merged with problem.yaml `limits:`)
    limits: dict
    # scheduler-policy extensions (all optional in problem.yaml)
    levers: dict[str, list]          # component -> ORDERED [SpliceUnit, ...] that can move it
    phases: list[dict]               # marker-delimited structural phases (crossover units)
    component_priors: dict[str, float]
    crossover_family_key: Optional[str]   # genome unit whose allele defines a "family"
    # misc
    libraries: str
    docs: dict[str, str]             # {filename: text}


class Problem:
    """Runtime handle to a loaded problem. Passed everywhere a constant used to be."""

    def __init__(self, config: ProblemConfig, evaluator: Callable[[str, str], dict]):
        self.config = config
        self._evaluator = evaluator

    # --- convenience accessors ---
    @property
    def name(self) -> str:
        return self.config.name

    @property
    def components(self) -> list[str]:
        return self.config.components

    @property
    def instances(self) -> list[str]:
        return self.config.instances

    @property
    def gate(self) -> list[str]:
        return self.config.gate

    @property
    def run_root(self) -> Path:
        return self.config.run_root

    @property
    def species_dir(self) -> Path:
        return self.config.run_root / "species"

    @property
    def evolution_log(self) -> Path:
        return self.config.run_root / "evolution_log.json"

    def instance_path(self, stem: str) -> Path:
        return self.config.inst_dir / f"{stem}.{self.config.inst_ext}"

    def levers_for(self, component: str) -> list[SpliceUnit]:
        """Ordered lever list for a component (problem.yaml `levers:`), else [splice owner]."""
        lv = self.config.levers.get(component)
        if lv:
            return list(lv)
        su = self.splice_for(component)
        return [su] if su else []

    @property
    def phases(self) -> list[dict]:
        return self.config.phases

    def splice_for(self, component: str) -> Optional[SpliceUnit]:
        return self.config.splice.get(component)

    def validate(self, instance_stem: str, solution_path) -> dict:
        """Call the problem's evaluator. Returns
        {feasible, cost, components: {name: float}, violations?, raw?}."""
        return self._evaluator(str(self.instance_path(instance_stem)), str(solution_path))


# --- loading ---

def _load_evaluator(root: Path, module: str, function: str) -> Callable[[str, str], dict]:
    path = root / f"{module}.py"
    if not path.exists():
        raise FileNotFoundError(f"evaluator module not found: {path}")
    spec = importlib.util.spec_from_file_location(f"_problem_{root.name}_{module}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    fn = getattr(mod, function, None)
    if not callable(fn):
        raise AttributeError(f"{path} has no callable '{function}'")
    return fn


def load_problem(name: str, repo_root: Path | None = None) -> Problem:
    repo = repo_root or REPO_ROOT
    root = repo / "problems" / name
    if not (root / "problem.yaml").exists():
        raise FileNotFoundError(f"no problem.yaml at {root}")
    cfg = yaml.safe_load((root / "problem.yaml").read_text(encoding="utf-8")) or {}

    inst = cfg.get("instances", {})
    instances = list(inst.get("all", []))
    gate = list(inst.get("gate", []))
    inst_dir = root / inst.get("dir", "instances")
    inst_ext = inst.get("ext", "json")

    components = list(cfg.get("components", []))
    splice = {c: SpliceUnit(**v) for c, v in (cfg.get("splice", {}) or {}).items()}

    levers = {c: [SpliceUnit(**u) for u in (lst or [])]
              for c, lst in (cfg.get("levers", {}) or {}).items()}
    phases = list(cfg.get("phases", []) or [])
    priors = {c: float(v) for c, v in (cfg.get("component_priors", {}) or {}).items()}
    xover_cfg = cfg.get("crossover", {}) or {}

    solver = cfg.get("solver", {})
    fitness = cfg.get("fitness", {}) or {}
    lim = _limits.load_limits(cfg.get("limits"))
    # scheduler caps: legacy problem.yaml `scheduler:` block > limits > defaults
    sched = {**_SCHED_DEFAULTS, **(lim.get("scheduler") or {}), **(cfg.get("scheduler", {}) or {})}
    n_instances = sched.get("n_instances", len(instances))
    full_feasible = sched.get("full_feasible", n_instances)
    donor_min_feasible = sched.get("donor_min_feasible", max(1, n_instances - 2))

    genome = cfg.get("genome", {}) or {}
    genome_style = genome.get("style", "freeform")
    genome_file = root / genome.get("file", "genome.yaml")
    genome_spec = {}
    if genome_file.exists():
        genome_spec = yaml.safe_load(genome_file.read_text(encoding="utf-8")) or {}

    docs = {}
    docs_dir = root / "docs"
    if docs_dir.exists():
        order = cfg.get("docs_order", _DOC_ORDER)
        seen = set()
        for fn in list(order) + sorted(p.name for p in docs_dir.glob("*.md")):
            if fn in seen:
                continue
            p = docs_dir / fn
            if p.exists():
                docs[fn] = p.read_text(encoding="utf-8")
                seen.add(fn)

    ev = cfg.get("evaluator", {}) or {}
    evaluator = _load_evaluator(root, ev.get("module", "evaluator"), ev.get("function", "validate"))

    config = ProblemConfig(
        name=cfg.get("name", name), root=root, run_root=(repo / "runs" / name),
        instances=instances, gate=gate, inst_dir=inst_dir, inst_ext=inst_ext,
        components=components, splice=splice,
        solver_cli=solver.get("cli", ""), solver_entry=solver.get("entry", "solve.py"),
        fitness_metric=fitness.get("metric", "rank_sum"), fitness_opts=fitness.get("opts", {}) or {},
        scheduler=sched, n_instances=n_instances, full_feasible=full_feasible,
        donor_min_feasible=donor_min_feasible,
        genome_style=genome_style, genome_file=genome_file, genome_spec=genome_spec,
        compute=cfg.get("compute", {"backend": "local"}) or {"backend": "local"},
        limits=lim,
        levers=levers, phases=phases, component_priors=priors,
        crossover_family_key=xover_cfg.get("family_key"),
        libraries=cfg.get("libraries", "the Python standard library"),
        docs=docs,
    )
    _validate_config(config, evaluator)
    config.run_root.mkdir(parents=True, exist_ok=True)
    return Problem(config, evaluator)


def _validate_config(c: ProblemConfig, evaluator) -> None:
    errs = []
    if not c.instances:
        errs.append("instances.all is empty")
    if c.n_instances != len(c.instances):
        errs.append(f"scheduler.n_instances ({c.n_instances}) != len(instances.all) ({len(c.instances)})")
    if not set(c.gate) <= set(c.instances):
        errs.append(f"gate instances not a subset of all: {set(c.gate) - set(c.instances)}")
    if not c.components:
        errs.append("components is empty")
    if not set(c.splice) <= set(c.components):
        errs.append(f"splice keys not a subset of components: {set(c.splice) - set(c.components)}")
    if not set(c.levers) <= set(c.components):
        errs.append(f"levers keys not a subset of components: {set(c.levers) - set(c.components)}")
    if not set(c.component_priors) <= set(c.components):
        errs.append(f"component_priors keys not a subset of components: {set(c.component_priors) - set(c.components)}")
    seen_ids = set()
    for ph in c.phases:
        if not ph.get("id") or not ph.get("name"):
            errs.append(f"phase entry needs id and name: {ph}")
        if ph.get("id") in seen_ids:
            errs.append(f"duplicate phase id {ph.get('id')}")
        seen_ids.add(ph.get("id"))
        if not set(ph.get("components") or []) <= set(c.components):
            errs.append(f"phase {ph.get('id')} components not a subset of components")
    if c.genome_style not in ("structured", "freeform"):
        errs.append(f"genome.style must be structured|freeform, got {c.genome_style!r}")
    if c.fitness_metric not in ("rank_sum", "normalized_cost"):
        errs.append(f"fitness.metric must be rank_sum|normalized_cost, got {c.fitness_metric!r}")
    if not callable(evaluator):
        errs.append("evaluator is not callable")
    if errs:
        raise ValueError(f"problem '{c.name}' config invalid:\n  - " + "\n  - ".join(errs))
