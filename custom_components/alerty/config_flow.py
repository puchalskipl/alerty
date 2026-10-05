"""Config flow: jeden wpis, cała konfiguracja w opcjach (UI)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
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
from .options import Options


def _duration_dict(value: timedelta | None, with_days: bool) -> dict[str, int]:
    total = int(value.total_seconds()) if value else 0
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    if with_days:
        return {"days": days, "hours": hours, "minutes": minutes, "seconds": seconds}
    return {"hours": days * 24 + hours, "minutes": minutes, "seconds": seconds}


class AlertyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Dodanie integracji: bez pytań, defaulty do opcji."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
        return self.async_create_entry(title="Alerty", data={}, options=Options().to_mapping())

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return AlertyOptionsFlow()


class AlertyOptionsFlow(config_entries.OptionsFlow):
    """Formularz opcji — zmiany działają od razu (update listener w __init__)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = Options.from_mapping(self.config_entry.options)
        notify_services = self._notify_services()
        for targets in current.push_targets.values():
            for target in targets:
                if target not in notify_services:
                    notify_services.append(target)

        schema: dict[Any, Any] = {
            vol.Required(OPT_MODE, default=current.mode): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[MODE_OBSERVE, MODE_NORMAL],
                    translation_key="mode",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                OPT_STARTUP_GRACE, default=_duration_dict(current.startup_grace, False)
            ): selector.DurationSelector(selector.DurationSelectorConfig(enable_day=False)),
        }
        for sev in SEVERITIES:
            schema[
                vol.Optional(
                    OPT_PUSH_TARGETS.format(severity=sev), default=list(current.push_targets[sev])
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_services,
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema[
                vol.Required(OPT_PERSISTENT.format(severity=sev), default=current.persistent[sev])
            ] = selector.BooleanSelector()
            schema[
                vol.Required(
                    OPT_REMINDER.format(severity=sev),
                    default=_duration_dict(current.reminder[sev], True),
                )
            ] = selector.DurationSelector(selector.DurationSelectorConfig(enable_day=True))
            schema[
                vol.Required(OPT_CHANNEL.format(severity=sev), default=current.channels[sev])
            ] = selector.TextSelector()
        schema.update(
            {
                vol.Required(OPT_ON_RESOLVE, default=current.on_resolve): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[ON_RESOLVE_UPDATE, ON_RESOLVE_DISMISS],
                        translation_key="on_resolve",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    OPT_CLEANUP_AFTER_DAYS, default=current.cleanup_after_days
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPT_CLEANUP_TIME, default=current.cleanup_time.isoformat()
                ): selector.TimeSelector(),
                vol.Required(
                    OPT_RETENTION_DAYS, default=current.retention_days
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPT_PUSH_CLICK_PATH, default=current.push_click_path
                ): selector.TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))

    def _notify_services(self) -> list[str]:
        getter = getattr(self.hass.services, "async_services_for_domain", None)
        if getter is not None:
            services = getter("notify")
        else:
            services = self.hass.services.async_services().get("notify", {})
        return sorted(name for name in services if name != "send_message")
