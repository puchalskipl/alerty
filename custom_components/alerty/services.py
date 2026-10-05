"""Usługi alerty.* (rejestrowane raz, na poziomie domeny)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .engine import Engine

SERVICE_SNOOZE = "snooze"
SERVICE_UNSNOOZE = "unsnooze"
SERVICE_ACKNOWLEDGE = "acknowledge"
SERVICE_ENABLE = "enable"
SERVICE_DISABLE = "disable"
SERVICE_CLEANUP = "cleanup"
SERVICE_EXPORT_JOURNAL = "export_journal"

ENTITY_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_ids})
SNOOZE_SCHEMA = ENTITY_SCHEMA.extend({vol.Required("duration"): cv.positive_time_period})
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
    async def snooze(call: ServiceCall) -> None:
        await _engine(hass).async_snooze(call.data["entity_id"], call.data["duration"])

    async def unsnooze(call: ServiceCall) -> None:
        await _engine(hass).async_unsnooze(call.data["entity_id"])

    async def acknowledge(call: ServiceCall) -> None:
        await _engine(hass).async_acknowledge(call.data["entity_id"])

    async def enable(call: ServiceCall) -> None:
        await _engine(hass).async_enable(call.data["entity_id"])

    async def disable(call: ServiceCall) -> None:
        await _engine(hass).async_disable(call.data["entity_id"])

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

    registrations = (
        (SERVICE_SNOOZE, snooze, SNOOZE_SCHEMA, SupportsResponse.NONE),
        (SERVICE_UNSNOOZE, unsnooze, ENTITY_SCHEMA, SupportsResponse.NONE),
        (SERVICE_ACKNOWLEDGE, acknowledge, ENTITY_SCHEMA, SupportsResponse.NONE),
        (SERVICE_ENABLE, enable, ENTITY_SCHEMA, SupportsResponse.NONE),
        (SERVICE_DISABLE, disable, ENTITY_SCHEMA, SupportsResponse.NONE),
        (SERVICE_CLEANUP, cleanup, CLEANUP_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_EXPORT_JOURNAL, export_journal, EXPORT_SCHEMA, SupportsResponse.ONLY),
    )
    for name, handler, schema, response in registrations:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN, name, handler, schema=schema, supports_response=response
            )
