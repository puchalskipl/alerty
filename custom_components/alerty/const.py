"""Stałe integracji alerty (bez importów Home Assistant — używane też w testach)."""

from __future__ import annotations

DOMAIN = "alerty"

# Encje alertów obserwowane przez integrację (definiowane w templates/alerting.yaml).
ALERT_PREFIX = "binary_sensor.alert_"
SEVERITIES = ("error", "warning", "info")

# Atrybuty encji alertu.
ATTR_SEVERITY = "severity"
ATTR_MESSAGE = "message"
ATTR_TITLE = "title"
ATTR_NOTIFY_TARGETS = "notify_targets"
ATTR_NOTIFY_PERSISTENT = "notify_persistent"
ATTR_NOTIFY_CHANNEL = "notify_channel"
ATTR_NOTIFY_CLICK_PATH = "notify_click_path"
ATTR_NOTIFY_REMINDER = "notify_reminder"  # godziny; 0 = bez przypomnień; brak = ustawienie poziomu
ATTR_JOURNAL = "journal"  # false = alert nie trafia do dziennika (tylko widok bieżący)
ATTR_ICON = "icon"
ATTR_FRIENDLY_NAME = "friendly_name"

# Persistent notification: id `alert_<entity_id>_<ts włączenia>` — osobny wpis
# w dzwonku na każde wystąpienie. Tag push jest stały per alert (`alert_<entity_id>`).
PERSISTENT_PREFIX = "alert_"

# Klucze opcji wpisu konfiguracyjnego (options flow).
OPT_MODE = "mode"  # tylko odczyt starych opcji (observe → oba kanały wyłączone)
OPT_PUSH_ENABLED = "push_enabled"  # kanał push na telefon — globalnie wł./wył.
OPT_PERSISTENT_ENABLED = "persistent_enabled"  # kanał „Powiadomienia w HA” (dzwonek)
OPT_STARTUP_GRACE = "startup_grace"
OPT_PUSH_TARGETS = "push_targets_{severity}"
OPT_PERSISTENT = "persistent_{severity}"
OPT_ON_RESOLVE = "on_resolve"
OPT_REMINDER = "reminder_{severity}"
OPT_RETENTION_DAYS = "retention_days"
OPT_CLEANUP_AFTER_DAYS = "cleanup_after_days"
OPT_CLEANUP_TIME = "cleanup_time"
OPT_PUSH_CLICK_PATH = "push_click_path"
OPT_CHANNEL = "channel_{severity}"

MODE_OBSERVE = "observe"  # stara wartość OPT_MODE
ON_RESOLVE_UPDATE = "update"
ON_RESOLVE_DISMISS = "dismiss"

DEFAULT_STARTUP_GRACE_S = 5 * 60
DEFAULT_PUSH_TARGETS = {"error": ("admins",), "warning": (), "info": ()}
DEFAULT_PERSISTENT = {"error": True, "warning": True, "info": False}
DEFAULT_REMINDER_S = {"error": 24 * 3600, "warning": 0, "info": 0}
DEFAULT_RETENTION_DAYS = 30
DEFAULT_CLEANUP_AFTER_DAYS = 7
DEFAULT_CLEANUP_TIME = "04:10:00"
DEFAULT_PUSH_CLICK_PATH = "/lovelace/system"
# Kanały powiadomień Androida — parametry kanału zamrażają się na telefonie przy
# pierwszym użyciu, dlatego nazwy i priorytety są ustalone z góry.
DEFAULT_CHANNELS = {
    "error": "Alerty - błędy",
    "warning": "Alerty - ostrzeżenia",
    "info": "Alerty - informacje",
}
PUSH_IMPORTANCE = {"error": "high", "warning": "default", "info": "low"}
PUSH_COLORS = {"error": "#db4437", "warning": "#f4b400", "info": "#4285f4"}
PUSH_GROUP = "alerty"

# Treści powiadomień (po polsku, jak reszta UI).
TEXT_RESOLVED_TITLE = "✅ {title}"
TEXT_RESOLVED_BODY = "{body} · ustąpiło {time}, trwało {duration}"

# Store.
STORAGE_VERSION = 1
STORE_STATE_KEY = f"{DOMAIN}/state"
STORE_JOURNAL_KEY = f"{DOMAIN}/journal"

# Limity atrybutów (websocket wysyła pełne atrybuty przy każdej zmianie).
JOURNAL_RECENT_COUNT = 25
JOURNAL_RECENT_BODY_CHARS = 120
JOURNAL_TOP_COUNT = 10
JOURNAL_TOP_WINDOW_DAYS = 30

# Sprawdzenie, czy encja alertu zniknęła na stałe (np. po template.reload).
MISSING_RECHECK_S = 10 * 60

# Sygnał dispatchera dla encji integracji.
SIGNAL_UPDATED = f"{DOMAIN}_updated"

# Docelowe entity_id encji integracji (polskie, zgodne z dotychczasowymi szablonami —
# dashboardy i chipy działają bez zmian). Klucz = translation_key, unique_id = alerty_<klucz>.
ENTITY_IDS = {
    "active_alerts": "sensor.aktywne_alerty",
    "errors_count": "sensor.aktywne_alerty_bledy_count",
    "warnings_count": "sensor.aktywne_alerty_ostrzezenia_count",
    "infos_count": "sensor.aktywne_alerty_info_count",
    "journal": "sensor.alert_dziennik",
    "snoozed": "sensor.alerty_wyciszone",
    "push": "switch.alerty_push",
    "persistent": "switch.alerty_powiadomienia_ha",
    "reminder_error": "number.alerty_przypomnienie_bledy",
    "reminder_warning": "number.alerty_przypomnienie_ostrzezenia",
    "reminder_info": "number.alerty_przypomnienie_informacje",
}
