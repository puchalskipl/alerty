"""Silnik integracji: spina zdarzenia HA z logiką (logic.py) i wykonuje akcje.

Odpowiada za: nasłuch `state_changed` encji alertów (tani filtr prefiksu),
ciszę po starcie i catch-up, tick minutowy (przypomnienia, wyciszenia),
sprzątanie dzienne, rejestr powiadomień persistent, zapis stanu i dziennika
oraz powiadamianie encji integracji o zmianach.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import logic
from .const import ALERT_PREFIX, SIGNAL_UPDATED
from .journal import Journal
from .logic import (
    Action,
    AlertDef,
    CreatePersistent,
    DismissPersistent,
    JournalClose,
    JournalMark,
    JournalOpen,
    ScheduleRecheck,
    SendPush,
    State,
)
from .notifications import Notifier
from .options import Options
from .store import AlertyStore

_LOGGER = logging.getLogger(__name__)

TICK_INTERVAL = timedelta(seconds=60)


@callback
def _is_alert_event(data: dict[str, Any]) -> bool:
    entity_id = data.get("entity_id")
    return isinstance(entity_id, str) and entity_id.startswith(ALERT_PREFIX)


class Engine:
    """Jedna instancja na wpis konfiguracyjny."""

    def __init__(
        self,
        hass: HomeAssistant,
        options: Options,
        state: State,
        journal: Journal,
        store: AlertyStore,
        notifier: Notifier | None = None,
    ) -> None:
        self.hass = hass
        self.options = options
        self.state = state
        self.journal = journal
        self.store = store
        self.notifier = notifier or Notifier(hass)
        self.ready = False
        self.existing_persistents: set[str] = set()
        self._unsubs: list[Callable[[], None]] = []
        self._rechecks: dict[str, Callable[[], None]] = {}
        self._cleanup_unsub: Callable[[], None] | None = None
        self._grace_unsub: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Cykl życia
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        self.existing_persistents = set(self._current_persistent_ids())
        self._unsubs.append(
            persistent_notification.async_register_callback(self.hass, self._on_persistent_update)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._on_state_changed, event_filter=_is_alert_event
            )
        )
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, TICK_INTERVAL))
        self._schedule_cleanup()
        self._unsubs.append(async_at_started(self.hass, self._on_started))
        self._notify_entities()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for unsub in self._rechecks.values():
            unsub()
        self._rechecks.clear()
        if self._cleanup_unsub:
            self._cleanup_unsub()
            self._cleanup_unsub = None
        if self._grace_unsub:
            self._grace_unsub()
            self._grace_unsub = None
        await self.store.async_save_now(self.state, self.journal)

    def apply_options(self, options: Options) -> None:
        """Nowe opcje z UI — bez przeładowania wpisu."""
        self.options = options
        self._schedule_cleanup()
        self._notify_entities()
        _LOGGER.debug("Opcje zaktualizowane: tryb %s", options.mode)

    # ------------------------------------------------------------------
    # Zdarzenia
    # ------------------------------------------------------------------

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        grace = self.options.startup_grace
        _LOGGER.info("HA wystartował — cisza %s, potem catch-up", grace)
        self._grace_unsub = async_call_later(self.hass, grace, self._on_grace_elapsed)

    async def _on_grace_elapsed(self, _now: datetime) -> None:
        self._grace_unsub = None
        await self.async_catch_up()

    async def async_catch_up(self) -> None:
        """Uzgodnij stan z rzeczywistością (po starcie, po przeładowaniu)."""
        snapshot: dict[str, tuple[AlertDef | None, str | None]] = {}
        for st in self.hass.states.async_all("binary_sensor"):
            if not logic.is_alert_entity(st.entity_id):
                continue
            snapshot[st.entity_id] = (
                AlertDef.from_attributes(st.entity_id, st.attributes),
                logic.effective_state(st.state),
            )
        self.existing_persistents = set(self._current_persistent_ids())
        actions = logic.catch_up(
            self.state, snapshot, self.existing_persistents, self._now(), self.options
        )
        self.ready = True
        await self._execute(actions)
        _LOGGER.info(
            "Catch-up: %d encji alertów, %d aktywnych, %d akcji",
            len(snapshot),
            len(self.state.active),
            len(actions),
        )

    async def _on_state_changed(self, event: Event) -> None:
        if not self.ready:
            return
        entity_id: str = event.data["entity_id"]
        new_state = event.data.get("new_state")
        if new_state is None:
            if entity_id in self.state.active:
                await self._execute([ScheduleRecheck(entity_id, timedelta(seconds=logic.MISSING_RECHECK_S))])
            return
        defn = AlertDef.from_attributes(entity_id, new_state.attributes)
        effective = logic.effective_state(new_state.state)
        actions = logic.handle_state(
            self.state,
            entity_id,
            defn,
            effective,
            self._now(),
            self.options,
            self.existing_persistents,
        )
        await self._execute(actions)

    async def _on_tick(self, _now: datetime) -> None:
        if not self.ready:
            return
        await self._execute(logic.tick(self.state, self._now(), self.options))

    async def _on_cleanup(self, _now: datetime) -> None:
        await self.async_cleanup(orphans=False)

    @callback
    def _on_persistent_update(self, update_type: Any, notifications: dict[str, Any]) -> None:
        name = getattr(update_type, "value", str(update_type)).lower()
        ids = set(notifications)
        if name == "current":
            self.existing_persistents = ids
        elif name in ("added", "updated"):
            self.existing_persistents |= ids
        elif name == "removed":
            self.existing_persistents -= ids
            changed = False
            for notification_id in ids:
                changed = logic.persistent_removed(self.state, notification_id) or changed
            if changed:
                self._save()

    async def _on_recheck(self, entity_id: str) -> None:
        self._rechecks.pop(entity_id, None)
        still_missing = self.hass.states.get(entity_id) is None
        actions = logic.handle_missing(
            self.state,
            entity_id,
            self._now(),
            self.options,
            self.existing_persistents,
            still_missing,
        )
        if still_missing:
            _LOGGER.info("Encja %s zniknęła na stałe — domykam wystąpienie", entity_id)
        await self._execute(actions)

    # ------------------------------------------------------------------
    # Usługi
    # ------------------------------------------------------------------

    async def async_snooze(self, entity_ids: Iterable[str], duration: timedelta) -> None:
        until = self._now() + duration
        actions: list[Action] = []
        for entity_id in entity_ids:
            actions.extend(logic.snooze(self.state, entity_id, until))
        await self._execute(actions)

    async def async_unsnooze(self, entity_ids: Iterable[str]) -> None:
        actions: list[Action] = []
        for entity_id in entity_ids:
            actions.extend(logic.unsnooze(self.state, entity_id, self._now(), self.options))
        await self._execute(actions)

    async def async_acknowledge(self, entity_ids: Iterable[str]) -> None:
        actions: list[Action] = []
        for entity_id in entity_ids:
            actions.extend(logic.acknowledge(self.state, entity_id))
        await self._execute(actions)

    async def async_disable(self, entity_ids: Iterable[str]) -> None:
        actions: list[Action] = []
        for entity_id in entity_ids:
            actions.extend(logic.disable(self.state, entity_id))
        await self._execute(actions)

    async def async_enable(self, entity_ids: Iterable[str]) -> None:
        actions: list[Action] = []
        for entity_id in entity_ids:
            actions.extend(logic.enable(self.state, entity_id, self._now(), self.options))
        await self._execute(actions)

    async def async_cleanup(self, orphans: bool) -> int:
        now = self._now()
        self.existing_persistents = set(self._current_persistent_ids())
        actions = logic.cleanup(
            self.state, now, self.options, self.existing_persistents, orphans=orphans
        )
        pruned = self.journal.prune(now, self.options.retention_days)
        await self._execute(actions)
        dismissed = sum(1 for a in actions if isinstance(a, DismissPersistent))
        _LOGGER.info("Sprzątanie: %d persistentów, %d wpisów dziennika", dismissed, pruned)
        return dismissed

    # ------------------------------------------------------------------
    # Widoki dla encji
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        data = logic.active_summary(self.state, self._now())
        data["mode"] = self.options.mode
        return data

    def journal_view(self) -> dict[str, Any]:
        now = self._now()
        counts = self.journal.counts(now)
        return {
            "recent": self.journal.recent(),
            "top_30d": self.journal.top(now),
            "today": counts["today"],
            "week": counts["week"],
            "entries": len(self.journal.entries),
        }

    # ------------------------------------------------------------------
    # Wykonanie akcji
    # ------------------------------------------------------------------

    async def _execute(self, actions: list[Action]) -> None:
        sending = self.options.sending
        for action in actions:
            if isinstance(action, JournalOpen):
                self.journal.open(action.entry)
            elif isinstance(action, JournalClose):
                self.journal.close(action.journal_id, action.entity_id, action.off, action.off_approx)
            elif isinstance(action, JournalMark):
                self.journal.mark(
                    action.journal_id,
                    snoozed=action.snoozed,
                    acknowledged=action.acknowledged,
                    disabled=action.disabled,
                    reminders=action.reminders,
                )
            elif isinstance(action, CreatePersistent):
                self.notifier.create_persistent(action, sending)
                if sending:
                    self.existing_persistents.add(action.notification_id)
                if not action.restore:
                    self.journal.note_persistent(action.journal_id, simulated=not sending)
            elif isinstance(action, DismissPersistent):
                self.notifier.dismiss_persistent(action, sending)
                self.existing_persistents.discard(action.notification_id)
            elif isinstance(action, SendPush):
                errors = await self.notifier.async_send_push(action, sending)
                self.journal.note_push(action.journal_id, action.targets, simulated=not sending)
                for error in errors:
                    self.journal.note_error(action.journal_id, error)
            elif isinstance(action, ScheduleRecheck):
                self._schedule_recheck(action.entity_id, action.delay)
        self._save()
        self._notify_entities()

    def _schedule_recheck(self, entity_id: str, delay: timedelta) -> None:
        if (unsub := self._rechecks.pop(entity_id, None)) is not None:
            unsub()

        async def _fire(_now: datetime) -> None:
            await self._on_recheck(entity_id)

        self._rechecks[entity_id] = async_call_later(self.hass, delay, _fire)

    def _schedule_cleanup(self) -> None:
        if self._cleanup_unsub:
            self._cleanup_unsub()
        t = self.options.cleanup_time
        self._cleanup_unsub = async_track_time_change(
            self.hass, self._on_cleanup, hour=t.hour, minute=t.minute, second=0
        )

    def _current_persistent_ids(self) -> Iterable[str]:
        """Aktualne powiadomienia w dzwonku (słownik integracji persistent_notification)."""
        data = self.hass.data.get(persistent_notification.DOMAIN)
        if isinstance(data, dict):
            return list(data)
        return []

    def _save(self) -> None:
        self.store.schedule_save(self.state, self.journal)

    def _notify_entities(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    @staticmethod
    def _now() -> datetime:
        return dt_util.now()
