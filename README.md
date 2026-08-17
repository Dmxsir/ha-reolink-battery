<a id="top"></a>

# Reolink Battery Camera for Home Assistant

<div align="center">

[![Release](https://img.shields.io/github/v/release/Dmxsir/ha-reolink-battery?style=for-the-badge&color=2ea44f&logo=github&label=release)](https://github.com/Dmxsir/ha-reolink-battery/releases/latest)
[![Diagnostic Beta](https://img.shields.io/badge/diagnostic-v0.1.2--beta.45-orange?style=for-the-badge&logo=github)](https://github.com/Dmxsir/ha-reolink-battery/releases/tag/v0.1.2-beta.45)
[![Validate](https://img.shields.io/github/actions/workflow/status/Dmxsir/ha-reolink-battery/validate.yaml?branch=main&style=for-the-badge&logo=githubactions&label=validate)](https://github.com/Dmxsir/ha-reolink-battery/actions/workflows/validate.yaml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=home-assistant)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Dmxsir&repository=ha-reolink-battery&category=integration)
[![License](https://img.shields.io/github/license/Dmxsir/ha-reolink-battery?style=for-the-badge&color=blue)](LICENSE)
[![Open Issues](https://img.shields.io/github/issues/Dmxsir/ha-reolink-battery?style=for-the-badge&color=red&logo=github)](https://github.com/Dmxsir/ha-reolink-battery/issues)
[![Stars](https://img.shields.io/github/stars/Dmxsir/ha-reolink-battery?style=for-the-badge&color=yellow&logo=github)](https://github.com/Dmxsir/ha-reolink-battery/stargazers)
[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20the%20project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/dmxsir)

<a href="https://ko-fi.com/dmxsir">
  <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/buymecoffe.png" alt="Buy me a coffee — support the project on Ko-fi" width="450">
</a>


**English** | [עברית](#hebrew)

</div>

A Home Assistant custom integration for battery-powered Reolink cameras that
should not be treated like permanently online RTSP or ONVIF cameras.

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

The current stable release is **v1.2.1**. It keeps the validated
**v0.1.2-beta.45 / v1.2.0 transport baseline unchanged** and adds persistent
recording-level deduplication. If multiple Android notifications map to the same
SD-card recording, the already-completed recording fingerprint is detected after
FileInfo lookup and before cmd13/cmd8, so only one
`reolink_battery_recording_ready` event is published. **v0.1.2-beta.45** remains
available as the diagnostic transport reference.

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

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/Companion%20App.jpg" alt="Home Assistant Companion App - Last Notification sensor" width="560">
    </td>
  </tr>
</table>

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

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-37-42.png" alt="Reolink account sign-in" width="560">
    </td>
  </tr>
</table>

#### 2. Select the camera

Select the battery-powered Reolink camera that you want to add to Home Assistant,
then press **Submit**.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-40-18.png" alt="Select a Reolink battery camera" width="560">
    </td>
  </tr>
</table>

#### 3. Enter the camera-local credentials

Enter the camera's own local administrator credentials. On legacy Argus cameras,
the username is normally `admin`. The **camera-local password is the device
password configured for the camera and is not your Reolink account password**.

Select the physical LAN interface that can reach the camera directly. Do not
select Tailscale, VPN, or other `/32` virtual interfaces.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-40-40.png" alt="Camera-local credentials and physical LAN interface" width="560">
    </td>
  </tr>
</table>

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

---

<a id="hebrew"></a>

<div dir="rtl" align="right">

## עברית — מצלמת Reolink מבוססת סוללה ל־Home Assistant

אינטגרציה מותאמת אישית ל־Home Assistant עבור מצלמות Reolink מבוססות סוללה, שאינן מיועדות להתנהג כמו מצלמות RTSP או ONVIF המחוברות באופן קבוע לרשת.

המצלמה העיקרית שנבדקה היא **Reolink Argus 2E**. תהליך העבודה המתוכנן הוא:

<div dir="ltr" align="left">

```text
אירוע תנועה
-> התראת Push מאפליקציית Reolink באנדרואיד
-> חיישן Last Notification של Home Assistant Companion
-> תור אירועים מתמשך
-> הערת המצלמה בצורה חסכונית בסוללה
-> איתור ההקלטה המתאימה בכרטיס ה-SD
-> הורדת MP4 מאומתת
-> אירוע של Home Assistant
-> אוטומציה אופציונלית (לדוגמה Telegram)
```

</div>

## מצב הפיתוח

הגרסה היציבה הנוכחית היא **v1.2.1**. היא משאירה ללא שינוי את בסיס ה־transport המאומת של **v0.1.2-beta.45 / v1.2.0**, ומוסיפה מנגנון deduplication מתמשך ברמת ההקלטה. אם כמה התראות Android ממופות לאותה הקלטה בכרטיס ה־SD, האינטגרציה מזהה fingerprint של הקלטה שכבר הושלמה לאחר FileInfo ולפני cmd13/cmd8, ולכן נורה רק אירוע `reolink_battery_recording_ready` אחד. גרסת **v0.1.2-beta.45** נשמרת כרפרנס אבחוני ל־transport.

מנגנון ההורדה האוטומטי פעיל: התראת Reolink תואמת שמתקבלת באנדרואיד נכנסת לתור, ה־worker ממתין שהמצלמה תסיים את ההקלטה, פותח חיבור Baichuan מקומי קצר, מוריד את ההקלטה המתאימה מכרטיס ה־SD, מאמת את קובץ ה־MP4 בדיסק ולאחר מכן יורה את אירוע Home Assistant בשם `reolink_battery_recording_ready`.

האינטגרציה נשארת ממוקדת במצלמות מבוססות סוללה. היא אינה מחזיקה חיבור קבוע למצלמה ואינה משתמשת ב־RTSP, ONVIF, polling לתצוגה מקדימה או streaming רציף.

## דרישות

<ul dir="rtl" align="right">
  <li><strong>Home Assistant 2026.8.1 ומעלה</strong>.</li>
  <li>מצלמת Reolink מבוססת סוללה נתמכת, עם הקלטה פעילה לכרטיס SD.</li>
  <li><strong>פרטי מנהל מקומיים של המצלמה</strong>. במצלמות Argus ישנות שם המשתמש הוא בדרך כלל <code>admin</code>; סיסמת ההתקן המקומית נפרדת מסיסמת חשבון Reolink.</li>
  <li>ממשק LAN פיזי מסוג IPv4 במחשב שעליו רץ Home Assistant, שיכול להגיע ישירות למצלמה. אין לבחור Tailscale, VPN או ממשק וירטואלי אחר מסוג <code>/32</code>.</li>
  <li>לעיבוד אוטומטי של תנועה והקלטות נדרש <strong>טלפון Android</strong> שעליו מותקנות האפליקציה הרשמית של <strong>Reolink</strong> והאפליקציה הרשמית <strong>Home Assistant Companion App</strong>. מנגנון ההתראות הנוכחי מיועד לאנדרואיד.</li>
</ul>

### הגדרת התראות באפליקציית Reolink

האינטגרציה משתמשת בהתראת ה־Push של Reolink שמתקבלת בטלפון Android כטריגר לתנועה. בטלפון:

1. אפשר התראות Android עבור **Reolink App**.
2. באפליקציית Reolink, פתח את הגדרות המצלמה והפעל **Push Notifications**.
3. הגדר את לוח הזמנים וסוגי הזיהוי כך שאירועי התנועה הרצויים יפיקו התראת Push.
4. השתמש בבדיקת ההתראות של Reolink, או צור אירוע תנועה אמיתי, וודא שהטלפון מקבל את ההתראה מהמצלמה.

הוראות רשמיות של Reolink:
https://support.reolink.com/articles/360004189214-How-to-Enable-Push-Notifications-on-iOS-and-Android-Phones/

### חיישן ההתראות של Home Assistant Companion App

באותו טלפון Android:

1. פתח **Home Assistant App -> Settings -> Companion App -> Manage Sensors**.
2. מצא והפעל את **Last Notification**.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/Companion%20App.jpg" alt="חיישן Last Notification באפליקציית Home Assistant Companion" width="560">
    </td>
  </tr>
</table>

3. הענק ל־Home Assistant את הרשאת **Notification Access** המבוקשת.
4. פתח את הגדרות חיישן Last Notification והגדר את **Allow List** כך שתכלול את אפליקציית Reolink (`com.mcu.reolink`). מומלץ להשתמש ב־Allow List ולא לבטל את הדרישה לרשימה זו.
5. לאחר הפעלת החיישן, גרום להתראת Reolink אחת להתקבל. חיישן התראות המשתמש ב־Allow List עשוי שלא להופיע כ־entity ב־Home Assistant עד שאפליקציה מאושרת מפרסמת התראה.
6. ב־Home Assistant עבור אל **Settings -> Devices & services -> Reolink Battery -> Configure** ובחר את חיישן **Last Notification** של הטלפון.

תיעוד חיישני Home Assistant Companion:
https://companion.home-assistant.io/docs/core/sensors/#notification-sensors

## התקנה ידנית

1. הורד את ארכיון הגרסה הרצויה מעמוד **Releases** ב־GitHub.
2. חלץ את הארכיון.
3. העתק את התיקייה:

   ```text
   custom_components/reolink_battery
   ```

   אל תיקיית ההגדרות של Home Assistant כך שהנתיב הסופי יהיה:

   ```text
   /config/custom_components/reolink_battery
   ```

4. אם `/config/custom_components` אינה קיימת, צור אותה תחילה.
5. הפעל מחדש את Home Assistant.
6. עבור אל **Settings -> Devices & services -> Add integration**.
7. חפש **Reolink Battery** והשלם את תהליך ההגדרה המתואר בהמשך.

## התקנה דרך HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Dmxsir&repository=ha-reolink-battery&category=integration)

1. פתח את **HACS** ב־Home Assistant.
2. בחר **Integrations**.
3. פתח את התפריט ובחר **Custom repositories**.
4. הוסף את `https://github.com/Dmxsir/ha-reolink-battery` בקטגוריה **Integration**.
5. פתח את **Reolink Battery Camera** ובחר **Download**.
6. הפעל מחדש את Home Assistant.
7. עבור אל **Settings -> Devices & services -> Add integration**.
8. חפש **Reolink Battery**.

## הגדרה

תהליך ההגדרה מבקש שתי שכבות נפרדות של פרטי התחברות:

<ul dir="rtl" align="right">
  <li><strong>פרטי חשבון Reolink</strong> עבור כניסה לחשבון, MFA אופציונלי בדוא"ל, גילוי המצלמות ושליפת אירועי ענן.</li>
  <li><strong>פרטי התחברות מקומיים למצלמה</strong> עבור חיבור Baichuan קצר למצלמה. ב־Argus 2E ישנה שם המשתמש הוא בדרך כלל <code>admin</code>, והסיסמה אינה סיסמת חשבון Reolink.</li>
</ul>

### הגדרה שלב אחר שלב

#### 1. התחברות לחשבון Reolink

הזן את כתובת הדוא"ל והסיסמה של חשבון Reolink. שלב זה משמש לגילוי מצלמות הסוללה המקושרות לחשבון ואינו יוצר קשר עם המצלמה עצמה ואינו מעיר אותה.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-37-42.png" alt="התחברות לחשבון Reolink" width="560">
    </td>
  </tr>
</table>

#### 2. בחירת המצלמה

בחר את מצלמת Reolink מבוססת הסוללה שברצונך להוסיף ל־Home Assistant ולאחר מכן לחץ **Submit**.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-40-18.png" alt="בחירת מצלמת Reolink מבוססת סוללה" width="560">
    </td>
  </tr>
</table>

#### 3. הזנת פרטי ההתחברות המקומיים למצלמה

הזן את פרטי המנהל המקומיים של המצלמה. במצלמות Argus ישנות שם המשתמש הוא בדרך כלל `admin`. **סיסמת המצלמה המקומית היא סיסמת ההתקן שהוגדרה במצלמה ואינה סיסמת חשבון Reolink**.

בחר את ממשק ה־LAN הפיזי שיכול להגיע ישירות למצלמה. אין לבחור Tailscale, VPN או ממשק וירטואלי אחר מסוג `/32`.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/IMAGE/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-40-40.png" alt="פרטי התחברות מקומיים למצלמה וממשק LAN פיזי" width="560">
    </td>
  </tr>
</table>

#### 4. בחירת חיישן ההתראות של Companion App

לאחר הוספת האינטגרציה, פתח:

**Settings -> Devices & services -> Reolink Battery -> Configure**

בחר את חיישן **Last Notification** של טלפון ה־Android שהכנת בסעיף הדרישות. ה־worker יאזין ל־entity הזה ללא polling לטלפון או למצלמה.

האינטגרציה בוחרת ממשק LAN פיזי פרטי מסוג IPv4 ודוחה ממשקי VPN/Tailscale מסוג `/32`. בדיקת ההגדרה מאתרת ומעירה את המצלמה שנבחרה, מבצעת אימות ומתנתקת מיד.

האינטגרציה אינה מפעילה ואינה משתמשת ב־RTSP, ONVIF, HTTP fallback, preview, live view, polling לתמונות snapshot או חיבור קבוע למצלמה. בדיקת אירועי הענן אינה יוצרת קשר עם המצלמה ואינה מעירה אותה.

## דוגמאות לאוטומציות

לאחר שההקלטה הורדה במלואה ואומתה, האינטגרציה יורה את האירוע:

<div dir="ltr" align="left">

```text
reolink_battery_recording_ready
```

</div>

נתוני האירוע השימושיים כוללים את `device_name`, `alarm_time`, `file_path`, `media_content_id` ו־`file_size`.

### שליחת ההקלטה ל־Telegram

החלף את ישות ההתראה של Telegram בישות שלך.

<div dir="ltr" align="left">

```yaml
alias: Reolink - שליחת הקלטת תנועה לטלגרם
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
      caption: "🎥 תנועה זוהתה במצלמה {{ trigger.event.data.device_name }}"
mode: queued
max: 10
```

</div>

### שליחה ל־Telegram ומחיקת העותק המקומי לאחר מכן

אם Telegram משמש אותך לאחסון לטווח ארוך ואינך רוצה שהקלטות שהורדו יצטברו על שרת Home Assistant, הוסף את `shell_command` הבא אל `configuration.yaml`:

<div dir="ltr" align="left">

```yaml
shell_command:
  delete_reolink_recording: 'rm -f -- "{{ file_path }}"'
```

</div>

לאחר מכן השתמש באוטומציה כגון:

<div dir="ltr" align="left">

```yaml
alias: Reolink - שליחת הקלטה לטלגרם וניקוי מקומי
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
      caption: "🎥 תנועה זוהתה במצלמה {{ trigger.event.data.device_name }}"

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

</div>

בדיקת הנתיב מגבילה בכוונה את המחיקה לקובצי MP4 בתוך תיקיית ברירת המחדל `/media/reolink_battery/`. אם אתה משתמש בתיקיית media מותאמת אישית ב־Home Assistant, שנה את תחילת הנתיב בהתאם. לאחר הוספת `shell_command`, בצע Reload ל־**Shell Commands** או הפעל מחדש את Home Assistant.

## פרטיות ואבטחה

פרטי ההתחברות נשמרים באמצעות config entries של Home Assistant. סיסמאות, אסימוני חשבון, סודות session, חומר קריפטוגרפי ו־UID מלא של המצלמה אינם נכללים ב־diagnostics ובלוגים.

## תודות וקרדיטים

פרויקט זה נשען על עבודה חשובה של קהילות הקוד הפתוח של Home Assistant ושל חוקרי Reolink, ולא היה מתאפשר באותה צורה בלעדיהן.

<ul dir="rtl" align="right">
  <li><strong>@@PH0@@</strong> מאת <strong>starkillerOG והתורמים</strong> — ספריית Python מרכזית עבור Reolink API/Baichuan, שבה האינטגרציה משתמשת ישירות לתקשורת עם ההתקן. הפרויקט מופץ ברישיון MIT.</li>
  <li><strong>@@PH0@@</strong> מאת <strong>thirtythreeforty</strong>, יחד עם ה־fork המורחב <strong>@@PH1@@</strong> — מקור השוואה חשוב מאוד ל־reverse engineering של פרוטוקול Baichuan הקנייני של Reolink, התנהגות UDP/P2P, גילוי, חיבור ו־transport. Neolink מופץ ברישיון AGPL; פרויקט זה <strong>אינו</strong> משלב קוד מקור של Neolink ומשתמש בו רק כיישום ייחוס לפרוטוקול.</li>
  <li><strong>@@PH0@@</strong> מאת <strong>Legrandin והתורמים</strong> — מספק את הרכיבים הקריפטוגרפיים שבהם נעשה שימוש דרך התלות <code>pycryptodomex</code>.</li>
  <li><strong>@@PH0@@</strong> והתורמים — עבור מסגרת האינטגרציות, ממשקי config entry, event bus, תשתית media source, מוסכמות diagnostics והמערכת האקולוגית שעליה בנויה האינטגרציה.</li>
  <li><strong>Reolink</strong> — עבור החומרה וההתנהגות של התוכנה הרשמית ששימשו לבדיקות interoperability. ההתנהגות של האפליקציה/SDK הרשמיים שימשה כהשוואה התנהגותית בלבד; הפרויקט אינו מפיץ קבצי SDK קנייניים של Reolink, קבצים בינאריים, תעבורת רשת מוקלטת או הקלטות מצלמה.</li>
  <li><strong>OpenAI Codex ו־ChatGPT</strong> — שימשו ככלי עזר לפיתוח עבור code review, debugging, תכנון בדיקות, תמיכה בניתוח פרוטוקולים ותיעוד. התנהגות הפרוטוקול והתיקונים אומתו מול קוד המקור, בדיקות regression אוטומטיות וחומרת מצלמה אמיתית לפני שנשמרו בפרויקט.</li>
</ul>

תודה מיוחדת לכל מי שמפרסם מחקרי פרוטוקול, דיווחי באגים, תוצאות בדיקות וכלי קוד פתוח עבור התקני Reolink. העבודה הזו צמצמה משמעותית עבודה כפולה של reverse engineering.

## רישיון

הפרויקט מופץ תחת [רישיון MIT](LICENSE). הוא תלוי בחבילת `reolink-aio` המופצת גם היא ברישיון MIT ואינו כולל קבצים בינאריים קנייניים של Reolink, קובצי SDK, תעבורת רשת מוקלטת או הקלטות.

פרויקט זה אינו קשור ל־Reolink ואינו מאושר או נתמך על ידה.

<p align="center" dir="ltr"><a href="#top">↑ Back to English</a></p>

</div>
