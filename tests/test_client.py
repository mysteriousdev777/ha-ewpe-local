"""The client against the fake module over real UDP on localhost."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from ipaddress import IPv4Address
import socket

import pytest

from custom_components.ewpe_local import client
from custom_components.ewpe_local.client import (
    EwpeClient,
    EwpeError,
    EwpePortClosed,
    discover,
    handshake,
    scan,
)
from custom_components.ewpe_local.packets import DeviceInfo

from .fake_module import MAC, FakeModule, start_module


@pytest.fixture
async def ewpe(module: FakeModule) -> AsyncGenerator[EwpeClient]:
    device = EwpeClient("127.0.0.1", MAC, module.cipher)
    yield device
    device.close()


async def test_status_has_the_known_keys(ewpe: EwpeClient, module: FakeModule) -> None:
    assert await ewpe.status(["Pow", "Dwet", "NoSuchKey"]) == {"Pow": 1, "Dwet": 7}
    assert module.requests[0] == "bind"


async def test_one_bind_for_many_requests(ewpe: EwpeClient, module: FakeModule) -> None:
    """One bind, and no other request than those asked for."""
    for _ in range(3):
        await ewpe.status(["Pow"])
    await asyncio.sleep(0.05)  # room for a request the library would send by itself
    assert module.requests == ["bind", "status", "status", "status"]


async def test_another_reply_is_not_taken(ewpe: EwpeClient, module: FakeModule) -> None:
    """A status reply with other keys (late, or not for this request) is skipped."""
    module.decoy = True
    assert await ewpe.status(["Pow", "DwatSen"]) == {"Pow": 1, "DwatSen": 60}


async def test_command_returns_the_echo(ewpe: EwpeClient, module: FakeModule) -> None:
    assert await ewpe.command({"Dwet": 8}) == {"Dwet": 8}
    assert module.commands == [{"Dwet": 8}]
    assert module.status["Dwet"] == 8


async def test_echo_is_not_the_state(ewpe: EwpeClient, module: FakeModule) -> None:
    """In Dry the device ignores the fan and still echoes it: only a read tells."""
    module.status["Dmod"] = 5
    module.status["WdSpd"] = 5
    assert await ewpe.command({"WdSpd": 1}) == {"WdSpd": 1}
    assert await ewpe.status(["WdSpd"]) == {"WdSpd": 5}


async def test_no_reply_then_a_new_session(
    ewpe: EwpeClient, module: FakeModule
) -> None:
    await ewpe.status(["Pow"])
    module.silent = True
    for _ in range(client.NEW_SESSION_AFTER):
        with pytest.raises(EwpeError):
            await ewpe.status(["Pow"])
    module.silent = False
    assert await ewpe.status(["Pow"]) == {"Pow": 1}
    assert module.requests.count("bind") == 2


async def test_late_reply_after_a_timeout(ewpe: EwpeClient, module: FakeModule) -> None:
    await ewpe.status(["Pow"])
    module.delay = client.REPLY_TIMEOUT * 2
    with pytest.raises(EwpeError):
        await ewpe.status(["Pow"])
    module.delay = 0
    await asyncio.sleep(client.REPLY_TIMEOUT * 2)  # the late reply arrives: ignored
    assert await ewpe.status(["DwatSen"]) == {"DwatSen": 60}


@pytest.mark.parametrize("module_kwargs", [{"cipher": 1}])
async def test_wrong_cipher_gets_no_bind(module: FakeModule) -> None:
    device = EwpeClient("127.0.0.1", MAC, 2)
    try:
        with pytest.raises(EwpeError):
            await device.status(["Pow"])
    finally:
        device.close()
    assert "bind" not in module.requests


@pytest.mark.parametrize("module_kwargs", [{"mac": "aabbccdd0001"}])
async def test_another_device_at_the_address(module: FakeModule) -> None:
    """A device with another MAC answers the bind: its reply is not taken."""
    device = EwpeClient("127.0.0.1", MAC, module.cipher)
    try:
        with pytest.raises(EwpeError):
            await device.status(["Pow"])
    finally:
        device.close()
    assert module.requests == ["bind"]


async def test_close_fails_a_waiting_request(
    ewpe: EwpeClient, module: FakeModule
) -> None:
    await ewpe.status(["Pow"])
    module.delay = 10
    request = asyncio.create_task(ewpe.status(["Pow"]))
    await asyncio.sleep(0.05)
    ewpe.close()
    with pytest.raises(EwpeError, match="closed"):
        await request
    with pytest.raises(EwpeError, match="closed"):
        await ewpe.status(["Pow"])
    assert ewpe.closed


async def test_requests_are_one_at_a_time(ewpe: EwpeClient, module: FakeModule) -> None:
    replies = await asyncio.gather(
        ewpe.status(["Pow"]), ewpe.command({"Lig": 0}), ewpe.status(["Lig"])
    )
    assert replies == [{"Pow": 1}, {"Lig": 0}, {"Lig": 0}]


@pytest.mark.parametrize("module_kwargs", [{"cipher": 1}, {"cipher": 2}])
async def test_handshake_finds_the_cipher(module: FakeModule) -> None:
    info = DeviceInfo("127.0.0.1", module.port, MAC, "13805")
    cipher, status = await handshake(info, ["Pow", "vender"])
    assert cipher == module.cipher
    assert status == {"Pow": 1}


async def test_handshake_without_answer(module: FakeModule) -> None:
    module.silent = True
    info = DeviceInfo("127.0.0.1", module.port, MAC, "13805")
    with pytest.raises(EwpeError):
        await handshake(info, ["Pow"])


@pytest.fixture
async def module_on_7000(
    socket_enabled: None, fast_timeouts: None
) -> AsyncGenerator[FakeModule]:
    """Scan always goes to port 7000."""
    try:
        fake = await start_module(port=client.PORT)
    except OSError:
        pytest.skip("UDP port 7000 is taken")
    yield fake
    fake.close()


async def test_scan(module_on_7000: FakeModule) -> None:
    info = await scan("127.0.0.1")
    assert (info.ip, info.mac, info.mid) == ("127.0.0.1", MAC, "13805")


async def test_scan_without_answer(module_on_7000: FakeModule) -> None:
    module_on_7000.silent = True
    with pytest.raises(EwpeError) as err:
        await scan("127.0.0.1")
    assert not isinstance(err.value, EwpePortClosed)  # the port is open


async def test_scan_of_a_refused_port(
    socket_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing listens on the port: the scan says so at once, not after its timeout."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    monkeypatch.setattr(client, "PORT", port)
    monkeypatch.setattr(client, "SCAN_TIMEOUT", 30)
    async with asyncio.timeout(5):
        with pytest.raises(EwpePortClosed):
            await scan("127.0.0.1")


async def test_discover_one_entry_per_device(module_on_7000: FakeModule) -> None:
    try:
        other = await start_module(
            "127.0.0.2", client.PORT, mac="aabbccdd0001", mid="10001"
        )
    except OSError:
        pytest.skip("UDP 127.0.0.2:7000 is not available")
    try:
        # The same device reached twice, as by the subnet and the global broadcast.
        found = await discover(["127.0.0.1", "127.0.0.1", IPv4Address("127.0.0.2")])
    finally:
        other.close()
    assert sorted((info.ip, info.mac, info.mid) for info in found) == [
        ("127.0.0.1", MAC, "13805"),
        ("127.0.0.2", "aabbccdd0001", "10001"),
    ]
    assert module_on_7000.requests == ["scan", "scan"]


async def test_discover_without_answer(module_on_7000: FakeModule) -> None:
    module_on_7000.silent = True
    assert await discover(["127.0.0.1"]) == []


async def test_discover_needs_addresses() -> None:
    assert await discover([]) == []


async def test_no_socket_for_a_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_socket(*args: object, **kwargs: object) -> None:
        raise OSError("no network")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "create_datagram_endpoint", no_socket)
    assert await discover(["192.0.2.255"]) == []
    with pytest.raises(EwpeError, match="cannot scan"):
        await scan("192.0.2.10")
