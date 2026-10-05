"""Przełącznik trybu: on = wysyłanie (normal), off = obserwacja. Zapisuje do opcji wpisu."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MODE_NORMAL, MODE_OBSERVE, OPT_MODE
from .engine import Engine
from .entity import AlertyEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([SendingSwitch(entry.runtime_data, entry)])


class SendingSwitch(AlertyEntity, SwitchEntity):
    _attr_icon = "mdi:bell-ring"

    def __init__(self, engine: Engine, entry) -> None:
        super().__init__(engine, "sending")
        self._entry = entry

    @property
    def is_on(self) -> bool:
        return self.engine.options.sending

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set_mode(MODE_NORMAL)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set_mode(MODE_OBSERVE)

    def _set_mode(self, mode: str) -> None:
        # Update listener w __init__ przekaże nowe opcje silnikowi i odświeży encje.
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, OPT_MODE: mode}
        )
