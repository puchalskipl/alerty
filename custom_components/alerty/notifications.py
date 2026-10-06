"""Wykonanie akcji powiadomień: persistent (dzwonek) i push (notify.*).

O tym, czy kanał jest włączony, decyduje logika (logic.py) — tu tylko wysyłka.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound

from .logic import CreatePersistent, DismissPersistent, SendPush

_LOGGER = logging.getLogger(__name__)


class Notifier:
    """Cienka warstwa nad persistent_notification i notify.*"""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def create_persistent(self, action: CreatePersistent) -> None:
        persistent_notification.async_create(
            self.hass,
            action.message,
            title=action.title,
            notification_id=action.notification_id,
        )

    def dismiss_persistent(self, action: DismissPersistent) -> None:
        persistent_notification.async_dismiss(self.hass, action.notification_id)

    async def async_send_push(self, action: SendPush) -> list[str]:
        """Wysyła push do każdego celu; zwraca listę błędów (bez przerywania)."""
        errors: list[str] = []
        payload: dict[str, Any] = {
            "title": action.title,
            "message": action.message,
            "data": dict(action.data),
        }
        for target in action.targets:
            try:
                await self.hass.services.async_call("notify", target, payload, blocking=True)
            except ServiceNotFound:
                errors.append(f"notify.{target}: brak usługi")
                _LOGGER.warning("Alert push: usługa notify.%s nie istnieje", target)
            except Exception as err:  # noqa: BLE001 — jeden cel nie może zablokować reszty
                errors.append(f"notify.{target}: {err}")
                _LOGGER.warning("Alert push do notify.%s nie powiódł się: %s", target, err)
        return errors
