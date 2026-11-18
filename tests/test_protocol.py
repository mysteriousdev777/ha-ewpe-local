"""Value codes and the model table (no network, no Home Assistant)."""

from __future__ import annotations

import pytest

from custom_components.ewpe_local.models import (
    DEHUMIDIFIER_MIDS,
    VENDER_MIDS,
    find_model,
)
from custom_components.ewpe_local.protocol import (
    AMBIENT_LIGHT,
    CHILD_LOCK,
    FAN,
    FILTER,
    HEALTH,
    LIGHT,
    MODE,
    MUTE,
    ODOR_LEVELS,
    PM25_LEVELS,
    POWER,
    SWING,
    TARGET,
    as_bool,
    as_int,
    code_from_target,
    decode,
    encode,
    fault,
    firmware_version,
    normalize_mac,
    normalize_vender,
    rssi,
    target_from_code,
    temperature,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(7, 7), (0, 0), (True, None), ("7", None), (None, None), (7.0, None)],
)
def test_as_int(value: object, expected: int | None) -> None:
    assert as_int(value) == expected


def test_as_bool() -> None:
    assert as_bool(1) is True
    assert as_bool(2) is True
    assert as_bool(0) is False
    assert as_bool(None) is None


@pytest.mark.parametrize(
    ("error", "tank_full", "expected"),
    [
        (0, 0, False),
        (0, 1, False),
        (0, None, False),  # no error flag: no fault, whatever the tank
        (1, 0, True),
        (2, 0, True),
        (1, 1, False),  # the full tank sets the flag too
        (1, None, None),  # cannot tell
        (None, 0, None),
        ("1", 0, None),
    ],
)
def test_fault_leaves_out_the_full_tank(
    error: object, tank_full: object, expected: bool | None
) -> None:
    assert fault(error, tank_full) is expected


def test_target_codes_match_the_panel() -> None:
    # Checked on the device: 30 % = 3, 50 % = 7, 70 % = 11, 80 % = 13.
    for humidity, code in ((30, 3), (50, 7), (55, 8), (70, 11), (80, 13)):
        assert target_from_code(code) == humidity
        assert code_from_target(humidity) == code
    assert code_from_target(52) == 7
    assert code_from_target(53) == 8
    assert target_from_code(None) is None


def test_temperature_offset() -> None:
    assert temperature(61) == 21
    assert temperature(40) == 0
    assert temperature(0) is None
    assert temperature("61") is None


def test_rssi_only_negative() -> None:
    assert rssi(-55) == -55
    assert rssi(0) is None
    assert rssi(None) is None


def test_levels() -> None:
    assert decode(PM25_LEVELS, 1) == "excellent"
    assert decode(PM25_LEVELS, 3) == "bad"
    assert decode(ODOR_LEVELS, 2) == "slight"
    assert decode(PM25_LEVELS, 0) is None  # the panel hides 0
    assert decode(PM25_LEVELS, [1]) is None
    assert encode(PM25_LEVELS, "good") == 2
    assert encode(PM25_LEVELS, "nope") is None


def test_firmware_version() -> None:
    assert firmware_version("362001065279+U-WB05RT13V1.45.bin") == "U-WB05RT13V1.45"
    assert firmware_version("V1.45") == "V1.45"
    assert firmware_version("") is None
    assert firmware_version(5) is None


def test_normalize() -> None:
    mac = "AA:BB:CC:DD:EE:FF"  # privacy: ignore (test MAC)
    assert normalize_mac(mac) == "aabbccddeeff"
    assert normalize_vender(" 7B000001 ") == "7b000001"
    assert normalize_vender(71000001) == "71000001"
    assert normalize_vender(True) is None
    assert normalize_vender("") is None


def test_13805() -> None:
    model = find_model("13805")
    assert model is not None
    assert model.modes == {2: "dehumidify", 5: "dry", 9: "purify"}
    assert model.fan_speeds == {0: "auto", 1: "low", 3: "medium", 5: "high"}
    assert (model.target_min, model.target_max) == (30, 80)
    assert AMBIENT_LIGHT not in model.columns
    assert MODE in model.columns


def test_13805_locks() -> None:
    model = find_model("13805")
    assert model is not None
    assert model.locked(TARGET, "dry") and model.locked(TARGET, "purify")
    assert not model.locked(TARGET, "dehumidify")
    assert model.locked(FAN, "dry") and not model.locked(FAN, "purify")
    assert model.locked(HEALTH, "purify") and not model.locked(HEALTH, "dry")
    assert not model.locked(TARGET, None)


def test_13805_full_tank_locks_all_but_power_and_child_lock() -> None:
    model = find_model("13805")
    assert model is not None
    for column in (MODE, TARGET, FAN, LIGHT, MUTE, SWING, HEALTH):
        assert model.tank_locked(column, True), column
        assert not model.tank_locked(column, False), column
        assert not model.tank_locked(column, None), column
    for column in (POWER, CHILD_LOCK, FILTER):
        assert not model.tank_locked(column, True), column


@pytest.mark.parametrize("vender", ["71000001", "7b000001", "7B000001"])
def test_13806_ambient_light(vender: str) -> None:
    model = find_model("13806", vender)
    base = find_model("13805")
    assert model is not None and base is not None
    assert model.columns == (*base.columns, AMBIENT_LIGHT)
    assert model.modes == base.modes
    assert model.locks == base.locks
    assert model.tank_locks == base.tank_locks
    assert model.tank_locked(AMBIENT_LIGHT, True)


@pytest.mark.parametrize(
    ("mid", "vender"),
    [
        ("13806", None),
        ("13806", "default"),
        ("13800", None),
        ("13802", None),
        ("13803", None),
        ("13804", None),
        ("10001", None),
        (None, None),
    ],
)
def test_not_supported(mid: str | None, vender: str | None) -> None:
    assert find_model(mid, vender) is None


def test_mid_sets() -> None:
    assert {"13806"} == VENDER_MIDS
    assert VENDER_MIDS <= DEHUMIDIFIER_MIDS
    assert "13805" in DEHUMIDIFIER_MIDS
