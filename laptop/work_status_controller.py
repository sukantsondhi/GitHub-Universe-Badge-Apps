"""Desktop controller for the GitHub Universe 2025 Work Status badge."""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, ttk
from urllib import error, parse, request


APP_TITLE = "Work Status Badge"
CONFIG_PATH = Path(__file__).resolve().with_name("badge_profiles.json")
LEGACY_CONFIG_PATH = Path.home() / ".work_status_badge.json"
API_PATH = "/api/status"
REQUEST_TIMEOUT = 2.5
STATUSES = {
    "available": ("Available", "Come on in", "#2ea043"),
    "meeting": ("In a meeting", "Please don't disturb", "#f85149"),
    "focus": ("Focus", "Deep work", "#a371f7"),
    "away": ("Away", "Back soon", "#d29922"),
    "lunch": ("\U0001F35B Lunch", "Curry break", "#f0883e"),
    "sleep": ("Sleep", "Do not disturb", "#388bfd"),
}
CUSTOM_SYMBOLS = {
    "⭐  Star": "star",
    "❤️  Heart": "heart",
    "✅  Check": "check",
    "⚠️  Alert": "alert",
    "☕  Coffee": "coffee",
    "🚪  Door": "door",
    "💻  Coding": "code",
    "⚡  Bolt": "bolt",
}
SYMBOL_EMOJI = {
    "star": "⭐",
    "heart": "❤️",
    "check": "✅",
    "alert": "⚠️",
    "coffee": "☕",
    "door": "🚪",
    "code": "💻",
    "bolt": "⚡",
}
DEFAULT_CUSTOM = {
    "color": "#1F6FEB",
    "symbol": "star",
    "text": "HELLO",
}


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


class BadgeClient:
    def __init__(self, address: str):
        self.base_url = badge_base_url(address)

    def get_status(self) -> dict:
        req = request.Request(
            self.base_url + API_PATH,
            headers={"Accept": "application/json"},
            method="GET",
        )
        return self._send(req)

    def set_status(self, status: str, note: str = "") -> dict:
        if status not in STATUSES:
            raise ValueError("Unknown status: " + status)
        payload = json.dumps({
            "status": status,
            "note": note.strip()[:24],
        }).encode("utf-8")
        req = request.Request(
            self.base_url + API_PATH,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._send(req)

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
        req = request.Request(
            self.base_url + API_PATH,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._send(req)

    @staticmethod
    def _send(req: request.Request) -> dict:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("The badge returned an unexpected response.")
        return data


def normalize_config(data: object) -> dict:
    """Return the current named-profile config, migrating the old format."""
    custom = dict(DEFAULT_CUSTOM)
    if not isinstance(data, dict):
        return {"version": 3, "selected": "", "badges": {}, "custom": custom}

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
            "version": 3,
            "selected": selected,
            "badges": clean_badges,
            "custom": custom,
        }

    # Version 1 stored just one address.
    old_address = data.get("address", "")
    if isinstance(old_address, str) and old_address.strip():
        return {
            "version": 3,
            "selected": "My Badge",
            "badges": {"My Badge": old_address.strip()},
            "custom": custom,
        }
    return {"version": 3, "selected": "", "badges": {}, "custom": custom}


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
        self.root.geometry("940x820")
        self.root.minsize(860, 760)
        self.root.configure(bg="#070b14")

        config = load_config()
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
        self.current_var = tk.StringVar(value="NOT CONNECTED")
        self.address_visible = False
        self.connection_var = tk.StringVar(
            value="Choose a saved badge or add one to get started."
        )
        self.current_status: str | None = None
        self.busy = False
        self.closing = False
        self.result_queue: queue.Queue[tuple[str, object, str]] = queue.Queue()
        self.status_buttons: dict[str, tk.Button] = {}

        self._build_ui()
        self.note_var.trace_add("write", self._limit_note)
        self.custom_text_var.trace_add("write", self._limit_custom_text)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll_results)
        self.root.after(60000, self._refresh_battery_periodically)

        if self.address_var.get().strip():
            self.root.after(250, self.refresh_status)

    def _build_ui(self) -> None:
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

    def _update_custom_preview(self) -> None:
        symbol = CUSTOM_SYMBOLS.get(self.custom_symbol_var.get(), "star")
        self.custom_preview.configure(bg=self.custom_color)
        self.custom_preview.itemconfigure(
            self.custom_preview_symbol,
            text=SYMBOL_EMOJI[symbol],
        )
        self.custom_preview.itemconfigure(
            self.custom_preview_text,
            text=self.custom_text_var.get().strip() or "CUSTOM",
        )

    def _persist_profiles(self) -> None:
        save_config({
            "version": 3,
            "selected": self.device_var.get(),
            "badges": self.profiles,
            "custom": {
                "color": self.custom_color,
                "symbol": CUSTOM_SYMBOLS[self.custom_symbol_var.get()],
                "text": self.custom_text_var.get().strip()[:24] or "CUSTOM",
            },
        })

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
        self.current_var.set(name.upper() if name else "NOT CONNECTED")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill="#f4f8ff")
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
        self.current_var.set("NEW BADGE")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill="#f4f8ff")
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
        self.current_var.set("NOT CONNECTED")
        self.hero.itemconfigure(self.current_badge, text=self.current_var.get())
        self.hero.itemconfigure(self.current_status_display, text="—", fill="#f4f8ff")
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
                if status == self.current_status:
                    button.configure(relief="solid", bd=3)
                else:
                    button.configure(relief="flat", bd=0)
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
        self.custom_send_button.configure(state=state)
        self.connection_var.set(message)
        self.connection_label.configure(fg="#f2cc60" if busy else "#8fa4bd")
        if busy:
            self._set_connection_indicator("connecting", "CONNECTING…")

    def _run_request(self, operation, success_message: str) -> None:
        if self.busy:
            return
        try:
            client = BadgeClient(self.address_var.get())
        except ValueError as exc:
            self._show_error(str(exc))
            return

        self._set_busy(True, "Contacting badge...")

        def worker() -> None:
            try:
                result = operation(client)
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
                else:
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
        self.hero.itemconfigure(
            self.battery_display,
            text=text,
            fill=color,
        )

    def _request_succeeded(self, result: dict, success_message: str) -> None:
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
            if status == self.current_status:
                button.configure(relief="solid", bd=3)
            else:
                button.configure(relief="flat", bd=0)

    def refresh_status(self) -> None:
        self._run_request(
            lambda client: client.get_status(),
            "Connected. Badge status loaded.",
        )

    def send_status(self, status: str) -> None:
        label = STATUSES[status][0]
        note = self.note_var.get()
        self._run_request(
            lambda client: client.set_status(status, note),
            label + " sent to badge.",
        )

    def send_custom(self) -> None:
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
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    WorkStatusController(root)
    root.mainloop()


if __name__ == "__main__":
    main()
