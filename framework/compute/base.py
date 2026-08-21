"""ComputeBackend: the four calls the operational layer uses. A backend evaluates a
species' solver over the problem's instances and returns the directory of produced
solutions (sol_<stem>.<ext>), which build_diagnostics then validates.

The solutions-dir contract is identical for local and remote so downstream code is
backend-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class ComputeBackend(ABC):
    def __init__(self, problem):
        self.problem = problem

    @abstractmethod
    def reachable(self) -> bool:
        """Is the backend usable right now? (local -> always True; remote -> VPN/host check.)"""

    @abstractmethod
    def deploy(self, name: str, solver_dir: Path) -> bool:
        """Stage the solver + inputs where run() expects them (local -> no-op)."""

    @abstractmethod
    def run(self, name: str, solver_dir: Path, *, instances, seed: int,
            time_limit: int, threads: int) -> Optional[Path]:
        """Evaluate the solver over `instances`; return the solutions dir (sol_<stem>.<ext>)
        or None on failure."""
