"""Opcje integracji: defaulty, parsowanie wartości z formularza, round-trip."""

from __future__ import annotations

from datetime import time, timedelta

import pytest

from custom_components.alerty.const import (
    MODE_NORMAL,
    MODE_OBSERVE,
    ON_RESOLVE_DISMISS,
    ON_RESOLVE_UPDATE,
    OPT_CHANNEL,
    OPT_CLEANUP_AFTER_DAYS,
    OPT_CLEANUP_TIME,
    OPT_MODE,
    OPT_ON_RESOLVE,
    OPT_PERSISTENT,
    OPT_PUSH_CLICK_PATH,
    OPT_PUSH_TARGETS,
    OPT_REMINDER,
    OPT_RETENTION_DAYS,
    OPT_STARTUP_GRACE,
)
from custom_components.alerty.options import Options, parse_duration, parse_time


def test_defaults():
    o = Options.from_mapping({})
    assert o.mode == MODE_OBSERVE and not o.sending
    assert o.startup_grace == timedelta(minutes=5)
    assert o.push_targets == {"error": ("admins",), "warning": (), "info": ()}
    assert o.persistent == {"error": True, "warning": True, "info": False}
    assert o.on_resolve == ON_RESOLVE_UPDATE
    assert o.reminder == {"error": timedelta(hours=24), "warning": None, "info": None}
    assert o.retention_days == 30 and o.cleanup_after_days == 7
    assert o.cleanup_time == time(4, 10)
    assert o.push_click_path == "/lovelace/system"
    assert o.channels["error"] == "Alerty - błędy"
    assert Options.from_mapping(None) == o


def test_from_mapping_parses_form_values():
    o = Options.from_mapping(
        {
            OPT_MODE: MODE_NORMAL,
            OPT_STARTUP_GRACE: {"hours": 0, "minutes": 2, "seconds": 30},
            OPT_PUSH_TARGETS.format(severity="warning"): ["notify.mobile", "admins"],
            OPT_PUSH_TARGETS.format(severity="error"): [],
            OPT_PERSISTENT.format(severity="info"): "true",
            OPT_ON_RESOLVE: ON_RESOLVE_DISMISS,
            OPT_REMINDER.format(severity="error"): 0,
            OPT_REMINDER.format(severity="warning"): "12:00:00",
            OPT_RETENTION_DAYS: "45",
            OPT_CLEANUP_AFTER_DAYS: 0,
            OPT_CLEANUP_TIME: "03:00",
            OPT_PUSH_CLICK_PATH: " /lovelace/alertowanie ",
            OPT_CHANNEL.format(severity="info"): "Info kanał",
            OPT_CHANNEL.format(severity="error"): "   ",
        }
    )
    assert o.sending
    assert o.startup_grace == timedelta(minutes=2, seconds=30)
    assert o.push_targets["warning"] == ("mobile", "admins")
    assert o.push_targets["error"] == ()
    assert o.persistent["info"] is True
    assert o.on_resolve == ON_RESOLVE_DISMISS
    assert o.reminder == {"error": None, "warning": timedelta(hours=12), "info": None}
    assert o.retention_days == 45 and o.cleanup_after_days == 0
    assert o.cleanup_time == time(3, 0)
    assert o.push_click_path == "/lovelace/alertowanie"
    assert o.channels["info"] == "Info kanał" and o.channels["error"] == "Alerty - błędy"


def test_invalid_values_fall_back():
    o = Options.from_mapping(
        {
            OPT_MODE: "chaos",
            OPT_ON_RESOLVE: "explode",
            OPT_STARTUP_GRACE: "abc",
            OPT_RETENTION_DAYS: -5,
            OPT_CLEANUP_TIME: "25:99",
            OPT_PUSH_CLICK_PATH: "",
            OPT_PUSH_TARGETS.format(severity="error"): 7,
        }
    )
    assert o.mode == MODE_OBSERVE and o.on_resolve == ON_RESOLVE_UPDATE
    assert o.startup_grace == timedelta(minutes=5)
    assert o.retention_days == 30 and o.cleanup_time == time(4, 10)
    assert o.push_click_path == "/lovelace/system"
    assert o.push_targets["error"] == ("admins",)


def test_to_mapping_round_trip():
    original = Options.from_mapping(
        {
            OPT_MODE: MODE_NORMAL,
            OPT_REMINDER.format(severity="warning"): 3600,
            OPT_PUSH_TARGETS.format(severity="info"): ["mobile"],
        }
    )
    assert Options.from_mapping(original.to_mapping()) == original
    mapping = original.to_mapping()
    assert mapping[OPT_STARTUP_GRACE] == 300
    assert mapping[OPT_REMINDER.format(severity="info")] == 0
    assert mapping[OPT_CLEANUP_TIME] == "04:10:00"


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        (0, None),
        (90, timedelta(seconds=90)),
        ({"days": 1, "hours": 2}, timedelta(days=1, hours=2)),
        ({"minutes": "x"}, None),
        ("01:30:00", timedelta(hours=1, minutes=30)),
        ("00:00:00", None),
        ("45", timedelta(seconds=45)),
        ("a:b", None),
        (timedelta(minutes=3), timedelta(minutes=3)),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


def test_parse_time():
    assert parse_time("05:30", "04:10:00") == time(5, 30)
    assert parse_time(time(1, 2), "04:10:00") == time(1, 2)
    assert parse_time("nope", "04:10:00") == time(4, 10)
