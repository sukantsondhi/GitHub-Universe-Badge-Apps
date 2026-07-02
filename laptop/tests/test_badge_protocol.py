import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


class FakeState:
    saved = None

    @staticmethod
    def save(_name, value):
        FakeState.saved = value
        return True

    @staticmethod
    def load(_name, _value):
        return False


class FakeBrushes:
    @staticmethod
    def color(*components):
        return components


class FakePixelFont:
    @staticmethod
    def load(path):
        return path


class FakeShape:
    def stroke(self, _width):
        return self


class FakeShapes:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: FakeShape()


class FakeScreen:
    def __init__(self):
        self.font = None
        self.brush = None
        self.texts = []

    def measure_text(self, text):
        return (len(text) * 5, 8)

    def text(self, text, _x, _y):
        self.texts.append(text)

    def draw(self, _shape):
        pass

    def clear(self):
        pass


fake_io = types.SimpleNamespace(
    ticks=0,
    pressed=set(),
    led={},
    LED_TOP_LEFT=0,
    LED_TOP_RIGHT=1,
    LED_BOTTOM_LEFT=2,
    LED_BOTTOM_RIGHT=3,
)
fake_badgeware = types.SimpleNamespace(
    State=FakeState,
    io=fake_io,
    brushes=FakeBrushes(),
    shapes=FakeShapes(),
    screen=FakeScreen(),
    PixelFont=FakePixelFont,
    get_battery_level=lambda: 75,
    is_charging=lambda: False,
    run=lambda *_args, **_kwargs: None,
)
fake_network = types.SimpleNamespace(STA_IF=0)

sys.modules.setdefault("badgeware", fake_badgeware)
sys.modules.setdefault("network", fake_network)

badge_path = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "work-status"
    / "__init__.py"
)
spec = importlib.util.spec_from_file_location("work_status_badge", badge_path)
badge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(badge)


def request_bytes(method, payload=None):
    body = b""
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    return (
        ("%s /api/status HTTP/1.1\r\n" % method).encode("ascii")
        + b"Host: badge\r\n"
        + ("Content-Length: %d\r\n" % len(body)).encode("ascii")
        + b"\r\n"
        + body
    )


def response_json(response):
    return json.loads(response.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


class BadgeProtocolTests(unittest.TestCase):
    def setUp(self):
        fake_io.ticks = 0
        badge.current_status = "available"
        badge.current_note = ""
        badge.custom_text = "HELLO"
        badge.custom_symbol = "star"
        badge.custom_color = "#1F6FEB"
        badge.refresh_custom_brushes()
        badge.battery_level = None
        badge.last_battery_check = -10000
        badge.night_sleep_at = 0
        badge.last_b_press = -10000
        FakeState.saved = None

    def test_get_returns_current_state(self):
        response = badge.handle_request(request_bytes("GET"))
        self.assertTrue(response.startswith(b"HTTP/1.1 200"))
        payload = response_json(response)
        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["battery"], 75)
        self.assertFalse(payload["charging"])

    def test_post_updates_and_persists_state(self):
        response = badge.handle_request(
            request_bytes("POST", {"status": "meeting", "note": "Back at 3"})
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 200"))
        self.assertEqual(badge.current_status, "meeting")
        self.assertEqual(badge.current_note, "Back at 3")
        self.assertEqual(FakeState.saved["status"], "meeting")

    def test_rejects_unknown_status(self):
        response = badge.handle_request(
            request_bytes("POST", {"status": "vacation", "note": ""})
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 400"))
        self.assertEqual(badge.current_status, "available")

    def test_lunch_status_updates_and_persists(self):
        response = badge.handle_request(
            request_bytes("POST", {"status": "lunch", "note": "Curry time"})
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 200"))
        self.assertEqual(badge.current_status, "lunch")
        self.assertEqual(badge.current_note, "Curry time")
        self.assertEqual(FakeState.saved["status"], "lunch")

    def test_sanitizes_and_limits_note(self):
        badge.handle_request(
            request_bytes("POST", {"status": "away", "note": "Line\n" + "x" * 40})
        )
        self.assertNotIn("\n", badge.current_note)
        self.assertLessEqual(len(badge.current_note), 24)

    def test_custom_status_updates_and_persists_design(self):
        response = badge.handle_request(request_bytes("POST", {
            "status": "custom",
            "custom_text": "LUNCH TIME",
            "custom_symbol": "coffee",
            "custom_color": "#F0883E",
        }))
        self.assertTrue(response.startswith(b"HTTP/1.1 200"))
        self.assertEqual(badge.current_status, "custom")
        self.assertEqual(badge.custom_text, "LUNCH TIME")
        self.assertEqual(badge.custom_symbol, "coffee")
        self.assertEqual(badge.custom_color, "#F0883E")
        self.assertEqual(FakeState.saved["custom_symbol"], "coffee")

    def test_custom_status_rejects_invalid_design(self):
        response = badge.handle_request(request_bytes("POST", {
            "status": "custom",
            "custom_text": "NOPE",
            "custom_symbol": "unknown",
            "custom_color": "red",
        }))
        self.assertTrue(response.startswith(b"HTTP/1.1 400"))

    def test_unknown_route_returns_404(self):
        response = badge.handle_request(
            b"GET /unknown HTTP/1.1\r\nHost: badge\r\n\r\n"
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 404"))

    def test_every_status_screen_draws(self):
        badge.wifi_state = "online"
        badge.show_address_until = 0
        badge.notification_until = 0
        for status in badge.STATUS_ORDER:
            with self.subTest(status=status):
                badge.current_status = status
                fake_badgeware.screen.texts = []
                badge.draw_ui()
                expected = (
                    badge.custom_text
                    if status == "custom"
                    else badge.STATUS_LABELS[status]
                )
                self.assertIn(expected, fake_badgeware.screen.texts)
                self.assertFalse(any(
                    text.startswith("A:") or text.startswith("C:")
                    for text in fake_badgeware.screen.texts
                ))
                self.assertIn("75%", fake_badgeware.screen.texts)

    def test_c_button_address_overlay_draws(self):
        badge.wifi_state = "online"
        badge.ip_address = "192.168.1.42"
        badge.show_address_until = 100
        fake_badgeware.screen.texts = []
        badge.draw_ui()
        self.assertIn("192.168.1.42", fake_badgeware.screen.texts)

    def test_double_b_sleep_preparation_disables_services(self):
        badge.server_socket = None
        badge.wlan = None
        fake_io.ticks = 1000
        badge.prepare_night_sleep()
        self.assertEqual(badge.night_sleep_at, 1150)
        self.assertEqual(badge.wifi_state, "sleeping")

    def test_every_custom_symbol_draws(self):
        badge.current_status = "custom"
        badge.show_address_until = 0
        badge.notification_until = 0
        for symbol in badge.CUSTOM_SYMBOLS:
            with self.subTest(symbol=symbol):
                badge.custom_symbol = symbol
                fake_badgeware.screen.texts = []
                badge.draw_ui()
                self.assertIn(badge.custom_text, fake_badgeware.screen.texts)


if __name__ == "__main__":
    unittest.main()
