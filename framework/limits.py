"""Central limits & caps -- loaded from config/limits.yaml, overridable per problem.

Every cap the engine needs (agent timeouts, evaluation guards, compute retries, scheduler
attempt caps) is declared in ONE file so a campaign can be audited and tuned without
reading code. `load_limits()` merges, highest precedence first:

    problem.yaml `limits:` block  >  config/limits.yaml  >  _FALLBACK (below)

Access: `problem.config.limits["evaluation"]["collapse_ratio"]` or `get(limits, "a.b.c")`.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LIMITS_FILE = REPO_ROOT / "config" / "limits.yaml"

# Hard-coded safety net: used only for keys missing from both YAML sources.
_FALLBACK: dict[str, dict[str, Any]] = {
    "agent": {"build_timeout_s": 2400, "deliberate_noop_min_s": 120, "rate_limit_backoff_s": 1800,
              "reachability_wait_s": 1800, "reachability_poll_s": 120},
    "evaluation": {"recheck_champion": True, "collapse_ratio": 0.30, "ratchet_baseline": True},
    "compute": {"max_run_wait_s": 7200, "poll_interval_s": 30, "max_poll_failures": 5,
                "collect_verify_retries": 3, "collect_verify_wait_s": 60,
                "scp_retries": 20, "scp_retry_delay_s": 60, "net_retries": 8, "net_retry_delay_s": 30,
                "set_walltime": True, "walltime_margin_s": 600},
    "scheduler": {"evolve_max_attempts": 2, "crossover_max_attempts": 2, "feas_repair_giveup": 3},
    "selection": {"leader_bias": 1.5, "explore_floor": 1.5, "exhausted_weight": 1.0, "diversity_decay": 0.7},
    "targeting": {"abs_base": 0.10},
    "ladder": {"wildcard_cap": 2, "max_species": 6, "new_species_cooldown": 3, "hopeless_attempts": 4},
    "crossover": {"exhausted_any_donor": True, "instance_xover_min_wins": 3,
                  "instance_xover_margin": 0.02, "instance_xover_cap": 2},
}


def _deep_merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_limits(problem_overrides: dict | None = None, limits_file: Path | None = None) -> dict:
    path = limits_file or LIMITS_FILE
    repo = {}
    if path.exists():
        repo = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _deep_merge(_deep_merge(_FALLBACK, repo), problem_overrides)


def get(limits: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = limits
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
