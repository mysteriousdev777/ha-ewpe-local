"""Humidity, temperature, air quality levels, error code, Wi-Fi signal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .entity import EwpeEntity
from .protocol import (
    ERROR,
    HUMIDITY,
    ODOR,
    ODOR_LEVELS,
    PM25,
    PM25_LEVELS,
    RSSI,
    TEMPERATURE,
    as_int,
    decode,
    rssi,
    temperature,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EwpeSensorDescription(SensorEntityDescription):
    column: str
    value: Callable[[Any], StateType]


SENSORS: tuple[EwpeSensorDescription, ...] = (
    EwpeSensorDescription(
        key="humidity",
        column=HUMIDITY,
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value=as_int,
    ),
    EwpeSensorDescription(
        key="temperature",
        column=TEMPERATURE,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value=temperature,
    ),
    EwpeSensorDescription(
        key="pm25_level",
        column=PM25,
        translation_key="pm25_level",
        device_class=SensorDeviceClass.ENUM,
        options=list(PM25_LEVELS.values()),
        value=partial(decode, PM25_LEVELS),
    ),
    EwpeSensorDescription(
        key="odor_level",
        column=ODOR,
        translation_key="odor_level",
        device_class=SensorDeviceClass.ENUM,
        options=list(ODOR_LEVELS.values()),
        value=partial(decode, ODOR_LEVELS),
    ),
    EwpeSensorDescription(
        key="error_code",
        column=ERROR,
        translation_key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=as_int,
    ),
    EwpeSensorDescription(
        key="rssi",
        column=RSSI,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=rssi,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EwpeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EwpeSensor(coordinator, description)
        for description in SENSORS
        if description.column in coordinator.model.columns
    )


class EwpeSensor(EwpeEntity, SensorEntity):
    entity_description: EwpeSensorDescription

    def __init__(
        self, coordinator: EwpeCoordinator, description: EwpeSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value(self.raw(self.entity_description.column))
