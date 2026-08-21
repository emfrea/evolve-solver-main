"""Prompt assembly -- the ONE place that renders agent prompts. Every problem fact
(docs, components, splice phrasing, solver CLI, libraries, gate list) is derived from the
Problem object only, so no problem literal leaks into the framework. Genome rendering
branches on genome style in exactly one function (genome_block).
"""
from __future__ import annotations

import textwrap

from ..genome import StructuredGenome, FreeformGenome
from .. import phases as _phases


def problem_docs(problem) -> str:
    parts = [f"# === {fn} ===\n\n{txt}" for fn, txt in problem.config.docs.items()]
    return "\n\n".join(parts) if parts else "(no problem docs provided)"


def solver_contract_line(problem) -> str:
    """Human-readable CLI line derived from the problem's solver.cli template."""
    return problem.config.solver_cli.format(
        python="python", solve=problem.config.solver_entry, instance="<instance>",
        output="<output>", seed="S", time_limit="T", threads="N", progress_log="P")


def components_line(problem) -> str:
    return ", ".join(problem.components)


def genome_block(genome, problem) -> str:
    """Render a genome for prompts. Branches on style; the ONLY style-aware renderer."""
    if isinstance(genome, StructuredGenome) or problem.config.genome_style == "structured":
        cat = problem.config.genome_spec.get("catalog", {})
        defining = {c for c, v in cat.items() if v.get("species_defining")}
        lines = []
        genes = genome.genes if hasattr(genome, "genes") else {}
        for c, g in genes.items():
            tag = "[SPECIES-DEFINING]" if c in defining else "[tunable]"
            role = cat.get(c, {}).get("role", "")
            desc = cat.get(c, {}).get("alleles", {}).get(g.allele, "")
            lines.append(f"  {tag} {c} = {g.allele}")
            if role:
                lines.append(f"    Role: {role}")
            if desc:
                lines.append(f"    Allele: {desc}")
            if g.notes:
                lines.append(f"    Notes: {g.notes}")
            if g.params:
                lines.append(f"    Starting params: {g.params}")
        return "\n".join(lines)
    # freeform
    g = genome
    out = [f"  name: {g.name}", f"  summary: {g.summary}"]
    for ph, txt in (g.phases or {}).items():
        out.append(f"  phase[{ph}]: {txt}")
    if g.notes:
        out.append(f"  notes: {g.notes}")
    return "\n".join(out)


_HOW_TO_WORK = """
    ═══════════════ HOW TO WORK ═══════════════
    - The workspace already has: your solver ({entry}), evaluator.py, check.py, and the gate
      instances in instances/ ({gate}).
    - TEST SHORT ({t}s): run   python check.py instances/{first_gate}.{ext}
      It runs your solver on the instance and prints feasibility + total cost + the per-component
      breakdown ({components}). Iterate: fix crashes / infeasibility, then improve the target.
    - The solver MUST honour this CLI EXACTLY:
        {cli}
    - Self-contained: use only {libraries}.
    - Leave the final working {entry} in this directory.

    STRICT SCOPE (do not violate): your ONLY job is to write/edit and test `{entry}` in THIS
    directory. The ONLY commands you may run are `python check.py ...` and `python {entry} ...`.
    NEVER run any repository or pipeline script (run_evolution_step.py, build_and_eval.py,
    campaign_tick.py, seed_species.py, scripts/*), NEVER `cd` outside this directory, and NEVER
    invoke the campaign/orchestrator or another `claude` agent. Doing so spawns runaway nested
    processes.
"""


def _how_to_work(problem, self_test_time) -> str:
    first_gate = problem.gate[0] if problem.gate else problem.instances[0]
    return _HOW_TO_WORK.format(
        entry=problem.config.solver_entry, gate=", ".join(problem.gate), t=self_test_time,
        first_gate=first_gate, ext=problem.config.inst_ext, components=components_line(problem),
        cli=solver_contract_line(problem), libraries=problem.config.libraries)


def build_prompt(name, genome, problem, self_test_time) -> str:
    return textwrap.dedent(f"""
    Build a brand-new solver from scratch in THIS directory, implementing the algorithm
    described by the genome below. You are an autonomous agent: write {problem.config.solver_entry},
    RUN it locally to test it (via check.py), read the result, fix problems, and iterate until
    the gate instances are FEASIBLE (0 hard violations) or you have made your best effort.

    ═══════════════ PROBLEM ═══════════════
    {problem_docs(problem)}

    ═══════════════ GENOME TO IMPLEMENT (species: {name}) ═══════════════
    {genome_block(genome, problem)}

    The species-defining part fixes the algorithm; honour it. Prioritise FEASIBILITY first,
    then reduce the soft-cost components ({components_line(problem)}).
    {phase_contract(problem)}
    {_how_to_work(problem, self_test_time)}
    """).strip()


def phase_contract(problem) -> str:
    """Marker-delimited phase contract (when the problem declares `phases:`)."""
    if not problem.phases:
        return ""
    return ("═══════════════ PHASE STRUCTURE (required -- crossover splices these) ═══════════════\n"
            + _phases.contract_block(problem.phases))


def evolve_prompt(species, genome, decision, diag, donor_diag, problem, self_test_time) -> str:
    target = decision.target_component
    if target == "feasibility":
        focus = textwrap.dedent(f"""
        GOAL: FIX FEASIBILITY. The solver is INFEASIBLE on some instances ({decision.reason}).
        Find why (run check.py, read the violations) and harden construction/repair so the solver
        is feasible (0 violations) on ALL gate instances. Do not sacrifice feasibility where it holds.""")
    elif getattr(decision, "splice_gene", None) == "wildcard":
        focus = textwrap.dedent(f"""
        GOAL: FREE-FORM IMPROVEMENT (wildcard). Every prescribed lever on this species has been tried
        and failed against the current champion -- so this step has NO prescribed lever. You have
        full latitude over ANY phase and ANY tunable unit, including INVENTING an approach the
        catalog does not list, as long as the species' defining identity stays intact, feasibility
        never regresses, and no component gets much worse. Focus suggestion (weakest component):
        '{target}'. Make ONE substantial, coherent change -- not a scatter of tweaks.""")
    else:
        donor = decision.donor
        donor_val = (donor_diag or {}).get("component_totals", {}).get(target) if donor_diag else None
        mine = diag.get("component_totals", {}).get(target)
        mine_s = f"{mine:,.0f}" if mine is not None else "n/a"
        best_line = (f"The pool's best ('{donor}') achieves {target} = {donor_val:,.0f} -- aim toward that."
                     if (donor and donor_val is not None) else "")
        su = problem.splice_for(target)
        unit = getattr(decision, "splice_gene", None) or (su.unit if su else "the owning unit")
        phase = getattr(decision, "splice_phase", None) or (su.phase if su else "the relevant phase")
        focus = textwrap.dedent(f"""
        GOAL: REDUCE the soft-cost component '{target}'.
        Current: {species} has {target} = {mine_s} (summed over instances). {best_line}
        LEVER ('{unit}'): {phase}. This is the chosen route to move '{target}' on this iteration --
        improve THAT part of the solver WITHOUT regressing feasibility or making the other
        components much worse. Keep the algorithm (genome) the same -- this is tuning of one
        lever, not a redesign.""")
    return textwrap.dedent(f"""
    You are improving an existing solver (species: {species}) IN PLACE. The current working solver
    is {problem.config.solver_entry} in this directory. Modify it.

    {focus}

    GENOME (keep this algorithm):
    {genome_block(genome, problem)}
    {phase_contract(problem)}

    ═══════════════ PROBLEM ═══════════════
    {problem_docs(problem)}
    {_how_to_work(problem, self_test_time)}
    """).strip()


def crossover_prompt(target_species, genome, decision, target_diag, donor_diag, problem, self_test_time) -> str:
    donor = decision.donor
    comp = decision.target_component
    su = problem.splice_for(comp)
    phase = decision.splice_phase or (su.phase if su else "the relevant phase")
    mine = target_diag.get("component_totals", {}).get(comp)
    theirs = (donor_diag or {}).get("component_totals", {}).get(comp) if donor_diag else None
    mine_s = f"{mine:,.0f}" if mine is not None else "n/a"
    theirs_s = f"{theirs:,.0f}" if theirs is not None else "n/a"
    ph = _phases.phase_for_component(problem.phases, comp) if problem.phases else None
    marker_block = ""
    if ph:
        m0, m1 = _phases.marker_start(ph["id"], ph["name"]), _phases.marker_end(ph["id"], ph["name"])
        marker_block = textwrap.dedent(f"""
    ═══════════════ THE PHASE TO GRAFT -- {_phases.phase_label(ph)} ═══════════════
    {ph.get('responsibility', '')}
    boundary -- {ph.get('boundary', '')}
    In each file the phase is delimited EXACTLY by these marker lines:
        {m0}
        ... phase body ...
        {m1}
    Replace the TARGET's block (between its markers) with the DONOR's block (between the donor's
    matching markers), reconcile the boundary hand-off, keep every OTHER phase of the target
    untouched, and keep the markers in the result. If a file is not yet marker-delimited, first
    locate the section performing the responsibility above, wrap it in the markers, then splice.""")
    wins_block = ""
    wins = getattr(decision, "instance_wins", None)
    if wins:
        rows = "\n".join(
            f"      {w['inst']}: donor {w['donor']:,.2f} vs target "
            + (f"{w['target']:,.2f} (-{w['gap']*100:.1f}%)" if w.get('target') is not None else "INFEASIBLE")
            for w in wins)
        wins_block = textwrap.dedent(f"""
    ═══════════════ WHY THIS SPLICE -- THE DONOR'S PER-INSTANCE WINS ═══════════════
    On these instances the donor's solutions BEAT the target's -- capturing them is the entire
    purpose of the graft. SUCCESS = the hybrid matches the donor there WITHOUT regressing any
    instance where the target is currently better:
{rows}""")
    return textwrap.dedent(f"""
    You are performing a CROSSOVER between two solvers via code splice.

    TARGET species '{target_species}': its solver is {problem.config.solver_entry} in this directory
      (modify this one). It is weak on '{comp}' ({comp} = {mine_s}).
    DONOR species '{donor}': its solver is donor_solve.py (read-only reference). It is the pool's best
      on '{comp}' ({comp} = {theirs_s}) via its {phase}.
    {marker_block}
    {wins_block}

    GOAL: graft the DONOR's {phase} into the TARGET's solver so the target's '{comp}' improves toward
    the donor's level, WITHOUT breaking the target's feasibility or its strong components. Copy the
    donor's implementation of that phase and reconcile the data hand-off at the phase boundary. Keep
    the TARGET's species-defining algorithm; only the {phase} comes from the donor.

    GENOME of the target (preserve its identity):
    {genome_block(genome, problem)}

    ═══════════════ PROBLEM ═══════════════
    {problem_docs(problem)}
    {_how_to_work(problem, self_test_time)}
    """).strip()


# --- ideator (new species) ---

def existing_species_summary(names) -> str:
    if not names:
        return "(none yet -- you are inventing the first.)"
    return "\n".join(f"  - {n}" for n in sorted(names))


def new_species_prompt(problem, existing_names) -> str:
    style = problem.config.genome_style
    if style == "structured":
        cat = problem.config.genome_spec.get("catalog", {})
        catalog_txt = "\n".join(
            f"  {c} [{'species-defining' if v.get('species_defining') else 'tunable'}]: "
            + ", ".join(v.get("alleles", {}).keys())
            for c, v in cat.items())
        schema = ('{"name","mode":"new_species","rationale","genes":{<category>:{"allele","notes"}},'
                  '"expected_strengths":[...],"expected_weaknesses":[...],"complexity":"S|M|L"}')
        genome_guidance = (f"Fill EVERY gene category from the catalog below with an allele "
                           f"(you may invent new alleles).\n\nGENE CATALOG:\n{catalog_txt}")
    else:
        schema = ('{"name","mode":"new_species","summary","phases":{<phase>:<text>},"rationale",'
                  '"expected_strengths":[...],"expected_weaknesses":[...],"complexity":"S|M|L"}')
        genome_guidance = ("Invent ANY algorithm. Describe it free-form: a summary plus, for each of the "
                           f"problem's phases ({', '.join(problem.config.splice) or 'the natural subproblems'}), "
                           "how you handle it.")
    return textwrap.dedent(f"""
    Invent a NEW solver algorithm for the problem below, ALGORITHMICALLY DISTINCT from the existing
    species. Aim to be strong on at least some of the cost components ({components_line(problem)}) and
    feasibility-first.

    ═══════════════ PROBLEM ═══════════════
    {problem_docs(problem)}

    EXISTING SPECIES (invent something DISTINCT):
    {existing_species_summary(existing_names)}

    {genome_guidance}

    Respond with ONLY valid JSON matching:
    {schema}
    """).strip()


# --- workspace check.py (rendered per problem, copied into agent workspaces) ---

def render_check_script(problem, self_test_time: int = 5) -> str:
    """A tiny harness the agent runs: `python check.py instances/<inst>.<ext>` -> runs the
    solver on that instance and prints feasibility + cost + components. Problem-agnostic:
    the solver command comes from the problem's CLI template, validation from evaluator.py."""
    cli = problem.config.solver_cli
    return textwrap.dedent(f'''\
        """Self-test harness (auto-generated). Usage: python check.py <instance_file>"""
        import json, re, shlex, subprocess, sys
        from pathlib import Path
        from evaluator import validate

        TEMPLATE = {cli!r}
        ENTRY = {problem.config.solver_entry!r}
        TIME_LIMIT = {self_test_time}

        def build_cmd(instance, output):
            subs = dict(python=sys.executable, solve=ENTRY, instance=instance, output=output,
                        seed=42, time_limit=TIME_LIMIT, threads=1, progress_log="")
            s = re.sub(r"\\[[^\\]]*\\]", "", TEMPLATE)   # drop optional [...] for the self-test
            return shlex.split(s.format(**subs), posix=False)

        def main():
            inst = sys.argv[1]
            out = "_check_out." + Path(inst).suffix.lstrip(".")
            p = subprocess.run(build_cmd(inst, out), capture_output=True, text=True)
            if p.returncode != 0:
                print("SOLVER FAILED rc=%d\\n%s" % (p.returncode, (p.stderr or p.stdout)[:800])); return
            res = validate(inst, out)
            print(json.dumps(res, indent=2))
        main()
        ''')
