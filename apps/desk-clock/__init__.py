"""Location-aware desk clock for Mona-OS v4.03."""

import gc
import json
import math
import sys
import time

import network
import ntptime
from badgeware import State, io, brushes, shapes, screen, PixelFont, run
from urllib.urequest import urlopen


SMALL_FONT = PixelFont.load("/system/assets/fonts/ark.ppf")
LARGE_FONT = PixelFont.load("/system/assets/fonts/absolute.ppf")

BACKGROUND = brushes.color(7, 12, 24)
PANEL = brushes.color(14, 27, 47)
WHITE = brushes.color(244, 248, 255)
MUTED = brushes.color(128, 151, 180)
BLUE = brushes.color(88, 166, 255)
PURPLE = brushes.color(163, 113, 247)
AMBER = brushes.color(242, 204, 96)
RED = brushes.color(248, 81, 73)

WIFI_SSID = None
WIFI_PASSWORD = None
wlan = None
wifi_state = "waiting"
view_mode = "digital"
use_24_hour = True
location_name = "LOCAL TIME"
timezone_name = ""
utc_offset = 0
last_sync_ticks = None
sync_stage = 0
next_sync_ticks = 900
error_message = ""


def center_text(value, y):
    width, _ = screen.measure_text(value)
    screen.text(value, int(80 - width / 2), y)


def load_settings():
    global view_mode, use_24_hour, location_name, timezone_name, utc_offset
    saved = {
        "view": "digital",
        "24_hour": True,
        "location": "LOCAL TIME",
        "timezone": "",
        "offset": 0,
    }
    try:
        if State.load("desk_clock", saved):
            if saved.get("view") in ("digital", "analog"):
                view_mode = saved["view"]
            use_24_hour = bool(saved.get("24_hour", True))
            location_name = str(saved.get("location", "LOCAL TIME"))[:20]
            timezone_name = str(saved.get("timezone", ""))[:24]
            utc_offset = int(saved.get("offset", 0))
    except Exception:
        pass


def save_settings():
    try:
        State.save("desk_clock", {
            "view": view_mode,
            "24_hour": use_24_hour,
            "location": location_name,
            "timezone": timezone_name,
            "offset": utc_offset,
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
    global wlan, wifi_state
    if not load_wifi_credentials():
        wifi_state = "missing Wi-Fi config"
        return False
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        try:
            wlan.config(pm=0xA11140)
        except Exception:
            pass
        if not wlan.isconnected():
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        wifi_state = "connecting"
    if wlan.isconnected():
        wifi_state = "online"
        return True
    return False


def fetch_location_and_time():
    global location_name, timezone_name, utc_offset
    global last_sync_ticks, error_message, wifi_state
    response = None
    try:
        response = urlopen(
            "https://worldtimeapi.org/api/ip",
            headers={"User-Agent": "MonaBadge/4.03"},
            timeout=6,
        )
        result = json.load(response)
        location_name = str(result.get("timezone", "LOCAL")).split("/")[-1]
        location_name = location_name.replace("_", " ")[:20].upper()
        timezone_name = str(result.get("abbreviation", ""))[:8]
        utc_offset = int(result.get("raw_offset", 0))
        utc_offset += int(result.get("dst_offset", 0))
        try:
            ntptime.settime()
        except Exception:
            pass
        last_sync_ticks = io.ticks
        error_message = ""
        wifi_state = "synced"
        save_settings()
    except Exception as exc:
        error_message = "SYNC FAILED"
        wifi_state = "offline"
        print("Clock sync error:", exc)
    finally:
        try:
            if response:
                response.close()
        except Exception:
            pass
        gc.collect()


def local_time():
    try:
        return time.localtime(time.time() + utc_offset)
    except Exception:
        return time.localtime()


def draw_header(now):
    screen.font = SMALL_FONT
    screen.brush = MUTED
    screen.text(location_name, 6, 5)
    if timezone_name:
        width, _ = screen.measure_text(timezone_name)
        screen.text(timezone_name, 154 - width, 5)
    screen.brush = PANEL
    screen.draw(shapes.line(6, 17, 154, 17, 1))


def formatted_hour(hour):
    if use_24_hour:
        return hour, ""
    marker = "AM" if hour < 12 else "PM"
    hour = hour % 12
    return (12 if hour == 0 else hour), marker


def draw_digital(now):
    hour, marker = formatted_hour(now[3])
    clock_text = "%02d:%02d" % (hour, now[4])
    if io.ticks % 1000 >= 500:
        clock_text = "%02d %02d" % (hour, now[4])
    screen.font = LARGE_FONT
    screen.brush = WHITE
    center_text(clock_text, 34)
    screen.font = SMALL_FONT
    if marker:
        screen.brush = BLUE
        center_text(marker, 65)
    weekdays = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
    date_text = "%s  %02d/%02d/%04d" % (
        weekdays[now[6]], now[2], now[1], now[0]
    )
    screen.brush = MUTED
    center_text(date_text, 80)
    screen.brush = BLUE
    progress = int((now[4] * 60 + now[5]) * 148 / 3600)
    screen.draw(shapes.rounded_rectangle(6, 96, progress, 4, 2))


def hand_point(cx, cy, length, value, maximum):
    angle = (value / maximum) * math.pi * 2 - math.pi / 2
    return (
        int(cx + math.cos(angle) * length),
        int(cy + math.sin(angle) * length),
    )


def draw_analog(now):
    cx, cy, radius = 80, 65, 40
    screen.brush = PANEL
    screen.draw(shapes.circle(cx, cy, radius))
    screen.brush = BLUE
    screen.draw(shapes.circle(cx, cy, radius).stroke(2))
    for number in range(12):
        outer = hand_point(cx, cy, 35, number, 12)
        inner = hand_point(cx, cy, 31 if number % 3 else 29, number, 12)
        screen.brush = WHITE if number % 3 == 0 else MUTED
        screen.draw(shapes.line(inner[0], inner[1], outer[0], outer[1], 1))
    hour_value = (now[3] % 12) + now[4] / 60
    hour_end = hand_point(cx, cy, 20, hour_value, 12)
    minute_end = hand_point(cx, cy, 29, now[4] + now[5] / 60, 60)
    second_end = hand_point(cx, cy, 32, now[5], 60)
    screen.brush = WHITE
    screen.draw(shapes.line(cx, cy, hour_end[0], hour_end[1], 4))
    screen.brush = BLUE
    screen.draw(shapes.line(cx, cy, minute_end[0], minute_end[1], 2))
    screen.brush = RED
    screen.draw(shapes.line(cx, cy, second_end[0], second_end[1], 1))
    screen.draw(shapes.circle(cx, cy, 2))


def draw_footer():
    screen.font = SMALL_FONT
    message = error_message
    color = RED
    if not message:
        if wifi_state in ("online", "synced"):
            message = "A VIEW   B 12/24   C SYNC"
            color = MUTED
        elif wifi_state == "connecting":
            message = "CONNECTING TO WI-FI..."
            color = AMBER
        elif wifi_state == "missing Wi-Fi config":
            message = "ADD WI-FI TO SECRETS.PY"
            color = AMBER
        else:
            message = "USING SAVED LOCATION"
            color = MUTED
    screen.brush = color
    center_text(message, 108)


def update():
    global view_mode, use_24_hour, sync_stage, next_sync_ticks
    global error_message
    if io.BUTTON_A in io.pressed:
        view_mode = "analog" if view_mode == "digital" else "digital"
        save_settings()
    if io.BUTTON_B in io.pressed:
        use_24_hour = not use_24_hour
        save_settings()
    if io.BUTTON_C in io.pressed:
        error_message = ""
        sync_stage = 1

    online = service_wifi()
    if online and io.ticks >= next_sync_ticks and sync_stage == 0:
        sync_stage = 1
    if sync_stage == 1:
        # Render one complete frame before the HTTPS request can block.
        sync_stage = 2
    elif sync_stage == 2:
        fetch_location_and_time()
        next_sync_ticks = io.ticks + 6 * 60 * 60 * 1000
        sync_stage = 0

    now = local_time()
    screen.brush = BACKGROUND
    screen.clear()
    draw_header(now)
    if view_mode == "analog":
        draw_analog(now)
    else:
        draw_digital(now)
    draw_footer()
    time.sleep_ms(80)


def init():
    load_settings()


def on_exit():
    save_settings()
    gc.collect()


init()
run(update)
