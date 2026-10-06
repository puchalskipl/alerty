"""Dziennik alertów: model, przycinanie, prezentacja, limit rozmiaru atrybutu."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from custom_components.alerty.journal import Journal, JournalEntry, format_duration


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "< 1 min"),
        (59, "< 1 min"),
        (180, "3 min"),
        (2 * 3600, "2 h"),
        (2 * 3600 + 15 * 60, "2 h 15 min"),
        (28 * 3600, "1 d 4 h"),
        (3 * 86400, "3 d"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(timedelta(seconds=seconds)) == expected


def test_format_duration_none():
    assert format_duration(None) == ""


def entry(now, eid="binary_sensor.alert_a", sev="error", offset=0, **kw) -> JournalEntry:
    return JournalEntry(
        id=kw.pop("id", f"j{offset}"),
        entity_id=eid,
        title=kw.pop("title", "Tytuł " + eid),
        severity=sev,
        body=kw.pop("body", "treść"),
        on=now + timedelta(minutes=offset),
        **kw,
    )


def test_open_close_by_id_and_latest_open(now):
    journal = Journal()
    first = journal.open(entry(now, offset=0, id="first"))
    second = journal.open(entry(now, offset=5, id="second"))
    assert journal.latest_open("binary_sensor.alert_a") is second

    closed = journal.close("first", "binary_sensor.alert_a", now + timedelta(minutes=3))
    assert closed is first and first.duration_s == 180 and not first.off_approx
    assert journal.close("first", "binary_sensor.alert_a", now + timedelta(hours=1)) is None

    closed = journal.close(None, "binary_sensor.alert_a", now + timedelta(minutes=9), off_approx=True)
    assert closed is second and second.off_approx and second.duration_s == 240
    assert journal.latest_open("binary_sensor.alert_a") is None
    assert journal.close(None, "binary_sensor.alert_a", now) is None


def test_close_clamps_off_before_on(now):
    journal = Journal([entry(now, offset=10)])
    closed = journal.close(None, "binary_sensor.alert_a", now)
    assert closed is not None and closed.duration_s == 0 and closed.off == closed.on


def test_marks_and_notes(now):
    journal = Journal([entry(now, id="j")])
    journal.mark("j", muted=True, dismissed=True, disabled=True, reminders=2)
    journal.note_push("j", ["admins", "mobile"], simulated=False)
    journal.note_push("j", ["admins"], simulated=True)
    journal.note_persistent("j", simulated=False)
    journal.note_error("j", "x" * 300)
    journal.mark("missing", muted=True)
    e = journal.get("j")
    assert e.muted and e.dismissed and e.disabled and e.reminders == 2
    assert e.notified == {
        "push": ["admins", "mobile"],
        "persistent": True,
        "simulated": True,
        "errors": ["x" * 200],
    }
    assert journal.dirty


def test_prune_keeps_open_and_recent(now):
    old_closed = entry(now, offset=-60 * 24 * 40, id="old")
    old_closed.off = old_closed.on + timedelta(hours=1)
    old_open = entry(now, offset=-60 * 24 * 40, id="old-open")
    fresh = entry(now, offset=-60 * 24 * 10, id="fresh")
    journal = Journal([old_closed, old_open, fresh])
    assert journal.prune(now, retention_days=30) == 1
    assert [e.id for e in journal.entries] == ["old-open", "fresh"]
    assert journal.prune(now, retention_days=30) == 0


def test_recent_newest_first_and_truncated(now):
    journal = Journal()
    for i in range(30):
        journal.open(entry(now, offset=i, id=f"j{i}", body="x" * 200))
    journal.close("j29", "binary_sensor.alert_a", now + timedelta(minutes=29, seconds=90))
    recent = journal.recent(count=25, max_body=120)
    assert len(recent) == 25
    assert recent[0]["on"] == (now + timedelta(minutes=29)).isoformat()
    assert recent[0]["d"] == "1 min" and recent[0]["off"] is not None
    assert recent[1]["d"] is None and recent[1]["off"] is None
    assert len(recent[0]["b"]) == 120 and recent[0]["b"].endswith("…")
    assert set(recent[0]) == {"e", "t", "s", "b", "on", "off", "d"}


def test_recent_fits_attribute_budget(now):
    journal = Journal()
    for i in range(25):
        journal.open(
            entry(
                now,
                eid="binary_sensor.alert_rozdzielnica_monitorowanie_akumulatora_nie_dziala",
                offset=i,
                id=f"j{i}",
                title="Rozdzielnica - monitorowanie akumulatora nie działa (bardzo długi tytuł)",
                body="ż" * 400,
            )
        )
    payload = json.dumps(journal.recent(), ensure_ascii=False)
    assert len(payload.encode("utf-8")) < 12 * 1024


def test_top_counts_and_totals(now):
    journal = Journal()
    for i in range(3):
        e = journal.open(entry(now, offset=-60 * i, id=f"a{i}"))
        journal.close(e.id, e.entity_id, e.on + timedelta(minutes=10))
    b = journal.open(entry(now, eid="binary_sensor.alert_b", sev="info", offset=-30, id="b"))
    journal.open(entry(now, eid="binary_sensor.alert_old", offset=-60 * 24 * 40, id="old"))
    top = journal.top(now, window_days=30, count=10)
    assert [t["entity_id"] for t in top] == ["binary_sensor.alert_a", "binary_sensor.alert_b"]
    assert top[0] == {
        "entity_id": "binary_sensor.alert_a",
        "title": "Tytuł binary_sensor.alert_a",
        "count": 3,
        "total_s": 1800,
        "total": "30 min",
    }
    assert top[1]["count"] == 1 and top[1]["total_s"] == 1800  # otwarty liczony do „teraz”
    assert b.open


def test_counts_today_and_week(now):
    journal = Journal(
        [
            entry(now, offset=-60, id="today"),
            entry(now, offset=-60 * 24 * 3, id="week"),
            entry(now, offset=-60 * 24 * 10, id="older"),
        ]
    )
    assert journal.counts(now) == {"today": 1, "week": 2}


def test_round_trip_sorts_and_skips_garbage(now):
    journal = Journal([entry(now, offset=5, id="later"), entry(now, offset=0, id="earlier")])
    journal.close("later", "binary_sensor.alert_a", now + timedelta(minutes=7))
    data = journal.to_dict()
    data["entries"].append({"id": "garbage"})
    data["entries"].append("not a dict")
    restored = Journal.from_dict(data)
    assert [e.id for e in restored.entries] == ["earlier", "later"]
    assert restored.get("later").duration_s == 120
    assert restored.to_dict()["entries"] == sorted(
        journal.to_dict()["entries"], key=lambda e: e["on"]
    )
    assert Journal.from_dict(None).entries == []


def test_export_filters(now):
    journal = Journal(
        [
            entry(now, offset=-120, id="a"),
            entry(now, eid="binary_sensor.alert_b", offset=-60, id="b"),
            entry(now, offset=0, id="c"),
        ]
    )
    assert [e["id"] for e in journal.export(since=now - timedelta(minutes=90))] == ["b", "c"]
    assert [e["id"] for e in journal.export(until=now - timedelta(minutes=90))] == ["a"]
    assert [e["id"] for e in journal.export(entity_id="binary_sensor.alert_b")] == ["b"]
    assert journal.export()[0]["duration"] is None


def test_legacy_snoozed_entry_reads_as_muted(now):
    data = entry(now, id="old").to_dict()
    data.pop("muted"); data.pop("dismissed")
    data["snoozed"] = True
    data["acknowledged"] = True
    restored = JournalEntry.from_dict(data)
    assert restored.muted is True and restored.dismissed is False


def test_export_accepts_naive_dates(now):
    journal = Journal([entry(now, id="a", offset=-120), entry(now, id="b", offset=-10)])
    naive_since = (now - timedelta(minutes=60)).replace(tzinfo=None)
    assert [e["id"] for e in journal.export(since=naive_since)] == ["b"]
    naive_until = (now - timedelta(minutes=60)).replace(tzinfo=None)
    assert [e["id"] for e in journal.export(until=naive_until)] == ["a"]
