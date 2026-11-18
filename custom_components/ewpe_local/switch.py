"""Swing, Health, lights, mute, child lock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .entity import EwpeEntity
from .protocol import (
    AMBIENT_LIGHT,
    CHILD_LOCK,
    HEALTH,
    LIGHT,
    MUTE,
    SWING,
    as_bool,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EwpeSwitchDescription(SwitchEntityDescription):
    column: str
    on_value: int = 1  # off is 0


SWITCHES: tuple[EwpeSwitchDescription, ...] = (
    EwpeSwitchDescription(key="swing", column=SWING, translation_key="swing"),
    # From the network, on means both UV and ion; Purify keeps it on.
    EwpeSwitchDescription(key="health", column=HEALTH, translation_key="health"),
    # Off: the display; on: the display and the air quality light.
    EwpeSwitchDescription(
        key="light",
        column=LIGHT,
        translation_key="light",
        entity_category=EntityCategory.CONFIG,
    ),
    EwpeSwitchDescription(
        key="ambient_light",
        column=AMBIENT_LIGHT,
        translation_key="ambient_light",
        entity_category=EntityCategory.CONFIG,
    ),
    # Silences the beeps, also for commands from Home Assistant.
    EwpeSwitchDescription(
        key="mute",
        column=MUTE,
        translation_key="mute",
        on_value=2,
        entity_category=EntityCategory.CONFIG,
    ),
    EwpeSwitchDescription(
        key="child_lock",
        column=CHILD_LOCK,
        translation_key="child_lock",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EwpeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EwpeSwitch(coordinator, description)
        for description in SWITCHES
        if description.column in coordinator.model.columns
    )


class EwpeSwitch(EwpeEntity, SwitchEntity):
    entity_description: EwpeSwitchDescription

    def __init__(
        self, coordinator: EwpeCoordinator, description: EwpeSwitchDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return as_bool(self.raw(self.entity_description.column))

    async def async_turn_on(self, **kwargs: Any) -> None:
        description = self.entity_description
        await self.coordinator.async_set(description.column, description.on_value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(self.entity_description.column, 0)
