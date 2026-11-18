"""Base entity: one device, one coordinator."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EwpeCoordinator


class EwpeEntity(CoordinatorEntity[EwpeCoordinator]):
    """An entity of the device; `key` makes its unique id."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EwpeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        self._attr_device_info = coordinator.device_info

    def raw(self, column: str) -> Any:
        """A status value as the device reported it."""
        return self.coordinator.data.get(column)
