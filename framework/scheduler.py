"""Scheduler policy: decide EVOLVE vs CROSSOVER vs NEW_SPECIES per species, and pick the
next operation across the island population. PROBLEM-AGNOSTIC: components, levers, phases,
priors and every cap come from the loaded Problem / config/limits.yaml.

Escalation ladder, per species:
    feasibility-first (with a give-up valve)
    -> HOPELESS rule: worst-ranked, never promoted, >= hopeless_attempts real attempts
       => replace it (self-cull -> new_species)
    -> INSTANCE-TARGETED crossover: a same-family donor strictly better on >= N instances
    -> per component, rotate through its ORDERED LEVER LIST (several genome units can move
       one component); a lever dies after evolve_max_attempts failed REAL attempts or ONE
       deliberate no-op; evidence expires at each promotion
    -> when every lever of the bottleneck is spent: structural CROSSOVER from any cheaper
       donor (the donor_gap threshold gates EARLY grafts only)
    -> when every component is exhausted: WILDCARD (free-form, wildcard_cap per window)
    -> then NEW_SPECIES (bounded by max_species + cooldown; at cap, cull the worst)
    -> else a low-priority re-push, flagged `exhausted` so selection moves elsewhere.

Target choice = attractiveness = prior(component) x online affinity x (abs_base + donor gap).
Island selection = rank-based weight (leader bias, exploration floor, exhausted cap) x
diversity_decay^(recent ops); feasibility repairs always dominate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class SchedulerParams:
    donor_gap: float = 0.15
    plateau_k: int = 2
    diversity_decay: float = 0.7
    infeasible_bonus: float = 1e9
    evolve_max_attempts: int = 2
    crossover_max_attempts: int = 2
    new_species_prize: float = 0.5
    abs_prize_floor: float = 1.0
    feas_repair_giveup: int = 3
    n_instances: int = 30
    full_feasible: int = 30
    donor_min_feasible: int = 28
    # selection (limits.yaml `selection:`)
    leader_bias: float = 1.5
    explore_floor: float = 1.5
    exhausted_weight: float = 1.0
    # targeting
    abs_base: float = 0.10
    # ladder
    wildcard_cap: int = 2
    max_species: int = 6
    new_species_cooldown: int = 3
    hopeless_attempts: int = 4
    # crossover
    exhausted_any_donor: bool = True
    instance_xover_min_wins: int = 3
    instance_xover_margin: float = 0.02
    instance_xover_cap: int = 2

    @classmethod
    def from_problem(cls, problem) -> "SchedulerParams":
        s = dict(problem.config.scheduler)
        lim = problem.config.limits
        sel, tgt, lad, xo = (lim.get("selection", {}), lim.get("targeting", {}),
                             lim.get("ladder", {}), lim.get("crossover", {}))
        return cls(
            donor_gap=s.get("donor_gap", 0.15), plateau_k=s.get("plateau_k", 2),
            diversity_decay=s.get("diversity_decay", sel.get("diversity_decay", 0.7)),
            infeasible_bonus=s.get("infeasible_bonus", 1e9),
            evolve_max_attempts=s.get("evolve_max_attempts", 2),
            crossover_max_attempts=s.get("crossover_max_attempts", 2),
            new_species_prize=s.get("new_species_prize", 0.5), abs_prize_floor=s.get("abs_prize_floor", 1.0),
            feas_repair_giveup=s.get("feas_repair_giveup", 3),
            n_instances=problem.config.n_instances, full_feasible=problem.config.full_feasible,
            donor_min_feasible=problem.config.donor_min_feasible,
            leader_bias=float(sel.get("leader_bias", 1.5)), explore_floor=float(sel.get("explore_floor", 1.5)),
            exhausted_weight=float(sel.get("exhausted_weight", 1.0)),
            abs_base=float(tgt.get("abs_base", 0.10)),
            wildcard_cap=int(lad.get("wildcard_cap", 2)), max_species=int(lad.get("max_species", 6)),
            new_species_cooldown=int(lad.get("new_species_cooldown", 3)),
            hopeless_attempts=int(lad.get("hopeless_attempts", 4)),
            exhausted_any_donor=bool(xo.get("exhausted_any_donor", True)),
            instance_xover_min_wins=int(xo.get("instance_xover_min_wins", 3)),
            instance_xover_margin=float(xo.get("instance_xover_margin", 0.02)),
            instance_xover_cap=int(xo.get("instance_xover_cap", 2)),
        )


@dataclass
class Decision:
    species: str
    operation: str                      # "evolve" | "crossover" | "new_species"
    target_component: Optional[str] = None
    reason: str = ""
    donor: Optional[str] = None
    splice_gene: Optional[str] = None   # lever unit for evolve ("wildcard" = free-form); splice unit for crossover
    splice_phase: Optional[str] = None  # human phase text (or phase id when phases are declared)
    prize: float = 0.0
    exhausted: bool = False             # every lever + crossover spent; deprioritise in select_next
    cull_target: Optional[str] = None   # new_species at cap / hopeless: species to retire first
    instance_wins: Optional[list] = None  # instance-targeted crossover: the donor's per-instance wins

    def to_dict(self) -> dict:
        return asdict(self)


WILDCARD = "wildcard"

# --- STATUS TAXONOMY ---------------------------------------------------------------------
# Only a REAL attempt -- the agent produced a distinct candidate that was judged -- may consume
# a lever. Infrastructure failures, session limits, spawns and agent crashes are journaled but
# never exhaust anything (18 verdicts poisoned by licence-token starvation once retired levers
# on every species).
NON_ATTEMPT_STATUSES = frozenset({
    "no_population", "rate_limited", "agent_failed", "unreachable", "deploy_failed",
    "eval_failed", "ideator_failed", "new_species", "new_species_failed", "gate_only", "culled",
})
# A deliberate no-op -- ran to completion, exited 0, program unchanged -- is an evidence-based
# "this cannot be improved": ONE retires the lever.
DELIBERATE_NOOP_STATUS = "agent_noop"


# --- population helpers --------------------------------------------------------------------

def per_component_best(population, components, params) -> dict[str, tuple[str, float]]:
    """For each component, the best DONOR species and its per-feasible-instance AVERAGE cost."""
    best: dict[str, tuple[str, float]] = {}
    for comp in components:
        contenders = []
        for name, diag in population.items():
            fc = diag.get("feasible_count", 0)
            if fc >= params.donor_min_feasible and fc > 0 and comp in diag.get("component_totals", {}):
                contenders.append((name, diag["component_totals"][comp] / fc))
        if contenders:
            best[comp] = min(contenders, key=lambda x: x[1])
    return best


def _recent_ops(history, species, window: int = 6) -> int:
    return sum(1 for h in history[-window:] if h.get("species") == species)


def _species_window(history, species) -> list[dict]:
    """History AFTER this species' last PROMOTION: a promotion rewrites the program, so older
    evidence is stale (levers were found held shut by failures six versions old)."""
    idx = -1
    for i, h in enumerate(history):
        if h.get("species") == species and h.get("promoted"):
            idx = i
    return history[idx + 1:]


def _is_real(h: dict) -> bool:
    return h.get("status") not in NON_ATTEMPT_STATUSES and h.get("rate_limited") is not True


def _failed_ops(history, species, component, operation, unit: str | None = None) -> int:
    """Non-promoted REAL attempts of `operation` on (species, component[, lever unit]) since
    the species' last promotion."""
    return sum(1 for h in _species_window(history, species)
               if h.get("species") == species and h.get("operation") == operation
               and h.get("target_component") == component
               and (unit is None or h.get("splice_gene") == unit)
               and not h.get("promoted") and _is_real(h))


def _deliberate_noop(history, species, component, operation, unit: str | None = None) -> bool:
    return any(h.get("species") == species and h.get("operation") == operation
               and h.get("target_component") == component
               and (unit is None or h.get("splice_gene") == unit)
               and h.get("status") == DELIBERATE_NOOP_STATUS
               for h in _species_window(history, species))


def _real_attempts(history, species) -> int:
    return sum(1 for h in history if h.get("species") == species and h.get("status") == "evaluated")


def _ever_promoted(history, species) -> bool:
    return any(h.get("species") == species and h.get("promoted") for h in history)


# --- levers ---------------------------------------------------------------------------------

def _levers(problem, component) -> list[tuple[str, str]]:
    """ORDERED (unit, phase) levers that can move `component`: problem.yaml `levers:` when
    declared, else the single `splice:` owner. Several genome units influence one outcome; a
    1:1 component->unit map leaves the others permanently unreachable."""
    lv = problem.levers_for(component) if hasattr(problem, "levers_for") else None
    if lv:
        return [(u.unit, u.phase) for u in lv]
    su = problem.splice_for(component)
    return [(su.unit, su.phase)] if su else []


def _lever_dead(history, species, component, unit, params) -> bool:
    return (_deliberate_noop(history, species, component, "evolve", unit)
            or _failed_ops(history, species, component, "evolve", unit) >= params.evolve_max_attempts)


def _evolve_exhausted(history, species, component, problem, params) -> bool:
    levers = _levers(problem, component)
    if not levers:
        return (_deliberate_noop(history, species, component, "evolve")
                or _failed_ops(history, species, component, "evolve") >= params.evolve_max_attempts)
    return all(_lever_dead(history, species, component, u, params) for u, _ in levers)


def _select_lever(history, species, component, problem, params) -> tuple[Optional[str], Optional[str]]:
    levers = _levers(problem, component)
    for unit, phase in levers:
        if not _lever_dead(history, species, component, unit, params):
            return unit, phase
    return levers[-1] if levers else (None, None)


def _crossover_capped(history, species, component, params) -> bool:
    return (_deliberate_noop(history, species, component, "crossover")
            or _failed_ops(history, species, component, "crossover") >= params.crossover_max_attempts)


def _wildcard_attempts(history, species) -> int:
    return sum(1 for h in _species_window(history, species)
               if h.get("species") == species and h.get("operation") == "evolve"
               and h.get("splice_gene") == WILDCARD and not h.get("promoted") and _is_real(h))


def _recent_new_species(history, k) -> bool:
    return any(h.get("operation") == "new_species" for h in history[-k:])


def _feasibility_given_up(history, species, params) -> bool:
    ops = [h for h in history if h.get("species") == species]
    if len(ops) < params.feas_repair_giveup:
        return False

    def failed(h):
        if h.get("operation") != "evolve" or h.get("target_component") != "feasibility":
            return False
        fc = (h.get("after") or {}).get("feasible")
        return not (h.get("promoted") and fc == params.full_feasible)

    return all(failed(h) for h in ops[-params.feas_repair_giveup:])


# --- targeting: prior x affinity x (abs_base + gap) ------------------------------------------

def _prior(problem, component) -> float:
    pri = getattr(problem.config, "component_priors", {}) or {}
    return float(pri.get(component, 1.0))


def _affinity(history, species, component) -> float:
    """Online evidence: when this species targeted this component, did its TOTAL cost go
    down (win) or up (loss)? Laplace-smoothed, neutral (1.0) with no history."""
    wins = losses = 0
    for h in history:
        if h.get("species") != species or h.get("target_component") != component:
            continue
        b, a = (h.get("before") or {}).get("total"), (h.get("after") or {}).get("total")
        if b is None or a is None:
            continue
        if a < b:
            wins += 1
        elif a > b:
            losses += 1
    return (1.0 + wins) / (1.0 + losses)


# --- population ranking (for selection + hopeless + cull) -----------------------------------

def _ranks(population, problem) -> dict[str, int]:
    """1 = best under the problem's fitness metric (rank_sum / normalized_cost)."""
    try:
        from .fitness import score_population
        vec = {n: [(d.get("per_instance", {}).get(s, {}).get("total")
                    if d.get("per_instance", {}).get(s, {}).get("feasible") else None)
                   for s in problem.instances] for n, d in population.items()}
        rows = score_population(vec, problem.config.fitness_metric, problem.config.fitness_opts)
        return {r["name"]: r["rank"] for r in rows}
    except Exception:
        return {n: i + 1 for i, n in enumerate(population)}


def _worst(population, problem, exclude: str | None = None) -> Optional[str]:
    ranks = _ranks(population, problem)
    cands = [(r, n) for n, r in ranks.items() if n != exclude]
    return max(cands)[1] if cands else None


# --- instance-targeted crossover -------------------------------------------------------------

def _family(species, problem) -> str:
    """The allele of the problem's family key gene (default: first species-defining gene),
    read from the species' saved genome.json; '' when unavailable (disables family gating)."""
    try:
        key = getattr(problem.config, "crossover_family_key", None)
        if not key:
            from .genome import catalog_species_defining
            sd = catalog_species_defining(problem.config.genome_spec)
            key = sd[0] if sd else None
        if not key:
            return ""
        g = json.loads((problem.species_dir / species / "genome.json").read_text(encoding="utf-8"))
        genes = g.get("genes", g)
        v = genes.get(key, "")
        return v.get("allele", "") if isinstance(v, dict) else (v if isinstance(v, str) else "")
    except Exception:
        return ""


def instance_wins(target: dict, donor: dict, instances, margin: float) -> list[dict]:
    """Instances where the donor is feasible and strictly cheaper than the target by more than
    `margin` (relative) -- or feasible where the target is not."""
    out = []
    tp, dp = target.get("per_instance", {}), donor.get("per_instance", {})
    for inst in instances:
        t, d = tp.get(inst, {}), dp.get(inst, {})
        if not d.get("feasible"):
            continue
        if not t.get("feasible"):
            out.append({"inst": inst, "target": None, "donor": d.get("total"), "gap": None})
            continue
        tc, dc = float(t.get("total", 0.0)), float(d.get("total", 0.0))
        if tc > 0 and dc < tc * (1.0 - margin):
            out.append({"inst": inst, "target": tc, "donor": dc, "gap": round((tc - dc) / tc, 4)})
    out.sort(key=lambda w: -(w["gap"] if w["gap"] is not None else 1.0))
    return out


def _instance_xover_attempts(history, species, donor) -> int:
    return sum(1 for h in _species_window(history, species)
               if h.get("species") == species and h.get("operation") == "crossover"
               and h.get("donor") == donor and h.get("instance_wins") is not None
               and not h.get("promoted") and _is_real(h))


def _instance_crossover(species, population, history, problem, params) -> Optional[Decision]:
    """A donor behind on every AGGREGATE can still hold instance wins the target lacks, which
    component averages never surface. Same-family donors only (same-family grafts promoted
    5/13 vs 0/3 cross-family in the campaigns this was distilled from)."""
    diag = population[species]
    if diag.get("feasible_count", 0) < params.full_feasible:
        return None
    fam = _family(species, problem)
    best = None
    for donor, ddiag in population.items():
        if donor == species or ddiag.get("feasible_count", 0) < params.donor_min_feasible:
            continue
        if fam and _family(donor, problem) != fam:
            continue
        wins = instance_wins(diag, ddiag, problem.instances, params.instance_xover_margin)
        if len(wins) < params.instance_xover_min_wins:
            continue
        if _instance_xover_attempts(history, species, donor) >= params.instance_xover_cap:
            continue
        score = sum((w["gap"] if w["gap"] is not None else 1.0) for w in wins)
        if best is None or score > best[1]:
            best = (donor, score, wins)
    if best is None:
        return None
    donor, score, wins = best
    # splice the phase owning the component where the donor is furthest ahead on those instances
    comp_gain: dict[str, float] = {}
    for w in wins:
        tpi, dpi = diag["per_instance"].get(w["inst"], {}), population[donor]["per_instance"].get(w["inst"], {})
        for c in problem.components:
            tc = float((tpi.get("components") or {}).get(c, 0.0))
            dc = float((dpi.get("components") or {}).get(c, 0.0))
            comp_gain[c] = comp_gain.get(c, 0.0) + max(0.0, tc - dc)
    comp = max(comp_gain, key=comp_gain.get) if comp_gain else (problem.components[0] if problem.components else None)
    su = problem.splice_for(comp) if comp else None
    insts = ", ".join(w["inst"] for w in wins[:6])
    return Decision(
        species=species, operation="crossover", target_component=comp, donor=donor,
        splice_gene=su.unit if su else None, splice_phase=su.phase if su else None,
        instance_wins=wins,
        reason=(f"same-family donor '{donor}' is strictly better on {len(wins)} instance(s) "
                f"({insts}) -> targeted graft of the phase owning '{comp}'"),
        prize=float(len(wins)),
    )


# --- the policy -----------------------------------------------------------------------------

def decide_operation(species, population, history, problem, params=None) -> Decision:
    params = params or SchedulerParams.from_problem(problem)
    components = problem.components
    diag = population[species]

    # 0. feasibility first (with the give-up valve)
    feas = diag.get("feasible_count", 0)
    if feas < params.full_feasible and not _feasibility_given_up(history, species, params):
        return Decision(species=species, operation="evolve", target_component="feasibility",
                        reason=f"infeasible on {params.full_feasible - feas} instance(s); repair first",
                        prize=params.infeasible_bonus + (params.full_feasible - feas))

    # 0b. HOPELESS: worst-ranked, never promoted, enough real attempts => replace the design
    n_species = sum(1 for s in population if not s.startswith(("_", ".")))
    if (n_species > 1 and not _ever_promoted(history, species)
            and _real_attempts(history, species) >= params.hopeless_attempts
            and _worst(population, problem) == species
            and not _recent_new_species(history, params.new_species_cooldown)):
        return Decision(species=species, operation="new_species", cull_target=species,
                        reason=(f"'{species}' is the worst design after {_real_attempts(history, species)} "
                                f"real attempts and has never been promoted -> replace it (self-cull)"),
                        prize=params.new_species_prize)

    # 0c. instance-targeted crossover beats the component policy when available
    ix = _instance_crossover(species, population, history, problem, params)
    if ix is not None:
        return ix

    comp_costs = diag["component_totals"]
    feas_s = feas or params.full_feasible
    best = per_component_best(population, components, params)

    cands: dict[str, dict] = {}
    for comp in components:
        s_avg = comp_costs.get(comp, 0) / feas_s
        donor, donor_avg = best.get(comp, (None, None))
        has_donor = donor is not None and donor != species and donor_avg is not None and s_avg > donor_avg
        if not has_donor:
            donor, gap_rel = None, 0.0
        elif donor_avg > 0:
            gap_rel = (s_avg - donor_avg) / donor_avg
        else:
            gap_rel = float("inf")
        ev_done = _evolve_exhausted(history, species, comp, problem, params)
        xo_done = _crossover_capped(history, species, comp, params)
        donor_ok = has_donor and (params.exhausted_any_donor or gap_rel >= params.donor_gap)
        if ev_done and (not donor_ok or xo_done):
            continue  # fully exhausted component
        attractiveness = _prior(problem, comp) * _affinity(history, species, comp) * (
            params.abs_base + (gap_rel if gap_rel != float("inf") else 10.0))
        cands[comp] = {"attr": attractiveness, "donor": donor, "gap_rel": gap_rel,
                       "ev_done": ev_done, "xo_done": xo_done, "donor_ok": donor_ok}

    if not cands:
        weak = max(components, key=lambda c: comp_costs.get(c, 0.0) * _prior(problem, c), default=None)
        # WILDCARD rung: free-form shots before spending a spawn
        if _wildcard_attempts(history, species) < params.wildcard_cap:
            n = _wildcard_attempts(history, species) + 1
            return Decision(species=species, operation="evolve", target_component=weak,
                            splice_gene=WILDCARD,
                            splice_phase="free-form: any phase, any unit -- invent if the listed ones are spent",
                            reason=f"every lever on '{species}' is spent -> wildcard evolve ({n}/{params.wildcard_cap})",
                            prize=0.6)
        if not _recent_new_species(history, params.new_species_cooldown):
            cull = None
            if n_species >= params.max_species:
                cull = _worst(population, problem, exclude=species)
            if n_species < params.max_species or cull is not None:
                return Decision(species=species, operation="new_species", target_component=weak,
                                cull_target=cull,
                                reason=(f"'{species}' exhausted every lever, wildcard and crossover -> "
                                        f"spawn a new species (target its weak '{weak}')"
                                        + (f", culling '{cull}'" if cull else "")),
                                prize=params.new_species_prize)
        unit, phase = _select_lever(history, species, weak, problem, params) if weak else (None, None)
        return Decision(species=species, operation="evolve", target_component=weak,
                        splice_gene=unit, splice_phase=phase,
                        reason=f"all exhausted (pool at cap / spawn cooldown); low-priority re-push of '{weak}'",
                        prize=0.2, exhausted=True)

    bottleneck = max(cands, key=lambda c: cands[c]["attr"])
    c = cands[bottleneck]
    if c["ev_done"] and c["donor_ok"] and not c["xo_done"]:
        su = problem.splice_for(bottleneck)
        gap_txt = "much" if c["gap_rel"] == float("inf") else f"{c['gap_rel']*100:.0f}%"
        return Decision(species=species, operation="crossover", target_component=bottleneck,
                        donor=c["donor"], splice_gene=su.unit if su else None,
                        splice_phase=su.phase if su else None,
                        reason=(f"'{bottleneck}': every lever spent and '{c['donor']}' is {gap_txt} "
                                f"cheaper -> structural; splice its {su.phase if su else 'owning phase'}"),
                        prize=c["attr"])
    unit, phase = _select_lever(history, species, bottleneck, problem, params)
    return Decision(species=species, operation="evolve", target_component=bottleneck, donor=c["donor"],
                    splice_gene=unit, splice_phase=phase,
                    reason=(f"bottleneck '{bottleneck}' (attractiveness {c['attr']:.2f}) via lever "
                            f"'{unit}'; " + ("close the gap to '%s'" % c["donor"] if c["donor"]
                                             else "push for absolute gains (already best)")),
                    prize=c["attr"])


def select_next(population, history, problem, params=None) -> Decision:
    """Island selection: feasibility repairs dominate; otherwise weight species by their
    population rank (leader bias, exploration floor, exhausted cap) x diversity decay."""
    params = params or SchedulerParams.from_problem(problem)
    decisions = [decide_operation(s, population, history, problem, params) for s in population]
    ranks = _ranks(population, problem)
    n = max(1, len(population))

    def value(d: Decision) -> float:
        if d.prize >= params.infeasible_bonus:
            w = d.prize
        else:
            w = max(params.leader_bias ** (n - ranks.get(d.species, n)), params.explore_floor)
            if d.exhausted:
                w = min(w, params.exhausted_weight)
        return w * (params.diversity_decay ** _recent_ops(history, d.species))

    return max(decisions, key=value)


def all_decisions(population, history, problem, params=None):
    params = params or SchedulerParams.from_problem(problem)
    return [decide_operation(s, population, history, problem, params) for s in population]


# --- IO -------------------------------------------------------------------------------------

def load_population(problem) -> dict[str, dict]:
    pop: dict[str, dict] = {}
    root = problem.species_dir
    if not root.exists():
        return pop
    for diag_path in sorted(root.glob("*/diagnostics.json")):
        name = diag_path.parent.name
        if name.startswith("_") or name.startswith("."):
            continue
        d = json.loads(diag_path.read_text(encoding="utf-8"))
        d["species"] = name
        pop[name] = d
    return pop


def load_history(problem) -> list[dict]:
    path = problem.evolution_log
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []
