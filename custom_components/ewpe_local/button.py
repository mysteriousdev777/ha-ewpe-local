"""Reset the filter cleaning reminder."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .entity import EwpeEntity
from .protocol import FILTER

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EwpeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if FILTER in coordinator.model.columns:
        async_add_entities([EwpeResetFilter(coordinator)])


class EwpeResetFilter(EwpeEntity, ButtonEntity):
    """Writes `Dfltr` 0, as the app's Close button on the reminder does."""

    _attr_translation_key = "reset_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EwpeCoordinator) -> None:
        super().__init__(coordinator, "reset_filter")

    async def async_press(self) -> None:
        await self.coordinator.async_set(FILTER, 0)
