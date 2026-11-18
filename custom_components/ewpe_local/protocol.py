"""Status keys of Gree dehumidifiers and their value codes.

Pure Python: no Home Assistant, no network. Values come from the device's JSON, so
every decoder accepts anything and returns None for what it does not expect.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

POWER: Final = "Pow"  # 1 on, 0 off
MODE: Final = "Dmod"  # mode code, per model
TARGET: Final = "Dwet"  # target humidity: 15 + 5 * code, in %
HUMIDITY: Final = "DwatSen"  # current humidity, %
FAN: Final = "WdSpd"  # fan speed code, per model
TANK_FULL: Final = "DwatFul"  # 1: the water tank is full or removed
FILTER: Final = "Dfltr"  # 1: clean the filter (every 250 h); writing 0 resets it
ERROR: Final = "AllErr"  # 0: no error; the tank full counts too
TEMPERATURE: Final = "TemSen"  # air temperature + 40, in °C
LIGHT: Final = "Lig"  # 0: display off; 1: display and air quality light on
SWING: Final = "SwUpDn"  # 1: the louver swings, 0: stopped
HEALTH: Final = "Health"  # 1: UV and ion on (the panel button also picks one of them)
MUTE: Final = "Quiet"  # 2: muted (no beeps, also not for commands), 0: sound on
CHILD_LOCK: Final = "ChildLock"  # 1: panel buttons locked
PM25: Final = "PM2P5"  # PM2.5 level, see PM25_LEVELS
ODOR: Final = "airQlt"  # odor sensor level, see ODOR_LEVELS
AMBIENT_LIGHT: Final = "Dazzling"  # 1: ambient light on
RSSI: Final = "rssi"  # Wi-Fi signal of the module, dBm
VENDER: Final = "vender"  # panel variant of some models
FIRMWARE: Final = "hid"  # "<firmware code>+<module><version>.bin"

TARGET_STEP: Final = 5
TEMPERATURE_OFFSET: Final = 40

PM25_LEVELS: Final[Mapping[int, str]] = {1: "excellent", 2: "good", 3: "bad"}
ODOR_LEVELS: Final[Mapping[int, str]] = {1: "normal", 2: "slight", 3: "serious"}


def as_int(value: Any) -> int | None:
    """An integer value, or None (JSON booleans and strings are not codes)."""
    return value if type(value) is int else None


def as_bool(value: Any) -> bool | None:
    """1 and other non-zero codes are True, 0 is False."""
    code = as_int(value)
    return None if code is None else code != 0


def decode(table: Mapping[int, str], value: Any) -> str | None:
    """The name of a code; None for a missing or unknown one."""
    code = as_int(value)
    return None if code is None else table.get(code)


def encode(table: Mapping[int, str], name: str) -> int | None:
    """The code of a name; None for an unknown one."""
    for code, known in table.items():
        if known == name:
            return code
    return None


def target_from_code(value: Any) -> int | None:
    """Target humidity in % from its code (3 → 30 %, 13 → 80 %)."""
    code = as_int(value)
    return None if code is None else 15 + TARGET_STEP * code


def code_from_target(humidity: float) -> int:
    """The code of a target humidity, rounded to the nearest 5 %."""
    return round((humidity - 15) / TARGET_STEP)


def temperature(value: Any) -> int | None:
    """Air temperature in °C; 0 (that would be -40 °C) means no reading."""
    raw = as_int(value)
    return None if not raw else raw - TEMPERATURE_OFFSET


def rssi(value: Any) -> int | None:
    """Signal strength in dBm; only negative values are readings."""
    level = as_int(value)
    return level if level is not None and level < 0 else None


def firmware_version(value: Any) -> str | None:
    """Module and firmware version from the firmware id.

    "362001065279+U-WB05RT13V1.45.bin" → "U-WB05RT13V1.45".
    """
    if not isinstance(value, str) or not value:
        return None
    version = value.rpartition("+")[2].removesuffix(".bin")
    return version or None


def normalize_vender(value: Any) -> str | None:
    """The panel variant as text, as the panels compare it ("71000001", "7b000001")."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip().lower()
    return text or None


def normalize_mac(value: str) -> str:
    """12 lowercase hex digits, as the module writes its MAC."""
    return value.replace(":", "").replace("-", "").replace(".", "").lower()
