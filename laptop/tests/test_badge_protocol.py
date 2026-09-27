import importlib.util
import binascii
import hashlib
import hmac
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
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

    def blit(self, _image, _x, _y):
        pass

    def scale_blit(self, _image, _x, _y, _width, _height):
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
class FakeImage:
    @staticmethod
    def load(path):
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            return types.SimpleNamespace(
                width=image.width, height=image.height,
                pixel=image.convert("RGB").getpixel((0, 0)),
            )


fake_badgeware = types.SimpleNamespace(
    State=FakeState,
    io=fake_io,
    brushes=FakeBrushes(),
    shapes=FakeShapes(),
    screen=FakeScreen(),
    PixelFont=FakePixelFont,
    Image=FakeImage,
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
        fake_io.pressed = set()
        badge.current_status = "available"
        badge.photo_image = None
        badge.photo_animation = None
        badge.photo_filename = ""
        badge.photo_upload = None
        badge.photo_last_finish = None
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
        self.assertIn("PAIR YOUR LAPTOP", fake_badgeware.screen.texts)
        self.assertIn("123 456", fake_badgeware.screen.texts)
        self.assertIn("UP: APPROVE", fake_badgeware.screen.texts)
        self.assertIn("DOWN: REJECT", fake_badgeware.screen.texts)

    def test_pairing_can_be_rejected_or_expire_without_trusting_device(self):
        for action in ("reject", "expire"):
            with self.subTest(action=action):
                badge.trusted_devices = {}
                badge.pending_pairing = {"id": DEVICE_ID, "name": "Test", "expires": 100}
                if action == "reject":
                    badge.reject_pending_pairing()
                else:
                    fake_io.ticks = 100
                    badge.expire_security_state()
                self.assertIsNone(badge.pending_pairing)
                self.assertNotIn(DEVICE_ID, badge.trusted_devices)

    def test_badge_buttons_cycle_status_toggle_address_and_clear_trust(self):
        with patch.object(badge, "service_wifi"), patch.object(badge, "service_http"), \
                patch.object(badge, "draw_ui"), patch.object(badge.time, "sleep_ms", create=True):
            badge.current_status = "photo"
            badge.current_note = "Old note"
            fake_io.pressed = {fake_io.BUTTON_A}
            badge.update()
            self.assertEqual(badge.current_status, "available")
            self.assertEqual(badge.current_note, "")
            badge.show_address_until = 0
            fake_io.pressed = {fake_io.BUTTON_C}
            badge.update()
            self.assertGreater(badge.show_address_until, fake_io.ticks)
            fake_io.pressed = {fake_io.BUTTON_DOWN}
            badge.update()
            self.assertEqual(badge.trusted_devices, {})
            self.assertEqual(badge.show_address_until, 0)

    def test_abandoned_upload_expires_without_losing_saved_photo(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            badge, "PHOTO_TEMP", os.path.join(folder, "incoming.png"),
        ):
            Path(badge.PHOTO_TEMP).write_bytes(b"partial upload")
            badge.photo_upload = {"last_activity": 0, "owner": "other-controller"}
            badge.photo_filename = "saved-photo.png"
            fake_io.ticks = badge.PHOTO_UPLOAD_TIMEOUT_MS - 1
            badge.expire_photo_upload()
            self.assertIsNotNone(badge.photo_upload)
            fake_io.ticks += 1
            response = self.photo_command("GET", "/api/frame")
            self.assertTrue(response.startswith(b"HTTP/1.1 200"))
            self.assertIsNone(badge.photo_upload)
            self.assertFalse(Path(badge.PHOTO_TEMP).exists())
            self.assertEqual(badge.photo_filename, "saved-photo.png")

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
                self.assertIn("LINK", fake_badgeware.screen.texts)
                if status != "custom" and not badge.current_note:
                    self.assertIn("A: next  C: connect", fake_badgeware.screen.texts)
                self.assertIn("75%", fake_badgeware.screen.texts)


    def photo_command(self, method, path, body=b"", query="", nonce=None):
        if nonce is None:
            challenge = badge.handle_request(request_bytes(
                "GET", path="/api/challenge?device_id=" + DEVICE_ID,
            ))
            nonce = response_json(challenge)["nonce"]
        signature = hmac.new(
            DEVICE_KEY,
            badge.auth_message(method, path, nonce, body),
            hashlib.sha256,
        ).hexdigest()
        head = (
            "%s %s%s HTTP/1.1\r\nHost: badge\r\n"
            "Content-Length: %d\r\n"
            "X-Work-Device: %s\r\nX-Work-Nonce: %s\r\n"
            "X-Work-Signature: %s\r\n\r\n"
        ) % (method, path, query, len(body), DEVICE_ID, nonce, signature)
        return badge.handle_request(head.encode("ascii") + body)

    def test_photo_requires_existing_work_status_pairing(self):
        raw = request_bytes("POST", {"size": 100, "sha256": "a" * 64},
                            path="/api/frame/start")
        self.assertTrue(badge.handle_request(raw).startswith(b"HTTP/1.1 401"))
        good = self.photo_command("GET", "/api/frame")
        self.assertTrue(good.startswith(b"HTTP/1.1 200"))
        self.assertFalse(response_json(good)["has_photo"])
        self.assertIn([80, 60], response_json(good)["png_sizes"])
        self.assertEqual(response_json(good)["animation_format"], "wsa1")

    def test_photo_response_supplies_signed_single_use_next_challenge(self):
        _, challenge = badge.issue_challenge(DEVICE_ID)
        original_nonce = challenge["nonce"]
        first = self.photo_command("GET", "/api/frame", nonce=original_nonce)
        body = first.split(b"\r\n\r\n", 1)[1]
        expected = hmac.new(
            DEVICE_KEY, badge.response_auth_message(original_nonce, body), hashlib.sha256,
        ).hexdigest()
        self.assertIn(("X-Work-Signature: " + expected).encode(), first)
        nonce = response_json(first)["next_nonce"]
        self.assertNotEqual(nonce, original_nonce)
        second = self.photo_command("GET", "/api/frame", nonce=nonce)
        self.assertTrue(second.startswith(b"HTTP/1.1 200"))
        replay = self.photo_command("GET", "/api/frame", nonce=nonce)
        self.assertTrue(replay.startswith(b"HTTP/1.1 401"))
        next_nonce = response_json(second)["next_nonce"]
        fake_io.ticks += badge.AUTH_NONCE_TIMEOUT_MS
        expired = self.photo_command("GET", "/api/frame", nonce=next_nonce)
        self.assertTrue(expired.startswith(b"HTTP/1.1 401"))

    def test_upload_services_network_without_status_animation_delay(self):
        with patch.object(badge, "service_wifi"), patch.object(badge, "service_http"), \
                patch.object(badge, "draw_ui"), patch.object(badge, "client_socket", None), \
                patch.object(badge.time, "sleep_ms", create=True) as delay:
            badge.photo_upload = {"last_activity": 0}
            fake_io.ticks = 500
            badge.update()
            delay.assert_called_with(badge.TRANSFER_FRAME_DELAY_MS)
            fake_io.ticks = badge.TRANSFER_FAST_WINDOW_MS + 1
            badge.update()
            delay.assert_called_with(badge.FRAME_DELAY_MS)
            badge.client_socket = object()
            badge.update()
            delay.assert_called_with(badge.TRANSFER_FRAME_DELAY_MS)
            badge.client_socket = None
            badge.photo_upload = None
            badge.current_status = "sleep"
            badge.update()
            delay.assert_called_with(badge.SLEEP_STATUS_FRAME_DELAY_MS)

    def upload_animation(self, payload):
        metadata = json.dumps({
            "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            "format": "wsa1",
        }).encode()
        start = self.photo_command("POST", "/api/frame/start", metadata)
        self.assertTrue(start.startswith(b"HTTP/1.1 200"), start)
        for offset in range(0, len(payload), badge.PHOTO_CHUNK_MAX):
            chunk = payload[offset:offset + badge.PHOTO_CHUNK_MAX]
            response = self.photo_command(
                "POST", "/api/frame/chunk", chunk, "?offset=" + str(offset))
            self.assertTrue(response.startswith(b"HTTP/1.1 200"), response)
        return self.photo_command("POST", "/api/frame/finish", metadata)

    def test_animation_upload_playback_reload_and_failed_replacement(self):
        from PIL import Image
        from laptop.photo_tools import encode_badge_animation

        with tempfile.TemporaryDirectory() as folder, patch.multiple(
            badge, PHOTO_TEMP=os.path.join(folder, "upload.png"),
            ANIMATION_A=os.path.join(folder, "anim-a"),
            ANIMATION_B=os.path.join(folder, "anim-b"),
        ):
            path = Path(folder) / "moving.gif"
            Image.new("RGB", (160, 120), "red").save(
                path, save_all=True, append_images=[Image.new("RGB", (160, 120), "blue")],
                duration=[100, 250], loop=1,
            )
            payload = encode_badge_animation(path)
            response = self.upload_animation(payload)
            self.assertTrue(response.startswith(b"HTTP/1.1 200"), response)
            self.assertEqual(badge.photo_image.pixel, (255, 0, 0))
            fake_io.ticks = 99
            badge.draw_ui()
            self.assertEqual(badge.photo_animation["index"], 0)
            fake_io.ticks = 100
            badge.draw_ui()
            self.assertEqual(badge.photo_image.pixel, (0, 0, 255))
            fake_io.ticks = 350
            badge.draw_ui()
            self.assertEqual(badge.photo_image.pixel, (255, 0, 0))
            fake_io.ticks = 450
            badge.draw_ui()
            fake_io.ticks = 700
            badge.draw_ui()
            self.assertEqual(badge.photo_image.pixel, (0, 0, 255))
            self.assertEqual(badge.photo_animation["remaining"], 0)
            badge.photo_image = None
            badge.photo_animation = None
            badge.load_photo()
            self.assertEqual(badge.photo_image.pixel, (255, 0, 0))
            previous = badge.photo_filename
            response = self.upload_animation(payload[:-12])
            self.assertTrue(response.startswith(b"HTTP/1.1 400"), response)
            self.assertEqual(badge.photo_filename, previous)
            self.assertEqual(badge.photo_image.pixel, (255, 0, 0))
            self.assertEqual(badge.photo_status()["frame_count"], 2)
            badge.photo_animation["remaining"] = -1
            fake_io.ticks = badge.photo_animation["due"]
            badge.draw_ui()
            fake_io.ticks = badge.photo_animation["due"]
            badge.draw_ui()
            self.assertEqual(badge.photo_animation["index"], 0)
            self.assertEqual(badge.photo_animation["remaining"], -1)

    def test_default_photo_storage_is_outside_read_only_system_apps(self):
        self.assertFalse(badge.PHOTO_TEMP.startswith(badge.APP_DIR + "/"))
        self.assertFalse(badge.PHOTO_A.startswith(badge.APP_DIR + "/"))
        self.assertFalse(badge.PHOTO_B.startswith(badge.APP_DIR + "/"))
        self.assertTrue(badge.PHOTO_TEMP.startswith("/"))

    def test_authenticated_photo_upload_switches_back_to_status(self):
        from PIL import Image
        from laptop.photo_tools import encode_badge_png

        png = encode_badge_png(Image.new("RGB", (160, 120), (25, 120, 200)))
        with tempfile.TemporaryDirectory() as folder:
            old_paths = (badge.PHOTO_TEMP, badge.PHOTO_A, badge.PHOTO_B)
            try:
                badge.PHOTO_TEMP = os.path.join(folder, "upload.png")
                badge.PHOTO_A = os.path.join(folder, "photo-a.png")
                badge.PHOTO_B = os.path.join(folder, "photo-b.png")
                metadata = json.dumps({
                    "size": len(png),
                    "sha256": hashlib.sha256(png).hexdigest(),
                }).encode()
                start = self.photo_command(
                    "POST", "/api/frame/start", metadata)
                self.assertTrue(start.startswith(b"HTTP/1.1 200"))
                self.assertEqual(response_json(start)["chunk_bytes"], badge.PHOTO_CHUNK_MAX)
                for offset in range(0, len(png), badge.PHOTO_CHUNK_MAX):
                    body = png[offset:offset + badge.PHOTO_CHUNK_MAX]
                    chunk = self.photo_command(
                        "POST", "/api/frame/chunk", body, "?offset=" + str(offset))
                    self.assertEqual(response_json(chunk)["offset"], offset + len(body))
                finish = self.photo_command(
                    "POST", "/api/frame/finish", b"{}")
                self.assertTrue(finish.startswith(b"HTTP/1.1 200"))
                self.assertEqual(badge.current_status, "photo")
                self.assertTrue(os.path.exists(badge.PHOTO_A))
                self.assertTrue(badge.status_payload()["photo"])
                self.assertIsNone(badge.photo_animation)
                badge.draw_ui()
                response = badge.handle_request(authenticated_request(
                    "POST", {"status": "focus", "note": ""}))
                self.assertTrue(response.startswith(b"HTTP/1.1 200"))
                self.assertEqual(badge.current_status, "focus")
            finally:
                badge.PHOTO_TEMP, badge.PHOTO_A, badge.PHOTO_B = old_paths

    def test_photo_rejects_wrong_transfer_checksum(self):
        with tempfile.TemporaryDirectory() as folder:
            old = badge.PHOTO_TEMP
            badge.PHOTO_TEMP = os.path.join(folder, "incoming.png")
            try:
                metadata = json.dumps({"size": 24, "sha256": "0" * 64}).encode()
                self.assertTrue(self.photo_command(
                    "POST", "/api/frame/start", metadata).startswith(b"HTTP/1.1 200"))
                self.assertTrue(self.photo_command(
                    "POST", "/api/frame/chunk", b"x" * 24, "?offset=0"
                ).startswith(b"HTTP/1.1 200"))
                self.assertTrue(self.photo_command(
                    "POST", "/api/frame/finish", b"{}"
                ).startswith(b"HTTP/1.1 400"))
                self.assertEqual(badge.current_status, "available")
            finally:
                badge.PHOTO_TEMP = old

    def test_repeated_photo_chunk_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            old = badge.PHOTO_TEMP
            badge.PHOTO_TEMP = os.path.join(folder, "incoming.png")
            try:
                body = b"x" * 24
                metadata = json.dumps({
                    "size": 48,
                    "sha256": hashlib.sha256(body * 2).hexdigest(),
                }).encode()
                self.assertTrue(self.photo_command(
                    "POST", "/api/frame/start", metadata,
                ).startswith(b"HTTP/1.1 200"))
                first = self.photo_command(
                    "POST", "/api/frame/chunk", body, "?offset=0",
                )
                repeated = self.photo_command(
                    "POST", "/api/frame/chunk", body, "?offset=0",
                )
                self.assertEqual(response_json(first)["offset"], 24)
                self.assertEqual(response_json(repeated)["offset"], 24)
                self.assertEqual(os.path.getsize(badge.PHOTO_TEMP), 24)
            finally:
                badge.PHOTO_TEMP = old

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
