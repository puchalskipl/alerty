"""Opcje integracji jako czysty dataclass (bez importów Home Assistant).

`Options.from_mapping()` przyjmuje płaski słownik z `ConfigEntry.options`
(klucze z const.OPT_*), toleruje brakujące i źle zapisane wartości — zawsze
zwraca kompletny zestaw ustawień z defaultami.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timedelta
from typing import Any, Mapping

from .const import (
    DEFAULT_CHANNELS,
    DEFAULT_CLEANUP_AFTER_DAYS,
    DEFAULT_CLEANUP_TIME,
    DEFAULT_PERSISTENT,
    DEFAULT_PUSH_CLICK_PATH,
    DEFAULT_PUSH_TARGETS,
    DEFAULT_REMINDER_S,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_STARTUP_GRACE_S,
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
    SEVERITIES,
)


def parse_duration(value: Any) -> timedelta | None:
    """Czas trwania z sekund, słownika DurationSelector albo "HH:MM:SS"; None/0 → None."""
    if value is None or value is False:
        return None
    if isinstance(value, timedelta):
        return value if value.total_seconds() > 0 else None
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value)) if value > 0 else None
    if isinstance(value, Mapping):
        try:
            td = timedelta(
                days=float(value.get("days", 0) or 0),
                hours=float(value.get("hours", 0) or 0),
                minutes=float(value.get("minutes", 0) or 0),
                seconds=float(value.get("seconds", 0) or 0),
            )
        except (TypeError, ValueError):
            return None
        return td if td.total_seconds() > 0 else None
    if isinstance(value, str):
        parts = value.strip().split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 3:
            td = timedelta(hours=nums[0], minutes=nums[1], seconds=nums[2])
        elif len(nums) == 2:
            td = timedelta(hours=nums[0], minutes=nums[1])
        elif len(nums) == 1:
            td = timedelta(seconds=nums[0])
        else:
            return None
        return td if td.total_seconds() > 0 else None
    return None


def parse_time(value: Any, default: str) -> time:
    """Godzina z "HH:MM[:SS]" (TimeSelector); przy błędzie default."""
    for candidate in (value, default):
        if isinstance(candidate, time):
            return candidate
        if isinstance(candidate, str):
            try:
                return time.fromisoformat(candidate.strip())
            except ValueError:
                continue
    return time.fromisoformat(default)


def normalize_target(value: str) -> str:
    """Nazwa usługi notify bez prefiksu domeny: "notify.admins" → "admins"."""
    value = value.strip()
    if value.startswith("notify."):
        value = value[len("notify.") :]
    return value


def _targets(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        value = [v for v in value.split(",")]
    try:
        return tuple(normalize_target(str(v)) for v in value if str(v).strip())
    except TypeError:
        return default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ("true", "on", "yes", "1"):
            return True
        if value.lower() in ("false", "off", "no", "0"):
            return False
    return default


def _int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= minimum else default


@dataclass(frozen=True)
class Options:
    """Ustawienia globalne integracji."""

    mode: str = MODE_OBSERVE
    startup_grace: timedelta = timedelta(seconds=DEFAULT_STARTUP_GRACE_S)
    push_targets: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_PUSH_TARGETS)
    )
    persistent: Mapping[str, bool] = field(default_factory=lambda: dict(DEFAULT_PERSISTENT))
    on_resolve: str = ON_RESOLVE_UPDATE
    reminder: Mapping[str, timedelta | None] = field(
        default_factory=lambda: {
            sev: parse_duration(seconds) for sev, seconds in DEFAULT_REMINDER_S.items()
        }
    )
    retention_days: int = DEFAULT_RETENTION_DAYS
    cleanup_after_days: int = DEFAULT_CLEANUP_AFTER_DAYS
    cleanup_time: time = time.fromisoformat(DEFAULT_CLEANUP_TIME)
    push_click_path: str = DEFAULT_PUSH_CLICK_PATH
    channels: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_CHANNELS))

    @property
    def sending(self) -> bool:
        return self.mode == MODE_NORMAL

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "Options":
        data = data or {}
        mode = data.get(OPT_MODE)
        if mode not in (MODE_OBSERVE, MODE_NORMAL):
            mode = MODE_OBSERVE
        on_resolve = data.get(OPT_ON_RESOLVE)
        if on_resolve not in (ON_RESOLVE_UPDATE, ON_RESOLVE_DISMISS):
            on_resolve = ON_RESOLVE_UPDATE
        startup = parse_duration(data.get(OPT_STARTUP_GRACE))
        click = data.get(OPT_PUSH_CLICK_PATH)
        if not isinstance(click, str) or not click.strip():
            click = DEFAULT_PUSH_CLICK_PATH
        channels = {}
        for sev in SEVERITIES:
            value = data.get(OPT_CHANNEL.format(severity=sev))
            channels[sev] = (
                value.strip()
                if isinstance(value, str) and value.strip()
                else DEFAULT_CHANNELS[sev]
            )
        return cls(
            mode=mode,
            startup_grace=startup
            if startup is not None
            else timedelta(seconds=DEFAULT_STARTUP_GRACE_S),
            push_targets={
                sev: _targets(
                    data.get(OPT_PUSH_TARGETS.format(severity=sev)), DEFAULT_PUSH_TARGETS[sev]
                )
                for sev in SEVERITIES
            },
            persistent={
                sev: _bool(data.get(OPT_PERSISTENT.format(severity=sev)), DEFAULT_PERSISTENT[sev])
                for sev in SEVERITIES
            },
            on_resolve=on_resolve,
            reminder={
                sev: parse_duration(
                    data.get(OPT_REMINDER.format(severity=sev), DEFAULT_REMINDER_S[sev])
                )
                for sev in SEVERITIES
            },
            retention_days=_int(data.get(OPT_RETENTION_DAYS), DEFAULT_RETENTION_DAYS, 1),
            cleanup_after_days=_int(
                data.get(OPT_CLEANUP_AFTER_DAYS), DEFAULT_CLEANUP_AFTER_DAYS, 0
            ),
            cleanup_time=parse_time(data.get(OPT_CLEANUP_TIME), DEFAULT_CLEANUP_TIME),
            push_click_path=click.strip(),
            channels=channels,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Płaski słownik — wartości domyślne formularza opcji."""
        result: dict[str, Any] = {
            OPT_MODE: self.mode,
            OPT_STARTUP_GRACE: int(self.startup_grace.total_seconds()),
            OPT_ON_RESOLVE: self.on_resolve,
            OPT_RETENTION_DAYS: self.retention_days,
            OPT_CLEANUP_AFTER_DAYS: self.cleanup_after_days,
            OPT_CLEANUP_TIME: self.cleanup_time.isoformat(),
            OPT_PUSH_CLICK_PATH: self.push_click_path,
        }
        for sev in SEVERITIES:
            result[OPT_PUSH_TARGETS.format(severity=sev)] = list(self.push_targets[sev])
            result[OPT_PERSISTENT.format(severity=sev)] = self.persistent[sev]
            reminder = self.reminder[sev]
            result[OPT_REMINDER.format(severity=sev)] = (
                int(reminder.total_seconds()) if reminder else 0
            )
            result[OPT_CHANNEL.format(severity=sev)] = self.channels[sev]
        return result
