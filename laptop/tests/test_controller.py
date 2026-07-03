import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import work_status_controller as controller
from work_status_controller import (
    BadgeClient,
    STATUSES,
    badge_base_url,
    darken_hex,
    normalize_config,
    tint_hex,
)


class MockBadgeHandler(BaseHTTPRequestHandler):
    state = {
        "status": "available",
        "note": "",
        "ip": "127.0.0.1",
        "port": 0,
        "battery": 75,
        "charging": False,
    }

    def log_message(self, _format, *_args):
        pass

    def _reply(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/api/status":
            self._reply(404, {"error": "not found"})
            return
        self._reply(200, self.state)

    def do_POST(self):
        if self.path != "/api/status":
            self._reply(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        update = json.loads(self.rfile.read(length).decode("utf-8"))
        self.state = dict(update)
        self.state["note"] = update.get("note", "")
        self.state["ip"] = "127.0.0.1"
        self.state["port"] = self.server.server_port
        self.state["battery"] = 75
        self.state["charging"] = False
        self._reply(200, self.state)


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockBadgeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.address = "127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_address_defaults_to_port_8080(self):
        self.assertEqual(
            badge_base_url("192.168.1.42"),
            "http://192.168.1.42:8080",
        )

    def test_invalid_address_is_rejected(self):
        with self.assertRaises(ValueError):
            badge_base_url("https://example.com")
        with self.assertRaises(ValueError):
            badge_base_url("")

    def test_preview_background_matches_badge_color_treatment(self):
        self.assertEqual(darken_hex("#1F6FEB"), "#06162F")
        self.assertEqual(darken_hex("#FF9F43"), "#331F0D")

    def test_glass_tint_blends_status_color_towards_white(self):
        self.assertEqual(tint_hex("#000000"), "#E0E0E0")
        self.assertEqual(tint_hex("#FFFFFF"), "#FFFFFF")

    def test_old_address_config_is_migrated_to_named_profile(self):
        config = normalize_config({"address": "192.168.1.42:8080"})
        self.assertEqual(config["selected"], "My Badge")
        self.assertEqual(
            config["badges"]["My Badge"],
            "192.168.1.42:8080",
        )

    def test_named_profiles_are_sanitized(self):
        config = normalize_config({
            "version": 2,
            "selected": "Office",
            "badges": {
                "Office": "192.168.1.10:8080",
                "": "invalid",
                "Broken": 123,
            },
        })
        self.assertEqual(
            config["badges"],
            {"Office": "192.168.1.10:8080"},
        )

    def test_profiles_are_saved_beside_the_controller(self):
        original_config = controller.CONFIG_PATH
        original_legacy = controller.LEGACY_CONFIG_PATH
        try:
            with tempfile.TemporaryDirectory() as temp:
                controller.CONFIG_PATH = Path(temp) / "badge_profiles.json"
                controller.LEGACY_CONFIG_PATH = Path(temp) / "legacy.json"
                expected = {
                    "version": 3,
                    "selected": "Office",
                    "badges": {"Office": "192.168.1.42:8080"},
                    "custom": dict(controller.DEFAULT_CUSTOM),
                }
                controller.save_config(expected)
                self.assertEqual(controller.load_config(), expected)
        finally:
            controller.CONFIG_PATH = original_config
            controller.LEGACY_CONFIG_PATH = original_legacy

    def test_legacy_profile_is_loaded_when_local_file_is_empty(self):
        original_config = controller.CONFIG_PATH
        original_legacy = controller.LEGACY_CONFIG_PATH
        try:
            with tempfile.TemporaryDirectory() as temp:
                controller.CONFIG_PATH = Path(temp) / "badge_profiles.json"
                controller.LEGACY_CONFIG_PATH = Path(temp) / "legacy.json"
                controller.CONFIG_PATH.write_text(
                    '{"version": 2, "selected": "", "badges": {}}',
                    encoding="utf-8",
                )
                controller.LEGACY_CONFIG_PATH.write_text(
                    '{"address": "192.168.1.50:8080"}',
                    encoding="utf-8",
                )
                loaded = controller.load_config()
                self.assertEqual(
                    loaded["badges"]["My Badge"],
                    "192.168.1.50:8080",
                )
        finally:
            controller.CONFIG_PATH = original_config
            controller.LEGACY_CONFIG_PATH = original_legacy

    def test_reads_current_status(self):
        result = BadgeClient(self.address).get_status()
        self.assertIn(result["status"], tuple(STATUSES) + ("custom",))
        self.assertEqual(result["battery"], 75)

    def test_sends_every_status(self):
        client = BadgeClient(self.address)
        for status in STATUSES:
            with self.subTest(status=status):
                result = client.set_status(status, "Testing")
                self.assertEqual(result["status"], status)
                self.assertEqual(result["note"], "Testing")

    def test_note_is_limited(self):
        result = BadgeClient(self.address).set_status("focus", "x" * 50)
        self.assertEqual(result["note"], "x" * 24)

    def test_sends_custom_status(self):
        result = BadgeClient(self.address).set_custom(
            "LUNCH TIME",
            "coffee",
            "#F0883E",
        )
        self.assertEqual(result["status"], "custom")
        self.assertEqual(result["custom_text"], "LUNCH TIME")
        self.assertEqual(result["custom_symbol"], "coffee")
        self.assertEqual(result["custom_color"], "#F0883E")


if __name__ == "__main__":
    unittest.main()
