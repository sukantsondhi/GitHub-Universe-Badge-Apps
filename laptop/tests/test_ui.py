"""Real Tk regression checks without networking or persisted user settings."""
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import work_status_controller as controller


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.close_root)
        for name, value in (
            ("load_config", controller.normalize_config({})),
            ("load_device_config", {"id": "ab" * 16, "name": "Test", "keys": {}}),
            ("save_config", None), ("save_device_config", None),
        ):
            mock = patch.object(controller, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.app = controller.WorkStatusController(self.root)
        self.root.update()

    def close_root(self):
        self.app._stop_photo_animation()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()

    def test_custom_editor_fits_small_window(self):
        self.root.geometry("820x620")
        self.root.update()
        self.app.toggle_custom_panel()
        self.root.update()
        self.app._apply_responsive_layout()
        self.root.update()
        button = self.app.custom_send_button
        self.assertTrue(button.winfo_ismapped())
        bottom = button.winfo_rooty() + button.winfo_height()
        self.assertLessEqual(bottom, self.app.activity_shell.winfo_rooty())
        self.assertFalse(self.app.status_shell.winfo_ismapped())

    def test_busy_locks_target_and_theme(self):
        self.app.address_var.set("192.168.1.10")
        self.app._set_busy(True, "Sending")
        self.app.new_profile()
        self.app.toggle_theme()
        self.assertEqual(self.app.address_var.get(), "192.168.1.10")
        self.assertTrue(self.app.busy)
        self.assertEqual(str(self.app.device_picker["state"]), "disabled")
        self.app._set_busy(False, "Done")
        self.assertEqual(str(self.app.device_picker["state"]), "readonly")

    def test_background_refresh_preserves_unsent_note_and_custom_design(self):
        self.app._request_succeeded({"status": "focus", "note": "Saved"}, "Connected")
        self.app.note_var.set("Unsent note")
        self.app.custom_text_var.set("Draft design")
        symbol = self.app.custom_symbol_var.get()
        color = self.app.custom_color
        self.app._request_succeeded({"status": "meeting", "note": "Remote", "battery": 42},
                                    "Badge battery refreshed.")
        self.assertEqual(self.app.note_var.get(), "Unsent note")
        self.assertEqual(self.app.current_status, "meeting")
        self.assertEqual(self.app.badge_battery, 42)
        self.app._request_succeeded({"status": "custom", "custom_text": "Remote design",
                                    "custom_symbol": "coffee", "custom_color": "#FF0000"},
                                   "Badge battery refreshed.")
        self.assertEqual(self.app.custom_text_var.get(), "Draft design")
        self.assertEqual(self.app.custom_symbol_var.get(), symbol)
        self.assertEqual(self.app.custom_color, color)

    def test_profiles_save_switch_and_forget_without_losing_other_badges(self):
        for name, address in (("Desk", "192.0.2.10"), ("Workshop", "192.0.2.11")):
            self.app.new_profile()
            self.app.profile_name_var.set(name)
            self.app.address_var.set(address)
            self.app.save_profile()
        self.assertEqual(len(self.app.profiles), 2)
        self.app.device_var.set("Desk")
        self.app._select_profile()
        self.assertEqual(self.app.address_var.get(), "192.0.2.10:8080")
        self.assertIsNone(self.app.current_payload)
        self.app.forget_profile()
        self.assertEqual(list(self.app.profiles), ["Workshop"])
        self.assertEqual(self.app.device_var.get(), "Workshop")

    def test_ip_visibility_and_fullscreen_escape(self):
        self.assertNotEqual(self.app.address_entry.cget("show"), "")
        self.app.toggle_address_visibility()
        self.assertEqual(self.app.address_entry.cget("show"), "")
        self.app.toggle_address_visibility()
        self.assertNotEqual(self.app.address_entry.cget("show"), "")
        self.app.toggle_fullscreen()
        self.root.update()
        self.assertTrue(self.app.fullscreen)
        self.app._escape_view()
        self.root.update()
        self.assertFalse(self.app.fullscreen)

    def test_mini_controller_shares_state_and_respects_busy_controls(self):
        self.app._request_succeeded({"status": "focus", "battery": 90}, "Connected")
        self.app.open_widget()
        self.root.update()
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertEqual(set(self.app.widget_buttons), set(controller.STATUSES))
        self.assertIn("90%", self.app.widget_battery_var.get())
        self.app._set_busy(True, "Sending")
        self.assertTrue(all(str(button["state"]) == "disabled"
                            for button in self.app.widget_buttons.values()))
        self.app._set_busy(False, "Done")
        self.app.close_widget()
        self.root.update()
        self.assertIsNone(self.app.widget_window)
        self.assertNotEqual(self.root.state(), "withdrawn")

    def test_error_and_progress_results_restore_controls(self):
        self.app._set_busy(True, "Sending")
        self.app.result_queue.put(("photo_progress", 42, ""))
        self.app.result_queue.put(("error", None, "Test connection failed"))
        self.app._poll_results()
        self.assertIn("42%", self.app.photo_progress_var.get())
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.connection_var.get(), "Test connection failed")

    def test_cancelled_media_picker_keeps_existing_draft(self):
        from PIL import Image

        source = Image.new("RGB", (160, 120), "red")
        self.app.photo_source = source
        with patch.object(controller.filedialog, "askopenfilename", return_value=""):
            self.app.choose_photo()
        self.assertIs(self.app.photo_source, source)

    def test_theme_rebuild_keeps_tiles_and_draft(self):
        self.app.note_var.set("Unsaved draft")
        self.app.toggle_theme()
        self.root.update()
        self.assertEqual(self.app.note_var.get(), "Unsaved draft")
        self.assertTrue(all(tile.winfo_ismapped() for tile in self.app.status_tiles.values()))

    def test_pasted_note_updates_counter(self):
        self.app.note_var.set("x" * 40)
        self.assertEqual(self.app.note_var.get(), "x" * 24)
        self.assertEqual(self.app.note_count_var.get(), "24 / 24")

    def test_connected_preview_fits_small_window(self):
        self.root.geometry("820x620")
        self.app._request_succeeded(
            {"status": "focus", "note": "Back soon", "battery": 82}, "Connected")
        self.root.update()
        self.app._apply_responsive_layout()
        self.root.update()
        preview = self.app.current_badge_preview
        self.assertTrue(preview.winfo_ismapped())
        self.assertLessEqual(preview.winfo_rooty() + preview.winfo_height(),
                             self.app.activity_shell.winfo_rooty())
        self.assertFalse(self.app.profile_editor.winfo_ismapped())

    def test_photo_tab_is_integrated_and_controls_fit(self):
        self.root.geometry("820x620")
        self.app._show_composer("photo")
        self.root.update()
        self.app._apply_responsive_layout()
        self.root.update()
        self.assertTrue(self.app.photo_shell.winfo_ismapped())
        self.assertEqual(self.app.photo_tab["text"], "Upload media")
        self.assertFalse(self.app.status_shell.winfo_ismapped())
        self.assertFalse(self.app.custom_shell.winfo_ismapped())
        upload = self.app.photo_upload_button
        self.assertTrue(upload.winfo_ismapped())
        self.assertLessEqual(
            upload.winfo_rooty() + upload.winfo_height(),
            self.app.activity_shell.winfo_rooty(),
        )
        self.assertEqual(str(upload["state"]), "disabled")
        self.assertIn("Upload", upload["text"])
        self.assertIn("GIF", self.app.photo_choose_button["text"])

    def test_photo_tab_and_unsent_crop_survive_theme_change(self):
        from PIL import Image
        self.app.photo_source = Image.new("RGB", (640, 480), "#3184cf")
        self.app._show_composer("photo")
        self.app.toggle_theme()
        self.root.update()
        self.assertTrue(self.app.photo_shell.winfo_ismapped())
        self.assertEqual(self.app.photo_source.size, (640, 480))
        self.app._request_succeeded(
            {"status": "photo", "battery": 75, "photo": True},
            "Photo displayed",
        )
        self.assertEqual(self.app.current_status, "photo")
        self.assertIn("PHOTO FRAME", self.app.current_var.get())

    def test_custom_tab_survives_theme_change(self):
        self.app._show_composer(True)
        self.app.custom_text_var.set("Building something")
        self.app.toggle_theme()
        self.root.update()
        self.assertTrue(self.app.custom_shell.winfo_ismapped())
        self.assertFalse(self.app.status_shell.winfo_ismapped())
        self.assertEqual(self.app.custom_text_var.get(), "Building something")

    def test_gif_preview_moves_and_upload_sends_animation_with_crop(self):
        import tempfile
        from PIL import Image
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "moving.gif"
            Image.new("RGB", (320, 240), "red").save(
                path, save_all=True, append_images=[Image.new("RGB", (320, 240), "blue")],
                duration=10000, loop=0,
            )
            self.app._show_composer("photo")
            with patch.object(controller.filedialog, "askopenfilename", return_value=str(path)):
                self.app.choose_photo()
            self.assertEqual(self.app.photo_source.getpixel((0, 0)), (255, 0, 0))
            self.app._advance_photo_preview()
            self.assertEqual(self.app.photo_source.getpixel((0, 0)), (0, 0, 255))
            self.app.photo_zoom_var.set(1.5)
            self.app.photo_pan_x = 8
            self.app.photo_pan_y = -12
            self.app.toggle_theme()
            self.root.update()
            self.assertEqual(self.app.photo_source_path, str(path))
            client = Mock()
            with patch.object(self.app, "_has_pairing_key", return_value=True), patch.object(
                self.app, "_run_request",
            ) as run, patch.object(controller.photo_tools, "encode_badge_animation", return_value=b"frames") as encode:
                self.app.send_photo()
                run.call_args.args[0](client)
            encode.assert_called_once_with(str(path), 1.5, 8, -12)
            client.send_animation.assert_called_once()
            client.send_photo.assert_not_called()
            self.app._stop_photo_animation()
            self.assertIsNone(self.app.photo_animation_after)
