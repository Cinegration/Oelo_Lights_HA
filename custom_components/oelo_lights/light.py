from __future__ import annotations

import logging
import asyncio
import json
from json import JSONDecodeError
import voluptuous as vol
import aiohttp
import async_timeout
import re
import urllib.parse
from typing import Any

_LOGGER = logging.getLogger(__name__)

try:
    from .patterns import pattern_commands
    from .const import DOMAIN
except ImportError:
    try:
        from patterns import pattern_commands
    except ImportError:
        pattern_commands = {"Solid White": "http://{ip}/setPattern?patternType=custom&zones={zone}&num_zones=1&num_colors=1&colors=255,255,255&direction=F&speed=0&gap=0&other=0&pause=0"}
        _LOGGER.warning("Could not import patterns.py, using default Solid White pattern.")
    try:
        from const import DOMAIN
    except ImportError:
        DOMAIN = "oelo_lights"
        _LOGGER.warning("Could not import const.py, using default DOMAIN 'oelo_lights'.")


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
from homeassistant.exceptions import NoEntitySpecifiedError

from datetime import timedelta
SCAN_INTERVAL = timedelta(seconds=30)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    ip_address = entry.data[CONF_IP_ADDRESS]
    session = aiohttp_client.async_get_clientsession(hass)

    light_entities = []

    for zone in range(1, 7):
        light_entity = OeloLight(session, ip_address, zone, entry)
        light_entities.append(light_entity)

    async_add_entities(light_entities, True)

class OeloLight(LightEntity, RestoreEntity):

    _attr_has_entity_name = True
    _attr_should_poll = True

    def __init__(
        self,
        session: aiohttp.ClientSession,
        ip: str,
        zone: int,
        entry: ConfigEntry
    ) -> None:
        self._session = session
        self._ip = ip
        self._zone = zone
        self._entry = entry
        self._state = False
        self._brightness: int | None = 255
        self._rgb_color: tuple[int, int, int] | None = (255, 255, 255)
        self._intended_effect: str | None = None
        self._last_successful_command: str | None = None

        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB
        self._attr_supported_features = LightEntityFeature.EFFECT
        self._attr_unique_id = f"{entry.entry_id}_zone_{self._zone}"
        self._attr_name = f"Zone {zone}"
        self._attr_available = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="Oelo",
            model="Light Controller",
            configuration_url=f"http://{self._ip}/",
        )

    @property
    def available(self) -> bool:
        return self._attr_available

    @property
    def is_on(self) -> bool | None:
        return self._state if self.available else None

    @property
    def brightness(self) -> int | None:
        return self._brightness if self.available else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._rgb_color if self.available else None

    @property
    def effect(self) -> str | None:
        if not self.available:
            return None
        return self._intended_effect if self.is_on else None

    @property
    def effect_list(self) -> list[str] | None:
        return list(pattern_commands.keys()) if self.available else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            if last_state.state == STATE_ON:
                self._state = True
                self._brightness = last_state.attributes.get(ATTR_BRIGHTNESS, 255)
                self._intended_effect = last_state.attributes.get(ATTR_EFFECT)
                rgb_color = last_state.attributes.get(ATTR_RGB_COLOR)
                if rgb_color is not None and isinstance(rgb_color, (list, tuple)) and len(rgb_color) == 3:
                     try:
                        self._rgb_color = tuple(int(c) for c in rgb_color) 
                     except (ValueError, TypeError):
                         _LOGGER.warning("%s: Invalid RGB color %s restored from state, using default.", self.entity_id or self._attr_name, rgb_color)
                         self._rgb_color = (255, 255, 255)
            else:
                self._state = False
                self._brightness = last_state.attributes.get(ATTR_BRIGHTNESS, 255)
                self._intended_effect = last_state.attributes.get(ATTR_EFFECT)
                rgb_color = last_state.attributes.get(ATTR_RGB_COLOR)
                if rgb_color is not None and isinstance(rgb_color, (list, tuple)) and len(rgb_color) == 3:
                    try:
                        self._rgb_color = tuple(int(c) for c in rgb_color) 
                    except (ValueError, TypeError):
                        self._rgb_color = (255, 255, 255)

            _LOGGER.debug("%s: Restored state: On=%s, Brightness=%s, Effect=%s, RGB=%s",
                          self.entity_id or self._attr_name, self._state, self._brightness, self._intended_effect, self._rgb_color)


    async def async_update(self) -> None:
        log_prefix = self.entity_id or self._attr_name
        _LOGGER.debug("%s: Updating state and availability", log_prefix)
        url = f"http://{self._ip}/getController"
        new_availability = False
        new_state = self._state
        current_pattern = None

        try:
            async with async_timeout.timeout(10):
                if self._session is None or self._session.closed:
                     _LOGGER.warning("%s: HTTP session closed or invalid during update.", log_prefix)
                     return

                async with self._session.get(url) as response:
                    response.raise_for_status()
                    try:
                        data = await response.json(content_type=None)
                        _LOGGER.debug("%s: getController raw response: %s", log_prefix, data)
                    except (aiohttp.ContentTypeError, ValueError, JSONDecodeError) as json_err:
                         resp_text = await response.text()
                         _LOGGER.warning("%s: Invalid JSON received from getController: %s. Response text: %s",
                                         log_prefix, json_err, resp_text[:100])
                         new_availability = True
                         return

                    new_availability = True

                    if not isinstance(data, list):
                        _LOGGER.warning("%s: Expected a list from getController, but received type %s. Data: %s",
                                        log_prefix, type(data).__name__, str(data)[:100])
                        return

                    zone_data = None
                    for item in data:
                        if isinstance(item, dict) and item.get("num") == self._zone:
                            zone_data = item
                            break

                    if not zone_data:
                        _LOGGER.warning("%s: Data for this zone number not found in getController response list.", log_prefix)
                        return

                    current_pattern = zone_data.get("pattern")

                    if current_pattern is None:
                        _LOGGER.warning("%s: 'pattern' key is missing in the zone data: %s", log_prefix, zone_data)
                        return

                    is_actually_on = current_pattern != "off"
                    new_state = is_actually_on

        except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as conn_err:
            log_level = logging.WARNING if self._attr_available else logging.DEBUG
            _LOGGER.log(log_level, "%s: Connection error during update: %s", log_prefix, conn_err)
            new_availability = False
        except aiohttp.ClientResponseError as resp_err:
            log_level = logging.WARNING if self._attr_available else logging.DEBUG
            _LOGGER.log(log_level, "%s: HTTP error %s during update: %s", log_prefix, resp_err.status, resp_err.message)
            new_availability = False
        except aiohttp.ClientError as client_err:
            log_level = logging.WARNING if self._attr_available else logging.DEBUG
            _LOGGER.log(log_level, "%s: Client error during update: %s", log_prefix, client_err)
            new_availability = False
        except Exception as err:
            log_level = logging.ERROR if self._attr_available else logging.DEBUG
            _LOGGER.log(log_level, "%s: Unexpected error during update: %s", log_prefix, err, exc_info=True)
            new_availability = False
        finally:
            can_write_state = self.hass is not None and self.entity_id is not None

            state_changed = self._state != new_state
            availability_changed = self._attr_available != new_availability

            if availability_changed:
                 _LOGGER.info("%s: Availability changed: %s -> %s",
                              log_prefix, "was available" if self._attr_available else "was unavailable",
                              "now available" if new_availability else "now unavailable")
                 self._attr_available = new_availability

            if self._attr_available and state_changed:
                _LOGGER.info("%s: State change detected via polling: %s -> %s (Pattern: '%s')",
                             log_prefix, "On" if self._state else "Off", "On" if new_state else "Off", current_pattern)
                self._state = new_state

            if can_write_state and (availability_changed or (self._attr_available and state_changed)):
                _LOGGER.debug("%s: Writing state change to HA (Available: %s, State: %s)",
                              log_prefix, self._attr_available, "On" if self._state else "Off")
                self.async_write_ha_state()
            elif not can_write_state and (availability_changed or state_changed):
                _LOGGER.debug("%s: Change detected during initial setup (Available: %s, State: %s). State write deferred until entity is fully added.",
                              log_prefix, new_availability, "On" if new_state else "Off")
            elif not self._attr_available and not availability_changed:
                 _LOGGER.debug("%s: Still unavailable.", log_prefix)
            elif can_write_state:
                 _LOGGER.debug("%s: No change detected during update.", log_prefix)


    async def async_turn_on(self, **kwargs: Any) -> None:
        log_prefix = self.entity_id or self._attr_name
        _LOGGER.debug("%s: Turning on with kwargs: %s", log_prefix, kwargs)

        if not self._attr_available:
             _LOGGER.warning("%s: Cannot turn on: Controller is unavailable.", log_prefix)
             return

        url_to_send: str | None = None
        effect_to_set: str | None = self._intended_effect
        rgb_to_set: tuple[int, int, int] | None = self._rgb_color
        brightness_to_set: int = self._brightness if self._brightness is not None else 255

        triggered_by_effect = False

        if ATTR_BRIGHTNESS in kwargs:
            brightness_to_set = kwargs[ATTR_BRIGHTNESS]
            _LOGGER.debug("%s: Brightness specified: %d", log_prefix, brightness_to_set)

        brightness_to_set = max(0, min(brightness_to_set, 255))
        brightness_factor = brightness_to_set / 255.0

        if ATTR_RGB_COLOR in kwargs:
            _LOGGER.debug("%s: RGB color specified: %s", log_prefix, kwargs[ATTR_RGB_COLOR])
            rgb_to_set = kwargs[ATTR_RGB_COLOR]
            effect_to_set = None
            triggered_by_effect = False
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

        elif ATTR_EFFECT in kwargs:
            selected_effect = kwargs[ATTR_EFFECT]
            _LOGGER.debug("%s: Effect specified: %s", log_prefix, selected_effect)
            if selected_effect in pattern_commands:
                effect_to_set = selected_effect
                triggered_by_effect = True
                base_command_url_unparsed = self._get_base_effect_url(selected_effect)
                if base_command_url_unparsed:
                    self._last_successful_command = base_command_url_unparsed
                    extracted_rgb = self._extract_first_color_from_url(base_command_url_unparsed)
                    if extracted_rgb:
                        rgb_to_set = extracted_rgb
                    url_to_send = self._adjust_colors_in_url(base_command_url_unparsed, brightness_factor)
                else:
                    _LOGGER.error("%s: Could not get base URL for effect '%s'", log_prefix, selected_effect)
                    return
            else:
                _LOGGER.error("%s: Invalid effect selected: '%s'. Valid effects: %s",
                              log_prefix, selected_effect, list(pattern_commands.keys()))
                return

        elif not self._state or ATTR_BRIGHTNESS in kwargs:
            _LOGGER.debug("%s: Turning on from off state or adjusting brightness only.", log_prefix)
            base_url_for_on: str | None = None

            if effect_to_set and triggered_by_effect == False:
                _LOGGER.debug("%s: Replaying intended effect '%s'", log_prefix, effect_to_set)
                base_url_for_on = self._get_base_effect_url(effect_to_set)
                if base_url_for_on:
                    self._last_successful_command = base_url_for_on
                    extracted_rgb = self._extract_first_color_from_url(base_url_for_on)
                    if extracted_rgb: rgb_to_set = extracted_rgb
                else:
                     _LOGGER.warning("%s: Failed to get base URL for stored effect '%s'.", log_prefix, effect_to_set)
                     effect_to_set = None
                     rgb_to_set = (255, 255, 255)

            elif self._last_successful_command:
                 _LOGGER.debug("%s: Replaying last successful command.", log_prefix)
                 base_url_for_on = self._last_successful_command
                 parsed_command = urllib.parse.urlparse(base_url_for_on)
                 command_params = urllib.parse.parse_qs(parsed_command.query)
                 last_pattern = command_params.get("patternType", [""])[0]
                 if last_pattern == "custom":
                      extracted_rgb = self._extract_first_color_from_url(base_url_for_on)
                      if extracted_rgb: rgb_to_set = extracted_rgb
                      effect_to_set = None
                 elif last_pattern != "off":
                     found_effect = False
                     for name, cmd_url in pattern_commands.items():
                         try:
                             parsed_cmd_url = urllib.parse.urlparse(cmd_url)
                             cmd_params = urllib.parse.parse_qs(parsed_cmd_url.query)
                             if cmd_params.get("patternType", [""])[0] == last_pattern:
                                 effect_to_set = name
                                 extracted_rgb = self._extract_first_color_from_url(base_url_for_on)
                                 if extracted_rgb: rgb_to_set = extracted_rgb
                                 found_effect = True
                                 break
                         except Exception as e:
                              _LOGGER.warning("%s: Error parsing pattern command URL '%s' while checking last command: %s", log_prefix, cmd_url, e)
                              continue
                     if not found_effect:
                          effect_to_set = None
                          extracted_rgb = self._extract_first_color_from_url(base_url_for_on)
                          if extracted_rgb: rgb_to_set = extracted_rgb
                          else: rgb_to_set = (255,255,255)

            if base_url_for_on:
                url_to_send = self._adjust_colors_in_url(base_url_for_on, brightness_factor)
            else:
                 _LOGGER.debug("%s: No previous state known, falling back to default white.", log_prefix)
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
             _LOGGER.debug("%s: Turn on called, but no state change needed or URL not constructed.", log_prefix)
             success = self._state and self._attr_available

        if success:
            self._state = True
            self._brightness = brightness_to_set
            self._rgb_color = rgb_to_set
            self._intended_effect = effect_to_set
            self._attr_color_mode = ColorMode.RGB
            if not self._attr_available:
                 _LOGGER.info("%s: Marking available after successful turn_on command", log_prefix)
                 self._attr_available = True
        else:
             _LOGGER.error("%s: Failed to execute turn_on command.", log_prefix)
             if self._attr_available:
                 _LOGGER.warning("%s: Marking unavailable after failed turn_on command", log_prefix)
                 self._attr_available = False

        if self.hass is not None and self.entity_id is not None:
            self.async_write_ha_state()
        else:
            _LOGGER.debug("%s: State updated internally, write deferred until entity is added.", log_prefix)


    async def async_turn_off(self, **kwargs: Any) -> None:
        log_prefix = self.entity_id or self._attr_name
        _LOGGER.debug("%s: Turning off", log_prefix)

        if not self._attr_available and self._state:
             _LOGGER.warning("%s: Unavailable but current state is ON. Attempting turn off anyway.", log_prefix)
        elif not self._attr_available:
             _LOGGER.warning("%s: Cannot turn off: Controller is unavailable.", log_prefix)
             return
        elif not self._state:
             _LOGGER.debug("%s: Already off.", log_prefix)
             if not self._attr_available:
                 _LOGGER.info("%s: Marking available (turn_off called while already off)", log_prefix)
                 self._attr_available = True
                 if self.hass is not None and self.entity_id is not None:
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
                 _LOGGER.info("%s: Marking available after successful turn_off command", log_prefix)
                 self._attr_available = True
        else:
            _LOGGER.error("%s: Failed to turn off.", log_prefix)
            if self._attr_available:
                 _LOGGER.warning("%s: Marking unavailable after failed turn_off command", log_prefix)
                 self._attr_available = False

        if self.hass is not None and self.entity_id is not None:
            self.async_write_ha_state()
        else:
            _LOGGER.debug("%s: State updated internally, write deferred until entity is added.", log_prefix)


    def _get_base_effect_url(self, effect_name: str) -> str | None:
        log_prefix = self.entity_id or self._attr_name
        if effect_name not in pattern_commands:
            _LOGGER.error("%s: Effect '%s' not found in pattern_commands", log_prefix, effect_name)
            return None

        base_command_url_template = pattern_commands[effect_name]
        if not isinstance(base_command_url_template, str):
             _LOGGER.error("%s: Pattern command for effect '%s' is not a string: %s", log_prefix, effect_name, base_command_url_template)
             return None

        try:
            parsed_template = urllib.parse.urlparse(base_command_url_template)
            template_query = urllib.parse.parse_qs(parsed_template.query, keep_blank_values=True)

            template_query['zones'] = [str(self._zone)]
            template_query['num_zones'] = ['1']

            scheme = parsed_template.scheme if parsed_template.scheme else 'http'
            netloc = self._ip
            path = parsed_template.path if parsed_template.path else '/setPattern'

            final_query = urllib.parse.urlencode(template_query, doseq=True)
            final_url = urllib.parse.urlunparse(
                (scheme,
                 netloc,
                 path,
                 parsed_template.params,
                 final_query,
                 parsed_template.fragment)
            )
            _LOGGER.debug("%s: Constructed base URL for effect '%s': %s", log_prefix, effect_name, final_url)
            return final_url

        except Exception as e:
            _LOGGER.error("%s: Error building base URL for effect '%s' from template '%s': %s",
                          log_prefix, effect_name, base_command_url_template, e)
            return None

    def _extract_first_color_from_url(self, url: str) -> tuple[int, int, int] | None:
        log_prefix = self.entity_id or self._attr_name
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
                        _LOGGER.warning("%s: Not enough numeric color values (found %d) in 'colors=%s' for URL: %s",
                                        log_prefix, len(color_values_str), colors_str, url)
                else:
                    _LOGGER.debug("%s: Empty 'colors' param in URL: %s", log_prefix, url)
            else:
                _LOGGER.debug("%s: No 'colors' param in URL: %s", log_prefix, url)

        except ValueError as e:
                _LOGGER.error("%s: Invalid non-integer color value found parsing 'colors' from URL: %s. Error: %s", log_prefix, url, e)
        except Exception as e:
            _LOGGER.error("%s: Error parsing color from URL '%s': %s", log_prefix, url, e)

        return None

    async def _send_request(self, url: str) -> bool:
        log_prefix = self.entity_id or self._attr_name
        try:
            parsed_url = urllib.parse.urlparse(url)
            scheme = 'http'
            netloc = self._ip
            final_url = urllib.parse.urlunparse(parsed_url._replace(scheme=scheme, netloc=netloc))
        except Exception as e:
            _LOGGER.error("%s: Failed to parse or rebuild final URL from '%s': %s", log_prefix, url, e)
            return False

        _LOGGER.debug("%s: Sending request: %s", log_prefix, final_url)
        try:
            async with async_timeout.timeout(10):
                if self._session is None or self._session.closed:
                     _LOGGER.error("%s: HTTP session is closed or invalid when trying to send request.", log_prefix)
                     return False

                async with self._session.get(final_url) as response:
                    resp_text = await response.text()
                    response.raise_for_status()

                    if "Command Received" in resp_text:
                         _LOGGER.info("%s: Request successful (Status: %d, Response: '%s')", log_prefix, response.status, resp_text.strip())
                         return True
                    else:
                         _LOGGER.warning("%s: Request successful (Status: %d), but response text was unexpected: '%s'", log_prefix, response.status, resp_text.strip())
                         return True

        except asyncio.TimeoutError:
            _LOGGER.error("%s: Request timed out calling: %s", log_prefix, final_url)
            return False
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("%s: HTTP request failed: %s (URL: %s)", log_prefix, str(err), final_url)
            return False
        except aiohttp.ClientConnectionError as err:
             _LOGGER.error("%s: HTTP connection failed: %s (URL: %s)", log_prefix, err, final_url)
             return False
        except aiohttp.ClientError as err:
            _LOGGER.error("%s: HTTP client error: %s (URL: %s)", log_prefix, err, final_url)
            return False
        except Exception as err:
            _LOGGER.exception("%s: An unexpected error occurred during request: %s (URL: %s)", log_prefix, err, final_url, exc_info=True)
            return False

    def _adjust_colors_in_url(self, url: str, brightness_factor: float) -> str:
        log_prefix = self.entity_id or self._attr_name
        if not url:
            _LOGGER.warning("%s: Attempted to adjust colors in an empty URL.", log_prefix)
            return ""
        if not (0.0 <= brightness_factor <= 1.0):
             _LOGGER.warning("%s: Brightness factor %.2f out of range [0.0, 1.0]. Clamping.", log_prefix, brightness_factor)
             brightness_factor = max(0.0, min(brightness_factor, 1.0))

        try:
            parsed_url = urllib.parse.urlparse(url)
            scheme = 'http'
            netloc = self._ip
            path = parsed_url.path
            params = parsed_url.params
            fragment = parsed_url.fragment

            query_params = urllib.parse.parse_qs(parsed_url.query, keep_blank_values=True)

            if 'colors' in query_params:
                colors_str = query_params['colors'][0]
                if colors_str:
                    color_values_str = [c.strip() for c in colors_str.split(',') if c.strip().isdigit()]
                    if not color_values_str:
                        _LOGGER.warning("%s: Colors parameter '%s' in URL '%s' contained no numeric values. Cannot adjust brightness.", log_prefix, colors_str, url)
                        new_query = urllib.parse.urlencode(query_params, doseq=True)
                        return urllib.parse.urlunparse((scheme, netloc, path, params, new_query, fragment))


                    try:
                        original_colors = list(map(int, color_values_str))

                        if len(original_colors) % 3 != 0:
                            _LOGGER.warning("%s: Number of color values (%d) in URL '%s' is not a multiple of 3. Brightness adjustment might be applied unevenly.", log_prefix, len(original_colors), url)

                        adjusted_colors = [max(0, min(int(round(value * brightness_factor)), 255)) for value in original_colors]

                        adjusted_colors_str = ','.join(map(str, adjusted_colors))

                        query_params['colors'] = [adjusted_colors_str]

                    except ValueError:
                        _LOGGER.error("%s: Invalid non-integer color value found during conversion in colors parameter '%s' for URL: %s. Cannot adjust brightness.", log_prefix, colors_str, url)
                        new_query = urllib.parse.urlencode(query_params, doseq=True)
                        return urllib.parse.urlunparse((scheme, netloc, path, params, new_query, fragment))
                else:
                    _LOGGER.debug("%s: Colors parameter is present but empty in URL: %s. Cannot adjust brightness.", log_prefix, url)
                    new_query = urllib.parse.urlencode(query_params, doseq=True)
                    return urllib.parse.urlunparse((scheme, netloc, path, params, new_query, fragment))

            else:
                _LOGGER.debug("%s: No 'colors' parameter to adjust in URL: %s", log_prefix, url)
                new_query = urllib.parse.urlencode(query_params, doseq=True)
                return urllib.parse.urlunparse((scheme, netloc, path, params, new_query, fragment))


            new_query = urllib.parse.urlencode(query_params, doseq=True)
            new_url = urllib.parse.urlunparse(
                (scheme, netloc, path, params, new_query, fragment)
            )
            _LOGGER.debug("%s: Adjusted URL colors for brightness %.2f: %s", log_prefix, brightness_factor, new_url)
            return new_url

        except Exception as e:
            _LOGGER.exception("%s: Error adjusting colors in URL '%s': %s", log_prefix, url, e, exc_info=True)
            try:
                 parsed_url = urllib.parse.urlparse(url)
                 return urllib.parse.urlunparse(parsed_url._replace(scheme='http', netloc=self._ip))
            except Exception:
                 return url