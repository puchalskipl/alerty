"""Przełączniki kanałów: push na telefon i „Powiadomienia w HA” (dzwonek).

Zapisują do opcji wpisu; wyłączony kanał nic nie wysyła, po włączeniu nic nie jest nadrabiane.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import OPT_PERSISTENT_ENABLED, OPT_PUSH_ENABLED
from .engine import Engine
from .entity import AlertyEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    engine: Engine = entry.runtime_data
    async_add_entities(
        [
            ChannelSwitch(engine, entry, "push", OPT_PUSH_ENABLED, "mdi:cellphone-message"),
            ChannelSwitch(engine, entry, "persistent", OPT_PERSISTENT_ENABLED, "mdi:bell-badge"),
        ]
    )


class ChannelSwitch(AlertyEntity, SwitchEntity):
    def __init__(self, engine: Engine, entry, key: str, option: str, icon: str) -> None:
        super().__init__(engine, key)
        self._entry = entry
        self._option = option
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        if self._option == OPT_PUSH_ENABLED:
            return self.engine.options.push_enabled
        return self.engine.options.persistent_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)

    def _set(self, value: bool) -> None:
        # Update listener w __init__ przekaże nowe opcje silnikowi i odświeży encje.
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, self._option: value}
        )
