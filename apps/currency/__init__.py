"""Fast-starting currency board for Mona-OS v4.03."""

import gc
import json
import sys
import time

import network
from badgeware import State, io, brushes, shapes, screen, PixelFont, run
from urllib.urequest import urlopen


SMALL_FONT = PixelFont.load("/system/assets/fonts/ark.ppf")
LARGE_FONT = PixelFont.load("/system/assets/fonts/compass.ppf")

BACKGROUND = brushes.color(7, 12, 24)
PANEL = brushes.color(14, 27, 47)
WHITE = brushes.color(244, 248, 255)
MUTED = brushes.color(128, 151, 180)
GREEN = brushes.color(63, 185, 80)
BLUE = brushes.color(88, 166, 255)
AMBER = brushes.color(242, 204, 96)
RED = brushes.color(248, 81, 73)

API_URL = (
    "https://api.frankfurter.dev/v1/latest"
    "?base=USD&symbols=GBP,INR"
)
REFRESH_INTERVAL_MS = 30 * 60 * 1000
WIFI_RETRY_MS = 15000

WIFI_SSID = None
WIFI_PASSWORD = None
wlan = None
rates = {}
rate_date = ""
last_update_ticks = None
next_fetch_ticks = 800
next_wifi_attempt = 0
fetch_stage = 0
status_message = "STARTING..."


def center_text(value, y):
    width, _ = screen.measure_text(value)
    screen.text(value, int(80 - width / 2), y)


def load_cache():
    global rates, rate_date
    saved = {"GBP": 0, "INR": 0, "date": ""}
    try:
        if State.load("currency_rates", saved):
            if saved.get("GBP") and saved.get("INR"):
                rates = {
                    "GBP": float(saved["GBP"]),
                    "INR": float(saved["INR"]),
                }
                rate_date = str(saved.get("date", ""))[:12]
    except Exception:
        rates = {}


def save_cache():
    if not rates:
        return
    try:
        State.save("currency_rates", {
            "GBP": rates["GBP"],
            "INR": rates["INR"],
            "date": rate_date,
        })
    except Exception:
        pass


def load_wifi_credentials():
    global WIFI_SSID, WIFI_PASSWORD
    if WIFI_SSID is not None:
        return bool(WIFI_SSID)
    try:
        sys.path.insert(0, "/")
        from secrets import WIFI_SSID as ssid, WIFI_PASSWORD as password
        WIFI_SSID = ssid
        WIFI_PASSWORD = password
        sys.path.pop(0)
    except Exception:
        WIFI_SSID = ""
        WIFI_PASSWORD = ""
    return bool(WIFI_SSID)


def service_wifi():
    global wlan, next_wifi_attempt, status_message
    if not load_wifi_credentials():
        status_message = "ADD WI-FI TO SECRETS.PY"
        return False
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        try:
            wlan.config(pm=0xA11140)
        except Exception:
            pass
    if wlan.isconnected():
        return True
    if io.ticks >= next_wifi_attempt:
        next_wifi_attempt = io.ticks + WIFI_RETRY_MS
        try:
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        except Exception:
            pass
    status_message = "CONNECTING TO WI-FI..."
    return False


def fetch_rates():
    global rates, rate_date, last_update_ticks, status_message
    response = None
    try:
        response = urlopen(
            API_URL,
            headers={"User-Agent": "MonaBadge/4.03"},
            timeout=6,
        )
        payload = json.load(response)
        fresh = payload.get("rates", {})
        rates = {
            "GBP": float(fresh["GBP"]),
            "INR": float(fresh["INR"]),
        }
        rate_date = str(payload.get("date", ""))[:12]
        last_update_ticks = io.ticks
        status_message = "LIVE RATES"
        save_cache()
    except Exception as exc:
        status_message = "UPDATE FAILED - C TO RETRY"
        print("Currency update error:", exc)
    finally:
        try:
            if response:
                response.close()
        except Exception:
            pass
        gc.collect()


def draw_rate(code, value, y, color):
    screen.brush = PANEL
    screen.draw(shapes.rounded_rectangle(7, y, 146, 32, 5))
    screen.font = SMALL_FONT
    screen.brush = MUTED
    screen.text("1 USD", 14, y + 6)
    screen.brush = color
    screen.text(code, 130, y + 6)
    display = "--" if value is None else (
        "%.4f" % value if value < 10 else "%.2f" % value
    )
    screen.font = LARGE_FONT
    screen.brush = WHITE
    screen.text(display, 14, y + 14)


def draw_screen():
    screen.brush = BACKGROUND
    screen.clear()
    screen.font = SMALL_FONT
    screen.brush = WHITE
    screen.text("CURRENCY BOARD", 7, 6)
    screen.brush = BLUE
    screen.draw(shapes.circle(148, 10, 3))
    draw_rate("GBP", rates.get("GBP"), 22, BLUE)
    draw_rate("INR", rates.get("INR"), 58, GREEN)
    screen.font = SMALL_FONT
    if fetch_stage:
        footer = "UPDATING RATES..."
        color = AMBER
    elif status_message:
        footer = status_message
        color = RED if "FAILED" in footer else MUTED
    else:
        footer = "C REFRESH"
        color = MUTED
    if rate_date and not fetch_stage and "FAILED" not in footer:
        footer = "RATE DATE " + rate_date + "   C REFRESH"
    screen.brush = color
    center_text(footer, 101)


def update():
    global fetch_stage, next_fetch_ticks, status_message
    if io.BUTTON_C in io.pressed:
        fetch_stage = 1
        status_message = ""

    online = service_wifi()
    if online and io.ticks >= next_fetch_ticks and fetch_stage == 0:
        fetch_stage = 1

    if fetch_stage == 1:
        # Show cached data and a loading state before starting HTTPS.
        fetch_stage = 2
    elif fetch_stage == 2:
        fetch_rates()
        next_fetch_ticks = io.ticks + REFRESH_INTERVAL_MS
        fetch_stage = 0

    draw_screen()
    time.sleep_ms(100)


def init():
    global status_message
    load_cache()
    status_message = "CACHED - CONNECTING..." if rates else "CONNECTING..."


def on_exit():
    save_cache()
    gc.collect()


init()
run(update)
