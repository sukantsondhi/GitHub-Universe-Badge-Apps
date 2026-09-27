"""No-terminal launcher for the Work Status Badge controller."""

import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox


try:
    # Make relative imports and assets reliable when launched by double-click.
    launcher_dir = Path(__file__).resolve().parent
    from media_dependencies import (
        install_media_dependencies,
        missing_media_dependencies,
    )

    missing = missing_media_dependencies()
    if missing:
        setup_window = tk.Tk()
        setup_window.withdraw()
        try:
            approved = messagebox.askyesno(
                "Enable media uploads",
                "Work Status is missing media support:\n\n"
                + "\n".join("• " + name for name in missing)
                + "\n\nInstall it now for this GUI?",
                parent=setup_window,
            )
            if approved:
                installed, detail = install_media_dependencies()
                if installed:
                    messagebox.showinfo(
                        "Media uploads ready", detail, parent=setup_window,
                    )
                else:
                    messagebox.showerror(
                        "Media setup failed",
                        detail + "\n\nWork Status will still open without media support.",
                        parent=setup_window,
                    )
        finally:
            setup_window.destroy()

    from work_status_controller import main

    main()
except Exception:
    messagebox.showerror(
        "Work Status Badge",
        "The controller could not start:\n\n" + traceback.format_exc(),
    )
