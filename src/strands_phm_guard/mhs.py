"""MHS-style hardware-action guard (deterministic rule table, no model in loop).

Aligned with the Model Hardware Standard (MHS) research preview driver model:
agents operate physical devices through read/write primitives ("get temperature",
"set temperature"). This module validates such commands against per-parameter
safety envelopes BEFORE execution. All verdicts are recomputable from inputs.

This is an independent implementation aligned with publicly described MHS
concepts (read/write primitives, device safety limits, discovery); it is not an
official SDK and implements no Anthropic protocol internals.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Device parameter safety envelopes (deterministic, recomputable).
# key: (min, max). Keys follow "<param>_<unit>" so unit checking is table-driven.
# ---------------------------------------------------------------------------

DEVICE_ENVELOPES: Dict[str, tuple] = {
    "temperature_k": (0.0, 2273.15),        # 0 K .. ~2000 C equivalent
    "temperature_c": (-273.15, 2000.0),
    "temperature_f": (-459.67, 3632.0),
    "pressure_bar": (0.0, 5000.0),          # ultra-high-pressure lab equipment
    "voltage_v": (-1.0e6, 1.0e6),
    "current_a": (-1.0e4, 1.0e4),
    "power_w": (0.0, 1.0e9),
    "force_n": (0.0, 1.0e7),
    "volume_ml": (0.0, 1.0e6),
    "mass_g": (0.0, 1.0e7),
    "rpm": (0.0, 5.0e5),
    "ph": (0.0, 14.0),
    "humidity_pct": (0.0, 100.0),
    "speed_mps": (0.0, 299792458.0),        # c
    "altitude_m": (0.0, 4.0e7),             # ~geostationary belt
    "depth_m": (0.0, 12000.0),              # Challenger Deep
}

# parameters that must carry a unit for validation; unknown unit => flagged
UNIT_BEARING = ("temperature", "pressure", "speed")

# unit -> canonical envelope key suffix
_UNIT_ALIASES = {
    "k": "k", "kelvin": "k",
    "c": "c", "celsius": "c",
    "f": "f", "fahrenheit": "f",
    "bar": "bar", "pa": "bar", "psi": "bar",
    "m/s": "mps", "mps": "mps",
}

ABSOLUTE_ZERO_K = 0.0


def check_device_command(
    action: str,
    param: str,
    value: Any = None,
    unit: Optional[str] = None,
) -> List[str]:
    """Validate one device command. Returns list of violation strings.

    Deterministic and recomputable: same inputs -> same verdict.
    ``action`` must be "read" or "write"; ``param`` is a device parameter name;
    ``value`` the commanded magnitude; ``unit`` its unit (optional).
    """
    violations: List[str] = []
    act = (action or "").strip().lower()
    if act not in ("read", "write"):
        violations.append(f"action={action!r} is neither read nor write (unknown command shape)")
        return violations

    p = (param or "").strip().lower()
    if not p:
        violations.append("param is empty")

    # ---- command-shape rules -------------------------------------------
    if act == "write":
        if value is None:
            violations.append("write command carries no value (cannot actuate)")
            return violations
    else:  # read
        if value is not None:
            violations.append(f"read command carries a value ({value!r}); reads take no payload")

    if value is None:
        return violations

    if isinstance(value, bool):
        violations.append(f"value={value!r} is boolean, expected numeric magnitude")
        return violations

    if not isinstance(value, (int, float)):
        violations.append(f"value={value!r} is not numeric (type={type(value).__name__})")
        return violations

    v = float(value)
    if math.isnan(v) or math.isinf(v):
        violations.append(f"{param}={value!r} is not a finite number (NaN/Inf must never reach hardware)")
        return violations

    # ---- envelope resolution: param may embed its unit (temperature_c) --
    env_key: Optional[str] = None
    if p in DEVICE_ENVELOPES:
        env_key = p
        # explicit unit must agree with the embedded one
        if unit is not None:
            u = _UNIT_ALIASES.get(unit.strip().lower())
            if u and not p.endswith("_" + u):
                violations.append(
                    f"unit {unit!r} disagrees with param {p!r} (embedded unit mismatch)")
    else:
        # bare param + unit (e.g. param="temperature", unit="C")
        base = p.split("_")[0]
        u = _UNIT_ALIASES.get((unit or "").strip().lower()) if unit else None
        if base in ("temperature", "pressure", "speed"):
            if u is None:
                violations.append(
                    f"param={p!r} is unit-bearing but unit={unit!r} is unrecognized; "
                    "cannot validate envelope deterministically")
                return violations
            env_key = f"{base}_{u}"
        # non-unit-bearing bare params keep their envelope if present
        elif f"{p}_{u}" in DEVICE_ENVELOPES:
            env_key = f"{p}_{u}"

    if env_key is None:
        return violations  # no envelope known for this param: not a violation

    lo, hi = DEVICE_ENVELOPES[env_key]
    if v < lo or v > hi:
        # special-case below-absolute-zero for clarity
        if env_key.startswith("temperature") and v < lo:
            violations.append(
                f"{param}={v}{unit or ''} is below absolute zero (envelope {env_key}: [{lo}, {hi}])")
        else:
            violations.append(
                f"{param}={v}{unit or ''} outside safety envelope {env_key}: [{lo}, {hi}]")
    return violations


def check_device_batch(commands: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate a batch of device commands; return per-command verdicts + digest.

    Recomputable audit artifact: sha256 over the canonical (rule, inputs, verdicts)
    triple, so any third party can re-derive the verdict chain.
    """
    results = []
    for cmd in commands:
        v = check_device_command(
            cmd.get("action", ""),
            cmd.get("param", ""),
            cmd.get("value"),
            cmd.get("unit"),
        )
        results.append({"command": cmd, "violations": v, "verdict": "PASS" if not v else "VETO"})
    digest_src = json.dumps(results, sort_keys=True, default=str).encode()
    return {"results": results, "audit_sha256": hashlib.sha256(digest_src).hexdigest()}


class MHSActionGuard:
    """Deterministic MHS-style hardware-command guard (three-state exit).

    Same deny/guide contract as ``PhysicsGuard``; composes with it by running
    both rule families and unioning violations.
    """

    name = "phm-mhs-action-guard"

    def __init__(self, mode: str = "deny") -> None:
        if mode not in ("deny", "guide"):
            raise ValueError("mode must be 'deny' or 'guide'")
        self.mode = mode
        self.stats = VerdictStats()

    def check(self, commands: List[Dict[str, Any]]) -> List[str]:
        """Batch entry point (non-handler contexts)."""
        violations: List[str] = []
        for cmd in commands:
            violations.extend(check_device_command(
                cmd.get("action", ""), cmd.get("param", ""),
                cmd.get("value"), cmd.get("unit")))
        return violations

    # -- Strands lifecycle hook (same shape as PhysicsGuard) ----------------
    def _check_event(self, event: Any) -> List[str]:
        tool_use = getattr(event, "tool_use", None)
        if tool_use is None and isinstance(event, dict):
            tool_use = event.get("tool_use")
        if tool_use is None:
            return []
        if isinstance(tool_use, dict):
            name = str(tool_use.get("name", "")).lower()
            args = tool_use.get("input") or tool_use.get("arguments") or {}
        else:
            name = str(getattr(tool_use, "name", "")).lower()
            args = getattr(tool_use, "input", None) or {}
        if not isinstance(args, dict):
            return []
        # only guard device-shaped tool calls (read_/write_/set_/get_ prefixes)
        if not any(name.startswith(p) for p in ("read", "write", "set", "get")):
            return []
        action = "write" if name.startswith(("write", "set")) else "read"
        param = str(args.get("param") or args.get("parameter") or args.get("device_param") or "")
        value = args.get("value")
        unit = args.get("unit")
        return check_device_command(action, param, value, unit)

    def _on_hook(self, event: Any):
        violations = self._check_event(event)
        self.stats.total += 1
        if not violations:
            self.stats.allowed += 1
            return Proceed()
        detail = "; ".join(violations[:4])
        if self.mode == "deny":
            self.stats.denied += 1
            return Deny(reason=f"mhs action guard veto: {detail}")
        self.stats.guided += 1
        return Guide(feedback="mhs action guard: recheck command - " + detail)


# reuse lifecycle primitives from guard.py at import time to avoid duplication drift
from .guard import Deny, Guide, Proceed, VerdictStats  # noqa: E402,F401
