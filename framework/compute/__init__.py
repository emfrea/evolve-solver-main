"""Compute backends: where a candidate solver is evaluated over the instance set.

    local        -- run locally (default; no HPC, no network).
    remote_slurm -- deploy + submit a SLURM array over SSH (config-driven; ported from the
                    original SLURM cluster adapter with its retry/poll machinery preserved).

get_backend(problem) returns the backend named in problem.compute.backend.
"""
from __future__ import annotations

from .base import ComputeBackend
from .local import LocalBackend


def get_backend(problem) -> ComputeBackend:
    name = (problem.config.compute or {}).get("backend", "local")
    if name == "local":
        return LocalBackend(problem)
    if name == "remote_slurm":
        from .remote_slurm import RemoteSlurmBackend
        return RemoteSlurmBackend(problem)
    raise ValueError(f"unknown compute backend: {name!r}")
