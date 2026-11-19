"""Add a device (search or IP), reconfigure its address; options: the polling.

The search is one broadcast scan while adding, with the device picked by the user. The
address is never changed by itself later: the protocol does not authenticate devices.
"""

from __future__ import annotations

from datetime import timedelta
from ipaddress import IPv4Address
from typing import Any

import voluptuous as vol

from homeassistant.components import network
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_SCAN_INTERVAL, UnitOfTime
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .client import EwpeError, EwpePortClosed, discover, handshake, scan
from .const import (
    CONF_CIPHER,
    CONF_MID,
    CONF_VENDER,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .models import DEHUMIDIFIER_MIDS, VENDER_MIDS, find_model
from .packets import DeviceInfo
from .protocol import POWER, VENDER, normalize_vender

USER_SCHEMA = vol.Schema({vol.Optional(CONF_HOST, default=""): str})  # empty: search
HOST_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=MAX_SCAN_INTERVAL,
                step=5,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement=UnitOfTime.SECONDS,
            )
        )
    }
)


class InvalidHost(ValueError):
    """Not an IPv4 address."""


class EwpeLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Search or scan one address, check the model, find the cipher."""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, DeviceInfo] = {}  # search results by MAC

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EwpeOptionsFlow:
        return EwpeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """An address, or an empty field for a search."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if host := user_input.get(CONF_HOST, "").strip():
                try:
                    title, data = await self._async_probe(host)
                except InvalidHost:
                    errors[CONF_HOST] = "invalid_host"
                except EwpePortClosed:
                    errors["base"] = "port_closed"
                except EwpeError:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(title=title, data=data)
            else:
                self._found = await self._async_search()
                if self._found:
                    return await self.async_step_pick_device()
                errors["base"] = "no_devices_found"
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One of the dehumidifiers the search found."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                title, data = await self._async_add(self._found[user_input[CONF_MAC]])
            except EwpeError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=title, data=data)
        devices = {
            mac: f"{info.ip} ({format_mac(mac)})" for mac, info in self._found.items()
        }
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema({vol.Required(CONF_MAC): vol.In(devices)}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                _, data = await self._async_probe(user_input[CONF_HOST])
            except InvalidHost:
                errors[CONF_HOST] = "invalid_host"
            except EwpePortClosed:
                errors["base"] = "port_closed"
            except EwpeError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                HOST_SCHEMA, user_input or {CONF_HOST: entry.data[CONF_HOST]}
            ),
            errors=errors,
        )

    async def _async_search(self) -> dict[str, DeviceInfo]:
        """Dehumidifiers that answer a broadcast scan and are not added yet, by MAC."""
        addresses = await network.async_get_ipv4_broadcast_addresses(self.hass)
        added = self._async_current_ids()
        found: dict[str, DeviceInfo] = {}
        for info in sorted(
            await discover(addresses), key=lambda info: IPv4Address(info.ip)
        ):
            if info.mac not in added and info.mid in DEHUMIDIFIER_MIDS:
                found[info.mac] = info
        return found

    async def _async_probe(self, host: str) -> tuple[str, dict[str, Any]]:
        """Title and entry data for the device at an address the user entered.

        Raises InvalidHost, EwpePortClosed (the address refuses the port), EwpeError
        (no answer) or AbortFlow (as _async_add).
        """
        try:
            address = str(IPv4Address(host.strip()))
        except ValueError as err:
            raise InvalidHost from err
        return await self._async_add(await scan(address))

    async def _async_add(self, info: DeviceInfo) -> tuple[str, dict[str, Any]]:
        """Title and entry data for a device that answered a scan.

        Raises EwpeError (no bind or status reply) or AbortFlow (another device, one
        already added, not a dehumidifier, a model not supported yet).
        """
        mac = info.mac  # normalized by the scan
        await self.async_set_unique_id(mac)
        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch(reason="wrong_device")
        else:
            self._abort_if_unique_id_configured(updates={CONF_HOST: info.ip})

        mid = info.mid
        if mid not in DEHUMIDIFIER_MIDS:
            raise AbortFlow("not_dehumidifier", {"mid": str(mid)})
        columns = [POWER, VENDER] if mid in VENDER_MIDS else [POWER]
        cipher, status = await handshake(info, columns)
        vender = normalize_vender(status.get(VENDER)) if mid in VENDER_MIDS else None
        model = find_model(mid, vender)
        if model is None:
            raise AbortFlow("unsupported_model", {"mid": mid, "vender": vender or "-"})

        return model.name, {
            CONF_HOST: info.ip,
            CONF_MAC: mac,
            CONF_MID: mid,
            CONF_VENDER: vender,
            CONF_CIPHER: cipher,
        }


class EwpeOptionsFlow(OptionsFlow):
    """The polling interval; a loaded entry takes it from its next poll, no reload."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # The selector checks the limits; it gives a float.
            seconds = int(user_input[CONF_SCAN_INTERVAL])
            entry = self.config_entry
            if entry.state is ConfigEntryState.LOADED:
                entry.runtime_data.update_interval = timedelta(seconds=seconds)
            return self.async_create_entry(data={CONF_SCAN_INTERVAL: seconds})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
