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

This project is an **alpha** and is not production ready.

Milestone 3A is complete: the integration loads, provides an account/device
config flow, validates a short battery-safe local camera session, polls the
cloud event path, deduplicates decoded events, and persists them in a bounded
pending queue.

Automatic motion-to-recording download and Telegram delivery are still under
development. The pending queue currently has no camera worker, so a cloud event
cannot wake the camera or download a recording. The live Message Center STM v1
response decoder also requires final cloud-only validation.

## HACS installation

1. Open **HACS** in Home Assistant.
2. Select **Integrations**.
3. Open the menu and select **Custom repositories**.
4. Add `https://github.com/Dmxsir/ha-reolink-battery` as category
   **Integration**.
5. Open **Reolink Battery Camera** and select **Download**.
6. Restart Home Assistant.
7. Go to **Settings -> Devices & services -> Add integration**.
8. Search for **Reolink Battery**.

Home Assistant 2026.8.1 or newer is required for this alpha release.

## Configuration

The config flow asks for two separate credential layers:

- **Reolink account credentials** for account login, optional email MFA,
  device discovery, and cloud event retrieval.
- **Camera-local credentials** for the short Baichuan device session. On a
  legacy Argus 2E the username normally defaults to `admin`, and its password
  is not the Reolink account password.

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

## License

This project is licensed under the [MIT License](LICENSE). It depends on the
MIT-licensed `reolink-aio` package and does not include proprietary Reolink
binaries, SDK files, captured traffic, or recordings.

This project is not affiliated with or endorsed by Reolink.
