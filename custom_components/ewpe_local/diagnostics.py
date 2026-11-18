"""Diagnostics: the model, the firmware and the raw status (no address, no MAC)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant

from .coordinator import EwpeConfigEntry

TO_REDACT = {CONF_HOST, CONF_MAC}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EwpeConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "model": coordinator.model.key,
        "firmware": coordinator.firmware,
        "last_update_success": coordinator.last_update_success,
        "status": coordinator.data,
    }
