# Contributing

Work Status targets the Universe 2025 badge running Mona-OS v4.03. Keep changes
compatible with its constrained MicroPython runtime. The desktop controller
uses Python 3.11+, Tkinter and optional media dependencies.

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r laptop/requirements-media.txt
.venv\Scripts\python -m unittest discover -s laptop/tests -v
.venv\Scripts\python -m compileall -q apps laptop tools
```

On Linux use `.venv/bin/python`; Tk and a display are required. For headless
tests use `xvfb-run -a python -m unittest discover -s laptop/tests -v` after
installing Tkinter and Xvfb through your distribution. CI is configured for
Windows and Ubuntu. Do not run badge entry points with desktop Python expecting
hardware APIs to exist; protocol tests supply explicit stubs.

## Changes and tests

- Keep badge allocations small and drawing work bounded. Do not cache entire
  animations in RAM or bypass upload validation to improve speed.
- Extend existing tests under [laptop/tests](laptop/tests) for the affected
  protocol, media or UI behaviour. Use generated fixtures and fake keys only.
- Preserve older badge/client compatibility when changing the upload protocol.
- Test pairing approval/rejection, timeout, replay protection and response
  authentication after security changes. Report hardware verification separately.
- Update the [user guide](laptop/README.md) when controls, limits or behaviour
  change. Do not include personal addresses, SSIDs, passwords, keys or photos.

## Documentation images

```powershell
python tools/render_readme_images.py
```

Run in an interactive desktop session with Pillow installed. The renderer
temporarily opens topmost Tk windows and captures only their content bounds.
It uses patched configuration loaders, fictional profiles and original demo
art, not your saved credentials or connected badge. It does not replace app
icons. Inspect the generated images before publishing.

Badge images are desktop illustrations from drawing functions, with substitute
fonts. Label actual hardware photographs separately and remove personal/network
information before attaching them.

## Secret audit before publishing

Install [Gitleaks](https://github.com/gitleaks/gitleaks) from an official release
and verify its published checksum, then run:

```powershell
git fetch --all --tags
python tools/audit_secrets.py --gitleaks path/to/gitleaks
```

The audit rejects shallow checkouts and checks publishable current files,
every reachable commit, complete historical blobs, commit messages, and literal
Python Wi-Fi settings. It emits locations rather than secret values and exits
nonzero on findings. Optional `--summary` output should stay in an ignored local
folder, not be attached to public issues without inspection.

Ignoring or deleting a file does not erase earlier commits. If a real secret
was committed, rotate/revoke it first, then coordinate a history rewrite with
the maintainers. Never paste the value into an issue or commit message.

## Pull requests

Describe the user-visible change, tests run and any unverified hardware behaviour.
Include sanitized screenshots for UI changes. Avoid unrelated formatting,
generated device state, private media and dependency caches. Original application
code uses the [MIT License](LICENSE); retain the applicable
[third-party notices](THIRD_PARTY_NOTICES.md) when reusing upstream material.
