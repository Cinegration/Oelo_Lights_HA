"""Config flow for Oelo Lights."""

from __future__ import annotations
import logging
from typing import Any
import voluptuous as vol
import ipaddress
import asyncio  
import aiohttp  

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.helpers.aiohttp_client import async_get_clientsession 

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class CannotConnect(Exception):
    """Exception raised when a connection to the device cannot be established."""
    pass

class InvalidAuth(Exception):
    """Exception raised when authentication fails."""
    pass

class InvalidIP(Exception):
    """Exception raised for invalid IP format."""
    pass


async def validate_input(hass, data):
    """Validate user input allows us to connect."""

    ip = data.get(CONF_IP_ADDRESS)
    if not ip:
        raise InvalidIP("No IP address provided.")

    try:
        ipaddress.ip_address(ip)
    except ValueError:
        _LOGGER.debug("Invalid IP address format: %s", ip)
        raise InvalidIP("Invalid IP address format.")

    session = async_get_clientsession(hass)
    controller_url = f"http://{ip}/getController"

    try:
        _LOGGER.debug("Attempting to connect to Oelo controller at %s", controller_url)
        async with session.get(controller_url, timeout=10) as response:
            if response.status == 200:
                _LOGGER.debug("Successfully connected to Oelo controller at %s", ip)
                return {"title": "Oelo Lights"} 
            else:
                _LOGGER.warning(
                    "Failed to connect to Oelo controller at %s - HTTP Status: %s",
                    ip,
                    response.status,
                )
                raise CannotConnect(f"Controller responded with status {response.status}")

    except (aiohttp.ClientConnectorError, aiohttp.ClientError) as err:
        _LOGGER.warning(
            "Failed to connect to Oelo controller at %s: %s", ip, err
        )
        raise CannotConnect(f"Could not connect to the controller at {ip}. Check IP address and ensure device is online.")
    except asyncio.TimeoutError:
        _LOGGER.warning("Timeout connecting to Oelo controller at %s", ip)
        raise CannotConnect(f"Connection to the controller at {ip} timed out.")
    except Exception as exc:
        _LOGGER.exception("Unexpected error validating Oelo controller at %s: %s", ip, exc)
        raise 


STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_IP_ADDRESS): str, 
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
                info = await validate_input(self.hass, user_input)
                await self.async_set_unique_id(user_input[CONF_IP_ADDRESS], raise_on_progress=False)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(title=info["title"], data=user_input)

            except InvalidIP:
                errors["base"] = "invalid_ip"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception: 
                _LOGGER.exception("Unexpected exception during user step")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"error_message": errors.get("base", "")} 
        )


    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
        ) -> ConfigFlowResult:
        """Allow reconfiguration of an existing config entry."""
        errors: dict[str, str] = {}
        config_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None:

            current_data = {**config_entry.data, **user_input}
            try:
                await validate_input(self.hass, current_data)

                if config_entry.data.get(CONF_IP_ADDRESS) != user_input.get(CONF_IP_ADDRESS):
                     _LOGGER.debug("Oelo controller IP changed from %s to %s",
                                   config_entry.data.get(CONF_IP_ADDRESS),
                                   user_input.get(CONF_IP_ADDRESS))
            
                     if self._async_current_entries(): 
                         existing_entry = await self.async_set_unique_id(user_input[CONF_IP_ADDRESS], raise_on_progress=False)
                         if existing_entry and existing_entry.entry_id != config_entry.entry_id:
                              errors["base"] = "reconfigure_failed_duplicate_ip"
                              return self.async_show_form(
                                  step_id="reconfigure",
                                  data_schema=vol.Schema({vol.Required(CONF_IP_ADDRESS, default=user_input.get(CONF_IP_ADDRESS)): str}),
                                  errors=errors,
                                  description_placeholders={"ip_address": user_input.get(CONF_IP_ADDRESS)}
                              )

                     return self.async_update_reload_and_abort(
                         config_entry,
                         unique_id=user_input.get(CONF_IP_ADDRESS), 
                         data=current_data,
                         reason="reconfigure_successful",
                     )
                else:
                     _LOGGER.debug("Oelo controller IP address unchanged during reconfigure.")
                     return self.async_abort(reason="reconfigure_successful")


            except InvalidIP:
                errors["base"] = "invalid_ip"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during reconfigure step")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_IP_ADDRESS, default=config_entry.data.get(CONF_IP_ADDRESS)): str}),
            errors=errors,
        )