"""Silnik na atrapie HA: nasłuch, cisza po starcie, catch-up, kanały, działania użytkownika, restart."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests import fakes  # instaluje atrapy homeassistant.* przed importem silnika
from tests.fakes import Clock, FakeHass

from custom_components.alerty.const import (
    OPT_PERSISTENT_ENABLED,
    OPT_PUSH_ENABLED,
    STORE_JOURNAL_KEY,
    STORE_STATE_KEY,
)
from custom_components.alerty.engine import Engine
from custom_components.alerty.options import Options
from custom_components.alerty.store import AlertyStore

EID = "binary_sensor.alert_ha_cpu_high"
ATTRS = {
    "friendly_name": "Home Assistant - wysokie obciążenie CPU",
    "severity": "error",
    "message": "14% przez 10 min (próg 10%).",
    "icon": "mdi:cpu-64-bit",
}


@pytest.fixture(autouse=True)
def _reset_clock():
    Clock.now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    yield


async def make_engine(hass: FakeHass, **options) -> Engine:
    store = AlertyStore(hass)
    state, journal = await store.async_load()
    engine = Engine(hass, Options.from_mapping(options), state, journal, store)
    await engine.async_start()
    return engine


async def start_with_grace(hass: FakeHass, **options) -> Engine:
    engine = await make_engine(hass, **options)
    assert await hass.fire_timers() == 1  # upływ ciszy po starcie → catch-up
    assert engine.ready
    return engine


async def test_events_in_grace_are_ignored_then_catch_up_sends():
    hass = FakeHass()
    engine = await make_engine(hass)
    await hass.states.set(EID, "on", ATTRS)
    assert hass.services.calls == [] and hass.persistent() == {}
    assert not engine.ready

    await hass.fire_timers()
    assert engine.ready
    assert len(hass.persistent()) == 1
    pid, note = next(iter(hass.persistent().items()))
    assert pid.startswith("alert_" + EID + "_")
    assert note["title"] == ATTRS["friendly_name"] and note["message"] == ATTRS["message"]
    assert [(c.domain, c.service) for c in hass.services.calls] == [("notify", "admins")]
    push = hass.services.calls[0].data
    assert push["data"]["tag"] == "alert_" + EID
    assert push["data"]["channel"] == "Alerty - błędy"
    assert engine.summary()["total"] == 1 and engine.state.active[EID].source == "catchup"
    entry = engine.journal.entries[0]
    assert entry.notified == {"push": ["admins"], "persistent": True, "simulated": False, "errors": []}
    assert hass.storage[STORE_STATE_KEY]["active"][EID]["persistent_id"] == pid
    assert hass.storage[STORE_JOURNAL_KEY]["entries"][0]["id"] == entry.id


async def test_channels_off_send_nothing_but_journal():
    hass = FakeHass()
    engine = await start_with_grace(hass, **{OPT_PUSH_ENABLED: False, OPT_PERSISTENT_ENABLED: False})
    await hass.states.set(EID, "on", ATTRS)
    assert hass.services.calls == [] and hass.persistent() == {}
    summary = engine.summary()
    assert summary["total"] == 1
    assert summary["push_enabled"] is False and summary["persistent_enabled"] is False
    entry = engine.journal.entries[0]
    assert entry.notified["push"] == [] and entry.notified["persistent"] is False
    assert engine.state.active[EID].persistent_id is None


async def test_resolve_updates_persistent_with_snapshot():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    pid = engine.state.active[EID].persistent_id
    Clock.advance(hours=2, minutes=15)
    await hass.states.set(EID, "off", {**ATTRS, "message": "5% (próg 10%)."})
    assert len(hass.services.calls) == 1  # bez push na OFF
    note = hass.persistent()[pid]
    assert note["title"] == "✅ " + ATTRS["friendly_name"]
    assert note["message"] == "14% przez 10 min (próg 10%). · ustąpiło 14:15, trwało 2 h 15 min"
    assert EID not in engine.state.active
    assert engine.journal.entries[0].duration_s == 8100
    assert engine.state.resolved_persistents == {pid: Clock.now}


async def test_user_dismissed_persistent_is_not_resurrected():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    pid = engine.state.active[EID].persistent_id
    fakes.pn_async_dismiss(hass, pid)  # użytkownik kliknął „zamknij” w dzwonku
    assert engine.state.active[EID].persistent_id is None
    await hass.states.set(EID, "off", ATTRS)
    assert hass.persistent() == {}


async def test_unavailable_keeps_record_and_attribute_change_updates_body_now():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    await hass.states.set(EID, "unavailable", {})
    assert EID in engine.state.active
    await hass.states.set(EID, "on", {**ATTRS, "message": "20%"})
    rec = engine.state.active[EID]
    assert rec.body_now == "20%" and rec.body_on == ATTRS["message"]
    assert len(hass.services.calls) == 1


async def test_removed_entity_rechecked_then_closed():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    pid = engine.state.active[EID].persistent_id
    await hass.states.remove(EID)
    assert EID in engine.state.active and len(hass.timers) == 1
    assert hass.timers[0].delay == timedelta(minutes=10)
    await hass.fire_timers()
    assert EID not in engine.state.active
    assert pid not in hass.persistent()
    assert engine.journal.entries[0].off_approx is True


async def test_removed_entity_that_returns_is_kept():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    await hass.states.remove(EID)
    await hass.states.set(EID, "on", ATTRS)  # wróciła po template.reload
    await hass.fire_timers()
    assert EID in engine.state.active and len(hass.services.calls) == 1


async def test_reminder_via_tick():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    Clock.advance(hours=23)
    await hass.tick()
    assert len(hass.services.calls) == 1
    Clock.advance(hours=1)
    await hass.tick()
    assert len(hass.services.calls) == 2
    assert hass.services.calls[1].data["data"]["tag"] == "alert_" + EID
    assert engine.journal.entries[0].reminders == 1


async def test_mute_dismiss_disable_actions():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await engine.async_user_action("mute", [EID])
    await hass.states.set(EID, "on", ATTRS)
    assert hass.services.calls == [] and hass.persistent() == {}
    summary = engine.summary()
    assert summary["total"] == 1 and summary["active"][0]["muted"] and summary["muted"] == [EID]
    assert hass.storage[STORE_STATE_KEY]["muted"] == [EID]

    await engine.async_user_action("unmute", [EID])
    Clock.advance(hours=30)
    await hass.tick()
    assert hass.services.calls == []  # trwające wystąpienie dalej ciche

    await engine.async_user_action("dismiss", [EID])
    assert engine.summary()["total"] == 0 and engine.summary()["dismissed"][0]["entity_id"] == EID
    await engine.async_user_action("undismiss", [EID])
    await engine.async_user_action("disable", [EID])
    assert engine.summary()["total"] == 0 and engine.summary()["disabled"] == [EID]
    await engine.async_user_action("enable", [EID])
    assert engine.summary()["total"] == 1 and hass.services.calls == []


async def test_push_error_is_recorded_not_raised():
    hass = FakeHass()
    hass.services.missing.add(("notify", "admins"))
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    assert engine.journal.entries[0].notified["errors"] == ["notify.admins: brak usługi"]
    assert len(hass.persistent()) == 1  # persistent mimo błędu push


async def test_cleanup_orphans_and_resolved():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    fakes.pn_async_create(hass, "stary", "Błąd", "alert_binary_sensor.alert_x_1790262322")
    fakes.pn_async_create(hass, "inne", "HACS", "hacs_update")
    await hass.states.set(EID, "on", ATTRS)
    pid = engine.state.active[EID].persistent_id
    await hass.states.set(EID, "off", ATTRS)
    Clock.advance(days=8)
    dismissed = await engine.async_cleanup(orphans=True)
    assert dismissed == 2
    assert set(hass.persistent()) == {"hacs_update"}
    assert pid not in hass.persistent()


async def test_apply_options_switches_channels_and_reschedules_cleanup():
    hass = FakeHass()
    engine = await start_with_grace(hass, **{OPT_PUSH_ENABLED: False})
    assert [t["active"] for t in hass.time_changes] == [True]
    engine.apply_options(Options.from_mapping({"cleanup_time": "03:00:00"}))
    assert [t["active"] for t in hass.time_changes] == [False, True]
    assert hass.time_changes[1]["hour"] == 3
    await hass.states.set(EID, "on", ATTRS)
    assert len(hass.services.calls) == 1


async def test_restart_restores_record_and_persistent_without_push():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    pid = engine.state.active[EID].persistent_id
    on_ts = engine.state.active[EID].on_ts
    await engine.async_stop()

    # „Restart”: nowy hass z tym samym storage; persistenty w RAM przepadły.
    hass2 = FakeHass()
    hass2.storage = hass.storage
    await hass2.states.set(EID, "on", ATTRS)
    Clock.advance(minutes=30)
    engine2 = await start_with_grace(hass2)
    assert engine2.state.active[EID].on_ts == on_ts  # „od” sprzed restartu
    assert pid in hass2.persistent() and hass2.persistent()[pid]["message"] == ATTRS["message"]
    assert hass2.services.calls == []  # bez ponownego push
    assert len(engine2.journal.entries) == 1


async def test_restart_closes_alert_that_resolved_while_down():
    hass = FakeHass()
    engine = await start_with_grace(hass)
    await hass.states.set(EID, "on", ATTRS)
    await engine.async_stop()

    hass2 = FakeHass()
    hass2.storage = hass.storage
    await hass2.states.set(EID, "off", ATTRS)
    Clock.advance(minutes=30)
    engine2 = await start_with_grace(hass2)
    assert EID not in engine2.state.active
    entry = engine2.journal.entries[0]
    assert entry.off is not None and entry.off_approx is True
