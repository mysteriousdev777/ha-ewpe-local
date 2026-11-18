"""Packets, checked against greeclimate: an independent implementation of the protocol.

Both encryptions are deterministic (ECB; GCM with a fixed nonce), so a request must be
the very bytes greeclimate sends, and each side must read what the other encrypts.
"""

from __future__ import annotations

import base64
import contextlib
import json
import random
from typing import Any

from greeclimate.cipher import CipherV1, CipherV2
from greeclimate.deviceinfo import DeviceInfo as GreeDeviceInfo
from greeclimate.network import DeviceProtocol2
import pytest

from custom_components.ewpe_local import packets
from custom_components.ewpe_local.packets import (
    DeviceInfo,
    InvalidPacket,
    PackCipher,
    Reply,
)

from .fake_module import KEY, MAC

OTHER_MAC = "aabbccdd0001"
# The test MAC as some modules write it.
SPELLED_MAC = "AA:BB:CC:DD:EE:FF"  # privacy: ignore
ADDR = ("192.0.2.10", 7000)  # documentation range (RFC 5737)
GREE_CIPHERS = {1: CipherV1, 2: CipherV2}


def gree_cipher(version: int, key: str | None = None) -> Any:
    cipher = GREE_CIPHERS[version]
    return cipher() if key is None else cipher(key.encode())


def ours(version: int, key: str | None = None) -> PackCipher:
    return PackCipher(version, None if key is None else key.encode())


def gree_datagram(message: dict[str, Any], cipher: Any) -> bytes:
    """What greeclimate sends for a message (as its DeviceProtocolBase2.send)."""
    message = dict(message)
    if message.get("pack"):
        message["pack"], tag = cipher.encrypt(message["pack"])
        if tag:
            message["tag"] = tag
    return json.dumps(message).encode()


def reply(
    pack: dict[str, Any], cipher: Any, envelope: dict[str, Any] | None = None
) -> bytes:
    """A reply as the module sends it, encrypted by greeclimate."""
    encrypted, tag = cipher.encrypt(pack)
    message = {"t": "pack", "i": 0, "uid": 0, "cid": MAC, "tcid": "app"}
    message.update(envelope or {})
    message["pack"] = encrypted
    if tag:
        message["tag"] = tag
    return json.dumps(message).encode()


GREE = DeviceProtocol2()
GREE_INFO = GreeDeviceInfo(ADDR[0], ADDR[1], MAC, None)


def test_scan_request_is_greeclimates() -> None:
    assert packets.scan_request() == gree_datagram({"t": "scan"}, None)


@pytest.mark.parametrize("version", [1, 2])
def test_requests_are_greeclimates(version: int) -> None:
    generic, gree_generic = ours(version), gree_cipher(version)
    session, gree_session = ours(version, KEY), gree_cipher(version, KEY)
    assert packets.bind_request(generic, MAC) == gree_datagram(
        GREE.create_bind_message(GREE_INFO), gree_generic
    )
    assert packets.status_request(session, MAC, ["Pow", "Dwet"]) == gree_datagram(
        GREE.create_status_message(GREE_INFO, "Pow", "Dwet"), gree_session
    )
    assert packets.command_request(session, MAC, {"Dwet": 8, "Pow": 1}) == (
        gree_datagram(
            GREE.create_command_message(GREE_INFO, Dwet=8, Pow=1), gree_session
        )
    )


@pytest.mark.parametrize("key", [None, KEY])
@pytest.mark.parametrize("version", [1, 2])
def test_encryption_is_greeclimates(version: int, key: str | None) -> None:
    """Every padding length; text that JSON escapes."""
    cipher, gree = ours(version, key), gree_cipher(version, key)
    for size in range(2 * packets.BLOCK_SIZE + 1):
        pack = {"t": "dat", "text": "x" * size, "other": "ü✓"}
        assert cipher.encrypt(pack) == gree.encrypt(pack)
        assert cipher.decrypt(*gree.encrypt(pack)) == pack
        assert gree.decrypt(*cipher.encrypt(pack)) == pack


def test_unknown_version() -> None:
    with pytest.raises(ValueError, match="version"):
        PackCipher(3)


@pytest.mark.parametrize("version", [1, 2])
def test_status_reply(version: int) -> None:
    pack = {"t": "dat", "mac": MAC, "r": 200, "cols": ["Pow", "Dwet"], "dat": [1, 7]}
    data = reply(pack, gree_cipher(version, KEY))
    assert packets.device_reply(data, ours(version, KEY), MAC) == Reply(
        "dat", {"Pow": 1, "Dwet": 7}
    )


@pytest.mark.parametrize(
    "pack",
    [
        {"t": "res", "mac": MAC, "r": 200, "opt": ["Dwet"], "p": [8], "val": [8]},
        {"t": "res", "mac": MAC, "r": 200, "opt": ["Dwet"], "p": [8]},  # no val
        {"t": "RES", "opt": ["Dwet"], "p": [8]},  # any case, MAC only outside
    ],
)
def test_command_echo(pack: dict[str, Any]) -> None:
    data = reply(pack, gree_cipher(2, KEY))
    assert packets.device_reply(data, ours(2, KEY), MAC) == Reply("res", {"Dwet": 8})


@pytest.mark.parametrize("version", [1, 2])
def test_bind_reply(version: int) -> None:
    pack = {"t": "bindok", "mac": MAC, "key": KEY, "r": 200}
    data = reply(pack, gree_cipher(version))
    assert packets.device_reply(data, ours(version), MAC) == Reply(
        "bindok", {"key": KEY}
    )


def test_other_replies_have_no_values() -> None:
    data = reply({"t": "unknown", "mac": MAC, "x": 1}, gree_cipher(2, KEY))
    assert packets.device_reply(data, ours(2, KEY), MAC) == Reply("unknown", {})


@pytest.mark.parametrize(
    ("pack", "envelope"),
    [
        ({"t": "dat", "mac": OTHER_MAC, "cols": ["Pow"], "dat": [1]}, None),
        ({"t": "dat", "cid": OTHER_MAC, "cols": ["Pow"], "dat": [1]}, None),
        ({"t": "dat", "cols": ["Pow"], "dat": [1]}, {"cid": OTHER_MAC}),
        ({"t": "dat", "mac": 5, "cols": ["Pow"], "dat": [1]}, None),
        ({"t": "dat", "cols": ["Pow"], "dat": [1, 2]}, None),
        ({"t": "dat", "cols": "Pow", "dat": [1]}, None),
        ({"t": "dat", "cols": [""], "dat": [1]}, None),
        ({"t": "dat", "cols": [5], "dat": [1]}, None),
        ({"t": "res", "opt": ["Pow"], "val": 1}, None),
        ({"t": "res", "opt": ["Pow"]}, None),
        ({"mac": MAC, "cols": ["Pow"], "dat": [1]}, None),  # no type
        ({"t": 5}, None),
        ({"t": "bindok", "key": KEY, "r": 200}, {"cid": ""}),  # who binds?
        ({"t": "bindok", "mac": MAC, "key": KEY, "r": 500}, None),
        ({"t": "bindok", "mac": MAC, "key": KEY, "r": "200"}, None),
        ({"t": "bindok", "mac": MAC, "key": "short", "r": 200}, None),
        ({"t": "bindok", "mac": MAC, "key": 5, "r": 200}, None),
        ({"t": "bindok", "mac": MAC, "key": "\ud800" * 16}, None),
    ],
)
def test_invalid_replies(pack: dict[str, Any], envelope: dict[str, Any] | None) -> None:
    data = reply(pack, gree_cipher(2, KEY), envelope)
    with pytest.raises(InvalidPacket):
        packets.device_reply(data, ours(2, KEY), MAC)


def test_wrong_key_or_tag() -> None:
    pack = {"t": "dat", "mac": MAC, "cols": ["Pow"], "dat": [1]}
    for version in (1, 2):
        data = reply(pack, gree_cipher(version))  # the generic key, not the device's
        with pytest.raises(InvalidPacket):
            packets.device_reply(data, ours(version, KEY), MAC)
    message = json.loads(reply(pack, gree_cipher(2, KEY)))
    for tag in (None, base64.b64encode(bytes(16)).decode(), "not base64!", 5):
        broken = {**message, "tag": tag}
        with pytest.raises(InvalidPacket):
            packets.device_reply(json.dumps(broken).encode(), ours(2, KEY), MAC)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"garbage",
        b"\xff\xfe",
        b"[]",
        b'"pack"',
        b"[" * 100_000,
        b'{"pack": 5}',
        b'{"pack": null}',
        b'{"pack": "\xc3\xbc"}',
        b'{"t": "pack"}',
        b'{"pack": "AAAA"}',
        b'{"pack": "' + b"9" * 5000 + b'"}',
    ],
)
def test_not_packets(data: bytes) -> None:
    for version in (1, 2):
        with pytest.raises(InvalidPacket):
            packets.device_reply(data, ours(version, KEY), MAC)
    with pytest.raises(InvalidPacket):
        packets.scan_reply(data, ADDR)


@pytest.mark.parametrize("version", [1, 2])
def test_scan_reply(version: int) -> None:
    pack = {"t": "dev", "cid": MAC, "mac": SPELLED_MAC, "mid": "13805"}
    data = reply(pack, gree_cipher(version), {"i": 1})
    assert packets.scan_reply(data, ADDR) == DeviceInfo(*ADDR, MAC, "13805")


@pytest.mark.parametrize(
    ("pack", "expected"),
    [
        ({"t": "dev", "mac": "", "cid": MAC, "mid": "13805"}, (MAC, "13805")),
        ({"t": "dev", "mac": MAC, "mid": 13805}, (MAC, None)),
        ({"t": "dev", "mac": MAC}, (MAC, None)),
    ],
)
def test_scan_reply_fields(pack: dict[str, Any], expected: tuple[str, Any]) -> None:
    info = packets.scan_reply(reply(pack, gree_cipher(1)), ADDR)
    assert (info.mac, info.mid) == expected


@pytest.mark.parametrize(
    "pack",
    [
        {"t": "dev", "mid": "13805"},
        {"t": "dev", "mac": "app", "mid": "13805"},
        {"t": "dev", "mac": MAC + "00", "mid": "13805"},
        {"t": "dev", "mac": 5, "mid": "13805"},
    ],
)
def test_scan_reply_without_a_mac(pack: dict[str, Any]) -> None:
    with pytest.raises(InvalidPacket):
        packets.scan_reply(reply(pack, gree_cipher(1)), ADDR)


def test_anything_else_is_an_invalid_packet() -> None:
    """Random and damaged datagrams: InvalidPacket, never another exception."""
    rng = random.Random(7000)
    good = [
        reply(
            {"t": "dat", "mac": MAC, "cols": ["Pow"], "dat": [1]}, gree_cipher(2, KEY)
        ),
        reply({"t": "bindok", "mac": MAC, "key": KEY}, gree_cipher(1)),
        reply({"t": "dev", "mac": MAC, "mid": "13805"}, gree_cipher(1)),
    ]
    for n in range(3000):
        if n % 3 == 0:
            data = rng.randbytes(rng.randrange(200))
        elif n % 3 == 1:
            damaged = bytearray(rng.choice(good))
            for _ in range(rng.randrange(1, 4)):
                damaged[rng.randrange(len(damaged))] = rng.randrange(256)
            data = bytes(damaged)
        else:
            message = {
                "pack": base64.b64encode(rng.randbytes(16 * rng.randrange(4))).decode()
            }
            if rng.random() < 0.5:
                message["tag"] = base64.b64encode(rng.randbytes(16)).decode()
            data = json.dumps(message).encode()
        for parse in (
            lambda d: packets.device_reply(d, ours(1, KEY), MAC),
            lambda d: packets.device_reply(d, ours(2, KEY), MAC),
            lambda d: packets.scan_reply(d, ADDR),
        ):
            with contextlib.suppress(InvalidPacket):
                parse(data)
