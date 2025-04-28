from __future__ import annotations
import logging
import asyncio
import voluptuous as vol
import aiohttp
import async_timeout
import re
import urllib.parse

try:
    from custom_components.oelo_lights.patterns import pattern_commands
    from custom_components.oelo_lights.const import DOMAIN
except ImportError:
    from .patterns import pattern_commands
    from .const import DOMAIN

from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode, PLATFORM_SCHEMA, LightEntity, LightEntityFeature
)

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import DeviceInfo 

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
):
    """Set up the Oelo Lights platform from a config entry."""
    ip_address = entry.data[CONF_IP_ADDRESS]

    session = hass.helpers.aiohttp_client.async_get_clientsession()

    light_entities = []

    # Create a light entity for each of the 6 zones on the controller
    for zone in range(1, 7):
        light_entity = OeloLight(session, ip_address, zone, entry)
        light_entities.append(light_entity)

    async_add_entities(light_entities)

class OeloLight(LightEntity, RestoreEntity):
    """Representation of an Oelo Light zone."""

    _attr_has_entity_name = True

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
        # self._name = f"Oelo Zone: {zone}"
        self._effect = None
        self._last_successful_command = None

        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB
        self._attr_supported_features = LightEntityFeature.EFFECT 

        # --- Device and Entity Identification ---
        self._attr_unique_id = f"{self._entry.entry_id}_zone_{self._zone}"
        self._attr_name = f"Zone {self._zone}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the controller."""
        return DeviceInfo(
            identifiers={

                (DOMAIN, self._entry.entry_id)
            },
            name=self._entry.title, 
            manufacturer="Oelo",
            model="Light Controller",
            configuration_url=f"http://{self._ip}/",
            # sw_version= #
            # hw_version= # 
        )

    @property
    def is_on(self) -> bool:
        """Return the state of the light."""
        return self._state

    @property
    def brightness(self) -> int | None: # Allow None
        """Return the brightness of the light."""
        return self._brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None: 
        """Return the RGB color of the light."""
        return self._rgb_color

    @property
    def effect(self) -> str | None:
        """Return the current effect."""
        return self._effect

    @property
    def effect_list(self) -> list[str]:
        """Return the list of supported effects."""
        return list(pattern_commands.keys())

    # Remove supported_features property, _attr_supported_features is used
    # @property
    # def supported_features(self) -> int:
    #     """Flag supported features."""
    #     return LightEntityFeature.EFFECT

    async def async_added_to_hass(self):
        """Handle entity which is added to Home Assistant."""
        await super().async_added_to_hass()
        # Restore entity state
        state = await self.async_get_last_state()
        if state:
            self._state = state.state == "on"
            self._brightness = state.attributes.get(ATTR_BRIGHTNESS) 
            self._effect = state.attributes.get("effect") 
            rgb_color = state.attributes.get(ATTR_RGB_COLOR) 
            if rgb_color is not None:
                self._rgb_color = tuple(rgb_color)


    async def async_turn_on(self, **kwargs):
        """Turn on the light with optional RGB color, brightness, and effect."""
        new_state = True
        url = None
        current_effect = self._effect 
        current_rgb = self._rgb_color 
        current_brightness = self._brightness

        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
        elif self._brightness is None: 
            self._brightness = 255

        brightness_factor = (self._brightness or 255) / 255

        if ATTR_RGB_COLOR in kwargs:
            self._effect = None 
            self._rgb_color = kwargs[ATTR_RGB_COLOR]
            scaled_color = tuple(int(c * brightness_factor) for c in self._rgb_color)
            url_params = {
                "patternType": "custom",
                "num_zones": 1,
                "zones": self._zone,
                "num_colors": 1,
                "colors": ','.join(map(str, scaled_color)),
                "direction": "F", 
                "speed": 0,
                "gap": 0,
                "other": 0,
                "pause": 0
            }
            query_string = urllib.parse.urlencode(url_params)
            url = f"http://{self._ip}/setPattern?{query_string}"
            self._last_successful_command = url 

        elif "effect" in kwargs:
            self._effect = kwargs["effect"]

            if self._effect in pattern_commands:
                pattern_command_template = pattern_commands[self._effect]
                if "{zone}" in pattern_command_template:
                    base_command = pattern_command_template.format(zone=self._zone)
                else:
                    _LOGGER.warning("Pattern '%s' might not be zone-specific. Applying globally or adjust logic.", self._effect)
                    base_command = pattern_command_template 

                parsed_command_url = urllib.parse.urlparse(base_command)
                base_url_path = parsed_command_url.path.lstrip('/') 
                base_query_params = urllib.parse.parse_qs(parsed_command_url.query)

                base_query_params['zones'] = [str(self._zone)]
                base_query_params['num_zones'] = ['1'] 

                temp_url_for_adjustment = urllib.parse.urlunparse(
                    ('http', self._ip, base_url_path, '', urllib.parse.urlencode(base_query_params, doseq=True), '')
                )

                url = self._adjust_colors_in_url(temp_url_for_adjustment, brightness_factor)
                self._last_successful_command = temp_url_for_adjustment 

            else:
                _LOGGER.error("Invalid effect selected: %s", self._effect)
                self._effect = current_effect
                self._brightness = current_brightness
                self.async_write_ha_state() 
                return

        elif self._state is False: 
            if self._last_successful_command:
                _LOGGER.debug("Turning on zone %d using last command: %s", self._zone, self._last_successful_command)
                url = self._adjust_colors_in_url(self._last_successful_command, brightness_factor)
            else:
                # Default to white if no previous command
                _LOGGER.debug("Turning on zone %d to default white", self._zone)
                self._rgb_color = (255, 255, 255)
                self._effect = None
                scaled_color = tuple(int(c * brightness_factor) for c in self._rgb_color)
                url_params = {
                    "patternType": "custom", "num_zones": 1, "zones": self._zone,
                    "num_colors": 1, "colors": ','.join(map(str, scaled_color)),
                    "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
                }
                query_string = urllib.parse.urlencode(url_params)
                url = f"http://{self._ip}/setPattern?{query_string}"
                self._last_successful_command = url 

        elif ATTR_BRIGHTNESS in kwargs: 
            if self._last_successful_command:
                _LOGGER.debug("Adjusting brightness for zone %d using last command: %s", self._zone, self._last_successful_command)
                url = self._adjust_colors_in_url(self._last_successful_command, brightness_factor)
            else:
                _LOGGER.warning("Cannot adjust brightness for zone %d: No last command available.", self._zone)

                self._brightness = current_brightness
                self.async_write_ha_state()
                return


        if url:
            success = await self._send_request(url)
            if success:
                self._state = True
            else:
                self._state = False
                self._brightness = current_brightness
                self._rgb_color = current_rgb
                self._effect = current_effect
        else:
            _LOGGER.debug("No state change needed or no command generated for zone %d", self._zone)
            if self._state != new_state:
                self._state = new_state


        # Update Home Assistant state
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        url_params = {
            "patternType": "off", "num_zones": 1, "zones": self._zone,
            "num_colors": 1, "colors": "0,0,0", 
            "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
        }
        query_string = urllib.parse.urlencode(url_params)
        url = f"http://{self._ip}/setPattern?{query_string}"

        success = await self._send_request(url)

        if success:
            self._state = False
        else:
            _LOGGER.error("Failed to turn off zone %d, state might be incorrect.", self._zone)


        self.async_write_ha_state()

    async def _send_request(self, url: str) -> bool:
        """Send a request to the given URL. Returns True on success."""
        _LOGGER.debug("Sending request to zone %d: %s", self._zone, url)
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(url) as response:
                    response.raise_for_status() 
                    _LOGGER.info("Request successful for zone %d (Status: %d)", self._zone, response.status)
                    return True
        except asyncio.TimeoutError:
            _LOGGER.error("Request timed out for zone %d calling: %s", self._zone, url)
            return False
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("HTTP request failed for zone %d: %s %s (URL: %s)", self._zone, err.status, err.message, url)
            return False
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP request failed for zone %d: %s (URL: %s)", self._zone, err, url)
            return False
        except Exception as err:
            _LOGGER.error("An unexpected error occurred during request for zone %d: %s (URL: %s)", self._zone, err, url)
            return False

    def _adjust_colors_in_url(self, url: str, brightness_factor: float) -> str:
        """Adjusts the 'colors' parameter in a URL query string based on brightness factor."""
        try:
            parsed_url = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(parsed_url.query)

            if 'colors' in query_params:
                colors_str = query_params['colors'][0]
                if colors_str:
                    color_values_str = [c.strip() for c in colors_str.split(',') if c.strip()]
                    if not color_values_str:
                        _LOGGER.warning("Colors parameter '%s' resulted in empty list for URL: %s", colors_str, url)
                        return url

                    try:
                        colors = list(map(int, color_values_str))
                        if len(colors) % 3 != 0:
                            _LOGGER.warning("Number of color values (%d) is not a multiple of 3 in URL: %s", len(colors), url)
                            return url

                        adjusted_colors = [max(0, min(int(value * brightness_factor), 255)) for value in colors]
                        adjusted_colors_str = ','.join(map(str, adjusted_colors))
                        query_params['colors'] = [adjusted_colors_str]

                    except ValueError:
                        _LOGGER.error("Invalid non-integer color value found in colors parameter '%s' for URL: %s", colors_str, url)
                        return url
                else:
                    _LOGGER.debug("Colors parameter is present but empty in URL: %s", url)
                    return url
            else:
                _LOGGER.debug("No 'colors' parameter to adjust in URL: %s", url)
                return url


            new_query = urllib.parse.urlencode(query_params, doseq=True)

            new_url = urllib.parse.urlunparse(
                (parsed_url.scheme or 'http', 
                parsed_url.netloc or self._ip,
                parsed_url.path,
                parsed_url.params,
                new_query,
                parsed_url.fragment)
            )

            return new_url

        except Exception as e:
            _LOGGER.error("Error adjusting colors in URL '%s': %s", url, e)
            return url