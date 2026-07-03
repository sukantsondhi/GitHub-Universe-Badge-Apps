import importlib.util
import binascii
import hashlib
import hmac
import json
import os
import sys
import types
import unittest
from pathlib import Path


class FakeState:
    saved = None
    storage = {}

    @staticmethod
    def save(name, value):
        FakeState.saved = value
        FakeState.storage[name] = json.loads(json.dumps(value))
        return True

    @staticmethod
    def load(name, value):
        if name in FakeState.storage:
            value.update(json.loads(json.dumps(FakeState.storage[name])))
            return True
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
    BUTTON_A=0,
    BUTTON_B=1,
    BUTTON_C=2,
    BUTTON_UP=3,
    BUTTON_DOWN=4,
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


DEVICE_ID = "01" * 16
DEVICE_KEY = bytes(range(32))


def request_bytes(method, payload=None, path="/api/status", headers=None):
    body = b""
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    lines = [
        "%s %s HTTP/1.1" % (method, path),
        "Host: badge",
        "Content-Length: %d" % len(body),
    ]
    for name, value in (headers or {}).items():
        lines.append("%s: %s" % (name, value))
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


def response_json(response):
    return json.loads(response.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def authenticated_request(method, payload=None):
    challenge_response = badge.handle_request(request_bytes(
        "GET",
        path="/api/challenge?device_id=" + DEVICE_ID,
    ))
    nonce = response_json(challenge_response)["nonce"]
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    message = badge.auth_message(method, "/api/status", nonce, body)
    signature = hmac.new(DEVICE_KEY, message, hashlib.sha256).hexdigest()
    return request_bytes(method, payload, headers={
        "X-Work-Device": DEVICE_ID,
        "X-Work-Nonce": nonce,
        "X-Work-Signature": signature,
    })


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
        badge.trusted_devices = {
            DEVICE_ID: {"name": "Test laptop", "key": DEVICE_KEY},
        }
        badge.auth_nonces = {}
        badge.pending_pairing = None
        badge.pairing_results = {}
        badge.security_notice_until = 0
        FakeState.saved = None
        FakeState.storage = {}

    def test_get_returns_current_state(self):
        response = badge.handle_request(authenticated_request("GET"))
        self.assertTrue(response.startswith(b"HTTP/1.1 200"))
        payload = response_json(response)
        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["battery"], 75)
        self.assertFalse(payload["charging"])

    def test_post_updates_and_persists_state(self):
        response = badge.handle_request(
            authenticated_request(
                "POST", {"status": "meeting", "note": "Back at 3"},
            )
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 200"))
        self.assertEqual(badge.current_status, "meeting")
        self.assertEqual(badge.current_note, "Back at 3")
        self.assertEqual(FakeState.saved["status"], "meeting")

    def test_rejects_unknown_status(self):
        response = badge.handle_request(
            authenticated_request(
                "POST", {"status": "vacation", "note": ""},
            )
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 400"))
        self.assertEqual(badge.current_status, "available")

    def test_lunch_status_updates_and_persists(self):
        response = badge.handle_request(
            authenticated_request(
                "POST", {"status": "lunch", "note": "Curry time"},
            )
        )
        self.assertTrue(response.startswith(b"HTTP/1.1 200"))
        self.assertEqual(badge.current_status, "lunch")
        self.assertEqual(badge.current_note, "Curry time")
        self.assertEqual(FakeState.saved["status"], "lunch")

    def test_sanitizes_and_limits_note(self):
        badge.handle_request(
            authenticated_request(
                "POST",
                {"status": "away", "note": "Line\n" + "x" * 40},
            )
        )
        self.assertNotIn("\n", badge.current_note)
        self.assertLessEqual(len(badge.current_note), 24)

    def test_custom_status_updates_and_persists_design(self):
        response = badge.handle_request(authenticated_request("POST", {
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
        response = badge.handle_request(authenticated_request("POST", {
            "status": "custom",
            "custom_text": "NOPE",
            "custom_symbol": "unknown",
            "custom_color": "red",
        }))
        self.assertTrue(response.startswith(b"HTTP/1.1 400"))

    def test_unauthenticated_status_request_is_rejected(self):
        response = badge.handle_request(request_bytes("GET"))
        self.assertTrue(response.startswith(b"HTTP/1.1 401"))

    def test_authenticated_request_cannot_be_replayed(self):
        raw_request = authenticated_request("GET")
        first = badge.handle_request(raw_request)
        replay = badge.handle_request(raw_request)
        self.assertTrue(first.startswith(b"HTTP/1.1 200"))
        self.assertTrue(replay.startswith(b"HTTP/1.1 401"))

    def test_authenticated_response_is_signed_by_badge(self):
        raw_request = authenticated_request("GET")
        nonce = ""
        for line in raw_request.split(b"\r\n"):
            if line.lower().startswith(b"x-work-nonce:"):
                nonce = line.split(b":", 1)[1].strip().decode("ascii")
        response = badge.handle_request(raw_request)
        headers, body = response.split(b"\r\n\r\n", 1)
        supplied = ""
        for line in headers.split(b"\r\n"):
            if line.lower().startswith(b"x-work-signature:"):
                supplied = line.split(b":", 1)[1].strip().decode("ascii")
        expected = hmac.new(
            DEVICE_KEY,
            badge.response_auth_message(nonce, body),
            hashlib.sha256,
        ).hexdigest()
        self.assertTrue(hmac.compare_digest(supplied, expected))

    def test_pairing_requires_approval_and_derives_same_key(self):
        badge.trusted_devices = {}
        private_key = os.urandom(32)
        public_key = badge.x25519(private_key, badge.X25519_BASE)
        response = badge.handle_request(request_bytes(
            "POST",
            {
                "device_id": DEVICE_ID,
                "device_name": "New laptop",
                "public_key": binascii.hexlify(public_key).decode("ascii"),
            },
            path="/api/pair",
        ))
        self.assertTrue(response.startswith(b"HTTP/1.1 200"))
        pairing = response_json(response)
        badge_public = binascii.unhexlify(pairing["public_key"])
        transcript = badge.pairing_transcript(
            DEVICE_ID, public_key, badge_public,
        )
        self.assertEqual(pairing["code"], badge.pairing_code(transcript))
        expected_key = badge.pairing_key(
            badge.x25519(private_key, badge_public),
            transcript,
        )
        self.assertTrue(badge.approve_pending_pairing())
        self.assertEqual(badge.trusted_devices[DEVICE_ID]["key"], expected_key)
        badge.trusted_devices = {}
        badge.load_trusted_devices()
        self.assertEqual(badge.trusted_devices[DEVICE_ID]["key"], expected_key)
        status = badge.handle_request(request_bytes(
            "GET",
            path="/api/pair/status?device_id=" + DEVICE_ID,
        ))
        self.assertEqual(response_json(status)["status"], "approved")

    def test_pending_pairing_draws_code_and_button_choices(self):
        badge.pending_pairing = {
            "id": DEVICE_ID,
            "name": "Office laptop",
            "code": "123456",
            "expires": 30000,
        }
        fake_badgeware.screen.texts = []
        badge.draw_ui()
        self.assertIn("PAIR NEW CONTROLLER", fake_badgeware.screen.texts)
        self.assertIn("123 456", fake_badgeware.screen.texts)
        self.assertIn("UP: APPROVE", fake_badgeware.screen.texts)
        self.assertIn("DOWN: REJECT", fake_badgeware.screen.texts)

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
