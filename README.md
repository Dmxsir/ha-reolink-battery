# Reolink Battery Camera for Home Assistant

<div align="center">

[![Release](https://img.shields.io/github/v/release/Dmxsir/ha-reolink-battery?style=for-the-badge&color=2ea44f&logo=github&label=release)](https://github.com/Dmxsir/ha-reolink-battery/releases/latest)
[![Beta](https://img.shields.io/github/v/release/Dmxsir/ha-reolink-battery?include_prereleases&style=for-the-badge&color=orange&logo=github&label=beta)](https://github.com/Dmxsir/ha-reolink-battery/releases)
[![Validate](https://img.shields.io/github/actions/workflow/status/Dmxsir/ha-reolink-battery/validate.yaml?branch=main&style=for-the-badge&logo=githubactions&label=validate)](https://github.com/Dmxsir/ha-reolink-battery/actions/workflows/validate.yaml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=home-assistant)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Dmxsir&repository=ha-reolink-battery&category=integration)
[![License](https://img.shields.io/github/license/Dmxsir/ha-reolink-battery?style=for-the-badge&color=blue)](LICENSE)
[![Open Issues](https://img.shields.io/github/issues/Dmxsir/ha-reolink-battery?style=for-the-badge&color=red&logo=github)](https://github.com/Dmxsir/ha-reolink-battery/issues)
[![Stars](https://img.shields.io/github/stars/Dmxsir/ha-reolink-battery?style=for-the-badge&color=yellow&logo=github)](https://github.com/Dmxsir/ha-reolink-battery/stargazers)

</div>

An experimental Home Assistant custom integration for battery-powered Reolink
cameras that should not be treated like permanently online RTSP or ONVIF
cameras.

The primary tested device is the **Reolink Argus 2E**. The intended workflow is:

```text
Motion event
-> Reolink Android push notification
-> Home Assistant Companion Last Notification sensor
-> persistent event queue
-> battery-safe camera wake
-> locate the matching SD recording
-> verified MP4 download
-> Home Assistant event
-> optional automation (for example Telegram)
```

## Development status

The current tested baseline is **v0.1.2-beta.45**. It is being kept unchanged
for a soak-test period before promotion to the next stable release.

The automatic recording worker is operational: a matching Android Reolink push
notification is queued, the worker waits for the camera recording to settle,
opens a short local Baichuan session, downloads the matching SD-card recording,
verifies the MP4 on disk, and then fires the
`reolink_battery_recording_ready` Home Assistant event.

The integration remains battery-oriented. It does not maintain a permanent
camera connection and does not use RTSP, ONVIF, preview polling, or continuous
streaming.

## Required

- **Home Assistant 2026.8.1 or newer**.
- A supported Reolink battery camera with recording enabled on its SD card.
- The camera's **local administrator credentials**. On legacy Argus cameras the
  username is normally `admin`; the local device password is separate from the
  Reolink account password.
- A physical IPv4 LAN interface on the Home Assistant host that can reach the
  camera directly. Do not select Tailscale, VPN, or other `/32` virtual
  interfaces.
- For automatic motion-to-recording processing, an **Android phone** running
  both the official **Reolink App** and the official **Home Assistant Companion
  App**. The current notification bridge is Android-specific.

### Reolink App notification setup

The integration uses the Reolink push notification received by the Android
phone as the motion trigger. On the phone:

1. Allow Android notifications for the **Reolink App**.
2. In the Reolink App, open the camera settings and enable **Push Notifications**.
3. Configure the notification schedule/detection types so the desired motion
   events generate a push notification.
4. Use Reolink's notification test, or trigger a real motion event, and confirm
   that the phone receives the camera notification.

Official Reolink instructions:
https://support.reolink.com/articles/360004189214-How-to-Enable-Push-Notifications-on-iOS-and-Android-Phones/

### Home Assistant Companion App notification sensor

On the same Android phone:

1. Open **Home Assistant App -> Settings -> Companion App -> Manage Sensors**.
2. Find and enable **Last Notification**.

![Home Assistant Companion App - Last Notification sensor](https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/Companion%20App.jpg)

3. Grant Home Assistant the requested **notification access** permission.
4. Open the Last Notification sensor settings and configure its **Allow List**
   to include the Reolink App (`com.mcu.reolink`). Using an allow list is
   recommended instead of disabling the allow-list requirement.
5. Trigger one Reolink notification after enabling the sensor. A notification
   sensor using an allow list may not appear as a Home Assistant entity until an
   allowed application posts a notification.
6. In Home Assistant go to **Settings -> Devices & services -> Reolink Battery -> Configure**
   and select that phone's **Last Notification** sensor.

Home Assistant Companion sensor documentation:
https://companion.home-assistant.io/docs/core/sensors/#notification-sensors

## Manual Installation

1. Download the desired release archive from the GitHub **Releases** page.
2. Extract the archive.
3. Copy the folder:

   ```text
   custom_components/reolink_battery
   ```

   into your Home Assistant configuration directory so the final path is:

   ```text
   /config/custom_components/reolink_battery
   ```

4. If `/config/custom_components` does not exist, create it first.
5. Restart Home Assistant.
6. Go to **Settings -> Devices & services -> Add integration**.
7. Search for **Reolink Battery** and complete the setup flow below.

## HACS installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Dmxsir&repository=ha-reolink-battery&category=integration)

1. Open **HACS** in Home Assistant.
2. Select **Integrations**.
3. Open the menu and select **Custom repositories**.
4. Add `https://github.com/Dmxsir/ha-reolink-battery` as category
   **Integration**.
5. Open **Reolink Battery Camera** and select **Download**.
6. Restart Home Assistant.
7. Go to **Settings -> Devices & services -> Add integration**.
8. Search for **Reolink Battery**.

## Configuration

The config flow asks for two separate credential layers:

- **Reolink account credentials** for account login, optional email MFA,
  device discovery, and cloud event retrieval.
- **Camera-local credentials** for the short Baichuan device session. On a
  legacy Argus 2E the username normally defaults to `admin`, and its password
  is not the Reolink account password.

### Step-by-step setup

#### 1. Sign in to your Reolink account

Enter the email address and password for your Reolink account. This step is used
to discover the battery cameras linked to the account and does **not** contact
or wake the camera itself.

![Reolink account sign-in](https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-37-42.png)

#### 2. Select the camera

Select the battery-powered Reolink camera that you want to add to Home Assistant,
then press **Submit**.

![Select a Reolink battery camera](https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-40-18.png)

#### 3. Enter the camera-local credentials

Enter the camera's own local administrator credentials. On legacy Argus cameras,
the username is normally `admin`. The **camera-local password is the device
password configured for the camera and is not your Reolink account password**.

Select the physical LAN interface that can reach the camera directly. Do not
select Tailscale, VPN, or other `/32` virtual interfaces.

![Camera-local credentials and physical LAN interface](https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-40-40.png)

#### 4. Select the Companion App notification sensor

After the integration has been added, open:

**Settings -> Devices & services -> Reolink Battery -> Configure**

Select the Android phone's **Last Notification** sensor that you prepared in the
Required section. The worker will listen to that entity without polling the
phone or the camera.

The integration selects a physical private IPv4 LAN and rejects `/32`
VPN/Tailscale adapters. Setup validation resolves and wakes the selected camera,
authenticates, and disconnects immediately.

It does not enable or use RTSP, ONVIF, HTTP fallback, preview, live view,
snapshot polling, or a permanent camera connection. Cloud event polling does
not contact or wake the camera.

## Automation Examples

After a recording has been completely downloaded and verified, the integration
fires:

```text
reolink_battery_recording_ready
```

Useful event data includes `device_name`, `alarm_time`, `file_path`,
`media_content_id`, and `file_size`.

### Send the recording to Telegram

Replace the Telegram notify entity with your own entity.

```yaml
alias: Reolink - Send motion recording to Telegram
triggers:
  - trigger: event
    event_type: reolink_battery_recording_ready
conditions: []
actions:
  - action: telegram_bot.send_video
    data:
      entity_id:
        - notify.YOUR_TELEGRAM_BOT_ENTITY
      file: "{{ trigger.event.data.file_path }}"
      caption: "🎥 Motion detected on {{ trigger.event.data.device_name }}"
mode: queued
max: 10
```

### Send to Telegram and delete the local copy afterwards

If Telegram is your long-term storage and you do not want downloaded recordings
to accumulate on the Home Assistant server, add this `shell_command` to
`configuration.yaml`:

```yaml
shell_command:
  delete_reolink_recording: 'rm -f -- "{{ file_path }}"'
```

Then use an automation such as:

```yaml
alias: Reolink - Send recording to Telegram and clean up
triggers:
  - trigger: event
    event_type: reolink_battery_recording_ready
conditions: []
actions:
  - variables:
      video_file: "{{ trigger.event.data.file_path }}"

  - action: telegram_bot.send_video
    data:
      entity_id:
        - notify.YOUR_TELEGRAM_BOT_ENTITY
      file: "{{ video_file }}"
      caption: "🎥 Motion detected on {{ trigger.event.data.device_name }}"

  - delay:
      seconds: 5

  - condition: template
    value_template: >
      {{ video_file is string
         and video_file.startswith('/media/reolink_battery/')
         and video_file.endswith('.mp4') }}

  - action: shell_command.delete_reolink_recording
    data:
      file_path: "{{ video_file }}"

mode: queued
max: 10
```

The path check intentionally limits deletion to MP4 files inside the default
`/media/reolink_battery/` directory. If you use a custom Home Assistant media
directory, adjust the prefix accordingly. Reload **Shell Commands** or restart
Home Assistant after adding the `shell_command`.

## Privacy and security

Credentials are stored through Home Assistant config entries. Passwords,
account tokens, session secrets, cryptographic material, and full camera UIDs
are excluded from diagnostics and logs.

## Acknowledgements and credits

This project builds on, and was made possible by, work from the wider open-source
Home Assistant and Reolink reverse-engineering communities.

- **[reolink_aio](https://github.com/starkillerOG/reolink_aio)** by
  **starkillerOG and contributors** — the core Python Reolink API/Baichuan
  library used directly by this integration for device communication. The
  project is MIT licensed.
- **[Neolink](https://github.com/thirtythreeforty/neolink)** by
  **thirtythreeforty**, together with the extended
  **[QuantumEntangledAndy/neolink](https://github.com/QuantumEntangledAndy/neolink)**
  fork — an invaluable independent reverse-engineering reference for Reolink's
  proprietary Baichuan protocol, UDP/P2P behavior, discovery, connection and
  transport semantics. Neolink is AGPL-licensed; this project does **not**
  incorporate Neolink source code and uses it only as a protocol/reference
  implementation.
- **[PyCryptodome](https://github.com/Legrandin/pycryptodome)** by
  **Legrandin and contributors** — provides the cryptographic primitives used
  through the `pycryptodomex` dependency.
- **[Home Assistant](https://github.com/home-assistant/core)** and its
  contributors — for the integration framework, config-entry APIs, event bus,
  media-source infrastructure, diagnostics conventions, and the ecosystem this
  integration is built for.
- **Reolink** — for the hardware and official software behavior used during
  interoperability testing. Official application/SDK behavior was used only as
  a behavioral reference; no proprietary Reolink binaries, SDK files, captured
  traffic, or recordings are distributed by this project.
- **OpenAI Codex and ChatGPT** — used as development assistants for code review,
  debugging, test design, protocol-analysis support, and documentation. Protocol
  behavior and fixes were validated against source code, automated regression
  tests, and real camera hardware before being retained.

Special thanks to everyone who publishes protocol research, bug reports, test
results, and open-source tooling around Reolink devices. That work significantly
reduced the amount of duplicated reverse-engineering effort required here.

## License

This project is licensed under the [MIT License](LICENSE). It depends on the
MIT-licensed `reolink-aio` package and does not include proprietary Reolink
binaries, SDK files, captured traffic, or recordings.

This project is not affiliated with or endorsed by Reolink.
