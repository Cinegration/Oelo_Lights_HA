from __future__ import annotations
import logging
from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Dummy exceptions for validation errors.
class CannotConnect(Exception):
    """Exception raised when a connection to the device cannot be established."""
    pass

class InvalidAuth(Exception):
    """Exception raised when authentication fails."""
    pass

async def validate_input(hass, data):
    """
    Validate the user input for connecting to the device.

    In a real implementation, you would attempt to connect to your device (for example,
    via an HTTP request) using the provided IP address. Here we simply return a dummy title.
    """
    ip = data.get(CONF_IP_ADDRESS)
    if not ip:
        raise CannotConnect("No IP address provided.")
    # You can add more real validation logic here.
    return {"title": f"Oelo Lights"}

# Define the schema for user input.
STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_IP_ADDRESS, description={"suggested_value": "10.10.10.1"}): str,
})

class OeloLightsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Oelo Lights."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                # Validate the provided data.
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

            if not errors:
                # Validation succeeded; set a unique id and create the config entry.
                await self.async_set_unique_id(info.get("title"))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        # Show the form to the user.
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow reconfiguration of an existing config entry."""
        errors: dict[str, str] = {}
        config_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None:
            try:
                # Here we use the existing IP address from the config entry.
                user_input[CONF_IP_ADDRESS] = config_entry.data[CONF_IP_ADDRESS]
                await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    config_entry,
                    unique_id=config_entry.unique_id,
                    data={**config_entry.data, **user_input},
                    reason="reconfigure_successful",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_IP_ADDRESS): str}),
            errors=errors,
        )
