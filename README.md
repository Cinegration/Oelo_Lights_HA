# Oelo Lights Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

## Overview

This custom integration allows you to control your Oelo Lights system directly from Home Assistant. It supports multi-zone control, effects, color, and brightness, and is optimized for performance and reliability.

---

## Features

- Control up to 6 Oelo light zones individually
- Set color, brightness, and effects per zone
- Effect list with dozens of built-in patterns
- Optimized polling (single request for all zones)
- Handles device availability and offline detection
- Debounced command sending to prevent overload
- Home Assistant native config flow (UI setup)
- Supports Home Assistant scenes, automations, and scripts

---

## Installation

### HACS (Recommended)

1. Go to **HACS > Integrations** in Home Assistant.
2. Click the three dots (upper right) > **Custom repositories**.
3. Add your repository URL (e.g., `https://github.com/Cinegration/Oelo_Lights_HA`) and select **Integration**.
4. Search for **Oelo Lights** in HACS and install.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/oelo_lights` folder to your Home Assistant `custom_components` directory.
2. Restart Home Assistant.

---

## Configuration

1. Go to **Settings > Devices & Services**.
2. Click **Add Integration** and search for **Oelo Lights**.
3. Enter the IP address of your Oelo controller.
4. The integration will automatically create 6 light entities (one for each zone).

---

## Usage

- Each zone appears as a separate light entity in Home Assistant.
- You can control color, brightness, and effects from the UI, automations, or scripts.
- All standard Home Assistant light features are supported.

---

## Troubleshooting

- If your lights show as "Unavailable," check that your Oelo controller is online and reachable from your Home Assistant network.
- Ensure the IP address is correct in the integration settings.
- Check Home Assistant logs for detailed error messages.

---

## Advanced

- **Custom Effects:** The integration supports an extensive list of offical Oelo patterns.
- **Debounced Commands:** Rapid changes (e.g., from automations) are automatically buffered to avoid overloading the controller.
- **Shared Polling:** Only one HTTP request is made per poll interval, regardless of the number of zones.

---

## Contributing

Pull requests, bug reports, and feature requests are welcome!  
Please open an issue or PR on [GitHub](https://github.com/Cinegration/Oelo_Lights_HA).

---

## License

MIT License

---

## Credits

- [Oelo Lighting Solutions](https://oelo.com/)
- [Home Assistant](https://www.home-assistant.io/)
