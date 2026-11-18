"""Entities: states from the fake module, commands, mode and tank locks, availability."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ewpe_local.const import CONFIRM_DELAY, DEFAULT_SCAN_INTERVAL
from homeassistant.components import humidifier
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.humidifier import (
    ATTR_AVAILABLE_MODES,
    ATTR_CURRENT_HUMIDITY,
    ATTR_HUMIDITY,
    ATTR_MODE,
    SERVICE_SET_HUMIDITY,
    SERVICE_SET_MODE,
)
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .fake_module import FakeModule

DEHUMIDIFIER = "humidifier.dehumidifier"
FAN = "select.dehumidifier_fan_speed"


async def call(
    hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: Any
) -> None:
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def confirm(hass: HomeAssistant) -> None:
    """Let the read-back after a command run."""
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=CONFIRM_DELAY + 1)
    )
    await hass.async_block_till_done()


async def poll(hass: HomeAssistant) -> None:
    """Let the next poll run: a background task of the entry."""
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_states(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    state = hass.states.get(DEHUMIDIFIER)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_MODE] == "dehumidify"
    assert state.attributes[ATTR_HUMIDITY] == 50
    assert state.attributes[ATTR_CURRENT_HUMIDITY] == 60
    assert state.attributes[ATTR_AVAILABLE_MODES] == ["dehumidify", "dry", "purify"]
    # The step attribute exists from Home Assistant 2026.2; older releases ignore it.
    if step := getattr(humidifier, "ATTR_TARGET_HUMIDITY_STEP", None):
        assert state.attributes[step] == 5
    assert state.attributes["min_humidity"] == 30
    assert state.attributes["max_humidity"] == 80

    expected = {
        FAN: "low",
        "sensor.dehumidifier_humidity": "60",
        "sensor.dehumidifier_temperature": "21",
        "sensor.dehumidifier_pm2_5_level": "excellent",
        "sensor.dehumidifier_odor_level": "normal",
        "sensor.dehumidifier_error_code": "0",
        "binary_sensor.dehumidifier_water_tank_full": STATE_OFF,
        "binary_sensor.dehumidifier_filter_cleaning": STATE_OFF,
        "binary_sensor.dehumidifier_fault": STATE_OFF,
        "switch.dehumidifier_swing": STATE_OFF,
        "switch.dehumidifier_health_uv_ion": STATE_ON,
        "switch.dehumidifier_light": STATE_ON,
        "switch.dehumidifier_mute": STATE_OFF,
        "switch.dehumidifier_child_lock": STATE_OFF,
    }
    for entity_id, value in expected.items():
        assert hass.states.get(entity_id).state == value, entity_id
    assert hass.states.get("sensor.dehumidifier_signal_strength") is None  # disabled
    assert hass.states.get("button.dehumidifier_reset_filter_reminder") is not None


async def test_target_humidity(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    await call(hass, "humidifier", SERVICE_SET_HUMIDITY, DEHUMIDIFIER, humidity=55)
    assert module.commands == [{"Dwet": 8}]
    assert hass.states.get(DEHUMIDIFIER).attributes[ATTR_HUMIDITY] == 55
    await confirm(hass)
    assert hass.states.get(DEHUMIDIFIER).attributes[ATTR_HUMIDITY] == 55


async def test_power(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    await call(hass, "humidifier", SERVICE_TURN_OFF, DEHUMIDIFIER)
    assert module.status["Pow"] == 0
    assert hass.states.get(DEHUMIDIFIER).state == STATE_OFF
    await call(hass, "humidifier", SERVICE_TURN_ON, DEHUMIDIFIER)
    assert module.status["Pow"] == 1


async def test_dry_mode_locks_target_and_fan(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    await call(hass, "humidifier", SERVICE_SET_MODE, DEHUMIDIFIER, mode="dry")
    assert module.status["Dmod"] == 5
    await confirm(hass)
    state = hass.states.get(DEHUMIDIFIER)
    assert state.attributes[ATTR_MODE] == "dry"
    assert state.attributes.get(ATTR_HUMIDITY) is None  # no target in Dry
    assert hass.states.get(FAN).state == "high"  # the device switched it

    with pytest.raises(ServiceValidationError):
        await call(hass, "humidifier", SERVICE_SET_HUMIDITY, DEHUMIDIFIER, humidity=60)
    with pytest.raises(ServiceValidationError):
        await call(hass, "select", SERVICE_SELECT_OPTION, FAN, **{ATTR_OPTION: "low"})
    assert module.commands == [{"Dmod": 5}]


async def test_purify_keeps_health_on(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    await call(hass, "humidifier", SERVICE_SET_MODE, DEHUMIDIFIER, mode="purify")
    with pytest.raises(ServiceValidationError):
        await call(
            hass, "switch", SERVICE_TURN_OFF, "switch.dehumidifier_health_uv_ion"
        )
    await call(hass, "select", SERVICE_SELECT_OPTION, FAN, **{ATTR_OPTION: "auto"})
    assert module.commands == [{"Dmod": 9}, {"WdSpd": 0}]


async def test_what_the_mode_keeps_is_no_error(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    """Asking for the value a mode keeps (High in Dry, Health in Purify) sends nothing."""
    await call(hass, "humidifier", SERVICE_SET_MODE, DEHUMIDIFIER, mode="dry")
    await confirm(hass)
    await call(hass, "select", SERVICE_SELECT_OPTION, FAN, **{ATTR_OPTION: "high"})
    await call(hass, "humidifier", SERVICE_SET_MODE, DEHUMIDIFIER, mode="purify")
    await confirm(hass)
    await call(hass, "switch", SERVICE_TURN_ON, "switch.dehumidifier_health_uv_ion")
    assert module.commands == [{"Dmod": 5}, {"Dmod": 9}]


async def test_full_tank_allows_power_and_child_lock_only(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    """As the panel: with the tank full only power and the child lock change."""
    module.status.update(DwatFul=1, AllErr=1)  # the full tank sets the error flag too
    await poll(hass)
    assert hass.states.get("binary_sensor.dehumidifier_water_tank_full").state == (
        STATE_ON
    )
    assert hass.states.get("binary_sensor.dehumidifier_fault").state == STATE_OFF
    assert hass.states.get("sensor.dehumidifier_error_code").state == "1"

    refused = [
        ("humidifier", SERVICE_SET_MODE, DEHUMIDIFIER, {"mode": "dry"}),
        ("humidifier", SERVICE_SET_HUMIDITY, DEHUMIDIFIER, {"humidity": 60}),
        ("select", SERVICE_SELECT_OPTION, FAN, {ATTR_OPTION: "high"}),
        ("switch", SERVICE_TURN_ON, "switch.dehumidifier_swing", {}),
        ("switch", SERVICE_TURN_OFF, "switch.dehumidifier_light", {}),
        ("switch", SERVICE_TURN_ON, "switch.dehumidifier_mute", {}),
        ("switch", SERVICE_TURN_OFF, "switch.dehumidifier_health_uv_ion", {}),
    ]
    for domain, service, entity_id, data in refused:
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, domain, service, entity_id, **data)
        assert err.value.translation_key == "tank_full", (service, entity_id)
    # What the device has anyway is no error and sends nothing.
    await call(hass, "switch", SERVICE_TURN_OFF, "switch.dehumidifier_swing")
    assert module.commands == []

    await call(hass, "switch", SERVICE_TURN_ON, "switch.dehumidifier_child_lock")
    await call(
        hass, "button", SERVICE_PRESS, "button.dehumidifier_reset_filter_reminder"
    )
    await call(hass, "humidifier", SERVICE_TURN_OFF, DEHUMIDIFIER)
    assert module.commands == [{"ChildLock": 1}, {"Dfltr": 0}, {"Pow": 0}]


async def test_emptied_tank_is_read_before_refusing(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    """The tank was emptied after the last poll: the command goes through."""
    tank = "binary_sensor.dehumidifier_water_tank_full"
    module.status.update(DwatFul=1, AllErr=1)
    await poll(hass)
    assert hass.states.get(tank).state == STATE_ON

    module.status.update(DwatFul=0, AllErr=0)  # emptied: Home Assistant has not polled
    await call(hass, "switch", SERVICE_TURN_ON, "switch.dehumidifier_swing")
    assert module.commands == [{"SwUpDn": 1}]
    assert hass.states.get(tank).state == STATE_OFF


async def test_mode_changed_on_the_panel_is_read_before_refusing(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    """Dry left on the panel after the last poll: the fan can change."""
    await call(hass, "humidifier", SERVICE_SET_MODE, DEHUMIDIFIER, mode="dry")
    await confirm(hass)
    module.status["Dmod"] = 2  # Dehumidify chosen on the panel
    await call(hass, "select", SERVICE_SELECT_OPTION, FAN, **{ATTR_OPTION: "medium"})
    assert module.commands == [{"Dmod": 5}, {"WdSpd": 3}]
    assert hass.states.get(DEHUMIDIFIER).attributes[ATTR_MODE] == "dehumidify"


async def test_refused_when_the_read_before_gets_no_answer(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    """A silent device: the last known state decides, the device turns unavailable."""
    module.status["DwatFul"] = 1
    await poll(hass)
    module.silent = True
    with pytest.raises(ServiceValidationError):
        await call(hass, "switch", SERVICE_TURN_ON, "switch.dehumidifier_swing")
    assert module.commands == []
    assert hass.states.get(DEHUMIDIFIER).state == STATE_UNAVAILABLE


async def test_fault_is_the_error_flag_without_the_full_tank(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    fault = "binary_sensor.dehumidifier_fault"
    module.status["AllErr"] = 1
    await poll(hass)
    assert hass.states.get(fault).state == STATE_ON
    # Commands are not locked by a fault: the panel locks for the full tank only.
    await call(hass, "switch", SERVICE_TURN_ON, "switch.dehumidifier_swing")
    assert module.commands == [{"SwUpDn": 1}]

    module.status["AllErr"] = 0
    await poll(hass)
    assert hass.states.get(fault).state == STATE_OFF


async def test_lost_echo_is_read_back(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    """The device did it and only its reply got lost: the state is read back."""
    swing = "switch.dehumidifier_swing"
    module.lose_echo = True
    with pytest.raises(HomeAssistantError):
        await call(hass, "switch", SERVICE_TURN_ON, swing)
    assert module.status["SwUpDn"] == 1
    assert hass.states.get(swing).state == STATE_OFF
    await confirm(hass)
    assert hass.states.get(swing).state == STATE_ON


async def test_switches_and_button(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    await call(hass, "switch", SERVICE_TURN_ON, "switch.dehumidifier_mute")
    await call(hass, "switch", SERVICE_TURN_ON, "switch.dehumidifier_swing")
    await call(hass, "switch", SERVICE_TURN_OFF, "switch.dehumidifier_light")
    await call(
        hass, "button", SERVICE_PRESS, "button.dehumidifier_reset_filter_reminder"
    )
    assert module.commands == [{"Quiet": 2}, {"SwUpDn": 1}, {"Lig": 0}, {"Dfltr": 0}]
    assert hass.states.get("switch.dehumidifier_mute").state == STATE_ON


async def test_unavailable_and_back(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    module.silent = True
    await poll(hass)
    assert hass.states.get(DEHUMIDIFIER).state == STATE_UNAVAILABLE

    module.silent = False
    await poll(hass)
    assert hass.states.get(DEHUMIDIFIER).state == STATE_ON


async def test_command_without_answer(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    module.silent = True
    with pytest.raises(HomeAssistantError):
        await call(hass, "humidifier", SERVICE_TURN_OFF, DEHUMIDIFIER)
    assert hass.states.get(DEHUMIDIFIER).state == STATE_ON  # nothing changed


async def test_one_lost_reply_is_not_unavailable(
    hass: HomeAssistant, entry: MockConfigEntry, module: FakeModule
) -> None:
    """The second request of a poll gets the answer."""
    original = module.datagram_received
    dropped = []

    def drop_first(data: bytes, addr: tuple[str, int]) -> None:
        if not dropped:
            dropped.append(data)
            return
        original(data, addr)

    module.datagram_received = drop_first  # type: ignore[method-assign]
    await poll(hass)
    assert dropped
    assert hass.states.get(DEHUMIDIFIER).state == STATE_ON
