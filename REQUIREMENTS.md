# Yoink — Manual Setup

The one-click installer is shipping in v1 launch. Until then, here's the manual setup.

## Prerequisites

- Windows 10 or 11
- Python 3.11 or higher
- Git (for cloning this repo)
- A Chromium-based browser (Chrome, Edge, Brave, Comet, Arc)

## Setup steps

1. Clone the repo:
   ```
   git clone https://github.com/ryanbiddy/yoink.git
   cd yoink
   ```

2. Install yt-dlp:
   ```
   pip install yt-dlp
   ```

3. Install ffmpeg:
   ```
   winget install Gyan.FFmpeg
   ```
   Restart your terminal so the new PATH takes effect.

4. Verify both tools are available:
   ```
   yt-dlp --version
   ffmpeg -version
   ```

5. Start the Yoink server:
   ```
   double-click start_server.bat
   ```
   Or from PowerShell:
   ```
   pythonw server.py
   ```

6. Verify the server is running:
   ```
   Invoke-RestMethod http://127.0.0.1:5179/ping
   ```
   Should return `ok : True`.

7. Load the extension in your browser:
   - Open `chrome://extensions/` (or `comet://extensions/`)
   - Toggle Developer mode on
   - Click "Load unpacked"
   - Select the `extension/` folder

8. Pin the Yoink extension to your toolbar.

9. Open any YouTube video. Click the Yoink button under the video.

## Known caveats

- The server must be running for the extension to work
- ffmpeg PATH only refreshes for shells started after the install
- If you start the server from an elevated shell, it may conflict with the regular shell instance — use the regular shell unless you have a specific reason

## Troubleshooting

If the Yoink button doesn't appear, refresh the YouTube page once. YouTube is a SPA and the extension's MutationObserver sometimes loses the race on first load.

If clicking Yoink does nothing, open DevTools (F12), Console tab, and look for errors prefixed with `[Yoink]`.
