from __future__ import annotations
import logging
import asyncio
import voluptuous as vol
import aiohttp
import async_timeout
from urllib.parse import urlencode

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

async def async_setup_platform(
    hass: HomeAssistant, config: dict, async_add_entities: AddEntitiesCallback, discovery_info=None
):
    """Set up the Oelo Lights platform."""
    ip_address = config.get(CONF_IP_ADDRESS)
    zone_name = config.get(CONF_NAME)
    session = hass.helpers.aiohttp_client.async_get_clientsession()

    # Create a list of light entities
    light_entities = []

    # Create a light entity for each of the 6 zones
    for zone in range(1, 7):
        light_entity = OeloLight(session, ip_address, f"{zone_name} {zone}", zone)
        light_entities.append(light_entity)

    # Add all light entities
    async_add_entities(light_entities)

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
        self._rate_limit = asyncio.Semaphore(1)

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

    @property
    def supported_color_modes(self) -> set:
        """Return the color modes the light supports."""
        return {ColorMode.RGB}

    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode."""
        return ColorMode.RGB

async def async_added_to_hass(self) -> None:
    """Handle entity which is added to Home Assistant."""
    await super().async_added_to_hass()
    # Restore state if there is a saved state
    state = await self.async_get_last_state()
    if state:
        self._state = state.state == "on"
        self._brightness = state.attributes.get("brightness", 255)
        self._effect = state.attributes.get("effect")
        
        # Add error handling for rgb_color
        rgb_color = state.attributes.get("rgb_color")
        if rgb_color is not None:
            self._rgb_color = tuple(rgb_color)
        else:
            self._rgb_color = (0, 0, 255)

    async def async_turn_on(self, **kwargs: dict) -> None:
        """Turn on the light with optional RGB color, brightness, and effect."""
        self._state = True

        if ATTR_RGB_COLOR in kwargs:
            self._rgb_color = kwargs[ATTR_RGB_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
        else:
            self._brightness = 255  # Ensure brightness is set to a valid value if not provided

        brightness_factor = self._brightness / 255
        scaled_color = tuple(int(c * brightness_factor) for c in self._rgb_color)

        params = {
            "patternType": "custom",
            "num_zones": 1,
            "zones": self._zone,
            "num_colors": 1,
            "colors": ",".join(map(str, scaled_color)),
            "direction": "F",
            "speed": 0,
            "gap": 0,
            "other": 0,
            "pause": 0,
        }

        url = f"http://{self._ip}/setPattern?" + urlencode(params)
        await self._send_request(url)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: dict) -> None:
        """Turn off the light."""
        self._state = False
        url = f"http://{self._ip}/setPattern?patternType=off&num_zones=1&zones={self._zone}&num_colors=1&colors=0,0,0&direction=F&speed=0&gap=0&other=0&pause=0"
        await self._send_request(url)
        self.async_write_ha_state()

    def turn_on(self, **kwargs: dict) -> None:
        """Turn on the light (synchronous wrapper)."""
        self.async_turn_on(**kwargs)

    def turn_off(self, **kwargs: dict) -> None:
        """Turn off the light (synchronous wrapper)."""
        self.async_turn_off(**kwargs)

    async def _send_request(self, url: str) -> None:
        """Send a request to the given URL with retries."""
        retries = 3
        async with self._rate_limit:
            for attempt in range(retries):
                try:
                    async with async_timeout.timeout(10):
                        async with self._session.get(url) as response:
                            if response.status == 200:
                                _LOGGER.info("Request successful for zone %d", self._zone)
                                return
                            else:
                                _LOGGER.error("Failed request for zone %d: %s", self._zone, response.status)
                except asyncio.TimeoutError:
                    _LOGGER.error("Request timed out for zone %d", self._zone)
                except aiohttp.ClientError as err:
                    _LOGGER.error("HTTP request failed for zone %d: %s", self._zone, err)

                # Wait before retrying
                await asyncio.sleep(1)

        _LOGGER.error("Request ultimately failed after retries for zone %d", self._zone)
