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
    # Mode → status keys the device sets itself when it enters the mode from another one,
    # over the same keys of that command.
    on_enter: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    # Status keys the panel does not let change while the water tank is full.
    tank_locks: frozenset[str] = frozenset()

    def locked(self, column: str, mode: str | None) -> bool:
        """Whether a key cannot be changed in a mode."""
        return mode is not None and mode in self.locks.get(column, frozenset())

    def entered(self, mode: str | None) -> Mapping[str, int]:
        """What the device sets itself on entering a mode from another one."""
        return {} if mode is None else self.on_enter.get(mode, {})

    def tank_locked(self, column: str, tank_full: bool | None) -> bool:
        """Whether a key cannot be changed because the water tank is full."""
        return tank_full is True and column in self.tank_locks


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
    # Checked on the device: Dry sets the target to 50 % and the fan to High, Dehumidify
    # the fan to High; Purify turns Health on (the manual, the app's panel) and keeps the
    # fan and the target.
    on_enter={
        "dry": {TARGET: 7, FAN: 5},
        "dehumidify": {FAN: 5},
        "purify": {HEALTH: 1},
    },
    # Everything but the power and the child lock (and the filter reset, not a panel
    # button), as in the app's panel; also the ambient light of the 13806 variant.
    # Not checked on the device. With the power off the panel locks most keys too, but
    # the device keeps what it gets then and applies it when turned on: not locked here.
    tank_locks=frozenset(
        {MODE, TARGET, FAN, LIGHT, MUTE, SWING, HEALTH, AMBIENT_LIGHT}
    ),
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
