"""Usługi alerty.* (rejestrowane raz, na poziomie domeny)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .engine import USER_ACTIONS, Engine

SERVICE_CLEANUP = "cleanup"
SERVICE_EXPORT_JOURNAL = "export_journal"

ENTITY_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_ids})
CLEANUP_SCHEMA = vol.Schema({vol.Optional("orphans", default=False): cv.boolean})
EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("since"): cv.datetime,
        vol.Optional("until"): cv.datetime,
        vol.Optional("entity_id"): cv.entity_id,
    }
)


def _engine(hass: HomeAssistant) -> Engine:
    for entry in hass.config_entries.async_entries(DOMAIN):
        engine = getattr(entry, "runtime_data", None)
        if isinstance(engine, Engine):
            return engine
    raise HomeAssistantError("Integracja Alerty nie jest załadowana")


def async_register(hass: HomeAssistant) -> None:
    def user_action(name: str):
        async def handler(call: ServiceCall) -> None:
            await _engine(hass).async_user_action(name, call.data["entity_id"])

        return handler

    async def cleanup(call: ServiceCall) -> ServiceResponse:
        dismissed = await _engine(hass).async_cleanup(orphans=call.data["orphans"])
        return {"dismissed": dismissed}

    async def export_journal(call: ServiceCall) -> ServiceResponse:
        entries: list[dict[str, Any]] = _engine(hass).journal.export(
            since=call.data.get("since"),
            until=call.data.get("until"),
            entity_id=call.data.get("entity_id"),
        )
        return {"count": len(entries), "entries": entries}

    registrations = [
        (name, user_action(name), ENTITY_SCHEMA, SupportsResponse.NONE) for name in USER_ACTIONS
    ]
    registrations += [
        (SERVICE_CLEANUP, cleanup, CLEANUP_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_EXPORT_JOURNAL, export_journal, EXPORT_SCHEMA, SupportsResponse.ONLY),
    ]
    for name, handler, schema, response in registrations:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN, name, handler, schema=schema, supports_response=response
            )
