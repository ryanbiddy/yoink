# Yoink Windows installer — build guide

## Quick start

```powershell
# From the repo root, on Windows with PowerShell 5.1 or later:
.\build.ps1
```

The script downloads dependencies on first run (~80 MB cached under `build\cache\`), stages the install layout, and compiles `build\Yoink-Setup-1.0.0.exe`.

To wipe everything and rebuild from scratch:

```powershell
.\build.ps1 -Clean
```

## Prerequisites

You need both of these installed on the build machine:

- **Inno Setup 6** — <https://jrsoftware.org/isdl.php>. The default install path (`C:\Program Files (x86)\Inno Setup 6`) is auto-detected, otherwise put `ISCC.exe` on `PATH`.
- **PowerShell 5.1+** — ships with Windows 10/11.

You do *not* need a system Python installed; the build downloads and uses an embeddable Python distribution exclusively.

## Architecture: why Python embeddable + Inno Setup

The spec offered two options. We picked **Option B (Python embeddable + Inno Setup)** for the v1 ship.

| Concern | Option A (PyInstaller) | Option B (embeddable) |
|---|---|---|
| Antivirus false positives | High — PyInstaller bootloader is a known heuristic trip | Low — install is just `python.exe` + `.py` files |
| Build complexity | Spec file tuning, hidden imports | Plain `pip install --target` |
| Hotfix path | Rebuild + redownload entire bundle | Edit `.py` files in place |
| Install size | Smaller (~30 MB) | Larger (~120 MB) |
| Startup time | Slightly faster (already-frozen) | Negligible difference for our HTTP server |
| Update mechanism | Replace `.exe` | Replace `.py` files |

The deciding factor is AV reliability. v1 ships unsigned (we can't justify a code-signing certificate before launch validates the product), so anything that flags antivirus is a death sentence for the activation funnel — the user we just walked through `setup.html` is exactly the user who'll abandon if SmartScreen blocks the install. PyInstaller bootloaders trigger heuristic flags often enough that we'd be debugging false positives instead of bugs.

The 120 MB install footprint is acceptable; the extension already implies users are doing meaningful work with YouTube videos and they have disk.

## What gets bundled

The installer lays out `%LOCALAPPDATA%\Yoink\`:

```
python\           Python 3.11 embeddable + Lib\site-packages with yt-dlp
bin\              ffmpeg.exe, ffprobe.exe (PATH-prepended by server.py)
server.py         The local HTTP helper
yt_extract.py     Imported by server.py (parse_srt, slugify, fmt_time)
topics.json       Topic-folder routing rules
stop-server.bat   Reads server.pid and kills the helper
stop-server.ps1   PowerShell variant + defensive command-line sweep
yoink.ico         Used for shortcuts and the uninstaller chrome
unins000.exe      Inno Setup writes this; runs the uninstaller
```

Plus, Windows-side:

- Start Menu group `Yoink` with **Yoink Server** (start), **Stop Yoink Server**, **Yoink folder**, **Uninstall Yoink**.
- `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Yoink` value pointing at `pythonw.exe server.py` so the helper auto-starts on login. Removed cleanly on uninstall (`uninsdeletevalue`).
- Optional **Launch Yoink Server now** checkbox on the finish page (default checked).

The helper runs under `pythonw.exe`, so there's no console window. `server.py` writes `server.pid` on startup and removes it on graceful exit; `stop-server.bat` reads it.

## Where dependencies come from

- **Python embeddable** — `https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip`. Update the `$PYTHON_VERSION` constant in `build.ps1` to bump.
- **yt-dlp** — installed via `pip install yt-dlp` into the embeddable's `site-packages`. Picks up the latest release at build time. Pin a version by changing the `pip install` invocation in `build.ps1` if a future yt-dlp release breaks us.
- **ffmpeg** — gyan.dev "release essentials" build (Windows static, GPL): `https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip`. The build script extracts only `ffmpeg.exe` and `ffprobe.exe`; the rest of the archive is discarded.
- **get-pip.py** — `https://bootstrap.pypa.io/get-pip.py`. Used once during the build to bootstrap pip into the embeddable.

All three are cached under `build\cache\` after the first download. Delete the cache or pass `-Clean` to force a refresh.

## v1 release notes

| Component | Version | SHA256 | Notes |
|---|---|---|---|
| Python embeddable | 3.11.9 (amd64) | locked in `build.ps1` | Acceptance: 3.11.9 is the last 3.11.x with binary installers from python.org. Later 3.11.x are source-only security releases that we'd have to build ourselves. v1 ships 3.11.9 knowing the gap; v1.5 plan: move to the latest 3.12 embeddable. |
| ffmpeg | 7.1 essentials build | locked in `build.ps1` | Pulled from `github.com/GyanD/codexffmpeg/releases` (gyan.dev's GitHub mirror) for stable URLs. |
| yt-dlp | 2026.03.17 | (pip) | Pinned via `pip install yt-dlp==2026.03.17`. Bump after compatibility-testing a new release. |
| Pillow | 10.4.0 | (pip) | Drives the multimodal paste-corpus generator (resize + JPEG-recompress + base64-encode screenshots for clipboard embedding). Pinned via `pip install Pillow==10.4.0`. |

The `Confirm-Hash` helper in `build.ps1` verifies SHA256 for the directly-downloaded artifacts (Python embeddable + ffmpeg + get-pip.py). To bootstrap on first run the `$..._SHA256` constants are empty; the build prints the computed hash with a warning, you paste it in, commit, and subsequent builds verify. A mismatch on any artifact deletes the cached file and fails the build, so a compromised mirror or silent upstream change can't slip through.

yt-dlp's hash isn't pinned because pip's hash-locking requires a `requirements.txt` with `--require-hashes`, and we accept the trust-pip-itself model for v1 -- a compromised release of yt-dlp on PyPI affects the entire Python ecosystem, not just us.

## Updating versions

| Component | Where to change |
|---|---|
| Python | `$PYTHON_VERSION` in `build.ps1`, and the `python*._pth` glob in stage step 2b — Python 3.12 would be `python312._pth` (no other code change needed). |
| yt-dlp | Edit the `pip install yt-dlp` line in `build.ps1` to pin a version (`yt-dlp==2025.10.01`, etc). |
| ffmpeg | gyan.dev rolls the static "release essentials" build forward; the URL stays the same. To pin, swap to a versioned URL from the same site. |
| Yoink itself | Update `$VERSION` in `build.ps1`, `AppVersion` in `installer\yoink.iss`, and `VERSION` in `server.py`. The output filename and the registry/Start Menu names will follow. |

## How `server.py` finds bundled binaries

`server.py` calls `subprocess.run(["ffmpeg", ...])` with no path. To make that work post-install, the top of `server.py` prepends `<install dir>\bin` to the process `PATH`:

```python
_BIN_DIR = HERE / "bin"
if _BIN_DIR.is_dir():
    os.environ["PATH"] = str(_BIN_DIR) + os.pathsep + os.environ.get("PATH", "")
```

In dev mode (running from the repo) `bin\` doesn't exist and the line is a no-op — `ffmpeg` resolves via the user's existing PATH like before.

yt-dlp is invoked as `[sys.executable, "-m", "yt_dlp"]`, so the right interpreter (the embeddable) drives the right `yt_dlp` package automatically.

## Known issues

### Antivirus warnings on unsigned builds

`Yoink-Setup-1.0.0.exe` is unsigned. SmartScreen will show "Windows protected your PC" the first time a user runs it, and some AV products may quarantine it. There are three mitigations, in order of cost:

1. **None** — accept the SmartScreen click-through ("More info" → "Run anyway"). Document it on `setup.html` so users know what to expect. Acceptable for v1 if launch volume is small.
2. **Code signing** — buy an OV cert (~$70/yr from one of the few remaining issuers) and sign the installer + `pythonw.exe` with `signtool.exe`. Removes most AV friction but doesn't fully clear SmartScreen until reputation builds.
3. **EV cert** — clears SmartScreen instantly but requires a hardware token and ~$300/yr.

Add signing to `build.ps1` after step 3 (compile) — see `signtool sign /fd SHA256 /tr <ts-url> /td SHA256 /a $exe`.

### Pip bootstrap pulls files we don't ship

`get-pip.py` installs pip + setuptools + wheel into the embeddable. We strip those after `yt-dlp` is installed (see step 2e in `build.ps1`) so the shipped install only contains what the server actually imports. If a future yt-dlp adds a transitive dep, it'll land in `site-packages` automatically and get included.

### "Edit prompts" link in the popup

The popup's `Edit prompts ▸` link sends a request to the server's `/open-prompts` endpoint, which expects `<HERE>\extension\prompts.json` to exist on disk. That path is the dev-mode layout. Installed users don't have an `extension\` folder next to the server — the extension is loaded from the Chrome Web Store.

For installed users, "Edit prompts" will fail silently. Tracked as a v1.1 task: store user-overridden prompts in `chrome.storage.local` instead of on disk.

### `topics.json` is read-only after install

`topics.json` ships with the installer and lives in `%LOCALAPPDATA%\Yoink\`. Users can edit it (the path is user-writable) but there's no UI for it; today this is a power-user knob. Treat it as configuration that the next installer version will overwrite.

## Launch checklist

Before flipping the extension's download button live:

1. **Build a release artifact:** `.\build.ps1` → produces `build\Yoink-Setup-1.0.0.exe`.
2. **Smoke-test on a clean Windows VM** (see Testing matrix below).
3. **Tag the release in git:** `git tag v1.0.0 && git push --tags`.
4. **Publish to GitHub releases:**
   - Create a new release at `https://github.com/ryanbiddy/yoink/releases/new`.
   - Tag: `v1.0.0`. Title: `Yoink 1.0.0`.
   - Attach `build\Yoink-Setup-1.0.0.exe` as the release asset.
   - Publish (not draft).
   - Verify `https://github.com/ryanbiddy/yoink/releases/latest/download/Yoink-Setup-1.0.0.exe` resolves to the file.
5. **Flip the extension's `INSTALLER_PUBLISHED` flag:**
   - Edit `extension/setup.js` and set `const INSTALLER_PUBLISHED = true;`.
   - Reload the extension and visit `setup.html` -- the **Download Yoink Setup for Windows** button should now be active and link to the latest release.
   - Commit + push: `git commit -am "Enable installer download button (release published)"`.
6. **Publish the extension** to the Chrome Web Store with the updated `setup.js`.

The flag exists so the extension can ship to early users *before* the installer is uploaded -- they see "Coming soon" instead of clicking through to a 404. Forgetting to flip it after publishing the release is recoverable but visible: the download button stays "Coming soon" until the next extension release.

## Testing matrix

After `build.ps1` finishes, smoke-test by:

1. **Fresh install** — run `Yoink-Setup-1.0.0.exe` on a Windows VM that doesn't have Yoink. Confirm:
   - Default install path is `%LOCALAPPDATA%\Yoink`.
   - "Launch Yoink Server now" is checked by default on the finish page.
   - After finish, `Get-Process pythonw` shows a process whose path is inside the install dir.
   - The browser extension's popup turns green within ~3 seconds.
2. **Auto-start** — restart Windows (or sign out and back in). Confirm the helper is running again from the registry Run key.
3. **Stop and restart** — Start Menu → Yoink → Stop Yoink Server. Extension popup goes orange. Start Menu → Yoink → Yoink Server. Goes green again.
4. **Uninstall** — Settings → Apps → Yoink → Uninstall. After completion verify:
   - `%LOCALAPPDATA%\Yoink` is gone (or close to gone — log files may remain if the server was hard-killed).
   - The HKCU `Run\Yoink` value is gone (`reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).
   - The Start Menu group is gone.
