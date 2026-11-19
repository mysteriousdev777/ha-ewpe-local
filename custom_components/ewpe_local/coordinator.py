"""Polls the device and sends commands for the entities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_SCAN_INTERVAL
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import EwpeClient, EwpeError
from .const import (
    CONF_MID,
    CONFIRM_DELAY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .models import Model
from .protocol import (
    FIRMWARE,
    MODE,
    POWER,
    TANK_FULL,
    as_bool,
    as_int,
    decode,
    firmware_version,
)

_LOGGER = logging.getLogger(__name__)

# Keys of one command: the mode first, as it may change other keys itself (Dry turns
# the fan to High); the power last, so the device turns on or off with the settings
# of the command (tested with the fan).
_COMMAND_ORDER: Mapping[str, int] = {MODE: -1, POWER: 1}

type EwpeConfigEntry = ConfigEntry[EwpeCoordinator]


def scan_interval(entry: ConfigEntry) -> timedelta:
    """The polling interval of the entry's options, kept within the limits."""
    seconds = entry.options.get(CONF_SCAN_INTERVAL)
    if type(seconds) is not int:
        seconds = DEFAULT_SCAN_INTERVAL
    return timedelta(seconds=min(max(seconds, MIN_SCAN_INTERVAL), MAX_SCAN_INTERVAL))


class EwpeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """The device's status keys, as it reports them."""

    config_entry: EwpeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EwpeConfigEntry,
        client: EwpeClient,
        model: Model,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=scan_interval(entry),
            always_update=False,
        )
        self.client = client
        self.model = model
        self.mac: str = entry.data[CONF_MAC]
        self.firmware: str | None = None
        self._confirm_job = HassJob(self._async_confirm, cancel_on_shutdown=True)
        self._unsub_confirm: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.mac)},
            connections={(CONNECTION_NETWORK_MAC, format_mac(self.mac))},
            manufacturer="Gree",
            model=self.model.name,
            model_id=self.config_entry.data[CONF_MID],
            name=self.config_entry.title,
            sw_version=self.firmware,
        )

    @property
    def mode(self) -> str | None:
        """The current mode name."""
        return decode(self.model.modes, self.data.get(MODE))

    def locked(self, column: str) -> bool:
        """Whether the current mode keeps a key from changing."""
        return self.model.locked(column, self.mode)

    def _lock(
        self, column: str, mode: str | None
    ) -> tuple[str, dict[str, str] | None] | None:
        """Why the panel would not let a key change in a mode: error key, placeholders."""
        if self.model.tank_locked(column, as_bool(self.data.get(TANK_FULL))):
            return "tank_full", None
        if self.model.locked(column, mode):
            return "locked", {"mode": str(mode)}
        return None

    def _mode_after(self, values: Mapping[str, int]) -> str | None:
        """The mode once a command with these keys is done."""
        return decode(self.model.modes, values[MODE]) if MODE in values else self.mode

    def _kept(self, column: str, mode: str | None) -> int | None:
        """A key's value in a mode, unless a command sets it: the mode's own or as is."""
        forced = self.model.forced(column, mode)
        return as_int(self.data.get(column)) if forced is None else forced

    def _refusal(
        self, values: Mapping[str, int]
    ) -> tuple[str, dict[str, str] | None] | None:
        """Why the panel would refuse a command: the lock of a key it would change.

        A key is compared with its value once the command is done: Dry with the fan at
        High asks for nothing Dry does not do itself, whatever the fan was before.
        """
        mode = self._mode_after(values)
        for column, value in values.items():
            if value != self._kept(column, mode) and (lock := self._lock(column, mode)):
                return lock
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        columns = self.model.columns
        if self.data is None:  # the first read also asks for the firmware
            columns = (*columns, FIRMWARE)
        try:
            try:
                data = await self.client.status(columns)
            except EwpeError:
                # The module answers late while its cloud thread blocks: ask once more
                # before the device counts as unavailable.
                _LOGGER.debug("No status from %s, asking again", self.client.host)
                data = await self.client.status(columns)
        except EwpeError as err:
            if self.client.closed:  # unloading: keep what we have, quietly
                return self.data or {}
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="no_answer",
                translation_placeholders={"host": self.client.host},
            ) from err
        if FIRMWARE in data:
            self.firmware = firmware_version(data.pop(FIRMWARE))
        return data

    async def async_set(self, column: str, value: int) -> None:
        """Write one status key, then read the device back."""
        await self.async_set_many({column: value})

    async def async_set_many(self, values: Mapping[str, int]) -> None:
        """Write status keys in one command, then read the device back.

        One command, one beep of the device. The panel's locks are those of the mode the
        device is in once the command is done: from Dry, a command with Dehumidify may
        set the target.
        """
        if self._refusal(values) is not None:
            # The tank may have been emptied, or the mode changed on the panel, since
            # the last poll: read the device once before refusing.
            await self.async_refresh()
        if (refusal := self._refusal(values)) is not None:
            translation_key, placeholders = refusal
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders=placeholders,
            )
        # What a locked key has anyway (High in Dry, Health on in Purify) is not sent.
        mode = self._mode_after(values)
        command = {
            column: value
            for column, value in sorted(
                values.items(), key=lambda item: _COMMAND_ORDER.get(item[0], 0)
            )
            if self._lock(column, mode) is None
        }
        if not command:
            return
        try:
            echo = await self.client.command(command)
        except EwpeError as err:
            # The device may have done it and only the reply got lost: read it back.
            if not self.client.closed:
                self._schedule_confirm()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_answer",
                translation_placeholders={"host": self.client.host},
            ) from err
        # The echo is what the module passed on; the read-back below tells what the
        # device did with it.
        self.async_set_updated_data({**self.data, **echo})
        self._schedule_confirm()

    @callback
    def _schedule_confirm(self) -> None:
        """Read back CONFIRM_DELAY after the last command."""
        if self._unsub_confirm is not None:
            self._unsub_confirm()
        self._unsub_confirm = async_call_later(
            self.hass, CONFIRM_DELAY, self._confirm_job
        )

    async def _async_confirm(self, _now: datetime) -> None:
        self._unsub_confirm = None
        await self.async_refresh()

    async def async_shutdown(self) -> None:
        """Stop polling, forget a pending read-back, close the socket."""
        await super().async_shutdown()
        if self._unsub_confirm is not None:
            self._unsub_confirm()
            self._unsub_confirm = None
        self.client.close()
