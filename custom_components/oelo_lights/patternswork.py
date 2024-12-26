from __future__ import annotations
import logging
import asyncio
import voluptuous as vol
import aiohttp
import async_timeout

try:
    from custom_components.oelo_lights.patterns import pattern_commands
except ImportError:
    from .patterns import pattern_commands

from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode, PLATFORM_SCHEMA, LightEntity, LightEntityFeature
)
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

_LOGGER = logging.getLogger(__name__)

# Define the platform schema to allow configuration from YAML
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_IP_ADDRESS, default="192.168.32.39"): vol.Coerce(str),
    vol.Required(CONF_NAME, default="Zone"): vol.Coerce(str),
})

PATTERNS = {
    "Off": "setPattern?patternType=off&num_zones=1&zones={zone}&num_colors=1&colors=0,0,0&direction=F&speed=0&gap=0&other=0&pause=0",
    "Christmas": "setPattern?patternType=christmas&num_zones=1&zones={zone}",
    "Easter": "setPattern?patternType=easter&num_zones=1&zones={zone}",
    "Custom": "setPattern?patternType=custom&num_zones=1&zones={zone}&num_colors=1&colors={colors}&direction=F&speed=0&gap=0&other=0&pause=0"
}

async def async_setup_platform(
    hass: HomeAssistant, config: dict, async_add_entities: AddEntitiesCallback, discovery_info=None
):
    """Set up the Oelo Lights platform."""
    ip_address = config.get(CONF_IP_ADDRESS)
    zone_name = config.get(CONF_NAME)
    session = aiohttp.ClientSession()

    # Create a list of light entities
    light_entities = []

    # Create a light entity for each of the 6 zones
    for zone in range(1, 7):
        light_entity = OeloLight(session, ip_address, f"{zone_name} {zone}", zone)
        light_entities.append(light_entity)

    # Add all light entities
    async_add_entities(light_entities)

    # Ensure the session is closed on shutdown
    def close_session(event):
        session.close()

    hass.bus.async_listen_once("homeassistant_stop", close_session)

class OeloLight(LightEntity, RestoreEntity):
    """Representation of an Oelo Light."""

    def __init__(self, session: aiohttp.ClientSession, ip: str, zone_name: str, zone: int):
        """Initialize the light."""
        self._session = session
        self._ip = ip
        self._zone = zone
        self._state = False
        self._brightness = 255  # Max brightness
        self._rgb_color = (0, 0, 255)  # Default to blue
        self._name = f"Oelo Zone: {zone_name}"
        self._effect = None

        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB
        self._attr_supported_features = self.supported_features

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

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
        # Restore state if there is a saved state
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

        if ATTR_RGB_COLOR in kwargs:
            self._rgb_color = kwargs[ATTR_RGB_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
        else:
            self._brightness = 255  # Set to max brightness if not provided
        if "effect" in kwargs:
            self._effect = kwargs["effect"]

        if self._effect and self._effect in pattern_commands:
            # Apply the effect using the corresponding pattern command
            pattern_command = pattern_commands[self._effect].format(zone=self._zone)
            url = f"http://{self._ip}/{pattern_command}"
        else:
            # Normal RGB color change
            brightness_factor = self._brightness / 255
            scaled_color = tuple(int(c * brightness_factor) for c in self._rgb_color)
            url = (
                f"http://{self._ip}/setPattern?patternType=custom&num_zones=1&zones={self._zone}"
                f"&num_colors=1&colors={','.join(map(str, scaled_color))}"
                "&direction=F&speed=0&gap=0&other=0&pause=0"
            )

        await self._send_request(url)

    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        self._state = False

        url = f"http://{self._ip}/{pattern_commands['Off'].format(zone=self._zone)}"

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
