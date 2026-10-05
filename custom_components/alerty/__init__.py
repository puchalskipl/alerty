"""Alerty — obserwator encji binary_sensor.alert_*: powiadomienia, dziennik, wyciszanie.

Definicje alertów zostają w templates/alerting.yaml; integracja zastępuje
dawny dispatcher (automation.alertowanie), agregator i tracker persistentów.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from . import services
from .const import DOMAIN, ENTITY_IDS
from .engine import Engine
from .options import Options
from .store import AlertyStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]

type AlertyConfigEntry = ConfigEntry[Engine]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    services.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AlertyConfigEntry) -> bool:
    store = AlertyStore(hass)
    state, journal = await store.async_load()
    engine = Engine(hass, Options.from_mapping(entry.options), state, journal, store)
    entry.runtime_data = engine

    _claim_entity_ids(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await engine.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AlertyConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: AlertyConfigEntry) -> None:
    entry.runtime_data.apply_options(Options.from_mapping(entry.options))


def _claim_entity_ids(hass: HomeAssistant) -> None:
    """Przejmij docelowe entity_id (np. sensor.aktywne_alerty) po dawnych szablonach.

    Stary wpis innej platformy jest usuwany tylko, gdy jest osierocony (nie ma go
    już w stanach HA — po usunięciu z YAML i template.reload). Nasza encja
    zarejestrowana wcześniej pod id z sufiksem (_2) zostaje przemianowana.
    """
    registry = er.async_get(hass)
    for key, desired in ENTITY_IDS.items():
        domain = desired.split(".", 1)[0]
        unique_id = f"{DOMAIN}_{key}"
        ours = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if ours == desired:
            continue
        holder = registry.async_get(desired)
        if holder is not None and holder.platform != DOMAIN:
            if hass.states.get(desired) is not None:
                continue  # inna integracja nadal używa tego id — nie ruszamy
            registry.async_remove(desired)
            _LOGGER.info("Usunięto osierocony wpis %s (%s)", desired, holder.platform)
        if ours is not None and registry.async_get(desired) is None:
            registry.async_update_entity(ours, new_entity_id=desired)
            _LOGGER.info("Przemianowano %s → %s", ours, desired)
