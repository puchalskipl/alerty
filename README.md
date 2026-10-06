# Alerty

Home Assistant custom integration that turns template `binary_sensor.alert_*` entities into
notifications, a journal and a manageable alert list. Alerts stay defined in YAML; the
integration only observes them.

## How it works

- Any `binary_sensor.alert_*` with a `severity` attribute (`error` / `warning` / `info`) is an alert.
- `off → on`: snapshots `message`, opens a journal entry, creates a persistent notification and
  sends push (per severity defaults, overridable per alert).
- `on → off`: closes the journal entry and updates the notification to
  `✅ <title>` with the original message, resolve time and duration. No push.
- `unavailable` / `unknown` never resolve an alert. After an HA restart the integration waits
  (default 5 min), then reconciles stored state with reality and restores persistent notifications.

## Alert definition

```yaml
template:
  - binary_sensor:
      - unique_id: alert_server_disk_full
        default_entity_id: binary_sensor.alert_server_disk_full
        name: "Server - disk almost full"
        icon: mdi:harddisk
        delay_off: "00:01:00"
        state: "{{ states('sensor.disk_used') | float(0) > 90 }}"
        attributes:
          severity: "warning"                     # required
          message: "{{ states('sensor.disk_used') }}% used (threshold 90%)."
          notify_targets: '{{ ["mobile"] }}'      # optional, notify.* names; [] = no push
          notify_persistent: false                # optional
          notify_click_path: /lovelace/server     # optional, opened from push
          notify_channel: "Alarm"                 # optional Android channel
          journal: false                          # optional, skip the journal (status-type alerts)
```

## User actions

| Service | Effect |
|---|---|
| `alerty.dismiss` / `alerty.undismiss` | hide the current occurrence (no reminders); the next occurrence notifies normally |
| `alerty.mute` / `alerty.unmute` | mute until unmuted: this and later occurrences send nothing, alert stays visible |
| `alerty.disable` / `alerty.enable` | disable until enabled: sends nothing and is hidden from the active list |
| `alerty.cleanup` | dismiss old resolved notifications, prune the journal |
| `alerty.export_journal` | return journal entries (response data) |

Unmute / enable never catch up: an ongoing occurrence stays silent.

## Entities

- `sensor.aktywne_alerty` — active alerts; attributes `active`, `dismissed`, `muted`, `disabled`, `since`, counts
- `sensor.aktywne_alerty_{bledy,ostrzezenia,info}_count` — counts per severity
- `sensor.alert_dziennik` — journal (`recent`, `top_30d`)
- `sensor.alerty_wyciszone` — muted and disabled alerts
- `switch.alerty_push`, `switch.alerty_powiadomienia_ha` — global push / persistent channels

Entity names and the UI are translated (Polish and English).

## Options (UI)

Default push targets, persistent notifications, reminder interval and Android channel per
severity; startup grace period; what happens to a notification when the alert resolves;
cleanup schedule; journal retention (30 days); default click path. Changes apply without restart.

## Install and develop

Copy `custom_components/alerty` to the HA config directory (or run `tools/deploy.ps1`),
restart HA and add the **Alerty** integration. Tests run without Home Assistant:

```
pip install -r requirements_test.txt
python -m pytest
```
