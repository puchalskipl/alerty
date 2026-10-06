"""Tabela decyzyjna integracji alerty — każdy wiersz planu jako test."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.alerty import logic
from custom_components.alerty.const import (
    ON_RESOLVE_DISMISS,
    OPT_ON_RESOLVE,
    OPT_PERSISTENT_ENABLED,
    OPT_PUSH_ENABLED,
    OPT_PUSH_TARGETS,
)
from custom_components.alerty.logic import (
    ActiveRecord,
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
from custom_components.alerty.options import Options

EID = "binary_sensor.alert_ha_cpu_high"
TAG = "alert_binary_sensor.alert_ha_cpu_high"
PID = f"{TAG}_{int(datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc).timestamp())}"


def attrs(**overrides):
    base = {
        "friendly_name": "Home Assistant - wysokie obciążenie CPU",
        "severity": "error",
        "message": "14% przez 10 min (próg 10%).",
        "icon": "mdi:cpu-64-bit",
    }
    base.update(overrides)
    return base


def defn(**overrides) -> AlertDef:
    result = AlertDef.from_attributes(EID, attrs(**overrides))
    assert result is not None
    return result


def opts(**overrides) -> Options:
    return Options.from_mapping(overrides)


def only(actions, kind):
    found = [a for a in actions if isinstance(a, kind)]
    return found


# --------------------------------------------------------------------------
# AlertDef.from_attributes
# --------------------------------------------------------------------------


def test_alert_def_requires_severity():
    assert AlertDef.from_attributes(EID, {"friendly_name": "x"}) is None
    assert AlertDef.from_attributes(EID, {"severity": "critical"}) is None
    assert AlertDef.from_attributes(EID, None) is None


def test_alert_def_title_and_body_fallbacks():
    d = defn()
    assert d.title == "Home Assistant - wysokie obciążenie CPU"
    assert d.body == "14% przez 10 min (próg 10%)."
    assert defn(title="Własny tytuł").title == "Własny tytuł"
    assert defn(message="   ").body == "Home Assistant - wysokie obciążenie CPU"
    no_name = AlertDef.from_attributes(EID, {"severity": "INFO"})
    assert no_name is not None
    assert no_name.title == "alert_ha_cpu_high"
    assert no_name.severity == "info"


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("['admins']", ("admins",)),
        ("[]", ()),
        ("", ()),
        (["notify.mobile", "admins"], ("mobile", "admins")),
        ("admins, mobile", ("admins", "mobile")),
        ("admins", ("admins",)),
        ("mobile_app_lukasz_note15pro", ("mobile_app_lukasz_note15pro",)),
        (42, ()),
    ],
)
def test_parse_targets(value, expected):
    assert logic.parse_targets(value) == expected


def test_alert_def_overrides():
    d = defn(
        notify_targets="['mobile']",
        notify_persistent="false",
        notify_channel=" Alarm ",
        notify_click_path="/lovelace/kosiarka",
    )
    assert d.push_targets == ("mobile",)
    assert d.persistent is False
    assert d.channel == "Alarm"
    assert d.click_path == "/lovelace/kosiarka"
    assert defn(notify_channel="  ").channel is None
    assert defn(notify_persistent=True).persistent is True
    assert defn().persistent is None


# --------------------------------------------------------------------------
# #1 / #2 — włączenie alertu
# --------------------------------------------------------------------------


def test_row1_error_default_creates_persistent_and_push(now):
    state = State()
    actions = logic.handle_state(state, EID, defn(), "on", now, opts())

    assert isinstance(actions[0], JournalOpen)
    entry = actions[0].entry
    assert entry.entity_id == EID and entry.body == "14% przez 10 min (próg 10%)."
    persistent = only(actions, CreatePersistent)
    assert persistent == [
        CreatePersistent(PID, "Home Assistant - wysokie obciążenie CPU", "14% przez 10 min (próg 10%).", entry.id)
    ]
    push = only(actions, SendPush)
    assert len(push) == 1
    assert push[0].targets == ("admins",)
    assert push[0].title == entry.title and push[0].message == entry.body
    assert push[0].reminder is False
    data = push[0].data
    assert data["tag"] == TAG
    assert data["channel"] == "Alerty - błędy"
    assert data["importance"] == "high"
    assert data["priority"] == "high" and data["ttl"] == 0
    assert data["clickAction"] == "/lovelace/system"
    assert "notification_icon" not in data  # push bez ikony (decyzja usera 2026-10-06)

    rec = state.active[EID]
    assert rec.persistent_id == PID and rec.push_sent and rec.last_push_ts == now
    assert rec.body_on == rec.body_now == entry.body
    assert rec.journal_id == entry.id


def test_row1_warning_default_only_persistent(now):
    state = State()
    actions = logic.handle_state(state, EID, defn(severity="warning"), "on", now, opts())
    assert len(only(actions, CreatePersistent)) == 1
    assert only(actions, SendPush) == []
    assert state.active[EID].targets == ()


def test_row1_info_default_only_journal(now):
    state = State()
    actions = logic.handle_state(state, EID, defn(severity="info"), "on", now, opts())
    assert [type(a) for a in actions] == [JournalOpen]
    assert state.active[EID].persistent_id is None


def test_row1_per_alert_overrides_win(now):
    state = State()
    d = defn(
        severity="info",
        notify_targets="['mobile']",
        notify_persistent=True,
        notify_channel="Alarm",
        notify_click_path="/lovelace/kosiarka",
    )
    actions = logic.handle_state(state, EID, d, "on", now, opts())
    assert len(only(actions, CreatePersistent)) == 1
    push = only(actions, SendPush)[0]
    assert push.targets == ("mobile",)
    assert push.data["channel"] == "Alarm"
    assert push.data["clickAction"] == "/lovelace/kosiarka"
    assert push.data["importance"] == "low"
    assert "priority" not in push.data and "ttl" not in push.data


def test_row1_global_targets_from_options(now):
    state = State()
    o = opts(**{OPT_PUSH_TARGETS.format(severity="warning"): ["notify.mobile"]})
    actions = logic.handle_state(state, EID, defn(severity="warning"), "on", now, o)
    assert only(actions, SendPush)[0].targets == ("mobile",)


def test_row1_empty_targets_means_no_push(now):
    state = State()
    actions = logic.handle_state(state, EID, defn(notify_targets="[]"), "on", now, opts())
    assert only(actions, SendPush) == []
    assert len(only(actions, CreatePersistent)) == 1


def test_row2_muted_only_journal_but_visible(now):
    state = State()
    state.muted.add(EID)
    actions = logic.handle_state(state, EID, defn(), "on", now, opts())
    assert [type(a) for a in actions] == [JournalOpen]
    assert actions[0].entry.muted is True
    rec = state.active[EID]
    assert rec.silenced and not rec.notified
    summary = logic.active_summary(state, now)
    assert summary["total"] == 1 and summary["errors_count"] == 1  # wyciszony jest na liście
    assert summary["active"][0]["muted"] is True and summary["muted"] == [EID]


def test_row2_disabled_only_journal_and_hidden(now):
    state = State()
    state.disabled.add(EID)
    actions = logic.handle_state(state, EID, defn(), "on", now, opts())
    assert [type(a) for a in actions] == [JournalOpen]
    assert actions[0].entry.disabled is True
    assert state.active[EID].silenced
    summary = logic.active_summary(state, now)
    assert summary["total"] == 0 and summary["active"] == [] and summary["disabled"] == [EID]


def test_on_without_severity_is_ignored(now):
    state = State()
    assert logic.handle_state(state, EID, None, "on", now, opts()) == []
    assert state.active == {}


def test_duplicate_on_event_is_noop(now):
    state = State()
    logic.handle_state(state, EID, defn(), "on", now, opts())
    later = now + timedelta(minutes=5)
    assert logic.handle_state(state, EID, defn(), "on", later, opts()) == []
    assert state.active[EID].on_ts == now


# --------------------------------------------------------------------------
# #3 / #4 — ustąpienie
# --------------------------------------------------------------------------


def _activate(state, now, d=None, o=None):
    actions = logic.handle_state(state, EID, d or defn(), "on", now, o or opts())
    return actions[0].entry.id


def test_row3_resolve_updates_persistent_with_on_snapshot(now):
    state = State()
    journal_id = _activate(state, now)
    off = now + timedelta(hours=2, minutes=15)
    # Treść w chwili OFF jest już „zdrowa” — persistent ma dostać snapshot z ON.
    actions = logic.handle_state(
        state, EID, defn(message="5% (próg 10%)."), "off", off, opts(), {PID}
    )
    assert actions[0] == JournalClose(EID, journal_id, off, False)
    persistent = only(actions, CreatePersistent)[0]
    assert persistent.notification_id == PID
    assert persistent.title == "✅ Home Assistant - wysokie obciążenie CPU"
    assert persistent.message == "14% przez 10 min (próg 10%). · ustąpiło 14:15, trwało 2 h 15 min"
    assert only(actions, SendPush) == []
    assert EID not in state.active
    assert state.resolved_persistents[PID] == off


def test_row3_resolve_dismiss_option(now):
    state = State()
    _activate(state, now)
    o = opts(**{OPT_ON_RESOLVE: ON_RESOLVE_DISMISS})
    actions = logic.handle_state(state, EID, defn(), "off", now + timedelta(hours=1), o, {PID})
    assert only(actions, DismissPersistent) == [DismissPersistent(PID)]
    assert only(actions, CreatePersistent) == []
    assert state.resolved_persistents == {}


def test_row3_resolve_when_user_closed_persistent(now):
    state = State()
    _activate(state, now)
    logic.persistent_removed(state, PID)
    actions = logic.handle_state(state, EID, defn(), "off", now + timedelta(hours=1), opts(), set())
    assert [type(a) for a in actions] == [JournalClose]
    assert state.resolved_persistents == {}


def test_row3_resolve_when_persistent_vanished(now):
    state = State()
    _activate(state, now)
    actions = logic.handle_state(state, EID, defn(), "off", now + timedelta(hours=1), opts(), set())
    assert [type(a) for a in actions] == [JournalClose]


def test_row4_off_without_record_closes_orphan_journal(now):
    state = State()
    actions = logic.handle_state(state, EID, defn(), "off", now, opts())
    assert actions == [JournalClose(EID, None, now, True)]


# --------------------------------------------------------------------------
# #5 / #6 — unavailable i zmiana atrybutów
# --------------------------------------------------------------------------


def test_row5_unavailable_keeps_record(now):
    state = State()
    _activate(state, now)
    assert logic.handle_state(state, EID, None, None, now + timedelta(minutes=1), opts()) == []
    assert EID in state.active


def test_row6_attribute_change_updates_body_now_only(now):
    state = State()
    _activate(state, now)
    actions = logic.handle_state(
        state, EID, defn(message="22% przez 10 min (próg 10%)."), "on", now + timedelta(minutes=3), opts()
    )
    assert actions == []
    rec = state.active[EID]
    assert rec.body_now == "22% przez 10 min (próg 10%)."
    assert rec.body_on == "14% przez 10 min (próg 10%)."


# --------------------------------------------------------------------------
# #7 / #8 — brak encji i catch-up po starcie
# --------------------------------------------------------------------------


def test_row8_catch_up(now):
    state = State()
    _activate(state, now)  # EID aktywny, persistent utworzony przed restartem
    gone = "binary_sensor.alert_gone"
    state.active[gone] = ActiveRecord(gone, "Gone", "info", "x", now, "j-gone")
    off_eid = "binary_sensor.alert_was_on"
    state.active[off_eid] = ActiveRecord(
        off_eid, "Was on", "warning", "y", now - timedelta(hours=3), "j-off", persistent_id="alert_" + off_eid
    )
    new_eid = "binary_sensor.alert_new"
    new_def = AlertDef.from_attributes(new_eid, {"severity": "warning", "friendly_name": "New", "message": "n"})
    unavailable_eid = "binary_sensor.alert_unavail"

    later = now + timedelta(minutes=6)
    snapshot = {
        EID: (defn(message="15%"), "on"),
        off_eid: (None, "off"),
        new_eid: (new_def, "on"),
        unavailable_eid: (None, None),
    }
    actions = logic.catch_up(state, snapshot, existing_persistents=set(), now=later, opts=opts())

    assert ScheduleRecheck(gone, timedelta(minutes=10)) in actions
    restore = [a for a in only(actions, CreatePersistent) if a.restore]
    assert restore == [CreatePersistent(PID, "Home Assistant - wysokie obciążenie CPU", "14% przez 10 min (próg 10%).", state.active[EID].journal_id, restore=True)]
    assert state.active[EID].body_now == "15%"
    assert only(actions, SendPush) == []  # odtworzenie persistenta nie wysyła push
    assert JournalClose(off_eid, "j-off", later, True) in actions
    assert off_eid not in state.active
    opened = only(actions, JournalOpen)
    assert len(opened) == 1 and opened[0].entry.entity_id == new_eid and opened[0].entry.source == "catchup"
    assert state.active[new_eid].source == "catchup"
    assert unavailable_eid not in state.active
    assert gone in state.active  # decyzja dopiero po ponownym sprawdzeniu


def test_row7_missing_entity_resolved_with_dismiss(now):
    state = State()
    _activate(state, now)
    later = now + timedelta(minutes=10)
    assert logic.handle_missing(state, EID, later, opts(), {PID}, still_missing=False) == []
    assert EID in state.active
    actions = logic.handle_missing(state, EID, later, opts(), {PID}, still_missing=True)
    assert actions[0] == JournalClose(EID, state_journal_id(actions), later, True)
    assert only(actions, DismissPersistent) == [DismissPersistent(PID)]
    assert only(actions, CreatePersistent) == []
    assert EID not in state.active


def state_journal_id(actions):
    return actions[0].journal_id


# --------------------------------------------------------------------------
# #9 / #10 — przypomnienia i wygasanie wyciszeń
# --------------------------------------------------------------------------


def test_row9_reminder_after_interval(now):
    state = State()
    journal_id = _activate(state, now)
    assert logic.tick(state, now + timedelta(hours=23), opts()) == []
    actions = logic.tick(state, now + timedelta(hours=24), opts())
    push = only(actions, SendPush)
    assert len(push) == 1 and push[0].reminder and push[0].targets == ("admins",)
    assert push[0].message == "14% przez 10 min (próg 10%)."
    assert JournalMark(journal_id, reminders=1) in actions
    assert logic.tick(state, now + timedelta(hours=24, minutes=1), opts()) == []
    again = logic.tick(state, now + timedelta(hours=48), opts())
    assert len(only(again, SendPush)) == 1 and state.active[EID].reminders == 2


def test_row9_no_reminder_when_dismissed_silenced_no_targets_or_warning(now):
    state = State()
    journal_id = _activate(state, now)
    assert logic.dismiss(state, EID) == [JournalMark(journal_id, dismissed=True)]
    assert logic.tick(state, now + timedelta(hours=30), opts()) == []

    state = State()
    _activate(state, now)
    logic.mute(state, EID)
    assert logic.tick(state, now + timedelta(hours=30), opts()) == []

    state = State()
    _activate(state, now, defn(notify_targets="[]"))
    assert logic.tick(state, now + timedelta(hours=30), opts()) == []

    state = State()
    _activate(state, now, defn(severity="warning"))
    assert logic.tick(state, now + timedelta(days=5), opts()) == []


def test_row9_no_reminder_when_push_channel_off_or_push_never_sent(now):
    state = State()
    _activate(state, now)
    assert logic.tick(state, now + timedelta(hours=30), opts(**{OPT_PUSH_ENABLED: False})) == []

    state = State()
    _activate(state, now, o=opts(**{OPT_PUSH_ENABLED: False}))  # push nie poszedł
    assert not state.active[EID].push_sent
    assert logic.tick(state, now + timedelta(hours=30), opts()) == []  # po włączeniu bez nadrabiania


# --------------------------------------------------------------------------
# Kanały globalne
# --------------------------------------------------------------------------


def test_channels_off_send_nothing_but_journal(now):
    state = State()
    o = opts(**{OPT_PUSH_ENABLED: False, OPT_PERSISTENT_ENABLED: False})
    actions = logic.handle_state(state, EID, defn(), "on", now, o)
    assert [type(a) for a in actions] == [JournalOpen]
    rec = state.active[EID]
    assert rec.persistent_id is None and not rec.push_sent and not rec.silenced
    assert logic.active_summary(state, now)["total"] == 1


def test_push_off_still_creates_persistent(now):
    state = State()
    actions = logic.handle_state(state, EID, defn(), "on", now, opts(**{OPT_PUSH_ENABLED: False}))
    assert len(only(actions, CreatePersistent)) == 1 and only(actions, SendPush) == []


def test_persistent_off_skips_restore_on_catch_up(now):
    state = State()
    _activate(state, now)
    actions = logic.catch_up(
        state,
        {EID: (defn(), "on")},
        set(),
        now + timedelta(minutes=6),
        opts(**{OPT_PERSISTENT_ENABLED: False}),
    )
    assert only(actions, CreatePersistent) == []


# --------------------------------------------------------------------------
# Działania użytkownika
# --------------------------------------------------------------------------


def test_dismiss_hides_current_occurrence_only(now):
    state = State()
    journal_id = _activate(state, now)
    assert logic.dismiss(state, EID) == [JournalMark(journal_id, dismissed=True)]
    assert logic.dismiss(state, EID) == []  # drugi raz bez zmian
    summary = logic.active_summary(state, now)
    assert summary["total"] == 0 and summary["active"] == []
    assert [d["entity_id"] for d in summary["dismissed"]] == [EID]
    # Ustąpił → odrzucenie znika razem z wystąpieniem; kolejne zgłasza się normalnie.
    logic.handle_state(state, EID, defn(), "off", now + timedelta(hours=1), opts(), {PID})
    actions = logic.handle_state(state, EID, defn(), "on", now + timedelta(hours=2), opts())
    assert len(only(actions, SendPush)) == 1 and len(only(actions, CreatePersistent)) == 1
    assert logic.active_summary(state, now)["total"] == 1


def test_undismiss_restores_without_sending(now):
    state = State()
    _activate(state, now)
    logic.dismiss(state, EID)
    assert logic.undismiss(state, EID) == []
    assert logic.active_summary(state, now)["total"] == 1
    assert logic.undismiss(State(), EID) == [] and logic.dismiss(State(), EID) == []


def test_mute_active_then_unmute_sends_only_on_next_occurrence(now):
    state = State()
    journal_id = _activate(state, now)
    assert logic.mute(state, EID) == [JournalMark(journal_id, muted=True)]
    assert state.active[EID].silenced and logic.active_summary(state, now)["total"] == 1
    assert logic.unmute(state, EID) == []
    assert EID not in state.muted and state.active[EID].silenced  # trwające dalej ciche
    assert logic.tick(state, now + timedelta(hours=30), opts()) == []
    logic.handle_state(state, EID, defn(), "off", now + timedelta(hours=31), opts(), {PID})
    actions = logic.handle_state(state, EID, defn(), "on", now + timedelta(hours=32), opts())
    assert len(only(actions, SendPush)) == 1


def test_mute_inactive_applies_to_next_occurrences(now):
    state = State()
    assert logic.mute(state, EID) == []
    for hour in (1, 3):
        actions = logic.handle_state(state, EID, defn(), "on", now + timedelta(hours=hour), opts())
        assert [type(a) for a in actions] == [JournalOpen]
        logic.handle_state(state, EID, defn(), "off", now + timedelta(hours=hour + 1), opts())


def test_disable_enable(now):
    state = State()
    journal_id = _activate(state, now)
    assert logic.disable(state, EID) == [JournalMark(journal_id, disabled=True)]
    summary = logic.active_summary(state, now)
    assert summary["total"] == 0 and summary["disabled"] == [EID] and summary["dismissed"] == []
    assert logic.enable(state, EID) == []  # włączenie nic nie wysyła
    assert EID not in state.disabled and state.active[EID].silenced
    assert logic.active_summary(state, now)["total"] == 1
    # wyłączony nieaktywny: kolejne wystąpienie ciche
    state2 = State()
    logic.disable(state2, EID)
    actions = logic.handle_state(state2, EID, defn(), "on", now, opts())
    assert [type(a) for a in actions] == [JournalOpen]


def test_journal_false_skips_journal_but_still_notifies(now):
    assert defn().journal is True and defn(journal="false").journal is False
    state = State()
    actions = logic.handle_state(state, EID, defn(journal=False), "on", now, opts())
    assert only(actions, JournalOpen) == []
    assert len(only(actions, CreatePersistent)) == 1 and len(only(actions, SendPush)) == 1
    rec = state.active[EID]
    assert rec.journal is False and rec.journal_id == ""
    assert logic.active_summary(state, now)["total"] == 1
    assert State.from_dict(state.to_dict()).active[EID].journal is False
    off = logic.handle_state(state, EID, defn(journal=False), "off", now + timedelta(hours=1), opts(), {PID})
    assert only(off, JournalClose) == []
    assert len(only(off, CreatePersistent)) == 1  # „✅ …” nadal
    assert EID not in state.active


# --------------------------------------------------------------------------
# #14 / #15 — sprzątanie i zamknięcie przez użytkownika
# --------------------------------------------------------------------------


def test_row14_cleanup(now):
    state = State()
    state.resolved_persistents = {
        "alert_old": now - timedelta(days=8),
        "alert_fresh": now - timedelta(days=2),
        "alert_vanished": now - timedelta(days=9),
    }
    existing = {"alert_old", "alert_fresh"}
    actions = logic.cleanup(state, now, opts(), existing)
    assert actions == [DismissPersistent("alert_old")]
    assert set(state.resolved_persistents) == {"alert_fresh"}


def test_row14_cleanup_orphans(now):
    state = State()
    _activate(state, now)
    state.resolved_persistents["alert_resolved"] = now
    existing = {PID, "alert_resolved", "alert_binary_sensor.alert_x_1790262322", "hacs_update"}
    actions = logic.cleanup(state, now, opts(), existing, orphans=True)
    assert actions == [DismissPersistent("alert_binary_sensor.alert_x_1790262322")]

    # Przeterminowany ustąpiony + duchy: każdy id dokładnie raz.
    state.resolved_persistents["alert_resolved"] = now - timedelta(days=8)
    actions = logic.cleanup(state, now, opts(), existing, orphans=True)
    assert actions == [
        DismissPersistent("alert_resolved"),
        DismissPersistent("alert_binary_sensor.alert_x_1790262322"),
    ]


def test_each_occurrence_gets_its_own_persistent(now):
    state = State()
    _activate(state, now)
    logic.handle_state(state, EID, defn(), "off", now + timedelta(hours=1), opts(), {PID})
    second_on = now + timedelta(hours=2)
    actions = logic.handle_state(state, EID, defn(), "on", second_on, opts())
    second_pid = only(actions, CreatePersistent)[0].notification_id
    assert second_pid == f"{TAG}_{int(second_on.timestamp())}" and second_pid != PID
    assert PID in state.resolved_persistents  # poprzednie „✅” zostaje do sprzątania
    assert only(actions, SendPush)[0].data["tag"] == TAG  # push: ten sam tag


def test_row15_persistent_removed(now):
    state = State()
    _activate(state, now)
    state.resolved_persistents["alert_other"] = now
    assert logic.persistent_removed(state, PID) is True
    assert state.active[EID].persistent_id is None
    assert logic.persistent_removed(state, "alert_other") is True
    assert logic.persistent_removed(state, "alert_unknown") is False


# --------------------------------------------------------------------------
# Stan: serializacja i widok
# --------------------------------------------------------------------------


def test_state_round_trip(now):
    state = State()
    _activate(state, now)
    state.muted.add("binary_sensor.alert_b")
    state.disabled.add("binary_sensor.alert_c")
    state.active[EID].dismissed = True
    state.resolved_persistents["alert_d"] = now
    restored = State.from_dict(state.to_dict())
    assert restored.to_dict() == state.to_dict()
    rec = restored.active[EID]
    assert rec.on_ts == now and rec.targets == ("admins",) and rec.persistent_id == PID
    assert State.from_dict(None).to_dict() == State().to_dict()
    assert State.from_dict({"active": {"x": {"entity_id": "x"}}}).active == {}


def test_state_from_legacy_format(now):
    """Stan sprzed 2026-10-07: snoozes pomijane, disabled/snoozed_until -> silenced."""
    iso = now.isoformat()
    legacy = {
        "active": {
            EID: {"entity_id": EID, "on_ts": iso, "snoozed_until": iso},
            "binary_sensor.alert_d": {"entity_id": "binary_sensor.alert_d", "on_ts": iso, "disabled": True},
            "binary_sensor.alert_n": {"entity_id": "binary_sensor.alert_n", "on_ts": iso, "acknowledged": True},
        },
        "snoozes": {EID: iso},
        "disabled": ["binary_sensor.alert_d"],
    }
    state = State.from_dict(legacy)
    assert state.active[EID].silenced and state.active["binary_sensor.alert_d"].silenced
    assert not state.active["binary_sensor.alert_n"].silenced
    assert not state.active["binary_sensor.alert_n"].dismissed
    assert state.muted == set() and state.disabled == {"binary_sensor.alert_d"}
    assert "snoozes" not in state.to_dict()


def test_active_summary_orders_by_severity_then_time(now):
    state = State()
    for i, (eid, sev) in enumerate(
        [
            ("binary_sensor.alert_i", "info"),
            ("binary_sensor.alert_w", "warning"),
            ("binary_sensor.alert_e2", "error"),
            ("binary_sensor.alert_e1", "error"),
        ]
    ):
        d = AlertDef.from_attributes(eid, {"severity": sev, "friendly_name": eid, "message": "m"})
        logic.handle_state(state, eid, d, "on", now + timedelta(minutes=i), opts())
    summary = logic.active_summary(state, now)
    assert [a["entity_id"] for a in summary["active"]] == [
        "binary_sensor.alert_e2",
        "binary_sensor.alert_e1",
        "binary_sensor.alert_w",
        "binary_sensor.alert_i",
    ]
    assert summary["errors_count"] == 2 and summary["warnings"] == ["binary_sensor.alert_w"]
    assert summary["since"]["binary_sensor.alert_i"] == now.isoformat()


def test_resolved_text_uses_local_clock_of_off():
    off = datetime(2026, 10, 5, 14, 32, tzinfo=timezone(timedelta(hours=2)))
    title_fmt, body = logic.resolved_text("Treść.", off, timedelta(minutes=3))
    assert title_fmt.format(title="T") == "✅ T"
    assert body == "Treść. · ustąpiło 14:32, trwało 3 min"
