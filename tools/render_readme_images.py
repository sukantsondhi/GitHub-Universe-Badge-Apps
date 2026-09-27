"""Render reproducible README previews from the badge drawing functions."""

import importlib.util
import math
import sys
import types
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageGrab


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "images"
SCALE = 2


def font(size, bold=False):
    name = "seguisb.ttf" if bold else "segoeui.ttf"
    for path in (Path("C:/Windows/Fonts") / name,
                 Path("/usr/share/fonts/truetype/dejavu") /
                 ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


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

    def blit(self, image, left, top):
        self.scale_blit(image, left, top, image.width, image.height)

    def scale_blit(self, image, left, top, width, height):
        resized = image.convert("RGBA").resize((int(width), int(height)), Image.Resampling.NEAREST)
        self.image.paste(resized, (int(left), int(top)), resized)

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
        Image=types.SimpleNamespace(load=Image.open),
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

    app.pending_pairing = {"name": "Demo laptop", "code": "123456", "expires": 30000}
    app.draw_ui()
    screen.save("work-status-pairing.png")
    app.pending_pairing = None
    app.ip_address = "192.0.2.42"
    app.show_address_until = io.ticks + 8000
    app.draw_ui()
    screen.save("work-status-address.png")
    app.show_address_until = 0
    app.current_status = "photo"
    app.photo_image = demo_frames()[3].resize((80, 60), Image.Resampling.LANCZOS)
    app.draw_ui()
    screen.save("work-status-photo.png")


def demo_frames():
    """Original geometric artwork; no third-party media or private photos."""
    frames = []
    for index in range(12):
        image = Image.new("RGB", (320, 240), "#edf4f2")
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((0, 168, 319, 239), fill="#18342e")
        drawing.rectangle((24, 28, 295, 147), fill="#ffffff")
        drawing.text((39, 42), "HELLO UNIVERSE", font=font(22, True), fill="#18342e")
        drawing.line((40, 113, 280, 113), fill="#b5cbc3", width=3)
        left = 40 + index * 18
        drawing.rectangle((left, 90, left + 34, 125), fill="#ef7956")
        drawing.rectangle((24, 188, 94, 212), fill="#55c4af")
        drawing.rectangle((106, 188, 176, 212), fill="#f3bf5f")
        drawing.rectangle((188, 188, 295, 212), fill="#d5e3de")
        frames.append(image)
    return frames


def render_demo_animation():
    frames = demo_frames()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUTPUT / "demo-animation.gif", save_all=True,
                   append_images=frames[1:], duration=140, loop=0, disposal=2)


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


def capture_laptop(theme="light", view="status", compact=False):
    """Capture the real dashboard using isolated, non-networked sample data."""
    import tkinter as tk
    from unittest.mock import patch

    sys.path.insert(0, str(ROOT / "laptop"))
    import work_status_controller as controller

    config = controller.normalize_config({})
    config["theme"] = theme
    config["badges"] = {"Demo badge": "192.0.2.42:8080", "Workshop": "192.0.2.43:8080"}
    config["selected"] = "Demo badge"
    identity = {"id": "ab" * 16, "name": "Preview", "keys": {}}
    with patch.object(controller, "load_config", return_value=config), \
         patch.object(controller, "load_device_config", return_value=identity), \
         patch.object(controller, "save_config"), \
         patch.object(controller, "save_device_config"):
        root = tk.Tk()
        app = None
        try:
            app = controller.WorkStatusController(root)
            root.geometry("820x620+24+24" if compact else "1180x830+24+24")
            if view != "setup":
                app._request_succeeded(
                    {"status": "focus", "note": "Back at 14:30", "battery": 82},
                    "Focus mode is on. Your badge is up to date.",
                )
            if view == "custom":
                app._show_composer(True)
                app.custom_text_var.set("BUILDING SOMETHING")
                app._update_custom_preview()
            elif view == "media":
                app._show_composer("photo")
                with patch.object(controller.filedialog, "askopenfilename", return_value=str(OUTPUT / "demo-animation.gif")):
                    app.choose_photo()
            elif view == "pairing":
                app._set_busy(True, "Requesting secure pairing...")
                app.result_queue.put(("pair_pending", {"code": "123456"}, ""))
                app._poll_results()
            elif view == "error":
                app._request_failed("Cannot reach badge: connection timed out.")
            elif view == "setup":
                app.profile_details_open = True
                app.toggle_address_visibility()
            elif view == "widget":
                app.open_widget()
            target = app.widget_window if view == "widget" else root
            target.attributes("-topmost", True)
            target.lift()
            root.update()
            app._apply_responsive_layout()
            root.after(350, root.quit)
            root.mainloop()
            if not target.winfo_viewable():
                raise RuntimeError("The screenshot window is not visible.")
            left, top = target.winfo_rootx(), target.winfo_rooty()
            width, height = target.winfo_width(), target.winfo_height()
            OUTPUT.mkdir(parents=True, exist_ok=True)
            name = "laptop-controller" if view == "status" else "laptop-" + view
            if theme == "dark":
                name += "-dark"
            if compact:
                name += "-compact"
            ImageGrab.grab((left, top, left + width, top + height)).save(OUTPUT / (name + ".png"), optimize=True)
        finally:
            if app is not None:
                app._stop_photo_animation()
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
    render_demo_animation()
    render_work_status()
    render_clock()
    render_currency()
    for screenshot_view in ("status", "custom", "media", "setup", "pairing", "error", "widget"):
        capture_laptop(view=screenshot_view)
    capture_laptop(theme="dark")
    capture_laptop(compact=True)
    print("README images written to", OUTPUT)
