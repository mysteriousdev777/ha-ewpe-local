"""Water tank full, filter cleaning reminder, a fault other than the full tank."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .entity import EwpeEntity
from .protocol import ERROR, FILTER, TANK_FULL, as_bool, fault

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EwpeBinarySensorDescription(BinarySensorEntityDescription):
    columns: tuple[str, ...]  # the model polls them all, or there is no entity
    value_fn: Callable[[Mapping[str, Any]], bool | None]


BINARY_SENSORS: tuple[EwpeBinarySensorDescription, ...] = (
    # Also on with the tank removed. Mute silences the device's own alarm for it.
    EwpeBinarySensorDescription(
        key="water_tank_full",
        columns=(TANK_FULL,),
        value_fn=lambda data: as_bool(data.get(TANK_FULL)),
        translation_key="water_tank_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    # Every 250 hours of work; the "Reset filter reminder" button clears it.
    EwpeBinarySensorDescription(
        key="filter",
        columns=(FILTER,),
        value_fn=lambda data: as_bool(data.get(FILTER)),
        translation_key="filter",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    # The error flag, which the full tank sets too, without the tank: a malfunction.
    # Its code shows only on the device's display.
    EwpeBinarySensorDescription(
        key="fault",
        columns=(ERROR, TANK_FULL),
        value_fn=lambda data: fault(data.get(ERROR), data.get(TANK_FULL)),
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EwpeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EwpeBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if all(column in coordinator.model.columns for column in description.columns)
    )


class EwpeBinarySensor(EwpeEntity, BinarySensorEntity):
    entity_description: EwpeBinarySensorDescription

    def __init__(
        self, coordinator: EwpeCoordinator, description: EwpeBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)
