"""Logika decyzyjna integracji alerty — czysta, bez importów Home Assistant.

Funkcje tego modułu dostają stan integracji (`State`) i informacje o alercie,
modyfikują stan i zwracają listę akcji zewnętrznych (powiadomienia, dziennik),
które wykonuje `engine.py`. Dzięki temu każdy wiersz tabeli decyzyjnej
jest testowalny lokalnie, bez HA.

Kluczowe uproszczenie: decyzja zależy tylko od pary (efektywny nowy stan encji,
czy istnieje aktywny rekord) — ten sam kod obsługuje zmianę stanu, catch-up po
starcie HA i powrót encji z `unavailable`.

Działania użytkownika (od 2026-10-07):
- **odrzuć** — tylko bieżące wystąpienie: znika z listy aktywnych, bez przypomnień;
  kolejne wystąpienie zgłasza się normalnie;
- **wycisz** (do odwołania) — to i kolejne wystąpienia nic nie wysyłają; alert jest
  widoczny i liczony; po odwołaniu wysyła dopiero przy następnym wystąpieniu;
- **wyłącz na stałe** — jak wyciszenie, ale alert znika z listy aktywnych;
  po włączeniu też nic nie wysyła do następnego wystąpienia.
Globalnie: kanały push i „Powiadomienia w HA” (dzwonek) można wyłączyć w opcjach;
po włączeniu nic nie jest nadrabiane.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import AbstractSet, Any, Mapping

from .const import (
    ALERT_PREFIX,
    ATTR_FRIENDLY_NAME,
    ATTR_ICON,
    ATTR_JOURNAL,
    ATTR_MESSAGE,
    ATTR_NOTIFY_CHANNEL,
    ATTR_NOTIFY_CLICK_PATH,
    ATTR_NOTIFY_PERSISTENT,
    ATTR_NOTIFY_TARGETS,
    ATTR_SEVERITY,
    ATTR_TITLE,
    MISSING_RECHECK_S,
    ON_RESOLVE_UPDATE,
    PERSISTENT_PREFIX,
    PUSH_COLORS,
    PUSH_GROUP,
    PUSH_IMPORTANCE,
    SEVERITIES,
    TEXT_RESOLVED_BODY,
    TEXT_RESOLVED_TITLE,
)
from .journal import JournalEntry, format_duration, new_id
from .options import Options, normalize_target

# --------------------------------------------------------------------------
# Definicja alertu (czytana z atrybutów encji przy każdym zdarzeniu)
# --------------------------------------------------------------------------


def is_alert_entity(entity_id: str) -> bool:
    return entity_id.startswith(ALERT_PREFIX)


def effective_state(state: str | None) -> str | None:
    """'on' / 'off' albo None dla unavailable/unknown/brak encji."""
    if state in ("on", "off"):
        return state
    return None


def parse_targets(value: Any) -> tuple[str, ...] | None:
    """Cele push z atrybutu `notify_targets`.

    Przyjmuje listę, string z listą Pythona ("['admins']" — tak HA zapisuje
    szablon `{{ ["admins"] }}`), string CSV albo None (= użyj domyślnych).
    Pusta lista oznacza świadomy brak push.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            parsed = [part for part in text.split(",")]
        if isinstance(parsed, str):
            parsed = [parsed]
        if not isinstance(parsed, (list, tuple, set)):
            return ()
        value = parsed
    if isinstance(value, (list, tuple, set)):
        return tuple(normalize_target(str(v)) for v in value if str(v).strip())
    return ()


def parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "on", "yes", "1"):
            return True
        if text in ("false", "off", "no", "0"):
            return False
    return None


@dataclass(frozen=True)
class AlertDef:
    """To, co integracja wie o alercie z jego atrybutów."""

    entity_id: str
    title: str
    severity: str
    body: str
    icon: str | None = None
    push_targets: tuple[str, ...] | None = None
    persistent: bool | None = None
    channel: str | None = None
    click_path: str | None = None
    journal: bool = True  # `journal: false` = tylko widok bieżący, bez wpisu w dzienniku

    @classmethod
    def from_attributes(
        cls, entity_id: str, attributes: Mapping[str, Any] | None
    ) -> "AlertDef | None":
        """None, gdy encja nie jest alertem (brak poprawnego `severity`)."""
        attributes = attributes or {}
        severity = attributes.get(ATTR_SEVERITY)
        if not isinstance(severity, str) or severity.lower() not in SEVERITIES:
            return None
        title = attributes.get(ATTR_TITLE) or attributes.get(ATTR_FRIENDLY_NAME)
        if not isinstance(title, str) or not title.strip():
            title = entity_id.removeprefix("binary_sensor.")
        message = attributes.get(ATTR_MESSAGE)
        body = message.strip() if isinstance(message, str) else ""
        icon = attributes.get(ATTR_ICON)
        channel = attributes.get(ATTR_NOTIFY_CHANNEL)
        click = attributes.get(ATTR_NOTIFY_CLICK_PATH)
        journal = parse_bool(attributes.get(ATTR_JOURNAL))
        return cls(
            entity_id=entity_id,
            title=title.strip(),
            severity=severity.lower(),
            body=body or title.strip(),
            icon=icon if isinstance(icon, str) and icon else None,
            push_targets=parse_targets(attributes.get(ATTR_NOTIFY_TARGETS)),
            persistent=parse_bool(attributes.get(ATTR_NOTIFY_PERSISTENT)),
            channel=channel.strip() if isinstance(channel, str) and channel.strip() else None,
            click_path=click.strip() if isinstance(click, str) and click.strip() else None,
            journal=True if journal is None else journal,
        )


# --------------------------------------------------------------------------
# Stan integracji (zapisywany w .storage/alerty/state)
# --------------------------------------------------------------------------


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
class ActiveRecord:
    """Aktywne wystąpienie alertu ze snapshotem treści z chwili włączenia."""

    entity_id: str
    title: str
    severity: str
    body_on: str
    on_ts: datetime
    journal_id: str
    source: str = "change"
    body_now: str = ""
    icon: str | None = None
    channel: str | None = None
    click_path: str | None = None
    targets: tuple[str, ...] = ()
    want_persistent: bool = False
    persistent_id: str | None = None
    push_sent: bool = False
    last_push_ts: datetime | None = None
    reminders: int = 0
    # To wystąpienie nic nie wysyła (wyciszone/wyłączone w chwili włączenia albo w trakcie).
    # Odwołanie wyciszenia/wyłączenia tego nie cofa — wysyła dopiero kolejne wystąpienie.
    silenced: bool = False
    dismissed: bool = False  # odrzucone: ukryte z listy aktywnych, bez przypomnień
    journal: bool = True

    @property
    def notified(self) -> bool:
        return self.push_sent or self.persistent_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "title": self.title,
            "severity": self.severity,
            "body_on": self.body_on,
            "body_now": self.body_now,
            "on_ts": _iso(self.on_ts),
            "journal_id": self.journal_id,
            "source": self.source,
            "icon": self.icon,
            "channel": self.channel,
            "click_path": self.click_path,
            "targets": list(self.targets),
            "want_persistent": self.want_persistent,
            "persistent_id": self.persistent_id,
            "push_sent": self.push_sent,
            "last_push_ts": _iso(self.last_push_ts),
            "reminders": self.reminders,
            "silenced": self.silenced,
            "dismissed": self.dismissed,
            "journal": self.journal,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActiveRecord | None":
        on_ts = _from_iso(data.get("on_ts"))
        entity_id = data.get("entity_id")
        if not on_ts or not entity_id:
            return None
        # Stary format (przed 2026-10-07): disabled / snoozed_until wyciszały wystąpienie.
        silenced = bool(
            data.get("silenced", False) or data.get("disabled", False) or data.get("snoozed_until")
        )
        return cls(
            entity_id=str(entity_id),
            title=str(data.get("title") or entity_id),
            severity=str(data.get("severity") or "info"),
            body_on=str(data.get("body_on") or ""),
            on_ts=on_ts,
            journal_id=str(data.get("journal_id") or ""),
            source=str(data.get("source") or "change"),
            body_now=str(data.get("body_now") or ""),
            icon=data.get("icon") or None,
            channel=data.get("channel") or None,
            click_path=data.get("click_path") or None,
            targets=tuple(str(t) for t in (data.get("targets") or [])),
            want_persistent=bool(data.get("want_persistent", False)),
            persistent_id=data.get("persistent_id") or None,
            push_sent=bool(data.get("push_sent", False)),
            last_push_ts=_from_iso(data.get("last_push_ts")),
            reminders=int(data.get("reminders") or 0),
            silenced=silenced,
            dismissed=bool(data.get("dismissed", False)),
            journal=bool(data.get("journal", True)),
        )


@dataclass
class State:
    """Cały trwały stan integracji poza dziennikiem."""

    active: dict[str, ActiveRecord] = field(default_factory=dict)
    resolved_persistents: dict[str, datetime] = field(default_factory=dict)
    muted: set[str] = field(default_factory=set)  # wyciszone do odwołania
    disabled: set[str] = field(default_factory=set)  # wyłączone na stałe

    def is_silenced(self, entity_id: str) -> bool:
        return entity_id in self.muted or entity_id in self.disabled

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": {eid: rec.to_dict() for eid, rec in self.active.items()},
            "resolved_persistents": {
                pid: _iso(ts) for pid, ts in self.resolved_persistents.items()
            },
            "muted": sorted(self.muted),
            "disabled": sorted(self.disabled),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "State":
        data = data or {}
        state = cls()
        for eid, raw in (data.get("active") or {}).items():
            if isinstance(raw, Mapping):
                rec = ActiveRecord.from_dict(raw)
                if rec:
                    state.active[eid] = rec
        for pid, raw in (data.get("resolved_persistents") or {}).items():
            ts = _from_iso(raw)
            if ts:
                state.resolved_persistents[pid] = ts
        # `snoozes` (wyciszenia czasowe sprzed 2026-10-07) celowo pomijane.
        state.muted = {str(e) for e in (data.get("muted") or [])}
        state.disabled = {str(e) for e in (data.get("disabled") or [])}
        return state


# --------------------------------------------------------------------------
# Akcje zwracane do silnika
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class JournalOpen:
    entry: JournalEntry


@dataclass(frozen=True)
class JournalClose:
    entity_id: str
    journal_id: str | None
    off: datetime
    off_approx: bool = False


@dataclass(frozen=True)
class JournalMark:
    journal_id: str
    muted: bool | None = None
    dismissed: bool | None = None
    disabled: bool | None = None
    reminders: int | None = None


@dataclass(frozen=True)
class CreatePersistent:
    notification_id: str
    title: str
    message: str
    journal_id: str | None = None
    restore: bool = False


@dataclass(frozen=True)
class DismissPersistent:
    notification_id: str


@dataclass(frozen=True)
class SendPush:
    targets: tuple[str, ...]
    title: str
    message: str
    data: dict[str, Any]
    journal_id: str | None = None
    reminder: bool = False


@dataclass(frozen=True)
class ScheduleRecheck:
    entity_id: str
    delay: timedelta


Action = (
    JournalOpen
    | JournalClose
    | JournalMark
    | CreatePersistent
    | DismissPersistent
    | SendPush
    | ScheduleRecheck
)


# --------------------------------------------------------------------------
# Pomocnicze
# --------------------------------------------------------------------------


def persistent_id_for(entity_id: str, on_ts: datetime) -> str:
    """Osobny persistent na każde wystąpienie (historia w dzwonku)."""
    return f"{PERSISTENT_PREFIX}{entity_id}_{int(on_ts.timestamp())}"


def push_tag_for(entity_id: str) -> str:
    return f"{PERSISTENT_PREFIX}{entity_id}"


def push_data(rec: ActiveRecord, opts: Options) -> dict[str, Any]:
    """Pole `data` powiadomienia push dla aplikacji Companion (Android).

    Bez `notification_icon` — telefon pokazuje domyślną ikonę aplikacji HA.
    """
    severity = rec.severity if rec.severity in SEVERITIES else "info"
    data: dict[str, Any] = {
        "tag": push_tag_for(rec.entity_id),
        "channel": rec.channel or opts.channels.get(severity, ""),
        "importance": PUSH_IMPORTANCE[severity],
        "color": PUSH_COLORS[severity],
        "group": PUSH_GROUP,
        "clickAction": rec.click_path or opts.push_click_path,
    }
    if severity == "error":
        data["priority"] = "high"
        data["ttl"] = 0
    return data


def resolved_text(body_on: str, off: datetime, duration: timedelta) -> tuple[str, str]:
    """(suffix tytułu, treść) persistenta po ustąpieniu alertu."""
    return (
        TEXT_RESOLVED_TITLE,
        TEXT_RESOLVED_BODY.format(
            body=body_on, time=off.strftime("%H:%M"), duration=format_duration(duration)
        ),
    )


def _wants_persistent(defn: AlertDef, opts: Options) -> bool:
    if defn.persistent is not None:
        return defn.persistent
    return bool(opts.persistent.get(defn.severity, False))


def _targets_for(defn: AlertDef, opts: Options) -> tuple[str, ...]:
    if defn.push_targets is not None:
        return defn.push_targets
    return tuple(opts.push_targets.get(defn.severity, ()))


def _notify_actions(rec: ActiveRecord, opts: Options, now: datetime) -> list[Action]:
    """Persistent + push dla nowego wystąpienia — tylko we włączonych kanałach."""
    actions: list[Action] = []
    if opts.persistent_enabled and rec.want_persistent and rec.persistent_id is None:
        rec.persistent_id = persistent_id_for(rec.entity_id, rec.on_ts)
        actions.append(
            CreatePersistent(rec.persistent_id, rec.title, rec.body_on, rec.journal_id)
        )
    if opts.push_enabled and rec.targets and not rec.push_sent:
        rec.push_sent = True
        rec.last_push_ts = now
        actions.append(
            SendPush(rec.targets, rec.title, rec.body_on, push_data(rec, opts), rec.journal_id)
        )
    return actions


def _resolve_actions(
    state: State,
    rec: ActiveRecord,
    now: datetime,
    opts: Options,
    existing_persistents: AbstractSet[str],
    *,
    off_approx: bool = False,
    dismiss_only: bool = False,
) -> list[Action]:
    """Alert ustąpił: domknij dziennik, zaktualizuj albo usuń persistent."""
    actions: list[Action] = []
    if rec.journal:
        actions.append(JournalClose(rec.entity_id, rec.journal_id, now, off_approx))
    pid = rec.persistent_id
    if pid and pid in existing_persistents:
        if opts.on_resolve == ON_RESOLVE_UPDATE and not dismiss_only:
            title_fmt, body = resolved_text(rec.body_on, now, now - rec.on_ts)
            actions.append(
                CreatePersistent(pid, title_fmt.format(title=rec.title), body, rec.journal_id)
            )
            state.resolved_persistents[pid] = now
        else:
            actions.append(DismissPersistent(pid))
    state.active.pop(rec.entity_id, None)
    return actions


def _mark(rec: ActiveRecord | None, **flags: Any) -> list[Action]:
    if rec is None or not rec.journal_id:
        return []
    return [JournalMark(rec.journal_id, **flags)]


# --------------------------------------------------------------------------
# Główne przejścia
# --------------------------------------------------------------------------


def handle_state(
    state: State,
    entity_id: str,
    defn: AlertDef | None,
    effective: str | None,
    now: datetime,
    opts: Options,
    existing_persistents: AbstractSet[str] = frozenset(),
    *,
    source: str = "change",
) -> list[Action]:
    """Nowy (efektywny) stan encji alertu."""
    rec = state.active.get(entity_id)

    if effective is None:  # unavailable/unknown: rekord zostaje
        return []

    if effective == "on":
        if rec is not None:  # trwa; odśwież bieżącą treść
            if defn is not None and defn.body != rec.body_now:
                rec.body_now = defn.body
            return []
        if defn is None:  # encja bez severity — nie jest alertem
            return []
        muted = entity_id in state.muted
        disabled = entity_id in state.disabled
        rec = ActiveRecord(
            entity_id=entity_id,
            title=defn.title,
            severity=defn.severity,
            body_on=defn.body,
            body_now=defn.body,
            on_ts=now,
            journal_id=new_id() if defn.journal else "",
            source=source,
            icon=defn.icon,
            channel=defn.channel,
            click_path=defn.click_path,
            targets=_targets_for(defn, opts),
            want_persistent=_wants_persistent(defn, opts),
            silenced=muted or disabled,
            journal=defn.journal,
        )
        state.active[entity_id] = rec
        actions: list[Action] = []
        if defn.journal:
            actions.append(
                JournalOpen(
                    JournalEntry(
                        id=rec.journal_id,
                        entity_id=entity_id,
                        title=defn.title,
                        severity=defn.severity,
                        body=defn.body,
                        on=now,
                        muted=muted,
                        disabled=disabled,
                        source=source,
                    )
                )
            )
        if rec.silenced:  # wyciszony/wyłączony: tylko dziennik
            return actions
        actions.extend(_notify_actions(rec, opts, now))
        return actions

    # effective == "off"
    if rec is None:  # osierocony wpis dziennika domknąć w przybliżeniu
        return [JournalClose(entity_id, None, now, True)]
    return _resolve_actions(
        state, rec, now, opts, existing_persistents, off_approx=(source == "catchup")
    )


def catch_up(
    state: State,
    snapshot: Mapping[str, tuple[AlertDef | None, str | None]],
    existing_persistents: AbstractSet[str],
    now: datetime,
    opts: Options,
) -> list[Action]:
    """Po starcie HA (lub przeładowaniu) uzgodnij stan z rzeczywistością.

    `snapshot` = {entity_id: (AlertDef|None, efektywny stan)} dla wszystkich
    encji z prefiksem alertu, które istnieją w HA.
    """
    actions: list[Action] = []
    for entity_id in list(state.active):
        if entity_id not in snapshot:
            actions.append(ScheduleRecheck(entity_id, timedelta(seconds=MISSING_RECHECK_S)))
    for entity_id, (defn, effective) in snapshot.items():
        rec = state.active.get(entity_id)
        if effective == "on" and rec is not None:
            if defn is not None:
                rec.body_now = defn.body
            if (
                opts.persistent_enabled
                and rec.persistent_id
                and rec.persistent_id not in existing_persistents
            ):
                # Persistenty nie przeżywają restartu — odtwórz z treścią z chwili ON.
                actions.append(
                    CreatePersistent(
                        rec.persistent_id, rec.title, rec.body_on, rec.journal_id, restore=True
                    )
                )
            continue
        actions.extend(
            handle_state(
                state, entity_id, defn, effective, now, opts, existing_persistents, source="catchup"
            )
        )
    return actions


def handle_missing(
    state: State,
    entity_id: str,
    now: datetime,
    opts: Options,
    existing_persistents: AbstractSet[str],
    still_missing: bool,
) -> list[Action]:
    """Encja zniknęła (np. template.reload) i po odczekaniu nadal jej nie ma."""
    rec = state.active.get(entity_id)
    if rec is None or not still_missing:
        return []
    return _resolve_actions(
        state, rec, now, opts, existing_persistents, off_approx=True, dismiss_only=True
    )


def tick(state: State, now: datetime, opts: Options) -> list[Action]:
    """Przypomnienia (co minutę): trwający, zgłoszony pushem, niewyciszony, nieodrzucony."""
    actions: list[Action] = []
    if not opts.push_enabled:
        return actions
    for rec in list(state.active.values()):
        if rec.silenced or rec.dismissed or not rec.push_sent or not rec.targets:
            continue
        interval = opts.reminder.get(rec.severity)
        if interval is None:
            continue
        last = rec.last_push_ts or rec.on_ts
        if now - last >= interval:
            rec.last_push_ts = now
            rec.reminders += 1
            actions.append(
                SendPush(
                    rec.targets,
                    rec.title,
                    rec.body_on,
                    push_data(rec, opts),
                    rec.journal_id,
                    reminder=True,
                )
            )
            if rec.journal_id:
                actions.append(JournalMark(rec.journal_id, reminders=rec.reminders))
    return actions


# --------------------------------------------------------------------------
# Usługi (działania użytkownika)
# --------------------------------------------------------------------------


def dismiss(state: State, entity_id: str) -> list[Action]:
    """Odrzuć bieżące wystąpienie: znika z listy aktywnych, bez przypomnień."""
    rec = state.active.get(entity_id)
    if rec is None or rec.dismissed:
        return []
    rec.dismissed = True
    return _mark(rec, dismissed=True)


def undismiss(state: State, entity_id: str) -> list[Action]:
    """Przywróć odrzucone wystąpienie na listę aktywnych (bez wysyłania)."""
    rec = state.active.get(entity_id)
    if rec is None or not rec.dismissed:
        return []
    rec.dismissed = False
    return []


def mute(state: State, entity_id: str) -> list[Action]:
    """Wycisz do odwołania: to i kolejne wystąpienia nic nie wysyłają."""
    state.muted.add(entity_id)
    rec = state.active.get(entity_id)
    if rec is None:
        return []
    rec.silenced = True
    return _mark(rec, muted=True)


def unmute(state: State, entity_id: str) -> list[Action]:
    """Odwołaj wyciszenie; trwające wystąpienie nadal nic nie wysyła."""
    state.muted.discard(entity_id)
    return []


def disable(state: State, entity_id: str) -> list[Action]:
    """Wyłącz na stałe: nic nie wysyła i znika z listy aktywnych."""
    state.disabled.add(entity_id)
    rec = state.active.get(entity_id)
    if rec is None:
        return []
    rec.silenced = True
    return _mark(rec, disabled=True)


def enable(state: State, entity_id: str) -> list[Action]:
    """Włącz z powrotem; trwające wystąpienie wraca na listę, ale nic nie wysyła."""
    state.disabled.discard(entity_id)
    return []


def cleanup(
    state: State,
    now: datetime,
    opts: Options,
    existing_persistents: AbstractSet[str],
    orphans: bool = False,
) -> list[Action]:
    """Sprzątanie persistentów ustąpionych alertów (i duchów)."""
    actions: list[Action] = []
    dismissed: set[str] = set()
    threshold = timedelta(days=opts.cleanup_after_days)
    for pid, off in list(state.resolved_persistents.items()):
        if pid not in existing_persistents:
            del state.resolved_persistents[pid]
            continue
        if now - off >= threshold:
            actions.append(DismissPersistent(pid))
            dismissed.add(pid)
            del state.resolved_persistents[pid]
    if orphans:
        known = {rec.persistent_id for rec in state.active.values() if rec.persistent_id}
        known |= set(state.resolved_persistents) | dismissed
        for pid in sorted(existing_persistents):
            if pid.startswith(PERSISTENT_PREFIX) and pid not in known:
                actions.append(DismissPersistent(pid))
    return actions


def persistent_removed(state: State, notification_id: str) -> bool:
    """Użytkownik zamknął powiadomienie w dzwonku — nie wskrzeszać."""
    changed = False
    for rec in state.active.values():
        if rec.persistent_id == notification_id:
            rec.persistent_id = None
            changed = True
    if state.resolved_persistents.pop(notification_id, None) is not None:
        changed = True
    return changed


# --------------------------------------------------------------------------
# Widok dla encji integracji
# --------------------------------------------------------------------------


def _item(state: State, rec: ActiveRecord) -> dict[str, Any]:
    return {
        "entity_id": rec.entity_id,
        "title": rec.title,
        "severity": rec.severity,
        "body": rec.body_on,
        "body_now": rec.body_now,
        "since": _iso(rec.on_ts),
        "muted": rec.entity_id in state.muted,
    }


def active_summary(state: State, now: datetime) -> dict[str, Any]:
    """Atrybuty `sensor.aktywne_alerty` i `sensor.alerty_wyciszone`.

    Lista aktywnych = trwające, nieodrzucone i niewyłączone (wyciszone są na liście,
    z flagą `muted`). Odrzucone osobno; wyciszone i wyłączone jako listy entity_id
    (także nietrwające — do katalogu i sekcji „Wyłączone”).
    """
    ordered = sorted(state.active.values(), key=lambda r: (SEVERITIES.index(r.severity), r.on_ts))
    visible = [r for r in ordered if not r.dismissed and r.entity_id not in state.disabled]
    dismissed = [r for r in ordered if r.dismissed and r.entity_id not in state.disabled]
    counts = {sev: sum(1 for r in visible if r.severity == sev) for sev in SEVERITIES}
    return {
        "total": len(visible),
        "errors_count": counts["error"],
        "warnings_count": counts["warning"],
        "infos_count": counts["info"],
        "errors": [r.title for r in visible if r.severity == "error"],
        "warnings": [r.title for r in visible if r.severity == "warning"],
        "infos": [r.title for r in visible if r.severity == "info"],
        "active": [_item(state, r) for r in visible],
        "dismissed": [_item(state, r) for r in dismissed],
        "since": {r.entity_id: _iso(r.on_ts) for r in state.active.values()},
        "muted": sorted(state.muted),
        "disabled": sorted(state.disabled),
    }
