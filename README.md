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

50 tests, no network, fully deterministic.

## MHS-style hardware action guard (v0.2.0)

New in 0.2.0: a rule-table guard aligned with the Model Hardware Standard (MHS)
research-preview driver model — AI agents operate physical devices through
read/write primitives ("get temperature", "set temperature"). Before such a
command reaches hardware, `check_device_command` validates it deterministically:

- **Absolute-zero floor**: temperature writes below 0 K / -273.15 °C / -459.67 °F are flagged (unit-aware).
- **Device envelopes**: per-parameter safety ranges (pH 0–14, humidity 0–100 %, pressure, voltage, RPM, ...) — out-of-range writes are flagged with the envelope that fired.
- **Command shape**: write commands carrying no value, read commands carrying a payload, unknown actions, empty parameters, and unit/param mismatches are all flagged.
- **Non-finite rejection**: NaN/±inf writes never pass.
- **Audit chain**: SHA-256 verdict chain over (rule inputs → verdict), recomputable offline.

```python
from strands_phm_guard.mhs import check_device_command, MHSActionGuard

check_device_command("write", "temperature_c", -300.0)
# -> ['temperature_c=-300.0 is below absolute zero (envelope temperature_c: [-273.15, 2000.0])']

guard = MHSActionGuard(mode="deny")   # or "guide"; denies/guides only device-shaped calls
```

This is an independent, deterministic implementation aligned with publicly
described MHS concepts (read/write primitives, device safety limits); it is not
an official SDK and implements no Anthropic protocol internals.

## Scope note

This is a thin rule-table layer: it validates **numeric argument bounds** (speed-of-light ceiling, negative-physical-quantity rejection, non-finite rejection, configurable per-argument hard limits). It does not parse natural language and does not call any model.

Related proposal: [strands-agents/harness-sdk#5934](https://github.com/strands-agents/harness-sdk/issues/5934).
