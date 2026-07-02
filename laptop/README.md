# Work Status Badge Controller

This folder contains the Windows controller for the **Work Status** badge app.
It uses only Python's standard library.

## First-time setup

1. Copy this entire `laptop` folder from the badge drive to your laptop.
2. Start **Work Status** from the badge's app menu.
3. Press **C** on the badge to reveal its address, such as
   `192.168.1.42:8080`, for eight seconds.
4. Double-click `Work Status.pyw` to launch without a terminal window.
   `run_work_status.bat` remains available for troubleshooting.
5. Enter a profile name and the address shown on the badge, then select
   **Save Badge** and **Connect**.

The controller remembers multiple named badges in `badge_profiles.json` beside
the Python script, so copying the `laptop` folder also copies its profiles.
Select **New Badge**, enter a unique name and address, then select **Save
Badge**. Saving a new name keeps all existing badges. Use the dropdown to
switch between every saved badge, and **Show IP**/**Hide IP** to control
whether the selected address is visible. **Forget** removes only the selected
profile. Older profiles stored in
`%USERPROFILE%\.work_status_badge.json` are imported automatically when the
local profile file is absent. For the most reliable setup, reserve each badge's
IP address in your router's DHCP settings.

The connected badge's battery percentage appears in the controller header and
refreshes automatically every minute. Charging state is shown beside it.

## Use

Enter an optional short note, then press **Available**, **In a meeting**,
**Focus**, **Away**, **Lunch**, or **Sleep**. Lunch displays an animated curry
bowl. The badge changes immediately. Button A on the badge cycles through the
six presets and the last custom status, clearing the note when a preset is
selected. A bright border
flashes when a remote update arrives.

The large **Create Your Own Status** panel is the fifth option. Choose an emoji
(star, heart, check, alert, coffee, door, coding or bolt), pick a colour, enter
up to 24 characters, review the live preview, and select **Send My Custom
Status**. Custom settings are saved in `badge_profiles.json` and restored the
next time the controller opens. The badge renders a matching full-screen vector
symbol because its pixel fonts do not contain Unicode emoji glyphs.

The badge uses the full screen for the current symbol and text:

- Press **A** to cycle through the preset statuses and last custom design.
- Press **B twice quickly** to enter Mona-OS hardware sleep. The display,
  lighting and Wi-Fi turn off, so remote updates are unavailable while asleep.
  Press RESET to wake reliably.
- Press **C** to show the temporary network-address screen.

Both devices must be on the same local Wi-Fi network. The API has no password,
so any device on that trusted network can update the sign.

## HTTP API

The badge listens on port `8080` while the Work Status app is open:

```text
GET  /api/status
POST /api/status
Content-Type: application/json

{"status":"meeting","note":"Back at 3pm"}
```

Valid preset status values are `available`, `meeting`, `focus`, `away`,
`lunch`, and `sleep`. Notes are limited to 24 printable characters.

Custom requests use:

```json
{
  "status": "custom",
  "custom_text": "LUNCH TIME",
  "custom_symbol": "coffee",
  "custom_color": "#F0883E"
}
```

## Troubleshooting

- **Cannot reach badge:** confirm the Work Status app is open and use the exact
  address revealed by pressing C.
- **WiFi: missing config:** set `WIFI_SSID` and `WIFI_PASSWORD` in the badge's
  root `secrets.py`.
- **Address changed:** reconnect using the new address shown on the badge.
- Some guest Wi-Fi networks block device-to-device traffic. Use the main home
  network or disable client isolation.

The badge has no onboard speaker or buzzer, so it cannot produce an audible
notification without extra hardware. A small active buzzer can be added using
the exposed GPIO pads; the built-in app uses a visual flash instead.
