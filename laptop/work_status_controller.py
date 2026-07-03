"""Desktop controller for the GitHub Universe 2025 Work Status badge."""

from __future__ import annotations

import binascii
import hashlib
import hmac
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, ttk
from urllib import error, parse, request


APP_TITLE = "Work Status Badge"
CONFIG_PATH = Path(__file__).resolve().with_name("badge_profiles.json")
LEGACY_CONFIG_PATH = Path.home() / ".work_status_badge.json"
DEVICE_CONFIG_PATH = Path.home() / ".work_status_badge_device.json"
API_PATH = "/api/status"
REQUEST_TIMEOUT = 2.5
PAIRING_TIMEOUT = 35
PAIRING_REQUEST_TIMEOUT = 10
X25519_PRIME = (1 << 255) - 19
X25519_A24 = 121665
X25519_BASE = b"\x09" + (b"\x00" * 31)
STATUSES = {
    "available": ("Available", "Open for collaboration", "#2ebe5c"),
    "meeting": ("In a meeting", "Please do not disturb", "#f85149"),
    "focus": ("Focus mode", "Deep work in progress", "#b878ff"),
    "away": ("Away", "Back soon", "#ffb848"),
    "lunch": ("Lunch break", "Refuelling", "#ff9f43"),
    "sleep": ("Offline", "Sleeping", "#58a6ff"),
}
BADGE_LABELS = {
    "available": "I'M FREE",
    "meeting": "IN A MEETING",
    "focus": "DEEP WORK",
    "away": "BACK SOON",
    "lunch": "LUNCH BREAK",
    "sleep": "SLEEPING",
}
CUSTOM_SYMBOLS = {
    "Star / highlight": "star",
    "Heart / welcome": "heart",
    "Check / complete": "check",
    "Alert / attention": "alert",
    "Coffee / break": "coffee",
    "Door / access": "door",
    "Code / building": "code",
    "Bolt / urgent": "bolt",
}
DEFAULT_CUSTOM = {
    "color": "#1F6FEB",
    "symbol": "star",
    "text": "HELLO",
}
THEMES = {
    "light": {
        "bg": "#eaf2f8", "panel": "#f9fcff", "raised": "#ffffff",
        "field": "#ffffff", "border": "#cfdeea", "border_hot": "#078cb7",
        "text": "#10213a", "muted": "#647a91", "cyan": "#078cb7",
        "violet": "#7657d6", "active": "#e7f5fb",
    },
    "dark": {
        "bg": "#090f18", "panel": "#111a27", "raised": "#182435",
        "field": "#0d1622", "border": "#2b3c52", "border_hot": "#38bdf8",
        "text": "#edf6ff", "muted": "#91a4b8", "cyan": "#38bdf8",
        "violet": "#a78bfa", "active": "#21344a",
    },
}


class PairingRequired(RuntimeError):
    pass


class AuthenticationFailed(RuntimeError):
    pass


def x25519(private_key: bytes, peer_public: bytes) -> bytes:
    """Return an RFC 7748 X25519 shared secret."""
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
    result = x_2 * pow(z_2, X25519_PRIME - 2, X25519_PRIME)
    return (result % X25519_PRIME).to_bytes(32, "little")


def pairing_transcript(
    device_id: str,
    client_public: bytes,
    badge_public: bytes,
) -> bytes:
    return (
        b"work-status-pair-v1\x00"
        + device_id.encode("ascii")
        + client_public
        + badge_public
    )


def derive_pairing_key(shared_secret: bytes, transcript: bytes) -> bytes:
    return hashlib.sha256(
        b"work-status-key-v1\x00" + shared_secret + transcript
    ).digest()


def pairing_code(transcript: bytes) -> str:
    digest = hashlib.sha256(
        b"work-status-code-v1\x00" + transcript
    ).digest()
    return "%06d" % (int.from_bytes(digest[:4], "big") % 1000000)


def auth_message(method: str, path: str, nonce: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return ("%s\n%s\n%s\n%s" % (
        method, path, nonce, body_hash,
    )).encode("ascii")


def response_auth_message(nonce: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return ("response\n%s\n%s" % (nonce, body_hash)).encode("ascii")


def fitted_window_geometry(screen_width: int, screen_height: int) -> str:
    """Fit the dashboard to the display while leaving room for OS chrome."""
    width = min(1080, max(720, screen_width - 80))
    height = min(830, max(500, screen_height - 96))
    width = min(width, screen_width)
    height = min(height, screen_height)
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    return "%dx%d+%d+%d" % (width, height, x, y)


def darken_hex(color: str, divisor: int = 5) -> str:
    """Return the badge's dark background treatment for an accent color."""
    return "#%02X%02X%02X" % tuple(
        int(color[index:index + 2], 16) // divisor
        for index in (1, 3, 5)
    )


def tint_hex(color: str, strength: float = 0.88) -> str:
    """Blend an accent towards white for a glass-panel tint."""
    channels = []
    for index in (1, 3, 5):
        channel = int(color[index:index + 2], 16)
        channels.append(round(channel + (255 - channel) * strength))
    return "#%02X%02X%02X" % tuple(channels)


def badge_base_url(address: str) -> str:
    value = address.strip()
    if not value:
        raise ValueError("Enter the badge IP address shown on its screen.")
    if "://" not in value:
        value = "http://" + value
    parsed = parse.urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("Use an address such as 192.168.1.42 or 192.168.1.42:8080.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Enter only the badge address, without a page path.")
    try:
        port = parsed.port or 8080
    except ValueError as exc:
        raise ValueError("The port must be a number.") from exc
    host = parsed.hostname
    if ":" in host:
        host = "[" + host + "]"
    return "http://%s:%d" % (host, port)


def normalize_device_config(data: object) -> dict:
    result = {"version": 1, "id": "", "name": "", "keys": {}}
    if not isinstance(data, dict):
        return result
    device_id = data.get("id", "")
    if (
        isinstance(device_id, str)
        and len(device_id) == 32
        and all(char in "0123456789abcdef" for char in device_id)
    ):
        result["id"] = device_id
    name = data.get("name", "")
    if isinstance(name, str):
        result["name"] = name.strip()[:16]
    keys = data.get("keys", {})
    if isinstance(keys, dict):
        for address, key in keys.items():
            if (
                isinstance(address, str)
                and address.startswith("http://")
                and isinstance(key, str)
                and len(key) == 64
                and all(char in "0123456789abcdef" for char in key)
            ):
                result["keys"][address] = key
    return result


def load_device_config() -> dict:
    try:
        config = normalize_device_config(
            json.loads(DEVICE_CONFIG_PATH.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError):
        config = normalize_device_config({})
    if not config["id"]:
        config["id"] = binascii.hexlify(os.urandom(16)).decode("ascii")
    if not config["name"]:
        config["name"] = (socket.gethostname().strip() or "Laptop")[:16]
    save_device_config(config)
    return config


def save_device_config(config: dict) -> None:
    try:
        DEVICE_CONFIG_PATH.write_text(
            json.dumps(normalize_device_config(config), indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


class BadgeClient:
    def __init__(
        self,
        address: str,
        device_id: str = "",
        device_name: str = "",
        device_key: bytes | None = None,
    ):
        self.base_url = badge_base_url(address)
        self.device_id = device_id
        self.device_name = device_name[:16] or "Laptop"
        self.device_key = device_key

    def get_status(self) -> dict:
        return self._signed_request(API_PATH, "GET")

    def set_status(self, status: str, note: str = "") -> dict:
        if status not in STATUSES:
            raise ValueError("Unknown status: " + status)
        payload = json.dumps({
            "status": status,
            "note": note.strip()[:24],
        }).encode("utf-8")
        return self._signed_request(API_PATH, "POST", payload)

    def set_custom(self, text: str, symbol: str, color: str) -> dict:
        if symbol not in CUSTOM_SYMBOLS.values():
            raise ValueError("Unknown custom symbol: " + symbol)
        if (
            len(color) != 7
            or not color.startswith("#")
            or any(char not in "0123456789abcdefABCDEF" for char in color[1:])
        ):
            raise ValueError("Custom color must be a six-digit hex color.")
        payload = json.dumps({
            "status": "custom",
            "custom_text": text.strip()[:24] or "CUSTOM",
            "custom_symbol": symbol,
            "custom_color": color.upper(),
        }).encode("utf-8")
        return self._signed_request(API_PATH, "POST", payload)

    def begin_pairing(self) -> dict:
        if not self.device_id:
            raise RuntimeError("This controller has no device identity.")
        private_key = os.urandom(32)
        client_public = x25519(private_key, X25519_BASE)
        payload = json.dumps({
            "device_id": self.device_id,
            "device_name": self.device_name,
            "public_key": binascii.hexlify(client_public).decode("ascii"),
        }).encode("utf-8")
        result = self._send(
            request.Request(
                self.base_url + "/api/pair",
                data=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                method="POST",
            ),
            timeout=PAIRING_REQUEST_TIMEOUT,
        )
        try:
            badge_public = binascii.unhexlify(result["public_key"])
        except (KeyError, ValueError, binascii.Error) as exc:
            raise RuntimeError("Badge returned an invalid pairing key.") from exc
        if len(badge_public) != 32:
            raise RuntimeError("Badge returned an invalid pairing key.")
        transcript = pairing_transcript(
            self.device_id, client_public, badge_public,
        )
        code = pairing_code(transcript)
        if result.get("code") != code:
            raise RuntimeError(
                "Pairing code mismatch. Cancel pairing and check the network."
            )
        shared = x25519(private_key, badge_public)
        if shared == b"\x00" * 32:
            raise RuntimeError("Badge returned an unsafe pairing key.")
        return {
            "code": code,
            "key": derive_pairing_key(shared, transcript),
        }

    def get_pairing_status(self) -> str:
        result = self._send(request.Request(
            self.base_url
            + "/api/pair/status?device_id="
            + parse.quote(self.device_id),
            headers={"Accept": "application/json"},
            method="GET",
        ))
        return str(result.get("status", "unknown"))

    def _signed_request(
        self,
        path: str,
        method: str,
        body: bytes = b"",
    ) -> dict:
        if not self.device_id or self.device_key is None:
            raise PairingRequired("Pair this controller before connecting.")
        challenge = self._send(request.Request(
            self.base_url
            + "/api/challenge?device_id="
            + parse.quote(self.device_id),
            headers={"Accept": "application/json"},
            method="GET",
        ))
        nonce = challenge.get("nonce", "")
        if not isinstance(nonce, str) or len(nonce) != 32:
            raise RuntimeError("Badge returned an invalid security challenge.")
        signature = hmac.new(
            self.device_key,
            auth_message(method, path, nonce, body),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Accept": "application/json",
            "X-Work-Device": self.device_id,
            "X-Work-Nonce": nonce,
            "X-Work-Signature": signature,
        }
        data = None
        if method == "POST":
            data = body
            headers["Content-Type"] = "application/json"
        return self._send(
            request.Request(
                self.base_url + path,
                data=data,
                headers=headers,
                method=method,
            ),
            response_key=self.device_key,
            response_nonce=nonce,
        )

    @staticmethod
    def _send(
        req: request.Request,
        timeout: float = REQUEST_TIMEOUT,
        response_key: bytes | None = None,
        response_nonce: str = "",
    ) -> dict:
        try:
            with request.urlopen(req, timeout=timeout) as response:
                body = response.read()
                if response_key is not None:
                    supplied = response.headers.get(
                        "X-Work-Signature", "",
                    )
                    expected = hmac.new(
                        response_key,
                        response_auth_message(response_nonce, body),
                        hashlib.sha256,
                    ).hexdigest()
                    if not hmac.compare_digest(supplied, expected):
                        raise AuthenticationFailed(
                            "Badge response authentication failed."
                        )
                data = json.loads(body.decode("utf-8"))
        except error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = str(payload.get("error", "request rejected"))
            except Exception:
                message = "request rejected"
            if exc.code == 403 and message == "pairing required":
                raise PairingRequired(
                    "This controller is not paired with the badge."
                ) from exc
            if exc.code == 401:
                raise AuthenticationFailed(
                    "Badge authentication failed. Press C then DOWN on the "
                    "badge to clear trust, then select Connect."
                ) from exc
            if exc.code == 409 and message == "device already paired":
                raise AuthenticationFailed(
                    "The badge remembers this device but its local key is "
                    "missing. Press C then DOWN on the badge, then Connect."
                ) from exc
            if exc.code == 409 and message == "trusted device limit reached":
                raise RuntimeError(
                    "The badge already remembers eight controllers. Press C "
                    "then DOWN on the badge to clear them before pairing."
                ) from exc
            raise RuntimeError(
                "Badge rejected the request (HTTP %d): %s" % (
                    exc.code, message,
                )
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError("The badge returned an unexpected response.")
        return data


def normalize_config(data: object) -> dict:
    """Return the current named-profile config, migrating the old format."""
    custom = dict(DEFAULT_CUSTOM)
    if not isinstance(data, dict):
        return {
            "version": 4, "selected": "", "badges": {}, "custom": custom,
            "theme": "light",
        }

    stored_custom = data.get("custom")
    if isinstance(stored_custom, dict):
        color = stored_custom.get("color")
        symbol = stored_custom.get("symbol")
        text = stored_custom.get("text")
        if (
            isinstance(color, str)
            and len(color) == 7
            and color.startswith("#")
            and all(char in "0123456789abcdefABCDEF" for char in color[1:])
        ):
            custom["color"] = color.upper()
        if symbol in CUSTOM_SYMBOLS.values():
            custom["symbol"] = symbol
        if isinstance(text, str) and text.strip():
            custom["text"] = text.strip()[:24]

    badges = data.get("badges")
    if isinstance(badges, dict):
        clean_badges = {}
        for name, address in badges.items():
            if isinstance(name, str) and isinstance(address, str):
                name = name.strip()[:32]
                address = address.strip()
                if name and address:
                    clean_badges[name] = address
        selected = data.get("selected", "")
        if selected not in clean_badges:
            selected = next(iter(clean_badges), "")
        return {
            "version": 4,
            "selected": selected,
            "badges": clean_badges,
            "custom": custom,
            "theme": data.get("theme", "light")
            if data.get("theme") in THEMES else "light",
        }

    # Version 1 stored just one address.
    old_address = data.get("address", "")
    if isinstance(old_address, str) and old_address.strip():
        return {
            "version": 4,
            "selected": "My Badge",
            "badges": {"My Badge": old_address.strip()},
            "custom": custom,
            "theme": "light",
        }
    return {
        "version": 4, "selected": "", "badges": {}, "custom": custom,
        "theme": "light",
    }


def load_config() -> dict:
    local_config = None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        local_config = normalize_config(data)
        if local_config["badges"]:
            return local_config
    except (OSError, ValueError):
        pass

    try:
        data = json.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8"))
        legacy_config = normalize_config(data)
        if legacy_config["badges"]:
            return legacy_config
    except (OSError, ValueError):
        pass

    return local_config or normalize_config({})


def save_config(config: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(normalize_config(config), indent=2),
            encoding="utf-8",
        )
    except OSError:
        # A read-only home folder should not prevent status updates.
        pass


class WorkStatusController:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(fitted_window_geometry(screen_width, screen_height))
        self.root.minsize(
            min(820, max(640, screen_width - 80)),
            min(620, max(500, screen_height - 96)),
        )
        self.root.configure(bg="#eaf2f8")

        config = load_config()
        device_config = load_device_config()
        self.device_id = device_config["id"]
        self.device_name = device_config["name"]
        self.device_keys: dict[str, str] = dict(device_config["keys"])
        self.profiles: dict[str, str] = dict(config["badges"])
        selected = config["selected"]
        selected_address = self.profiles.get(selected, "")
        self.device_var = tk.StringVar(value=selected)
        self.profile_name_var = tk.StringVar(value=selected)
        self.address_var = tk.StringVar(value=selected_address)
        self.note_var = tk.StringVar()
        self.note_count_var = tk.StringVar(value="0 / 24")
        custom = config["custom"]
        symbol_label = next(
            label for label, value in CUSTOM_SYMBOLS.items()
            if value == custom["symbol"]
        )
        self.custom_text_var = tk.StringVar(value=custom["text"])
        self.custom_symbol_var = tk.StringVar(value=symbol_label)
        self.custom_color = custom["color"]
        self.theme_name = config.get("theme", "light")
        self.current_var = tk.StringVar(value="NOT CONNECTED")
        self.address_visible = False
        self.connection_var = tk.StringVar(
            value="Choose a saved badge or add one to get started."
        )
        self.current_status: str | None = None
        self.current_payload: dict | None = None
        self.badge_battery: int | None = None
        self.badge_charging = False
        self.busy = False
        self.closing = False
        self.fullscreen = False
        self.windowed_geometry = self.root.geometry()
        self.widget_window: tk.Toplevel | None = None
        self.widget_buttons: dict[str, tk.Button] = {}
        self.widget_battery_var = tk.StringVar(value="--%")
        self._responsive_after: str | None = None
        self.pairing_active = False
        self.result_queue: queue.Queue[tuple[str, object, str]] = queue.Queue()
        self.status_buttons: dict[str, tk.Button] = {}

        self._build_ui()
        self.note_var.trace_add("write", self._limit_note)
        self.custom_text_var.trace_add("write", self._limit_custom_text)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self._escape_view)
        self.root.bind("<Control-Shift-W>", self.open_widget)
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.after(100, self._poll_results)
        self.root.after(60000, self._refresh_battery_periodically)
        self.root.after_idle(self._apply_responsive_layout)

        if self.address_var.get().strip():
            self.root.after(250, self.refresh_status)

    def _build_ui(self) -> None:
        palette = THEMES[self.theme_name]
        bg = palette["bg"]
        panel = palette["panel"]
        panel_raised = palette["raised"]
        field = palette["field"]
        border = palette["border"]
        border_hot = palette["border_hot"]
        text = palette["text"]
        muted = palette["muted"]
        cyan = palette["cyan"]
        violet = palette["violet"]
        self.root.configure(bg=bg)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Dark.TEntry",
            fieldbackground=field,
            foreground=text,
            insertcolor=text,
            bordercolor=border,
            lightcolor=field,
            darkcolor=border,
            padding=10,
        )
        style.map(
            "Dark.TEntry",
            bordercolor=[("focus", border_hot)],
            lightcolor=[("focus", border_hot)],
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=field,
            background=panel_raised,
            foreground=text,
            arrowcolor=cyan,
            bordercolor=border,
            lightcolor="#e8f3f9",
            darkcolor=border,
            padding=10,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", field)],
            foreground=[("readonly", text)],
            selectbackground=[("readonly", field)],
            selectforeground=[("readonly", text)],
        )

        def card(parent):
            shell = tk.Frame(
                parent,
                bg=border,
                highlightbackground=border,
                highlightthickness=1,
            )
            body = tk.Frame(
                shell,
                bg=panel,
                highlightbackground=border,
                highlightthickness=1,
            )
            body.pack(fill="both", expand=True, padx=2, pady=2)
            return shell, body

        def small_button(
            parent,
            label,
            command,
            fg=text,
            button_bg=panel_raised,
        ):
            return tk.Button(
                parent,
                text=label,
                command=command,
                bg=button_bg,
                fg=fg,
                activebackground=palette["active"],
                activeforeground=text,
                relief="flat",
                bd=0,
                padx=13,
                highlightthickness=1,
                highlightbackground=border,
                font=("Segoe UI Semibold", 9),
                cursor="hand2",
            )

        # Command-deck header with a restrained grid and live telemetry.
        self.hero = tk.Canvas(
            self.root,
            height=154,
            bg=panel,
            highlightthickness=0,
        )
        self.hero.pack(fill="x", padx=22, pady=(12, 8))
        self.hero.create_rectangle(0, 0, 1100, 154, fill=panel, outline="")
        for x in range(0, 1100, 36):
            self.hero.create_line(x, 0, x, 154, fill=border)
        for y in range(10, 154, 27):
            self.hero.create_line(0, y, 1100, y, fill=border)
        self.hero.create_polygon(
            390, 0, 580, 0, 485, 154, 295, 154,
            fill=palette["raised"], outline="",
        )
        self.hero.create_oval(
            250, -170, 610, 190, fill=panel, outline="",
        )
        self.hero.create_oval(
            360, -180, 650, 110, fill=palette["raised"], outline="",
        )
        self.hero.create_line(0, 153, 1100, 153, fill=border_hot, width=2)
        self.hero.create_rectangle(
            18, 16, 111, 36, fill=palette["raised"], outline=border,
        )
        self.hero.create_text(
            64, 26, text="TUFTY // LINK", fill=cyan,
            font=("Consolas", 8, "bold"),
        )
        self.hero.create_text(
            18, 67, text="STATUS COMMAND", anchor="w", fill=text,
            font=("Segoe UI Semibold", 24),
        )
        self.hero_subtitle = self.hero.create_text(
            19, 95,
            text="Broadcast your workspace signal",
            anchor="w",
            fill=muted,
            font=("Segoe UI", 10),
        )
        self.current_badge = self.hero.create_text(
            19, 120,
            text=self.current_var.get(),
            anchor="w",
            fill=cyan,
            font=("Consolas", 8, "bold"),
        )
        self.theme_button = tk.Button(
            self.hero,
            text="☾  DARK" if self.theme_name == "light" else "☀  LIGHT",
            command=self.toggle_theme,
            bg=panel_raised,
            fg=text,
            activebackground=palette["active"],
            activeforeground=text,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=border,
            font=("Segoe UI Semibold", 8),
            cursor="hand2",
        )
        self.theme_button.place(x=282, y=109, width=92, height=27)

        def chip(x1, x2, title):
            self.hero.create_rectangle(
                x1 + 2, 24, x2 + 2, 100, fill=border, outline="",
                tags="wide_header",
            )
            self.hero.create_rectangle(
                x1, 22, x2, 98, fill=panel_raised, outline=border, width=1,
                tags="wide_header",
            )
            self.hero.create_line(
                x1 + 1, 23, x2 - 1, 23, fill=border_hot,
                tags="wide_header",
            )
            self.hero.create_line(
                x1 + 9, 89, x1 + 27, 89, fill=cyan, width=2,
                tags="wide_header",
            )
            self.hero.create_text(
                x1 + 13, 42,
                text=title,
                anchor="w",
                fill=muted,
                font=("Consolas", 7, "bold"),
                tags="wide_header",
            )

        chip(402, 510, "LINK STATE")
        self.connection_dot = self.hero.create_oval(
            416, 59, 426, 69, fill="#6e7f93", outline="",
            tags="wide_header",
        )
        self.connection_state_display = self.hero.create_text(
            434, 64,
            text="OFFLINE",
            anchor="w",
            fill="#a9b8c9",
            font=("Consolas", 9, "bold"),
            tags="wide_header",
        )
        chip(520, 628, "BADGE POWER")
        self.battery_display = self.hero.create_text(
            534, 64,
            text="--%",
            anchor="w",
            fill=muted,
            font=("Consolas", 13, "bold"),
            tags="wide_header",
        )
        chip(638, 756, "ACTIVE SIGNAL")
        self.current_status_display = self.hero.create_text(
            652, 64,
            text="—",
            anchor="w",
            fill=text,
            font=("Segoe UI Semibold", 10),
            tags="wide_header",
        )

        self.preview_shell = tk.Frame(
            self.hero,
            bg=border,
            highlightbackground=border,
            highlightthickness=1,
        )
        self.preview_shell.place(
            relx=1.0, x=-238, y=8, width=224, height=138,
        )
        self.current_badge_preview = tk.Canvas(
            self.preview_shell,
            bg="#080a0f",
            highlightthickness=0,
        )
        self.current_badge_preview.pack(
            fill="both", expand=True, padx=4, pady=4,
        )
        self.current_badge_preview.bind(
            "<Configure>",
            lambda _event: self._update_current_badge_preview(),
        )
        self._update_current_badge_preview()

        self.fullscreen_button = tk.Button(
            self.hero,
            text="FULL SCREEN",
            command=self.toggle_fullscreen,
            bg=panel_raised,
            fg=text,
            activebackground=palette["active"],
            activeforeground=text,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=border,
            font=("Segoe UI Semibold", 8),
            cursor="hand2",
        )
        self.fullscreen_button.place(x=384, y=109, width=104, height=27)
        self.widget_button = tk.Button(
            self.hero,
            text="DESKTOP WIDGET",
            command=self.open_widget,
            bg=panel_raised,
            fg=cyan,
            activebackground=palette["active"],
            activeforeground=text,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=border,
            font=("Segoe UI Semibold", 8),
            cursor="hand2",
        )
        self.widget_button.place(x=498, y=109, width=120, height=27)

        # Connection bay.
        self.profile_shell, profile = card(self.root)
        self.profile_shell.pack(fill="x", padx=22, pady=(0, 12))
        heading = tk.Frame(profile, bg=panel)
        heading.pack(fill="x", padx=16, pady=(10, 6))
        tk.Label(
            heading,
            text="01  BADGE UPLINK",
            bg=panel,
            fg=text,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")
        tk.Label(
            heading,
            text="PRESS C ON BADGE FOR ADDRESS",
            bg=panel,
            fg=cyan,
            font=("Consolas", 8, "bold"),
        ).pack(side="right")

        selector = tk.Frame(profile, bg=panel)
        selector.pack(fill="x", padx=16, pady=(0, 7))
        self.device_picker = ttk.Combobox(
            selector,
            textvariable=self.device_var,
            values=sorted(self.profiles),
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 10),
        )
        self.device_picker.pack(side="left", fill="x", expand=True)
        self.device_picker.bind("<<ComboboxSelected>>", self._select_profile)
        self.new_badge_button = small_button(
            selector, "+ NEW BADGE", self.new_profile, fg=cyan,
        )
        self.new_badge_button.pack(side="left", padx=(8, 0), ipady=4)
        self.refresh_button = small_button(
            selector,
            "CONNECT",
            self.refresh_status,
            fg="#031019",
            button_bg=cyan,
        )
        self.refresh_button.pack(side="left", padx=(8, 0), ipady=4)

        editor = tk.Frame(profile, bg=panel)
        editor.pack(fill="x", padx=16, pady=(0, 10))
        ttk.Entry(
            editor,
            textvariable=self.profile_name_var,
            style="Dark.TEntry",
            font=("Segoe UI", 10),
        ).pack(side="left", fill="x", expand=True)
        self.address_entry = ttk.Entry(
            editor,
            textvariable=self.address_var,
            style="Dark.TEntry",
            font=("Consolas", 10),
            show="\u2022",
        )
        self.address_entry.pack(
            side="left", fill="x", expand=True, padx=(8, 0),
        )
        self.address_entry.bind(
            "<Return>", lambda _event: self.save_profile(),
        )
        self.visibility_button = small_button(
            editor, "SHOW IP", self.toggle_address_visibility, fg=cyan,
        )
        self.visibility_button.pack(side="left", padx=(6, 0), ipady=4)
        self.save_button = small_button(editor, "SAVE", self.save_profile)
        self.save_button.pack(side="left", padx=(8, 0), ipady=4)
        self.delete_button = small_button(
            editor, "FORGET", self.forget_profile, fg="#ff7185",
        )
        self.delete_button.pack(side="left", padx=(6, 0), ipady=4)

        # Preset signals and custom composer.
        self.content = tk.Frame(self.root, bg=bg)
        self.content.pack(fill="both", expand=True, padx=22)
        self.content.columnconfigure(0, weight=11, uniform="content")
        self.content.columnconfigure(1, weight=9, uniform="content")
        self.content.rowconfigure(0, weight=1)
        status_shell, status_card = card(self.content)
        status_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        custom_shell, custom_card = card(self.content)
        custom_shell.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        note_head = tk.Frame(status_card, bg=panel)
        note_head.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(
            note_head,
            text="02  QUICK SIGNALS",
            bg=panel,
            fg=text,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")
        tk.Label(
            note_head,
            textvariable=self.note_count_var,
            bg=panel,
            fg=muted,
            font=("Consolas", 9),
        ).pack(side="right")
        tk.Label(
            status_card,
            text="Optional transmission note",
            bg=panel,
            fg=muted,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=16, pady=(2, 0))
        ttk.Entry(
            status_card,
            textvariable=self.note_var,
            style="Dark.TEntry",
            font=("Segoe UI", 11),
        ).pack(fill="x", padx=16, pady=(7, 9))

        grid = tk.Frame(status_card, bg=panel)
        grid.pack(fill="both", expand=True, padx=11, pady=(0, 11))

        def tile_hover(button, tile, icon, active):
            tile_color = button._hover_bg if active else button._rest_bg
            button.configure(bg=tile_color)
            tile.configure(bg=tile_color)
            icon.configure(bg=tile_color)

        for index, (status, details) in enumerate(STATUSES.items()):
            label, subtitle, color = details
            if self.theme_name == "dark":
                tile_bg = darken_hex(color, 7)
                hover_bg = darken_hex(color, 5)
            else:
                tile_bg = tint_hex(color, 0.91)
                hover_bg = tint_hex(color, 0.82)
            tile = tk.Frame(
                grid,
                bg=tile_bg,
                highlightbackground=color,
                highlightthickness=1,
            )
            icon = tk.Canvas(
                tile,
                width=54,
                height=62,
                bg=tile_bg,
                highlightthickness=0,
                cursor="hand2",
            )
            icon.pack(side="left", padx=(6, 0), fill="y")
            self._draw_preset_symbol(
                icon, status, 27, 31, 0.36, color, tile_bg,
            )
            button = tk.Button(
                tile,
                text="%s\n%s" % (label.upper(), subtitle),
                command=lambda selected=status: self.send_status(selected),
                bg=tile_bg,
                fg=text,
                activebackground=hover_bg,
                activeforeground=text,
                disabledforeground="#92a2b2",
                relief="flat",
                bd=0,
                font=("Segoe UI Semibold", 10),
                cursor="hand2",
                justify="left",
                anchor="w",
                padx=8,
                highlightthickness=0,
            )
            button._rest_bg = tile_bg
            button._hover_bg = hover_bg
            button._tile_frame = tile
            button._icon_canvas = icon
            button.pack(side="left", fill="both", expand=True)
            button.bind(
                "<Enter>",
                lambda _event, item=button, frame=tile, glyph=icon:
                tile_hover(
                    item, frame, glyph, True,
                ),
            )
            button.bind(
                "<Leave>",
                lambda _event, item=button: self._restore_button_border(item),
            )
            tile.bind(
                "<Enter>",
                lambda _event, item=button, frame=tile, glyph=icon:
                tile_hover(item, frame, glyph, True),
            )
            tile.bind(
                "<Leave>",
                lambda _event, item=button: self._restore_button_border(item),
            )
            icon.bind(
                "<Button-1>",
                lambda _event, selected=status: self.send_status(selected),
            )
            tile.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=5,
                pady=5,
            )
            self.status_buttons[status] = button
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        for row in range(3):
            grid.rowconfigure(row, weight=1)

        self.custom_head = tk.Frame(custom_card, bg=panel)
        self.custom_head.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(
            self.custom_head,
            text="03  SIGNAL LAB",
            bg=panel,
            fg=text,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w")
        tk.Label(
            self.custom_head,
            text="Badge-accurate vector preview",
            bg=panel,
            fg=muted,
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(2, 0))

        self.custom_body = tk.Frame(custom_card, bg=panel)
        self.custom_body.pack(
            fill="both", expand=True, padx=15, pady=(0, 14),
        )
        self.custom_preview = tk.Canvas(
            self.custom_body,
            height=118,
            bg=darken_hex(self.custom_color),
            highlightbackground=border_hot,
            highlightthickness=1,
        )
        self.custom_preview.pack(fill="x")
        self.custom_preview.bind(
            "<Configure>", lambda _event: self._update_custom_preview(),
        )

        self.custom_editor = tk.Frame(self.custom_body, bg=panel)
        self.custom_editor.pack(fill="both", expand=True, pady=(9, 0))
        tk.Label(
            self.custom_editor,
            text="MESSAGE",
            bg=panel,
            fg=muted,
            font=("Consolas", 8, "bold"),
        ).pack(anchor="w")
        ttk.Entry(
            self.custom_editor,
            textvariable=self.custom_text_var,
            style="Dark.TEntry",
            font=("Segoe UI Semibold", 12),
        ).pack(fill="x", pady=(4, 8))
        options = tk.Frame(self.custom_editor, bg=panel)
        options.pack(fill="x")
        self.custom_symbol_picker = ttk.Combobox(
            options,
            textvariable=self.custom_symbol_var,
            values=list(CUSTOM_SYMBOLS),
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 9),
        )
        self.custom_symbol_picker.pack(side="left", fill="x", expand=True)
        self.custom_symbol_picker.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_custom_preview(),
        )
        self.custom_color_button = tk.Button(
            options,
            text=self.custom_color.upper(),
            command=self.choose_custom_color,
            bg=self.custom_color,
            fg="#ffffff",
            activebackground=self.custom_color,
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            highlightthickness=1,
            highlightbackground=border,
            font=("Consolas", 9),
            cursor="hand2",
        )
        self.custom_color_button.pack(
            side="left", padx=(7, 0), ipady=4,
        )
        self.custom_send_button = tk.Button(
            self.custom_editor,
            text="BROADCAST CUSTOM SIGNAL",
            command=self.send_custom,
            bg="#8957e5",
            fg="#ffffff",
            activebackground=violet,
            activeforeground="#ffffff",
            relief="flat",
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
            highlightthickness=1,
            highlightbackground="#bd93ff",
        )
        self.custom_send_button.pack(fill="x", pady=(9, 0), ipady=6)
        self._update_custom_preview()

        # Persistent system log.
        self.activity_shell, activity = card(self.root)
        self.activity_shell.pack(
            side="bottom",
            before=self.content,
            fill="x",
            padx=22,
            pady=(8, 10),
        )
        self.activity_dot = tk.Label(
            activity,
            text="\u25cf",
            bg=panel,
            fg="#6e7f93",
            font=("Segoe UI", 11),
        )
        self.activity_dot.pack(side="left", padx=(14, 7), pady=7)
        tk.Label(
            activity,
            text="SYSTEM LOG  //",
            bg=panel,
            fg=cyan,
            font=("Consolas", 8, "bold"),
        ).pack(side="left", padx=(0, 8))
        self.connection_label = tk.Label(
            activity,
            textvariable=self.connection_var,
            bg=panel,
            fg=muted,
            anchor="w",
            pady=7,
            font=("Segoe UI Semibold", 9),
        )
        self.connection_label.pack(side="left", fill="x", expand=True)
        tk.Label(
            activity,
            text="LOCAL WI-FI / PORT 8080",
            bg=panel,
            fg="#526a85",
            font=("Consolas", 7, "bold"),
        ).pack(side="right", padx=14)

    def _build_ui_previous(self) -> None:
        bg = "#070b14"
        glass = "#101827"
        field = "#0b1422"
        border = "#2a405e"
        text = "#f4f8ff"
        muted = "#8fa4bd"
        blue = "#58a6ff"

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Dark.TEntry",
            fieldbackground=field,
            foreground=text,
            insertcolor=text,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            padding=9,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=field,
            background="#17263b",
            foreground=text,
            arrowcolor=text,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            padding=9,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", field)],
            foreground=[("readonly", text)],
            selectbackground=[("readonly", field)],
            selectforeground=[("readonly", text)],
        )

        def card(parent):
            shell = tk.Frame(
                parent, bg="#09101c",
                highlightbackground="#1b2b42", highlightthickness=1,
            )
            body = tk.Frame(
                shell, bg=glass,
                highlightbackground=border, highlightthickness=1,
            )
            body.pack(fill="both", expand=True, padx=2, pady=2)
            return shell, body

        def small_button(parent, label, command, fg=text, button_bg="#17263b"):
            return tk.Button(
                parent, text=label, command=command,
                bg=button_bg, fg=fg,
                activebackground="#223752", activeforeground="#ffffff",
                relief="flat", bd=0, padx=12,
                font=("Segoe UI Semibold", 9), cursor="hand2",
            )

        # Gradient header and glass telemetry chips.
        self.hero = tk.Canvas(
            self.root, height=132, bg=bg, highlightthickness=0,
        )
        self.hero.pack(fill="x", padx=24, pady=(16, 8))
        for y in range(132):
            ratio = y / 131
            color = "#%02x%02x%02x" % (
                int(10 + 8 * ratio),
                int(22 + 10 * ratio),
                int(39 + 20 * ratio),
            )
            self.hero.create_line(0, y, 1000, y, fill=color)
        self.hero.create_oval(690, -115, 930, 125, fill="#152d57", outline="")
        self.hero.create_oval(790, -85, 990, 115, fill="#33235e", outline="")
        self.hero.create_line(0, 131, 1000, 131, fill="#355175")
        self.hero.create_text(
            20, 32, text="Work Status", anchor="w", fill=text,
            font=("Segoe UI Semibold", 27),
        )
        self.hero.create_text(
            21, 66, text="Your door sign control room", anchor="w", fill=muted,
            font=("Segoe UI", 11),
        )
        self.current_badge = self.hero.create_text(
            21, 99, text=self.current_var.get(), anchor="w", fill=blue,
            font=("Segoe UI Semibold", 10),
        )

        def chip(x1, x2, title):
            self.hero.create_rectangle(
                x1, 28, x2, 103, fill=field, outline="#345071", width=1,
            )
            self.hero.create_line(x1 + 1, 29, x2 - 1, 29, fill="#56779f")
            self.hero.create_text(
                x1 + 17, 47, text=title, anchor="w", fill=muted,
                font=("Segoe UI Semibold", 8),
            )

        chip(455, 590, "CONNECTION")
        self.connection_dot = self.hero.create_oval(
            472, 66, 482, 76, fill="#6e7f93", outline="",
        )
        self.connection_state_display = self.hero.create_text(
            490, 71, text="OFFLINE", anchor="w", fill="#a9b8c9",
            font=("Segoe UI Semibold", 10),
        )
        chip(600, 735, "BATTERY")
        self.battery_display = self.hero.create_text(
            617, 72, text="--%", anchor="w", fill=muted,
            font=("Segoe UI Semibold", 14),
        )
        chip(745, 890, "CURRENT STATUS")
        self.current_status_display = self.hero.create_text(
            762, 72, text="—", anchor="w", fill=text,
            font=("Segoe UI Semibold", 12),
        )

        # Saved badge profile.
        profile_shell, profile = card(self.root)
        profile_shell.pack(fill="x", padx=24, pady=(0, 12))
        heading = tk.Frame(profile, bg=glass)
        heading.pack(fill="x", padx=16, pady=(10, 7))
        tk.Label(
            heading, text="Badge connection", bg=glass, fg=text,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")
        tk.Label(
            heading, text="Profiles stay on this laptop", bg=glass, fg=muted,
            font=("Segoe UI", 8),
        ).pack(side="right")

        selector = tk.Frame(profile, bg=glass)
        selector.pack(fill="x", padx=16, pady=(0, 7))
        self.device_picker = ttk.Combobox(
            selector, textvariable=self.device_var,
            values=sorted(self.profiles), state="readonly",
            style="Dark.TCombobox", font=("Segoe UI", 10),
        )
        self.device_picker.pack(side="left", fill="x", expand=True)
        self.device_picker.bind("<<ComboboxSelected>>", self._select_profile)
        self.new_badge_button = small_button(
            selector, "+ New", self.new_profile, fg=blue,
        )
        self.new_badge_button.pack(side="left", padx=(8, 0), ipady=5)
        self.refresh_button = small_button(
            selector, "Connect", self.refresh_status,
            fg="#ffffff", button_bg="#1f6feb",
        )
        self.refresh_button.pack(side="left", padx=(8, 0), ipady=5)

        labels = tk.Frame(profile, bg=glass)
        labels.pack(fill="x", padx=18)
        tk.Label(
            labels, text="Profile name", bg=glass, fg=muted,
            font=("Segoe UI", 8),
        ).pack(side="left")
        tk.Label(
            labels, text="Badge IP address (press C on badge)",
            bg=glass, fg=muted, font=("Segoe UI", 8),
        ).pack(side="left", padx=(190, 0))
        editor = tk.Frame(profile, bg=glass)
        editor.pack(fill="x", padx=16, pady=(3, 11))
        ttk.Entry(
            editor, textvariable=self.profile_name_var,
            style="Dark.TEntry", font=("Segoe UI", 10),
        ).pack(side="left", fill="x", expand=True)
        self.address_entry = ttk.Entry(
            editor, textvariable=self.address_var, style="Dark.TEntry",
            font=("Consolas", 10), show="\u2022",
        )
        self.address_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.address_entry.bind("<Return>", lambda _event: self.save_profile())
        self.visibility_button = small_button(
            editor, "Show IP", self.toggle_address_visibility, fg=blue,
        )
        self.visibility_button.pack(side="left", padx=(6, 0), ipady=5)
        self.save_button = small_button(editor, "Save", self.save_profile)
        self.save_button.pack(side="left", padx=(8, 0), ipady=5)
        self.delete_button = small_button(
            editor, "Forget", self.forget_profile, fg="#ff7b72",
        )
        self.delete_button.pack(side="left", padx=(6, 0), ipady=5)

        # Status controls and custom status sit side by side.
        content = tk.Frame(self.root, bg=bg)
        content.pack(fill="both", expand=True, padx=24)
        content.columnconfigure(0, weight=3, uniform="content")
        content.columnconfigure(1, weight=2, uniform="content")
        content.rowconfigure(0, weight=1)
        status_shell, status_card = card(content)
        status_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        custom_shell, custom_card = card(content)
        custom_shell.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        note_head = tk.Frame(status_card, bg=glass)
        note_head.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(
            note_head, text="Choose a status", bg=glass, fg=text,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")
        tk.Label(
            note_head, textvariable=self.note_count_var, bg=glass, fg=muted,
            font=("Consolas", 9),
        ).pack(side="right")
        tk.Label(
            status_card, text="Optional message shown beneath the status",
            bg=glass, fg=muted, font=("Segoe UI", 8),
        ).pack(anchor="w", padx=16, pady=(2, 0))
        ttk.Entry(
            status_card, textvariable=self.note_var,
            style="Dark.TEntry", font=("Segoe UI", 11),
        ).pack(fill="x", padx=16, pady=(7, 9))

        grid = tk.Frame(status_card, bg=glass)
        grid.pack(fill="both", expand=True, padx=11, pady=(0, 11))
        for index, (status, details) in enumerate(STATUSES.items()):
            label, subtitle, color = details
            button = tk.Button(
                grid, text=label.upper() + "\n" + subtitle,
                command=lambda selected=status: self.send_status(selected),
                bg=color, fg="#ffffff", activebackground=color,
                activeforeground="#ffffff", disabledforeground="#c9d1d9",
                relief="flat", bd=0, font=("Segoe UI Semibold", 11),
                cursor="hand2",
            )
            button.bind(
                "<Enter>",
                lambda _event, item=button: item.configure(relief="raised", bd=2),
            )
            button.bind(
                "<Leave>",
                lambda _event, item=button: self._restore_button_border(item),
            )
            button.grid(
                row=index // 2, column=index % 2, sticky="nsew",
                padx=5, pady=5,
            )
            self.status_buttons[status] = button
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        for row in range(3):
            grid.rowconfigure(row, weight=1)

        custom_head = tk.Frame(custom_card, bg=glass)
        custom_head.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(
            custom_head, text="Create your own", bg=glass, fg=text,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w")
        tk.Label(
            custom_head, text="Preview exactly what you will send",
            bg=glass, fg=muted, font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(2, 0))
        custom_body = tk.Frame(custom_card, bg=glass)
        custom_body.pack(fill="both", expand=True, padx=15, pady=(0, 14))
        self.custom_preview = tk.Canvas(
            custom_body, height=137, bg=self.custom_color,
            highlightbackground="#ffffff", highlightthickness=1,
        )
        self.custom_preview.pack(fill="x")
        self.custom_preview_symbol = self.custom_preview.create_text(
            156, 45,
            text=SYMBOL_EMOJI[CUSTOM_SYMBOLS[self.custom_symbol_var.get()]],
            fill="#ffffff", font=("Segoe UI Emoji", 38),
        )
        self.custom_preview_text = self.custom_preview.create_text(
            156, 105, text=self.custom_text_var.get(), fill="#ffffff",
            width=285, justify="center", font=("Segoe UI Semibold", 13),
        )
        custom_editor = tk.Frame(custom_body, bg=glass)
        custom_editor.pack(fill="both", expand=True, pady=(9, 0))
        tk.Label(
            custom_editor, text="Status text", bg=glass, fg=muted,
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        ttk.Entry(
            custom_editor, textvariable=self.custom_text_var,
            style="Dark.TEntry", font=("Segoe UI Semibold", 12),
        ).pack(fill="x", pady=(4, 8))
        options = tk.Frame(custom_editor, bg=glass)
        options.pack(fill="x")
        self.custom_symbol_picker = ttk.Combobox(
            options, textvariable=self.custom_symbol_var,
            values=list(CUSTOM_SYMBOLS), state="readonly",
            style="Dark.TCombobox", font=("Segoe UI Emoji", 9),
        )
        self.custom_symbol_picker.pack(side="left", fill="x", expand=True)
        self.custom_symbol_picker.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_custom_preview(),
        )
        self.custom_color_button = tk.Button(
            options, text=self.custom_color.upper(),
            command=self.choose_custom_color, bg=self.custom_color,
            fg="#ffffff", activebackground=self.custom_color,
            activeforeground="#ffffff", relief="flat", padx=9,
            font=("Consolas", 9), cursor="hand2",
        )
        self.custom_color_button.pack(side="left", padx=(7, 0), ipady=5)
        self.custom_send_button = tk.Button(
            custom_editor, text="SEND CUSTOM STATUS",
            command=self.send_custom, bg="#8957e5", fg="#ffffff",
            activebackground="#a371f7", activeforeground="#ffffff",
            relief="flat", font=("Segoe UI Semibold", 10), cursor="hand2",
        )
        self.custom_send_button.pack(fill="x", pady=(9, 0), ipady=7)

        # Persistent activity strip gives every network action a clear result.
        activity_shell, activity = card(self.root)
        activity_shell.pack(fill="x", padx=24, pady=(12, 17))
        self.activity_dot = tk.Label(
            activity, text="\u25cf", bg=glass, fg="#6e7f93",
            font=("Segoe UI", 11),
        )
        self.activity_dot.pack(side="left", padx=(14, 6), pady=8)
        self.connection_label = tk.Label(
            activity, textvariable=self.connection_var, bg=glass, fg=muted,
            anchor="w", pady=8, font=("Segoe UI Semibold", 9),
        )
        self.connection_label.pack(side="left", fill="x", expand=True)
        tk.Label(
            activity, text="Local Wi-Fi", bg=glass, fg="#657b96",
            font=("Segoe UI", 8),
        ).pack(side="right", padx=14)

    def _build_ui_legacy(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Dark.TEntry",
            fieldbackground="#161b22",
            foreground="#f0f6fc",
            insertcolor="#f0f6fc",
            bordercolor="#30363d",
            lightcolor="#30363d",
            darkcolor="#30363d",
            padding=8,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground="#161b22",
            background="#21262d",
            foreground="#f0f6fc",
            arrowcolor="#f0f6fc",
            bordercolor="#30363d",
            lightcolor="#30363d",
            darkcolor="#30363d",
            padding=8,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", "#161b22")],
            foreground=[("readonly", "#f0f6fc")],
            selectbackground=[("readonly", "#161b22")],
            selectforeground=[("readonly", "#f0f6fc")],
        )

        self.hero = tk.Canvas(
            self.root,
            height=105,
            bg="#0d1117",
            highlightthickness=0,
        )
        self.hero.pack(fill="x", padx=28, pady=(18, 8))
        self.hero.create_oval(510, -70, 700, 120, fill="#1f6feb", outline="")
        self.hero.create_oval(570, -30, 730, 130, fill="#a371f7", outline="")
        self.hero.create_text(
            4, 27,
            text="WORK STATUS",
            anchor="w",
            fill="#f0f6fc",
            font=("Segoe UI Semibold", 26),
        )
        self.hero.create_text(
            5, 62,
            text="A tiny control room for your door sign",
            anchor="w",
            fill="#8b949e",
            font=("Segoe UI", 11),
        )
        self.current_badge = self.hero.create_text(
            5, 88,
            text=self.current_var.get(),
            anchor="w",
            fill="#58a6ff",
            font=("Segoe UI Semibold", 9),
        )
        self.hero.create_rectangle(
            475, 70, 615, 100,
            fill="#0d1117",
            outline="#30363d",
            width=1,
        )
        self.battery_display = self.hero.create_text(
            545, 85,
            text="BATTERY  --%",
            fill="#8b949e",
            font=("Segoe UI Semibold", 10),
        )

        profile_card = tk.Frame(
            self.root,
            bg="#161b22",
            highlightbackground="#30363d",
            highlightthickness=1,
        )
        profile_card.pack(fill="x", padx=28, pady=(0, 16))

        card_header = tk.Frame(profile_card, bg="#161b22")
        card_header.pack(fill="x", padx=16, pady=(13, 8))
        tk.Label(
            card_header,
            text="BADGE PROFILES",
            bg="#161b22",
            fg="#8b949e",
            font=("Segoe UI Semibold", 9),
        ).pack(side="left")
        tk.Label(
            card_header,
            text="Saved only on this laptop",
            bg="#161b22",
            fg="#6e7681",
            font=("Segoe UI", 8),
        ).pack(side="right")

        selector_row = tk.Frame(profile_card, bg="#161b22")
        selector_row.pack(fill="x", padx=16, pady=(0, 10))
        self.device_picker = ttk.Combobox(
            selector_row,
            textvariable=self.device_var,
            values=sorted(self.profiles),
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 10),
        )
        self.device_picker.pack(side="left", fill="x", expand=True)
        self.device_picker.bind("<<ComboboxSelected>>", self._select_profile)
        self.new_badge_button = tk.Button(
            selector_row,
            text="New Badge",
            command=self.new_profile,
            bg="#21262d",
            fg="#58a6ff",
            activebackground="#30363d",
            activeforeground="#79c0ff",
            relief="flat",
            padx=14,
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        )
        self.new_badge_button.pack(side="left", padx=(8, 0), ipady=5)
        self.refresh_button = tk.Button(
            selector_row,
            text="Connect",
            command=self.refresh_status,
            bg="#238636",
            fg="#ffffff",
            activebackground="#2ea043",
            activeforeground="#ffffff",
            relief="flat",
            padx=18,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        self.refresh_button.pack(side="left", padx=(8, 0), ipady=5)

        editor_labels = tk.Frame(profile_card, bg="#161b22")
        editor_labels.pack(fill="x", padx=18)
        tk.Label(
            editor_labels,
            text="Profile name",
            bg="#161b22",
            fg="#6e7681",
            font=("Segoe UI", 8),
        ).pack(side="left")
        tk.Label(
            editor_labels,
            text="Badge address (press C on badge)",
            bg="#161b22",
            fg="#6e7681",
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(105, 0))

        editor_row = tk.Frame(profile_card, bg="#161b22")
        editor_row.pack(fill="x", padx=16, pady=(3, 14))
        name_entry = ttk.Entry(
            editor_row,
            textvariable=self.profile_name_var,
            style="Dark.TEntry",
            font=("Segoe UI", 10),
        )
        name_entry.pack(side="left", fill="x", expand=True)
        self.address_entry = ttk.Entry(
            editor_row,
            textvariable=self.address_var,
            style="Dark.TEntry",
            font=("Consolas", 10),
            show="•",
        )
        self.address_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.address_entry.bind("<Return>", lambda _event: self.save_profile())
        self.visibility_button = tk.Button(
            editor_row,
            text="Show IP",
            command=self.toggle_address_visibility,
            bg="#21262d",
            fg="#58a6ff",
            activebackground="#30363d",
            activeforeground="#79c0ff",
            relief="flat",
            padx=10,
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        )
        self.visibility_button.pack(side="left", padx=(6, 0), ipady=5)
        self.save_button = tk.Button(
            editor_row,
            text="Save Badge",
            command=self.save_profile,
            bg="#21262d",
            fg="#f0f6fc",
            activebackground="#30363d",
            activeforeground="#ffffff",
            relief="flat",
            padx=13,
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        )
        self.save_button.pack(side="left", padx=(8, 0), ipady=5)
        self.delete_button = tk.Button(
            editor_row,
            text="Forget",
            command=self.forget_profile,
            bg="#21262d",
            fg="#f85149",
            activebackground="#30363d",
            activeforeground="#ff7b72",
            relief="flat",
            padx=10,
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        )
        self.delete_button.pack(side="left", padx=(6, 0), ipady=5)

        note_header = tk.Frame(self.root, bg="#0d1117")
        note_header.pack(fill="x", padx=30)
        tk.Label(
            note_header,
            text="MESSAGE ON THE DOOR",
            bg="#0d1117",
            fg="#8b949e",
            font=("Segoe UI Semibold", 9),
        ).pack(side="left")
        tk.Label(
            note_header,
            textvariable=self.note_count_var,
            bg="#0d1117",
            fg="#6e7681",
            font=("Consolas", 9),
        ).pack(side="right")
        note_entry = ttk.Entry(
            self.root,
            textvariable=self.note_var,
            style="Dark.TEntry",
            font=("Segoe UI", 12),
        )
        note_entry.pack(fill="x", padx=28, pady=(6, 16))

        button_grid = tk.Frame(self.root, bg="#0d1117")
        button_grid.pack(fill="both", expand=True, padx=23)
        for index, (status, details) in enumerate(STATUSES.items()):
            label, subtitle, color = details
            button = tk.Button(
                button_grid,
                text=label.upper() + "\n" + subtitle,
                command=lambda selected=status: self.send_status(selected),
                bg=color,
                fg="#ffffff",
                activebackground=color,
                activeforeground="#ffffff",
                disabledforeground="#c9d1d9",
                relief="flat",
                bd=0,
                font=("Segoe UI Semibold", 13),
                cursor="hand2",
            )
            button.bind(
                "<Enter>",
                lambda _event, item=button: item.configure(relief="raised", bd=2),
            )
            button.bind(
                "<Leave>",
                lambda _event, item=button: self._restore_button_border(item),
            )
            button.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=5,
                pady=5,
            )
            self.status_buttons[status] = button
        button_grid.columnconfigure(0, weight=1)
        button_grid.columnconfigure(1, weight=1)
        button_grid.rowconfigure(0, weight=1)
        button_grid.rowconfigure(1, weight=1)
        button_grid.rowconfigure(2, weight=1)

        custom_card = tk.Frame(
            self.root,
            bg="#161b22",
            highlightbackground="#30363d",
            highlightthickness=1,
        )
        custom_card.pack(fill="x", padx=28, pady=(14, 0))
        custom_heading = tk.Frame(custom_card, bg="#161b22")
        custom_heading.pack(fill="x", padx=16, pady=(13, 8))
        tk.Label(
            custom_heading,
            text="CREATE YOUR OWN STATUS",
            bg="#161b22",
            fg="#f0f6fc",
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w")
        tk.Label(
            custom_heading,
            text="Pick an emoji, color and message — preview it before sending",
            bg="#161b22",
            fg="#8b949e",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        custom_body = tk.Frame(custom_card, bg="#161b22")
        custom_body.pack(fill="x", padx=15, pady=(0, 15))
        self.custom_preview = tk.Canvas(
            custom_body,
            width=138,
            height=132,
            bg=self.custom_color,
            highlightthickness=0,
        )
        self.custom_preview.pack(side="left")
        self.custom_preview_symbol = self.custom_preview.create_text(
            69, 48,
            text=SYMBOL_EMOJI[CUSTOM_SYMBOLS[self.custom_symbol_var.get()]],
            fill="#ffffff",
            font=("Segoe UI Emoji", 38),
        )
        self.custom_preview_text = self.custom_preview.create_text(
            69, 102,
            text=self.custom_text_var.get(),
            fill="#ffffff",
            width=124,
            justify="center",
            font=("Segoe UI Semibold", 11),
        )

        custom_editor = tk.Frame(custom_body, bg="#161b22")
        custom_editor.pack(side="left", fill="both", expand=True, padx=(14, 0))
        tk.Label(
            custom_editor,
            text="Status text",
            bg="#161b22",
            fg="#8b949e",
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        custom_text_entry = ttk.Entry(
            custom_editor,
            textvariable=self.custom_text_var,
            style="Dark.TEntry",
            font=("Segoe UI Semibold", 13),
        )
        custom_text_entry.pack(fill="x", pady=(4, 10))

        custom_options = tk.Frame(custom_editor, bg="#161b22")
        custom_options.pack(fill="x")
        self.custom_symbol_picker = ttk.Combobox(
            custom_options,
            textvariable=self.custom_symbol_var,
            values=list(CUSTOM_SYMBOLS),
            state="readonly",
            width=16,
            style="Dark.TCombobox",
            font=("Segoe UI Emoji", 10),
        )
        self.custom_symbol_picker.pack(side="left", fill="x", expand=True)
        self.custom_symbol_picker.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_custom_preview(),
        )
        self.custom_color_button = tk.Button(
            custom_options,
            text="Choose color  " + self.custom_color.upper(),
            command=self.choose_custom_color,
            bg=self.custom_color,
            fg="#ffffff",
            activebackground=self.custom_color,
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            font=("Consolas", 9),
            cursor="hand2",
        )
        self.custom_color_button.pack(side="left", padx=(8, 0), ipady=5)
        self.custom_send_button = tk.Button(
            custom_editor,
            text="SEND MY CUSTOM STATUS",
            command=self.send_custom,
            bg="#8957e5",
            fg="#ffffff",
            activebackground="#a371f7",
            activeforeground="#ffffff",
            relief="flat",
            font=("Segoe UI Semibold", 11),
            cursor="hand2",
        )
        self.custom_send_button.pack(fill="x", pady=(10, 0), ipady=7)

        self.connection_label = tk.Label(
            self.root,
            textvariable=self.connection_var,
            bg="#161b22",
            fg="#8b949e",
            anchor="w",
            padx=14,
            pady=11,
            font=("Segoe UI Semibold", 9),
        )
        self.connection_label.pack(fill="x", padx=28, pady=(15, 22))

    def _limit_note(self, *_args) -> None:
        value = self.note_var.get()
        if len(value) > 24:
            self.note_var.set(value[:24])
            return
        self.note_count_var.set("%d / 24" % len(value))

    def _limit_custom_text(self, *_args) -> None:
        value = self.custom_text_var.get()
        if len(value) > 24:
            self.custom_text_var.set(value[:24])
            return
        self._update_custom_preview()

    def _update_current_badge_preview(self) -> None:
        """Render the last state received from the badge as a live screen."""
        canvas = getattr(self, "current_badge_preview", None)
        if canvas is None:
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), 212)
        height = max(canvas.winfo_height(), 128)
        payload = self.current_payload

        if not payload:
            palette = THEMES[self.theme_name]
            canvas.configure(bg=palette["raised"])
            canvas.create_text(
                width / 2, 48,
                text="BADGE DISPLAY",
                fill=palette["text"],
                font=("Consolas", 10, "bold"),
            )
            canvas.create_text(
                width / 2, 72,
                text="CONNECT TO SYNC",
                fill=palette["muted"],
                font=("Consolas", 7),
            )
            return

        status = payload.get("status", "available")
        palettes = {
            "available": ("#080a0f", "#2ebe5c"),
            "meeting": ("#3e1217", "#f85149"),
            "focus": ("#24143a", "#b878ff"),
            "away": ("#3b270d", "#ffb848"),
            "lunch": ("#3d1d09", "#ff9f43"),
            "sleep": ("#091b3d", "#58a6ff"),
        }
        if status == "custom":
            accent = str(payload.get("custom_color", "#1F6FEB"))
            if len(accent) != 7:
                accent = "#1F6FEB"
            background = darken_hex(accent)
        else:
            background, accent = palettes.get(status, palettes["available"])

        white = "#f5f7fa"
        canvas.configure(bg=background)
        if status == "custom":
            symbol = str(payload.get("custom_symbol", "star"))
            self._draw_preview_symbol(
                canvas, symbol, width / 2, 43, accent, white,
            )
            label = str(payload.get("custom_text", "CUSTOM"))[:24]
            note = ""
        else:
            self._draw_preset_symbol(
                canvas, status, width / 2, 43, 0.66, accent, background,
            )
            label = BADGE_LABELS.get(status, "STATUS")
            note = str(payload.get("note", ""))[:24]

        canvas.create_text(
            width / 2, 98,
            text=label,
            fill=white,
            width=width - 24,
            font=("Segoe UI Semibold", 10),
        )
        if note:
            canvas.create_text(
                width / 2, 114,
                text=note,
                fill=accent,
                width=width - 24,
                font=("Segoe UI", 7),
            )

        # Miniature battery indicator mirrors the badge's top-left telemetry.
        battery = self.badge_battery
        canvas.create_rectangle(
            8, 8, 25, 16, outline=white, width=1,
        )
        canvas.create_rectangle(25, 10, 27, 14, fill=white, outline="")
        if isinstance(battery, int):
            fill = round(14 * max(0, min(100, battery)) / 100)
            if fill:
                canvas.create_rectangle(
                    10, 10, 10 + fill, 14, fill=accent, outline="",
                )

    @staticmethod
    def _draw_preset_symbol(
        canvas: tk.Canvas,
        status: str,
        x: float,
        y: float,
        scale: float,
        accent: str,
        background: str,
    ) -> None:
        """Draw the same visual metaphor used by each badge preset."""
        def point(dx, dy):
            return x + dx * scale, y + dy * scale

        line_width = max(2, round(6 * scale))
        background_level = sum(
            int(background[index:index + 2], 16)
            for index in (1, 3, 5)
        ) / 3
        white = "#10213a" if background_level > 150 else "#f5f7fa"

        if status == "available":
            radius = 30 * scale
            canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=accent, outline="",
            )
            x1, y1 = point(-14, -28)
            x2, y2 = point(14, 30)
            canvas.create_rectangle(
                x1, y1, x2, y2, fill=background, outline="",
            )
            knob_x, knob_y = point(8, 3)
            knob = max(2, 3 * scale)
            canvas.create_oval(
                knob_x - knob, knob_y - knob,
                knob_x + knob, knob_y + knob,
                fill=accent, outline="",
            )
        elif status == "meeting":
            x1, y1 = point(-57, -29)
            x2, y2 = point(57, 29)
            canvas.create_rectangle(
                x1, y1, x2, y2,
                outline=accent, width=line_width,
            )
            head_x, head_y = point(-32, 0)
            radius = 10 * scale
            canvas.create_oval(
                head_x - radius, head_y - radius,
                head_x + radius, head_y + radius,
                fill=white, outline="",
            )
            for index, height_value in enumerate((14, 25, 18, 31)):
                left, bottom = point(-4 + index * 15, 21)
                right, top = point(5 + index * 15, 21 - height_value)
                canvas.create_rectangle(
                    left, top, right, bottom, fill=white, outline="",
                )
        elif status == "focus":
            x1, y1 = point(-58, -29)
            x2, y2 = point(58, 29)
            canvas.create_rectangle(
                x1, y1, x2, y2,
                outline=accent, width=line_width,
            )
            for y_offset, left, right in (
                (-17, -42, 13), (0, -24, 32), (17, -42, 2)
            ):
                start = point(left, y_offset)
                end = point(right, y_offset)
                canvas.create_line(
                    *start, *end,
                    fill=accent,
                    width=line_width,
                    capstyle=tk.ROUND,
                )
            cursor_a = point(37, 10)
            cursor_b = point(48, 22)
            canvas.create_rectangle(
                *cursor_a, *cursor_b, fill=white, outline="",
            )
        elif status == "away":
            x1, y1 = point(-33, -20)
            x2, y2 = point(28, 23)
            canvas.create_rectangle(
                x1, y1, x2, y2,
                outline=accent, width=line_width,
            )
            arc_a = point(18, -15)
            arc_b = point(48, 18)
            canvas.create_arc(
                *arc_a, *arc_b,
                start=275, extent=170, style="arc",
                outline=accent, width=line_width,
            )
            for offset in (-14, 12):
                steam_a = point(offset, -28)
                steam_b = point(offset + 6, -43)
                canvas.create_line(
                    *steam_a, *steam_b,
                    fill=white, width=max(2, round(4 * scale)),
                    capstyle=tk.ROUND,
                )
        elif status == "lunch":
            rim_a = point(-42, -11)
            rim_b = point(42, 20)
            canvas.create_rectangle(
                *rim_a, *rim_b, fill=accent, outline="",
            )
            inner_a = point(-34, -7)
            inner_b = point(34, 2)
            canvas.create_rectangle(
                *inner_a, *inner_b, fill=background, outline="",
            )
            for offset in (-23, 0, 23):
                dot_x, dot_y = point(offset, -2)
                radius = max(2, 3 * scale)
                canvas.create_oval(
                    dot_x - radius, dot_y - radius,
                    dot_x + radius, dot_y + radius,
                    fill=white, outline="",
                )
            base_a = point(-34, 26)
            base_b = point(34, 26)
            canvas.create_line(
                *base_a, *base_b,
                fill=white, width=line_width, capstyle=tk.ROUND,
            )
        else:
            radius = 29 * scale
            moon_x, moon_y = point(-8, -2)
            canvas.create_oval(
                moon_x - radius, moon_y - radius,
                moon_x + radius, moon_y + radius,
                fill=accent, outline="",
            )
            cover_x, cover_y = point(7, -13)
            canvas.create_oval(
                cover_x - radius, cover_y - radius,
                cover_x + radius, cover_y + radius,
                fill=background, outline="",
            )
            star_x, star_y = point(38, -17)
            star_r = max(2, 3 * scale)
            canvas.create_oval(
                star_x - star_r, star_y - star_r,
                star_x + star_r, star_y + star_r,
                fill=white, outline="",
            )

    def _update_custom_preview(self) -> None:
        symbol = CUSTOM_SYMBOLS.get(self.custom_symbol_var.get(), "star")
        canvas = self.custom_preview
        canvas.configure(bg=darken_hex(self.custom_color))
        canvas.delete("preview")

        width = max(canvas.winfo_width(), 320)
        height = canvas.winfo_height()
        cx = width / 2
        accent = self.custom_color
        white = "#f7fbff"
        shadow = darken_hex(self.custom_color, 3)

        if height < 90:
            canvas.create_line(
                14, 13, 42, 13, fill=accent, width=2, tags="preview",
            )
            canvas.create_text(
                14, 22,
                text="LIVE PREVIEW",
                anchor="w",
                fill="#91a7c1",
                font=("Consolas", 7, "bold"),
                tags="preview",
            )
            canvas.create_text(
                cx,
                height / 2 + 8,
                text=self.custom_text_var.get().strip().upper() or "CUSTOM",
                fill=white,
                width=max(width - 110, 180),
                justify="center",
                font=("Segoe UI Semibold", 12),
                tags="preview",
            )
            canvas.create_text(
                width - 14, 22,
                text=symbol.upper(),
                anchor="e",
                fill=accent,
                font=("Consolas", 8, "bold"),
                tags="preview",
            )
            return

        canvas.create_line(
            18, 17, 54, 17, fill=accent, width=2, tags="preview",
        )
        canvas.create_text(
            18, 25,
            text="LIVE PREVIEW",
            anchor="w",
            fill="#91a7c1",
            font=("Consolas", 7, "bold"),
            tags="preview",
        )
        canvas.create_oval(
            cx - 38, 4, cx + 38, 80,
            outline=shadow, width=2, tags="preview",
        )

        self._draw_preview_symbol(canvas, symbol, cx, 42, accent, white)
        canvas.create_text(
            cx, 98,
            text=self.custom_text_var.get().strip().upper() or "CUSTOM",
            fill=white,
            width=max(width - 40, 200),
            justify="center",
            font=("Segoe UI Semibold", 13),
            tags="preview",
        )
        canvas.create_text(
            width - 17, 25,
            text="160 x 120",
            anchor="e",
            fill="#91a7c1",
            font=("Consolas", 7),
            tags="preview",
        )

    @staticmethod
    def _draw_preview_symbol(
        canvas: tk.Canvas,
        symbol: str,
        x: float,
        y: float,
        accent: str,
        white: str,
    ) -> None:
        """Draw a font-independent symbol matching the badge vector art."""
        line = {
            "fill": accent,
            "width": 5,
            "capstyle": tk.ROUND,
            "joinstyle": tk.ROUND,
            "tags": "preview",
        }

        if symbol == "heart":
            canvas.create_polygon(
                x, y + 32,
                x - 31, y + 4,
                x - 31, y - 12,
                x - 23, y - 24,
                x - 10, y - 27,
                x, y - 15,
                x + 10, y - 27,
                x + 23, y - 24,
                x + 31, y - 12,
                x + 31, y + 4,
                smooth=True,
                splinesteps=24,
                fill=accent,
                outline=white,
                width=2,
                tags="preview",
            )
        elif symbol == "check":
            canvas.create_oval(
                x - 31, y - 31, x + 31, y + 31,
                outline=accent, width=5, tags="preview",
            )
            canvas.create_line(
                x - 18, y, x - 5, y + 14, x + 22, y - 16, **line,
            )
        elif symbol == "alert":
            canvas.create_polygon(
                x, y - 32,
                x - 34, y + 28,
                x + 34, y + 28,
                fill="",
                outline=accent,
                width=5,
                joinstyle=tk.ROUND,
                tags="preview",
            )
            canvas.create_line(x, y - 10, x, y + 9, **line)
            canvas.create_oval(
                x - 3, y + 17, x + 3, y + 23,
                fill=white, outline="", tags="preview",
            )
        elif symbol == "coffee":
            canvas.create_rectangle(
                x - 29, y - 17, x + 20, y + 21,
                outline=accent, width=5, tags="preview",
            )
            canvas.create_arc(
                x + 11, y - 10, x + 42, y + 17,
                start=275,
                extent=170,
                style="arc",
                outline=accent,
                width=5,
                tags="preview",
            )
            canvas.create_line(x - 38, y + 29, x + 38, y + 29, **line)
            canvas.create_line(
                x - 15, y - 27, x - 10, y - 37,
                fill=white,
                width=3,
                capstyle=tk.ROUND,
                tags="preview",
            )
            canvas.create_line(
                x + 6, y - 27, x + 11, y - 37,
                fill=white,
                width=3,
                capstyle=tk.ROUND,
                tags="preview",
            )
        elif symbol == "door":
            canvas.create_rectangle(
                x - 24, y - 34, x + 24, y + 34,
                outline=accent, width=5, tags="preview",
            )
            canvas.create_line(x - 12, y - 22, x + 12, y - 22, **line)
            canvas.create_line(x - 12, y + 16, x + 12, y + 16, **line)
            canvas.create_oval(
                x + 9, y - 2, x + 15, y + 4,
                fill=white, outline="", tags="preview",
            )
        elif symbol == "code":
            canvas.create_rectangle(
                x - 47, y - 31, x + 47, y + 31,
                outline=accent, width=4, tags="preview",
            )
            canvas.create_line(
                x - 19, y - 13, x - 32, y, x - 19, y + 13, **line,
            )
            canvas.create_line(
                x + 19, y - 13, x + 32, y, x + 19, y + 13, **line,
            )
            canvas.create_line(x + 8, y - 18, x - 8, y + 18, **line)
        elif symbol == "bolt":
            canvas.create_polygon(
                x + 10, y - 36,
                x - 22, y + 3,
                x - 4, y + 3,
                x - 14, y + 36,
                x + 24, y - 8,
                x + 5, y - 8,
                fill=accent,
                outline=white,
                width=2,
                tags="preview",
            )
        else:
            points = (
                x, y - 35,
                x + 10, y - 11,
                x + 36, y - 10,
                x + 16, y + 7,
                x + 23, y + 33,
                x, y + 19,
                x - 23, y + 33,
                x - 16, y + 7,
                x - 36, y - 10,
                x - 10, y - 11,
                x, y - 35,
            )
            canvas.create_line(*points, **line)

    def _persist_profiles(self) -> None:
        save_config({
            "version": 4,
            "selected": self.device_var.get(),
            "badges": self.profiles,
            "theme": self.theme_name,
            "custom": {
                "color": self.custom_color,
                "symbol": CUSTOM_SYMBOLS[self.custom_symbol_var.get()],
                "text": self.custom_text_var.get().strip()[:24] or "CUSTOM",
            },
        })
        save_device_config({
            "version": 1,
            "id": self.device_id,
            "name": self.device_name,
            "keys": self.device_keys,
        })

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        if self._responsive_after is not None:
            self.root.after_cancel(self._responsive_after)
        self._responsive_after = self.root.after(
            60, self._apply_responsive_layout,
        )

    def _apply_responsive_layout(self) -> None:
        """Keep the full dashboard usable on short and narrow displays."""
        self._responsive_after = None
        if self.closing or not self.root.winfo_exists():
            return
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        compact = height < 780
        wide_header = width >= 1000 and not compact
        outer_pad = 14 if width < 1000 else 22

        self.hero.configure(height=116 if compact else 154)
        self.hero.pack_configure(
            padx=outer_pad,
            pady=(6, 5) if compact else (12, 8),
        )
        self.profile_shell.pack_configure(
            padx=outer_pad,
            pady=(0, 7) if compact else (0, 12),
        )
        self.content.pack_configure(padx=outer_pad)
        self.activity_shell.pack_configure(
            padx=outer_pad,
            pady=(5, 5) if compact else (8, 10),
        )

        self.hero.itemconfigure(
            "wide_header",
            state="normal" if wide_header else "hidden",
        )
        if wide_header:
            self.preview_shell.place(
                relx=1.0, x=-238, y=8, width=224, height=138,
            )
        else:
            self.preview_shell.place_forget()

        if compact:
            self.hero.itemconfigure(self.hero_subtitle, state="hidden")
            self.hero.coords(self.current_badge, 19, 101)
            control_y = 80
            self.custom_preview.configure(height=66)
            self.custom_head.pack_configure(pady=(7, 3))
            self.custom_body.pack_configure(pady=(0, 8))
            self.custom_editor.pack_configure(pady=(4, 0))
            self.custom_send_button.pack_configure(pady=(5, 0), ipady=3)
        else:
            self.hero.itemconfigure(self.hero_subtitle, state="normal")
            self.hero.coords(self.current_badge, 19, 120)
            control_y = 109
            self.custom_preview.configure(height=118)
            self.custom_head.pack_configure(pady=(12, 8))
            self.custom_body.pack_configure(pady=(0, 14))
            self.custom_editor.pack_configure(pady=(9, 0))
            self.custom_send_button.pack_configure(pady=(9, 0), ipady=6)
        self.theme_button.place_configure(y=control_y)
        self.fullscreen_button.place_configure(y=control_y)
        self.widget_button.place_configure(y=control_y)
        self.fullscreen_button.configure(
            text="EXIT FULL SCREEN" if self.fullscreen else "FULL SCREEN",
        )

    def toggle_fullscreen(self, _event=None):
        """Toggle a borderless view; Escape always returns to a window."""
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.windowed_geometry = self.root.geometry()
        self.root.attributes("-fullscreen", self.fullscreen)
        self.root.after_idle(self._apply_responsive_layout)
        return "break"

    def _escape_view(self, _event=None):
        if self.widget_window is not None:
            self.close_widget()
        elif self.fullscreen:
            self.toggle_fullscreen()
        return "break"

    def open_widget(self, _event=None):
        """Open an always-on-top compact controller and hide the dashboard."""
        if self.widget_window is not None:
            self.widget_window.lift()
            return "break"

        palette = THEMES[self.theme_name]
        window = tk.Toplevel(self.root)
        self.widget_window = window
        window.title("Work Status Widget")
        window.configure(bg=palette["bg"])
        window.resizable(False, False)
        window.attributes("-topmost", True)
        window.protocol("WM_DELETE_WINDOW", self.close_widget)
        window.bind("<Escape>", lambda _event: self.close_widget())

        width, height = 410, 430
        x = max(0, window.winfo_screenwidth() - width - 24)
        y = 24
        window.geometry("%dx%d+%d+%d" % (width, height, x, y))

        header = tk.Frame(window, bg=palette["panel"])
        header.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(
            header,
            text="WORK STATUS",
            bg=palette["panel"],
            fg=palette["cyan"],
            font=("Consolas", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(
            header,
            textvariable=self.current_var,
            bg=palette["panel"],
            fg=palette["text"],
            font=("Segoe UI Semibold", 15),
            anchor="w",
        ).pack(fill="x", padx=12)
        tk.Label(
            header,
            textvariable=self.widget_battery_var,
            bg=palette["panel"],
            fg=palette["muted"],
            font=("Consolas", 9, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(2, 10))

        ttk.Entry(
            window,
            textvariable=self.note_var,
            style="Dark.TEntry",
            font=("Segoe UI", 10),
        ).pack(fill="x", padx=10, pady=(0, 7))

        grid = tk.Frame(window, bg=palette["bg"])
        grid.pack(fill="both", expand=True, padx=6)
        self.widget_buttons.clear()
        for index, (status, details) in enumerate(STATUSES.items()):
            label, _subtitle, color = details
            button_bg = (
                darken_hex(color, 6)
                if self.theme_name == "dark"
                else tint_hex(color, 0.9)
            )
            button = tk.Button(
                grid,
                text=label.upper(),
                command=lambda selected=status: self.send_status(selected),
                bg=button_bg,
                fg=palette["text"],
                activebackground=color,
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=color,
                font=("Segoe UI Semibold", 10),
                cursor="hand2",
            )
            button.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=4,
                pady=4,
            )
            self.widget_buttons[status] = button
        for column in range(2):
            grid.columnconfigure(column, weight=1)
        for row in range(3):
            grid.rowconfigure(row, weight=1)

        footer = tk.Frame(window, bg=palette["panel"])
        footer.pack(fill="x", padx=10, pady=(7, 10))
        tk.Label(
            footer,
            textvariable=self.connection_var,
            bg=palette["panel"],
            fg=palette["muted"],
            anchor="w",
            font=("Segoe UI", 8),
        ).pack(fill="x", padx=10, pady=(7, 4))
        controls = tk.Frame(footer, bg=palette["panel"])
        controls.pack(fill="x", padx=8, pady=(0, 7))
        tk.Button(
            controls,
            text="CONNECT",
            command=self.refresh_status,
            bg=palette["cyan"],
            fg="#031019",
            relief="flat",
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 4), ipady=4)
        tk.Button(
            controls,
            text="OPEN DASHBOARD",
            command=self.close_widget,
            bg=palette["raised"],
            fg=palette["text"],
            relief="flat",
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(4, 0), ipady=4)

        self.root.withdraw()
        self._highlight_current()
        window.lift()
        window.focus_force()
        return "break"

    def close_widget(self) -> None:
        if self.widget_window is not None:
            self.widget_window.destroy()
            self.widget_window = None
            self.widget_buttons.clear()
        if not self.closing:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

    def toggle_theme(self) -> None:
        """Switch themes while preserving the active controller state."""
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self._persist_profiles()
        for child in self.root.winfo_children():
            child.destroy()
        self.status_buttons.clear()
        self._build_ui()
        self.root.after_idle(self._apply_responsive_layout)
        if self.current_payload:
            self._request_succeeded(
                self.current_payload,
                self.connection_var.get(),
            )

    def choose_custom_color(self) -> None:
        _rgb, selected = colorchooser.askcolor(
            color=self.custom_color,
            title="Choose custom status color",
            parent=self.root,
        )
        if selected:
            self.custom_color = selected.upper()
            self.custom_color_button.configure(
                text=self.custom_color,
                bg=self.custom_color,
                activebackground=self.custom_color,
            )
            self._update_custom_preview()
            self._persist_profiles()

    def toggle_address_visibility(self) -> None:
        self.address_visible = not self.address_visible
        self.address_entry.configure(
            show="" if self.address_visible else "\u2022"
        )
        self.visibility_button.configure(
            text="Hide IP" if self.address_visible else "Show IP"
        )
        return
        self.address_visible = not self.address_visible
        self.address_entry.configure(show="" if self.address_visible else "•")
        self.visibility_button.configure(
            text="Hide IP" if self.address_visible else "Show IP"
        )

    def _select_profile(self, _event=None) -> None:
        name = self.device_var.get()
        self.profile_name_var.set(name)
        self.address_var.set(self.profiles.get(name, ""))
        self.current_status = None
        self.current_payload = None
        self.current_var.set(name.upper() if name else "NOT CONNECTED")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill="#10213a")
        self._update_current_badge_preview()
        self._set_connection_indicator("idle", "READY")
        self.connection_var.set("Profile loaded. Press Connect to check the badge.")
        self.connection_label.configure(fg="#8b949e")
        self._highlight_current()
        self._persist_profiles()

    def new_profile(self) -> None:
        self.device_var.set("")
        self.profile_name_var.set("")
        self.address_var.set("")
        self.current_status = None
        self.current_payload = None
        self.current_var.set("NEW BADGE")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill="#10213a")
        self._update_current_badge_preview()
        self._set_connection_indicator("idle", "SETUP")
        self._highlight_current()
        self.connection_var.set(
            "Enter a badge name and address, then select Save Badge."
        )
        self.connection_label.configure(fg="#58a6ff")

    def save_profile(self) -> None:
        name = self.profile_name_var.get().strip()[:32]
        if not name:
            self._show_error("Enter a name for this badge.")
            return
        try:
            normalized = badge_base_url(self.address_var.get())
        except ValueError as exc:
            self._show_error(str(exc))
            return

        address = normalized.removeprefix("http://")
        # A different name creates another profile. Existing badges remain
        # saved until explicitly removed with Forget.
        self.profiles[name] = address
        self.device_var.set(name)
        self.profile_name_var.set(name)
        self.address_var.set(address)
        self.device_picker.configure(values=sorted(self.profiles))
        self._persist_profiles()
        self.connection_var.set("Saved %s on this laptop." % name)
        self.connection_label.configure(fg="#58a6ff")

    def forget_profile(self) -> None:
        name = self.device_var.get()
        if name in self.profiles:
            del self.profiles[name]
        next_name = next(iter(sorted(self.profiles)), "")
        self.device_var.set(next_name)
        self.profile_name_var.set(next_name)
        self.address_var.set(self.profiles.get(next_name, ""))
        self.device_picker.configure(values=sorted(self.profiles))
        self.current_status = None
        self.current_payload = None
        self.current_var.set("NOT CONNECTED")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill="#10213a")
        self._update_current_badge_preview()
        self._set_connection_indicator("idle", "OFFLINE")
        self._highlight_current()
        self._persist_profiles()
        self.connection_var.set(
            "Profile forgotten." if name else "No saved profile selected."
        )
        self.connection_label.configure(fg="#8b949e")

    def _restore_button_border(self, button: tk.Button) -> None:
        for status, candidate in self.status_buttons.items():
            if candidate is button:
                resting = getattr(button, "_rest_bg", button["bg"])
                button.configure(bg=resting)
                tile = getattr(button, "_tile_frame", None)
                icon = getattr(button, "_icon_canvas", None)
                if tile is not None:
                    tile.configure(bg=resting)
                if icon is not None:
                    icon.configure(bg=resting)
                if status == self.current_status:
                    if tile is not None:
                        tile.configure(highlightthickness=3)
                else:
                    if tile is not None:
                        tile.configure(highlightthickness=1)
                return

    def _set_connection_indicator(self, state: str, label: str) -> None:
        colors = {
            "idle": ("#6e7f93", "#a9b8c9"),
            "connecting": ("#d29922", "#f2cc60"),
            "connected": ("#3fb950", "#56d364"),
            "error": ("#f85149", "#ff7b72"),
        }
        dot, foreground = colors.get(state, colors["idle"])
        self.hero.itemconfigure(self.connection_dot, fill=dot)
        self.hero.itemconfigure(
            self.connection_state_display,
            text=label,
            fill=foreground,
        )
        self.activity_dot.configure(fg=dot)

    def _set_busy(self, busy: bool, message: str) -> None:
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.refresh_button.configure(state=state)
        for button in self.status_buttons.values():
            button.configure(state=state)
        for button in self.widget_buttons.values():
            button.configure(state=state)
        self.custom_send_button.configure(state=state)
        self.connection_var.set(message)
        self.connection_label.configure(fg="#f2cc60" if busy else "#8fa4bd")
        if busy:
            self._set_connection_indicator("connecting", "CONNECTING…")

    def _make_badge_client(self) -> BadgeClient:
        base_url = badge_base_url(self.address_var.get())
        key = None
        key_hex = self.device_keys.get(base_url)
        if key_hex:
            try:
                key = binascii.unhexlify(key_hex)
            except (ValueError, binascii.Error):
                self.device_keys.pop(base_url, None)
        return BadgeClient(
            self.address_var.get(),
            device_id=self.device_id,
            device_name=self.device_name,
            device_key=key,
        )

    def _has_pairing_key(self) -> bool:
        try:
            return badge_base_url(self.address_var.get()) in self.device_keys
        except ValueError:
            return False

    def start_pairing(self) -> None:
        if self.busy:
            return
        try:
            client = self._make_badge_client()
        except ValueError as exc:
            self._show_error(str(exc))
            return

        self.pairing_active = True
        self._set_busy(True, "Requesting secure pairing...")

        def worker() -> None:
            try:
                pairing = client.begin_pairing()
                self.result_queue.put((
                    "pair_pending",
                    {"code": pairing["code"]},
                    "",
                ))
                deadline = time.monotonic() + PAIRING_TIMEOUT
                while time.monotonic() < deadline and not self.closing:
                    time.sleep(0.75)
                    status = client.get_pairing_status()
                    if status == "approved":
                        self.result_queue.put((
                            "pair_approved",
                            {
                                "address": client.base_url,
                                "key": binascii.hexlify(
                                    pairing["key"]
                                ).decode("ascii"),
                            },
                            "Secure pairing complete.",
                        ))
                        return
                    if status in ("rejected", "expired", "unknown"):
                        raise RuntimeError(
                            "Pairing was %s on the badge." % status
                        )
                raise RuntimeError(
                    "Pairing timed out. Select Connect to retry."
                )
            except (error.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                self.result_queue.put((
                    "error", None, "Cannot reach badge: %s" % reason,
                ))
            except (ValueError, RuntimeError) as exc:
                self.result_queue.put(("error", None, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _run_request(self, operation, success_message: str) -> None:
        if self.busy:
            return
        try:
            client = self._make_badge_client()
        except ValueError as exc:
            self._show_error(str(exc))
            return

        self._set_busy(True, "Contacting badge...")

        def worker() -> None:
            try:
                result = operation(client)
            except PairingRequired as exc:
                self.result_queue.put((
                    "pair_required",
                    {"address": client.base_url},
                    str(exc),
                ))
            except error.HTTPError as exc:
                message = "Badge rejected the request (HTTP %d)." % exc.code
                self.result_queue.put(("error", None, message))
            except (error.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                message = "Cannot reach badge: %s" % reason
                self.result_queue.put(("error", None, message))
            except (ValueError, RuntimeError) as exc:
                self.result_queue.put(("error", None, str(exc)))
            else:
                self.result_queue.put(("success", result, success_message))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_results(self) -> None:
        try:
            while True:
                kind, result, message = self.result_queue.get_nowait()
                if kind == "success":
                    self._request_succeeded(result, message)
                elif kind == "pair_pending":
                    code = result["code"]
                    self.current_var.set("PAIRING CODE  " + code)
                    self.hero.itemconfigure(
                        self.current_badge, text=self.current_var.get(),
                    )
                    self.connection_var.set(
                        "Confirm %s on the badge, then press UP to approve."
                        % code
                    )
                    self.connection_label.configure(fg="#f2cc60")
                    self._set_connection_indicator(
                        "connecting", "VERIFY CODE",
                    )
                elif kind == "pair_approved":
                    self.device_keys[result["address"]] = result["key"]
                    self.pairing_active = False
                    self._persist_profiles()
                    self._set_busy(False, message)
                    self.connection_label.configure(fg="#3fb950")
                    self._set_connection_indicator("connected", "PAIRED")
                    self.root.after(150, self.refresh_status)
                elif kind == "pair_required":
                    self.device_keys.pop(result["address"], None)
                    self._persist_profiles()
                    self._set_busy(False, message)
                    self.start_pairing()
                else:
                    self.pairing_active = False
                    self._request_failed(message)
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(100, self._poll_results)

    def _refresh_battery_periodically(self) -> None:
        if self.closing:
            return
        if self.address_var.get().strip() and not self.busy:
            self._run_request(
                lambda client: client.get_status(),
                "Badge battery refreshed.",
            )
        self.root.after(60000, self._refresh_battery_periodically)

    def _update_battery_display(self, result: dict) -> None:
        level = result.get("battery")
        charging = bool(result.get("charging", False))
        if isinstance(level, int):
            level = max(0, min(100, level))
            suffix = "  CHARGING" if charging else ""
            text = "%d%%%s" % (level, suffix)
            color = "#f85149" if level <= 20 else (
                "#d29922" if level <= 50 else "#3fb950"
            )
        else:
            text = "--%"
            color = "#8b949e"
            level = None
        self.badge_battery = level
        self.badge_charging = charging
        self.widget_battery_var.set(text)
        self.hero.itemconfigure(
            self.battery_display,
            text=text,
            fill=color,
        )

    def _request_succeeded(self, result: dict, success_message: str) -> None:
        self.current_payload = dict(result)
        self._update_battery_display(result)
        status = result.get("status")
        if status in STATUSES:
            self.current_status = status
            self.note_var.set(str(result.get("note", ""))[:24])
            label = STATUSES[status][0].upper()
            profile = self.device_var.get()
            self.current_var.set(label + ("  /  " + profile if profile else ""))
            self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
            self.hero.itemconfigure(
                self.current_status_display,
                text=label,
                fill=STATUSES[status][2],
            )
        elif status == "custom":
            self.current_status = "custom"
            custom_text = str(result.get("custom_text", "CUSTOM"))[:24]
            custom_symbol = result.get("custom_symbol", "star")
            custom_color = result.get("custom_color", "#1F6FEB")
            self.custom_text_var.set(custom_text)
            for label, value in CUSTOM_SYMBOLS.items():
                if value == custom_symbol:
                    self.custom_symbol_var.set(label)
                    break
            if (
                isinstance(custom_color, str)
                and len(custom_color) == 7
                and custom_color.startswith("#")
            ):
                self.custom_color = custom_color.upper()
                self.custom_color_button.configure(
                    text=self.custom_color,
                    bg=self.custom_color,
                    activebackground=self.custom_color,
                )
            self._update_custom_preview()
            profile = self.device_var.get()
            self.current_var.set("CUSTOM" + ("  /  " + profile if profile else ""))
            self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
            self.hero.itemconfigure(
                self.current_status_display,
                text=custom_text.upper()[:16],
                fill=self.custom_color,
            )
        self._persist_profiles()
        self._set_busy(False, success_message)
        self.connection_label.configure(fg="#3fb950")
        self._set_connection_indicator("connected", "CONNECTED")
        self._highlight_current()
        self._update_current_badge_preview()

    def _request_failed(self, message: str) -> None:
        self._set_busy(False, message)
        self.connection_label.configure(fg="#f85149")
        self._set_connection_indicator("error", "UNREACHABLE")

    def _show_error(self, message: str) -> None:
        self.connection_var.set(message)
        self.connection_label.configure(fg="#f85149")
        self._set_connection_indicator("error", "CHECK SETUP")

    def _highlight_current(self) -> None:
        for status, button in self.status_buttons.items():
            tile = getattr(button, "_tile_frame", None)
            if status == self.current_status:
                if tile is not None:
                    tile.configure(highlightthickness=3)
            else:
                if tile is not None:
                    tile.configure(highlightthickness=1)
        for status, button in self.widget_buttons.items():
            button.configure(
                highlightthickness=3 if status == self.current_status else 1,
            )

    def refresh_status(self) -> None:
        if not self._has_pairing_key():
            self.start_pairing()
            return
        self._run_request(
            lambda client: client.get_status(),
            "Connected. Badge status loaded.",
        )

    def send_status(self, status: str) -> None:
        if not self._has_pairing_key():
            self._show_error("Select Connect and approve pairing first.")
            return
        label = STATUSES[status][0]
        note = self.note_var.get()
        self._run_request(
            lambda client: client.set_status(status, note),
            label + " sent to badge.",
        )

    def send_custom(self) -> None:
        if not self._has_pairing_key():
            self._show_error("Select Connect and approve pairing first.")
            return
        text = self.custom_text_var.get()
        symbol = CUSTOM_SYMBOLS[self.custom_symbol_var.get()]
        color = self.custom_color
        self._run_request(
            lambda client: client.set_custom(text, symbol, color),
            "Custom status sent to badge.",
        )

    def _close(self) -> None:
        self.closing = True
        self._persist_profiles()
        if self.widget_window is not None:
            self.widget_window.destroy()
            self.widget_window = None
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    WorkStatusController(root)
    root.mainloop()


if __name__ == "__main__":
    main()
