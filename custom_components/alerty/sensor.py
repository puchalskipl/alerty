"""Encje sensor: aktywne alerty, liczniki per severity, dziennik, wyciszone."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .engine import Engine
from .entity import AlertyEntity

SUMMARY_ATTRS = (
    "errors_count",
    "warnings_count",
    "infos_count",
    "errors",
    "warnings",
    "infos",
    "active",
    "since",
    "mode",
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    engine: Engine = entry.runtime_data
    async_add_entities(
        [
            ActiveAlertsSensor(engine),
            CountSensor(engine, "errors_count", "mdi:alert-circle"),
            CountSensor(engine, "warnings_count", "mdi:alert"),
            CountSensor(engine, "infos_count", "mdi:information"),
            JournalSensor(engine),
            SnoozedSensor(engine),
        ]
    )


class ActiveAlertsSensor(AlertyEntity, SensorEntity):
    _attr_icon = "mdi:alert-circle"

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "active_alerts")

    @property
    def native_value(self) -> int:
        return self.engine.summary()["total"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self.engine.summary()
        return {key: summary[key] for key in SUMMARY_ATTRS}


class CountSensor(AlertyEntity, SensorEntity):
    def __init__(self, engine: Engine, key: str, icon: str) -> None:
        super().__init__(engine, key)
        self._key = key
        self._attr_icon = icon

    @property
    def native_value(self) -> int:
        return self.engine.summary()[self._key]


class JournalSensor(AlertyEntity, SensorEntity):
    _attr_icon = "mdi:history"

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "journal")

    @property
    def native_value(self) -> int:
        return self.engine.journal_view()["week"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.engine.journal_view()


class SnoozedSensor(AlertyEntity, SensorEntity):
    _attr_icon = "mdi:bell-sleep"

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "snoozed")

    @property
    def native_value(self) -> int:
        return len(self.engine.summary()["snoozed"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"items": self.engine.summary()["snoozed"]}
