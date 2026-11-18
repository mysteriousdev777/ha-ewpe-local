"""Fixtures: a fake module on a local UDP port, fast timeouts, a set-up entry."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewpe_local import client
from custom_components.ewpe_local.const import (
    CONF_CIPHER,
    CONF_MID,
    CONF_VENDER,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant

from .fake_module import MAC, FakeModule, start_module

FAST = 0.2  # seconds: reply and bind timeouts in tests


@pytest.fixture
def fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "REPLY_TIMEOUT", FAST)
    monkeypatch.setattr(client, "BIND_TIMEOUT", FAST)
    monkeypatch.setattr(client, "SCAN_TIMEOUT", FAST)


@pytest.fixture
def module_kwargs() -> dict[str, Any]:
    """What the fake module is; tests override this fixture."""
    return {}


@pytest.fixture
async def module(
    socket_enabled: None,
    fast_timeouts: None,
    monkeypatch: pytest.MonkeyPatch,
    module_kwargs: dict[str, Any],
) -> AsyncGenerator[FakeModule]:
    fake = await start_module(**module_kwargs)
    monkeypatch.setattr(client, "PORT", fake.port)
    yield fake
    fake.close()


@pytest.fixture
def entry_data(module: FakeModule) -> dict[str, Any]:
    return {
        CONF_HOST: "127.0.0.1",
        CONF_MAC: MAC,
        CONF_MID: module.mid,
        CONF_VENDER: module.status.get("vender"),
        CONF_CIPHER: module.cipher,
    }


@pytest.fixture
def entry_options() -> dict[str, Any]:
    """The entry's options; tests override this fixture."""
    return {}


@pytest.fixture
async def entry(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
    entry_options: dict[str, Any],
) -> AsyncGenerator[MockConfigEntry]:
    """The entry, set up; unloaded at the end so its socket closes."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dehumidifier",
        unique_id=MAC,
        data=entry_data,
        options=entry_options,
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    if config_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()
