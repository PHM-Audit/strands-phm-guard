# strands-phm-guard

Deterministic **physics/bounds intervention** for [Strands Agents](https://github.com/strands-agents) — validates tool-call arguments against a rule table **before execution**. No model in the loop, no external API; verdicts are recomputable from inputs alone.

The rule-table scorer behind this package is the same implementation merged into [microsoft/PyRIT](https://github.com/microsoft/PyRIT) (PR #3507) and published on [Dify Marketplace](https://github.com/langgenius/dify-official-plugins) as `phm-physics-check`.

## What it catches

| Argument | Value | Verdict |
|---|---|---|
| `speed` | `4e9` m/s | VETO — exceeds c |
| `power` | `-50` W | VETO — negative |
| `rpm` | `inf` | VETO — not finite |
| `speed` | `100` m/s | PASS |

## Install

```bash
pip install strands-phm-guard
```

## Use (Strands Agents SDK >= 1.64)

```python
from strands_phm_guard import PhysicsGuard

guard = PhysicsGuard(mode="deny")   # or mode="guide" for soft correction

# pass via Agent(interventions=[guard]) - see Strands interventions docs
```

Three-state exit:
- **PASS** → `Proceed()` (tool call dispatched)
- **DEGRADE** → `Guide(message=...)` (model gets the violation list and can self-correct)
- **VETO** → `Deny(reason=...)` (physically impossible call blocked before execution)

Stats are exposed as recomputable counters: `guard.stats.as_dict()`.

## Tests

```bash
pip install phm-lite-check pytest
pytest tests/ -q
```

16 tests, no network, fully deterministic.

## Scope note

This is a thin rule-table layer: it validates **numeric argument bounds** (speed-of-light ceiling, negative-physical-quantity rejection, non-finite rejection, configurable per-argument hard limits). It does not parse natural language and does not call any model.

Related proposal: [strands-agents/harness-sdk#5934](https://github.com/strands-agents/harness-sdk/issues/5934).
