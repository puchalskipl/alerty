"""Minimalne atrapy modułów Home Assistant używanych przez engine/store/notifications.

Instalowane w sys.modules przy imporcie tego modułu (lokalnie nie ma HA).
Atrapy zapisują wywołania (timery, usługi, powiadomienia), żeby testy mogły
sprawdzić, co silnik zrobił, i ręcznie „przesuwać czas”.
"""

from __future__ import annotations

import asyncio
import enum
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


class Clock:
    now: datetime = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def advance(cls, **kwargs: float) -> datetime:
        cls.now = cls.now + timedelta(**kwargs)
        return cls.now


# --- homeassistant.core -------------------------------------------------------


class Event:
    def __init__(self, event_type: str, data: dict[str, Any]) -> None:
        self.event_type = event_type
        self.data = data


class State:
    def __init__(self, entity_id: str, state: str, attributes: dict[str, Any] | None = None) -> None:
        self.entity_id = entity_id
        self.state = state
        self.attributes = dict(attributes or {})


def callback(func):
    return func


class ServiceNotFound(Exception):
    pass


class HomeAssistantError(Exception):
    pass


class SupportsResponse(enum.Enum):
    NONE = "none"
    OPTIONAL = "optional"
    ONLY = "only"


# --- atrapa hass ------------------------------------------------------------


class FakeStates:
    def __init__(self, hass: "FakeHass") -> None:
        self._hass = hass
        self._states: dict[str, State] = {}

    def get(self, entity_id: str) -> State | None:
        return self._states.get(entity_id)

    def async_all(self, domain: str | None = None) -> list[State]:
        return [s for s in self._states.values() if domain is None or s.entity_id.startswith(domain + ".")]

    async def set(self, entity_id: str, state: str, attributes: dict[str, Any] | None = None) -> None:
        old = self._states.get(entity_id)
        attrs = dict(attributes if attributes is not None else (old.attributes if old else {}))
        new = State(entity_id, state, attrs)
        self._states[entity_id] = new
        await self._hass.bus.async_fire(
            "state_changed", {"entity_id": entity_id, "old_state": old, "new_state": new}
        )

    async def remove(self, entity_id: str) -> None:
        old = self._states.pop(entity_id, None)
        await self._hass.bus.async_fire(
            "state_changed", {"entity_id": entity_id, "old_state": old, "new_state": None}
        )


class FakeBus:
    def __init__(self) -> None:
        self._listeners: list[tuple[str, Callable, Callable | None]] = []

    def async_listen(self, event_type: str, listener: Callable, event_filter: Callable | None = None):
        item = (event_type, listener, event_filter)
        self._listeners.append(item)

        def _cancel() -> None:
            if item in self._listeners:
                self._listeners.remove(item)

        return _cancel

    async def async_fire(self, event_type: str, data: dict[str, Any]) -> None:
        for etype, listener, flt in list(self._listeners):
            if etype != event_type:
                continue
            if flt is not None and not flt(data):
                continue
            result = listener(Event(event_type, data))
            if asyncio.iscoroutine(result):
                await result


@dataclass
class ServiceCallRecord:
    domain: str
    service: str
    data: dict[str, Any]


class FakeServices:
    def __init__(self) -> None:
        self.calls: list[ServiceCallRecord] = []
        self.missing: set[tuple[str, str]] = set()
        self.registered: dict[tuple[str, str], Any] = {}

    async def async_call(self, domain: str, service: str, data: dict[str, Any] | None = None, blocking: bool = False, **_: Any) -> None:
        if (domain, service) in self.missing:
            raise ServiceNotFound(f"{domain}.{service}")
        self.calls.append(ServiceCallRecord(domain, service, dict(data or {})))

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.registered

    def async_register(self, domain: str, service: str, handler: Any, schema: Any = None, supports_response: Any = None) -> None:
        self.registered[(domain, service)] = handler

    def async_services_for_domain(self, domain: str) -> dict[str, Any]:
        return {s: h for (d, s), h in self.registered.items() if d == domain}


@dataclass
class Timer:
    delay: timedelta
    action: Callable
    cancelled: bool = False


class FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.states = FakeStates(self)
        self.bus = FakeBus()
        self.services = FakeServices()
        self.timers: list[Timer] = []
        self.intervals: list[tuple[Callable, timedelta]] = []
        self.time_changes: list[dict[str, Any]] = []
        self.signals: list[str] = []
        self.pn_callbacks: list[Callable] = []
        self.storage: dict[str, Any] = {}

    async def fire_timers(self) -> int:
        """Odpala wszystkie oczekujące timery (async_call_later) — jak upływ czasu."""
        pending = [t for t in self.timers if not t.cancelled]
        self.timers = []
        for timer in pending:
            result = timer.action(Clock.now)
            if asyncio.iscoroutine(result):
                await result
        return len(pending)

    async def tick(self) -> None:
        """Odpala akcje z async_track_time_interval (tick minutowy silnika)."""
        for action, _ in list(self.intervals):
            result = action(Clock.now)
            if asyncio.iscoroutine(result):
                await result

    def persistent(self) -> dict[str, dict[str, Any]]:
        return self.data.setdefault("persistent_notification", {})


# --- helpers.event / start / dispatcher / storage ------------------------------


def async_track_time_interval(hass: FakeHass, action: Callable, interval: timedelta, **_: Any):
    item = (action, interval)
    hass.intervals.append(item)
    return lambda: hass.intervals.remove(item) if item in hass.intervals else None


def async_call_later(hass: FakeHass, delay: timedelta | float, action: Callable):
    if not isinstance(delay, timedelta):
        delay = timedelta(seconds=float(delay))
    timer = Timer(delay, action)
    hass.timers.append(timer)

    def _cancel() -> None:
        timer.cancelled = True

    return _cancel


def async_track_time_change(hass: FakeHass, action: Callable, hour=None, minute=None, second=None):
    item = {"action": action, "hour": hour, "minute": minute, "second": second, "active": True}
    hass.time_changes.append(item)

    def _cancel() -> None:
        item["active"] = False

    return _cancel


def async_at_started(hass: FakeHass, at_start_cb: Callable):
    at_start_cb(hass)
    return lambda: None


def async_dispatcher_send(hass: FakeHass, signal: str, *args: Any) -> None:
    hass.signals.append(signal)


def async_dispatcher_connect(hass: FakeHass, signal: str, target: Callable):
    return lambda: None


class Store:
    """Zapisuje natychmiast do hass.storage (zamiast z opóźnieniem na dysk)."""

    def __init__(self, hass: FakeHass, version: int, key: str, **_: Any) -> None:
        self._hass = hass
        self.key = key

    def __class_getitem__(cls, item):
        return cls

    async def async_load(self) -> Any:
        return self._hass.storage.get(self.key)

    async def async_save(self, data: Any) -> None:
        self._hass.storage[self.key] = data

    def async_delay_save(self, data_func: Callable[[], Any], delay: float = 0) -> None:
        self._hass.storage[self.key] = data_func()


# --- components.persistent_notification ------------------------------------------


class UpdateType(enum.Enum):
    CURRENT = "current"
    ADDED = "added"
    REMOVED = "removed"
    UPDATED = "updated"


PN_DOMAIN = "persistent_notification"


def pn_async_create(hass: FakeHass, message: str, title: str | None = None, notification_id: str | None = None) -> None:
    store = hass.persistent()
    update = UpdateType.UPDATED if notification_id in store else UpdateType.ADDED
    store[notification_id] = {"message": message, "title": title, "notification_id": notification_id}
    for cb in list(hass.pn_callbacks):
        cb(update, {notification_id: store[notification_id]})


def pn_async_dismiss(hass: FakeHass, notification_id: str) -> None:
    store = hass.persistent()
    item = store.pop(notification_id, None)
    if item is None:
        return
    for cb in list(hass.pn_callbacks):
        cb(UpdateType.REMOVED, {notification_id: item})


def pn_async_register_callback(hass: FakeHass, cb: Callable):
    hass.pn_callbacks.append(cb)
    return lambda: hass.pn_callbacks.remove(cb) if cb in hass.pn_callbacks else None


# --- instalacja atrap w sys.modules -----------------------------------------------


def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


def install() -> None:
    if "homeassistant" in sys.modules and getattr(sys.modules["homeassistant"], "_alerty_fake", False):
        return
    ha = _module("homeassistant")
    ha._alerty_fake = True
    ha.__path__ = []
    _module(
        "homeassistant.core",
        HomeAssistant=FakeHass,
        Event=Event,
        State=State,
        callback=callback,
        ServiceCall=object,
        ServiceResponse=dict,
        SupportsResponse=SupportsResponse,
    )
    _module(
        "homeassistant.const",
        EVENT_STATE_CHANGED="state_changed",
        EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
        Platform=types.SimpleNamespace(SENSOR="sensor", SWITCH="switch"),
    )
    _module("homeassistant.exceptions", ServiceNotFound=ServiceNotFound, HomeAssistantError=HomeAssistantError)
    helpers = _module("homeassistant.helpers")
    helpers.__path__ = []
    _module(
        "homeassistant.helpers.event",
        async_track_time_interval=async_track_time_interval,
        async_call_later=async_call_later,
        async_track_time_change=async_track_time_change,
    )
    _module("homeassistant.helpers.start", async_at_started=async_at_started)
    _module(
        "homeassistant.helpers.dispatcher",
        async_dispatcher_send=async_dispatcher_send,
        async_dispatcher_connect=async_dispatcher_connect,
    )
    _module("homeassistant.helpers.storage", Store=Store)
    util = _module("homeassistant.util")
    util.__path__ = []
    _module("homeassistant.util.dt", now=lambda: Clock.now)
    components = _module("homeassistant.components")
    components.__path__ = []
    _module(
        "homeassistant.components.persistent_notification",
        DOMAIN=PN_DOMAIN,
        UpdateType=UpdateType,
        async_create=pn_async_create,
        async_dismiss=pn_async_dismiss,
        async_register_callback=pn_async_register_callback,
    )


install()
