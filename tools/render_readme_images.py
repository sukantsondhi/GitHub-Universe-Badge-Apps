"""Render reproducible README previews from the badge drawing functions."""

import importlib.util
import math
import sys
import tempfile
import types
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "images"
SCALE = 2


def font(size, bold=False):
    name = "seguisb.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)


class PreviewFont:
    def __init__(self, size, bold=False):
        self.pillow = font(size, bold)


class Shape:
    def __init__(self, kind, args):
        self.kind = kind
        self.args = args
        self.stroke_width = 0

    def stroke(self, width):
        self.stroke_width = width
        return self


class Shapes:
    def rectangle(self, *args):
        return Shape("rectangle", args)

    def rounded_rectangle(self, *args):
        return Shape("rounded_rectangle", args)

    def circle(self, *args):
        return Shape("circle", args)

    def line(self, *args):
        return Shape("line", args)

    def arc(self, *args):
        return Shape("arc", args)

    def regular_polygon(self, *args):
        return Shape("regular_polygon", args)


class PreviewScreen:
    def __init__(self):
        self.image = Image.new("RGB", (160, 120), (0, 0, 0))
        self.draw_api = ImageDraw.Draw(self.image)
        self.brush = (255, 255, 255)
        self.font = PreviewFont(7)

    def clear(self):
        self.draw_api.rectangle((0, 0, 159, 119), fill=self.brush)

    def measure_text(self, value):
        box = self.draw_api.textbbox((0, 0), str(value), font=self.font.pillow)
        return box[2] - box[0], box[3] - box[1]

    def text(self, value, x, y):
        self.draw_api.text(
            (int(x), int(y)), str(value), fill=self.brush,
            font=self.font.pillow, stroke_width=0,
        )

    def draw(self, shape):
        args = shape.args
        width = max(1, int(shape.stroke_width))
        fill = None if shape.stroke_width else self.brush
        outline = self.brush if shape.stroke_width else None
        if shape.kind == "rectangle":
            x, y, w, h = args
            self.draw_api.rectangle(
                (x, y, x + w, y + h), fill=fill, outline=outline, width=width,
            )
        elif shape.kind == "rounded_rectangle":
            x, y, w, h, radius = args
            self.draw_api.rounded_rectangle(
                (x, y, x + w, y + h), radius=radius,
                fill=fill, outline=outline, width=width,
            )
        elif shape.kind == "circle":
            x, y, radius = args
            self.draw_api.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=fill, outline=outline, width=width,
            )
        elif shape.kind == "line":
            x1, y1, x2, y2, line_width = args
            self.draw_api.line(
                (x1, y1, x2, y2), fill=self.brush,
                width=max(1, int(line_width)), joint="curve",
            )
        elif shape.kind == "arc":
            x, y, radius, start, end = args
            self.draw_api.arc(
                (x - radius, y - radius, x + radius, y + radius),
                start=start, end=end, fill=self.brush, width=width,
            )
        elif shape.kind == "regular_polygon":
            x, y, radius, sides = args
            points = []
            for index in range(sides):
                angle = -math.pi / 2 + index * math.pi * 2 / sides
                points.append((
                    x + math.cos(angle) * radius,
                    y + math.sin(angle) * radius,
                ))
            self.draw_api.polygon(points, fill=self.brush)

    def save(self, name):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        resized = self.image.resize(
            (160 * SCALE, 120 * SCALE), Image.Resampling.NEAREST,
        )
        resized.save(OUTPUT / name, optimize=True)


class State:
    @staticmethod
    def load(_name, _target):
        return False

    @staticmethod
    def save(_name, _value):
        return True


class FakeWLAN:
    def active(self, *_args):
        return True

    def isconnected(self):
        return True

    def connect(self, *_args):
        return None

    def config(self, *_args, **_kwargs):
        return None


def load_badge_app(relative_path, name):
    screen = PreviewScreen()
    io = types.SimpleNamespace(
        ticks=2460,
        pressed=set(),
        held=set(),
        released=set(),
        changed=set(),
        BUTTON_A=1,
        BUTTON_B=2,
        BUTTON_C=3,
        BUTTON_UP=4,
        BUTTON_DOWN=5,
        led={},
        LED_TOP_LEFT=0,
        LED_TOP_RIGHT=1,
        LED_BOTTOM_LEFT=2,
        LED_BOTTOM_RIGHT=3,
    )
    badgeware = types.SimpleNamespace(
        State=State,
        io=io,
        brushes=types.SimpleNamespace(color=lambda *values: tuple(values[:3])),
        shapes=Shapes(),
        screen=screen,
        PixelFont=types.SimpleNamespace(
            load=lambda path: PreviewFont(
                25 if "absolute" in path else (
                    13 if "compass" in path else 7
                ),
                bold="compass" in path or "absolute" in path,
            )
        ),
        get_battery_level=lambda: 82,
        is_charging=lambda: False,
        run=lambda *_args, **_kwargs: None,
    )
    network = types.SimpleNamespace(STA_IF=0, WLAN=lambda *_args: FakeWLAN())
    old_badgeware = sys.modules.get("badgeware")
    old_network = sys.modules.get("network")
    old_ntptime = sys.modules.get("ntptime")
    old_urequest = sys.modules.get("urllib.urequest")
    sys.modules["badgeware"] = badgeware
    sys.modules["network"] = network
    sys.modules["ntptime"] = types.SimpleNamespace(settime=lambda: None)
    sys.modules["urllib.urequest"] = types.SimpleNamespace(urlopen=lambda *_a, **_k: None)
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if old_badgeware is None:
            del sys.modules["badgeware"]
        else:
            sys.modules["badgeware"] = old_badgeware
        if old_network is None:
            del sys.modules["network"]
        else:
            sys.modules["network"] = old_network
        if old_ntptime is None:
            del sys.modules["ntptime"]
        else:
            sys.modules["ntptime"] = old_ntptime
        if old_urequest is None:
            del sys.modules["urllib.urequest"]
        else:
            sys.modules["urllib.urequest"] = old_urequest
    return module, screen, io


def render_work_status():
    app, screen, io = load_badge_app(
        "apps/work-status/__init__.py", "preview_work_status",
    )
    app.wifi_state = "online"
    samples = (
        ("available", "work-status-available.png"),
        ("meeting", "work-status-meeting.png"),
        ("focus", "work-status-focus.png"),
        ("away", "work-status-away.png"),
        ("lunch", "work-status-lunch.png"),
        ("sleep", "work-status-sleep.png"),
    )
    for status, filename in samples:
        app.current_status = status
        app.current_note = ""
        app.show_address_until = 0
        app.notification_until = 0
        app.draw_ui()
        screen.save(filename)
    app.current_status = "custom"
    app.custom_text = "LUNCH TIME"
    app.custom_symbol = "coffee"
    app.custom_color = "#F0883E"
    app.refresh_custom_brushes()
    app.draw_ui()
    screen.save("work-status-custom.png")


def render_clock():
    app, screen, _io = load_badge_app(
        "apps/desk-clock/__init__.py", "preview_desk_clock",
    )
    app.location_name = "LONDON"
    app.timezone_name = "BST"
    app.wifi_state = "synced"
    now = (2026, 7, 2, 10, 24, 36, 3, 183)
    for mode in ("digital", "analog"):
        screen.brush = app.BACKGROUND
        screen.clear()
        app.draw_header(now)
        if mode == "digital":
            app.draw_digital(now)
        else:
            app.draw_analog(now)
        app.draw_footer()
        screen.save("desk-clock-%s.png" % mode)


def render_currency():
    app, screen, _io = load_badge_app(
        "apps/currency/__init__.py", "preview_currency",
    )
    app.rates = {"GBP": 0.7924, "INR": 83.61}
    app.rate_date = "2026-07-02"
    app.status_message = "LIVE RATES"
    app.fetch_stage = 0
    app.draw_screen()
    screen.save("currency.png")


def capture_laptop(theme="dark", custom=False):
    """Capture the real dashboard using isolated, non-networked sample data."""
    import tkinter as tk
    from unittest.mock import patch

    sys.path.insert(0, str(ROOT / "laptop"))
    import work_status_controller as controller

    config = controller.normalize_config({})
    config["theme"] = theme
    identity = {"id": "ab" * 16, "name": "Preview", "keys": {}}
    with patch.object(controller, "load_config", return_value=config), \
         patch.object(controller, "load_device_config", return_value=identity), \
         patch.object(controller, "save_config"), \
         patch.object(controller, "save_device_config"):
        root = tk.Tk()
        try:
            app = controller.WorkStatusController(root)
            app.device_var.set("Home office")
            app.profile_name_var.set("Home office")
            app.address_var.set("192.168.1.42:8080")
            app._request_succeeded(
                {"status": "focus", "note": "Back at 14:30", "battery": 82},
                "Focus mode is on. Your badge is up to date.",
            )
            if custom:
                app._show_composer(True)
            root.attributes("-topmost", True)
            root.lift()
            root.update()
            app._apply_responsive_layout()
            root.after(350, root.quit)
            root.mainloop()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            w, h = root.winfo_width(), root.winfo_height()
            OUTPUT.mkdir(parents=True, exist_ok=True)
            name = "laptop-custom.png" if custom else "laptop-controller.png"
            ImageGrab.grab((x, y, x + w, y + h)).save(OUTPUT / name, optimize=True)
        finally:
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            root.destroy()


def render_icon():
    image = Image.new("RGBA", (24, 24), (7, 12, 24, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((3, 3, 20, 20), fill=(14, 27, 47), outline=(88, 166, 255))
    draw.line((12, 12, 12, 6), fill=(244, 248, 255), width=2)
    draw.line((12, 12, 17, 14), fill=(163, 113, 247), width=2)
    draw.ellipse((10, 10, 13, 13), fill=(242, 204, 96))
    target = ROOT / "apps" / "desk-clock" / "icon.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, optimize=True)


if __name__ == "__main__":
    render_icon()
    render_work_status()
    render_clock()
    render_currency()
    capture_laptop()
    print("README images written to", OUTPUT)
