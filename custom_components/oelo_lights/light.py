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
except ImportError:
    from .patterns import pattern_commands

from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode, PLATFORM_SCHEMA, LightEntity, LightEntityFeature
)

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.config_entries import ConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_IP_ADDRESS): vol.Coerce(str),
})

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback, discovery_info=None, 
):
    """Set up the Oelo Lights platform."""
    ip_address = entry.data[CONF_IP_ADDRESS]

    session = aiohttp.ClientSession()

    light_entities = []

    # Create a light entity for each of the 6 zones on the controller
    for zone in range(1, 7):
        light_entity = OeloLight(session, ip_address, zone)
        light_entities.append(light_entity)

    async_add_entities(light_entities)

    def close_session(event):
        session.close()

    hass.bus.async_listen_once("homeassistant_stop", close_session)

class OeloLight(LightEntity, RestoreEntity):
    """Representation of an Oelo Light."""

    def __init__(self, session: aiohttp.ClientSession, ip: str, zone: int):
        """Initialize the light."""
        self._session = session
        self._ip = ip
        self._zone = zone
        self._state = False
        self._brightness = 255
        self._rgb_color = (255, 255, 255) 
        self._name = f"Oelo Zone: {zone}"
        self._effect = None
        self._last_successful_command = None

        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB
        self._attr_supported_features = self.supported_features

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this light."""
        return f"oelo_zone_{self._zone}"

    @property
    def is_on(self) -> bool:
        """Return the state of the light."""
        return self._state

    @property
    def brightness(self) -> int:
        """Return the brightness of the light."""
        return self._brightness if self._brightness is not None else 255

    @property
    def rgb_color(self) -> tuple[int, int, int]:
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

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        return LightEntityFeature.EFFECT

    async def async_added_to_hass(self):
        """Handle entity which is added to Home Assistant."""
        await super().async_added_to_hass()

        state = await self.async_get_last_state()
        if state:
            self._state = state.state == "on"
            if "brightness" in state.attributes:
                self._brightness = state.attributes["brightness"]
            if "effect" in state.attributes:
                self._effect = state.attributes["effect"]
            rgb_color = state.attributes.get("rgb_color")
            if rgb_color is not None:
                self._rgb_color = tuple(rgb_color)

    async def async_turn_on(self, **kwargs):
        """Turn on the light with optional RGB color, brightness, and effect."""
        self._state = True

        self._brightness = kwargs.get(ATTR_BRIGHTNESS, self._brightness if self._brightness is not None else 255)

        url = None

        if ATTR_RGB_COLOR in kwargs:
            self._effect = None
            self._rgb_color = kwargs[ATTR_RGB_COLOR]

            brightness_factor = self._brightness / 255
            scaled_color = tuple(int(c * brightness_factor) for c in self._rgb_color)
            url = (
                f"http://{self._ip}/setPattern?patternType=custom&num_zones=1&zones={self._zone}"
                f"&num_colors=1&colors={','.join(map(str, scaled_color))}"
                "&direction=F&speed=0&gap=0&other=0&pause=0"
            )
        
        elif "effect" in kwargs:
            self._effect = kwargs["effect"]
            self._rgb_color = (255, 255, 255)

            if self._effect in pattern_commands:
                pattern_command = pattern_commands[self._effect].format(zone=self._zone)
                url = f"http://{self._ip}/{pattern_command}"
            else:
                _LOGGER.error("Invalid effect selected: %s", self._effect)
                return

        if url:
            brightness_factor = self._brightness / 255
            new_url = self._adjust_colors_in_url(url, brightness_factor)
            await self._send_request(new_url)
            self._last_successful_command = url
        else:
            if self._last_successful_command:
                _LOGGER.warning("Retrying last successful command for zone %d", self._zone)
                brightness_factor = self._brightness / 255
                new_url = self._adjust_colors_in_url(self._last_successful_command, brightness_factor)
                await self._send_request(new_url)
            else:
                _LOGGER.warning("No previous command available, setting default white color for zone %d", self._zone)
                url = (
                    f"http://{self._ip}/setPattern?patternType=custom&num_zones=1&zones={self._zone}"
                    f"&num_colors=1&colors=255,255,255"
                    "&direction=F&speed=0&gap=0&other=0&pause=0"
                )
                await self._send_request(url)
                self._last_successful_command = url

    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        self._state = False

        url = f"http://{self._ip}/setPattern?patternType=off&num_zones=1&zones={self._zone}&num_colors=1&colors=0,0,0&direction=F&speed=0&gap=0&other=0&pause=0"

        await self._send_request(url)

    async def _send_request(self, url: str):
        """Send a request to the given URL."""
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(url) as response:
                    if response.status == 200:
                        _LOGGER.info("Request successful for zone %d", self._zone)
                    else:
                        _LOGGER.error("Failed request for zone %d: %s", self._zone, response.status)
        except asyncio.TimeoutError:
            _LOGGER.error("Request timed out for zone %d", self._zone)
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP request failed for zone %d: %s", self._zone, err)

    def turn_on(self, **kwargs):
        """Turn the light on."""
        asyncio.run_coroutine_threadsafe(self.async_turn_on(**kwargs), self.hass.loop)

    def turn_off(self, **kwargs):
        """Turn the light off."""
        asyncio.run_coroutine_threadsafe(self.async_turn_off(**kwargs), self.hass.loop)

    def _adjust_colors_in_url(self, url: str, brightness_factor: float) -> str:
        parsed_url = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed_url.query)

        if 'colors' in query_params:
            colors_str = query_params['colors'][0]
            if colors_str: 
                colors = [c for c in colors_str.split(',') if c]

                try:
                    colors = list(map(int, colors))
                except ValueError:
                    _LOGGER.error("Invalid color value found in the colors parameter: %s", colors_str)
                    colors = [0, 0, 0]

                adjusted_colors = [max(0, min(int(value * brightness_factor), 255)) for value in colors]
                
                adjusted_colors_str = ','.join(map(str, adjusted_colors))
                query_params['colors'] = [adjusted_colors_str]
            else:
                _LOGGER.error("Colors parameter is empty.")
                query_params['colors'] = ['0,0,0']

        new_query = urllib.parse.urlencode(query_params, doseq=True)
        
        new_url = urllib.parse.urlunparse(
            (parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.params, new_query, parsed_url.fragment)
        )

        _LOGGER.info("Adjusted URL: %s", new_url)
        
        return new_url
