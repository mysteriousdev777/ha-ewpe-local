"""A fake Gree Wi-Fi module of a dehumidifier on a local UDP port.

Behaves like the tested one: answers scan with the generic ECB key, binds with one
cipher only (silent to the other), answers status with the keys it knows, echoes
commands, ignores the fan and the target in Dry, and on entering a mode sets what the
mode sets itself, after the keys of the command (ON_ENTER).

It encrypts with greeclimate's ciphers: the integration's own packets are read and
answered by an independent implementation of the protocol.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from greeclimate.cipher import CipherV1, CipherV2

MAC = "aabbccddeeff"
KEY = "aabbccddeeff0123"
CIPHERS = {1: CipherV1, 2: CipherV2}
# What the device sets on entering a mode from another one, over the same keys of the
# command (checked on the device): Dry the target to 50 % and the fan to High,
# Dehumidify the fan to High, Purify Health on.
ON_ENTER: dict[int, dict[str, int]] = {
    5: {"Dwet": 7, "WdSpd": 5},
    2: {"WdSpd": 5},
    9: {"Health": 1},
}

STATUS_13805: dict[str, Any] = {
    "Pow": 1,
    "Dmod": 2,
    "Dwet": 7,
    "DwatSen": 60,
    "WdSpd": 1,
    "DwatFul": 0,
    "Dfltr": 0,
    "AllErr": 0,
    "TemSen": 61,
    "Lig": 1,
    "SwUpDn": 0,
    "Health": 1,
    "Quiet": 0,
    "ChildLock": 0,
    "PM2P5": 1,
    "airQlt": 1,
    "rssi": -55,
    "AppTimer": 0,
    "ModelType": 65536,
    "hid": "362001065279+U-WB05RT13V1.45.bin",
}


class FakeModule(asyncio.DatagramProtocol):
    def __init__(
        self,
        *,
        mac: str = MAC,
        mid: str = "13805",
        cipher: int = 2,
        status: dict[str, Any] | None = None,
    ) -> None:
        self.mac = mac
        self.mid = mid
        self.cipher = cipher
        self.status = dict(STATUS_13805 if status is None else status)
        self.silent = False  # drop every datagram
        self.delay = 0.0  # seconds before each status or command reply
        self.decoy = False  # before a status reply, send one with other keys
        self.lose_echo = False  # apply commands, but their reply gets lost
        # Seconds before a new mode sets its own keys (the device: under half a second),
        # so they also override a command sent right after the mode.
        self.enter_delay = 0.0
        self.requests: list[str] = []
        self.commands: list[dict[str, Any]] = []
        self.transport: asyncio.DatagramTransport | None = None
        self._timers: set[asyncio.TimerHandle] = set()

    @property
    def port(self) -> int:
        assert self.transport is not None
        return self.transport.get_extra_info("sockname")[1]

    def close(self) -> None:
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()
        if self.transport is not None:
            self.transport.close()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.silent:
            return
        message = json.loads(data)
        if message.get("t") == "scan":
            self.requests.append("scan")
            dev = {
                "t": "dev",
                "cid": self.mac,
                "mac": self.mac,
                "mid": self.mid,
                "name": self.mac,
                "brand": "gree",
                "ver": "V3.4.M",
            }
            self._send(addr, CipherV1(), dev, bind=True)
            return
        if message.get("i") == 1:
            generic = CIPHERS[self.cipher]()
            try:
                generic.decrypt(message["pack"], message.get("tag"))
            except Exception:
                return
            self.requests.append("bind")
            self._send(
                addr,
                generic,
                {"t": "bindok", "mac": self.mac, "key": KEY, "r": 200},
                bind=True,
            )
            return
        cipher = CIPHERS[self.cipher](KEY.encode())
        try:
            pack = cipher.decrypt(message["pack"], message.get("tag"))
        except Exception:
            return
        self.requests.append(pack["t"])
        if pack["t"] == "status":
            columns = [c for c in pack["cols"] if c in self.status]
            if not columns:
                return
            if self.decoy:
                self._send(addr, cipher, self._dat({"Pow": 0, "AppTimer": 1}))
            self._later(addr, cipher, self._dat({c: self.status[c] for c in columns}))
        elif pack["t"] == "cmd":
            values = dict(zip(pack["opt"], pack["p"], strict=True))
            self.commands.append(values)
            mode = self.status.get("Dmod")
            for key, value in values.items():
                self._apply(key, value)
            if self.status.get("Dmod") != mode:
                self._enter(ON_ENTER.get(self.status["Dmod"], {}))
            echo = {
                "t": "res",
                "mac": self.mac,
                "r": 200,
                "opt": pack["opt"],
                "p": pack["p"],
                "val": pack["p"],
            }
            if not self.lose_echo:
                self._later(addr, cipher, echo)

    def _enter(self, values: dict[str, int]) -> None:
        if not self.enter_delay:
            self.status.update(values)
            return

        def apply() -> None:
            self._timers.discard(timer)
            self.status.update(values)

        timer = asyncio.get_running_loop().call_later(self.enter_delay, apply)
        self._timers.add(timer)

    def _apply(self, key: str, value: Any) -> None:
        if key in ("WdSpd", "Dwet") and self.status.get("Dmod") == 5:
            return  # Dry keeps the fan and the target; the command is echoed anyway
        self.status[key] = value

    def _later(self, addr: tuple[str, int], cipher: Any, pack: dict[str, Any]) -> None:
        if not self.delay:
            self._send(addr, cipher, pack)
            return

        def send() -> None:
            self._timers.discard(timer)
            self._send(addr, cipher, pack)

        timer = asyncio.get_running_loop().call_later(self.delay, send)
        self._timers.add(timer)

    def _send(
        self,
        addr: tuple[str, int],
        cipher: Any,
        pack: dict[str, Any],
        bind: bool = False,
    ) -> None:
        if self.transport is None or self.transport.is_closing():
            return
        encrypted, tag = cipher.encrypt(pack)
        message = {
            "t": "pack",
            "i": 1 if bind else 0,
            "uid": 0,
            "cid": self.mac,
            "tcid": "app",
            "pack": encrypted,
        }
        if tag:
            message["tag"] = tag
        self.transport.sendto(json.dumps(message).encode(), addr)

    def _dat(self, values: dict[str, Any]) -> dict[str, Any]:
        return {
            "t": "dat",
            "mac": self.mac,
            "r": 200,
            "cols": list(values),
            "dat": list(values.values()),
        }


async def start_module(
    host: str = "127.0.0.1", port: int = 0, **kwargs: Any
) -> FakeModule:
    _, module = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: FakeModule(**kwargs), local_addr=(host, port)
    )
    return module
