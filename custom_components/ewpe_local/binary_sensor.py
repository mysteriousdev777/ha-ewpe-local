"""Water tank full, filter cleaning reminder."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .entity import EwpeEntity
from .protocol import FILTER, TANK_FULL, as_bool

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EwpeBinarySensorDescription(BinarySensorEntityDescription):
    column: str


BINARY_SENSORS: tuple[EwpeBinarySensorDescription, ...] = (
    # Also on with the tank removed. Mute silences the device's own alarm for it.
    EwpeBinarySensorDescription(
        key="water_tank_full",
        column=TANK_FULL,
        translation_key="water_tank_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    # Every 250 hours of work; the "Reset filter reminder" button clears it.
    EwpeBinarySensorDescription(
        key="filter",
        column=FILTER,
        translation_key="filter",
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
        if description.column in coordinator.model.columns
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
        return as_bool(self.raw(self.entity_description.column))
