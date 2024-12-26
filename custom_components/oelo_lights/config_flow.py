import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

# Define your domain, which should match the domain in your manifest.json
from .const import DOMAIN

# Schema for the config flow (to request the IP address from the user)
DATA_SCHEMA = vol.Schema({
    vol.Required("ip_address"): str,
})

class CustomLightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the custom light integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial step where the user configures the light."""
        errors = {}
        
        if user_input is not None:
            ip_address = user_input["ip_address"]
            # You might want to add some validation for the IP address here
            # if the validation fails, add errors like errors["ip_address"] = "invalid_ip"

            return self.async_create_entry(title="Custom Light", data=user_input)

        # Display the form to the user
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)
