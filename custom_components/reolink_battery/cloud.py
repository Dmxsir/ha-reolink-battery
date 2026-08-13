"""Minimal asynchronous Reolink account and Message Center client."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientError, ClientSession
from Cryptodome.Cipher import AES

from .const import MESSAGE_CENTER_PATH

API_BASE = "https://apis.reolink.com"
CLIENT_ID = "REO-aPOx]dxqdnaWBChRZprp"
USER_AGENT = "ReolinkAndroid App/4.61.0.3.20260721 (reolink-battery; Android/REL)"
TOKEN_PATH = "/v1.0/oauth2/token/"
MAX_RESPONSE_BYTES = 1_000_000


class CloudError(RuntimeError):
    """A sanitized cloud failure that never contains response content."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        code: int | None = None,
        symbol: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.symbol = symbol


class MfaRequiredError(CloudError):
    """The account requires MFA."""


class CloudEventDecodeError(CloudError):
    """The Message Center response needs an unsupported decoder."""


@dataclass(frozen=True, slots=True)
class CloudTokens:
    """Reolink account session values."""

    access_token: str
    refresh_token: str = ""
    mfa_trust_token: str = ""
    user_id: str = ""
    expires_at: float = 0.0


@dataclass(frozen=True, slots=True)
class CloudDevice:
    """Safe device metadata returned by account discovery."""

    uid: str
    title: str
    model: str
    passwordless: bool
    protocols: tuple[int, ...]
    status: str = ""
    activation_present: bool = False

    @property
    def auth_path(self) -> str:
        """Classify the official device authentication path."""
        cloud_capable = (
            self.passwordless and 3 in self.protocols
        ) or self.activation_present
        if cloud_capable and self.status.casefold() in {"bound", "shared"}:
            return "cloud_signature_v3"
        if not cloud_capable and self.status.casefold() == "bound":
            return "legacy_local_credential"
        return "unavailable"


@dataclass(frozen=True, slots=True)
class DecodedMessagePayload:
    """Decoded Message Center response and its observed transport wrapper."""

    payload: object
    wrapped: bool


@dataclass(frozen=True, slots=True)
class CloudEventPage:
    """One bounded Message Center page with secret-safe telemetry."""

    payload: object
    http_status: int
    wrapped: bool
    item_count: int
    next_token_present: bool


@dataclass(frozen=True, slots=True)
class _CloudHttpResponse:
    payload: object
    status: int


def _nested(payload: object, *keys: str) -> Any:
    """Find the first named value in a shallow response/data envelope."""
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload:
            return payload[key]
    data = payload.get("data")
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data[key]
    return None


def tokens_from_payload(payload: object) -> CloudTokens:
    """Extract tokens without retaining unrelated account data."""
    access = _nested(payload, "access_token", "accessToken")
    if not isinstance(access, str) or not access:
        raise CloudError("Reolink cloud response did not contain an access token")
    refresh = _nested(payload, "refresh_token", "refreshToken")
    trust = _nested(payload, "mfa_trust_token", "trust_token", "mfaTrustToken")
    user = _nested(payload, "userId", "user_id")
    if user is None:
        user_data = _nested(payload, "user")
        if isinstance(user_data, dict):
            user = user_data.get("id")
    expires_in = _nested(payload, "expires_in", "expiresIn")
    expires_absolute = _nested(payload, "expires_at", "expiresAt")
    expires_at = 0.0
    if isinstance(expires_absolute, (int, float)) and expires_absolute > 0:
        expires_at = float(expires_absolute)
        if expires_at > 10_000_000_000:
            expires_at /= 1000
    elif isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = datetime.now(UTC).timestamp() + float(expires_in)
    return CloudTokens(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) else "",
        mfa_trust_token=trust if isinstance(trust, str) else "",
        user_id=str(user) if user is not None else "",
        expires_at=expires_at,
    )


def devices_from_payload(payload: object) -> list[CloudDevice]:
    """Parse only the device fields required by the integration."""
    items = _nested(payload, "items")
    if not isinstance(items, list):
        raise CloudError("Reolink device list response did not contain items")
    devices: list[CloudDevice] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("uid"), str):
            continue
        model_data = item.get("model")
        model = ""
        if isinstance(model_data, dict):
            model = str(model_data.get("title") or model_data.get("key") or "")
        elif isinstance(model_data, str):
            model = model_data
        passwordless = item.get("passwordless")
        protocols = (
            passwordless.get("protocols", [])
            if isinstance(passwordless, dict)
            else []
        )
        devices.append(
            CloudDevice(
                uid=item["uid"],
                title=str(item.get("title") or item.get("deviceName") or ""),
                model=model,
                passwordless=bool(
                    isinstance(passwordless, dict) and passwordless.get("enabled")
                ),
                protocols=tuple(value for value in protocols if isinstance(value, int)),
                status=str(item.get("status") or ""),
                activation_present=isinstance(item.get("activation"), dict),
            )
        )
    return devices


def decode_message_center_payload(
    payload: object, user_id: str = ""
) -> DecodedMessagePayload:
    """Decode the official Message Center STM v1 envelope or plaintext JSON."""
    is_stm_v1 = isinstance(payload, dict) and (
        payload.get("stm") == 1
        or (
            "stm" not in payload
            and isinstance(payload.get("time"), (str, int))
            and isinstance(payload.get("data"), str)
        )
    )
    if is_stm_v1:
        timestamp = payload.get("time")
        encoded = payload.get("data")
        if not isinstance(timestamp, (str, int)) or not isinstance(encoded, str):
            raise CloudEventDecodeError("Message Center returned malformed STM v1")
        try:
            digest = hmac.new(
                str(timestamp).encode(),
                (user_id or "REOLINK_GUEST").encode(),
                hashlib.sha256,
            ).digest()
            ciphertext = base64.b64decode(encoded, validate=True)
            plaintext = AES.new(
                digest[:16],
                AES.MODE_CFB,
                iv=digest[16:],
                segment_size=128,
            ).decrypt(ciphertext)
            decoded = json.loads(plaintext.decode())
        except (
            binascii.Error,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise CloudEventDecodeError(
                "Message Center returned invalid STM v1"
            ) from exc
        if not isinstance(decoded, (dict, list)):
            raise CloudEventDecodeError("Message Center STM v1 was not a document")
        return DecodedMessagePayload(decoded, True)

    if isinstance(payload, dict) and "stm" in payload:
        raise CloudEventDecodeError("Message Center returned unsupported STM version")
    if isinstance(payload, (dict, list)):
        value = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(value, (dict, list)):
            return DecodedMessagePayload(value, False)
        if isinstance(payload, dict) and any(
            key in payload for key in ("items", "messages", "msgs", "list")
        ):
            return DecodedMessagePayload(payload, False)
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise CloudEventDecodeError(
                "Message Center returned unsupported plaintext"
            ) from exc
        if isinstance(decoded, (dict, list)):
            return DecodedMessagePayload(decoded, False)
    raise CloudEventDecodeError("Message Center returned an unsupported payload")


def decoded_message_payload(payload: object, user_id: str = "") -> object:
    """Compatibility helper returning only the decoded document."""
    return decode_message_center_payload(payload, user_id).payload


def _message_page_metadata(payload: object) -> tuple[int, bool]:
    value = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(value, (dict, list)):
        value = payload
    if isinstance(value, list):
        return len(value), False
    if not isinstance(value, dict):
        return 0, False
    items = next(
        (
            candidate
            for key in ("items", "messages", "msgs", "list")
            if isinstance(candidate := value.get(key), list)
        ),
        [],
    )
    token = value.get("nextToken")
    return len(items), isinstance(token, str) and bool(token)


class ReolinkCloudClient:
    """Small aiohttp client; it never contacts the camera."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._headers = {
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
            "X-Api-Challenge-Accept": "pow/1,captcha/1",
            "X-Client-Id": CLIENT_ID,
        }

    async def _request_json_response(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        form: dict[str, str] | None = None,
        access_token: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> _CloudHttpResponse:
        headers = dict(self._headers)
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if extra_headers:
            headers.update(extra_headers)
        try:
            async with asyncio.timeout(30):
                async with self._session.request(
                    method,
                    API_BASE + path,
                    headers=headers,
                    json=json_body,
                    data=form,
                ) as response:
                    raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                    status = response.status
                    content_type = response.headers.get("Content-Type", "")
        except (ClientError, TimeoutError) as exc:
            raise CloudError(
                f"Reolink cloud connection failed ({type(exc).__name__})"
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CloudError(
                f"Reolink cloud returned HTTP {status} with an oversized response",
                status=status,
            )
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            media_type = content_type.split(";", 1)[0] or "unknown"
            raise CloudError(
                f"Reolink cloud returned HTTP {status} with non-JSON content "
                f"({media_type}, {len(raw)} bytes)",
                status=status,
            ) from exc
        self._raise_error(status, payload)
        return _CloudHttpResponse(payload, status)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        form: dict[str, str] | None = None,
        access_token: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> object:
        return (
            await self._request_json_response(
                method,
                path,
                json_body=json_body,
                form=form,
                access_token=access_token,
                extra_headers=extra_headers,
            )
        ).payload

    @staticmethod
    def _raise_error(status: int, payload: object) -> None:
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            if status < 400:
                return
            raise CloudError(f"Reolink cloud returned HTTP {status}", status=status)
        code = error.get("code")
        symbol = error.get("symbol")
        error_type = (
            MfaRequiredError
            if code == 8208 or symbol == "mfa_required"
            else CloudError
        )
        raise error_type(
            f"Reolink cloud authentication failed (HTTP {status})",
            status=status,
            code=code if isinstance(code, int) else None,
            symbol=symbol if isinstance(symbol, str) else "",
        )

    async def async_password_grant(
        self, email: str, password: str, trust_token: str = ""
    ) -> CloudTokens:
        form = {
            "client_id": CLIENT_ID,
            "grant_type": "password",
            "username": email,
            "password": password,
            "session_mode": "true",
        }
        if trust_token:
            form.update(mfa_trusted="true", mfa_trust_token=trust_token)
        return tokens_from_payload(
            await self._request_json(
                "POST",
                TOKEN_PATH,
                form=form,
                extra_headers={"X-Verify-Scenario": "users.login_with_password"},
            )
        )

    async def async_send_email_mfa_code(self, email: str) -> str:
        payload = await self._request_json(
            "POST",
            "/v2/auth/mfa/codes",
            json_body={
                "clientId": CLIENT_ID,
                "scenario": "users.login_with_password",
                "method": "email",
                "data": {"emailAddress": email},
            },
        )
        code_id = _nested(payload, "id")
        if not isinstance(code_id, str) or not code_id:
            raise CloudError("Reolink cloud did not return an MFA code id")
        return code_id

    async def async_exchange_mfa_code(
        self, code_id: str, code: str
    ) -> tuple[str, str]:
        payload = await self._request_json(
            "POST", "/v2/auth/mfa/sessions", json_body={"id": code_id, "code": code}
        )
        verify_id = _nested(payload, "id")
        verify_code = _nested(payload, "code")
        if not all(isinstance(value, str) and value for value in (verify_id, verify_code)):
            raise CloudError("Reolink cloud did not return an MFA verification session")
        return verify_id, verify_code

    async def async_verified_password_grant(
        self,
        email: str,
        password: str,
        verify_id: str,
        verify_code: str,
    ) -> CloudTokens:
        return tokens_from_payload(
            await self._request_json(
                "POST",
                TOKEN_PATH,
                form={
                    "client_id": CLIENT_ID,
                    "grant_type": "password",
                    "username": email,
                    "password": password,
                    "session_mode": "true",
                    "mfa_trusted": "true",
                },
                extra_headers={
                    "X-Verify-Scenario": "users.login_with_password",
                    "X-Verify-Id": verify_id,
                    "X-Verify-Code": verify_code,
                },
            )
        )

    async def async_refresh_session(self, tokens: CloudTokens) -> CloudTokens:
        if not tokens.refresh_token:
            return tokens
        refreshed = tokens_from_payload(
            await self._request_json(
                "POST",
                TOKEN_PATH,
                form={
                    "client_id": CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                    "grant_session_code": "true",
                },
            )
        )
        return CloudTokens(
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or tokens.refresh_token,
            mfa_trust_token=refreshed.mfa_trust_token or tokens.mfa_trust_token,
            user_id=refreshed.user_id or tokens.user_id,
            expires_at=refreshed.expires_at,
        )

    async def async_query_devices(self, access_token: str) -> list[CloudDevice]:
        payload = await self._request_json(
            "POST",
            "/v2/cloud/devices/items/list-query",
            json_body={
                "statuses": ["bound", "unbound", "shared"],
                "includes": [
                    "model",
                    "passwordless",
                    "simCard",
                    "cloudServices",
                    "activation",
                    "smartHome",
                ],
                "lang": "en",
            },
            access_token=access_token,
        )
        return devices_from_payload(payload)

    async def async_query_events(
        self,
        access_token: str,
        user_id: str,
        uid: str,
        start: datetime,
        end: datetime,
    ) -> CloudEventPage:
        """Fetch one recent Message Center page; nextToken is not a cursor."""
        response = await self._request_json_response(
            "POST",
            MESSAGE_CENTER_PATH,
            json_body={
                "timeRanges": [
                    {
                        "startAt": int(start.timestamp() * 1000),
                        "endAt": int(end.timestamp() * 1000),
                    }
                ],
                "uids": [f"{uid}_00"],
                "alarmTypes": [],
            },
            access_token=access_token,
        )
        decoded = decode_message_center_payload(response.payload, user_id)
        item_count, token_present = _message_page_metadata(decoded.payload)
        return CloudEventPage(
            decoded.payload,
            response.status,
            decoded.wrapped,
            item_count,
            token_present,
        )
