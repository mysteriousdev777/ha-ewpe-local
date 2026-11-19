"""EWPE Local: Gree-platform dehumidifiers over the local network."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_MAC, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .client import EwpeClient
from .const import CONF_CIPHER, CONF_MID, CONF_VENDER, DOMAIN
from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .models import find_model

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.HUMIDIFIER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: EwpeConfigEntry) -> bool:
    data = entry.data
    model = find_model(data[CONF_MID], data.get(CONF_VENDER))
    if model is None:  # only if a later version drops a model
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unsupported_model",
            translation_placeholders={"mid": str(data[CONF_MID])},
        )
    client = EwpeClient(data[CONF_HOST], data[CONF_MAC], data[CONF_CIPHER])
    coordinator = EwpeCoordinator(hass, entry, client, model)
    # Closes the client on unload, and also if the first refresh fails.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # No update listener: a new address reloads the entry through Home Assistant
    # (reconfigure, or an address found again while adding), which warns about update
    # listeners there; a new polling interval goes to the coordinator (options flow).
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EwpeConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
