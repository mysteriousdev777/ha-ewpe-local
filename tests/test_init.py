"""Set-up, retry, unload, diagnostics."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewpe_local.client import EwpeClient
from custom_components.ewpe_local.const import CONF_MID, DOMAIN
from custom_components.ewpe_local.diagnostics import async_get_config_entry_diagnostics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .fake_module import MAC, FakeModule


async def test_setup_and_unload(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.firmware == "U-WB05RT13V1.45"

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, MAC)}
    assert (device.manufacturer, device.model, device.model_id) == (
        "Gree",
        "Dehumidifier",
        "13805",
    )
    assert device.sw_version == "U-WB05RT13V1.45"
    assert device.name == "Dehumidifier"

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(entities) == 17  # 13805: no ambient light

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert coordinator.client.closed


@pytest.mark.skipif(
    not os.path.isdir("/proc/self/fd"), reason="counts open files on Linux"
)
async def test_reloads_leave_no_sockets(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    def open_files() -> int:
        return len(os.listdir("/proc/self/fd"))

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    before = open_files()
    for _ in range(5):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert open_files() == before
    assert entry.state is ConfigEntryState.LOADED


async def test_polls_keep_the_session(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    await entry.runtime_data.async_refresh()
    await entry.runtime_data.async_refresh()
    assert module.requests.count("bind") == 1


async def test_retry_when_the_device_is_silent(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    module: FakeModule,
    entry_data: dict[str, Any],
) -> None:
    module.silent = True
    clients: list[EwpeClient] = []

    def make(*args: Any) -> EwpeClient:
        clients.append(EwpeClient(*args))
        return clients[-1]

    config_entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data=entry_data)
    config_entry.add_to_hass(hass)
    with patch("custom_components.ewpe_local.EwpeClient", side_effect=make):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert len(clients) == 1 and clients[0].closed  # no socket left behind


async def test_model_dropped_by_a_later_version(
    hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict[str, Any]
) -> None:
    config_entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, data={**entry_data, CONF_MID: "13800"}
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_diagnostics_hide_address_and_mac(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"]["host"] == "**REDACTED**"
    assert diagnostics["entry"]["mac"] == "**REDACTED**"
    assert diagnostics["model"] == "13805"
    assert diagnostics["status"]["Dwet"] == 7
    assert "hid" not in diagnostics["status"]


@pytest.mark.parametrize(
    "module_kwargs",
    [{"mid": "13806", "status": {"Pow": 0, "vender": "7b000001", "Dazzling": 1}}],
)
async def test_13806_has_the_ambient_light(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    assert entry.runtime_data.model.key == "13806-ambient"
    assert hass.states.get("switch.dehumidifier_ambient_light").state == "on"
