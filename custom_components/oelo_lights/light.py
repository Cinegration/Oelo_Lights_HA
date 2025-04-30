from __future__ import annotations
import logging
import asyncio
import voluptuous as vol
import aiohttp
import async_timeout
import re
import urllib.parse
from typing import Any

try:
    from .patterns import pattern_commands
    from .const import DOMAIN
except ImportError:
    try:
        from patterns import pattern_commands
    except ImportError:
        pattern_commands = {"Solid White": "http://{ip}/setPattern?patternType=custom&zones={zone}&num_zones=1&num_colors=1&colors=255,255,255&direction=F&speed=0&gap=0&other=0&pause=0"}
    try:
        from const import DOMAIN
    except ImportError:
        DOMAIN = "oelo_lights"

from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_EFFECT, ATTR_RGB_COLOR, ColorMode, PLATFORM_SCHEMA, LightEntity, LightEntityFeature
)

from homeassistant.const import CONF_IP_ADDRESS, STATE_ON, STATE_OFF
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import aiohttp_client

from datetime import timedelta
SCAN_INTERVAL = timedelta(seconds=30)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Oelo Lights platform from a config entry."""
    ip_address = entry.data[CONF_IP_ADDRESS]
    session = aiohttp_client.async_get_clientsession(hass)
    light_entities = []

    for zone in range(1, 7):
        light_entity = OeloLight(session, ip_address, zone, entry)
        light_entities.append(light_entity)

    async_add_entities(light_entities, True)

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
    ) -> None:
        """Initialize the light."""
        self._session = session
        self._ip = ip
        self._zone = zone
        self._entry = entry
        self._state = False
        self._brightness = 255
        self._rgb_color: tuple[int, int, int] | None = (255, 255, 255)
        self._intended_effect: str | None = None
        self._last_successful_command: str | None = None

        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB
        self._attr_supported_features = LightEntityFeature.EFFECT
        self._attr_unique_id = f"{self._entry.entry_id}_zone_{self._zone}"
        self._attr_name = f"Zone {zone}"

        self._attr_available = True

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
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._attr_available

    @property
    def is_on(self) -> bool | None:
        """Return the state of the light."""
        return self._state if self.available else None

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light."""
        return self._brightness if self.available else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the RGB color of the light."""
        return self._rgb_color if self.available else None

    @property
    def effect(self) -> str | None:
        """Return the current effect *if the light is on* and available."""
        if not self.available:
            return None
        return self._intended_effect if self.is_on else None

    @property
    def effect_list(self) -> list[str] | None:
        """Return the list of supported effects."""
        return list(pattern_commands.keys()) if self.available else None

    async def async_added_to_hass(self) -> None:
        """Handle entity which is added to Home Assistant."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._state = last_state.state == STATE_ON
            self._brightness = last_state.attributes.get(ATTR_BRIGHTNESS, 255)
            self._intended_effect = last_state.attributes.get(ATTR_EFFECT)
            rgb_color = last_state.attributes.get(ATTR_RGB_COLOR)
            if rgb_color is not None:
                self._rgb_color = tuple(rgb_color) # type: ignore[assignment]

    async def async_update(self) -> None:
        """Fetch state from controller and update availability."""
        _LOGGER.debug("Updating availability for zone %d", self._zone)
        url = f"http://{self._ip}/getController"
        try:
            async with async_timeout.timeout(10):
                if self._session is None or self._session.closed:
                     _LOGGER.warning("Zone %d: HTTP session closed during update.", self._zone)
                     if self._attr_available:
                         _LOGGER.info("Marking zone %d unavailable (session closed)", self._zone)
                         self._attr_available = False
                     return

                async with self._session.get(url) as response:
                    response.raise_for_status()
                    try:
                        data = await response.json(content_type=None)
                        _LOGGER.debug("Zone %d: getController response: %s", self._zone, data)
                    except (aiohttp.ContentTypeError, ValueError) as json_err:
                         _LOGGER.warning("Zone %d: Received non-JSON response from getController, but controller is reachable: %s", self._zone, json_err)
                    except Exception as parse_err:
                         _LOGGER.error("Zone %d: Error parsing getController response: %s", self._zone, parse_err)

                    if not self._attr_available:
                        _LOGGER.info("Marking zone %d available", self._zone)
                        self._attr_available = True

        except (asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientResponseError) as err:
            if self._attr_available:
                _LOGGER.warning("Marking zone %d unavailable. Error: %s", self._zone, err)
                self._attr_available = False
            else:
                 _LOGGER.debug("Zone %d remains unavailable. Error: %s", self._zone, err)
        except aiohttp.ClientError as err:
             if self._attr_available:
                _LOGGER.warning("Marking zone %d unavailable due to client error: %s", self._zone, err)
                self._attr_available = False
             else:
                 _LOGGER.debug("Zone %d remains unavailable due to client error: %s", self._zone, err)
        except Exception as err:
            _LOGGER.exception("Unexpected error during zone %d update: %s", self._zone, err)
            if self._attr_available:
                _LOGGER.warning("Marking zone %d unavailable due to unexpected error.", self._zone)
                self._attr_available = False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light with optional RGB color, brightness, and effect."""
        _LOGGER.debug("Turning on zone %d with kwargs: %s", self._zone, kwargs)

        if not self._attr_available:
             _LOGGER.warning("Cannot turn on zone %d: Controller is unavailable.", self._zone)
             return

        url_to_send = None
        effect_to_set: str | None = self._intended_effect
        rgb_to_set: tuple[int, int, int] | None = self._rgb_color
        brightness_to_set = self._brightness

        triggered_by_effect = False

        if ATTR_BRIGHTNESS in kwargs:
            brightness_to_set = kwargs[ATTR_BRIGHTNESS]
        if brightness_to_set is None:
             brightness_to_set = 255

        if ATTR_RGB_COLOR in kwargs:
            _LOGGER.debug("Zone %d: Setting RGB color via kwargs", self._zone)
            rgb_to_set = kwargs[ATTR_RGB_COLOR]
            effect_to_set = None
            triggered_by_effect = False

        elif ATTR_EFFECT in kwargs:
            _LOGGER.debug("Zone %d: Setting effect via kwargs", self._zone)
            selected_effect = kwargs[ATTR_EFFECT]
            if selected_effect in pattern_commands:
                effect_to_set = selected_effect
                triggered_by_effect = True
                base_command_url_unparsed = self._get_base_effect_url(selected_effect)
                if base_command_url_unparsed:
                    extracted_rgb = self._extract_first_color_from_url(base_command_url_unparsed)
                    if extracted_rgb:
                        rgb_to_set = extracted_rgb
                    self._last_successful_command = base_command_url_unparsed
                else:
                    _LOGGER.error("Zone %d: Could not get base URL for effect '%s'", self._zone, selected_effect)
                    return
            else:
                _LOGGER.error("Zone %d: Invalid effect selected: %s", self._zone, selected_effect)
                return

        brightness_factor = (brightness_to_set or 255) / 255.0

        if triggered_by_effect and effect_to_set and self._last_successful_command:
             _LOGGER.debug("Zone %d: Constructing URL for explicitly selected effect '%s'", self._zone, effect_to_set)
             url_to_send = self._adjust_colors_in_url(self._last_successful_command, brightness_factor)
        elif rgb_to_set is not None and not triggered_by_effect and effect_to_set is None:
             _LOGGER.debug("Zone %d: Constructing URL for explicitly selected RGB %s", self._zone, rgb_to_set)
             scaled_color = tuple(max(0, min(int(round(c * brightness_factor)), 255)) for c in rgb_to_set)
             url_params = {
                 "patternType": "custom", "num_zones": 1, "zones": self._zone,
                 "num_colors": 1, "colors": ','.join(map(str, scaled_color)),
                 "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
             }
             query_string = urllib.parse.urlencode(url_params)
             url_to_send = f"http://{self._ip}/setPattern?{query_string}"
             base_rgb_str = ','.join(map(str, rgb_to_set))
             base_url_params = url_params.copy()
             base_url_params["colors"] = base_rgb_str
             base_query_string = urllib.parse.urlencode(base_url_params)
             self._last_successful_command = f"http://{self._ip}/setPattern?{base_query_string}"
        elif not self._state or ATTR_BRIGHTNESS in kwargs:
             _LOGGER.debug("Zone %d: Turning on from off state or adjusting brightness.", self._zone)
             base_url_for_on = None
             if effect_to_set:
                 _LOGGER.debug("Zone %d: Reconstructing command for intended effect '%s'", self._zone, effect_to_set)
                 base_url_for_on = self._get_base_effect_url(effect_to_set)
                 if base_url_for_on:
                     self._last_successful_command = base_url_for_on
                     extracted_rgb = self._extract_first_color_from_url(base_url_for_on)
                     if extracted_rgb:
                         rgb_to_set = extracted_rgb
                 else:
                     _LOGGER.warning("Zone %d: Failed to get base URL for stored effect '%s'.", self._zone, effect_to_set)
                     effect_to_set = None
             if not base_url_for_on and self._last_successful_command:
                 _LOGGER.debug("Zone %d: Replaying last successful command.", self._zone)
                 base_url_for_on = self._last_successful_command
                 parsed_command = urllib.parse.urlparse(base_url_for_on)
                 command_params = urllib.parse.parse_qs(parsed_command.query)
                 if command_params.get("patternType", [""])[0] == "custom":
                      extracted_rgb = self._extract_first_color_from_url(base_url_for_on)
                      if extracted_rgb:
                          rgb_to_set = extracted_rgb
                      effect_to_set = None
             if base_url_for_on:
                 url_to_send = self._adjust_colors_in_url(base_url_for_on, brightness_factor)
             else:
                 _LOGGER.debug("Zone %d: Falling back to default white.", self._zone)
                 effect_to_set = None
                 rgb_to_set = (255, 255, 255)
                 scaled_color = tuple(max(0, min(int(round(c * brightness_factor)), 255)) for c in rgb_to_set)
                 url_params = {
                     "patternType": "custom", "num_zones": 1, "zones": self._zone,
                     "num_colors": 1, "colors": ','.join(map(str, scaled_color)),
                     "direction": "F", "speed": 0, "gap": 0, "other": 0, "pause": 0
                 }
                 query_string = urllib.parse.urlencode(url_params)
                 url_to_send = f"http://{self._ip}/setPattern?{query_string}"
                 base_url_params = url_params.copy()
                 base_url_params["colors"] = "255,255,255"
                 base_query_string = urllib.parse.urlencode(base_url_params)
                 self._last_successful_command = f"http://{self._ip}/setPattern?{base_query_string}"

        success = False
        if url_to_send:
            success = await self._send_request(url_to_send)
        else:
             _LOGGER.debug("Zone %d: Turn on called, but no state change needed.", self._zone)
             success = True

        if success:
            self._state = True
            self._brightness = brightness_to_set
            self._rgb_color = rgb_to_set
            self._intended_effect = effect_to_set
            self._attr_color_mode = ColorMode.RGB
            if not self._attr_available:
                 _LOGGER.info("Marking zone %d available after successful command", self._zone)
                 self._attr_available = True
        else:
             _LOGGER.error("Zone %d: Failed to execute turn_on command.", self._zone)
             if self._attr_available:
                 _LOGGER.warning("Marking zone %d unavailable after failed command", self._zone)
                 self._attr_available = False

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        _LOGGER.debug("Turning off zone %d", self._zone)

        if not self._attr_available:
             _LOGGER.warning("Cannot turn off zone %d: Controller is unavailable.", self._zone)
             return

        if not self._state:
             _LOGGER.debug("Zone %d already off.", self._zone)
             if not self._attr_available:
                  _LOGGER.info("Marking zone %d available (turn_off called while off)", self._zone)
                  self._attr_available = True
             self.async_write_ha_state()
             return

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
            if not self._attr_available:
                 _LOGGER.info("Marking zone %d available after successful command", self._zone)
                 self._attr_available = True
        else:
            _LOGGER.error("Failed to turn off zone %d.", self._zone)
            if self._attr_available:
                 _LOGGER.warning("Marking zone %d unavailable after failed command", self._zone)
                 self._attr_available = False

        self.async_write_ha_state()

    def _get_base_effect_url(self, effect_name: str) -> str | None:
        """Gets the base URL string for a given effect name, applying zone."""
        if effect_name not in pattern_commands:
            _LOGGER.error("Effect '%s' not found in pattern_commands", effect_name)
            return None

        base_command_url_template = pattern_commands[effect_name]

        try:
            parsed_template = urllib.parse.urlparse(base_command_url_template)
            template_query = urllib.parse.parse_qs(parsed_template.query)

            template_query['zones'] = [str(self._zone)]
            template_query['num_zones'] = ['1']

            scheme = parsed_template.scheme or 'http'
            netloc = parsed_template.netloc or self._ip
            path = parsed_template.path or '/setPattern'

            final_url = urllib.parse.urlunparse(
                (scheme,
                 netloc,
                 path,
                 parsed_template.params,
                 urllib.parse.urlencode(template_query, doseq=True),
                 parsed_template.fragment)
            )
            return final_url

        except Exception as e:
            _LOGGER.error("Error building base URL for effect '%s' from template '%s': %s",
                          effect_name, base_command_url_template, e)
            return None

    def _extract_first_color_from_url(self, url: str) -> tuple[int, int, int] | None:
        """Extracts the first RGB tuple from the 'colors' param of a URL."""
        if not url: return None
        try:
            parsed_url = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(parsed_url.query)

            if 'colors' in query_params:
                colors_str = query_params['colors'][0]
                if colors_str:
                    color_values_str = [c.strip() for c in colors_str.split(',') if c.strip().isdigit()]
                    if len(color_values_str) >= 3:
                        extracted_rgb = tuple(max(0, min(int(val), 255)) for val in color_values_str[:3])
                        return extracted_rgb
                    else:
                        _LOGGER.warning("Zone %d: Not enough numeric color values (found %d) in 'colors=%s' for URL: %s",
                                        self._zone, len(color_values_str), colors_str, url)
                else:
                    _LOGGER.debug("Zone %d: Empty 'colors' param in URL: %s", self._zone, url)
            else:
                _LOGGER.debug("Zone %d: No 'colors' param in URL: %s", self._zone, url)

        except ValueError:
                _LOGGER.error("Zone %d: Invalid non-integer color value found in URL: %s", self._zone, url)
        except Exception as e:
            _LOGGER.error("Zone %d: Error parsing color from URL '%s': %s", self._zone, url, e)

        return None

    async def _send_request(self, url: str) -> bool:
        """Send a request to the given URL. Returns True on success."""
        try:
            parsed_url = urllib.parse.urlparse(url)
            scheme = parsed_url.scheme or 'http'
            netloc = parsed_url.netloc or self._ip
            final_url = urllib.parse.urlunparse(parsed_url._replace(scheme=scheme, netloc=netloc))
        except Exception as e:
            _LOGGER.error("Zone %d: Failed to parse or build final URL from '%s': %s", self._zone, url, e)
            return False

        _LOGGER.debug("Sending request to zone %d: %s", self._zone, final_url)
        try:
            async with async_timeout.timeout(10):
                if self._session is None or self._session.closed:
                     _LOGGER.error("Zone %d: HTTP session is closed or invalid.", self._zone)
                     return False

                async with self._session.get(final_url) as response:
                    response.raise_for_status()
                    _LOGGER.info("Request successful for zone %d (Status: %d)", self._zone, response.status)
                    return True

        except asyncio.TimeoutError:
            _LOGGER.error("Request timed out for zone %d calling: %s", self._zone, final_url)
            return False
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("HTTP request failed for zone %d: %s %s (URL: %s)", self._zone, err.status, err.message, final_url)
            return False
        except aiohttp.ClientConnectionError as err:
             _LOGGER.error("HTTP connection failed for zone %d: %s (URL: %s)", self._zone, err, final_url)
             return False
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP client error for zone %d: %s (URL: %s)", self._zone, err, final_url)
            return False
        except Exception as err:
            _LOGGER.exception("An unexpected error occurred during request for zone %d: %s (URL: %s)", self._zone, err, final_url)
            return False

    def _adjust_colors_in_url(self, url: str, brightness_factor: float) -> str:
        """Adjusts the 'colors' parameter in a URL query string based on brightness factor."""
        if not url:
            _LOGGER.warning("Attempted to adjust colors in an empty URL.")
            return ""

        try:
            parsed_url = urllib.parse.urlparse(url)
            scheme = parsed_url.scheme or 'http'
            netloc = parsed_url.netloc or self._ip
            path = parsed_url.path
            params = parsed_url.params
            fragment = parsed_url.fragment

            query_params = urllib.parse.parse_qs(parsed_url.query)

            if 'colors' in query_params:
                colors_str = query_params['colors'][0]
                if colors_str:
                    color_values_str = [c.strip() for c in colors_str.split(',') if c.strip().isdigit()]
                    if not color_values_str:
                        _LOGGER.warning("Colors parameter '%s' resulted in empty numeric list for URL: %s", colors_str, url)
                        return url

                    try:
                        colors = list(map(int, color_values_str))

                        if len(colors) % 3 != 0:
                            _LOGGER.warning("Number of color values (%d) is not a multiple of 3 in URL: %s. Adjustment might be incorrect for some colors.", len(colors), url)

                        adjusted_colors = [max(0, min(int(round(value * brightness_factor)), 255)) for value in colors]
                        adjusted_colors_str = ','.join(map(str, adjusted_colors))
                        query_params['colors'] = [adjusted_colors_str]

                    except ValueError:
                        _LOGGER.error("Invalid non-integer color value found in colors parameter '%s' for URL: %s. Cannot adjust brightness.", colors_str, url)
                        return url
                else:
                    _LOGGER.debug("Colors parameter is present but empty in URL: %s. Cannot adjust brightness.", url)
                    return url
            else:
                _LOGGER.debug("No 'colors' parameter to adjust in URL: %s", url)
                return url

            new_query = urllib.parse.urlencode(query_params, doseq=True)
            new_url = urllib.parse.urlunparse(
                (scheme, netloc, path, params, new_query, fragment)
            )
            return new_url

        except Exception as e:
            _LOGGER.exception("Error adjusting colors in URL '%s': %s", url, e)
            return url