"""Config flow, with scan, search and handshake mocked (the client tests cover them)."""

from __future__ import annotations

from collections.abc import Generator
from ipaddress import IPv4Address
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ewpe_local.client import EwpeError
from custom_components.ewpe_local.const import (
    CONF_CIPHER,
    CONF_MID,
    CONF_VENDER,
    DOMAIN,
)
from custom_components.ewpe_local.packets import DeviceInfo
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import format_mac

from .fake_module import MAC

HOST = "192.0.2.10"  # documentation range (RFC 5737)
NEW_HOST = "192.0.2.11"
OTHER_MAC = "aabbccdd0001"
BROADCAST = {IPv4Address("255.255.255.255"), IPv4Address("192.0.2.255")}

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "no_setup")


@pytest.fixture
def no_setup() -> Generator[AsyncMock]:
    with patch(
        "custom_components.ewpe_local.async_setup_entry", return_value=True
    ) as setup:
        yield setup


def device(mid: str | None = "13805", host: str = HOST, mac: str = MAC) -> DeviceInfo:
    return DeviceInfo(host, 7000, mac, mid)


@pytest.fixture
def scan() -> Generator[AsyncMock]:
    with patch(
        "custom_components.ewpe_local.config_flow.scan", return_value=device()
    ) as mock:
        yield mock


@pytest.fixture
def handshake() -> Generator[AsyncMock]:
    with patch(
        "custom_components.ewpe_local.config_flow.handshake",
        return_value=(2, {"Pow": 1}),
    ) as mock:
        yield mock


@pytest.fixture
def search() -> Generator[AsyncMock]:
    """The search: the broadcast addresses Home Assistant gives, and the replies."""
    with (
        patch(
            "homeassistant.components.network.async_get_ipv4_broadcast_addresses",
            return_value=BROADCAST,
        ),
        patch(
            "custom_components.ewpe_local.config_flow.discover", return_value=[]
        ) as mock,
    ):
        yield mock


async def start(hass: HomeAssistant, host: str = HOST) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: host}
    )


def choices(result: dict[str, Any]) -> dict[str, str]:
    """The devices offered on the pick step: MAC → label."""
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick_device"
    (validator,) = result["data_schema"].schema.values()
    return validator.container


async def pick(
    hass: HomeAssistant, result: dict[str, Any], mac: str = MAC
) -> dict[str, Any]:
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC: mac}
    )


async def test_add_13805(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock
) -> None:
    result = await start(hass, f" {HOST} ")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dehumidifier"
    assert result["result"].unique_id == MAC
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_MAC: MAC,
        CONF_MID: "13805",
        CONF_VENDER: None,
        CONF_CIPHER: 2,
    }
    scan.assert_awaited_once_with(HOST)
    assert handshake.await_args.args[1] == ["Pow"]


async def test_add_13806_with_ambient_light(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock
) -> None:
    scan.return_value = device("13806")
    handshake.return_value = (2, {"Pow": 0, "vender": "7B000001"})
    result = await start(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_VENDER] == "7b000001"
    assert handshake.await_args.args[1] == ["Pow", "vender"]


@pytest.mark.parametrize(
    ("mid", "vender", "placeholders"),
    [
        ("13806", "default", {"mid": "13806", "vender": "default"}),
        ("13806", None, {"mid": "13806", "vender": "-"}),
        ("13802", None, {"mid": "13802", "vender": "-"}),
    ],
)
async def test_dehumidifier_not_supported_yet(
    hass: HomeAssistant,
    scan: AsyncMock,
    handshake: AsyncMock,
    mid: str,
    vender: str | None,
    placeholders: dict[str, str],
) -> None:
    scan.return_value = device(mid)
    handshake.return_value = (2, {"Pow": 0, "vender": vender} if vender else {"Pow": 0})
    result = await start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_model"
    assert result["description_placeholders"] == placeholders


@pytest.mark.parametrize("mid", ["10001", None])
async def test_not_a_dehumidifier(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock, mid: str | None
) -> None:
    scan.return_value = device(mid)
    result = await start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_dehumidifier"
    handshake.assert_not_awaited()


async def test_invalid_host(hass: HomeAssistant, scan: AsyncMock) -> None:
    result = await start(hass, "dehumidifier.local")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}
    scan.assert_not_awaited()


@pytest.mark.parametrize("failing", ["scan", "handshake"])
async def test_cannot_connect_then_retry(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock, failing: str
) -> None:
    mock = scan if failing == "scan" else handshake
    good = mock.return_value
    mock.side_effect = EwpeError("no reply")
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock.side_effect = None
    mock.return_value = good
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_added_updates_the_address(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    scan.return_value = device(host=NEW_HOST)
    result = await start(hass, NEW_HOST)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == NEW_HOST
    handshake.assert_not_awaited()


async def test_search_and_pick(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock, search: AsyncMock
) -> None:
    search.return_value = [
        device("10001", "192.0.2.5", "aabbccdd0002"),  # an air conditioner
        device(host=NEW_HOST, mac=OTHER_MAC),
        device(host=HOST),
    ]
    result = await start(hass, " ")
    search.assert_awaited_once_with(BROADCAST)
    assert choices(result) == {
        MAC: f"{HOST} ({format_mac(MAC)})",
        OTHER_MAC: f"{NEW_HOST} ({format_mac(OTHER_MAC)})",
    }
    assert list(choices(result)) == [MAC, OTHER_MAC]  # by address

    result = await pick(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == MAC
    assert result["data"][CONF_HOST] == HOST
    assert handshake.await_args.args[0].mac == MAC
    scan.assert_not_awaited()  # the search reply is enough


async def test_search_finds_nothing_new(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock, search: AsyncMock
) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={CONF_HOST: HOST}).add_to_hass(
        hass
    )
    search.return_value = [device(host=HOST), device("10001", NEW_HOST, OTHER_MAC)]
    result = await start(hass, "")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices_found"}

    # The address can be entered on the same form.
    scan.return_value = device(host=NEW_HOST, mac=OTHER_MAC)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_search_lists_a_dehumidifier_not_supported_yet(
    hass: HomeAssistant, handshake: AsyncMock, search: AsyncMock
) -> None:
    search.return_value = [device("13802")]
    result = await start(hass, "")
    assert list(choices(result)) == [MAC]
    result = await pick(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_model"
    assert result["description_placeholders"] == {"mid": "13802", "vender": "-"}


async def test_pick_cannot_connect_then_retry(
    hass: HomeAssistant, handshake: AsyncMock, search: AsyncMock
) -> None:
    search.return_value = [device()]
    result = await start(hass, "")
    good = handshake.return_value
    handshake.side_effect = EwpeError("no reply")
    result = await pick(hass, result)
    assert result["errors"] == {"base": "cannot_connect"}
    assert list(choices(result)) == [MAC]

    handshake.side_effect = None
    handshake.return_value = good
    result = await pick(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pick_a_device_added_meanwhile(
    hass: HomeAssistant, handshake: AsyncMock, search: AsyncMock
) -> None:
    search.return_value = [device(host=NEW_HOST)]
    result = await start(hass, "")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    result = await pick(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == NEW_HOST
    handshake.assert_not_awaited()


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        title="Dehumidifier",
        data={
            CONF_HOST: HOST,
            CONF_MAC: MAC,
            CONF_MID: "13805",
            CONF_VENDER: None,
            CONF_CIPHER: 2,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_reconfigure(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock
) -> None:
    entry = _entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    scan.return_value = device(host=NEW_HOST)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == NEW_HOST


async def test_reconfigure_to_another_device(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock
) -> None:
    entry = _entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    scan.return_value = device(host=NEW_HOST, mac="aabbccdd0000")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data[CONF_HOST] == HOST


async def test_reconfigure_cannot_connect(
    hass: HomeAssistant, scan: AsyncMock, handshake: AsyncMock
) -> None:
    entry = _entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    scan.side_effect = EwpeError("no reply")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
