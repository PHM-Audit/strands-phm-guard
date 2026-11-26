"""strands-phm-guard: deterministic physics/bounds intervention for Strands Agents.

Rule-table scorer (no model in the loop) wrapped as a Strands
InterventionHandler. Verdicts are recomputable; three-state exit
(allow / guide / deny). Rule table merged into microsoft/PyRIT as
PhysicsConsistencyScorer (PR #3507).
"""

from .guard import PhysicsGuard, VerdictStats, check_tool_args, HARD_LIMITS

__version__ = "0.2.1"
__all__ = ["PhysicsGuard", "VerdictStats", "check_tool_args", "HARD_LIMITS"]
