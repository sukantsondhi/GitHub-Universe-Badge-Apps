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
        self.assertFalse(self.app.status_shell.winfo_ismapped())
        self.assertFalse(self.app.custom_shell.winfo_ismapped())
        upload = self.app.photo_upload_button
        self.assertTrue(upload.winfo_ismapped())
        self.assertLessEqual(
            upload.winfo_rooty() + upload.winfo_height(),
            self.app.activity_shell.winfo_rooty(),
        )
        self.assertEqual(str(upload["state"]), "disabled")

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
