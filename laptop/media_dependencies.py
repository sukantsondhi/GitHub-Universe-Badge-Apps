"""Dependency checks used by the double-clickable Work Status launcher."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path


MEDIA_MODULES = {
    "Pillow": "PIL",
    "ImageIO": "imageio",
    "ImageIO FFmpeg": "imageio_ffmpeg",
}


def missing_media_dependencies(import_module=importlib.import_module) -> list[str]:
    missing = []
    for label, module_name in MEDIA_MODULES.items():
        try:
            import_module(module_name)
        except ImportError:
            missing.append(label)
    return missing


def console_python() -> Path:
    """Use python.exe beside pythonw.exe so pip output can be captured."""
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        candidate = executable.with_name("python.exe")
        if candidate.exists():
            return candidate
    return executable


def install_media_dependencies() -> tuple[bool, str]:
    requirements = Path(__file__).resolve().with_name("requirements-media.txt")
    if not requirements.exists():
        return False, "The media requirements file is missing."

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                str(console_python()), "-m", "pip", "install",
                "--disable-pip-version-check", "-r", str(requirements),
            ],
            capture_output=True,
            text=True,
            creationflags=creationflags,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "Could not start pip: %s" % exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "pip returned an error").strip()
        return False, detail[-1200:]

    importlib.invalidate_caches()
    missing = missing_media_dependencies()
    if missing:
        return False, "Still unavailable after installation: " + ", ".join(missing)
    return True, "Image, GIF, and video import is ready."
