"""Polling interval: options form without a reload, polling at the interval or off."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ewpe_local.const import DOMAIN
from custom_components.ewpe_local.coordinator import scan_interval
from homeassistant.const import ATTR_ENTITY_ID, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .fake_module import MAC, FakeModule

DEHUMIDIFIER = "humidifier.dehumidifier"


def statuses(module: FakeModule) -> int:
    return module.requests.count("status")


async def later(hass: HomeAssistant, start: datetime, seconds: int) -> None:
    async_fire_time_changed(hass, start + timedelta(seconds=seconds))
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_options_set_the_interval(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    coordinator = entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=30)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({}) == {CONF_SCAN_INTERVAL: 30}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 60}
    await hass.async_block_till_done()
    # No reload: the same coordinator takes the interval from its next poll.
    assert entry.runtime_data is coordinator
    assert not coordinator.client.closed
    assert coordinator.update_interval == timedelta(seconds=60)


async def test_options_of_an_entry_not_loaded(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
) -> None:
    """Saved for the next setup; no coordinator to tell."""
    config_entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=entry_data)
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SCAN_INTERVAL: 60}


async def test_no_update_listener(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Home Assistant reloads the entry after Reconfigure; an update listener there is
    reported (it stops working in Home Assistant 2026.12)."""
    assert not entry.update_listeners


@pytest.mark.parametrize("seconds", [10, 301])
async def test_options_limits(
    hass: HomeAssistant, entry: MockConfigEntry, seconds: int
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SCAN_INTERVAL: seconds}
        )
    hass.config_entries.options.async_abort(result["flow_id"])
    assert entry.options == {}


@pytest.mark.parametrize("entry_options", [{CONF_SCAN_INTERVAL: 60}])
async def test_polls_at_the_interval(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    start = dt_util.utcnow()
    before = statuses(module)
    await later(hass, start, 31)
    assert statuses(module) == before
    await later(hass, start, 61)
    assert statuses(module) == before + 1


async def test_other_entry_updates_do_not_reload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    coordinator = entry.runtime_data
    hass.config_entries.async_update_entry(entry, title="Basement")
    await hass.async_block_till_done()
    assert entry.runtime_data is coordinator
    assert not coordinator.client.closed


async def test_polling_turned_off_in_home_assistant(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    module: FakeModule,
    entry_data: dict[str, Any],
) -> None:
    """Polling off in System options: no polls, but update_entity still reads."""
    assert await async_setup_component(hass, "homeassistant", {})
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dehumidifier",
        unique_id=MAC,
        data=entry_data,
        pref_disable_polling=True,
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    before = statuses(module)
    await later(hass, dt_util.utcnow(), 301)
    assert statuses(module) == before

    await hass.services.async_call(
        "homeassistant", "update_entity", {ATTR_ENTITY_ID: DEHUMIDIFIER}, blocking=True
    )
    await hass.async_block_till_done()
    assert statuses(module) == before + 1
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("options", "seconds"),
    [
        ({}, 30),
        ({CONF_SCAN_INTERVAL: 60}, 60),
        ({CONF_SCAN_INTERVAL: 5}, 15),  # stored by hand: kept within the limits
        ({CONF_SCAN_INTERVAL: 1000}, 300),
        ({CONF_SCAN_INTERVAL: "60"}, 30),
        ({CONF_SCAN_INTERVAL: True}, 30),
    ],
)
def test_scan_interval(options: dict[str, Any], seconds: int) -> None:
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    assert scan_interval(entry) == timedelta(seconds=seconds)
