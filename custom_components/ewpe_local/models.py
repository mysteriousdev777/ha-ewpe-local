"""Supported models: what each one polls, its modes, fan speeds and panel rules.

A model is chosen by the device's MID (from the scan reply) and, for MIDs whose panel
depends on it, by its `vender` status value. Entities are created from `columns`: a
module answers for keys its device does not have, so the reply cannot tell.

Pure Python: no Home Assistant, no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Final

from .protocol import (
    AMBIENT_LIGHT,
    CHILD_LOCK,
    ERROR,
    FAN,
    FILTER,
    HEALTH,
    HUMIDITY,
    LIGHT,
    MODE,
    MUTE,
    ODOR,
    PM25,
    POWER,
    RSSI,
    SWING,
    TANK_FULL,
    TARGET,
    TEMPERATURE,
    normalize_vender,
)

# All MIDs of Gree dehumidifiers known to the EWPE Smart app, supported or not.
DEHUMIDIFIER_MIDS: Final = frozenset(
    {"13800", "13801", "13802", "13803", "13804", "13805", "13806"}
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Model:
    """One device panel."""

    key: str  # stable id, for diagnostics
    name: str  # default device name
    columns: tuple[str, ...]  # status keys polled; entities exist for these only
    modes: Mapping[int, str]  # MODE code → mode name
    fan_speeds: Mapping[int, str]  # FAN code → speed name
    target_min: int = 30  # %
    target_max: int = 80  # %
    # Status key → modes in which the panel does not let it change: the device then
    # ignores the value or sets its own (in Dry the fan is always High).
    locks: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def locked(self, column: str, mode: str | None) -> bool:
        """Whether a key cannot be changed in a mode."""
        return mode is not None and mode in self.locks.get(column, frozenset())


# Cooper&Hunter CH-D014WD6-30LD WF and its siblings: checked on the device.
_13805: Final = Model(
    key="13805",
    name="Dehumidifier",
    columns=(
        POWER,
        MODE,
        TARGET,
        HUMIDITY,
        FAN,
        TANK_FULL,
        FILTER,
        ERROR,
        TEMPERATURE,
        LIGHT,
        SWING,
        HEALTH,
        MUTE,
        CHILD_LOCK,
        PM25,
        ODOR,
        RSSI,
    ),
    modes={2: "dehumidify", 5: "dry", 9: "purify"},
    fan_speeds={0: "auto", 1: "low", 3: "medium", 5: "high"},
    locks={
        TARGET: frozenset({"dry", "purify"}),
        FAN: frozenset({"dry"}),
        HEALTH: frozenset({"purify"}),
    },
)

# 13806 with these `vender` values has the 13805 panel plus an ambient light.
_13806_AMBIENT: Final = replace(
    _13805, key="13806-ambient", columns=(*_13805.columns, AMBIENT_LIGHT)
)

_MODELS: Final[Mapping[tuple[str, str | None], Model]] = {
    ("13805", None): _13805,
    ("13806", "71000001"): _13806_AMBIENT,
    ("13806", "7b000001"): _13806_AMBIENT,
}

# MIDs whose model also depends on the `vender` status value.
VENDER_MIDS: Final = frozenset(mid for mid, vender in _MODELS if vender is not None)


def find_model(mid: str | None, vender: object = None) -> Model | None:
    """The supported model of a device, or None."""
    if mid in VENDER_MIDS:
        return _MODELS.get((mid, normalize_vender(vender)))
    return _MODELS.get((mid, None)) if mid is not None else None
