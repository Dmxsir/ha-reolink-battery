# Argus 2E device information research

Research date: 2026-08-14
Target: standalone Reolink Argus 2E with a legacy local credential
Runtime dependency inspected: `reolink-aio==0.21.8`

This milestone adds only cache-backed Home Assistant entities for values proven
on the real camera. It adds no local polling and does not change Message Center
behavior, download logic, protocol framing, or the published release.

## Source classification

Cloud discovery currently requests and retains only the device title, model,
binding status, and authentication capability metadata. On the real configured
device, model and `bound` status have been observed without contacting the
camera. Firmware, battery, charging, Wi-Fi, and storage values have not been
observed in that cloud response.

Message Center is also cloud-only and can carry event time, alarm type, and AI
type. The real STM v1 response has been decoded successfully, but its live page
contained zero events. Last-event entities therefore remain unproven and are
outside this milestone.

Safe cloud presence summary:

```text
CLOUD_MODEL_PRESENT=1
CLOUD_STATUS_PRESENT=1
CLOUD_FIRMWARE_PRESENT=0
CLOUD_BATTERY_PRESENT=0
CLOUD_CHARGING_PRESENT=0
CLOUD_STORAGE_PRESENT=0
CLOUD_WIFI_SIGNAL_PRESENT=0
CLOUD_LIVE_EVENT_PRESENT=0
```

## Static command map

`Wake required` means that a sleeping standalone Argus must first establish the
short UID/LAN/authenticated session. `reolink_aio` lists commands 253, 102, and
115 among its non-waking state reads, meaning they are suitable to collect when
a connection is already open; that does not make them cloud data.

| Command | Name/source | `reolink_aio` 0.21.8 | Expected safe response | Read-only | Wake required | Confidence / result |
|---|---|---|---|---|---|---|
| 80 | `GetDevInfo` | Yes, `get_info(None)` | `type`, `hardwareVersion`, `firmwareVersion`; serial/item fields deliberately discarded | Yes | Yes | High; validated live |
| 253 | `GetBatteryInfo` | Yes, `get_battery_info(0)` | `batteryPercent`, `chargeStatus`, `adapterStatus`, `lowPowerFlag`, `temperature`, `current`, `voltage`, `batteryVersion` | Yes | Yes | High; channel-0 shim and normalized battery/charging values validated live |
| 252 | `BatteryInfo` push | Parser only | Same battery structure as cmd253 | Yes, asynchronous state | Requires an open session | High parser confidence; not used because no persistent session is allowed |
| 102 | `GetHddInfo` | Yes, `GetHddInfo()` | SD/HDD list: `capacityG/M`, `remainSizeG/M`, `format`, `mount`, `number`, `storageType` | Yes | Yes | High; validated live |
| 115 | `getWifiSignal` | Yes, `get_wifi_signal(None)` | `signal` integer interpreted by `reolink_aio` as dBm | Yes | Yes | High; validated live |
| 199 | ability/host data | Yes | Broad capability metadata | Yes | Yes | High, but not sent: direct known commands were sufficient |
| unknown | APK `getBatteryAnalysis`, `getBatteryStatus`, `getBatteryType`, `getBatteryUsage`, `getPowerSupplyMode` | No proven Baichuan mapping | Additional battery/power structures | Appears read-only | Presumed yes | Low/medium; not sent because command IDs/framing are unproven |
| HTTPS | cloud device list | Yes, integration-owned | model, title, binding/auth capability metadata | Yes | No | High; model/status validated |
| HTTPS | Message Center | Yes, integration-owned | event timestamp/type/AI type when a message exists | Yes | No | Decoder validated; no real event observed yet |

The Android bundle confirms these enum values without requiring a camera request:

- adapter: `none`, `adapter`, `solarPanels`, `ACAdapter`, `DCAdapter`
- charging: `none`, `charging`, `completed`
- power supply: `normal`, `lowChargeOnly`, `disconnected`, `tooLow`,
  `tooHigh`, `unknown`
- storage UI states: undetected, abnormal, unformatted, or healthy

## Controlled live session

The initial device-information session performed only:

```text
UID resolve -> wake -> LAN -> legacy auth
-> cmd80 -> cmd253 -> cmd102 -> cmd115
-> logout/transport close
```

No preview, stream, RTSP, ONVIF, HTTP camera request, recording search,
download, configuration write, or persistent connection was used.

Safe normalized result:

```text
TRANSPORT_TYPE=LAN
DEVICE_INFO_SUPPORTED=1
DEVICE_MODEL=Reolink Argus 2E
DEVICE_FIRMWARE=0616_722_52_478
DEVICE_HARDWARE=BIPC_36S7616MC94

BATTERY_COMMAND_ACCEPTED=1
BATTERY_SUPPORTED=0

STORAGE_SUPPORTED=1
STORAGE_PRESENT=1
STORAGE_STATUS=normal
STORAGE_TOTAL_BYTES=14857000000
STORAGE_USED_BYTES=14311000000
STORAGE_FREE_BYTES=546000000

WIFI_SIGNAL_SUPPORTED=1
WIFI_RSSI_DBM=-30

SESSION_CLOSE_OK=1
SESSION_SECONDS=8.437
```

The storage parser follows `reolink_aio`'s decimal convention:
`capacityG * 1000 + capacityM` produces MB, then MB is converted to bytes with
`1 MB = 1,000,000 bytes`.

### Cmd253 channel-0 analysis and controlled validation

`reolink_aio` 0.21.8 registers cmd253 in `Baichuan.get_battery_info(channel)`,
which calls `_send_and_parse(253, channel)`. Its BatteryInfo parser calls
`_get_channel_from_xml_element()` before extracting the response fields. That
helper discards the element unless its channel already exists in
`Host._channels` or `Host._stream_channels`; the payload is therefore rejected
before parsing. The portable Argus login path intentionally disables first-login
metadata parsing because this firmware omits `analogChnNum`, leaving both lists
empty.

The narrow compatibility fix registers only the proven standalone channel 0 in
those two host inventories and sets the channel count to at least one. It does
not monkey-patch the parser or alter cmd253 framing. A deterministic offline test
shows that the same synthetic BatteryInfo is discarded before this adapter shim
and retained after it.

After the drop path was understood, exactly one additional read-only session was
performed:

```text
UID resolve -> wake -> LAN -> legacy auth
-> cmd80 -> cmd253
-> logout/transport close
```

Safe normalized result:

```text
BATTERY_CMD253_OK=1
BATTERY_CHANNEL=0
DEVICE_MODEL=Reolink Argus 2E
BATTERY_PERCENT=100
BATTERY_CHARGING=1
BATTERY_POWER_SOURCE=unknown
BATTERY_TEMPERATURE=34
SESSION_CLOSE_OK=1
SESSION_SECONDS=1.625
```

Battery percentage and charging are therefore verified. The response did not
provide enough stable information to classify battery/solar/external power, so
no power-source entity is implemented. Temperature was observed but is outside
the requested verified entity set. No low-battery value was present.

## Parser/cache design

`device_status.py` contains pure, Home-Assistant-independent parsers and two
separate immutable state lifetimes:

- `CloudState`: model/status and eventual Message Center event metadata; updated
  by the existing cloud poller and never contacts the camera.
- `LocalState`: device, battery, storage, Wi-Fi, and `refreshed_at`; replaced only
  during an already-open authenticated session or a future explicit refresh.

`DeviceState.with_cloud()` preserves the cached local snapshot while the camera
sleeps. `DeviceState.with_local()` does not change cloud state. The integration
restores this snapshot at setup, exposes presentation-only entities, and persists
a merged snapshot only when an already-open local session supplies one. Sensor
platforms contain no update method and set `should_poll` to false.

## Entity proposal

Only values proven on the real Argus are candidates for the first implementation.

| Field | Source | Wake required | Verified on Argus 2E | Proposed HA representation |
|---|---|---:|---:|---|
| Model | Cloud and cmd80 | No when read from cloud | Yes | DeviceInfo `model` |
| Firmware | Baichuan cmd80 | Yes | Yes | DeviceInfo `sw_version` |
| Hardware | Baichuan cmd80 | Yes | Yes | DeviceInfo `hw_version` |
| SD present/status | Baichuan cmd102 | Yes | Yes | Optional diagnostic storage-status entity |
| SD total | Baichuan cmd102 | Yes | Yes | Diagnostic data-size sensor, bytes |
| SD used | Baichuan cmd102 | Yes | Yes | Diagnostic data-size sensor, bytes |
| SD free | Baichuan cmd102 | Yes | Yes | Diagnostic data-size sensor, bytes |
| Wi-Fi RSSI | Baichuan cmd115 | Yes | Yes | Diagnostic signal-strength sensor, dBm |
| Battery percentage | Baichuan cmd253 | Yes | Yes | Battery percentage sensor |
| Charging | Baichuan cmd253 | Yes | Yes | Battery-charging binary sensor |
| Solar/external power | Baichuan cmd253 / APK enums | Yes | No | Do not create yet |
| Last event time | Message Center | No | No real event yet | Do not create yet |
| Last event type / AI type | Message Center | No | No real event yet | Do not create yet |
| Last local refresh | Local cache | No extra wake | Yes | Diagnostic timestamp sensor |

Recommended update policy:

1. Keep the current cloud poller cloud-only.
2. Populate local state during setup validation and opportunistically inside a
   future already-open motion/download session.
3. Optionally add a user-triggered refresh later, with one short session.
4. Never schedule frequent local polling and never clear the last valid local
   values merely because the camera is sleeping.

## Release-ready entity set

- Battery percentage sensor
- Charging binary sensor
- Storage total sensor
- Storage used sensor
- Storage free sensor
- Wi-Fi signal sensor
- Last local refresh timestamp sensor
- DeviceInfo model, software version, and hardware version

No power-source, battery-temperature, low-battery, storage-percentage, or
low-storage entity is included.

## Deterministic tests

The offline suite covers battery and charging normalization, absent optional
battery fields, cmd253 channel-0 compatibility, storage conversion and error
states, RSSI validation, DeviceInfo, cache preservation/notification/round-trip,
unavailable entity state, no local polling, and synthetic Home Assistant
setup/unload. Existing Message Center tests remain unchanged. No offline test
contacts the camera or cloud.

Validation results:

```text
REPOSITORY_TESTS_PASSED=21
EXISTING_POC_TESTS_PASSED=39
RUFF_OK=1
JSON_OK=1
HA_CORE_LOAD_OK=1
HA_CORE_ENTITY_COUNT=7
HA_CORE_DEVICE_INFO_OK=1
HA_CORE_LOCAL_CAMERA_CONTACTED=0
HA_CORE_UNLOAD_OK=1
```

The Core smoke test used the locally installed Home Assistant Core 2025.1.4
against a temporary config directory and a copied integration. The harness
removed the unrelated `network` dependency only from that temporary manifest,
because a minimal Core instance does not initialize the HTTP/auth frontend. It
loaded the real sensor and binary-sensor platforms and verified battery state,
charging state, DeviceInfo, and unload. The running Home Assistant instance was
not modified or reloaded.

```text
ARGUS_DEVICE_INFO_RESEARCH_COMPLETE=1
CLOUD_FIELDS_VERIFIED=2
LOCAL_FIELDS_VERIFIED=9
BATTERY_DATA_VERIFIED=1
CHARGING_DATA_VERIFIED=1
STORAGE_DATA_VERIFIED=1
WIFI_SIGNAL_VERIFIED=1
CAMERA_SESSIONS_USED=2
VERIFIED_ENTITIES_READY=1
BATTERY_CMD253_ANALYZED=1
```
