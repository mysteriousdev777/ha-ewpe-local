"""Packets of the Gree LAN protocol: requests, replies and the encryption of `pack`.

Every packet is a JSON object. Its content is in `pack`: a JSON object encrypted with
AES and base64-encoded. Version 1 of the protocol uses ECB; version 2 uses GCM with a
fixed nonce and associated data, the tag going in `tag`. A scan reply and a bind use the
generic key of the version; the bind reply gives the device key, used for everything
after it. The scan request itself is not encrypted.

Every reply is parsed strictly: anything else, from a broken datagram to another
device's reply, raises InvalidPacket and nothing more.

Pure Python: no Home Assistant, no network. AES comes from `cryptography`, a dependency
of Home Assistant itself.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import Any, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .protocol import normalize_mac

GENERIC_KEYS: Final = {1: b"a3K8Bx%2r8Y7#xDh", 2: b"{yxAHAY_Lm6pbC/<"}
GCM_NONCE: Final = bytes.fromhex("5440784449675a516c5e6313")
GCM_AAD: Final = b"qualcomm-test"
GCM_TAG_SIZE: Final = 16
BLOCK_SIZE: Final = 16
KEY_SIZES: Final = (16, 24, 32)  # bytes: AES-128, -192, -256

# Reply types (`t` of the pack, compared in lowercase).
BIND_OK: Final = "bindok"
DATA: Final = "dat"  # a status reply
RESULT: Final = "res"  # a command echo

_MAC: Final = re.compile(r"[0-9a-f]{12}")


class InvalidPacket(ValueError):
    """Not a packet of this protocol, not for this key, or not from this device."""


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """A device that answered a scan."""

    ip: str
    port: int
    mac: str  # 12 lowercase hex digits
    mid: str | None  # the model id


@dataclass(frozen=True, slots=True)
class Reply:
    """A reply of the device: its type and its values."""

    kind: str  # `t`, in lowercase
    values: dict[str, Any]  # status or echo keys; `key` of a bind; empty for others


class PackCipher:
    """AES of `pack` for one protocol version and key (the generic one by default)."""

    def __init__(self, version: int, key: bytes | None = None) -> None:
        if version not in GENERIC_KEYS:
            raise ValueError(f"unknown protocol version {version}")
        self.version = version
        self.key = GENERIC_KEYS[version] if key is None else key

    def encrypt(self, pack: Mapping[str, Any]) -> tuple[str, str | None]:
        """The encrypted pack and, for version 2, its tag."""
        data = json.dumps(pack).encode()
        if self.version == 1:
            size = BLOCK_SIZE - len(data) % BLOCK_SIZE  # PKCS#7
            encryptor = Cipher(algorithms.AES(self.key), modes.ECB()).encryptor()
            sealed = (
                encryptor.update(data + bytes([size]) * size) + encryptor.finalize()
            )
            return _base64(sealed), None
        sealed = AESGCM(self.key).encrypt(GCM_NONCE, data, GCM_AAD)
        return _base64(sealed[:-GCM_TAG_SIZE]), _base64(sealed[-GCM_TAG_SIZE:])

    def decrypt(self, pack: Any, tag: Any = None) -> dict[str, Any]:
        """The pack as a JSON object. Raises InvalidPacket."""
        try:
            data = base64.b64decode(pack)
            if self.version == 1:
                decryptor = Cipher(algorithms.AES(self.key), modes.ECB()).decryptor()
                plain = decryptor.update(data) + decryptor.finalize()
            elif tag is None:
                raise InvalidPacket("no tag")
            else:
                sealed = data + base64.b64decode(tag, validate=True)
                plain = AESGCM(self.key).decrypt(GCM_NONCE, sealed, GCM_AAD)
            text = plain.decode()
            # The object ends at its last brace; after it comes the padding.
            text = text[: text.rindex("}") + 1]
        except InvalidPacket:
            raise
        except (binascii.Error, InvalidTag, ValueError, TypeError) as err:
            raise InvalidPacket(f"cannot decrypt ({type(err).__name__})") from err
        return _object(text)


def scan_request() -> bytes:
    """A scan, to one address or a broadcast one: not encrypted."""
    return json.dumps({"t": "scan"}).encode()


def bind_request(cipher: PackCipher, mac: str) -> bytes:
    """A bind, with the generic key: the reply gives the device key."""
    return _request(cipher, mac, {"t": "bind", "mac": mac, "uid": 0}, bind=True)


def status_request(cipher: PackCipher, mac: str, columns: Sequence[str]) -> bytes:
    """A request for status keys."""
    return _request(cipher, mac, {"t": "status", "mac": mac, "cols": list(columns)})


def command_request(cipher: PackCipher, mac: str, values: Mapping[str, Any]) -> bytes:
    """A command: keys and their new values."""
    pack = {"t": "cmd", "mac": mac, "opt": list(values), "p": list(values.values())}
    return _request(cipher, mac, pack)


def device_reply(data: bytes, cipher: PackCipher, mac: str) -> Reply:
    """A reply of the device with this MAC, decrypted with this cipher.

    Raises InvalidPacket, also for a reply that names another device.
    """
    envelope = _object(data)
    pack = cipher.decrypt(envelope.get("pack"), envelope.get("tag"))
    # The device names itself in the envelope's `cid` and the pack's `mac` or `cid`:
    # each one given must be this device; a bind reply must give one.
    identities = [
        value
        for value in (pack.get("mac"), pack.get("cid"), envelope.get("cid"))
        if value is not None and value != ""
    ]
    if any(_mac(value) != mac for value in identities):
        raise InvalidPacket("from another device")
    kind = pack.get("t")
    if not isinstance(kind, str):
        raise InvalidPacket("no type")
    kind = kind.casefold()
    if kind == BIND_OK:
        if not identities:
            raise InvalidPacket("a bind reply without the device")
        return Reply(kind, _bind_key(pack))
    if kind in (DATA, RESULT):
        return Reply(kind, _properties(pack, kind))
    return Reply(kind, {})


def scan_reply(data: bytes, addr: tuple[str, int]) -> DeviceInfo:
    """The device that sent a scan reply from this address. Raises InvalidPacket."""
    envelope = _object(data)
    try:
        pack = PackCipher(1).decrypt(envelope.get("pack"))
    except InvalidPacket:
        if envelope.get("tag") is None:
            raise
        pack = PackCipher(2).decrypt(envelope.get("pack"), envelope.get("tag"))
    identity = pack.get("mac")
    if identity is None or identity == "":
        identity = pack.get("cid")
    mac = _mac(identity)
    if mac is None:
        raise InvalidPacket("no MAC")
    mid = pack.get("mid")
    return DeviceInfo(addr[0], addr[1], mac, mid if isinstance(mid, str) else None)


def _request(
    cipher: PackCipher, mac: str, pack: dict[str, Any], *, bind: bool = False
) -> bytes:
    request: dict[str, Any] = {
        "cid": "app",
        "i": 1 if bind else 0,
        "t": "pack",
        "uid": 0,
        "tcid": mac,
    }
    request["pack"], tag = cipher.encrypt(pack)
    if tag is not None:
        request["tag"] = tag
    return json.dumps(request).encode()


def _bind_key(pack: dict[str, Any]) -> dict[str, Any]:
    key = pack.get("key")
    result = pack.get("r", 200)
    try:
        size = len(key.encode()) if isinstance(key, str) else 0
    except UnicodeError:
        size = 0
    if size not in KEY_SIZES or type(result) is not int or result != 200:
        raise InvalidPacket("a bind refused, or without a usable key")
    return {"key": key}


def _properties(pack: dict[str, Any], kind: str) -> dict[str, Any]:
    """Status keys (`cols`, `dat`) or a command echo (`opt`, then `val` or `p`)."""
    if kind == DATA:
        names, values = pack.get("cols"), pack.get("dat")
    else:
        names, values = pack.get("opt"), pack.get("val")
        if values is not None and not isinstance(values, list):
            raise InvalidPacket("echo values are not a list")
        values = values or pack.get("p", [])
    if (
        not isinstance(names, list)
        or not isinstance(values, list)
        or len(names) != len(values)
        or not all(isinstance(name, str) and name for name in names)
    ):
        raise InvalidPacket("keys and values do not match")
    return dict(zip(names, values, strict=True))


def _mac(value: Any) -> str | None:
    """A MAC as 12 lowercase hex digits, or None."""
    if not isinstance(value, str):
        return None
    mac = normalize_mac(value)
    return mac if _MAC.fullmatch(mac) else None


def _object(data: str | bytes) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (ValueError, RecursionError) as err:
        raise InvalidPacket("not JSON") from err
    if not isinstance(value, dict):
        raise InvalidPacket("not a JSON object")
    return value


def _base64(data: bytes) -> str:
    return base64.b64encode(data).decode()
