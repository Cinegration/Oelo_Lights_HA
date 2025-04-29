from __future__ import annotations

import logging
import asyncio
import voluptuous as vol
import aiohttp
import async_timeout
import re
import urllib.parse
import json 

try:
    from .patterns import pattern_commands
    from .const import DOMAIN
except ImportError:
    try:
        from patterns import pattern_commands 
        from const import DOMAIN
    except ImportError:
        _LOGGER = logging.getLogger(__name__)
        _LOGGER.warning("Could not import patterns or const relative to light.py. Patterns may not work.")
        pattern_commands = {} 
        DOMAIN = "oelo_lights" 


from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ATTR_EFFECT,
    ColorMode, PLATFORM_SCHEMA, LightEntity, LightEntityFeature
)

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
):
    """Set up the Oelo Lights platform from a config entry."""
    ip_address = entry.data[CONF_IP_ADDRESS]

    session = async_get_clientsession(hass)

    controller_info_url = f"http://{ip_address}/getController"
    try:
        async with async_timeout.timeout(5):
            async with session.get(controller_info_url) as response:
                response.raise_for_status() 
                await response.text()
                _LOGGER.info("Successfully connected to Oelo controller at %s (initial check OK)", ip_address)

    except (asyncio.TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.error(
            "Failed to connect to Oelo controller at %s during setup check: %s. Check IP and network.",
            ip_address, err
        )
        raise PlatformNotReady(f"Could not connect to Oelo controller at {ip_address}") from err

    light_entities = []

    for zone in range(1, 7):
        light_entity = OeloLight(session, ip_address, zone, entry)
        light_entities.append(light_entity)

    async_add_entities(light_entities)

class OeloLight(LightEntity, RestoreEntity):
    """Representation of an Oelo Light zone."""

    _attr_has_entity_name = True
    _attr_should_poll = True 

    def __init__(
        self,
        session: aiohttp.ClientSession,
        ip: str,
        zone: int,
        entry: ConfigEntry
    ):
        """Initialize the light."""
        self._session = session
        self._ip = ip
        self._zone = zone
        self._entry = entry
        self._state = False
        self._brightness = 255
        self._rgb_color = (255, 255, 255) 
        self._effect = None 
        self._last_successful_command = None 
        self._api_pattern_name = None 
        self._attr_name = f"Zone {self._zone}" 
        self._controller_chip_id = None 
        self._controller_fw_version = None

        self._attr_available = True 

        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB
        self._attr_supported_features = LightEntityFeature.EFFECT
        self._attr_unique_id = f"{self._entry.entry_id}_zone_{self._zone}"


    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the controller."""
        device_info = DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)}, 
            name=self._entry.title, 
            manufacturer="Oelo",
            model="Light Controller",
            configuration_url=f"http://{self._ip}/",
        )

        return device_info

    @property
    def available(self) -> bool:
        """Return True if the light is available."""
        return self._attr_available

    @property
    def is_on(self) -> bool:
        """Return the state of the light."""
        return self._state

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light."""
        return self._brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the RGB color of the light."""
        return self._rgb_color

    @property
    def effect(self) -> str | None:
        """Return the current effect's descriptive name."""
        return self._effect

    @property
    def effect_list(self) -> list[str]:
        """Return the list of supported effects (descriptive names)."""
        return list(pattern_commands.keys())

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return entity specific state attributes."""
        attrs = {}
        if self._last_successful_command:
            attrs["last_command"] = self._last_successful_command
        if self._api_pattern_name:
            attrs["api_pattern"] = self._api_pattern_name
        return attrs


    async def async_added_to_hass(self):
        """Handle entity being added to Home Assistant."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state:
            self._state = state.state == "on"
            self._brightness = state.attributes.get(ATTR_BRIGHTNESS, self._brightness)
            self._effect = state.attributes.get(ATTR_EFFECT, self._effect)
            rgb_color = state.attributes.get(ATTR_RGB_COLOR)
            if rgb_color is not None:
                self._rgb_color = tuple(rgb_color)

            self._last_successful_command = state.attributes.get("last_command")

            _LOGGER.debug("Restored state for Zone %d: state=%s, brightness=%s, effect=%s, rgb=%s, last_command=%s",
                        self._zone, self._state, self._brightness, self._effect, self._rgb_color, self._last_successful_command)
        else:
            _LOGGER.debug("No previous state found for Zone %d.", self._zone)


    async def async_update(self):
        """Fetch state from the device."""
        _LOGGER.debug("Updating state for Zone %d", self._zone)
        url = f"http://{self._ip}/getController"
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(url) as response:
                    response.raise_for_status()
                    controller_data = await response.json(content_type=None)

            zone_data = next((z for z in controller_data if z.get("num") == self._zone), None)

            if zone_data:
                self._update_state_from_api(zone_data)
                if not self._attr_available:
                    _LOGGER.info("Oelo Zone %d is now available", self._zone)
                self._attr_available = True 

            else:
                _LOGGER.warning("No data found for Zone %d in /getController response", self._zone)

        except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as err:
            if self._attr_available: 
                _LOGGER.error("Error updating Oelo Zone %d state: %s. Marking unavailable.", self._zone, err)
            else:
                _LOGGER.debug("Oelo Zone %d still unavailable during update: %s", self._zone, err)
            self._attr_available = False 
        except Exception as err:
            _LOGGER.exception("Unexpected error updating Oelo Zone %d: %s. Marking unavailable.", self._zone, err)
            self._attr_available = False


    def _update_state_from_api(self, zone_data: dict):

        if not zone_data.get("enabled", True):
            if self._attr_available:
                _LOGGER.info("Oelo Zone %d is disabled via controller config. Marking unavailable.", self._zone)
            self._attr_available = False 
            if self._state: self._state = False
            if self._effect is not None: self._effect = None
            if self._rgb_color != (0, 0, 0): self._rgb_color = (0, 0, 0)
            return 

        self._attr_name = zone_data.get("name", self._attr_name) 
        self._api_pattern_name = zone_data.get("pattern") 

        new_state = (self._api_pattern_name != "off")
        _LOGGER.debug("Zone %d API reports pattern '%s'. Determined state: %s",
                    self._zone, self._api_pattern_name, new_state)

        if self._state != new_state:
            _LOGGER.info("Updating Zone %d ON/OFF state via API: %s -> %s",
                        self._zone, self._state, new_state)
            self._state = new_state

        if not self._state:
            if self._effect is not None:
                _LOGGER.debug("Zone %d state is OFF, clearing HA effect '%s'", self._zone, self._effect)
                self._effect = None

            if self._rgb_color != (0, 0, 0):
                _LOGGER.debug("Zone %d state is OFF, setting HA RGB color to (0,0,0)", self._zone)
                self._rgb_color = (0, 0, 0)

        else: 
            colors_str = zone_data.get("colors")
            parsed_rgb_from_api = None 

            if isinstance(colors_str, str) and colors_str.strip(): 
                try:
                    values_str = [v.strip() for v in colors_str.split(',') if v.strip()]

                    if len(values_str) >= 3:
                        r = int(values_str[0])
                        g = int(values_str[1])
                        b = int(values_str[2])

                        r = max(0, min(r, 255))
                        g = max(0, min(g, 255))
                        b = max(0, min(b, 255))

                        parsed_rgb_from_api = (r, g, b)
                        _LOGGER.debug("Zone %d parsed first RGB from API 'colors' ('%s'): %s", self._zone, colors_str, parsed_rgb_from_api)
                    else:
                        _LOGGER.debug("Zone %d API 'colors' field ('%s') has < 3 values, cannot parse first RGB.", self._zone, colors_str)

                except (ValueError, IndexError):
                    _LOGGER.warning("Zone %d could not parse first RGB from API 'colors' field ('%s'). It might contain non-numeric values or be malformed.", self._zone, colors_str)
                except Exception as e:
                    _LOGGER.exception("Zone %d unexpected error parsing first RGB from API 'colors' field ('%s'): %s", self._zone, colors_str, e)
            else:
                _LOGGER.debug("Zone %d API 'colors' field is missing, empty, or not a string ('%s'). Cannot parse first RGB.", self._zone, colors_str)

            if parsed_rgb_from_api is not None:
                if self._rgb_color != parsed_rgb_from_api:
                    _LOGGER.info("Updating Zone %d HA RGB color based on API poll: %s -> %s",
                                self._zone, self._rgb_color, parsed_rgb_from_api)
                    self._rgb_color = parsed_rgb_from_api
            else:
                _LOGGER.debug("Zone %d keeping existing HA RGB color (%s) as API color parsing was unsuccessful or data unavailable.",
                            self._zone, self._rgb_color)

    async def async_turn_on(self, **kwargs):
        """Turn on the light with optional RGB color, brightness, and effect."""
        _LOGGER.debug("Turning on Zone %d with args: %s", self._zone, kwargs)

        requested_effect = kwargs.get(ATTR_EFFECT)
        requested_rgb = kwargs.get(ATTR_RGB_COLOR)
        requested_brightness = kwargs.get(ATTR_BRIGHTNESS)

        target_brightness = requested_brightness if requested_brightness is not None else self._brightness
        if target_brightness is None or target_brightness <= 0: 
            target_brightness = 255 
        brightness_factor = target_brightness / 255.0


        url = None
        final_url_params = {} 
        command_type = None
        intended_effect_name = self._effect 
        intended_rgb = None

        if requested_rgb:
            _LOGGER.debug("Zone %d: Setting explicit RGB: %s", self._zone, requested_rgb)
            command_type = "color"
            intended_effect_name = None 
            intended_rgb = requested_rgb
            scaled_color = tuple(max(0, min(int(c * brightness_factor), 255)) for c in intended_rgb)
            final_url_params = {
                "patternType": "custom", "num_zones": 1, "zones": self._zone,
                "num_colors": 1, "colors": ','.join(map(str, scaled_color)),
                "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
            }
            url = self._build_url(final_url_params)

        elif requested_effect:
            _LOGGER.debug("Zone %d: Setting explicit effect: %s", self._zone, requested_effect)
            command_type = "effect"
            if requested_effect in pattern_commands:
                intended_effect_name = requested_effect
                intended_rgb = None 
                pattern_command_template = pattern_commands[requested_effect]
                if "{zone}" in pattern_command_template:
                    base_command_url_str = pattern_command_template.format(zone=self._zone)
                else:
                    _LOGGER.warning("Pattern '%s' template does not contain {zone}. Applying zone override.", requested_effect)
                    parsed_template = urllib.parse.urlparse(pattern_command_template)
                    template_params = urllib.parse.parse_qs(parsed_template.query)
                    template_params['zones'] = [str(self._zone)]
                    template_params['num_zones'] = ['1']
                    new_query = urllib.parse.urlencode(template_params, doseq=True)
                    scheme = parsed_template.scheme or 'http'
                    netloc = parsed_template.netloc or self._ip
                    base_command_url_str = urllib.parse.urlunparse(
                        (scheme, netloc, parsed_template.path, '', new_query, '')
                    )
                url = self._adjust_colors_in_url(base_command_url_str, brightness_factor)
                if url:
                    parsed_final_url = urllib.parse.urlparse(url)
                    final_url_params = urllib.parse.parse_qs(parsed_final_url.query)
                    final_url_params = {k: v[0] for k, v in final_url_params.items()}

            else:
                _LOGGER.error("Invalid effect selected for Zone %d: %s. Ignoring.", self._zone, requested_effect)
                self.async_write_ha_state()
                return

        else:
            if self._state and requested_brightness is not None:
                command_type = "brightness_only"
                _LOGGER.debug("Zone %d: Adjusting brightness only.", self._zone)
            else:
                command_type = "last"
                _LOGGER.debug("Zone %d: Turning on - Attempting to re-apply last known command.", self._zone)

            can_reapply = self._last_successful_command and self._last_successful_command.get("type") != "off"

            if can_reapply:
                _LOGGER.debug("Zone %d: Re-applying last command (type: %s) with brightness factor %.2f",
                            self._zone, self._last_successful_command.get("type"), brightness_factor)
                last_cmd = self._last_successful_command
                last_cmd_type = last_cmd["type"]

                if last_cmd_type == "color":
                    intended_effect_name = None
                    intended_rgb = last_cmd.get("rgb", (255,255,255)) 
                    scaled_color = tuple(max(0, min(int(c * brightness_factor), 255)) for c in intended_rgb)

                    params_to_use = {
                        "patternType": "custom", "num_zones": 1, "zones": self._zone,
                        "num_colors": 1, "colors": ','.join(map(str, scaled_color)),
                        "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
                    }

                    if last_cmd.get("params"):
                        params_to_use["direction"] = last_cmd["params"].get("direction", "F")
                        params_to_use["speed"] = last_cmd["params"].get("speed", 0)
                    final_url_params = params_to_use
                    url = self._build_url(final_url_params)

                elif last_cmd_type == "effect":
                    intended_effect_name = last_cmd.get("name")
                    if not intended_effect_name:
                        _LOGGER.warning("Last command was 'effect' but name is missing. Defaulting to white.")
                        can_reapply = False 
                        command_type = "default"
                    else:
                        intended_rgb = None 
                        pattern_command_template = pattern_commands.get(intended_effect_name)
                        if pattern_command_template:
                            if "{zone}" in pattern_command_template:
                                base_command_url_str = pattern_command_template.format(zone=self._zone)
                            else:
                                parsed_template = urllib.parse.urlparse(pattern_command_template)
                                template_params = urllib.parse.parse_qs(parsed_template.query)
                                template_params['zones'] = [str(self._zone)]
                                template_params['num_zones'] = ['1']
                                new_query = urllib.parse.urlencode(template_params, doseq=True)
                                scheme = parsed_template.scheme or 'http'
                                netloc = parsed_template.netloc or self._ip
                                base_command_url_str = urllib.parse.urlunparse(
                                    (scheme, netloc, parsed_template.path, '', new_query, '')
                                )
                            url = self._adjust_colors_in_url(base_command_url_str, brightness_factor)
                            if url:
                                parsed_final_url = urllib.parse.urlparse(url)
                                final_url_params = urllib.parse.parse_qs(parsed_final_url.query)
                                final_url_params = {k: v[0] for k, v in final_url_params.items()}
                        else:
                            _LOGGER.error("Cannot re-apply effect '%s': Not found in current patterns. Defaulting to white.", intended_effect_name)
                            can_reapply = False 
                            command_type = "default"
                else:
                    _LOGGER.warning("Unknown last command type '%s' stored. Defaulting to white.", last_cmd_type)
                    can_reapply = False 
                    command_type = "default"

            if not can_reapply:
                if command_type != "brightness_only":
                    command_type = "default"
                    _LOGGER.debug("Zone %d: No valid last command to re-apply or forced default. Setting default white with brightness factor %.2f", self._zone, brightness_factor)
                    intended_rgb = (255, 255, 255)
                    intended_effect_name = None
                    scaled_color = tuple(max(0, min(int(c * brightness_factor), 255)) for c in intended_rgb)
                    final_url_params = {
                        "patternType": "custom", "num_zones": 1, "zones": self._zone,
                        "num_colors": 1, "colors": ','.join(map(str, scaled_color)),
                        "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
                    }
                    url = self._build_url(final_url_params)
                else:
                    _LOGGER.warning("Zone %d: Brightness adjustment requested, but failed to re-apply last command. Setting default white.", self._zone)
                    command_type = "default"
                    intended_rgb = (255, 255, 255)
                    intended_effect_name = None
                    scaled_color = tuple(max(0, min(int(c * brightness_factor), 255)) for c in intended_rgb)
                    final_url_params = {
                        "patternType": "custom", "num_zones": 1, "zones": self._zone,
                        "num_colors": 1, "colors": ','.join(map(str, scaled_color)),
                        "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
                    }
                    url = self._build_url(final_url_params)

        success = False
        if url:
            success = await self._send_request(url)
            if success:
                self._state = True 
                self._brightness = target_brightness 
                try:
                    params_to_parse = final_url_params if final_url_params else urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                    colors_val = params_to_parse.get('colors')
                    colors_str = colors_val[0] if isinstance(colors_val, list) else colors_val

                    if colors_str:
                        values = [v.strip() for v in colors_str.split(',') if v.strip()]
                        if len(values) >= 3:
                            try:
                                r = max(0, min(int(values[0]), 255))
                                g = max(0, min(int(values[1]), 255))
                                b = max(0, min(int(values[2]), 255))
                                first_color = (r, g, b)
                                if self._rgb_color != first_color:
                                    _LOGGER.debug("Setting Zone %d RGB color from URL's first color: %s", self._zone, first_color)
                                    self._rgb_color = first_color
                            except (ValueError, IndexError):
                                _LOGGER.warning("Could not parse first RGB values from '%s' in URL: %s", colors_str, url)
                        else:
                            _LOGGER.debug("Not enough color values (need 3, got %d) in '%s' for URL: %s", len(values), colors_str, url)
                    else:
                        _LOGGER.debug("No 'colors' parameter found in sent command params/URL to parse: %s", url)
                except Exception as e:
                    _LOGGER.exception("Error parsing first color from command/URL '%s': %s", url, e)

                if self._effect != intended_effect_name:
                    _LOGGER.debug("Setting Zone %d effect state to: %s", self._zone, intended_effect_name)
                    self._effect = intended_effect_name

                if command_type == "color" or command_type == "default":
                    self._last_successful_command = {
                        "type": "color",
                        "params": final_url_params, 
                        "rgb": intended_rgb 
                    }
                elif command_type == "effect":
                    self._last_successful_command = {
                        "type": "effect",
                        "name": intended_effect_name,
                        "params": final_url_params 
                    }
                elif command_type == "brightness_only" or command_type == "last":
                    if self._last_successful_command:
                        self._last_successful_command["params"] = final_url_params 
                    else: 
                        _LOGGER.warning("Updated state based on 'last' command, but _last_successful_command was unexpectedly missing. Storing as unknown.")
                        self._last_successful_command = {"type": "unknown", "params": final_url_params}

            else:
                _LOGGER.error("Failed to turn on/update Zone %d via URL: %s", self._zone, url)

        else:
            _LOGGER.debug("No command URL generated for zone %d turn_on request (e.g., invalid effect)", self._zone)

        self.async_write_ha_state()


    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        _LOGGER.debug("Turning off Zone %d", self._zone)

        url_params = {
            "patternType": "off", "num_zones": 1, "zones": self._zone,
            "num_colors": 1, "colors": "0,0,0", 
            "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
        }
        url = self._build_url(url_params)

        success = await self._send_request(url)

        if success:
            self._state = False
            self._effect = None 
            self._rgb_color = (0, 0, 0) 

            _LOGGER.debug("Zone %d turned off successfully. _last_successful_command remains unchanged.", self._zone) # Optional: Add log
        else:
            _LOGGER.error("Failed to turn off Zone %d, state might be incorrect.", self._zone)

        self.async_write_ha_state()


    def _build_url(self, params: dict) -> str:
        """Builds the full URL for a setPattern command."""
        params.setdefault("patternType", "custom")
        params.setdefault("num_zones", 1)
        params.setdefault("zones", self._zone)
        params.setdefault("num_colors", 1)
        params.setdefault("colors", "255,255,255")
        params.setdefault("direction", "F")
        params.setdefault("speed", 0)
        params.setdefault("gap", 0)
        params.setdefault("other", 0)
        params.setdefault("pause", 0)

        sanitized_params = {}
        for key, value in params.items():
            if isinstance(value, list):
                sanitized_params[key] = value[0]
                sanitized_params[key] = value

        query_string = urllib.parse.urlencode(sanitized_params)
        return f"http://{self._ip}/setPattern?{query_string}"


    async def _send_request(self, url: str) -> bool:
        """Send a request to the given URL. Returns True on success."""
        _LOGGER.debug("Sending request to zone %d: %s", self._zone, url)
        try:
            async with async_timeout.timeout(15):
                async with self._session.get(url) as response:
                    resp_text = await response.text() 
                    _LOGGER.debug("Response status for zone %d: %d", self._zone, response.status)
                    _LOGGER.debug("Response text for zone %d: %s", self._zone, resp_text[:200])

                    response.raise_for_status() 

                    if not self._attr_available:
                        _LOGGER.info("Oelo Zone %d is now available (command successful)", self._zone)
                        self._attr_available = True
                    return True

        except asyncio.TimeoutError:
            _LOGGER.error("Request timed out for zone %d calling: %s", self._zone, url)
            if self._attr_available:
                _LOGGER.warning("Marking Oelo Zone %d as unavailable due to timeout.", self._zone)
                self._attr_available = False
            return False
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("HTTP request failed for zone %d: %s %s (URL: %s)", self._zone, err.status, err.message, url)
            if self._attr_available:
                _LOGGER.warning("Marking Oelo Zone %d as unavailable due to HTTP error.", self._zone)
                self._attr_available = False
            return False
        except aiohttp.ClientError as err: 
            _LOGGER.error("Connection or client error for zone %d: %s (URL: %s)", self._zone, err, url)
            if self._attr_available:
                _LOGGER.warning("Marking Oelo Zone %d as unavailable due to connection error.", self._zone)
                self._attr_available = False
            return False
        except Exception as err:
            _LOGGER.exception("An unexpected error occurred during request for zone %d: %s (URL: %s)", self._zone, err, url)
            if self._attr_available:
                _LOGGER.warning("Marking Oelo Zone %d as unavailable due to unexpected error.", self._zone)
                self._attr_available = False
            return False


    def _adjust_colors_in_url(self, url: str, brightness_factor: float) -> str:
        """Adjusts the 'colors' parameter in a URL query string based on brightness factor."""
        try:
            brightness_factor = max(0.0, min(float(brightness_factor), 1.0))

            parsed_url = urllib.parse.urlparse(url)
            scheme = parsed_url.scheme or 'http'
            netloc = parsed_url.netloc or self._ip
            query_params = urllib.parse.parse_qs(parsed_url.query)

            if 'colors' in query_params:
                colors_str = query_params['colors'][0]
                if colors_str and colors_str.strip() and colors_str != "0,0,0": 
                    color_values_str = [c.strip() for c in colors_str.split(',') if c.strip()]
                    if not color_values_str:
                        _LOGGER.warning("Colors parameter '%s' resulted in empty list for URL: %s", colors_str, url)
                        return url

                    colors = []
                    for val_str in color_values_str:
                            try:
                                colors.append(int(val_str))
                            except ValueError:
                                _LOGGER.warning("Skipping non-integer color value '%s' in URL: %s. Returning original.", val_str, url)
                                return url

                    if len(colors) % 3 != 0:
                        _LOGGER.warning("Number of color values (%d) is not a multiple of 3 in URL: %s. Cannot reliably scale. Returning original.", len(colors), url)
                        return url

                    adjusted_colors = [max(0, min(int(value * brightness_factor), 255)) for value in colors]
                    adjusted_colors_str = ','.join(map(str, adjusted_colors))
                    query_params['colors'] = [adjusted_colors_str]
                    _LOGGER.debug("Adjusted colors for factor %.2f: %s -> %s", brightness_factor, colors_str, adjusted_colors_str)

            else:
                _LOGGER.debug("No 'colors' parameter to adjust in URL: %s", url)
                return url

            new_query = urllib.parse.urlencode(query_params, doseq=True)
            new_url = urllib.parse.urlunparse(
                (scheme, netloc, parsed_url.path, parsed_url.params, new_query, parsed_url.fragment)
            )
            return new_url

        except Exception as e:
            _LOGGER.exception("Error adjusting colors in URL '%s': %s. Returning original.", url, e)
            return url