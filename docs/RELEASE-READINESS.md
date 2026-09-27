# Public release readiness

Review date: **2026-09-27**. This report records checks performed before the
release-preparation commit. The review did not change repository visibility
or rewrite Git history.

## Publication decisions

1. **Project license approved.** The maintainer approved the
   [MIT License](../LICENSE) for original application code on 2026-09-27.
   The license is included at the repository root.
2. **Retain upstream attribution.** All 45 bundled firmware files match the
   MIT-licensed Mona-OS v4.03 release. The upstream license notice is included;
   see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). A project license
   does not replace that attribution or grant trademark rights.
3. **Enable GitHub protections after publishing.** Enable private vulnerability
   reporting, secret scanning/push protection where available, and require the
   test and secret-audit checks before merging. Those settings have not been changed.

The private Pikachu test GIF remains local and ignored. Documentation uses
original generated animation instead. Generated badge state was removed from
Git's file list without deleting the local save.

## Secret audit

The audit uses checksum-verified **Gitleaks 8.30.1**, its default rules, custom
Wi-Fi SSID/password rules, and an additional Python literal-assignment check.
Remote-tracking references and tags were refreshed before scanning. The checkout
is not shallow.

| Scope | Result |
| --- | --- |
| All publishable tracked and nonignored untracked working-tree files | No detected secret or Wi-Fi assignment findings |
| All reachable local/remote-tracking branches and tags | **30 commits scanned** |
| Complete historical contents, including deleted/replaced files | **144 unique historical blobs scanned** |
| Reachable commit messages | Scanned; no detected findings |
| Historical HEAD | `449b7c4efde6fe167dee1d4aee301e82d99f5617` |
| History rewrite | Not performed; no detected secret required one |

Reproduce from the repository root after installing Gitleaks:

```powershell
git fetch --all --tags
python tools/audit_secrets.py --gitleaks path/to/gitleaks
```

**Limits:** no OCR of image pixels, no scan of unreachable objects or deleted
remote refs, and no audit of GitHub releases/artifacts, forks, caches or other
clones. This is detection-based assurance, not proof of absence. The screenshot
generator uses fictional configuration and captures only its own UI bounds;
review images before publication. A later commit or new file needs another scan.

## Feature coverage

Local result: **90 tests passed** on Windows/Python 3.11, with no skipped tests.
All app/tool Python files passed compilation. All 54 local documentation links
resolved, nine desktop screenshot files passed size/nonblank/metadata checks,
and the generated GIF retained all 12 demo frames. Synthetic non-secret fixtures
confirmed that both custom Wi-Fi scanning rules detect their intended patterns.

The tests use real Tkinter widgets, desktop Pillow decoding and controlled
badge/network stubs. They do not emulate the RP2350's complete memory allocator,
radio, timing, power management or firmware graphics implementation.

| Feature | Verification |
| --- | --- |
| All six presets and optional notes | API dispatch, persistence, note limits/sanitization and all badge drawing paths tested |
| Custom text, colour and all eight symbols | Validation, saving, drawing, colour treatment and editor previews tested |
| Secure pairing | X25519 reference vector, matching derived keys, physical-approval handler and persistence tested |
| Rejection, expiry and trust clearing | Rejected/expired pairing cannot create trust; C/DOWN revocation path tested |
| Authentication and replay protection | Unsigned requests, forged responses, nonce reuse and expiry tested |
| Chained upload challenges | Signed next challenge, local expiry fallback and dropped-response retry tested |
| Multiple profiles | Create/save/select/forget and legacy configuration import tested; other profiles are retained |
| IP visibility | Mask/unmask controls tested with fictional addresses |
| Background polling | Connected-only/busy guards reviewed; unsent note/custom drafts survive refreshes |
| Connection errors/progress | Queue results restore controls; progress and recoverable errors tested |
| Themes and view switching | Selected view and drafts survive theme rebuilds; preset/custom/media layouts tested |
| Fullscreen/Escape | Toggle and exit paths tested in real Tk |
| Mini controller | Shared state/battery, six presets, busy locks and dashboard restoration tested |
| Still images | JPEG/PNG/GIF import, crop, dimensions, palette, metadata stripping and compression tested |
| GIF preview | Multiple frames advance; source/crop survive theme changes; preview cleanup tested |
| GIF upload/playback | Frame timing, finite/infinite repeat counts, persistence and safe replacement tested |
| Video import | Generated two-frame MP4 tests first-frame extraction through ImageIO/FFmpeg; not full video playback or certification of every codec |
| Upload integrity | Full checksum, bounds, chunks, legacy negotiation, duplicate chunks and malformed replacements tested |
| Abandoned uploads | One-minute inactivity releases ownership and retains saved media |
| Badge A/C/DOWN controls | Status cycling, leaving photo mode, overlay and trust-clearing paths tested |
| Hardware sleep | Sleep-preparation handler tested; actual electrical sleep/wake is firmware/hardware-specific |
| HOME/app exit | Server-close/save lifecycle reviewed; HOME is dispatched by Mona-OS |
| Windows launcher | Dependency checks, interpreter selection and installer success/failure/timeout paths tested; clean-machine prompts remain a manual check |
| Local API and state | Routes, status/custom payloads, state reload and image-slot paths covered by protocol tests |

Test files:
[badge protocol](../laptop/tests/test_badge_protocol.py),
[controller](../laptop/tests/test_controller.py),
[media processing](../laptop/tests/test_photo_tools.py),
[Tk interface](../laptop/tests/test_ui.py), and
[dependency checks](../laptop/tests/test_media_dependencies.py).

## Hardware observations

During the preceding development session, the connected Universe 2025 badge
successfully received JPEG/PNG/GIF media over Wi-Fi, decoded and displayed it,
advanced GIF frame indices, replaced animation with a still image, and resumed
animation. The 16-frame test GIF improved from 74.2 s to 39.2 s after reducing
authentication round trips. These are individual measurements on one badge/LAN.

The new background-draft and abandoned-upload fixes in this review are
automatically tested but **have not been installed/retested on hardware during
this release review**. New GitHub Actions jobs are configured but have not run
on GitHub yet. Windows/Python 3.11 is the locally tested desktop environment.

## Screenshots

Fresh real-Tk captures cover the light/dark dashboards, custom editor, animated
media preview, profile setup, pairing, error recovery, compact layout and mini
controller. See the [workflow gallery](../laptop/README.md#workflow-screenshots).
Badge illustrations are generated from drawing functions with substitute fonts,
not physical photographs or pixel-exact firmware screenshots.

```powershell
python tools/render_readme_images.py
python -m unittest discover -s laptop/tests -v
python -m compileall -q apps laptop tools
```
