"""Desktop controller for the GitHub Universe 2025 Work Status badge."""

from __future__ import annotations

import binascii
import photo_tools
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
from tkinter import colorchooser, filedialog, ttk
from urllib import error, parse, request


APP_TITLE = "Work Status Badge"
CONFIG_PATH = Path(__file__).resolve().with_name("badge_profiles.json")
LEGACY_CONFIG_PATH = Path.home() / ".work_status_badge.json"
DEVICE_CONFIG_PATH = Path.home() / ".work_status_badge_device.json"
API_PATH = "/api/status"
REQUEST_TIMEOUT = 2.5
PHOTO_CHUNK_BYTES = 768
PHOTO_MAX_BYTES = 98304
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
    "sleep": ("Offline", "Still reachable by laptop", "#58a6ff"),
}
BADGE_LABELS = {
    "available": "I'M FREE",
    "meeting": "IN A MEETING",
    "focus": "DEEP WORK",
    "away": "BACK SOON",
    "lunch": "LUNCH BREAK",
    "sleep": "OFFLINE",
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
    width = min(1180, max(820, screen_width - 80))
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

    def send_photo(self, png: bytes, progress=None) -> dict:
        """Upload a picture through the EXISTING Work Status paired identity."""
        if not isinstance(png, bytes) or len(png) < 24 or len(png) > PHOTO_MAX_BYTES:
            raise ValueError("The badge requires a PNG smaller than 96 KiB.")
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Select a supported PNG image.")
        metadata = json.dumps({
            "size": len(png), "sha256": hashlib.sha256(png).hexdigest(),
        }).encode("utf-8")
        self._signed_request("/api/frame/start", "POST", metadata)
        sent = 0
        while sent < len(png):
            chunk = png[sent:sent + PHOTO_CHUNK_BYTES]
            answer = self._signed_request(
                "/api/frame/chunk", "POST", chunk,
                query="?offset=%d" % sent,
                content_type="application/octet-stream",
            )
            sent += len(chunk)
            if answer.get("offset") != sent:
                raise RuntimeError("Photo transfer offset differs from badge.")
            if progress is not None:
                progress(round(sent / len(png) * 95))
        answer = self._signed_request("/api/frame/finish", "POST", b"{}", timeout=12.0)
        if answer.get("displayed") is not True:
            raise RuntimeError("Badge did not confirm that it displayed the photo.")
        if progress is not None:
            progress(100)
        return self.get_status()

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
        query: str = "",
        content_type: str = "application/json",
        timeout: float = REQUEST_TIMEOUT,
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
            headers["Content-Type"] = content_type
        return self._send(
            request.Request(
                self.base_url + path + query,
                data=data,
                headers=headers,
                method=method,
            ),
            response_key=self.device_key,
            response_nonce=nonce,
            timeout=timeout,
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
        self.status_shell: tk.Frame | None = None
        self.custom_shell: tk.Frame | None = None
        self.status_grid: tk.Frame | None = None
        self.status_tiles: dict[str, tk.Frame] = {}
        self._status_columns = 0
        self.custom_panel_visible = False
        self.photo_panel_visible = False
        self.photo_source = None
        self.photo_preview_image = None
        self.photo_thumb_image = None
        self.photo_zoom_var = tk.DoubleVar(value=1.0)
        self.photo_name_var = tk.StringVar(value="Choose a photo from your laptop")
        self.photo_progress_var = tk.StringVar(value="Ready to create a badge photo")
        self.photo_pan_x = 0.0
        self.photo_pan_y = 0.0
        self.photo_drag = None
        self.custom_toggle_button: tk.Button | None = None
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
        p = THEMES[self.theme_name]
        self.root.configure(bg=p["bg"])
        style = ttk.Style(self.root)
        style.theme_use("clam")
        for name in ("Dark.TEntry", "Dark.TCombobox"):
            style.configure(name, fieldbackground=p["field"], background=p["raised"],
                            foreground=p["text"], arrowcolor=p["cyan"],
                            bordercolor=p["border"], lightcolor=p["border"],
                            darkcolor=p["border"], padding=7)
            style.map(name, fieldbackground=[("readonly", p["field"])],
                      foreground=[("readonly", p["text"])],
                      bordercolor=[("focus", p["cyan"])])

        def label(parent, text, size=10, color=None, bold=False):
            return tk.Label(parent, text=text, bg=parent["bg"],
                            fg=color or p["text"], anchor="w",
                            font=("Segoe UI Semibold" if bold else "Segoe UI", size))

        def button(parent, text, command, primary=False):
            return tk.Button(parent, text=text, command=command,
                             bg=p["cyan"] if primary else p["raised"],
                             fg=("#07131d" if self.theme_name == "dark" else "#ffffff") if primary else p["text"],
                             activebackground=p["active"], activeforeground=p["text"],
                             disabledforeground=p["muted"], relief="flat", bd=0,
                             padx=12, pady=7, cursor="hand2",
                             highlightthickness=1, highlightbackground=p["border"],
                             highlightcolor=p["cyan"], font=("Segoe UI Semibold", 9))

        # A quiet application bar, separate from the working area.
        self.topbar = tk.Frame(self.root, bg=p["panel"], padx=22, pady=12)
        self.topbar.pack(fill="x")
        label(self.topbar, "WS", 18, p["cyan"], True).pack(side="left", padx=(0, 10))
        brand = tk.Frame(self.topbar, bg=p["panel"])
        brand.pack(side="left")
        label(brand, "Work status", 18, bold=True).pack(anchor="w")
        label(brand, "Your space. Your signal.", 9, p["muted"]).pack(anchor="w")
        self.widget_button = button(self.topbar, "Mini controller", self.open_widget)
        self.widget_button.pack(side="right", padx=(8, 0))
        self.fullscreen_button = button(self.topbar, "Full screen", self.toggle_fullscreen)
        self.fullscreen_button.pack(side="right", padx=(8, 0))
        self.theme_button = button(self.topbar,
                                  "Dark theme" if self.theme_name == "light" else "Light theme",
                                  self.toggle_theme)
        self.theme_button.pack(side="right")

        # Feedback gets a reserved row and can never be pushed off-screen.
        self.activity_shell = tk.Frame(self.root, bg=p["panel"], padx=20, pady=9)
        self.activity_shell.pack(side="bottom", fill="x")
        self.activity_dot = label(self.activity_shell, "\u25cf", 10, p["muted"])
        self.activity_dot.pack(side="left", padx=(0, 9))
        self.connection_label = tk.Label(
            self.activity_shell, textvariable=self.connection_var, bg=p["panel"],
            fg=p["muted"], anchor="w", justify="left", font=("Segoe UI", 10))
        self.connection_label.pack(side="left", fill="x", expand=True)

        self.workspace = tk.Frame(self.root, bg=p["bg"])
        self.workspace.pack(fill="both", expand=True, padx=20, pady=18)
        self.sidebar = tk.Frame(self.workspace, bg=p["panel"], width=258,
                                highlightthickness=1, highlightbackground=p["border"])
        self.sidebar.pack(side="left", fill="y", padx=(0, 20))
        self.sidebar.pack_propagate(False)
        self.profile_shell = tk.Frame(self.sidebar, bg=p["panel"], padx=16, pady=14)
        self.profile_shell.pack(fill="x")
        label(self.profile_shell, "YOUR BADGE", 9, p["cyan"], True).pack(anchor="w", pady=(0, 10))
        self.device_picker = ttk.Combobox(self.profile_shell, textvariable=self.device_var,
            values=sorted(self.profiles), state="readonly", style="Dark.TCombobox", width=18)
        self.device_picker.pack(fill="x")
        self.device_picker.bind("<<ComboboxSelected>>", self._select_profile)
        self.profile_details_button = button(self.profile_shell, "Edit badge details", self.toggle_profile_details)
        self.profile_details_button.pack(fill="x", pady=(8, 0))
        self.profile_editor = tk.Frame(self.profile_shell, bg=p["panel"])
        self.profile_editor.pack(fill="x")
        self.profile_details_open = getattr(self, "profile_details_open", not bool(self.profiles))
        label(self.profile_editor, "Badge name", 9, p["muted"]).pack(anchor="w", pady=(10, 3))
        self.profile_name_entry = ttk.Entry(self.profile_editor, textvariable=self.profile_name_var,
                                            style="Dark.TEntry")
        self.profile_name_entry.pack(fill="x")
        address_heading = tk.Frame(self.profile_editor, bg=p["panel"])
        address_heading.pack(fill="x", pady=(8, 3))
        label(address_heading, "Network address", 9, p["muted"]).pack(side="left")
        self.visibility_button = button(address_heading,
            "Hide IP" if self.address_visible else "Show IP", self.toggle_address_visibility)
        self.visibility_button.configure(pady=1, padx=5, font=("Segoe UI", 8))
        self.visibility_button.pack(side="right")
        self.address_entry = ttk.Entry(self.profile_editor, textvariable=self.address_var,
            style="Dark.TEntry", show="" if self.address_visible else "\u2022")
        self.address_entry.pack(fill="x")
        self.address_entry.bind("<Return>", lambda _event: self.save_profile())
        label(self.profile_editor, "Press C on your badge to find it.", 8, p["muted"]).pack(anchor="w", pady=(4, 9))
        actions = tk.Frame(self.profile_editor, bg=p["panel"])
        actions.pack(fill="x")
        self.new_badge_button = button(actions, "New", self.new_profile)
        self.save_button = button(actions, "Save", self.save_profile)
        self.delete_button = button(actions, "Forget", self.forget_profile)
        for item in (self.new_badge_button, self.save_button, self.delete_button):
            item.configure(padx=8)
            item.pack(side="left", expand=True, fill="x", padx=2)
        self.refresh_button = button(self.profile_shell, "Connect to badge", self.refresh_status, True)
        self.refresh_button.pack(fill="x", pady=(10, 0))

        self.hero = tk.Canvas(self.sidebar, height=111, bg=p["panel"], highlightthickness=0)
        self.hero.pack(fill="x", padx=16)
        self.hero.create_line(0, 1, 226, 1, fill=p["border"])
        self.connection_dot = self.hero.create_oval(0, 17, 7, 24, fill=p["muted"], outline="")
        self.connection_state_display = self.hero.create_text(15, 21, anchor="w", text="NOT CONNECTED",
            fill=p["muted"], font=("Segoe UI Semibold", 9))
        self.battery_display = self.hero.create_text(0, 44, anchor="w", text="Battery: --",
            fill=p["muted"], font=("Segoe UI", 9))
        self.current_status_display = self.hero.create_text(0, 69, anchor="w", text="No status yet",
            fill=p["text"], font=("Segoe UI Semibold", 13), width=218)
        self.current_badge = self.hero.create_text(0, 97, anchor="w", text=self.current_var.get(),
            fill=p["muted"], font=("Segoe UI", 8), width=218)
        self.preview_shell = tk.Frame(self.sidebar, bg=p["panel"], padx=16)
        self.preview_shell.pack(fill="x", pady=(5, 12))
        label(self.preview_shell, "ON YOUR BADGE", 8, p["muted"], True).pack(anchor="w", pady=(0, 7))
        self.current_badge_preview = tk.Canvas(self.preview_shell, height=138,
            bg=p["raised"], highlightthickness=1, highlightbackground=p["border"])
        self.current_badge_preview.pack(fill="x")
        self.current_badge_preview.bind("<Configure>", lambda _event: self._update_current_badge_preview())

        self.main_panel = tk.Frame(self.workspace, bg=p["bg"])
        self.main_panel.pack(side="left", fill="both", expand=True)
        label(self.main_panel, "Make room for your best work.", 21, bold=True).pack(anchor="w")
        self.page_subtitle = label(self.main_panel, "Choose a status to update your badge.", 10, p["muted"])
        self.page_subtitle.pack(anchor="w", pady=(4, 16))
        tabs = tk.Frame(self.main_panel, bg=p["bg"])
        tabs.pack(fill="x", pady=(0, 15))
        self.presets_tab = button(tabs, "Quick statuses", lambda: self._show_composer(False))
        self.presets_tab.pack(side="left", padx=(0, 8))
        self.custom_toggle_button = button(tabs, "Create your own", lambda: self._show_composer(True))
        self.custom_toggle_button.pack(side="left")
        self.photo_tab = button(tabs, "Photo frame", lambda: self._show_composer("photo"))
        self.photo_tab.pack(side="left", padx=(8, 0))
        self.content = tk.Frame(self.main_panel, bg=p["bg"])
        self.content.pack(fill="both", expand=True)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.status_shell = tk.Frame(self.content, bg=p["bg"])
        self.status_shell.grid(row=0, column=0, sticky="nsew")
        note_header = tk.Frame(self.status_shell, bg=p["bg"])
        note_header.pack(fill="x")
        label(note_header, "Add a short note", 10, bold=True).pack(side="left")
        tk.Label(note_header, textvariable=self.note_count_var, bg=p["bg"], fg=p["muted"],
                 font=("Segoe UI", 9)).pack(side="right")
        self.note_entry = ttk.Entry(self.status_shell, textvariable=self.note_var,
                                   style="Dark.TEntry", font=("Segoe UI", 11))
        self.note_entry.pack(fill="x", pady=(6, 4))
        label(self.status_shell, "Optional - included with your next status", 9, p["muted"]).pack(anchor="w", pady=(0, 10))
        self.status_grid = tk.Frame(self.status_shell, bg=p["bg"])
        self.status_grid.pack(fill="both", expand=True)
        for status, (title, subtitle, color) in STATUSES.items():
            tile = tk.Frame(self.status_grid, bg=p["raised"],
                            highlightthickness=1, highlightbackground=p["border"])
            icon = tk.Canvas(tile, width=62, height=60, bg=p["raised"], highlightthickness=0)
            icon.pack(side="left", padx=(6, 0))
            icon_bg = darken_hex(color, 7)
            icon.create_oval(5, 4, 57, 56, fill=icon_bg, outline="")
            self._draw_preset_symbol(icon, status, 31, 30, 0.31, color, icon_bg)
            control = button(tile, title + "\n" + subtitle, lambda chosen=status: self.send_status(chosen))
            control.configure(anchor="w", justify="left", highlightthickness=0,
                              font=("Segoe UI Semibold", 11), padx=8)
            control.pack(side="left", fill="both", expand=True)
            control._rest_bg = p["raised"]
            control._hover_bg = p["active"]
            control._tile_frame = tile
            control._icon_canvas = icon
            icon.bind("<Button-1>", lambda _event, item=control: item.invoke())
            self.status_buttons[status] = control
            self.status_tiles[status] = tile
        self._set_status_grid_columns(2)

        self.custom_shell = tk.Frame(self.content, bg=p["panel"], padx=20, pady=16,
                                     highlightthickness=1, highlightbackground=p["border"])
        self.custom_shell.grid(row=0, column=0, sticky="nsew")
        self.custom_head = tk.Frame(self.custom_shell, bg=p["panel"])
        self.custom_head.pack(fill="x")
        label(self.custom_head, "A status that's yours.", 17, bold=True).pack(anchor="w")
        label(self.custom_head, "Pick a symbol, add a message, make it visible.", 9, p["muted"]).pack(anchor="w", pady=(3, 12))
        self.custom_body = tk.Frame(self.custom_shell, bg=p["panel"])
        self.custom_body.pack(fill="both", expand=True)
        self.custom_preview = tk.Canvas(self.custom_body, height=126, highlightthickness=0)
        self.custom_preview.pack(fill="x", pady=(0, 12))
        self.custom_preview.bind("<Configure>", lambda _event: self._update_custom_preview())
        self.custom_editor = tk.Frame(self.custom_body, bg=p["panel"])
        self.custom_editor.pack(fill="x")
        label(self.custom_editor, "Message - up to 24 characters", 9, p["muted"]).pack(anchor="w")
        ttk.Entry(self.custom_editor, textvariable=self.custom_text_var, style="Dark.TEntry",
                  font=("Segoe UI", 12)).pack(fill="x", pady=(5, 12))
        options = tk.Frame(self.custom_editor, bg=p["panel"])
        options.pack(fill="x")
        self.custom_symbol_picker = ttk.Combobox(options, textvariable=self.custom_symbol_var,
            values=list(CUSTOM_SYMBOLS), state="readonly", style="Dark.TCombobox")
        self.custom_symbol_picker.pack(side="left", fill="x", expand=True)
        self.custom_symbol_picker.bind("<<ComboboxSelected>>", lambda _event: self._update_custom_preview())
        self.custom_color_button = button(options, self.custom_color, self.choose_custom_color)
        self.custom_color_button.configure(bg=self.custom_color, fg="#ffffff")
        self.custom_color_button.pack(side="left", padx=(10, 0))
        self.custom_send_button = button(self.custom_editor, "Send to badge", self.send_custom, True)
        self.custom_send_button.pack(fill="x", pady=(16, 0))
        # The editor lives inside the main dashboard, not a separate browser.
        self.photo_shell = tk.Frame(self.content, bg=p["panel"], padx=20, pady=16,
                                    highlightthickness=1, highlightbackground=p["border"])
        self.photo_shell.grid(row=0, column=0, sticky="nsew")
        label(self.photo_shell, "Turn your badge into a photo frame.", 17, bold=True).pack(anchor="w")
        label(self.photo_shell, "A private 4:3 photo, cropped on your laptop and sent over home Wi-Fi.",
              9, p["muted"]).pack(anchor="w", pady=(4, 12))
        self.photo_preview = tk.Canvas(self.photo_shell, height=255, bg="#0e1c35",
                                       highlightthickness=1, highlightbackground=p["border"],
                                       cursor="fleur")
        self.photo_preview.pack(fill="both", expand=True)
        self.photo_preview.bind("<Configure>", lambda _event: self._draw_photo_preview())
        self.photo_preview.bind("<ButtonPress-1>", self._start_photo_drag)
        self.photo_preview.bind("<B1-Motion>", self._drag_photo)
        self.photo_preview.bind("<ButtonRelease-1>", lambda _event: setattr(self, "photo_drag", None))
        label(self.photo_shell, "Drag the preview to reposition the photo.",
              9, p["muted"]).pack(anchor="w", pady=(6, 3))
        controls = tk.Frame(self.photo_shell, bg=p["panel"])
        controls.pack(fill="x", pady=(4, 0))
        self.photo_choose_button = button(controls, "Choose photo", self.choose_photo)
        self.photo_choose_button.pack(side="left", padx=(0, 10))
        label(controls, "", 9, p["muted"]).pack(side="left", fill="x", expand=True)
        self.photo_zoom_label = label(controls, "Zoom 1.00x", 9, p["muted"])
        self.photo_zoom_label.pack(side="right")
        self.photo_scale = tk.Scale(self.photo_shell, variable=self.photo_zoom_var,
                                    from_=1.0, to=3.0, resolution=0.05,
                                    orient="horizontal", showvalue=False,
                                    command=self._photo_zoom_changed,
                                    bg=p["panel"], fg=p["text"],
                                    troughcolor=p["field"], highlightthickness=0,
                                    activebackground=p["cyan"])
        self.photo_scale.pack(fill="x", pady=(2, 0))
        self.photo_file_label = tk.Label(self.photo_shell, textvariable=self.photo_name_var,
                                         bg=p["panel"], fg=p["muted"], anchor="w",
                                         font=("Segoe UI", 9))
        self.photo_file_label.pack(fill="x", pady=(2, 0))
        self.photo_upload_button = button(self.photo_shell, "Display photo on badge",
                                          self.send_photo, True)
        self.photo_upload_button.pack(fill="x", pady=(10, 3))
        if self.photo_source is None:
            self.photo_upload_button.configure(state=tk.DISABLED)
        self.photo_progress_label = tk.Label(self.photo_shell, textvariable=self.photo_progress_var,
                                             bg=p["panel"], fg=p["muted"], anchor="w",
                                             font=("Segoe UI", 9))
        self.photo_progress_label.pack(fill="x")
        self._update_custom_preview()
        self._update_current_badge_preview()
        self._show_composer("photo" if self.photo_panel_visible
                            else self.custom_panel_visible)

    def toggle_profile_details(self) -> None:
        self.profile_details_open = not self.profile_details_open
        self._apply_responsive_layout()

    def _show_composer(self, visible: bool | str) -> None:
        self.photo_panel_visible = visible == "photo"
        self.custom_panel_visible = visible is True
        self._apply_responsive_layout()

    def _limit_note(self, *_args) -> None:
        value = self.note_var.get()
        if len(value) > 24:
            self.note_var.set(value[:24])
        self.note_count_var.set("%d / 24" % min(len(value), 24))

    def _limit_custom_text(self, *_args) -> None:
        value = self.custom_text_var.get()
        if len(value) > 24:
            self.custom_text_var.set(value[:24])
        self._update_custom_preview()

    def _update_current_badge_preview(self) -> None:
        """Render the last state received from the badge as a live screen."""
        canvas = getattr(self, "current_badge_preview", None)
        if canvas is None:
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
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
        if status == "photo":
            canvas.configure(bg="#0e1c35")
            canvas.create_text(width / 2, height / 2,
                               text="PHOTO FRAME ACTIVE",
                               fill="#80b5ff", font=("Segoe UI Semibold", 10))
            return
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
        canvas.scale("all", width / 2, 0, 1, min(1.0, height / 128))

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

    def _set_status_grid_columns(self, columns: int) -> None:
        grid = self.status_grid
        if grid is None or not self.status_tiles:
            return
        columns = max(1, min(columns, len(self.status_tiles)))
        if columns == self._status_columns:
            return
        tiles = [self.status_tiles[status] for status in STATUSES]
        for tile in tiles:
            tile.grid_forget()
        for index, tile in enumerate(tiles):
            tile.grid(
                row=index // columns,
                column=index % columns,
                sticky="nsew",
                padx=5,
                pady=5,
            )
        max_columns = len(tiles)
        max_rows = len(tiles)
        for column in range(max_columns):
            grid.columnconfigure(column, weight=1 if column < columns else 0)
        rows = (len(tiles) + columns - 1) // columns
        for row in range(max_rows):
            grid.rowconfigure(row, weight=1 if row < rows else 0)
        self._status_columns = columns

    def toggle_custom_panel(self) -> None:
        self._show_composer(not self.custom_panel_visible)

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        if self._responsive_after is not None:
            self.root.after_cancel(self._responsive_after)
        self._responsive_after = self.root.after(
            60, self._apply_responsive_layout,
        )

    def _apply_responsive_layout(self) -> None:
        self._responsive_after = None
        if self.closing or not self.root.winfo_exists():
            return
        width, height = self.root.winfo_width(), self.root.winfo_height()
        p = THEMES[self.theme_name]
        self.connection_label.configure(wraplength=max(300, width - 80))
        self.workspace.pack_configure(padx=14 if width < 1000 else 24,
                                      pady=10 if height < 700 else 20)
        self.sidebar.configure(width=236 if width < 1000 else 270)
        self.sidebar.pack_configure(padx=(0, 16 if width < 1000 else 28))
        self.current_badge_preview.configure(height=82 if height < 700 else 138)
        self.profile_shell.pack_configure(pady=8 if height < 700 else 14)
        if self.profile_details_open:
            self.profile_editor.pack(fill="x", before=self.refresh_button)
        else:
            self.profile_editor.pack_forget()
        self.profile_details_button.configure(
            text="Hide badge details" if self.profile_details_open else "Edit / add badge")
        self.photo_shell.grid_remove()
        self.custom_shell.grid_remove()
        self.status_shell.grid_remove()
        if self.photo_panel_visible:
            self.photo_shell.grid()
        elif self.custom_panel_visible:
            self.custom_shell.grid()
        else:
            self.status_shell.grid()
        for tab, active in ((self.presets_tab, not self.custom_panel_visible and not self.photo_panel_visible),
                            (self.custom_toggle_button, self.custom_panel_visible),
                            (self.photo_tab, self.photo_panel_visible)):
            tab.configure(bg=p["active"] if active else p["bg"],
                          fg=p["cyan"] if active else p["muted"])
        self._set_status_grid_columns(2)
        tile_width = max(120, (width - 330) // 2 - 80)
        for control in self.status_buttons.values():
            control.configure(wraplength=tile_width,
                              font=("Segoe UI Semibold", 10 if width < 1000 else 11))
        self.custom_preview.configure(height=68 if height < 760 else 126)
        self.photo_preview.configure(height=110 if height < 700 else 225)
        if self.photo_panel_visible:
            self._draw_photo_preview()
        self.fullscreen_button.configure(text="Exit full screen" if self.fullscreen else "Full screen")

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
        if self.busy:
            return
        link_state = getattr(self, "_connection_state", ("idle", "OFFLINE"))
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self._persist_profiles()
        self.close_widget()
        self._status_columns = 0
        self.status_tiles.clear()
        for child in self.root.winfo_children():
            child.destroy()
        self.status_buttons.clear()
        self._build_ui()
        self.root.after_idle(self._apply_responsive_layout)
        self._highlight_current()
        self._set_connection_indicator(*link_state)
        if self.current_payload:
            self._update_battery_display(self.current_payload)
            status = self.current_payload.get("status")
            label = ("PHOTO FRAME" if status == "photo" else
                     STATUSES[status][0] if status in STATUSES else "Custom")
            self.hero.itemconfigure(self.current_status_display, text=label)
        self._update_current_badge_preview()

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

    def _select_profile(self, _event=None) -> None:
        if self.busy:
            return
        name = self.device_var.get()
        self.profile_name_var.set(name)
        self.address_var.set(self.profiles.get(name, ""))
        self.current_status = None
        self.current_payload = None
        self._update_battery_display({})
        self.current_var.set(name.upper() if name else "NOT CONNECTED")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill=THEMES[self.theme_name]["text"])
        self._update_current_badge_preview()
        self._set_connection_indicator("idle", "READY")
        self.connection_var.set("Profile loaded. Press Connect to check the badge.")
        self.connection_label.configure(fg="#8b949e")
        self._highlight_current()
        self._persist_profiles()

    def new_profile(self) -> None:
        if self.busy:
            return
        self.profile_details_open = True
        self._apply_responsive_layout()
        self.device_var.set("")
        self.profile_name_var.set("")
        self.address_var.set("")
        self.current_status = None
        self.current_payload = None
        self._update_battery_display({})
        self.current_var.set("NEW BADGE")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill=THEMES[self.theme_name]["text"])
        self._update_current_badge_preview()
        self._set_connection_indicator("idle", "SETUP")
        self._highlight_current()
        self.connection_var.set(
            "Enter a badge name and address, then select Save Badge."
        )
        self.connection_label.configure(fg="#58a6ff")

    def save_profile(self) -> None:
        if self.busy:
            return
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
        if self.busy:
            return
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
        self._update_battery_display({})
        self.current_var.set("NOT CONNECTED")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill=THEMES[self.theme_name]["text"])
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
        self._connection_state = (state, label)
        colors = {
            "idle": ("#6e7f93", "#a9b8c9"),
            "connecting": ("#d29922", "#f2cc60"),
            "connected": ("#3fb950", "#56d364"),
            "error": ("#f85149", "#ff7b72"),
        }
        dot, foreground = colors.get(state, colors["idle"])
        if self.theme_name == "light":
            foreground = {"idle": "#647a91", "connecting": "#855900",
                          "connected": "#18793b", "error": "#ba2835"}.get(state, "#647a91")
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
        for widget in (self.new_badge_button, self.save_button, self.delete_button,
                       self.theme_button, self.profile_name_entry, self.address_entry):
            widget.configure(state=state)
        self.device_picker.configure(state="disabled" if busy else "readonly")
        for button in self.status_buttons.values():
            button.configure(state=state)
        for button in self.widget_buttons.values():
            button.configure(state=state)
        self.custom_send_button.configure(state=state)
        self.photo_choose_button.configure(state=state)
        self.photo_scale.configure(state=state)
        self.photo_upload_button.configure(
            state=state if self.photo_source is not None else tk.DISABLED)
        self.connection_var.set(message)
        self.connection_label.configure(fg=("#855900" if self.theme_name == "light" else "#f2cc60") if busy else THEMES[self.theme_name]["muted"])
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
                elif kind == "photo_progress":
                    self.photo_progress_var.set("Sending photo: %d%%" % result)
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
                    self.connection_label.configure(fg="#18793b" if self.theme_name == "light" else "#56d364")
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
        if self.current_payload is not None and self._has_pairing_key() and not self.busy:
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
        if self.current_payload is None:
            self.profile_details_open = False
            self._apply_responsive_layout()
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
        elif status == "photo":
            self.current_status = "photo"
            profile = self.device_var.get()
            self.current_var.set("PHOTO FRAME" +
                                 ("  /  " + profile if profile else ""))
            self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
            self.hero.itemconfigure(self.current_status_display,
                                    text="PHOTO FRAME", fill="#58a6ff")
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
        if success_message.startswith("Photo displayed"):
            self.photo_progress_var.set("Photo displayed successfully on the badge.")
        self.connection_label.configure(fg="#18793b" if self.theme_name == "light" else "#56d364")
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
                    tile.configure(highlightthickness=3, highlightbackground=STATUSES[status][2])
            else:
                if tile is not None:
                    tile.configure(highlightthickness=1, highlightbackground=THEMES[self.theme_name]["border"])
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

    def choose_photo(self) -> None:
        """Choose a local photo; no network transfer occurs until Send."""
        if self.busy:
            return
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a badge photo",
            filetypes=[
                ("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                ("All files", "*.*"),
            ],
        )
        if not filename:
            return
        try:
            self.photo_source = photo_tools.open_photo(filename)
        except (OSError, ValueError, RuntimeError) as exc:
            self.photo_source = None
            self.photo_upload_button.configure(state=tk.DISABLED)
            self._show_error(str(exc))
            return
        self.photo_zoom_var.set(1.0)
        self.photo_pan_x = self.photo_pan_y = 0.0
        self.photo_name_var.set(Path(filename).name)
        self.photo_progress_var.set("Ready to send. Drag to crop, or adjust zoom.")
        self.photo_upload_button.configure(state=tk.NORMAL)
        self._draw_photo_preview()

    def _cropped_photo(self):
        if self.photo_source is None:
            raise ValueError("Choose a photo first.")
        return photo_tools.crop_photo(
            self.photo_source, self.photo_zoom_var.get(),
            self.photo_pan_x, self.photo_pan_y,
        )

    def _draw_photo_preview(self) -> None:
        canvas = getattr(self, "photo_preview", None)
        if canvas is None or not canvas.winfo_exists():
            return
        canvas.delete("all")
        width = max(320, canvas.winfo_width())
        height = max(140, canvas.winfo_height())
        if self.photo_source is None:
            canvas.create_text(width / 2, height / 2 - 12,
                               text="YOUR PHOTO, YOUR BADGE",
                               fill="#acd0ff", font=("Segoe UI Semibold", 13))
            canvas.create_text(width / 2, height / 2 + 12,
                               text="Choose a picture to see your 4:3 preview",
                               fill="#839ab8", font=("Segoe UI", 10))
            return
        try:
            _, _, ImageTk = photo_tools.pillow()
            cropped = self._cropped_photo()
            scale = min((width - 12) / 160, (height - 12) / 120)
            size = (max(1, int(160 * scale)), max(1, int(120 * scale)))
            self.photo_preview_image = ImageTk.PhotoImage(
                cropped.resize(size, photo_tools.pillow()[0].Resampling.NEAREST),
                master=self.root,
            )
            canvas.create_image(width / 2, height / 2,
                                image=self.photo_preview_image, anchor="center")
            canvas.create_rectangle(width / 2 - size[0] / 2 - 1,
                                    height / 2 - size[1] / 2 - 1,
                                    width / 2 + size[0] / 2 + 1,
                                    height / 2 + size[1] / 2 + 1,
                                    outline="#73aaff", width=1)
        except (OSError, ValueError, RuntimeError) as exc:
            self.photo_progress_var.set(str(exc))

    def _draw_photo_thumbnail(self, canvas, width: int, height: int) -> None:
        try:
            Image, _, ImageTk = photo_tools.pillow()
            scale = min((width - 8) / 160, (height - 8) / 120)
            size = (max(1, int(160 * scale)), max(1, int(120 * scale)))
            self.photo_thumb_image = ImageTk.PhotoImage(
                self._cropped_photo().resize(size, Image.Resampling.NEAREST),
                master=self.root,
            )
            canvas.create_image(width / 2, height / 2,
                                image=self.photo_thumb_image)
        except (OSError, ValueError, RuntimeError):
            canvas.create_text(width / 2, height / 2, text="PHOTO FRAME",
                               fill="#80b5ff", font=("Segoe UI Semibold", 10))

    def _photo_zoom_changed(self, _value) -> None:
        self.photo_zoom_label.configure(text="Zoom %.2fx" %
                                        self.photo_zoom_var.get())
        self._draw_photo_preview()

    def _start_photo_drag(self, event) -> None:
        if self.photo_source is not None and not self.busy:
            self.photo_drag = (event.x, event.y, self.photo_pan_x, self.photo_pan_y)

    def _drag_photo(self, event) -> None:
        if self.photo_drag is None or self.photo_source is None or self.busy:
            return
        width = max(160, self.photo_preview.winfo_width())
        height = max(120, self.photo_preview.winfo_height())
        scale = min((width - 12) / 160, (height - 12) / 120)
        x, y, original_x, original_y = self.photo_drag
        self.photo_pan_x = original_x + (event.x - x) / max(scale, 0.01)
        self.photo_pan_y = original_y + (event.y - y) / max(scale, 0.01)
        self._draw_photo_preview()

    def send_photo(self) -> None:
        if self.busy:
            return
        if not self._has_pairing_key():
            self._show_error("Select Connect and approve pairing first.")
            return
        try:
            payload = photo_tools.encode_badge_png(self._cropped_photo())
        except (OSError, RuntimeError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self.photo_progress_var.set("Preparing authenticated photo transfer...")
        self._run_request(
            lambda client: client.send_photo(
                payload,
                progress=lambda percent: self.result_queue.put(
                    ("photo_progress", percent, ""),
                ),
            ),
            "Photo displayed on badge. Select a status to return to Work Status.",
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
