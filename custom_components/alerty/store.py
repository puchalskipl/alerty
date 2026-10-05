"""Trwały zapis stanu i dziennika w .storage/alerty/*."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION, STORE_JOURNAL_KEY, STORE_STATE_KEY
from .journal import Journal
from .logic import State

_LOGGER = logging.getLogger(__name__)

STATE_SAVE_DELAY = 2
JOURNAL_SAVE_DELAY = 5


class AlertyStore:
    """Dwa Store'y: mały stan (rekordy aktywne, wyciszenia) i dziennik (append-only)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._state_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORE_STATE_KEY, atomic_writes=True
        )
        self._journal_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORE_JOURNAL_KEY, atomic_writes=True
        )

    async def async_load(self) -> tuple[State, Journal]:
        state_raw = await self._state_store.async_load()
        journal_raw = await self._journal_store.async_load()
        state = State.from_dict(state_raw if isinstance(state_raw, dict) else None)
        journal = Journal.from_dict(journal_raw if isinstance(journal_raw, dict) else None)
        _LOGGER.debug(
            "Wczytano stan: %d aktywnych, dziennik: %d wpisów",
            len(state.active),
            len(journal.entries),
        )
        return state, journal

    def schedule_save(self, state: State, journal: Journal) -> None:
        """Zapis z opóźnieniem — wiele zdarzeń naraz to jeden zapis na dysk."""
        self._state_store.async_delay_save(state.to_dict, STATE_SAVE_DELAY)
        if journal.dirty:
            journal.dirty = False
            self._journal_store.async_delay_save(journal.to_dict, JOURNAL_SAVE_DELAY)

    async def async_save_now(self, state: State, journal: Journal) -> None:
        await self._state_store.async_save(state.to_dict())
        journal.dirty = False
        await self._journal_store.async_save(journal.to_dict())
