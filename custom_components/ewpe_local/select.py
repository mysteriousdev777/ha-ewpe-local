"""Fan speed."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .entity import EwpeEntity
from .protocol import FAN, decode, encode

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EwpeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if FAN in coordinator.model.columns:
        async_add_entities([EwpeFanSpeed(coordinator)])


class EwpeFanSpeed(EwpeEntity, SelectEntity):
    """`WdSpd`; locked in some modes (in Dry the device keeps High)."""

    _attr_translation_key = "fan_speed"

    def __init__(self, coordinator: EwpeCoordinator) -> None:
        super().__init__(coordinator, "fan_speed")
        self._attr_options = list(coordinator.model.fan_speeds.values())

    @property
    def current_option(self) -> str | None:
        return decode(self.coordinator.model.fan_speeds, self.raw(FAN))

    async def async_select_option(self, option: str) -> None:
        # HA checks the option against `options`, so the code exists.
        code = encode(self.coordinator.model.fan_speeds, option)
        assert code is not None
        await self.coordinator.async_set(FAN, code)
