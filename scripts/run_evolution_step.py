"""One autonomous evolution step (loopable) -- the campaign entrypoint.

  select_next -> agent evolve/crossover/new_species -> local gate -> backend eval
  -> diagnose -> promote-or-reject (pluggable fitness) -> append evolution_log.json

Problem-agnostic: pass --problem <name>. The operational layer (durable auth, resilient
loop, genuine-vs-transient rate-limit backoff, re-entrancy guard, windowless subprocesses,
budget-preserving reachability wait, per-step productive-time timing, promote-from-captured-
bytes, lock-proof workspaces) is preserved from the original gene-search campaign, plus:

  * NOISE GUARD  -- a candidate that would be promoted against the champion's CACHED
                    diagnostics triggers a fresh re-evaluation of the champion first;
  * RATCHET      -- the bar is the better of (cached, fresh) and the stored baseline only
                    ever moves up: promotions beat the champion's best demonstrated result,
                    never a lucky-candidate-vs-unlucky-cache dice swap;
  * ENV COLLAPSE -- a fresh champion eval far worse than its cache means the environment is
                    at fault (licence tokens, node faults): the step is voided, not judged;
  * STATUS TAXONOMY -- deliberate agent no-ops (ran, exited 0, changed nothing) are real
                    attempts; agent crashes and infrastructure failures are journaled but
                    never consume a lever (see framework.scheduler.NON_ATTEMPT_STATUSES).
All caps/thresholds come from config/limits.yaml.

    python scripts/run_evolution_step.py --problem toy --loop 5
    python scripts/run_evolution_step.py --problem toy --skip-remote   # build + gate only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    """Load ROOT/.env (gitignored) so a detached campaign self-authenticates the claude CLI."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _silence_subprocess_windows() -> None:
    """On win32, patch subprocess.Popen to add CREATE_NO_WINDOW so detached child console apps
    (claude, solvers) don't flash windows."""
    if sys.platform != "win32":
        return
    import subprocess as _sp
    _orig = _sp.Popen.__init__

    def _init(self, *a, **k):
        k["creationflags"] = k.get("creationflags", 0) | 0x08000000
        return _orig(self, *a, **k)

    _sp.Popen.__init__ = _init


_load_dotenv()
_silence_subprocess_windows()
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from framework.problem import load_problem
from framework import diagnostics
from framework.scheduler import load_population, load_history, select_next, SchedulerParams
from framework.fitness import score_population
from framework.agents.evolver import execute_decision
from framework.agents.builder import build_species
from framework.agents.ideator import propose_new_species
from framework.compute import get_backend
from scripts.build_and_eval import local_gate

# --- productive-time accounting (clock starts AFTER the reachability wait) ---
_STEP_T0 = None
_STEP_START_ISO = None


def _begin_step_clock():
    global _STEP_T0, _STEP_START_ISO
    _STEP_T0 = time.time()
    _STEP_START_ISO = time.strftime("%Y-%m-%dT%H:%M:%S")


def _append_log(problem, record: dict) -> None:
    if _STEP_T0 is not None and "duration_s" not in record:
        record.setdefault("started_at", _STEP_START_ISO)
        record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        record["duration_s"] = round(time.time() - _STEP_T0, 1)
    log_path = problem.evolution_log
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    log.append(record)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")


def _results_vec(diag: dict, problem) -> list:
    pi = diag.get("per_instance", {})
    return [(pi.get(s, {}).get("total") if pi.get(s, {}).get("feasible") else None) for s in problem.instances]


def _champion_better(population, species, champ, cand, problem) -> bool:
    """Promote iff swapping champion->candidate improves this species' population score
    under the problem's fitness metric (rank_sum or normalized_cost)."""
    base = {n: _results_vec(d, problem) for n, d in population.items()}
    with_champ = dict(base); with_champ[species] = _results_vec(champ, problem)
    with_cand = dict(base);  with_cand[species] = _results_vec(cand, problem)
    m, opts = problem.config.fitness_metric, problem.config.fitness_opts
    rc = {r["name"]: r["score"] for r in score_population(with_champ, m, opts)}
    rk = {r["name"]: r["score"] for r in score_population(with_cand, m, opts)}
    return rk[species] < rc[species]


def _collapsed(cached: dict, fresh: dict, ratio: float) -> bool:
    """Environment-fault detector: the champion's OWN code re-evaluated fresh lost feasibility
    on an instance it had, or its summed cost over the commonly-feasible instances is worse
    than the cache by more than `ratio`. Same code, same seed -- a swing that large is the
    evaluation environment (licence-token starvation, node faults), not the solver."""
    cp, fp = cached.get("per_instance", {}), fresh.get("per_instance", {})
    common_c = common_f = 0.0
    for inst, e in cp.items():
        if not e.get("feasible"):
            continue
        f = fp.get(inst, {})
        if not f.get("feasible"):
            return True
        common_c += float(e.get("total", 0.0))
        common_f += float(f.get("total", 0.0))
    return common_c > 0 and common_f > common_c * (1.0 + ratio)


def _fresh_champion_eval(problem, backend, species, seed, time_limit, threads):
    """Re-evaluate the CURRENT champion under the same conditions as the candidate. Returns
    fresh diagnostics (not saved over the species) or None if the evaluation failed."""
    sp_dir = problem.species_dir / species
    rc_dir = sp_dir / "_champ_recheck"
    if rc_dir.exists():
        shutil.rmtree(rc_dir, ignore_errors=True)
    rc_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sp_dir / problem.config.solver_entry, rc_dir / problem.config.solver_entry)
    name = f"{species}__champ"
    if not backend.deploy(name, rc_dir):
        return None
    sols = backend.run(name, rc_dir, instances=problem.instances, seed=seed,
                       time_limit=time_limit, threads=threads)
    if sols is None:
        return None
    return diagnostics.build_diagnostics(rc_dir, problem, solutions_dir=sols, save=False)


def _target_improved(decision, champ, cand, problem) -> bool:
    t = decision.target_component
    if t == "feasibility":
        return cand.get("feasible_count", 0) > champ.get("feasible_count", 0)
    return (cand.get("component_totals", {}).get(t, 1e18)
            < champ.get("component_totals", {}).get(t, 1e18))


def _sweep_candidates(problem) -> None:
    for cand in list(problem.species_dir.glob("*/_candidate*")) + list(problem.species_dir.glob("*/_champ_recheck")):
        try:
            shutil.rmtree(cand)
        except OSError as exc:
            print(f"[step] note: could not remove stale {cand} ({exc}); ignoring.")


def _cull_species(problem, name: str, reason: str) -> None:
    """Retire a species (population cap reached / hopeless rule): move its directory to
    runs/<problem>/species_parked/ (nothing is deleted; a parked species can be restored)."""
    src = problem.species_dir / name
    if not src.exists():
        return
    parked = problem.run_root / "species_parked"
    parked.mkdir(parents=True, exist_ok=True)
    dst = parked / name
    n = 2
    while dst.exists():
        dst = parked / f"{name}__{n}"; n += 1
    shutil.move(str(src), str(dst))
    _append_log(problem, {"operation": "cull", "species": name, "status": "culled",
                          "promoted": False, "reason": reason, "parked_to": str(dst)})
    print(f"[cull] parked '{name}' -> {dst}")


def _run_new_species(problem, backend, model, self_test_time, gate_time, build_timeout,
                     seed, time_limit, threads, skip_remote, pop) -> dict:
    ctx = ("Existing species (invent something ALGORITHMICALLY DISTINCT, with different "
           f"strengths): {', '.join(sorted(pop))}")
    try:
        proposal = propose_new_species(problem, model=model, extra_context=ctx, existing_names=list(pop))
    except Exception as exc:
        rec = {"operation": "new_species", "species": None, "status": "ideator_failed", "promoted": False}
        _append_log(problem, rec); print(f"[new_species] ideator failed: {exc}"); return rec

    name = proposal.get("name", "newspecies")
    base, n = name, 2
    while (problem.species_dir / name).exists() or (problem.run_root / "proposals" / f"{name}.json").exists():
        name = f"{base}-{n}"; n += 1
    proposal["name"] = name
    (problem.run_root / "proposals").mkdir(parents=True, exist_ok=True)
    (problem.run_root / "proposals" / f"{name}.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    print(f"[new_species] invented '{name}'")

    build_species(name, problem, model=model, self_test_time=self_test_time, timeout=build_timeout)
    sp_dir = problem.species_dir / name
    solve_py = sp_dir / problem.config.solver_entry
    if not solve_py.exists():
        shutil.rmtree(sp_dir, ignore_errors=True)
        rec = {"operation": "new_species", "species": name, "status": "no_candidate", "promoted": False}
        _append_log(problem, rec); return rec

    gate = local_gate(solve_py, problem, gate_time)
    feas_n = sum(1 for r in gate.values() if r.get("feasible"))
    print(f"    gate: {feas_n}/{len(problem.gate)} feasible")
    if feas_n == 0:
        shutil.rmtree(sp_dir, ignore_errors=True)
        (problem.run_root / "proposals" / f"{name}.json").unlink(missing_ok=True)
        rec = {"operation": "new_species", "species": name, "status": "gate_failed", "promoted": False}
        _append_log(problem, rec); return rec
    if skip_remote:
        return {"operation": "new_species", "species": name, "status": "gate_only"}

    if not backend.reachable():
        return {"operation": "new_species", "species": name, "status": "unreachable"}
    backend.deploy(name, sp_dir)
    sols = backend.run(name, sp_dir, instances=problem.instances, seed=seed,
                       time_limit=time_limit, threads=threads)
    if sols is None:
        rec = {"operation": "new_species", "species": name, "status": "eval_failed", "promoted": False}
        _append_log(problem, rec); return rec
    diag = diagnostics.build_diagnostics(sp_dir, problem, solutions_dir=sols)
    print(f"--- NEW species '{name}': feasible {diag['feasible_count']}/{problem.config.n_instances}  "
          f"total {diag['total_cost']:,.0f}")
    rec = {"operation": "new_species", "species": name, "target_component": None, "donor": None,
           "status": "evaluated", "improved": True, "promoted": True,
           "before": {"feasible": 0, "total": None, "component": None},
           "after": {"feasible": diag["feasible_count"], "total": diag["total_cost"], "component": None},
           "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _append_log(problem, rec)
    return rec


def run_step(problem, model, self_test_time, gate_time, build_timeout, seed, time_limit,
             threads, skip_remote) -> dict:
    _sweep_candidates(problem)
    pop = load_population(problem)
    history = load_history(problem)
    if not pop:
        print("[step] no population; build species first."); return {"status": "no_population"}
    decision = select_next(pop, history, problem)
    print(f"\n=== STEP: {decision.operation.upper()} {decision.species} "
          f"(target={decision.target_component}"
          + (f", donor={decision.donor}" if decision.donor else "") + ") ===")
    print(f"    {decision.reason}")

    backend = get_backend(problem)
    if not skip_remote:
        lim_a = problem.config.limits["agent"]
        max_wait, poll = int(lim_a["reachability_wait_s"]), int(lim_a["reachability_poll_s"])
        waited = 0
        while not backend.reachable():
            if waited >= max_wait:
                print(f"[step] compute unreachable for {max_wait}s; aborting step.")
                return {**decision.to_dict(), "status": "unreachable"}
            print(f"[step] compute unreachable; waiting {poll}s...")
            time.sleep(poll); waited += poll

    _begin_step_clock()

    if decision.operation == "new_species":
        if decision.cull_target:
            _cull_species(problem, decision.cull_target, decision.reason)
            pop = {k: v for k, v in pop.items() if k != decision.cull_target}
        return _run_new_species(problem, backend, model, self_test_time, gate_time, build_timeout,
                                seed, time_limit, threads, skip_remote, pop)

    champ = pop[decision.species]
    res = execute_decision(decision, pop, problem, model=model,
                           self_test_time=self_test_time, timeout=build_timeout)
    if res.get("rate_limited") and not res.get("solve_py_exists"):
        print("[step] genuine account/session limit AND no candidate -- backing off.")
        return {**decision.to_dict(), "status": "rate_limited"}
    cand_dir = Path(res["candidate_dir"])
    cand_solve = cand_dir / problem.config.solver_entry
    lim = problem.config.limits
    if not res["solve_py_exists"] or not res.get("candidate_changed", True):
        # The agent produced nothing new. A DELIBERATE no-op (clean exit after real work,
        # not rate-limited) is an evidence-based "cannot improve" and counts as an attempt;
        # anything else (instant CLI failure, crash, timeout) is an agent failure and must
        # not exhaust the lever.
        elapsed = float(res.get("agent_elapsed_s", 0.0))
        deliberate = (res.get("agent_returncode") == 0
                      and elapsed >= float(lim["agent"]["deliberate_noop_min_s"])
                      and not res.get("rate_limited"))
        status = "agent_noop" if deliberate else "agent_failed"
        rec = {**decision.to_dict(), "status": status, "improved": False, "promoted": False,
               "agent_returncode": res.get("agent_returncode"), "agent_elapsed_s": elapsed,
               "rate_limited": bool(res.get("rate_limited"))}
        _append_log(problem, rec)
        print(f"[step] agent produced no new candidate -> {status} "
              f"({'counts as a real attempt' if deliberate else 'not counted against the lever'}).")
        return rec
    cand_bytes = cand_solve.read_bytes()   # capture NOW (robust to a later workspace wipe)

    print(f"--- local gate ({gate_time}s) ---")
    gate = local_gate(cand_solve, problem, gate_time)
    feas_n = sum(1 for r in gate.values() if r.get("feasible"))
    print(f"    gate: {feas_n}/{len(problem.gate)} feasible")
    if feas_n == 0:
        rec = {**decision.to_dict(), "status": "gate_failed", "improved": False, "promoted": False}
        _append_log(problem, rec); return rec
    if skip_remote:
        return {**decision.to_dict(), "status": "gate_only"}

    cand_name = f"{decision.species}__cand"
    if not backend.reachable():
        return {**decision.to_dict(), "status": "unreachable"}
    if not backend.deploy(cand_name, cand_dir):
        rec = {**decision.to_dict(), "status": "deploy_failed", "improved": False, "promoted": False}
        _append_log(problem, rec); return rec
    sols = backend.run(cand_name, cand_dir, instances=problem.instances, seed=seed,
                       time_limit=time_limit, threads=threads)
    if sols is None:
        rec = {**decision.to_dict(), "status": "eval_failed", "improved": False, "promoted": False}
        _append_log(problem, rec)
        print("[step] evaluation failed -- journaled as an infrastructure failure (not a lever attempt).")
        return rec

    cand_diag = diagnostics.build_diagnostics(cand_dir, problem, solutions_dir=sols)
    improved = _target_improved(decision, champ, cand_diag, problem)

    # --- PROMOTION GATE: noise guard + ratchet + environment-collapse detector ---
    champ_gate = champ          # the champion diagnostics the candidate is judged against
    rechecked = False
    bar_source = "cached"
    ev = lim["evaluation"]
    if ev.get("recheck_champion", True) and _champion_better(pop, decision.species, champ, cand_diag, problem):
        print("--- noise guard: candidate beats the CACHED champion; re-evaluating the champion fresh ---")
        fresh = _fresh_champion_eval(problem, backend, decision.species, seed, time_limit, threads)
        if fresh is None:
            print("    [guard] fresh champion eval failed; judging against the cached baseline.")
        else:
            rechecked = True
            if _collapsed(champ, fresh, float(ev.get("collapse_ratio", 0.30))):
                print(f"    [guard] fresh champion eval COLLAPSED vs its cache "
                      f"({fresh.get('feasible_count')}/{problem.config.n_instances} feasible, "
                      f"total {fresh.get('total_cost', 0):,.0f} vs cached {champ.get('total_cost', 0):,.0f}) "
                      f"-- evaluation environment at fault; voiding this step.")
                rec = {**decision.to_dict(), "status": "eval_failed", "env_collapsed": True,
                       "improved": False, "promoted": False,
                       "champion_fresh_total": fresh.get("total_cost"),
                       "champion_cached_total": champ.get("total_cost")}
                _append_log(problem, rec)
                return rec
            # RATCHET: the bar is the better of cached and fresh; the baseline only moves up.
            if _champion_better(pop, decision.species, champ, fresh, problem):
                champ_gate, bar_source = fresh, "fresh"
                if ev.get("ratchet_baseline", True):
                    fresh["species"] = decision.species
                    (problem.species_dir / decision.species / "diagnostics.json").write_text(
                        json.dumps(fresh, indent=2), encoding="utf-8")
                    print(f"    [guard] champion baseline ratcheted UP "
                          f"({champ.get('total_cost', 0):,.0f} -> {fresh.get('total_cost', 0):,.0f}).")
            else:
                print(f"    [guard] fresh eval ({fresh.get('total_cost', 0):,.0f}) not better than the "
                      f"cached best ({champ.get('total_cost', 0):,.0f}); bar stays at the cached best.")
            improved = _target_improved(decision, champ_gate, cand_diag, problem)
    promoted = _champion_better(pop, decision.species, champ_gate, cand_diag, problem)

    print(f"--- result ---\n    champion : {champ_gate.get('feasible_count')}/{problem.config.n_instances}"
          f"  {champ_gate.get('total_cost', 0):,.0f} ({bar_source})\n    candidate: {cand_diag.get('feasible_count')}"
          f"/{problem.config.n_instances}  {cand_diag.get('total_cost', 0):,.0f}"
          f"\n    -> {'PROMOTED' if promoted else 'rejected'}")

    sp_dir = problem.species_dir / decision.species
    if promoted:
        archive = sp_dir / "archive"; archive.mkdir(exist_ok=True)
        m = len(list(archive.glob("solve_v*.py")))
        shutil.copy2(sp_dir / problem.config.solver_entry, archive / f"solve_v{m}.py")
        (sp_dir / problem.config.solver_entry).write_bytes(cand_bytes)
        cand_diag["species"] = decision.species
        (sp_dir / "diagnostics.json").write_text(json.dumps(cand_diag, indent=2), encoding="utf-8")
        per_inst = {s: cand_diag["per_instance"].get(s, {}).get("total") for s in problem.instances}
        (sp_dir / "results.json").write_text(json.dumps(
            {"species": decision.species, "seed": seed, "feasible_count": cand_diag["feasible_count"],
             "total_cost": cand_diag["total_cost"], "per_instance": per_inst}, indent=2), encoding="utf-8")

    t = decision.target_component
    rec = {**decision.to_dict(), "status": "evaluated", "improved": bool(improved), "promoted": bool(promoted),
           "champion_rechecked": rechecked, "champion_bar": bar_source,
           "before": {"feasible": champ_gate.get("feasible_count"), "total": champ_gate.get("total_cost"),
                      "component": champ_gate.get("component_totals", {}).get(t) if t and t != "feasibility" else None},
           "after": {"feasible": cand_diag.get("feasible_count"), "total": cand_diag.get("total_cost"),
                     "component": cand_diag.get("component_totals", {}).get(t) if t and t != "feasibility" else None},
           "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _append_log(problem, rec)
    return rec


def main() -> None:
    if os.environ.get("ES_IN_CAMPAIGN") == "1":
        print("[guard] a campaign is already active in this process tree; refusing nested run."); return
    os.environ["ES_IN_CAMPAIGN"] = "1"

    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True)
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--self-test-time", type=int, default=20)
    ap.add_argument("--gate-time", type=int, default=None)
    ap.add_argument("--build-timeout", type=int, default=None,
                    help="agent wall-clock cap (default: config/limits.yaml agent.build_timeout_s)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--time-limit", type=int, default=None)
    ap.add_argument("--skip-remote", action="store_true")
    args = ap.parse_args()

    problem = load_problem(args.problem)
    comp = problem.config.compute
    lim = problem.config.limits
    build_timeout = args.build_timeout if args.build_timeout is not None else int(lim["agent"]["build_timeout_s"])
    seed = args.seed if args.seed is not None else comp.get("seed", 57)
    time_limit = args.time_limit if args.time_limit is not None else comp.get("time_limit", 60)
    gate_time = args.gate_time if args.gate_time is not None else max(2, min(time_limit, 20))
    threads = comp.get("threads", 4)

    done = 0
    while done < args.loop:
        print(f"\n############### STEP {done+1}/{args.loop} [{args.problem}] ###############")
        try:
            rec = run_step(problem, args.model, args.self_test_time, gate_time, build_timeout,
                           seed, time_limit, threads, args.skip_remote)
        except Exception as exc:
            print(f"[loop] step {done+1} raised {type(exc).__name__}: {exc}")
            traceback.print_exc()
            print("[loop] continuing (step consumed).")
            done += 1; continue
        status = rec.get("status")
        if status == "no_population":
            break
        if status == "unreachable":
            print("[loop] compute unreachable; waiting 300s, retrying this step."); time.sleep(300); continue
        if status == "rate_limited":
            backoff = int(lim["agent"]["rate_limit_backoff_s"])
            print(f"[loop] rate limited; sleeping {backoff}s, retrying this step."); time.sleep(backoff); continue
        done += 1


if __name__ == "__main__":
    main()
