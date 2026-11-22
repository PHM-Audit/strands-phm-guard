"""Tests for the MHS-style hardware-action guard (deterministic, recomputable)."""

import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

mhs = importlib.import_module("strands_phm_guard.mhs")


# ---------------------------------------------------------------------------
# command-shape rules
# ---------------------------------------------------------------------------

def test_write_without_value_flagged():
    v = mhs.check_device_command("write", "temperature_c")
    assert any("carries no value" in x for x in v)


def test_read_with_value_flagged():
    v = mhs.check_device_command("read", "temperature_c", 25.0)
    assert any("reads take no payload" in x for x in v)


def test_unknown_action_flagged():
    v = mhs.check_device_command("execute", "temperature_c", 25.0)
    assert any("neither read nor write" in x for x in v)


def test_empty_param_flagged():
    v = mhs.check_device_command("write", "", 25.0)
    assert any("param is empty" in x for x in v)


def test_boolean_value_flagged():
    v = mhs.check_device_command("write", "temperature_c", True)
    assert any("boolean" in x for x in v)


def test_non_numeric_value_flagged():
    v = mhs.check_device_command("write", "temperature_c", "hot")
    assert any("not numeric" in x for x in v)


# ---------------------------------------------------------------------------
# NaN / Inf must never reach hardware
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_inf_flagged(bad):
    v = mhs.check_device_command("write", "temperature_c", bad)
    assert any("not a finite number" in x for x in v)


# ---------------------------------------------------------------------------
# absolute-zero family (the MHS-flavored physical impossibility)
# ---------------------------------------------------------------------------

def test_below_absolute_zero_c():
    v = mhs.check_device_command("write", "temperature_c", -300.0)
    assert any("below absolute zero" in x for x in v)


def test_below_absolute_zero_k():
    v = mhs.check_device_command("write", "temperature", -5.0, unit="K")
    assert any("below absolute zero" in x for x in v)


def test_below_absolute_zero_f():
    v = mhs.check_device_command("write", "temperature", -500.0, unit="F")
    assert any("below absolute zero" in x for x in v)


def test_valid_temperature_passes():
    assert mhs.check_device_command("write", "temperature_c", 25.0) == []
    assert mhs.check_device_command("write", "temperature", 310.0, unit="K") == []
    assert mhs.check_device_command("write", "temperature", 98.6, unit="F") == []


def test_kelvin_negative_is_absolute_zero_violation():
    v = mhs.check_device_command("write", "temperature_k", -1.0)
    assert any("below absolute zero" in x or "outside safety envelope" in x for x in v)


# ---------------------------------------------------------------------------
# envelope violations (table-driven)
# ---------------------------------------------------------------------------

def test_ph_out_of_range():
    v = mhs.check_device_command("write", "ph", 15.2)
    assert any("outside safety envelope" in x for x in v)


def test_humidity_out_of_range():
    v = mhs.check_device_command("write", "humidity_pct", 150.0)
    assert any("outside safety envelope" in x for x in v)


def test_rpm_over_ceiling():
    v = mhs.check_device_command("write", "rpm", 600000.0)
    assert any("outside safety envelope" in x for x in v)


def test_superluminal_speed():
    v = mhs.check_device_command("write", "speed", 3.1e8, unit="m/s")
    assert any("outside safety envelope" in x for x in v)


def test_negative_mass_flagged():
    v = mhs.check_device_command("write", "mass_g", -2.0)
    assert any("outside safety envelope" in x for x in v)


# ---------------------------------------------------------------------------
# unit discipline
# ---------------------------------------------------------------------------

def test_unit_bearing_without_unit_flagged():
    v = mhs.check_device_command("write", "temperature", 25.0)
    assert any("unrecognized" in x for x in v)


def test_unknown_unit_flagged():
    v = mhs.check_device_command("write", "temperature", 25.0, unit="blorp")
    assert any("unrecognized" in x for x in v)


def test_embedded_unit_mismatch_flagged():
    v = mhs.check_device_command("write", "temperature_c", 25.0, unit="K")
    assert any("disagrees with param" in x for x in v)


def test_unit_alias_psi_resolves():
    # psi alias maps to pressure_bar envelope family
    v = mhs.check_device_command("write", "pressure", 14.7, unit="psi")
    assert v == []  # 14.7 psi ~ 1 bar, inside envelope


def test_unit_alias_kelvin():
    assert mhs.check_device_command("write", "temperature", 77.0, unit="kelvin") == []


# ---------------------------------------------------------------------------
# pass-through behavior
# ---------------------------------------------------------------------------

def test_unknown_param_no_envelope_passes():
    # no envelope known -> not our verdict to make
    assert mhs.check_device_command("write", "warp_factor", 9.5) == []


def test_read_valid_passes():
    assert mhs.check_device_command("read", "temperature_c") == []


# ---------------------------------------------------------------------------
# batch audit chain (recomputable digest)
# ---------------------------------------------------------------------------

def test_batch_verdicts_and_digest_stable():
    cmds = [
        {"action": "write", "param": "temperature_c", "value": 25.0},
        {"action": "write", "param": "ph", "value": 15.2},
        {"action": "read", "param": "pressure_bar"},
    ]
    r1 = mhs.check_device_batch(cmds)
    r2 = mhs.check_device_batch(list(reversed(cmds)))
    verdicts = [r["verdict"] for r in r1["results"]]
    assert verdicts == ["PASS", "VETO", "PASS"]
    assert len(r1["audit_sha256"]) == 64
    # same inputs -> same digest (recomputable); order-insensitive digest is
    # NOT required, but determinism for identical order is.
    r3 = mhs.check_device_batch(cmds)
    assert r1["audit_sha256"] == r3["audit_sha256"]


def test_batch_digest_changes_with_input():
    cmds = [{"action": "write", "param": "temperature_c", "value": 25.0}]
    r1 = mhs.check_device_batch(cmds)
    r2 = mhs.check_device_batch([{"action": "write", "param": "temperature_c", "value": 26.0}])
    assert r1["audit_sha256"] != r2["audit_sha256"]


# ---------------------------------------------------------------------------
# handler integration (three-state exit, deny + guide)
# ---------------------------------------------------------------------------

class _FakeToolUse(dict):
    pass


class _FakeEvent:
    def __init__(self, tool_use):
        self.tool_use = tool_use


def _deny_cls():
    return mhs.Deny if hasattr(mhs, "Deny") else None


def test_handler_deny_on_violation():
    g = mhs.MHSActionGuard(mode="deny")
    ev = _FakeEvent({"name": "set_temperature",
                     "input": {"param": "temperature_c", "value": -300.0}})
    out = g._on_hook(ev)
    assert type(out).__name__ == "Deny"
    assert "below absolute zero" in getattr(out, "reason", "") or "veto" in getattr(out, "reason", "")


def test_handler_guide_mode():
    g = mhs.MHSActionGuard(mode="guide")
    ev = _FakeEvent({"name": "set_temperature",
                     "input": {"param": "temperature_c", "value": -300.0}})
    out = g._on_hook(ev)
    assert type(out).__name__ == "Guide"
    assert getattr(g.stats, "guided") == 1


def test_handler_pass_on_clean_device_call():
    g = mhs.MHSActionGuard(mode="deny")
    ev = _FakeEvent({"name": "set_temperature",
                     "input": {"param": "temperature_c", "value": 25.0}})
    out = g._on_hook(ev)
    assert type(out).__name__ == "Proceed"
    assert g.stats.allowed == 1


def test_handler_ignores_non_device_tools():
    g = mhs.MHSActionGuard(mode="deny")
    ev = _FakeEvent({"name": "calculator",
                     "input": {"param": "temperature_c", "value": -300.0}})
    out = g._on_hook(ev)
    assert type(out).__name__ == "Proceed"


def test_handler_passes_on_irrelevant_event():
    g = mhs.MHSActionGuard(mode="deny")
    out = g._on_hook(_FakeEvent({"other": 1}))
    assert type(out).__name__ == "Proceed"


def test_mode_validation():
    with pytest.raises(ValueError):
        mhs.MHSActionGuard(mode="yolo")
