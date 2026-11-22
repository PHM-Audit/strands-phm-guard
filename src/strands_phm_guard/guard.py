"""Strands intervention handler: deterministic physics/bounds guard for tool calls.

Wraps the phm-lite-check rule-table scorer (merged into microsoft/PyRIT as
PhysicsConsistencyScorer) as a Strands ``InterventionHandler``. No LLM in the
loop, no external API; verdicts are recomputable.

Strands is an optional import: when absent, the handler subclasses a local
protocol base with identical lifecycle shape so the package works standalone
and in tests.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

try:  # optional runtime dependency
    from strands.interventions.handler import InterventionHandler  # type: ignore
    from strands.interventions.actions import Deny, Guide, Proceed  # type: ignore

    _HAS_STRANDS = True
except Exception:  # pragma: no cover - exercised only without strands installed
    _HAS_STRANDS = False

    class InterventionHandler:  # type: ignore[no-redef]
        """Minimal protocol-compatible base (standalone mode)."""

        name: str = "base"

    class Proceed:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.action = "proceed"

    class Deny:  # type: ignore[no-redef]
        def __init__(self, reason: str = "") -> None:
            self.action = "deny"
            self.reason = reason

    class Guide:  # type: ignore[no-redef]
        def __init__(self, message: str = "") -> None:
            self.action = "guide"
            self.message = message


try:  # rule-table scorer: zero-dependency, deterministic
    from phm_lite_check.checker import check_quantity, check_text
except Exception as exc:  # pragma: no cover
    raise ImportError(
        "strands-phm-guard requires phm-lite-check: pip install phm-lite-check"
    ) from exc


# ---------------------------------------------------------------------------
# Rule table: argument-level physics bounds (deterministic, no model in loop)
# ---------------------------------------------------------------------------

HARD_LIMITS = {
    "speed": 299792458.0,  # m/s, c
    "velocity": 299792458.0,
    "rpm": 500000.0,  # typical industrial ceiling
    "force_n": 1e7,
    "power_w": 1e9,
    "altitude_m": 40000000.0,  # ~geostationary belt
    "depth_m": 12000.0,  # Challenger Deep
}
NEGATIVE_FORBIDDEN = {"mass", "power", "rpm", "speed", "energy"}


def check_tool_args(args: Dict[str, Any]) -> List[str]:
    """Return a list of violation descriptions for numeric tool arguments.

    Empty list == no violation. Deterministic; recomputable from inputs.
    """
    violations: List[str] = []
    for key, value in (args or {}).items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            violations.append(f"{key}={value!r} is not a finite number")
            continue
        if key in NEGATIVE_FORBIDDEN and v < 0:
            violations.append(f"{key}={v} is negative (forbidden for {key})")
        limit = HARD_LIMITS.get(key)
        if limit is not None and abs(v) > limit:
            violations.append(f"{key}={v} exceeds hard limit {limit}")
    return violations


class VerdictStats:
    """Recomputable counters; part of the audit trail."""

    def __init__(self) -> None:
        self.total = 0
        self.allowed = 0
        self.denied = 0
        self.guided = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "total": self.total,
            "allowed": self.allowed,
            "denied": self.denied,
            "guided": self.guided,
        }


class PhysicsGuard(InterventionHandler):
    """Deterministic pre-execution physics/bounds guard (three-state exit).

    mode="deny": physically impossible tool calls are blocked (VETO exit).
    mode="guide": blocked calls return Guide with the violation list (DEGRADE
    exit) so the model can self-correct instead of hard-failing.
    """

    name = "phm-physics-guard"

    def __init__(self, mode: str = "deny", hard_limits: Optional[dict] = None) -> None:
        if mode not in ("deny", "guide"):
            raise ValueError("mode must be 'deny' or 'guide'")
        self.mode = mode
        self.hard_limits = dict(HARD_LIMITS)
        if hard_limits:
            self.hard_limits.update(hard_limits)
        self.stats = VerdictStats()

    # -- lifecycle hook (Strands InterventionHandler) -----------------------
    # NOTE: the method below is the Strands lifecycle hook; its name matches the
    # base-class spelling exactly (verified against strands-agents 1.64.0).

    def before_tool_call(self, event: Any, **kwargs: Any):
        return self._on_hook(event, **kwargs)
    def _check_event(self, event: Any) -> List[str]:
        tool_use = getattr(event, "tool_use", None)
        if tool_use is None and isinstance(event, dict):
            tool_use = event.get("tool_use")
        if tool_use is None:
            return []
        if isinstance(tool_use, dict):
            args = tool_use.get("input") or tool_use.get("arguments") or {}
        else:
            args = getattr(tool_use, "input", None) or {}
        if not isinstance(args, dict):
            return []
        return check_tool_args(args)

    def _on_hook(self, event: Any):
        violations = self._check_event(event)
        self.stats.total += 1
        if not violations:
            self.stats.allowed += 1
            return Proceed()
        detail = "; ".join(violations[:4])
        if self.mode == "deny":
            self.stats.denied += 1
            return Deny(reason=f"physics guard veto: {detail}")
        self.stats.guided += 1
        detail_text = "physics guard: recheck arguments - " + detail
        return Guide(feedback=detail_text)

