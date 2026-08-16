# Reolink Battery Camera for Home Assistant

An experimental Home Assistant custom integration for battery-powered Reolink
cameras that should not be treated like permanently online RTSP or ONVIF
cameras.

The primary tested device is the **Reolink Argus 2E**. The intended workflow is:

```text
Motion event
-> Reolink cloud notification
-> wake camera
-> locate the matching SD recording
-> download MP4
-> Home Assistant
-> Telegram
```

## Development status

Version **0.1.0** is the first stable release of the currently implemented
feature set.

Milestone 3A is complete: the integration loads, provides an account/device
config flow, validates a short battery-safe local camera session, polls the
cloud Message Center, decodes its STM v1 response, deduplicates normalized
events, and persists them in a bounded pending queue.

The integration provides cache-backed entities for battery percentage,
charging, storage total/used/free, Wi-Fi signal, and the last local refresh
timestamp. Storage is retained internally as bytes and presented as decimal GB
with two suggested decimal places. Model, firmware, and hardware are shown on
the Home Assistant device.

The **Refresh device status** button performs one explicit short local session:
UID resolution, wake, LAN transport, local authentication, status queries, and
immediate disconnect. It updates all local entities without polling. Loading or
reloading Home Assistant does not wake the camera, and no periodic local refresh
is scheduled. Until a local snapshot has been captured, local entities remain
unavailable.

Automatic motion-to-recording download and Telegram delivery are still under
development. The pending queue currently has no camera worker, so a cloud event
cannot wake the camera or download a recording. Message Center polling remains
cloud-only and does not use the separately stored camera-local password.

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

Home Assistant 2026.8.1 or newer is required for this release.

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

The integration selects a physical private IPv4 LAN and rejects `/32`
VPN/Tailscale adapters. Setup validation resolves and wakes the selected camera,
authenticates, and disconnects immediately.

It does not enable or use RTSP, ONVIF, HTTP fallback, preview, live view,
snapshot polling, or a permanent camera connection. Cloud event polling does
not contact or wake the camera.

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
