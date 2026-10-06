"""Dziennik alertów — czysty model (bez importów Home Assistant).

Jeden wpis = jedno wystąpienie alertu (od włączenia do ustąpienia) z treścią
zapamiętaną w chwili włączenia. Przechowywany w .storage/alerty/journal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable
from uuid import uuid4

from .const import (
    JOURNAL_RECENT_BODY_CHARS,
    JOURNAL_RECENT_COUNT,
    JOURNAL_TOP_COUNT,
    JOURNAL_TOP_WINDOW_DAYS,
)


def new_id() -> str:
    return uuid4().hex[:12]


def format_duration(delta: timedelta | None) -> str:
    """Czas trwania po polsku, bez spacji przed jednostką: "3 min", "2 h 15 min", "1 d 4 h"."""
    if delta is None:
        return ""
    total = int(delta.total_seconds())
    if total < 60:
        return "< 1 min"
    minutes, _ = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} d {hours} h" if hours else f"{days} d"
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{minutes} min"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _from_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@dataclass
class JournalEntry:
    """Jedno wystąpienie alertu."""

    id: str
    entity_id: str
    title: str
    severity: str
    body: str
    on: datetime
    off: datetime | None = None
    duration_s: int | None = None
    off_approx: bool = False
    notified: dict[str, Any] = field(
        default_factory=lambda: {
            "push": [],
            "persistent": False,
            "simulated": False,
            "errors": [],
        }
    )
    reminders: int = 0
    muted: bool = False  # wyciszony (do odwołania) — nic nie wysłano / dalej nie wysyła
    dismissed: bool = False  # odrzucony przez użytkownika (ukryty z listy aktywnych)
    disabled: bool = False  # wyłączony na stałe
    source: str = "change"

    @property
    def open(self) -> bool:
        return self.off is None

    @property
    def duration(self) -> timedelta | None:
        if self.duration_s is None:
            return None
        return timedelta(seconds=self.duration_s)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["on"] = _iso(self.on)
        data["off"] = _iso(self.off)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JournalEntry | None":
        on = _from_iso(data.get("on"))
        if not on or not data.get("entity_id"):
            return None
        notified = data.get("notified") or {}
        return cls(
            id=str(data.get("id") or new_id()),
            entity_id=str(data["entity_id"]),
            title=str(data.get("title") or data["entity_id"]),
            severity=str(data.get("severity") or "info"),
            body=str(data.get("body") or ""),
            on=on,
            off=_from_iso(data.get("off")),
            duration_s=data.get("duration_s"),
            off_approx=bool(data.get("off_approx", False)),
            notified={
                "push": list(notified.get("push") or []),
                "persistent": bool(notified.get("persistent", False)),
                "simulated": bool(notified.get("simulated", False)),
                "errors": list(notified.get("errors") or []),
            },
            reminders=int(data.get("reminders") or 0),
            # `snoozed` — stare wpisy (wyciszenie czasowe przed 2026-10-07).
            muted=bool(data.get("muted", data.get("snoozed", False))),
            dismissed=bool(data.get("dismissed", False)),
            disabled=bool(data.get("disabled", False)),
            source=str(data.get("source") or "change"),
        )


class Journal:
    """Lista wpisów w kolejności chronologicznej (najstarszy pierwszy)."""

    def __init__(self, entries: Iterable[JournalEntry] | None = None) -> None:
        self.entries: list[JournalEntry] = list(entries or [])
        self.dirty = False

    # --- zapis / odczyt -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Journal":
        entries = []
        for raw in (data or {}).get("entries", []) or []:
            if isinstance(raw, dict):
                entry = JournalEntry.from_dict(raw)
                if entry:
                    entries.append(entry)
        entries.sort(key=lambda e: e.on)
        return cls(entries)

    # --- mutacje ----------------------------------------------------------

    def get(self, journal_id: str | None) -> JournalEntry | None:
        if not journal_id:
            return None
        for entry in reversed(self.entries):
            if entry.id == journal_id:
                return entry
        return None

    def latest_open(self, entity_id: str) -> JournalEntry | None:
        for entry in reversed(self.entries):
            if entry.entity_id == entity_id and entry.open:
                return entry
        return None

    def open(self, entry: JournalEntry) -> JournalEntry:
        self.entries.append(entry)
        self.dirty = True
        return entry

    def close(
        self,
        journal_id: str | None,
        entity_id: str,
        off: datetime,
        off_approx: bool = False,
    ) -> JournalEntry | None:
        """Domyka wpis po id, a bez id — ostatni otwarty wpis tej encji."""
        entry = self.get(journal_id) if journal_id else self.latest_open(entity_id)
        if entry is None or not entry.open:
            return None
        if off < entry.on:
            off = entry.on
        entry.off = off
        entry.duration_s = int((off - entry.on).total_seconds())
        entry.off_approx = off_approx
        self.dirty = True
        return entry

    def mark(
        self,
        journal_id: str | None,
        *,
        muted: bool | None = None,
        dismissed: bool | None = None,
        disabled: bool | None = None,
        reminders: int | None = None,
    ) -> None:
        entry = self.get(journal_id)
        if entry is None:
            return
        if muted is not None:
            entry.muted = muted
        if dismissed is not None:
            entry.dismissed = dismissed
        if disabled is not None:
            entry.disabled = disabled
        if reminders is not None:
            entry.reminders = reminders
        self.dirty = True

    def note_push(self, journal_id: str | None, targets: Iterable[str], simulated: bool) -> None:
        entry = self.get(journal_id)
        if entry is None:
            return
        for target in targets:
            if target not in entry.notified["push"]:
                entry.notified["push"].append(target)
        entry.notified["simulated"] = entry.notified["simulated"] or simulated
        self.dirty = True

    def note_persistent(self, journal_id: str | None, simulated: bool) -> None:
        entry = self.get(journal_id)
        if entry is None:
            return
        entry.notified["persistent"] = True
        entry.notified["simulated"] = entry.notified["simulated"] or simulated
        self.dirty = True

    def note_error(self, journal_id: str | None, text: str) -> None:
        entry = self.get(journal_id)
        if entry is None:
            return
        entry.notified["errors"].append(text[:200])
        self.dirty = True

    def prune(self, now: datetime, retention_days: int) -> int:
        """Usuwa domknięte wpisy starsze niż retencja; otwarte zostają zawsze."""
        cutoff = now - timedelta(days=retention_days)
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.open or e.on >= cutoff]
        removed = before - len(self.entries)
        if removed:
            self.dirty = True
        return removed

    # --- prezentacja ----------------------------------------------------

    def recent(
        self,
        count: int = JOURNAL_RECENT_COUNT,
        max_body: int = JOURNAL_RECENT_BODY_CHARS,
    ) -> list[dict[str, Any]]:
        """Ostatnie wpisy (najnowszy pierwszy) w zwartej postaci dla atrybutu encji."""
        result = []
        for entry in reversed(self.entries[-count:]):
            body = entry.body
            if len(body) > max_body:
                body = body[: max_body - 1].rstrip() + "…"
            result.append(
                {
                    "e": entry.entity_id,
                    "t": entry.title,
                    "s": entry.severity,
                    "b": body,
                    "on": _iso(entry.on),
                    "off": _iso(entry.off),
                    "d": format_duration(entry.duration) if entry.off else None,
                }
            )
        return result

    def top(
        self,
        now: datetime,
        window_days: int = JOURNAL_TOP_WINDOW_DAYS,
        count: int = JOURNAL_TOP_COUNT,
    ) -> list[dict[str, Any]]:
        """Najczęstsze alerty w oknie: liczba wystąpień i łączny czas trwania."""
        cutoff = now - timedelta(days=window_days)
        stats: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            if entry.on < cutoff:
                continue
            item = stats.setdefault(
                entry.entity_id,
                {"entity_id": entry.entity_id, "title": entry.title, "count": 0, "total_s": 0},
            )
            item["count"] += 1
            item["title"] = entry.title
            end = entry.off or now
            item["total_s"] += max(0, int((end - entry.on).total_seconds()))
        ranked = sorted(stats.values(), key=lambda i: (-i["count"], -i["total_s"], i["title"]))
        for item in ranked:
            item["total"] = format_duration(timedelta(seconds=item["total_s"]))
        return ranked[:count]

    def counts(self, now: datetime) -> dict[str, int]:
        """Liczba wystąpień dziś (od północy lokalnej `now`) i w ostatnich 7 dniach."""
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)
        return {
            "today": sum(1 for e in self.entries if e.on >= day_start),
            "week": sum(1 for e in self.entries if e.on >= week_start),
        }

    def export(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        entity_id: str | None = None,
    ) -> list[dict[str, Any]]:
        result = []
        for entry in self.entries:
            # Data bez strefy (np. z formularza usługi) = strefa wpisu (lokalna HA).
            if since and since.tzinfo is None:
                since = since.replace(tzinfo=entry.on.tzinfo)
            if until and until.tzinfo is None:
                until = until.replace(tzinfo=entry.on.tzinfo)
            if since and entry.on < since:
                continue
            if until and entry.on > until:
                continue
            if entity_id and entry.entity_id != entity_id:
                continue
            data = entry.to_dict()
            data["duration"] = format_duration(entry.duration) if entry.off else None
            result.append(data)
        return result
