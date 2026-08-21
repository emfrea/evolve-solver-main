"""Genome: the high-level description of a solver. Two interchangeable styles, selected
per problem via problem.yaml `genome.style`:

  structured -- a small set of orthogonal GENES, each holding one ALLELE from a catalog
                (problems/<name>/genome.yaml). Species-defining genes fix the algorithm's
                identity; tunable genes are refined and spliced.
  freeform   -- {name, summary, phases, notes}: the LLM invents any design; phases are the
                problem's natural subproblems (the crossover/splice units).

Both satisfy the GenomeLike protocol (species_key / to_dict / from_dict) so everything
downstream (scheduler, agents, diagnostics) is genome-agnostic. Only agents.prompts.genome_block
and the ideator proposal schema branch on the style.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GenomeLike(Protocol):
    def species_key(self) -> str: ...
    def to_dict(self) -> dict: ...


# --- structured ---

@dataclass
class Gene:
    category: str
    allele: str
    params: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict:
        return {"category": self.category, "allele": self.allele,
                "params": dict(self.params), "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "Gene":
        return cls(category=d["category"], allele=d["allele"],
                   params=dict(d.get("params", {})), notes=d.get("notes", ""))


@dataclass
class StructuredGenome:
    genes: dict[str, Gene] = field(default_factory=dict)
    species_defining: list[str] = field(default_factory=list)   # from the catalog

    def allele(self, category: str) -> str | None:
        g = self.genes.get(category)
        return g.allele if g else None

    def species_key(self) -> str:
        keys = self.species_defining or list(self.genes)
        return "|".join(f"{c}={self.allele(c) or '?'}" for c in keys)

    def to_dict(self) -> dict:
        return {"style": "structured", "genes": {c: g.to_dict() for c, g in self.genes.items()}}

    @classmethod
    def from_dict(cls, d: dict, species_defining: list[str] | None = None) -> "StructuredGenome":
        genes = {c: Gene.from_dict(g) for c, g in d.get("genes", {}).items()}
        return cls(genes=genes, species_defining=species_defining or [])


# --- free-form ---

@dataclass
class FreeformGenome:
    name: str = ""
    summary: str = ""
    phases: dict[str, str] = field(default_factory=dict)   # keyed by the problem's phase names
    notes: str = ""

    def species_key(self) -> str:
        return self.name

    def to_dict(self) -> dict:
        return {"style": "freeform", "name": self.name, "summary": self.summary,
                "phases": dict(self.phases), "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "FreeformGenome":
        return cls(name=d.get("name", ""), summary=d.get("summary", ""),
                   phases=dict(d.get("phases", {})), notes=d.get("notes", ""))


# --- helpers driven by the problem ---

def catalog_species_defining(genome_spec: dict) -> list[str]:
    """The species-defining gene categories declared in a structured genome.yaml catalog."""
    cat = genome_spec.get("catalog", {}) if genome_spec else {}
    return [c for c, v in cat.items() if v.get("species_defining")]


def genome_from_dict(d: dict, problem) -> GenomeLike:
    """Reconstruct a genome from a saved dict, honouring the problem's style."""
    style = d.get("style", problem.config.genome_style)
    if style == "structured":
        return StructuredGenome.from_dict(d, catalog_species_defining(problem.config.genome_spec))
    return FreeformGenome.from_dict(d)


def proposal_to_genome(proposal: dict, problem) -> GenomeLike:
    """Turn an ideator proposal into a genome for the problem's style."""
    if problem.config.genome_style == "structured":
        genes = {c: Gene(category=c, allele=g["allele"], notes=g.get("notes", ""),
                         params=g.get("params", {}))
                 for c, g in proposal.get("genes", {}).items()}
        return StructuredGenome(genes=genes,
                                species_defining=catalog_species_defining(problem.config.genome_spec))
    return FreeformGenome(name=proposal.get("name", ""), summary=proposal.get("summary", ""),
                          phases=proposal.get("phases", {}), notes=proposal.get("rationale", ""))
