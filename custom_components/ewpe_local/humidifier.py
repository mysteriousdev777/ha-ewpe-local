"""The dehumidifier: power, target humidity, mode; the set_state action."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.humidifier import (
    ATTR_HUMIDITY,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.const import ATTR_MODE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import EwpeConfigEntry, EwpeCoordinator
from .entity import EwpeEntity
from .protocol import (
    FAN,
    HUMIDITY,
    MODE,
    POWER,
    TARGET,
    TARGET_STEP,
    as_bool,
    as_int,
    code_from_target,
    encode,
    target_from_code,
)

PARALLEL_UPDATES = 1

SERVICE_SET_STATE = "set_state"
ATTR_POWER = "power"
ATTR_FAN_SPEED = "fan_speed"
# Several settings in one command, so one beep: the standard actions take one each.
SET_STATE_SCHEMA = vol.All(
    cv.has_at_least_one_key(ATTR_POWER, ATTR_MODE, ATTR_HUMIDITY, ATTR_FAN_SPEED),
    cv.make_entity_service_schema(
        {
            vol.Optional(ATTR_POWER): cv.boolean,
            vol.Optional(ATTR_MODE): cv.string,
            vol.Optional(ATTR_HUMIDITY): vol.Coerce(int),
            vol.Optional(ATTR_FAN_SPEED): cv.string,
        }
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EwpeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([EwpeDehumidifier(entry.runtime_data)])
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_SET_STATE, SET_STATE_SCHEMA, "async_set_state"
    )


class EwpeDehumidifier(EwpeEntity, HumidifierEntity):
    """Power (`Pow`), target humidity (`Dwet`), mode (`Dmod`)."""

    _attr_name = None
    _attr_translation_key = "dehumidifier"
    _attr_device_class = HumidifierDeviceClass.DEHUMIDIFIER
    _attr_supported_features = HumidifierEntityFeature.MODES
    _attr_target_humidity_step = TARGET_STEP

    def __init__(self, coordinator: EwpeCoordinator) -> None:
        super().__init__(coordinator, "dehumidifier")
        model = coordinator.model
        self._attr_min_humidity = model.target_min
        self._attr_max_humidity = model.target_max
        self._attr_available_modes = list(model.modes.values())

    @property
    def is_on(self) -> bool | None:
        return as_bool(self.raw(POWER))

    @property
    def mode(self) -> str | None:
        return self.coordinator.mode

    @property
    def current_humidity(self) -> int | None:
        return as_int(self.raw(HUMIDITY))

    @property
    def target_humidity(self) -> int | None:
        """None in modes without a target (the panel hides it there too)."""
        if self.coordinator.locked(TARGET):
            return None
        return target_from_code(self.raw(TARGET))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(POWER, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(POWER, 0)

    async def async_set_humidity(self, humidity: int) -> None:
        await self.coordinator.async_set(TARGET, code_from_target(humidity))

    async def async_set_mode(self, mode: str) -> None:
        await self.coordinator.async_set(MODE, self._mode_code(mode))

    async def async_set_state(
        self,
        power: bool | None = None,
        mode: str | None = None,
        humidity: int | None = None,
        fan_speed: str | None = None,
    ) -> None:
        """The set_state action: what is given, in one command."""
        model = self.coordinator.model
        values: dict[str, int] = {}
        if mode is not None:
            values[MODE] = self._mode_code(mode)
        if humidity is not None:
            if not model.target_min <= humidity <= model.target_max:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="humidity_out_of_range",
                    translation_placeholders={
                        "humidity": str(humidity),
                        "min": str(model.target_min),
                        "max": str(model.target_max),
                    },
                )
            values[TARGET] = code_from_target(humidity)
        if fan_speed is not None:
            code = encode(model.fan_speeds, fan_speed)
            if code is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_fan_speed",
                    translation_placeholders={"fan_speed": fan_speed},
                )
            values[FAN] = code
        if power is not None:
            values[POWER] = int(power)
        await self.coordinator.async_set_many(values)

    def _mode_code(self, mode: str) -> int:
        code = encode(self.coordinator.model.modes, mode)
        if code is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_mode",
                translation_placeholders={"mode": mode},
            )
        return code
