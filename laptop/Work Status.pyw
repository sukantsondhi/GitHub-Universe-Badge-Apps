"""No-terminal launcher for the Work Status Badge controller."""

import traceback
from pathlib import Path
from tkinter import messagebox


try:
    # Make relative imports and assets reliable when launched by double-click.
    launcher_dir = Path(__file__).resolve().parent
    from work_status_controller import main

    main()
except Exception:
    messagebox.showerror(
        "Work Status Badge",
        "The controller could not start:\n\n" + traceback.format_exc(),
    )
