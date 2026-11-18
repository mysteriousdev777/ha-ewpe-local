"""The dehumidifier: power, target humidity, mode."""

from __future__ import annotations

from typing import Any

from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .entity import EwpeEntity
from .protocol import (
    HUMIDITY,
    MODE,
    POWER,
    TARGET,
    TARGET_STEP,
    as_bool,
    as_int,
    code_from_target,
    encode,
    target_from_code,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EwpeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([EwpeDehumidifier(entry.runtime_data)])


class EwpeDehumidifier(EwpeEntity, HumidifierEntity):
    """Power (`Pow`), target humidity (`Dwet`), mode (`Dmod`)."""

    _attr_name = None
    _attr_translation_key = "dehumidifier"
    _attr_device_class = HumidifierDeviceClass.DEHUMIDIFIER
    _attr_supported_features = HumidifierEntityFeature.MODES
    _attr_target_humidity_step = TARGET_STEP

    def __init__(self, coordinator: EwpeCoordinator) -> None:
        super().__init__(coordinator, "dehumidifier")
        model = coordinator.model
        self._attr_min_humidity = model.target_min
        self._attr_max_humidity = model.target_max
        self._attr_available_modes = list(model.modes.values())

    @property
    def is_on(self) -> bool | None:
        return as_bool(self.raw(POWER))

    @property
    def mode(self) -> str | None:
        return self.coordinator.mode

    @property
    def current_humidity(self) -> int | None:
        return as_int(self.raw(HUMIDITY))

    @property
    def target_humidity(self) -> int | None:
        """None in modes without a target (the panel hides it there too)."""
        if self.coordinator.locked(TARGET):
            return None
        return target_from_code(self.raw(TARGET))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(POWER, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(POWER, 0)

    async def async_set_humidity(self, humidity: int) -> None:
        await self.coordinator.async_set(TARGET, code_from_target(humidity))

    async def async_set_mode(self, mode: str) -> None:
        code = encode(self.coordinator.model.modes, mode)
        if code is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_mode",
                translation_placeholders={"mode": mode},
            )
        await self.coordinator.async_set(MODE, code)
