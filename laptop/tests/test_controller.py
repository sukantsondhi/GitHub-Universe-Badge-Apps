import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import work_status_controller as controller
from work_status_controller import (
    AuthenticationFailed,
    BadgeClient,
    STATUSES,
    badge_base_url,
    darken_hex,
    fitted_window_geometry,
    normalize_device_config,
    normalize_config,
    tint_hex,
    x25519,
    X25519_BASE,
)

TEST_DEVICE_ID = "ab" * 16
TEST_DEVICE_KEY = bytes(range(32))


class MockBadgeHandler(BaseHTTPRequestHandler):
    state = {
        "status": "available",
        "note": "",
        "ip": "127.0.0.1",
        "port": 0,
        "battery": 75,
        "charging": False,
    }
    nonce = ""
    forge_response = False

    def log_message(self, _format, *_args):
        pass

    def _reply(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        response_nonce = getattr(self, "response_nonce", "")
        if response_nonce:
            if self.__class__.forge_response:
                signature = "00" * 32
                self.__class__.forge_response = False
            else:
                signature = hmac.new(
                    TEST_DEVICE_KEY,
                    controller.response_auth_message(response_nonce, body),
                    hashlib.sha256,
                ).hexdigest()
            self.send_header("X-Work-Signature", signature)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/challenge?"):
            if ("device_id=" + TEST_DEVICE_ID) not in self.path:
                self._reply(403, {"error": "pairing required"})
                return
            self.__class__.nonce = os.urandom(16).hex()
            self._reply(200, {"nonce": self.nonce, "expires_in": 10})
            return
        if self.path != "/api/status":
            self._reply(404, {"error": "not found"})
            return
        if not self._authorized("GET", b""):
            self._reply(401, {"error": "authentication failed"})
            return
        self._reply(200, self.state)

    def do_POST(self):
        if self.path != "/api/status":
            self._reply(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if not self._authorized("POST", body):
            self._reply(401, {"error": "authentication failed"})
            return
        update = json.loads(body.decode("utf-8"))
        self.state = dict(update)
        self.state["note"] = update.get("note", "")
        self.state["ip"] = "127.0.0.1"
        self.state["port"] = self.server.server_port
        self.state["battery"] = 75
        self.state["charging"] = False
        self._reply(200, self.state)

    def _authorized(self, method, body):
        nonce = self.headers.get("X-Work-Nonce", "")
        supplied = self.headers.get("X-Work-Signature", "")
        if (
            self.headers.get("X-Work-Device") != TEST_DEVICE_ID
            or not nonce
            or nonce != self.__class__.nonce
        ):
            return False
        self.response_nonce = nonce
        self.__class__.nonce = ""
        expected = hmac.new(
            TEST_DEVICE_KEY,
            controller.auth_message(method, "/api/status", nonce, body),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(supplied, expected)


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

    def badge_client(self):
        return BadgeClient(
            self.address,
            TEST_DEVICE_ID,
            "Test laptop",
            TEST_DEVICE_KEY,
        )

    def test_low_memory_photo_checks_badge_support_before_uploading(self):
        from unittest.mock import patch
        from PIL import Image
        import photo_tools

        payload = photo_tools.encode_badge_png(Image.new("RGB", (160, 120)))
        client = self.badge_client()
        with patch.object(client, "_photo_request", return_value={}) as send:
            with self.assertRaisesRegex(RuntimeError, "Update the Work Status app on the badge"):
                client.send_photo(payload)
            send.assert_called_once_with("/api/frame", "GET", b"")

    def test_animation_rejects_old_badge_without_sending_frames(self):
        from unittest.mock import patch

        client = self.badge_client()
        with patch.object(client, "_photo_request", return_value={}) as send:
            with self.assertRaisesRegex(RuntimeError, "animated GIFs"):
                client.send_animation(b"WSA1" + b"x" * 100)
            send.assert_called_once_with("/api/frame", "GET", b"")

    def test_animation_upload_uses_signed_chunks_and_longer_decode_timeout(self):
        from unittest.mock import patch

        client = self.badge_client()
        requests = []
        progress = []
        payload = b"WSA1" + b"x" * 4000

        def send(path, method, body=b"", query="", **kwargs):
            requests.append((path, method, body, kwargs))
            if path == "/api/frame":
                return {"animation_format": "wsa1", "max_animation_bytes": 524288}
            if path == "/api/frame/chunk":
                return {"offset": int(query.split("=")[1]) + len(body)}
            return {"displayed": True}

        with patch.object(client, "_photo_request", side_effect=send), patch.object(
            client, "get_status", return_value={"status": "photo"},
        ):
            self.assertEqual(client.send_animation(payload, progress.append), {"status": "photo"})
        self.assertEqual(json.loads(requests[1][2])["format"], "wsa1")
        self.assertEqual(b"".join(body for path, _, body, _ in requests
                                  if path == "/api/frame/chunk"), payload)
        self.assertEqual(requests[-1][3]["timeout"], 120.0)
        self.assertEqual(progress[-1], 100)

    def test_low_memory_photo_uploads_when_badge_advertises_support(self):
        from unittest.mock import patch
        from PIL import Image
        import photo_tools

        payload = photo_tools.encode_badge_png(Image.new("RGB", (160, 120)))
        client = self.badge_client()

        def send(path, _method, body=b"", query="", **_kwargs):
            if path == "/api/frame":
                return {"png_sizes": [[80, 60], [160, 120]]}
            if path == "/api/frame/chunk":
                return {"offset": int(query.split("=")[1]) + len(body)}
            return {"displayed": True}

        with patch.object(client, "_photo_request", side_effect=send), patch.object(
            client, "get_status", return_value={"status": "photo"},
        ):
            self.assertEqual(client.send_photo(payload), {"status": "photo"})

    def test_photo_upload_uses_signed_chunks_and_waits_for_display_confirmation(self):
        from unittest.mock import patch
        client = self.badge_client()
        payload = b"\x89PNG\r\n\x1a\n" + b"x" * 1897
        requests = []
        progress = []

        def signed(path, method, body=b"", query="", content_type="application/json", timeout=2.5):
            requests.append((path, method, body, query, content_type))
            if path == "/api/frame/chunk":
                offset = int(query.split("=")[1])
                return {"offset": offset + len(body)}
            if path == "/api/frame/finish":
                return {"displayed": True}
            if path == "/api/status":
                return {"status": "photo", "photo": True}
            return {"ok": True}

        with patch.object(client, "_signed_request", side_effect=signed):
            result = client.send_photo(payload, progress=progress.append)
        self.assertEqual(result["status"], "photo")
        self.assertEqual(progress[-1], 100)
        chunks = [item for item in requests if item[0] == "/api/frame/chunk"]
        self.assertGreater(len(chunks), 1)
        self.assertEqual(b"".join(item[2] for item in chunks), payload)
        self.assertTrue(all(item[4] == "application/octet-stream" for item in chunks))
        self.assertEqual(requests[-2][0], "/api/frame/finish")
        self.assertEqual(requests[-1][0], "/api/status")

    def test_photo_upload_rejects_invalid_payload(self):
        with self.assertRaises(ValueError):
            self.badge_client().send_photo(b"not a png" * 100)

    def test_photo_reuses_signed_next_challenge_without_extra_round_trip(self):
        from unittest.mock import patch

        client = self.badge_client()
        with patch.object(client, "_send", side_effect=[
            {"nonce": "a" * 32},
            {"next_nonce": "b" * 32, "nonce_expires_in": 10},
            {"offset": 1},
        ]) as send:
            client._photo_request("/api/frame", "GET", b"")
            client._photo_request("/api/frame/chunk", "POST", b"x", query="?offset=0")
        self.assertEqual(send.call_count, 3)
        self.assertEqual(send.call_args_list[-1].kwargs["response_nonce"], "b" * 32)
        self.assertEqual(client._photo_nonce, "")

    def test_photo_refreshes_cached_challenge_after_expiry(self):
        from unittest.mock import patch

        client = self.badge_client()
        with patch.object(client, "_send", side_effect=[
            {"nonce": "a" * 32},
            {"next_nonce": "b" * 32, "nonce_expires_in": 10},
            {"nonce": "c" * 32}, {"offset": 1},
        ]) as send:
            with patch.object(controller.time, "monotonic", return_value=100):
                client._photo_request("/api/frame", "GET", b"")
            with patch.object(controller.time, "monotonic", return_value=110):
                client._photo_request("/api/frame/chunk", "POST", b"x", query="?offset=0")
        self.assertEqual(send.call_count, 4)
        self.assertEqual(send.call_args_list[-1].kwargs["response_nonce"], "c" * 32)

    def test_photo_retry_refreshes_challenge_after_dropped_response(self):
        from unittest.mock import patch

        client = self.badge_client()
        with patch.object(client, "_send", side_effect=[
            {"nonce": "a" * 32},
            {"next_nonce": "b" * 32, "nonce_expires_in": 10},
            controller.error.URLError("lost response"),
            {"nonce": "c" * 32}, {"offset": 1},
        ]) as send, patch.object(controller.time, "sleep"):
            client._photo_request("/api/frame", "GET", b"")
            self.assertEqual(client._photo_request(
                "/api/frame/chunk", "POST", b"x", query="?offset=0"), {"offset": 1})
        self.assertEqual(send.call_args_list[2].kwargs["response_nonce"], "b" * 32)
        self.assertEqual(send.call_args_list[4].kwargs["response_nonce"], "c" * 32)

    def test_photo_does_not_cache_challenge_from_unverified_response(self):
        from unittest.mock import patch

        client = self.badge_client()
        with patch.object(client, "_send", side_effect=[
            {"nonce": "a" * 32}, AuthenticationFailed("invalid response signature"),
        ]):
            with self.assertRaises(AuthenticationFailed):
                client._photo_request("/api/frame", "GET", b"")
        self.assertEqual(client._photo_nonce, "")

    def test_upload_uses_negotiated_chunk_size_and_legacy_fallback(self):
        from functools import partial
        from unittest.mock import patch

        payload = b"\x89PNG\r\n\x1a\n" + b"x" * 3992

        def send(reply, chunks, path, _method, body=b"", query="", **_kwargs):
            if path == "/api/frame/start":
                return reply
            if path == "/api/frame/chunk":
                chunks.append(body)
                return {"offset": int(query.split("=")[1]) + len(body)}
            return {"displayed": True}

        for reply, expected in (({"chunk_bytes": 1536}, 3), ({}, 6)):
            with self.subTest(reply=reply):
                chunks = []
                client = self.badge_client()

                with patch.object(client, "_photo_request", side_effect=partial(send, reply, chunks)), patch.object(
                    client, "get_status", return_value={"status": "photo"},
                ):
                    client.send_photo(payload)
                self.assertEqual(len(chunks), expected)
                self.assertEqual(b"".join(chunks), payload)

    def test_upload_rejects_invalid_advertised_chunk_sizes(self):
        from unittest.mock import patch

        for chunk_bytes in (0, -1, True, "1536", 4096):
            with self.subTest(chunk_bytes=chunk_bytes):
                client = self.badge_client()
                with patch.object(client, "_photo_request", return_value={"chunk_bytes": chunk_bytes}) as send:
                    with self.assertRaisesRegex(RuntimeError, "invalid photo chunk size"):
                        client.send_photo(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
                    self.assertEqual(send.call_count, 1)

    def test_photo_upload_retries_a_dropped_chunk_connection(self):
        from unittest.mock import patch

        client = self.badge_client()
        payload = b"\x89PNG\r\n\x1a\n" + b"x" * 900
        dropped = False
        chunk_attempts = 0

        def signed(path, method, body=b"", query="",
                   content_type="application/json", timeout=2.5):
            nonlocal dropped, chunk_attempts
            if path == "/api/frame/chunk":
                chunk_attempts += 1
                if not dropped:
                    dropped = True
                    raise controller.error.URLError("connection dropped")
                offset = int(query.split("=")[1])
                return {"offset": offset + len(body)}
            if path == "/api/frame/finish":
                return {"displayed": True}
            if path == "/api/status":
                return {"status": "photo", "photo": True}
            return {"offset": 0}

        with patch.object(client, "_signed_request", side_effect=signed), \
                patch.object(controller.time, "sleep"):
            result = client.send_photo(payload)
        self.assertEqual(result["status"], "photo")
        self.assertGreaterEqual(chunk_attempts, 3)

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

    def test_window_geometry_fits_common_laptop_display(self):
        self.assertEqual(
            fitted_window_geometry(1366, 768),
            "1180x672+93+48",
        )
        self.assertEqual(
            fitted_window_geometry(800, 600),
            "800x504+0+48",
        )

    def test_x25519_matches_rfc_public_key_vector(self):
        private_key = bytes.fromhex(
            "77076d0a7318a57d3c16c17251b26645"
            "df4c2f87ebc0992ab177fba51db92c2a"
        )
        self.assertEqual(
            x25519(private_key, X25519_BASE).hex(),
            "8520f0098930a754748b7ddcb43ef75a0"
            "dbf3a0d26381af4eba4a98eaa9b4e6a",
        )

    def test_device_credentials_are_sanitized(self):
        config = normalize_device_config({
            "id": "ab" * 16,
            "name": "Office laptop with a very long name",
            "keys": {
                "http://192.168.1.42:8080": "01" * 32,
                "https://invalid": "01" * 32,
                "http://bad-key": "nope",
            },
        })
        self.assertEqual(config["id"], "ab" * 16)
        self.assertEqual(config["name"], "Office laptop wi")
        self.assertEqual(
            config["keys"],
            {"http://192.168.1.42:8080": "01" * 32},
        )

    def test_device_credentials_are_persisted(self):
        original_path = controller.DEVICE_CONFIG_PATH
        try:
            with tempfile.TemporaryDirectory() as temp:
                controller.DEVICE_CONFIG_PATH = Path(temp) / "device.json"
                expected = {
                    "version": 1,
                    "id": "cd" * 16,
                    "name": "Office laptop",
                    "keys": {
                        "http://192.168.1.42:8080": "02" * 32,
                    },
                }
                controller.save_device_config(expected)
                self.assertEqual(controller.load_device_config(), expected)
        finally:
            controller.DEVICE_CONFIG_PATH = original_path

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
                    "version": 4,
                    "selected": "Office",
                    "badges": {"Office": "192.168.1.42:8080"},
                    "custom": dict(controller.DEFAULT_CUSTOM),
                    "theme": "light",
                }
                controller.save_config(expected)
                self.assertEqual(controller.load_config(), expected)
        finally:
            controller.CONFIG_PATH = original_config
            controller.LEGACY_CONFIG_PATH = original_legacy

    def test_saved_theme_is_normalized(self):
        dark = normalize_config({
            "badges": {"Office": "192.168.1.42"},
            "selected": "Office",
            "theme": "dark",
        })
        self.assertEqual(dark["theme"], "dark")
        invalid = normalize_config({"theme": "neon"})
        self.assertEqual(invalid["theme"], "light")

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
        result = self.badge_client().get_status()
        self.assertIn(result["status"], tuple(STATUSES) + ("custom",))
        self.assertEqual(result["battery"], 75)

    def test_rejects_forged_badge_response(self):
        MockBadgeHandler.forge_response = True
        with self.assertRaises(AuthenticationFailed):
            self.badge_client().get_status()

    def test_sends_every_status(self):
        client = self.badge_client()
        for status in STATUSES:
            with self.subTest(status=status):
                result = client.set_status(status, "Testing")
                self.assertEqual(result["status"], status)
                self.assertEqual(result["note"], "Testing")

    def test_note_is_limited(self):
        result = self.badge_client().set_status("focus", "x" * 50)
        self.assertEqual(result["note"], "x" * 24)

    def test_sends_custom_status(self):
        result = self.badge_client().set_custom(
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
