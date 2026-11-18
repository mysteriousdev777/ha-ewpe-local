"""One device over the Gree LAN protocol (UDP 7000), through greeclimate.

greeclimate encrypts, binds and sends; replies come back as events. Here they become
request → reply calls: one request at a time per device, each with a timeout, and a
reply is taken only if it answers that request (the library sends a status request of
its own after binding, and a late reply may come after a timeout).

Also the scan, for adding a device: to one address, or a search by broadcast.

No Home Assistant imports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address
import logging
from typing import Any, Final

from greeclimate.cipher import CipherV1, CipherV2
from greeclimate.device import Device
from greeclimate.deviceinfo import DeviceInfo
from greeclimate.discovery import Discovery, Listener
from greeclimate.exceptions import DeviceNotBoundError, DeviceTimeoutError
from greeclimate.network import Response

from .protocol import normalize_mac

_LOGGER = logging.getLogger(__name__)

PORT: Final = 7000
CIPHERS: Final = {1: CipherV1, 2: CipherV2}
CIPHER_ORDER: Final = (2, 1)  # newer modules bind with GCM only, and V1 waits a timeout

SCAN_TIMEOUT: Final = 3.0  # the scan reply
BIND_TIMEOUT: Final = 5  # the bind reply, per cipher (an int for greeclimate)
SEND_TIMEOUT: Final = 5  # the socket taking a datagram
REPLY_TIMEOUT: Final = 5.0  # a status or command reply
NEW_SESSION_AFTER: Final = 3  # failed requests in a row before binding again

_FAILURES: Final = (TimeoutError, DeviceTimeoutError, DeviceNotBoundError, OSError)


class EwpeError(Exception):
    """The device did not answer, or the client is closed."""


@dataclass(frozen=True, slots=True)
class _Pending:
    kind: Response
    accepts: Callable[[dict[str, Any]], bool]
    future: asyncio.Future[dict[str, Any]]


class _Device(Device):
    """greeclimate's device, with only the client's own status requests.

    Socket errors are logged at debug level: an unreachable port (ICMP) would otherwise
    log a traceback at every poll; the request then times out and the coordinator
    reports it once.
    """

    async def update_state(self, columns: Sequence[str] | None = None) -> None:
        # After every bind greeclimate asks for its MID profile's columns by itself.
        # The client asks for the model's columns; that extra reply would only be one
        # more to skip, or be taken for the client's if the profile were a subset.
        if columns is not None:
            await super().update_state(columns)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Socket error from %s: %s", self.device_info.ip, exc)


class EwpeClient:
    """Status and command requests to one device."""

    def __init__(self, host: str, mac: str, mid: str | None, cipher: int) -> None:
        self._info = DeviceInfo(host, PORT, normalize_mac(mac), None, mid=mid)
        self._cipher = CIPHERS[cipher]
        self._device: _Device | None = None
        self._lock = asyncio.Lock()
        self._pending: _Pending | None = None
        self._failures = 0
        self._closed = False

    @property
    def host(self) -> str:
        return self._info.ip

    @property
    def closed(self) -> bool:
        return self._closed

    async def status(self, columns: Sequence[str]) -> dict[str, Any]:
        """Read status keys; the reply has those the module knows."""
        asked = frozenset(columns)

        def accepts(reply: dict[str, Any]) -> bool:
            return bool(reply) and reply.keys() <= asked

        return await self._request(
            Response.DATA, accepts, lambda device: device.update_state(list(columns))
        )

    async def command(self, values: Mapping[str, int]) -> dict[str, Any]:
        """Write keys; returns the module's echo, which is not the device's state."""
        sent = frozenset(values)

        def accepts(reply: dict[str, Any]) -> bool:
            return reply.keys() == sent

        return await self._request(
            Response.RESULT,
            accepts,
            lambda device: device.send(
                device.create_command_message(device.device_info, **values)
            ),
        )

    def close(self) -> None:
        """Close the socket and fail a request in progress; the client stays closed."""
        self._closed = True
        pending, self._pending = self._pending, None
        if pending is not None and not pending.future.done():
            pending.future.set_exception(EwpeError("client closed"))
        self._drop()

    async def _request(
        self,
        kind: Response,
        accepts: Callable[[dict[str, Any]], bool],
        send: Callable[[_Device], Awaitable[None]],
    ) -> dict[str, Any]:
        async with self._lock:
            if self._closed:
                raise EwpeError("client closed")
            future: asyncio.Future[dict[str, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            try:
                device = await self._session()
                self._pending = _Pending(kind, accepts, future)
                await send(device)
                async with asyncio.timeout(REPLY_TIMEOUT):
                    reply = await future
            except _FAILURES as err:
                self._failures += 1
                if self._failures >= NEW_SESSION_AFTER:
                    self._drop()
                raise EwpeError(f"no reply from {self.host}") from err
            finally:
                self._pending = None
                if not future.done():
                    future.cancel()
            self._failures = 0
            return reply

    async def _session(self) -> _Device:
        """The bound device, binding a new one when there is none."""
        device = self._device
        if device is not None and device.device_cipher is not None:
            return device
        self._drop()
        device = _Device(self._info, timeout=SEND_TIMEOUT, bind_timeout=BIND_TIMEOUT)
        device.add_handler(Response.DATA, self._on_data)
        device.add_handler(Response.RESULT, self._on_result)
        self._device = device
        try:
            await device.bind(cipher=self._cipher())
        except BaseException:
            self._drop()
            raise
        if self._closed:  # closed while binding
            raise DeviceNotBoundError
        return device

    def _drop(self) -> None:
        device, self._device = self._device, None
        if device is not None:
            device.close()

    def _on_data(self, values: dict[str, Any]) -> None:
        self._resolve(Response.DATA, values)

    def _on_result(self, values: dict[str, Any]) -> None:
        self._resolve(Response.RESULT, values)

    def _resolve(self, kind: Response, values: dict[str, Any]) -> None:
        pending = self._pending
        if (
            pending is None
            or pending.kind is not kind
            or pending.future.done()
            or not pending.accepts(values)
        ):
            _LOGGER.debug("Reply not waited for from %s: %s", self.host, sorted(values))
            return
        pending.future.set_result(dict(values))


async def scan(host: str) -> DeviceInfo:
    """The scan reply of the device at an IPv4 address."""
    loop = asyncio.get_running_loop()
    found: asyncio.Future[DeviceInfo] = loop.create_future()

    class _Found(Listener):
        async def device_found(self, device_info: DeviceInfo) -> None:
            if device_info.ip == host and not found.done():
                found.set_result(device_info)

    discovery = Discovery(timeout=SEND_TIMEOUT)
    discovery.add_listener(_Found())
    try:
        await discovery.search_devices(target_addresses=[host])
        async with asyncio.timeout(SCAN_TIMEOUT):
            return await found
    except (TimeoutError, OSError) as err:
        raise EwpeError(f"no scan reply from {host}") from err
    finally:
        discovery.close()
        if not found.done():
            found.cancel()


async def discover(addresses: Collection[IPv4Address | str]) -> list[DeviceInfo]:
    """Devices that answer a scan sent to these (broadcast) addresses, one per MAC.

    Waits SCAN_TIMEOUT for the replies. No addresses, no scan: greeclimate would list
    the network interfaces itself, a blocking call.
    """
    if not addresses:
        return []
    discovery = Discovery(timeout=SEND_TIMEOUT)
    try:
        return list(
            await discovery.scan(
                wait_for=SCAN_TIMEOUT, target_addresses=list(addresses)
            )
        )
    finally:
        discovery.close()


async def handshake(
    info: DeviceInfo, columns: Sequence[str]
) -> tuple[int, dict[str, Any]]:
    """Find the cipher the device binds with, and read some status keys with it."""
    for cipher in CIPHER_ORDER:
        client = EwpeClient(info.ip, info.mac, info.mid, cipher)
        try:
            return cipher, await client.status(columns)
        except EwpeError:
            _LOGGER.debug("No answer from %s with cipher V%s", info.ip, cipher)
        finally:
            client.close()
    raise EwpeError(f"no bind reply from {info.ip}")
