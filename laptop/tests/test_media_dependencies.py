"""Tests for the GUI launcher's optional media dependency bootstrap."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_dependencies


class MediaDependencyTests(unittest.TestCase):
    def test_reports_only_modules_that_cannot_be_imported(self):
        def importer(name):
            if name in ("imageio", "imageio_ffmpeg"):
                raise ImportError(name)
            return object()

        self.assertEqual(
            media_dependencies.missing_media_dependencies(importer),
            ["ImageIO", "ImageIO FFmpeg"],
        )

    def test_requirements_file_is_shipped_beside_launcher(self):
        requirements = Path(media_dependencies.__file__).with_name(
            "requirements-media.txt"
        )
        self.assertTrue(requirements.exists())
        content = requirements.read_text(encoding="utf-8")
        self.assertIn("Pillow", content)
        self.assertIn("imageio-ffmpeg", content)

    def test_pythonw_uses_its_sibling_console_interpreter(self):
        with patch.object(media_dependencies.sys, "executable", "C:/demo/pythonw.exe"), \
                patch.object(Path, "exists", return_value=True):
            self.assertEqual(media_dependencies.console_python().name, "python.exe")

    def test_dependency_install_uses_current_interpreter_and_rechecks_imports(self):
        result = media_dependencies.subprocess.CompletedProcess([], 0, "", "")
        with patch.object(media_dependencies.subprocess, "run", return_value=result) as run, \
                patch.object(media_dependencies, "missing_media_dependencies", return_value=[]):
            success, _ = media_dependencies.install_media_dependencies()
        self.assertTrue(success)
        self.assertEqual(run.call_args.args[0][0], str(media_dependencies.console_python()))
        self.assertEqual(run.call_args.args[0][1:4], ["-m", "pip", "install"])

    def test_dependency_install_failure_and_timeout_are_recoverable(self):
        result = media_dependencies.subprocess.CompletedProcess([], 1, "", "Test pip error")
        with patch.object(media_dependencies.subprocess, "run", return_value=result):
            success, detail = media_dependencies.install_media_dependencies()
        self.assertFalse(success)
        self.assertIn("Test pip error", detail)
        with patch.object(media_dependencies.subprocess, "run", side_effect=
                          media_dependencies.subprocess.TimeoutExpired("pip", 300)):
            success, detail = media_dependencies.install_media_dependencies()
        self.assertFalse(success)
        self.assertIn("Could not start pip", detail)


if __name__ == "__main__":
    unittest.main()
