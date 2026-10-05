"""Wspólna baza encji integracji: aktualizacja przez dispatcher, stałe entity_id."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, ENTITY_IDS, SIGNAL_UPDATED
from .engine import Engine


class AlertyEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: Engine, key: str) -> None:
        self.engine = engine
        self._attr_translation_key = key
        self._attr_unique_id = f"{DOMAIN}_{key}"
        # Sugerowany entity_id (polski, jak w dotychczasowych szablonach) — HA użyje go
        # tylko przy pierwszej rejestracji; później obowiązuje rejestr encji.
        self.entity_id = ENTITY_IDS[key]

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
