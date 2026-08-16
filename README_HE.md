<div dir="rtl" align="right">

# מצלמת Reolink מבוססת סוללה ל־Home Assistant

<div align="center" dir="ltr">

[![Release](https://img.shields.io/github/v/release/Dmxsir/ha-reolink-battery?style=for-the-badge&color=2ea44f&logo=github&label=release)](https://github.com/Dmxsir/ha-reolink-battery/releases/latest)
[![Beta](https://img.shields.io/github/v/release/Dmxsir/ha-reolink-battery?include_prereleases&style=for-the-badge&color=orange&logo=github&label=beta)](https://github.com/Dmxsir/ha-reolink-battery/releases)
[![Validate](https://img.shields.io/github/actions/workflow/status/Dmxsir/ha-reolink-battery/validate.yaml?branch=main&style=for-the-badge&logo=githubactions&label=validate)](https://github.com/Dmxsir/ha-reolink-battery/actions/workflows/validate.yaml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=home-assistant)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Dmxsir&repository=ha-reolink-battery&category=integration)
[![License](https://img.shields.io/github/license/Dmxsir/ha-reolink-battery?style=for-the-badge&color=blue)](LICENSE)
[![Open Issues](https://img.shields.io/github/issues/Dmxsir/ha-reolink-battery?style=for-the-badge&color=red&logo=github)](https://github.com/Dmxsir/ha-reolink-battery/issues)
[![Stars](https://img.shields.io/github/stars/Dmxsir/ha-reolink-battery?style=for-the-badge&color=yellow&logo=github)](https://github.com/Dmxsir/ha-reolink-battery/stargazers)
[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20the%20project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/dmxsir)

[English](README.md) | **עברית**

</div>

אינטגרציה ניסיונית מותאמת אישית ל־Home Assistant עבור מצלמות Reolink מבוססות סוללה, שאינן מיועדות להתנהג כמו מצלמות RTSP או ONVIF המחוברות באופן קבוע לרשת.

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

גרסת הבסיס הנבדקת כרגע היא **v0.1.2-beta.45**. הגרסה נשמרת ללא שינויים למשך תקופת soak test לפני קידומה לגרסה היציבה הבאה.

מנגנון ההורדה האוטומטי פעיל: התראת Reolink תואמת שמתקבלת באנדרואיד נכנסת לתור, ה־worker ממתין שהמצלמה תסיים את ההקלטה, פותח חיבור Baichuan מקומי קצר, מוריד את ההקלטה המתאימה מכרטיס ה־SD, מאמת את קובץ ה־MP4 בדיסק ולאחר מכן יורה את אירוע Home Assistant בשם `reolink_battery_recording_ready`.

האינטגרציה נשארת ממוקדת במצלמות מבוססות סוללה. היא אינה מחזיקה חיבור קבוע למצלמה ואינה משתמשת ב־RTSP, ONVIF, polling לתצוגה מקדימה או streaming רציף.

## דרישות

- **Home Assistant 2026.8.1 ומעלה**.
- מצלמת Reolink מבוססת סוללה נתמכת, עם הקלטה פעילה לכרטיס SD.
- **פרטי מנהל מקומיים של המצלמה**. במצלמות Argus ישנות שם המשתמש הוא בדרך כלל `admin`; סיסמת ההתקן המקומית נפרדת מסיסמת חשבון Reolink.
- ממשק LAN פיזי מסוג IPv4 במחשב שעליו רץ Home Assistant, שיכול להגיע ישירות למצלמה. אין לבחור Tailscale, VPN או ממשק וירטואלי אחר מסוג `/32`.
- לעיבוד אוטומטי של תנועה והקלטות נדרש **טלפון Android** שעליו מותקנות האפליקציה הרשמית של **Reolink** והאפליקציה הרשמית **Home Assistant Companion App**. מנגנון ההתראות הנוכחי מיועד לאנדרואיד.

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
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/Companion%20App.jpg" alt="חיישן Last Notification באפליקציית Home Assistant Companion" width="560">
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

- **פרטי חשבון Reolink** עבור כניסה לחשבון, MFA אופציונלי בדוא"ל, גילוי המצלמות ושליפת אירועי ענן.
- **פרטי התחברות מקומיים למצלמה** עבור חיבור Baichuan קצר למצלמה. ב־Argus 2E ישנה שם המשתמש הוא בדרך כלל `admin`, והסיסמה אינה סיסמת חשבון Reolink.

### הגדרה שלב אחר שלב

#### 1. התחברות לחשבון Reolink

הזן את כתובת הדוא"ל והסיסמה של חשבון Reolink. שלב זה משמש לגילוי מצלמות הסוללה המקושרות לחשבון ואינו יוצר קשר עם המצלמה עצמה ואינו מעיר אותה.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-37-42.png" alt="התחברות לחשבון Reolink" width="560">
    </td>
  </tr>
</table>

#### 2. בחירת המצלמה

בחר את מצלמת Reolink מבוססת הסוללה שברצונך להוסיף ל־Home Assistant ולאחר מכן לחץ **Submit**.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-40-18.png" alt="בחירת מצלמת Reolink מבוססת סוללה" width="560">
    </td>
  </tr>
</table>

#### 3. הזנת פרטי ההתחברות המקומיים למצלמה

הזן את פרטי המנהל המקומיים של המצלמה. במצלמות Argus ישנות שם המשתמש הוא בדרך כלל `admin`. **סיסמת המצלמה המקומית היא סיסמת ההתקן שהוגדרה במצלמה ואינה סיסמת חשבון Reolink**.

בחר את ממשק ה־LAN הפיזי שיכול להגיע ישירות למצלמה. אין לבחור Tailscale, VPN או ממשק וירטואלי אחר מסוג `/32`.

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Dmxsir/ha-reolink-battery/main/%D7%A6%D7%99%D7%9C%D7%95%D7%9D%20%D7%9E%D7%A1%D7%9A_2026-08-16_19-40-40.png" alt="פרטי התחברות מקומיים למצלמה וממשק LAN פיזי" width="560">
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

- **[reolink_aio](https://github.com/starkillerOG/reolink_aio)** מאת **starkillerOG והתורמים** — ספריית Python מרכזית עבור Reolink API/Baichuan, שבה האינטגרציה משתמשת ישירות לתקשורת עם ההתקן. הפרויקט מופץ ברישיון MIT.
- **[Neolink](https://github.com/thirtythreeforty/neolink)** מאת **thirtythreeforty**, יחד עם ה־fork המורחב **[QuantumEntangledAndy/neolink](https://github.com/QuantumEntangledAndy/neolink)** — מקור השוואה חשוב מאוד ל־reverse engineering של פרוטוקול Baichuan הקנייני של Reolink, התנהגות UDP/P2P, גילוי, חיבור ו־transport. Neolink מופץ ברישיון AGPL; פרויקט זה **אינו** משלב קוד מקור של Neolink ומשתמש בו רק כיישום ייחוס לפרוטוקול.
- **[PyCryptodome](https://github.com/Legrandin/pycryptodome)** מאת **Legrandin והתורמים** — מספק את הרכיבים הקריפטוגרפיים שבהם נעשה שימוש דרך התלות `pycryptodomex`.
- **[Home Assistant](https://github.com/home-assistant/core)** והתורמים — עבור מסגרת האינטגרציות, ממשקי config entry, event bus, תשתית media source, מוסכמות diagnostics והמערכת האקולוגית שעליה בנויה האינטגרציה.
- **Reolink** — עבור החומרה וההתנהגות של התוכנה הרשמית ששימשו לבדיקות interoperability. ההתנהגות של האפליקציה/SDK הרשמיים שימשה כהשוואה התנהגותית בלבד; הפרויקט אינו מפיץ קבצי SDK קנייניים של Reolink, קבצים בינאריים, תעבורת רשת מוקלטת או הקלטות מצלמה.
- **OpenAI Codex ו־ChatGPT** — שימשו ככלי עזר לפיתוח עבור code review, debugging, תכנון בדיקות, תמיכה בניתוח פרוטוקולים ותיעוד. התנהגות הפרוטוקול והתיקונים אומתו מול קוד המקור, בדיקות regression אוטומטיות וחומרת מצלמה אמיתית לפני שנשמרו בפרויקט.

תודה מיוחדת לכל מי שמפרסם מחקרי פרוטוקול, דיווחי באגים, תוצאות בדיקות וכלי קוד פתוח עבור התקני Reolink. העבודה הזו צמצמה משמעותית עבודה כפולה של reverse engineering.

## רישיון

הפרויקט מופץ תחת [רישיון MIT](LICENSE). הוא תלוי בחבילת `reolink-aio` המופצת גם היא ברישיון MIT ואינו כולל קבצים בינאריים קנייניים של Reolink, קובצי SDK, תעבורת רשת מוקלטת או הקלטות.

פרויקט זה אינו קשור ל־Reolink ואינו מאושר או נתמך על ידה.

</div>
