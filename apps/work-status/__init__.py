import gc
import binascii
import hashlib
import json
import os
import socket
import sys
import time

import network
from badgeware import (
    State,
    io,
    brushes,
    shapes,
    screen,
    PixelFont,
    get_battery_level,
    is_charging,
    run,
)


APP_DIR = "/system/apps/work-status"
SERVER_PORT = 8080
MAX_REQUEST_BYTES = 1024
CLIENT_TIMEOUT_MS = 1500
WIFI_RETRY_MS = 15000
FRAME_DELAY_MS = 100
SLEEP_STATUS_FRAME_DELAY_MS = 250
DOUBLE_B_WINDOW_MS = 700
PAIRING_TIMEOUT_MS = 30000
AUTH_NONCE_TIMEOUT_MS = 10000
MAX_TRUSTED_DEVICES = 8

STATUS_ORDER = (
    "available", "meeting", "focus", "away", "lunch", "sleep", "custom"
)
STATUS_LABELS = {
    "available": "I'M FREE",
    "meeting": "IN A MEETING",
    "focus": "DEEP WORK",
    "away": "BACK SOON",
    "lunch": "LUNCH BREAK",
    "sleep": "SLEEPING",
    "custom": "CUSTOM",
}
CUSTOM_SYMBOLS = (
    "star", "heart", "check", "alert", "coffee", "door", "code", "bolt"
)
X25519_PRIME = (1 << 255) - 19
X25519_A24 = 121665
X25519_BASE = b"\x09" + (b"\x00" * 31)

# Palette objects are created once to avoid per-frame allocations.
BLACK = brushes.color(8, 10, 15)
WHITE = brushes.color(245, 247, 250)
MUTED = brushes.color(145, 155, 170)
GREEN = brushes.color(46, 190, 92)
GREEN_DARK = brushes.color(10, 52, 34)
RED = brushes.color(248, 81, 73)
RED_DARK = brushes.color(62, 18, 23)
PURPLE = brushes.color(184, 120, 255)
PURPLE_DARK = brushes.color(36, 20, 58)
AMBER = brushes.color(255, 184, 72)
AMBER_DARK = brushes.color(59, 39, 13)
ORANGE = brushes.color(255, 159, 67)
ORANGE_DARK = brushes.color(61, 29, 9)
BLUE = brushes.color(88, 166, 255)
BLUE_DARK = brushes.color(9, 27, 61)

SMALL_FONT = PixelFont.load("/system/assets/fonts/ark.ppf")
TITLE_FONT = PixelFont.load("/system/assets/fonts/compass.ppf")

WIFI_SSID = None
WIFI_PASSWORD = None
wlan = None
wifi_state = "starting"
ip_address = "0.0.0.0"
next_wifi_attempt = 0

server_socket = None
client_socket = None
client_buffer = b""
client_started = 0

current_status = "available"
current_note = ""
custom_text = "HELLO"
custom_symbol = "star"
custom_color = "#1F6FEB"
custom_accent = brushes.color(31, 111, 235)
custom_background = brushes.color(6, 22, 47)
show_address_until = 0
notification_until = 0
battery_level = None
battery_charging = False
last_battery_check = -10000
last_b_press = -10000
night_sleep_at = 0
trusted_devices = {}
auth_nonces = {}
pending_pairing = None
pairing_results = {}
security_notice = ""
security_notice_until = 0


def center_text(text, y):
    width, _ = screen.measure_text(text)
    screen.text(text, int(80 - width / 2), y)


def clean_note(value):
    """Return a short ASCII note that the badge font can display safely."""
    if not isinstance(value, str):
        return ""
    result = ""
    for char in value:
        code = ord(char)
        if 32 <= code <= 126:
            result += char
        elif char in "\r\n\t":
            result += " "
        if len(result) >= 24:
            break
    return result.strip()


def valid_hex_color(value):
    if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
        return False
    try:
        int(value[1:], 16)
        return True
    except Exception:
        return False


def hexlify(value):
    return binascii.hexlify(value).decode("ascii")


def unhexlify(value, expected_bytes=None):
    if not isinstance(value, str):
        raise ValueError("hex value must be text")
    decoded = binascii.unhexlify(value)
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise ValueError("wrong hex value length")
    return decoded


def sha256(value):
    return hashlib.sha256(value).digest()


def hmac_sha256(key, message):
    """Small HMAC implementation for firmware builds without the hmac module."""
    if len(key) > 64:
        key = sha256(key)
    key = key + (b"\x00" * (64 - len(key)))
    outer = bytearray(64)
    inner = bytearray(64)
    for index in range(64):
        outer[index] = key[index] ^ 0x5C
        inner[index] = key[index] ^ 0x36
    return sha256(bytes(outer) + sha256(bytes(inner) + message))


def constant_time_equal(left, right):
    if len(left) != len(right):
        return False
    difference = 0
    for first, second in zip(left, right):
        difference |= first ^ second
    return difference == 0


def x25519(private_key, peer_public):
    """Return an RFC 7748 X25519 shared secret using MicroPython integers."""
    scalar_bytes = bytearray(private_key)
    scalar_bytes[0] &= 248
    scalar_bytes[31] &= 127
    scalar_bytes[31] |= 64
    scalar = int.from_bytes(scalar_bytes, "little")
    x_1 = int.from_bytes(peer_public, "little") % X25519_PRIME
    x_2, z_2 = 1, 0
    x_3, z_3 = x_1, 1
    swap = 0

    for bit_index in range(254, -1, -1):
        bit = (scalar >> bit_index) & 1
        swap ^= bit
        if swap:
            x_2, x_3 = x_3, x_2
            z_2, z_3 = z_3, z_2
        swap = bit

        a = (x_2 + z_2) % X25519_PRIME
        aa = (a * a) % X25519_PRIME
        b = (x_2 - z_2) % X25519_PRIME
        bb = (b * b) % X25519_PRIME
        e = (aa - bb) % X25519_PRIME
        c = (x_3 + z_3) % X25519_PRIME
        d = (x_3 - z_3) % X25519_PRIME
        da = (d * a) % X25519_PRIME
        cb = (c * b) % X25519_PRIME
        x_3 = ((da + cb) * (da + cb)) % X25519_PRIME
        z_3 = (x_1 * (da - cb) * (da - cb)) % X25519_PRIME
        x_2 = (aa * bb) % X25519_PRIME
        z_2 = (e * (aa + X25519_A24 * e)) % X25519_PRIME

    if swap:
        x_2, x_3 = x_3, x_2
        z_2, z_3 = z_3, z_2
    result = (x_2 * pow(z_2, X25519_PRIME - 2, X25519_PRIME))
    return (result % X25519_PRIME).to_bytes(32, "little")


def pairing_transcript(device_id, client_public, badge_public):
    return (
        b"work-status-pair-v1\x00"
        + device_id.encode("ascii")
        + client_public
        + badge_public
    )


def pairing_key(shared_secret, transcript):
    return sha256(b"work-status-key-v1\x00" + shared_secret + transcript)


def pairing_code(transcript):
    value = int.from_bytes(sha256(b"work-status-code-v1\x00" + transcript)[:4], "big")
    return "%06d" % (value % 1000000)


def auth_message(method, path, nonce, body):
    return (
        method.encode("ascii")
        + b"\n"
        + path.encode("ascii")
        + b"\n"
        + nonce.encode("ascii")
        + b"\n"
        + hexlify(sha256(body)).encode("ascii")
    )


def response_auth_message(nonce, body):
    return (
        b"response\n"
        + nonce.encode("ascii")
        + b"\n"
        + hexlify(sha256(body)).encode("ascii")
    )


def refresh_custom_brushes():
    global custom_accent, custom_background
    try:
        red = int(custom_color[1:3], 16)
        green = int(custom_color[3:5], 16)
        blue = int(custom_color[5:7], 16)
    except Exception:
        red, green, blue = 31, 111, 235
    custom_accent = brushes.color(red, green, blue)
    custom_background = brushes.color(red // 5, green // 5, blue // 5)


def refresh_battery_status():
    global battery_level, battery_charging, last_battery_check

    if battery_level is None or io.ticks - last_battery_check >= 10000:
        last_battery_check = io.ticks
        try:
            battery_level = int(get_battery_level())
            battery_level = max(0, min(100, battery_level))
            battery_charging = bool(is_charging())
        except Exception:
            battery_level = None
            battery_charging = False


def draw_battery_indicator():
    refresh_battery_status()

    # Dark backing keeps the indicator readable over every animation.
    screen.brush = BLACK
    screen.draw(shapes.rounded_rectangle(4, 4, 49, 14, 3))

    level = battery_level if battery_level is not None else 0
    if level <= 20:
        level_brush = RED
    elif level <= 50:
        level_brush = AMBER
    else:
        level_brush = GREEN

    # Battery body, terminal and proportional fill.
    screen.brush = WHITE
    screen.draw(shapes.rectangle(7, 7, 17, 8))
    screen.draw(shapes.rectangle(24, 9, 2, 4))
    screen.brush = BLACK
    screen.draw(shapes.rectangle(8, 8, 15, 6))
    fill_width = int((13 * level) / 100)
    if fill_width > 0:
        screen.brush = level_brush
        screen.draw(shapes.rectangle(9, 9, fill_width, 4))

    screen.font = SMALL_FONT
    screen.brush = GREEN if battery_charging else WHITE
    label = ("%d%%" % battery_level) if battery_level is not None else "--%"
    screen.text(label, 29, 6)


def load_wifi_config():
    global WIFI_SSID, WIFI_PASSWORD, wifi_state
    try:
        sys.path.insert(0, "/")
        from secrets import WIFI_SSID as configured_ssid
        from secrets import WIFI_PASSWORD as configured_password
        WIFI_SSID = configured_ssid
        WIFI_PASSWORD = configured_password
        wifi_state = "disconnected"
    except Exception:
        WIFI_SSID = None
        WIFI_PASSWORD = None
        wifi_state = "missing config"
    finally:
        if sys.path and sys.path[0] == "/":
            sys.path.pop(0)


def save_status():
    try:
        State.save("work_status", {
            "status": current_status,
            "note": current_note,
            "custom_text": custom_text,
            "custom_symbol": custom_symbol,
            "custom_color": custom_color,
        })
    except Exception as error:
        print("Could not save work status:", error)


def save_trusted_devices():
    try:
        records = []
        for device_id, device in trusted_devices.items():
            records.append({
                "id": device_id,
                "name": device["name"],
                "key": hexlify(device["key"]),
            })
        State.save("work_status_devices", {"devices": records})
    except Exception as error:
        print("Could not save trusted devices:", error)


def load_trusted_devices():
    global trusted_devices
    saved = {"devices": []}
    loaded = {}
    try:
        if State.load("work_status_devices", saved):
            records = saved.get("devices", [])
            if isinstance(records, list):
                for record in records[:MAX_TRUSTED_DEVICES]:
                    if not isinstance(record, dict):
                        continue
                    device_id = record.get("id", "")
                    name = clean_note(record.get("name", ""))[:16]
                    if (
                        isinstance(device_id, str)
                        and len(device_id) == 32
                        and all(char in "0123456789abcdef" for char in device_id)
                    ):
                        try:
                            key = unhexlify(record.get("key", ""), 32)
                        except Exception:
                            continue
                        loaded[device_id] = {
                            "name": name or "Controller",
                            "key": key,
                        }
    except Exception as error:
        print("Could not load trusted devices:", error)
    trusted_devices = loaded


def clear_trusted_devices():
    global trusted_devices, auth_nonces
    global pending_pairing, pairing_results
    global security_notice, security_notice_until
    trusted_devices = {}
    auth_nonces = {}
    pending_pairing = None
    pairing_results = {}
    save_trusted_devices()
    security_notice = "TRUST CLEARED"
    security_notice_until = io.ticks + 2500


def close_client():
    global client_socket, client_buffer, client_started
    if client_socket is not None:
        try:
            client_socket.close()
        except Exception:
            pass
    client_socket = None
    client_buffer = b""
    client_started = 0


def close_server():
    global server_socket
    close_client()
    if server_socket is not None:
        try:
            server_socket.close()
        except Exception:
            pass
    server_socket = None


def start_server():
    global server_socket, wifi_state
    if server_socket is not None:
        return
    try:
        address = socket.getaddrinfo("0.0.0.0", SERVER_PORT)[0][-1]
        server_socket = socket.socket()
        try:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        server_socket.bind(address)
        server_socket.listen(1)
        server_socket.setblocking(False)
        wifi_state = "online"
        print("Work Status listening on http://%s:%d" % (ip_address, SERVER_PORT))
    except Exception as error:
        print("HTTP server start failed:", error)
        close_server()
        wifi_state = "server error"


def service_wifi():
    global wlan, wifi_state, ip_address, next_wifi_attempt

    if not WIFI_SSID:
        wifi_state = "missing config"
        return

    if wlan is None:
        try:
            wlan = network.WLAN(network.STA_IF)
            wlan.active(True)
            try:
                power_save = getattr(wlan, "PM_POWERSAVE", None)
                if power_save is None:
                    power_save = getattr(network.WLAN, "PM_POWERSAVE", None)
                if power_save is not None:
                    wlan.config(pm=power_save)
            except Exception:
                pass
        except Exception as error:
            wifi_state = "wifi error"
            print("WiFi setup failed:", error)
            return

    if wlan.isconnected():
        ip_address = wlan.ifconfig()[0]
        if server_socket is None:
            start_server()
        return

    if server_socket is not None:
        close_server()
    ip_address = "0.0.0.0"
    wifi_state = "connecting"

    if io.ticks >= next_wifi_attempt:
        next_wifi_attempt = io.ticks + WIFI_RETRY_MS
        try:
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        except Exception as error:
            wifi_state = "wifi error"
            print("WiFi connect failed:", error)


def status_payload():
    refresh_battery_status()
    return {
        "status": current_status,
        "note": current_note,
        "custom_text": custom_text,
        "custom_symbol": custom_symbol,
        "custom_color": custom_color,
        "ip": ip_address,
        "port": SERVER_PORT,
        "battery": battery_level,
        "charging": battery_charging,
    }


def expire_security_state():
    global pending_pairing
    if (
        pending_pairing is not None
        and io.ticks >= pending_pairing["expires"]
    ):
        device_id = pending_pairing["id"]
        pairing_results[device_id] = {
            "status": "expired",
            "expires": io.ticks + PAIRING_TIMEOUT_MS,
        }
        pending_pairing = None

    for nonce in list(auth_nonces):
        if io.ticks >= auth_nonces[nonce]["expires"]:
            del auth_nonces[nonce]
    for device_id in list(pairing_results):
        if io.ticks >= pairing_results[device_id]["expires"]:
            del pairing_results[device_id]


def begin_pairing(update):
    global pending_pairing
    expire_security_state()
    device_id = update.get("device_id", "")
    device_name = clean_note(update.get("device_name", ""))[:16]
    public_hex = update.get("public_key", "")
    if (
        not isinstance(device_id, str)
        or len(device_id) != 32
        or any(char not in "0123456789abcdef" for char in device_id)
    ):
        return 400, {"error": "invalid device id"}
    if device_id in trusted_devices:
        return 409, {"error": "device already paired"}
    if len(trusted_devices) >= MAX_TRUSTED_DEVICES:
        return 409, {"error": "trusted device limit reached"}
    try:
        client_public = unhexlify(public_hex, 32)
    except Exception:
        return 400, {"error": "invalid public key"}
    if client_public == b"\x00" * 32:
        return 400, {"error": "invalid public key"}

    if pending_pairing is not None:
        if (
            pending_pairing["id"] == device_id
            and pending_pairing["client_public"] == client_public
        ):
            return 200, {
                "status": "pending",
                "public_key": hexlify(pending_pairing["badge_public"]),
                "code": pending_pairing["code"],
                "expires_in": max(
                    0, (pending_pairing["expires"] - io.ticks) // 1000,
                ),
            }
        return 409, {"error": "another pairing request is active"}

    badge_private = os.urandom(32)
    badge_public = x25519(badge_private, X25519_BASE)
    transcript = pairing_transcript(device_id, client_public, badge_public)
    code = pairing_code(transcript)
    pending_pairing = {
        "id": device_id,
        "name": device_name or "New controller",
        "client_public": client_public,
        "badge_private": badge_private,
        "badge_public": badge_public,
        "code": code,
        "expires": io.ticks + PAIRING_TIMEOUT_MS,
    }
    pairing_results.pop(device_id, None)
    return 200, {
        "status": "pending",
        "public_key": hexlify(badge_public),
        "code": code,
        "expires_in": PAIRING_TIMEOUT_MS // 1000,
    }


def approve_pending_pairing():
    global pending_pairing, security_notice, security_notice_until
    if pending_pairing is None:
        return False
    pairing = pending_pairing
    transcript = pairing_transcript(
        pairing["id"],
        pairing["client_public"],
        pairing["badge_public"],
    )
    shared = x25519(pairing["badge_private"], pairing["client_public"])
    if shared == b"\x00" * 32:
        reject_pending_pairing()
        return False
    key = pairing_key(shared, transcript)

    trusted_devices[pairing["id"]] = {
        "name": pairing["name"],
        "key": key,
    }
    save_trusted_devices()
    pairing_results[pairing["id"]] = {
        "status": "approved",
        "expires": io.ticks + PAIRING_TIMEOUT_MS,
    }
    security_notice = "PAIRED: " + pairing["name"]
    security_notice_until = io.ticks + 2500
    pending_pairing = None
    gc.collect()
    return True


def reject_pending_pairing():
    global pending_pairing, security_notice, security_notice_until
    if pending_pairing is None:
        return False
    pairing_results[pending_pairing["id"]] = {
        "status": "rejected",
        "expires": io.ticks + PAIRING_TIMEOUT_MS,
    }
    security_notice = "PAIRING REJECTED"
    security_notice_until = io.ticks + 2000
    pending_pairing = None
    gc.collect()
    return True


def pairing_status(device_id):
    expire_security_state()
    if pending_pairing is not None and pending_pairing["id"] == device_id:
        return {"status": "pending"}
    result = pairing_results.get(device_id)
    if result is not None:
        return {"status": result["status"]}
    if device_id in trusted_devices:
        return {"status": "approved"}
    return {"status": "unknown"}


def issue_challenge(device_id):
    expire_security_state()
    if device_id not in trusted_devices:
        return 403, {"error": "pairing required"}
    nonce = hexlify(os.urandom(16))
    if len(auth_nonces) >= 16:
        del auth_nonces[next(iter(auth_nonces))]
    auth_nonces[nonce] = {
        "device_id": device_id,
        "expires": io.ticks + AUTH_NONCE_TIMEOUT_MS,
    }
    return 200, {"nonce": nonce, "expires_in": AUTH_NONCE_TIMEOUT_MS // 1000}


def authorize_request(method, path, body, headers):
    expire_security_state()
    device_id = headers.get("x-work-device", "")
    nonce = headers.get("x-work-nonce", "")
    signature_hex = headers.get("x-work-signature", "")
    device = trusted_devices.get(device_id)
    challenge = auth_nonces.get(nonce)
    if device is None or challenge is None:
        return None
    if (
        device_id != challenge["device_id"]
        or io.ticks >= challenge["expires"]
    ):
        return None
    try:
        supplied = unhexlify(signature_hex, 32)
    except Exception:
        return None
    expected = hmac_sha256(
        device["key"],
        auth_message(method, path, nonce, body),
    )
    if not constant_time_equal(supplied, expected):
        return None
    del auth_nonces[nonce]
    return device["key"]


def make_response(code, payload, auth_key=None, auth_nonce=""):
    body = json.dumps(payload).encode("utf-8")
    reason = {
        200: "OK",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        413: "Payload Too Large",
    }.get(code, "Error")
    auth_header = ""
    if auth_key is not None and auth_nonce:
        signature = hexlify(hmac_sha256(
            auth_key,
            response_auth_message(auth_nonce, body),
        ))
        auth_header = "X-Work-Signature: %s\r\n" % signature
    headers = (
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %d\r\n"
        "Cache-Control: no-store\r\n"
        "%s"
        "Connection: close\r\n\r\n"
    ) % (code, reason, len(body), auth_header)
    return headers.encode("utf-8") + body


def handle_request(raw_request):
    global current_status, current_note, notification_until
    global custom_text, custom_symbol, custom_color

    header_end = raw_request.find(b"\r\n\r\n")
    if header_end < 0:
        return make_response(400, {"error": "incomplete request"})

    try:
        header_text = raw_request[:header_end].decode("utf-8")
        lines = header_text.split("\r\n")
        request_parts = lines[0].split()
        if len(request_parts) != 3:
            raise ValueError("bad request line")
        method = request_parts[0]
        raw_path = request_parts[1]
        path_parts = raw_path.split("?", 1)
        path = path_parts[0]
        query = path_parts[1] if len(path_parts) == 2 else ""
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        body = raw_request[header_end + 4:]
    except Exception:
        return make_response(400, {"error": "invalid request"})

    query_values = {}
    for field in query.split("&"):
        if "=" in field:
            name, value = field.split("=", 1)
            query_values[name] = value

    if path == "/api/pair":
        if method != "POST":
            return make_response(405, {"error": "method not allowed"})
        try:
            update = json.loads(body.decode("utf-8"))
            code, payload = begin_pairing(update)
            return make_response(code, payload)
        except Exception:
            return make_response(400, {"error": "invalid pairing request"})

    if path == "/api/pair/status":
        if method != "GET":
            return make_response(405, {"error": "method not allowed"})
        return make_response(
            200,
            pairing_status(query_values.get("device_id", "")),
        )

    if path == "/api/challenge":
        if method != "GET":
            return make_response(405, {"error": "method not allowed"})
        code, payload = issue_challenge(query_values.get("device_id", ""))
        return make_response(code, payload)

    if path != "/api/status":
        return make_response(404, {"error": "not found"})

    auth_key = authorize_request(method, path, body, headers)
    if auth_key is None:
        return make_response(401, {"error": "authentication failed"})

    if method == "GET":
        return make_response(
            200, status_payload(), auth_key, headers["x-work-nonce"],
        )

    if method != "POST":
        return make_response(
            405,
            {"error": "method not allowed"},
            auth_key,
            headers["x-work-nonce"],
        )

    try:
        update = json.loads(body.decode("utf-8"))
        requested_status = update.get("status")
        if requested_status not in STATUS_ORDER:
            return make_response(
                400,
                {"error": "invalid status"},
                auth_key,
                headers["x-work-nonce"],
            )
        if requested_status == "custom":
            requested_text = update.get("custom_text", "")
            requested_symbol = update.get("custom_symbol", "")
            requested_color = update.get("custom_color", "")
            if not isinstance(requested_text, str):
                return make_response(
                    400,
                    {"error": "custom text must be text"},
                    auth_key,
                    headers["x-work-nonce"],
                )
            if requested_symbol not in CUSTOM_SYMBOLS:
                return make_response(
                    400,
                    {"error": "invalid custom symbol"},
                    auth_key,
                    headers["x-work-nonce"],
                )
            if not valid_hex_color(requested_color):
                return make_response(
                    400,
                    {"error": "invalid custom color"},
                    auth_key,
                    headers["x-work-nonce"],
                )
            custom_text = clean_note(requested_text) or "CUSTOM"
            custom_symbol = requested_symbol
            custom_color = requested_color.upper()
            current_note = ""
            refresh_custom_brushes()
        else:
            requested_note = update.get("note", "")
            if not isinstance(requested_note, str):
                return make_response(
                    400,
                    {"error": "note must be text"},
                    auth_key,
                    headers["x-work-nonce"],
                )
            current_note = clean_note(requested_note)
        current_status = requested_status
        notification_until = io.ticks + 900
        save_status()
        return make_response(
            200, status_payload(), auth_key, headers["x-work-nonce"],
        )
    except Exception:
        return make_response(
            400,
            {"error": "invalid JSON"},
            auth_key,
            headers["x-work-nonce"],
        )


def request_is_complete(data):
    header_end = data.find(b"\r\n\r\n")
    if header_end < 0:
        return False
    content_length = 0
    try:
        header_text = data[:header_end].decode("utf-8").lower()
        for line in header_text.split("\r\n"):
            if line.startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
                break
    except Exception:
        return True
    return len(data) >= header_end + 4 + content_length


def service_http():
    global client_socket, client_buffer, client_started
    if server_socket is None:
        return

    if client_socket is None:
        try:
            client_socket, _ = server_socket.accept()
            client_socket.setblocking(False)
            client_buffer = b""
            client_started = io.ticks
        except OSError:
            return
        except Exception as error:
            print("HTTP accept failed:", error)
            return

    try:
        chunk = client_socket.recv(512)
        if chunk:
            client_buffer += chunk
            if len(client_buffer) > MAX_REQUEST_BYTES:
                try:
                    client_socket.send(make_response(413, {"error": "request too large"}))
                except Exception:
                    pass
                close_client()
                return
        elif client_buffer:
            close_client()
            return
    except OSError:
        pass
    except Exception as error:
        print("HTTP receive failed:", error)
        close_client()
        return

    if request_is_complete(client_buffer):
        response = handle_request(client_buffer)
        try:
            client_socket.send(response)
        except Exception as error:
            print("HTTP response failed:", error)
        close_client()
    elif io.ticks - client_started > CLIENT_TIMEOUT_MS:
        close_client()


def draw_available():
    pulse = int(io.ticks / 90) % 18
    pulse = pulse if pulse < 9 else 17 - pulse
    screen.brush = GREEN_DARK
    screen.draw(shapes.circle(80, 42, 30 + pulse))
    screen.brush = GREEN
    screen.draw(shapes.circle(80, 42, 27))
    screen.brush = BLACK
    screen.draw(shapes.rounded_rectangle(66, 13, 29, 62, 4))
    screen.brush = GREEN
    screen.draw(shapes.circle(88, 45, 3))


def draw_meeting():
    screen.brush = RED
    screen.draw(shapes.rounded_rectangle(10, 7, 140, 70, 9))
    screen.brush = RED_DARK
    screen.draw(shapes.rounded_rectangle(16, 13, 128, 58, 5))
    screen.brush = WHITE
    screen.draw(shapes.circle(40, 42, 12))
    for index in range(4):
        height = 13 + ((int(io.ticks / 120) + index * 3) % 22)
        screen.draw(shapes.rounded_rectangle(
            65 + index * 16, 65 - height, 10, height, 2
        ))


def draw_focus():
    offset = int(io.ticks / 150) % 13
    screen.brush = PURPLE_DARK
    screen.draw(shapes.rounded_rectangle(9, 7, 142, 70, 8))
    screen.brush = PURPLE
    screen.draw(shapes.rounded_rectangle(21, 20, 58 + offset, 7, 2))
    screen.draw(shapes.rounded_rectangle(35, 39, 78 - offset, 7, 2))
    screen.draw(shapes.rounded_rectangle(21, 58, 52 + offset, 7, 2))
    if int(io.ticks / 280) % 2:
        screen.brush = WHITE
        screen.draw(shapes.rectangle(123, 55, 12, 10))


def draw_away():
    screen.brush = AMBER
    screen.draw(shapes.rounded_rectangle(36, 25, 76, 49, 9))
    screen.draw(shapes.arc(112, 49, 20, -90, 90).stroke(9))
    screen.brush = AMBER_DARK
    screen.draw(shapes.rounded_rectangle(45, 34, 58, 31, 5))
    phase = int(io.ticks / 130) % 10
    screen.brush = WHITE
    screen.draw(shapes.line(57, 22 - phase // 3, 64, 6 - phase // 3, 4))
    other_phase = (phase + 5) % 10
    screen.draw(shapes.line(87, 22 - other_phase // 3,
                            94, 6 - other_phase // 3, 4))


def draw_lunch():
    steam = int(io.ticks / 150) % 7
    screen.brush = ORANGE_DARK
    screen.draw(shapes.rounded_rectangle(20, 9, 120, 68, 10))

    # A crisp curry bowl with a visible rim and animated steam.
    screen.brush = ORANGE
    screen.draw(shapes.rounded_rectangle(33, 34, 94, 34, 9))
    screen.brush = ORANGE_DARK
    screen.draw(shapes.rounded_rectangle(39, 39, 82, 11, 4))
    screen.brush = WHITE
    screen.draw(shapes.circle(57, 44, 3))
    screen.draw(shapes.circle(80, 43, 3))
    screen.draw(shapes.circle(103, 44, 3))
    screen.draw(shapes.line(45, 71, 115, 71, 4))
    screen.draw(shapes.line(58, 32 - steam // 3, 64, 16 - steam // 3, 4))
    second = (steam + 3) % 7
    screen.draw(shapes.line(91, 32 - second // 3,
                            97, 16 - second // 3, 4))


def draw_sleep():
    twinkle = int(io.ticks / 220) % 5
    screen.brush = BLUE
    screen.draw(shapes.circle(67, 39, 34))
    screen.brush = BLUE_DARK
    screen.draw(shapes.circle(82, 27, 32))

    screen.brush = WHITE
    screen.draw(shapes.circle(25, 24, 2 + twinkle // 2))
    screen.draw(shapes.circle(126, 19, 3))
    screen.draw(shapes.circle(135, 58, 2 + (4 - twinkle) // 2))
    screen.font = TITLE_FONT
    screen.brush = BLUE
    screen.text("Z", 94, 31)
    screen.font = SMALL_FONT
    screen.text("Z", 116, 18)


def draw_custom_symbol():
    pulse = int(io.ticks / 100) % 16
    pulse = pulse if pulse < 8 else 15 - pulse

    # A quiet halo keeps every icon centred without competing with its shape.
    screen.brush = custom_accent
    screen.draw(shapes.circle(80, 42, 37 + pulse // 3).stroke(2))
    screen.brush = custom_accent

    if custom_symbol == "heart":
        # Compact lobes and equal diagonals keep the heart visually centred.
        size = 15 + pulse // 6
        screen.draw(shapes.circle(66, 31, size))
        screen.draw(shapes.circle(94, 31, size))
        screen.draw(shapes.line(55, 38, 80, 68, 17))
        screen.draw(shapes.line(105, 38, 80, 68, 17))
        screen.brush = custom_background
        screen.draw(shapes.circle(80, 15, 12))
    elif custom_symbol == "check":
        screen.draw(shapes.circle(80, 42, 32).stroke(6))
        screen.draw(shapes.line(57, 42, 73, 58, 8))
        screen.draw(shapes.line(73, 58, 105, 25, 8))
    elif custom_symbol == "alert":
        screen.draw(shapes.line(80, 9, 45, 72, 5))
        screen.draw(shapes.line(45, 72, 115, 72, 5))
        screen.draw(shapes.line(115, 72, 80, 9, 5))
        screen.brush = WHITE
        screen.draw(shapes.line(80, 31, 80, 52, 6))
        screen.draw(shapes.circle(80, 63, 3))
    elif custom_symbol == "coffee":
        screen.draw(shapes.rounded_rectangle(43, 29, 68, 39, 6).stroke(6))
        screen.draw(shapes.arc(112, 48, 18, -90, 90).stroke(6))
        screen.draw(shapes.line(35, 75, 127, 75, 5))
        screen.brush = WHITE
        steam = int(io.ticks / 140) % 7
        screen.draw(shapes.line(61, 25 - steam // 3,
                                67, 10 - steam // 3, 3))
        other = (steam + 3) % 7
        screen.draw(shapes.line(88, 25 - other // 3,
                                94, 10 - other // 3, 3))
    elif custom_symbol == "door":
        screen.draw(shapes.rounded_rectangle(56, 7, 48, 70, 3).stroke(5))
        screen.draw(shapes.line(66, 23, 94, 23, 3))
        screen.draw(shapes.line(66, 60, 94, 60, 3))
        screen.brush = WHITE
        screen.draw(shapes.circle(92, 43, 3 + pulse // 4))
    elif custom_symbol == "code":
        screen.draw(shapes.rounded_rectangle(20, 10, 120, 68, 7).stroke(5))
        screen.draw(shapes.line(62, 28, 44, 44, 7))
        screen.draw(shapes.line(44, 44, 62, 60, 7))
        screen.draw(shapes.line(98, 28, 116, 44, 7))
        screen.draw(shapes.line(116, 44, 98, 60, 7))
        screen.draw(shapes.line(88, 24, 72, 64, 6))
    elif custom_symbol == "bolt":
        points = ((91, 7), (58, 43), (77, 43), (68, 77),
                  (104, 36), (85, 36), (91, 7))
        for index in range(len(points) - 1):
            start = points[index]
            end = points[index + 1]
            screen.draw(shapes.line(start[0], start[1],
                                    end[0], end[1], 6))
    else:
        points = ((80, 5), (91, 31), (120, 32), (98, 50), (105, 78),
                  (80, 63), (55, 78), (62, 50), (40, 32), (69, 31), (80, 5))
        for index in range(len(points) - 1):
            start = points[index]
            end = points[index + 1]
            screen.draw(shapes.line(start[0], start[1], end[0], end[1], 5))


def draw_address_overlay():
    screen.brush = BLACK
    screen.clear()
    screen.font = SMALL_FONT
    screen.brush = MUTED
    center_text("BADGE ADDRESS", 22)
    screen.font = TITLE_FONT
    screen.brush = GREEN
    center_text(ip_address, 46)
    screen.font = SMALL_FONT
    screen.brush = WHITE
    center_text("PORT %d" % SERVER_PORT, 76)
    screen.brush = MUTED
    center_text(
        "DOWN: CLEAR %d TRUSTED" % len(trusted_devices),
        98,
    )
    screen.draw(shapes.rounded_rectangle(1, 1, 158, 118, 7).stroke(3))
    draw_battery_indicator()


def draw_pairing_overlay():
    screen.brush = BLACK
    screen.clear()
    screen.font = SMALL_FONT
    screen.brush = MUTED
    center_text("PAIR NEW CONTROLLER", 8)
    screen.brush = WHITE
    center_text(pending_pairing["name"], 27)
    screen.font = TITLE_FONT
    screen.brush = GREEN
    center_text(pending_pairing["code"][:3] + " " + pending_pairing["code"][3:], 48)
    screen.font = SMALL_FONT
    screen.brush = WHITE
    center_text("MATCH CODE ON LAPTOP", 78)
    screen.brush = GREEN
    center_text("UP: APPROVE", 94)
    screen.brush = RED
    center_text("DOWN: REJECT", 108)
    screen.brush = GREEN
    screen.draw(shapes.rounded_rectangle(1, 1, 158, 118, 7).stroke(2))


def draw_security_notice():
    screen.brush = BLACK
    screen.clear()
    screen.font = SMALL_FONT
    screen.brush = MUTED
    center_text("CONTROLLER SECURITY", 28)
    screen.font = TITLE_FONT
    screen.brush = GREEN
    center_text(security_notice, 51)
    screen.font = SMALL_FONT
    screen.brush = WHITE
    center_text("%d TRUSTED DEVICE(S)" % len(trusted_devices), 84)
    screen.brush = GREEN
    screen.draw(shapes.rounded_rectangle(1, 1, 158, 118, 7).stroke(2))


def draw_ui():
    expire_security_state()
    if pending_pairing is not None:
        draw_pairing_overlay()
        return
    if io.ticks < security_notice_until:
        draw_security_notice()
        return
    if io.ticks < show_address_until:
        draw_address_overlay()
        return

    if current_status == "meeting":
        background, accent = RED_DARK, RED
    elif current_status == "focus":
        background, accent = PURPLE_DARK, PURPLE
    elif current_status == "away":
        background, accent = AMBER_DARK, AMBER
    elif current_status == "lunch":
        background, accent = ORANGE_DARK, ORANGE
    elif current_status == "sleep":
        background, accent = BLUE_DARK, BLUE
    elif current_status == "custom":
        background, accent = custom_background, custom_accent
    else:
        background, accent = BLACK, GREEN

    screen.brush = background
    screen.clear()

    if current_status == "meeting":
        draw_meeting()
    elif current_status == "focus":
        draw_focus()
    elif current_status == "away":
        draw_away()
    elif current_status == "lunch":
        draw_lunch()
    elif current_status == "sleep":
        draw_sleep()
    elif current_status == "custom":
        draw_custom_symbol()
    else:
        draw_available()

    screen.font = TITLE_FONT
    screen.brush = WHITE
    if current_status == "custom":
        if screen.measure_text(custom_text)[0] > 150:
            screen.font = SMALL_FONT
        center_text(custom_text, 96)
    else:
        center_text(STATUS_LABELS[current_status], 87)
        if current_note:
            screen.font = SMALL_FONT
            screen.brush = accent
            center_text(current_note, 105)

    if wifi_state != "online":
        screen.font = SMALL_FONT
        screen.brush = MUTED
        connection_text = (
            "Set WiFi in secrets.py"
            if wifi_state == "missing config"
            else "WiFi: " + wifi_state
        )
        center_text(connection_text, 111)

    # A brief accent frame confirms a remote status update without boxing in
    # the status screen during normal use.
    if io.ticks < notification_until:
        screen.brush = accent
        screen.draw(shapes.rounded_rectangle(5, 5, 150, 110, 5).stroke(2))

    draw_battery_indicator()


def prepare_night_sleep():
    """Shut down network activity and stage Mona-OS hardware sleep."""
    global night_sleep_at, wifi_state
    close_server()
    if wlan is not None:
        try:
            wlan.disconnect()
        except Exception:
            pass
        try:
            wlan.active(False)
        except Exception:
            pass
    wifi_state = "sleeping"
    night_sleep_at = io.ticks + 150


def draw_night_sleep_screen():
    screen.brush = brushes.color(0, 0, 0)
    screen.clear()


def enter_night_sleep():
    """Enter Mona-OS hardware sleep; press RESET to wake reliably."""
    global night_sleep_at, wlan, wifi_state, next_wifi_attempt
    try:
        import powman
        powman.sleep()
        import machine
        machine.reset()
    except Exception as error:
        print("Night sleep unavailable:", error)
        night_sleep_at = 0
        wlan = None
        wifi_state = "disconnected"
        next_wifi_attempt = 0


def init():
    global current_status, current_note
    global custom_text, custom_symbol, custom_color
    os.chdir(APP_DIR)
    saved = {
        "status": "available",
        "note": "",
        "custom_text": "HELLO",
        "custom_symbol": "star",
        "custom_color": "#1F6FEB",
    }
    try:
        if State.load("work_status", saved):
            if saved.get("status") in STATUS_ORDER:
                current_status = saved["status"]
            current_note = clean_note(saved.get("note", ""))
            custom_text = clean_note(saved.get("custom_text", "HELLO")) or "CUSTOM"
            if saved.get("custom_symbol") in CUSTOM_SYMBOLS:
                custom_symbol = saved["custom_symbol"]
            if valid_hex_color(saved.get("custom_color")):
                custom_color = saved["custom_color"].upper()
    except Exception as error:
        print("Could not load work status:", error)
    refresh_custom_brushes()
    load_trusted_devices()
    load_wifi_config()
    gc.collect()


def update():
    global current_status, current_note, show_address_until
    global last_b_press, night_sleep_at

    expire_security_state()
    if pending_pairing is not None:
        if io.BUTTON_UP in io.pressed:
            approve_pending_pairing()
        elif io.BUTTON_DOWN in io.pressed:
            reject_pending_pairing()
    else:
        if io.BUTTON_A in io.pressed:
            index = (STATUS_ORDER.index(current_status) + 1) % len(STATUS_ORDER)
            current_status = STATUS_ORDER[index]
            current_note = ""
            save_status()

        if io.BUTTON_C in io.pressed:
            show_address_until = io.ticks + 8000

        if io.BUTTON_DOWN in io.pressed and io.ticks < show_address_until:
            show_address_until = 0
            clear_trusted_devices()

        if io.BUTTON_B in io.pressed:
            if io.ticks - last_b_press <= DOUBLE_B_WINDOW_MS:
                last_b_press = -10000
                prepare_night_sleep()
            else:
                last_b_press = io.ticks

    if night_sleep_at:
        draw_night_sleep_screen()
        if io.ticks >= night_sleep_at:
            enter_night_sleep()
        time.sleep_ms(50)
        return

    service_wifi()
    service_http()
    draw_ui()
    if current_status == "sleep":
        time.sleep_ms(SLEEP_STATUS_FRAME_DELAY_MS)
    else:
        time.sleep_ms(FRAME_DELAY_MS)


def on_exit():
    close_server()
    save_status()


if __name__ == "__main__":
    run(update, init=init, on_exit=on_exit)
