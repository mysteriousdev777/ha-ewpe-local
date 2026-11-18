"""One device over the Gree LAN protocol (UDP 7000).

Requests become request → reply calls: one request at a time per device, each with a
timeout, and a reply is taken only if it answers that request (a late reply may come
after a timeout). The first request binds: the device gives its key, kept in memory
only; repeated failures start a new session with a new bind. The socket is connected to
the device: the system passes only its datagrams.

Also the scan, for adding a device: to one address, or a search by broadcast.

No Home Assistant imports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address
import logging
from typing import Any, Final

from . import packets
from .packets import DeviceInfo, InvalidPacket, PackCipher
from .protocol import normalize_mac

_LOGGER = logging.getLogger(__name__)

PORT: Final = 7000
CIPHER_ORDER: Final = (2, 1)  # newer modules bind with GCM only, and V1 waits a timeout

SCAN_TIMEOUT: Final = 3.0  # the scan replies
BIND_TIMEOUT: Final = 5.0  # the bind reply
REPLY_TIMEOUT: Final = 5.0  # a status or command reply
NEW_SESSION_AFTER: Final = 3  # failed requests in a row before binding again


class EwpeError(Exception):
    """The device did not answer, or the client is closed."""


class _SocketClosed(Exception):
    """The session's socket closed under a request."""


_FAILURES: Final = (TimeoutError, OSError, _SocketClosed)


@dataclass(frozen=True, slots=True)
class _Pending:
    kind: str
    cipher: PackCipher
    accepts: Callable[[dict[str, Any]], bool]
    future: asyncio.Future[dict[str, Any]]


class _Socket(asyncio.DatagramProtocol):
    """A UDP socket that hands its datagrams to a callback.

    Socket errors are logged at debug level: an unreachable port (ICMP) would otherwise
    log at every poll; the request then times out and the coordinator reports it once.
    """

    def __init__(self, received: Callable[[bytes, Any], None]) -> None:
        self._received = received

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self._received(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Socket error: %s", exc)


class EwpeClient:
    """Status and command requests to one device."""

    def __init__(self, host: str, mac: str, cipher: int) -> None:
        self._host = host
        self._mac = normalize_mac(mac)
        self._version = cipher
        self._transport: asyncio.DatagramTransport | None = None
        self._cipher: PackCipher | None = None  # with the device key, after a bind
        self._lock = asyncio.Lock()
        self._pending: _Pending | None = None
        self._failures = 0
        self._closed = False

    @property
    def host(self) -> str:
        return self._host

    @property
    def closed(self) -> bool:
        return self._closed

    async def status(self, columns: Sequence[str]) -> dict[str, Any]:
        """Read status keys; the reply has those the module knows."""
        asked = frozenset(columns)

        def accepts(values: dict[str, Any]) -> bool:
            return bool(values) and values.keys() <= asked

        return await self._request(
            packets.DATA,
            accepts,
            lambda cipher: packets.status_request(cipher, self._mac, columns),
        )

    async def command(self, values: Mapping[str, int]) -> dict[str, Any]:
        """Write keys; returns the module's echo, which is not the device's state."""
        sent = frozenset(values)

        def accepts(echo: dict[str, Any]) -> bool:
            return echo.keys() == sent

        return await self._request(
            packets.RESULT,
            accepts,
            lambda cipher: packets.command_request(cipher, self._mac, values),
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
        kind: str,
        accepts: Callable[[dict[str, Any]], bool],
        build: Callable[[PackCipher], bytes],
    ) -> dict[str, Any]:
        async with self._lock:
            if self._closed:
                raise EwpeError("client closed")
            try:
                cipher = await self._session()
                reply = await self._exchange(
                    kind, cipher, accepts, build(cipher), REPLY_TIMEOUT
                )
            except _FAILURES as err:
                self._failures += 1
                if self._failures >= NEW_SESSION_AFTER:
                    self._drop()
                raise EwpeError(f"no reply from {self._host}") from err
            self._failures = 0
            return reply

    async def _session(self) -> PackCipher:
        """The cipher with the device key; opens a socket and binds when there is none."""
        transport = self._transport
        if (
            self._cipher is not None
            and transport is not None
            and not transport.is_closing()
        ):
            return self._cipher
        self._drop()
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _Socket(self._received), remote_addr=(self._host, PORT)
        )
        if self._closed:  # closed while the socket opened
            transport.close()
            raise EwpeError("client closed")
        self._transport = transport
        generic = PackCipher(self._version)
        try:
            bound = await self._exchange(
                packets.BIND_OK,
                generic,
                lambda values: True,
                packets.bind_request(generic, self._mac),
                BIND_TIMEOUT,
            )
        except BaseException:
            self._drop()
            raise
        self._cipher = PackCipher(self._version, bound["key"].encode())
        return self._cipher

    async def _exchange(
        self,
        kind: str,
        cipher: PackCipher,
        accepts: Callable[[dict[str, Any]], bool],
        request: bytes,
        seconds: float,
    ) -> dict[str, Any]:
        """Send a request and wait for the reply it accepts, at most `seconds`."""
        transport = self._transport
        if transport is None or transport.is_closing():
            raise _SocketClosed
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending = _Pending(kind, cipher, accepts, future)
        try:
            transport.sendto(request)
            async with asyncio.timeout(seconds):
                return await future
        finally:
            self._pending = None
            if not future.done():
                future.cancel()

    def _received(self, data: bytes, addr: Any) -> None:
        pending = self._pending
        if pending is None or pending.future.done():
            _LOGGER.debug("Datagram from %s not waited for", self._host)
            return
        try:
            reply = packets.device_reply(data, pending.cipher, self._mac)
        except InvalidPacket as err:
            _LOGGER.debug("Ignoring a datagram from %s: %s", self._host, err)
            return
        if reply.kind != pending.kind or not pending.accepts(reply.values):
            _LOGGER.debug(
                "Reply not waited for from %s: %s %s",
                self._host,
                reply.kind,
                sorted(reply.values),
            )
            return
        pending.future.set_result(reply.values)

    def _drop(self) -> None:
        """Forget the session: close its socket; the next request binds again."""
        transport, self._transport = self._transport, None
        self._cipher = None
        if transport is not None:
            transport.close()


async def scan(host: str) -> DeviceInfo:
    """The scan reply of the device at an IPv4 address."""
    try:
        found = await _scan([host], until=host)
    except OSError as err:
        raise EwpeError(f"cannot scan {host}: {err}") from err
    for info in found:
        if info.ip == host:
            return info
    raise EwpeError(f"no scan reply from {host}")


async def discover(addresses: Collection[IPv4Address | str]) -> list[DeviceInfo]:
    """Devices that answer a scan sent to these (broadcast) addresses, one per MAC.

    Waits SCAN_TIMEOUT for the replies.
    """
    if not addresses:
        return []
    try:
        return await _scan([str(address) for address in addresses])
    except OSError as err:
        _LOGGER.debug("Cannot search: %s", err)
        return []


async def _scan(targets: Sequence[str], until: str | None = None) -> list[DeviceInfo]:
    """Send a scan to each target and gather the replies for SCAN_TIMEOUT.

    One device per MAC, its latest reply. With `until`, stops at the first reply from
    that address.
    """
    loop = asyncio.get_running_loop()
    found: dict[str, DeviceInfo] = {}
    done: asyncio.Future[None] = loop.create_future()

    def received(data: bytes, addr: Any) -> None:
        try:
            info = packets.scan_reply(data, addr)
        except InvalidPacket as err:
            _LOGGER.debug("Ignoring a scan reply from %s: %s", addr[0], err)
            return
        found[info.mac] = info
        if info.ip == until and not done.done():
            done.set_result(None)

    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Socket(received), local_addr=("0.0.0.0", 0), allow_broadcast=True
    )
    try:
        request = packets.scan_request()
        for target in targets:
            transport.sendto(request, (target, PORT))
        try:
            async with asyncio.timeout(SCAN_TIMEOUT):
                await done  # without `until`, the whole time
        except TimeoutError:
            pass
    finally:
        transport.close()
        if not done.done():
            done.cancel()
    return list(found.values())


async def handshake(
    info: DeviceInfo, columns: Sequence[str]
) -> tuple[int, dict[str, Any]]:
    """Find the cipher the device binds with, and read some status keys with it."""
    for cipher in CIPHER_ORDER:
        client = EwpeClient(info.ip, info.mac, cipher)
        try:
            return cipher, await client.status(columns)
        except EwpeError:
            _LOGGER.debug("No answer from %s with cipher V%s", info.ip, cipher)
        finally:
            client.close()
    raise EwpeError(f"no bind reply from {info.ip}")
