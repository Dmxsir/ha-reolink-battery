"""Config flow for Reolink Battery."""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import network
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from reolink_aio.exceptions import CredentialsInvalidError, ReolinkConnectionError

from .camera import async_validate_legacy_device
from .cloud import (
    CloudDevice,
    CloudError,
    CloudTokens,
    MfaRequiredError,
    ReolinkCloudClient,
)
from .const import (
    AUTH_PATH_LEGACY,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_EMAIL,
    CONF_ACCOUNT_PASSWORD,
    CONF_AUTH_PATH,
    CONF_DEVICE_NAME,
    CONF_DEVICE_PASSWORD,
    CONF_DEVICE_USERNAME,
    CONF_INTERFACE,
    CONF_LOCAL_STATE,
    CONF_MFA_TRUST_TOKEN,
    CONF_MODEL,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_UID,
    CONF_USER_ID,
    DEFAULT_DEVICE_USERNAME,
    DOMAIN,
)
from .device_status import local_state_as_dict
from .lan import lan_network_choices

_LOGGER = logging.getLogger(__name__)


class ReolinkBatteryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure one battery camera from a Reolink account."""

    VERSION = 1

    def __init__(self) -> None:
        self._email = ""
        self._account_password = ""
        self._tokens: CloudTokens | None = None
        self._mfa_code_id = ""
        self._devices: dict[str, CloudDevice] = {}
        self._selected: CloudDevice | None = None
        self._networks: dict[str, str] = {}

    def _cloud(self) -> ReolinkCloudClient:
        return ReolinkCloudClient(async_get_clientsession(self.hass))

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authenticate the Reolink account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._account_password = user_input[CONF_PASSWORD]
            try:
                self._tokens = await self._cloud().async_password_grant(
                    self._email, self._account_password
                )
            except MfaRequiredError:
                try:
                    self._mfa_code_id = await self._cloud().async_send_email_mfa_code(
                        self._email
                    )
                except CloudError as err:
                    _LOGGER.debug("MFA code request failed: %s", type(err).__name__)
                    errors["base"] = "cannot_connect"
                else:
                    return await self.async_step_mfa()
            except CloudError as err:
                _LOGGER.debug("Cloud login failed: %s", type(err).__name__)
                errors["base"] = (
                    "invalid_auth" if err.status in {401, 403} else "cannot_connect"
                )
            else:
                return await self.async_step_device()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify an emailed MFA code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                verify_id, verify_code = await self._cloud().async_exchange_mfa_code(
                    self._mfa_code_id, user_input["code"]
                )
                self._tokens = await self._cloud().async_verified_password_grant(
                    self._email,
                    self._account_password,
                    verify_id,
                    verify_code,
                )
            except CloudError as err:
                _LOGGER.debug("MFA verification failed: %s", type(err).__name__)
                errors["base"] = "invalid_auth"
            else:
                return await self.async_step_device()
        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover and select the account device."""
        errors: dict[str, str] = {}
        if not self._devices:
            try:
                assert self._tokens is not None
                devices = await self._cloud().async_query_devices(
                    self._tokens.access_token
                )
            except CloudError as err:
                _LOGGER.debug("Device discovery failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                self._devices = {
                    device.uid: device
                    for device in devices
                    if device.auth_path != "unavailable"
                }
                if not self._devices:
                    return self.async_abort(reason="no_devices")
        if user_input is not None:
            self._selected = self._devices[user_input[CONF_UID]]
            if self._selected.auth_path != AUTH_PATH_LEGACY:
                return self.async_abort(reason="unsupported_auth_path")
            await self.async_set_unique_id(self._selected.uid)
            self._abort_if_unique_id_configured()
            return await self.async_step_local()
        labels = {
            uid: f"{device.title or device.model or 'Reolink camera'} ({device.model})"
            for uid, device in self._devices.items()
        }
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({vol.Required(CONF_UID): vol.In(labels)}),
            errors=errors,
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate separate legacy camera credentials and selected LAN."""
        errors: dict[str, str] = {}
        if not self._networks:
            self._networks = lan_network_choices(
                await network.async_get_adapters(self.hass)
            )
            if not self._networks:
                return self.async_abort(reason="no_lan_interface")
        if user_input is not None:
            assert self._selected is not None
            try:
                validation = await async_validate_legacy_device(
                    self._selected.uid,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    ipaddress.ip_interface(user_input[CONF_INTERFACE]),
                )
            except CredentialsInvalidError as err:
                _LOGGER.debug("Local validation rejected credentials: %s", type(err).__name__)
                errors["base"] = "invalid_device_auth"
            except (ReolinkConnectionError, OSError, TimeoutError) as err:
                _LOGGER.debug("Local validation failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect_device"
            except Exception as err:  # noqa: BLE001 - recover to the config form.
                _LOGGER.debug("Unexpected local validation failure: %s", type(err).__name__)
                errors["base"] = "unknown"
            else:
                assert self._tokens is not None
                local_state = getattr(validation, "local_state", None)
                data = {
                    CONF_ACCOUNT_EMAIL: self._email,
                    CONF_ACCOUNT_PASSWORD: self._account_password,
                    CONF_ACCESS_TOKEN: self._tokens.access_token,
                    CONF_REFRESH_TOKEN: self._tokens.refresh_token,
                    CONF_MFA_TRUST_TOKEN: self._tokens.mfa_trust_token,
                    CONF_USER_ID: self._tokens.user_id,
                    CONF_TOKEN_EXPIRES_AT: self._tokens.expires_at,
                    CONF_UID: self._selected.uid,
                    CONF_DEVICE_NAME: self._selected.title,
                    CONF_MODEL: self._selected.model,
                    CONF_AUTH_PATH: self._selected.auth_path,
                    CONF_DEVICE_USERNAME: user_input[CONF_USERNAME],
                    CONF_DEVICE_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_INTERFACE: user_input[CONF_INTERFACE],
                }
                if local_state is not None:
                    data[CONF_LOCAL_STATE] = local_state_as_dict(local_state)
                return self.async_create_entry(
                    title=self._selected.title or self._selected.model or "Argus 2E",
                    data=data,
                )
        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=DEFAULT_DEVICE_USERNAME
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_INTERFACE): vol.In(self._networks),
                }
            ),
            errors=errors,
        )
