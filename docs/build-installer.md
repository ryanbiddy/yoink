# Yoink Windows installer — build guide

## Quick start

```powershell
# From the repo root, on Windows with PowerShell 5.1 or later:
.\build.ps1
```

The script downloads dependencies on first run (~80 MB cached under `build\cache\`), stages the install layout, and compiles `build\Yoink-Setup-2.0.0.exe`.

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

The spec offered two options. We picked **Option B (Python embeddable + Inno Setup)** for the v2 ship.

| Concern | Option A (PyInstaller) | Option B (embeddable) |
|---|---|---|
| Antivirus false positives | High — PyInstaller bootloader is a known heuristic trip | Low — install is just `python.exe` + `.py` files |
| Build complexity | Spec file tuning, hidden imports | Plain `pip install --target` |
| Hotfix path | Rebuild + redownload entire bundle | Edit `.py` files in place |
| Install size | Smaller (~30 MB) | Larger (~120 MB) |
| Startup time | Slightly faster (already-frozen) | Negligible difference for our HTTP server |
| Update mechanism | Replace `.exe` | Replace `.py` files |

The deciding factor is AV reliability. v2 ships unsigned (we can't justify a code-signing certificate before launch validates the product), so anything that flags antivirus is a death sentence for the activation funnel — the user we just walked through `setup.html` is exactly the user who'll abandon if SmartScreen blocks the install. PyInstaller bootloaders trigger heuristic flags often enough that we'd be debugging false positives instead of bugs.

The 120 MB install footprint is acceptable; the extension already implies users are doing meaningful work with YouTube videos and they have disk.

## What gets bundled

The installer lays out `%LOCALAPPDATA%\Yoink\`:

```
python\           Python 3.11 embeddable + Lib\site-packages with yt-dlp/Pillow/MCP/keyring
bin\              ffmpeg.exe, ffprobe.exe (PATH-prepended by server.py)
server.py         The local HTTP helper
yoink_mcp.py      MCP stdio entry point for agent clients
yoink_mcp_tools.py Shared MCP tool registry
requirements.txt  Dev/runtime MCP SDK + keyring pins
yt_extract.py     Imported by server.py (parse_srt, slugify, fmt_time)
topics.json       Topic-folder routing rules
stop-server.bat   Reads server.pid and kills the helper
stop-server.ps1   PowerShell variant + defensive command-line sweep
skills\yoink\     Yoink Operator Skill, plugin manifest, and system prompt
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
- **yt-dlp** — installed via `pip install yt-dlp==$YTDLP_VERSION` into the embeddable's `site-packages`. Bump `$YTDLP_VERSION` in `build.ps1` after compatibility-testing a new release.
- **ffmpeg** — gyan.dev "release essentials" build (Windows static, GPL): `https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip`. The build script extracts only `ffmpeg.exe` and `ffprobe.exe`; the rest of the archive is discarded.
- **get-pip.py** — `https://bootstrap.pypa.io/get-pip.py`. Used once during the build to bootstrap pip into the embeddable.

All three are cached under `build\cache\` after the first download. Delete the cache or pass `-Clean` to force a refresh.

## v2 release notes

| Component | Version | SHA256 | Notes |
|---|---|---|---|
| Python embeddable | 3.11.9 (amd64) | Locked in `build.ps1` | Acceptance: 3.11.9 is the last 3.11.x with binary installers from python.org. Later 3.11.x are source-only security releases that we'd have to build ourselves. v2 ships 3.11.9 knowing the gap; v2.1 plan: move to the latest 3.12 embeddable. |
| ffmpeg | 7.1 essentials build | Locked in `build.ps1` | Pulled from `github.com/GyanD/codexffmpeg/releases` (gyan.dev's GitHub mirror) for stable URLs. |
| yt-dlp | 2026.03.17 | (pip) | Pinned via `pip install yt-dlp==2026.03.17`. Bump after compatibility-testing a new release. |
| Pillow | 10.4.0 | (pip) | Drives the multimodal paste-corpus generator (resize + JPEG-recompress + base64-encode screenshots for clipboard embedding). Pinned via `pip install Pillow==10.4.0`. |
| MCP Python SDK | 1.27.1 | (pip) | Official Model Context Protocol Python SDK. Powers the stdio MCP server. Pinned via `pip install mcp==1.27.1` and `requirements.txt`. |
| keyring | 25.7.0 | (pip) | Stores the BYO Anthropic API key in Windows Credential Manager. Pinned via `pip install keyring==25.7.0` and `requirements.txt`. |

### SHA256 hashes

The `Confirm-Hash` helper in `build.ps1` verifies SHA256 for the directly-downloaded artifacts (Python embeddable + ffmpeg + get-pip.py). The `$..._SHA256` constants are locked in source, which means:

- A normal build prints `<component> hash OK`.
- A compromised mirror or silent upstream change fails the build with `SHA256 mismatch`.
- `Confirm-Hash` deletes the bad cached file so a re-run pulls fresh after you intentionally update the hash.

When bumping a directly-downloaded component:

1. Run `.\build.ps1` once on a network-connected machine.
2. If the version changed, copy the computed hash from the warning/error output after verifying the artifact source.
3. Paste them into the matching `$PYTHON_SHA256`, `$FFMPEG_SHA256`, `$GETPIP_SHA256` constants in `build.ps1`.
4. Re-run `.\build.ps1` -- it should now print `<component> hash OK` instead of the warnings.
5. Commit the version bump and matching hash update together.

Pip-installed packages (`yt-dlp`, `Pillow`, `mcp`, `keyring`) are version-pinned but not hash-locked yet. Full pip hash-locking would require a `requirements.txt` with `--require-hashes`; for now the installer accepts the trust-pip-itself model while keeping exact package versions stable.

## Updating versions

| Component | Where to change |
|---|---|
| Python | `$PYTHON_VERSION` in `build.ps1`, and the `python*._pth` glob in stage step 2b — Python 3.12 would be `python312._pth` (no other code change needed). |
| yt-dlp | Update `$YTDLP_VERSION` in `build.ps1`. |
| MCP Python SDK | Update `$MCP_VERSION` in `build.ps1` and the matching `mcp==...` pin in `requirements.txt`. |
| keyring | Update `$KEYRING_VERSION` in `build.ps1` and the matching `keyring==...` pin in `requirements.txt`. |
| ffmpeg | gyan.dev rolls the static "release essentials" build forward; the URL stays the same. To pin, swap to a versioned URL from the same site. |
| Yoink itself | Update `$VERSION` in `build.ps1`, `AppVersion` in `installer\yoink.iss`, `VERSION` in `server.py`, and `version` in `extension\manifest.json`. The output filename and the registry/Start Menu names will follow. |

## How `server.py` finds bundled binaries

`server.py` calls `subprocess.run(["ffmpeg", ...])` with no path. To make that work post-install, the top of `server.py` prepends `<install dir>\bin` to the process `PATH`:

```python
_BIN_DIR = HERE / "bin"
if _BIN_DIR.is_dir():
    os.environ["PATH"] = str(_BIN_DIR) + os.pathsep + os.environ.get("PATH", "")
```

In dev mode (running from the repo) `bin\` doesn't exist and the line is a no-op — `ffmpeg` resolves via the user's existing PATH like before.

yt-dlp is invoked as `[sys.executable, "-m", "yt_dlp"]`, so the right interpreter (the embeddable) drives the right `yt_dlp` package automatically.

### Dev mode output root override

By default, Yoink writes extracted videos under the user's Desktop output root (`Desktop\Yoink`, honoring Windows known-folder / OneDrive Desktop redirection). In dev mode, that can be awkward if the repo itself lives on the Desktop: personal yoinks may appear inside or next to the worktree.

Set `YOINK_OUTPUT_DIR` to an existing writable directory before starting `server.py` to override the output root:

```powershell
New-Item -ItemType Directory -Force C:\TestYoinkOut | Out-Null
$env:YOINK_OUTPUT_DIR = 'C:\TestYoinkOut'
python server.py
```

If the env var is missing, points at a non-existent path, or is not writable, Yoink falls back to the normal Desktop root. The installed Start Menu shortcut does not set this variable; it is intended for local development and support smoke tests.

## Known issues

### Antivirus warnings on unsigned builds

`Yoink-Setup-2.0.0.exe` is unsigned. SmartScreen will show "Windows protected your PC" the first time a user runs it, and some AV products may quarantine it. There are three mitigations, in order of cost:

1. **None** — accept the SmartScreen click-through ("More info" → "Run anyway"). Document it on `setup.html` so users know what to expect. Acceptable for v2 if launch volume is small.
2. **Code signing** — buy an OV cert (~$70/yr from one of the few remaining issuers) and sign the installer + `pythonw.exe` with `signtool.exe`. Removes most AV friction but doesn't fully clear SmartScreen until reputation builds.
3. **EV cert** — clears SmartScreen instantly but requires a hardware token and ~$300/yr.

Add signing to `build.ps1` after step 3 (compile) — see `signtool sign /fd SHA256 /tr <ts-url> /td SHA256 /a $exe`.

### Pip bootstrap pulls files we don't ship

`get-pip.py` installs pip + setuptools + wheel into the embeddable. We strip those after runtime packages are installed (see step 2e in `build.ps1`) so the shipped install only contains what the server actually imports. If a future package adds a transitive dependency, it'll land in `site-packages` automatically and get included.

### Prompts library is read-only in v1

The popup ships with 11 starter prompts loaded from `extension/prompts.json` inside the extension package. The original "Edit prompts" link was removed in v1 because its on-disk path (`<HERE>\extension\prompts.json`) only exists in dev mode -- installed users have no `extension\` folder next to the server. The `/open-prompts` server endpoint still exists for dev-mode use but isn't surfaced in the UI.

Tracked as a v1.1 task: store user-overridden prompts in `chrome.storage.local` and add an inline editor in the popup, so the prompt set is portable across installs and editable without touching the filesystem.

### `topics.json` is read-only after install

`topics.json` ships with the installer and lives in `%LOCALAPPDATA%\Yoink\`. Users can edit it (the path is user-writable) but there's no UI for it; today this is a power-user knob. Treat it as configuration that the next installer version will overwrite.

## Launch checklist

Before flipping the extension's download button live:

1. **Build a release artifact:** `.\build.ps1` → produces `build\Yoink-Setup-2.0.0.exe`.
2. **Smoke-test on a clean Windows VM** (see Testing matrix below).
3. **Tag the release in git:** `git tag v2.0.0 && git push --tags`.
4. **Publish to GitHub releases:**
   - Create a new release at `https://github.com/ryanbiddy/yoink/releases/new`.
   - Tag: `v2.0.0`. Title: `Yoink 2.0.0`.
   - Attach `build\Yoink-Setup-2.0.0.exe` as the release asset.
   - Publish (not draft).
   - Verify `https://github.com/ryanbiddy/yoink/releases/latest/download/Yoink-Setup-2.0.0.exe` resolves to the file.
5. **Flip the extension's `INSTALLER_PUBLISHED` flag:**
   - Edit `extension/setup.js` and set `const INSTALLER_PUBLISHED = true;`.
   - Reload the extension and visit `setup.html` -- the **Download Yoink Setup for Windows** button should now be active and link to the latest release.
   - Commit + push: `git commit -am "Enable installer download button (release published)"`.
6. **Publish the extension** to the Chrome Web Store with the updated `setup.js`.

The flag exists so the extension can ship to early users *before* the installer is uploaded -- they see "Coming soon" instead of clicking through to a 404. Forgetting to flip it after publishing the release is recoverable but visible: the download button stays "Coming soon" until the next extension release.

## Testing matrix

After `build.ps1` finishes, smoke-test by:

1. **Fresh install** — run `Yoink-Setup-2.0.0.exe` on a Windows VM that doesn't have Yoink. Confirm:
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
