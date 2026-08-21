"""Ideator agent: invent a new species genome (cold start / new_species escalation).
Problem-agnostic; the proposal schema and validation branch on the genome style.
"""
from __future__ import annotations

from typing import Any

from .base import call_claude, parse_json_response, AgentError
from . import prompts


def propose_new_species(problem, *, model: str = "opus", extra_context: str = "",
                        existing_names=None) -> dict[str, Any]:
    prompt = prompts.new_species_prompt(problem, existing_names or [])
    if extra_context:
        prompt += "\n\n" + extra_context
    raw = call_claude(prompt, model=model, cwd=problem.config.root)
    proposal = parse_json_response(raw, "ideator")
    _validate(proposal, problem)
    return proposal


def _validate(proposal: dict, problem) -> None:
    if not proposal.get("name"):
        raise AgentError("ideator proposal missing 'name'.")
    if problem.config.genome_style == "structured":
        cats = set(problem.config.genome_spec.get("catalog", {}).keys())
        genes = proposal.get("genes", {})
        missing = cats - set(genes)
        if missing:
            raise AgentError(f"structured proposal missing gene categories: {missing}")
        for c, g in genes.items():
            if "allele" not in g:
                raise AgentError(f"gene '{c}' missing 'allele'.")
    else:
        if not proposal.get("summary"):
            raise AgentError("freeform proposal missing 'summary'.")
