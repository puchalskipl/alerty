"""Odstęp przypomnień per poziom (błędy / ostrzeżenia / informacje) w godzinach.

Zapisują do opcji wpisu (`reminder_<poziom>`, sekundy) — to samo pole co w opcjach
integracji; 0 = bez przypomnień. Atrybut `push_targets` mówi, czy poziom domyślnie wysyła
push (przypomnienia dostaje tylko alert z adresatami push; atrybut `notify_targets` alertu nadpisuje).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import OPT_REMINDER, SEVERITIES
from .engine import Engine
from .entity import AlertyEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    engine: Engine = entry.runtime_data
    async_add_entities(ReminderNumber(engine, entry, sev) for sev in SEVERITIES)


class ReminderNumber(AlertyEntity, NumberEntity):
    _attr_icon = "mdi:bell-ring-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 168
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.HOURS

    def __init__(self, engine: Engine, entry, severity: str) -> None:
        super().__init__(engine, f"reminder_{severity}")
        self._entry = entry
        self._severity = severity

    @property
    def native_value(self) -> float:
        interval = self.engine.options.reminder[self._severity]
        return round(interval.total_seconds() / 3600, 2) if interval else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"push_targets": list(self.engine.options.push_targets[self._severity])}

    async def async_set_native_value(self, value: float) -> None:
        # Update listener w __init__ przekaże nowe opcje silnikowi i odświeży encje.
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={
                **self._entry.options,
                OPT_REMINDER.format(severity=self._severity): int(round(value * 3600)),
            },
        )
