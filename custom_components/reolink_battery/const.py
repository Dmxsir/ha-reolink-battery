"""Constants for the Reolink Battery integration."""

from datetime import timedelta

DOMAIN = "reolink_battery"
MANUFACTURER = "Reolink"

CONF_ACCOUNT_EMAIL = "account_email"
CONF_ACCOUNT_PASSWORD = "account_password"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_MFA_TRUST_TOKEN = "mfa_trust_token"
CONF_USER_ID = "user_id"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
CONF_UID = "uid"
CONF_DEVICE_NAME = "device_name"
CONF_MODEL = "model"
CONF_AUTH_PATH = "auth_path"
CONF_DEVICE_USERNAME = "device_username"
CONF_DEVICE_PASSWORD = "device_password"
CONF_INTERFACE = "interface"

AUTH_PATH_LEGACY = "legacy_local_credential"

DEFAULT_DEVICE_USERNAME = "admin"
DEFAULT_POLL_INTERVAL = timedelta(seconds=15)
DEFAULT_EVENT_WINDOW = timedelta(minutes=2)
MAX_PROCESSED_EVENT_IDS = 512
MAX_PENDING_EVENTS = 100

MESSAGE_CENTER_PATH = "/v2/message-center/devices/msgs/query"
MEANINGFUL_ALARM_TYPES = frozenset({"AI", "MD", "MOTION", "PEOPLE", "PERSON"})

STORAGE_VERSION = 1
