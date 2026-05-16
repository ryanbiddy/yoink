# Yoink installer build orchestrator.
#
# One-command build:  .\build.ps1
#
# Steps:
#   1. Download Python embeddable, ffmpeg, and get-pip into build\cache\
#      (skipped if already cached).
#   2. Stage the install layout under installer\staging\:
#        python\   embeddable Python with site-packages enabled and
#                  yt-dlp installed via pip
#        bin\      ffmpeg.exe (and ffprobe.exe if present)
#        server.py, yt_extract.py, topics.json, skills\, stop-server.{bat,ps1},
#        yoink.ico
#   3. Run ISCC.exe against installer\yoink.iss to produce
#      build\Yoink-Setup-<version>.exe
#
# See docs\build-installer.md for the architecture rationale and
# instructions on updating Python / yt-dlp / ffmpeg versions.

[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
# Suppress Invoke-WebRequest's progress UI -- on PS 5.1 it slows large
# downloads to a crawl due to a known performance bug.
$ProgressPreference = 'SilentlyContinue'

# ---- Paths --------------------------------------------------------------
$RepoRoot     = $PSScriptRoot
$InstallerDir = Join-Path $RepoRoot 'installer'
$BuildDir     = Join-Path $RepoRoot 'build'
$CacheDir     = Join-Path $BuildDir 'cache'
$StagingDir   = Join-Path $InstallerDir 'staging'
$TemplatesDir = Join-Path $InstallerDir 'templates'
$IconSrc      = Join-Path $InstallerDir 'yoink.ico'

# ---- Versions (pinned for v2 ship) --------------------------------------
$VERSION        = '2.0.0'
# Python 3.11.9 is the last 3.11.x with binary installers; later 3.11 are
# source-only security releases. v2 accepts this; v2.1 plan: move to 3.12.
$PYTHON_VERSION = '3.11.9'
$PYTHON_URL     = "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-embed-amd64.zip"
$GETPIP_URL     = 'https://bootstrap.pypa.io/get-pip.py'
# ffmpeg 7.1 essentials build from gyan.dev (mirrored on GitHub for stable URL).
$FFMPEG_VERSION = '7.1'
$FFMPEG_URL     = "https://github.com/GyanD/codexffmpeg/releases/download/$FFMPEG_VERSION/ffmpeg-$FFMPEG_VERSION-essentials_build.zip"
# yt-dlp pip pin -- bump after compatibility-testing a new release.
$YTDLP_VERSION  = '2026.03.17'
# Pillow is used for the multimodal paste-corpus generator (resize +
# JPEG-recompress + base64-encode the embedded screenshots). Pinned to
# a recent stable; bump at release-prep time after testing.
$PILLOW_VERSION = '10.4.0'
# Official Model Context Protocol Python SDK for the stdio MCP server.
# Also pinned in requirements.txt for dev installs and docs.
$MCP_VERSION    = '1.27.1'
# Windows Credential Manager wrapper for Anthropic API key storage.
# Also pinned in requirements.txt for dev installs and docs.
$KEYRING_VERSION = '25.7.0'

# ---- Hash verification --------------------------------------------------
# Direct-download SHA256s are locked as of v2.0. When bumping Python,
# ffmpeg, or get-pip.py, run build.ps1 once, verify the new artifact source,
# paste the new hash here, and rebuild. Subsequent builds fail with
# "SHA256 mismatch" if anything changes; Confirm-Hash deletes the bad cached
# file so a re-run pulls fresh.
$PYTHON_SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
$FFMPEG_SHA256 = "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541"
$GETPIP_SHA256 = "66904bccb878e363db6236ea900e6935e507dcb887e9f178f6212edfe7f46a76"

# ---- Helpers ------------------------------------------------------------
function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Find-Iscc {
    $candidates = @(
        (Join-Path ([Environment]::GetEnvironmentVariable('ProgramFiles(x86)')) 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "ISCC.exe not found. Install Inno Setup 6 from https://jrsoftware.org/isdl.php"
}

function Get-CachedFile($url, $dest) {
    if (Test-Path $dest) {
        Write-Host "    cached: $(Split-Path -Leaf $dest)"
        return
    }
    Write-Host "    downloading $(Split-Path -Leaf $dest) ..."
    $tmp = "$dest.tmp"
    try {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        Move-Item -Force $tmp $dest
    } catch {
        if (Test-Path $tmp) { Remove-Item -Force $tmp }
        throw
    }
}

function Confirm-Hash($path, $expected, $label) {
    $actual = (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToLower()
    if (-not $expected) {
        Write-Warning "    $label has no locked SHA256. Computed: $actual"
        Write-Warning "    Lock it by setting the matching `$..._SHA256 in build.ps1, then rebuild."
        return
    }
    if ($actual -ne $expected.ToLower()) {
        # Remove the bad cache so a re-run downloads fresh, in case the
        # corruption was transient. Don't ship a mismatched artifact.
        Remove-Item -Force -ErrorAction SilentlyContinue $path
        throw "$label SHA256 mismatch.`nExpected: $expected`nActual:   $actual"
    }
    Write-Host "    $label hash OK"
}

# ---- Optional clean -----------------------------------------------------
if ($Clean) {
    Write-Step 'Cleaning build/ and staging/'
    if (Test-Path $StagingDir) { Remove-Item -Recurse -Force $StagingDir }
    if (Test-Path $BuildDir)   { Remove-Item -Recurse -Force $BuildDir }
}

New-Item -ItemType Directory -Force -Path $CacheDir, $BuildDir | Out-Null

# ---- Sanity checks ------------------------------------------------------
if (-not (Test-Path $IconSrc)) {
    throw "Missing $IconSrc -- regenerate from extension\icons\icon-128-light.png"
}
foreach ($f in @('server.py','yt_extract.py','topics.json')) {
    if (-not (Test-Path (Join-Path $RepoRoot $f))) {
        throw "Missing $f at repo root"
    }
}
if (-not (Test-Path (Join-Path $RepoRoot 'skills\yoink\SKILL.md'))) {
    throw "Missing skills\yoink\SKILL.md at repo root"
}

# ---- 1. Download dependencies ------------------------------------------
Write-Step 'Fetching dependencies'
$pythonZip = Join-Path $CacheDir "python-$PYTHON_VERSION-embed-amd64.zip"
$ffmpegZip = Join-Path $CacheDir 'ffmpeg-release-essentials.zip'
$getPipPy  = Join-Path $CacheDir 'get-pip.py'

Get-CachedFile $PYTHON_URL $pythonZip
Confirm-Hash $pythonZip $PYTHON_SHA256 'Python embeddable'
Get-CachedFile $FFMPEG_URL $ffmpegZip
Confirm-Hash $ffmpegZip $FFMPEG_SHA256 'ffmpeg'
Get-CachedFile $GETPIP_URL $getPipPy
Confirm-Hash $getPipPy $GETPIP_SHA256 'get-pip.py'

# ---- 2. Stage -----------------------------------------------------------
Write-Step 'Staging'
if (Test-Path $StagingDir) { Remove-Item -Recurse -Force $StagingDir }
New-Item -ItemType Directory -Force -Path $StagingDir, "$StagingDir\python", "$StagingDir\bin" | Out-Null

# 2a. Extract Python embeddable
Write-Host '    extracting Python embeddable...'
Expand-Archive -Path $pythonZip -DestinationPath "$StagingDir\python" -Force

# 2b. Enable site-packages -- embeddable distributions ship with the
#     `import site` line commented out, which prevents Lib\site-packages
#     from being on sys.path. Uncomment it.
$pthFile = Get-ChildItem -Path "$StagingDir\python" -Filter '*._pth' | Select-Object -First 1
if (-not $pthFile) { throw 'Embeddable archive missing python*._pth' }
$pthContent = Get-Content -Raw $pthFile.FullName
$pthContent = $pthContent -replace '#\s*import\s+site', 'import site'
# Encode as ASCII (no BOM) -- the embeddable launcher reads _pth as bytes
# and a UTF-16 / UTF-8 BOM here will break sys.path setup.
[System.IO.File]::WriteAllText($pthFile.FullName, $pthContent, [System.Text.Encoding]::ASCII)

# 2c. Bootstrap pip into the embeddable
Write-Host '    bootstrapping pip in embeddable Python...'
$embedPython = "$StagingDir\python\python.exe"
& $embedPython $getPipPy --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw 'pip bootstrap failed' }

# 2d. Install yt-dlp + Pillow + MCP + keyring at pinned versions. Pip's hash-locking would
#     require a requirements file with --require-hashes; for v2 we accept
#     the trust-pip-itself model since the version pins are the
#     load-bearing part (a compromised release on PyPI affects everyone,
#     not just us). Pillow drives the multimodal paste-corpus generator
#     (resize / re-encode / base64 screenshots for clipboard embedding).
#     MCP powers yoink_mcp.py for stdio agent integrations. keyring stores
#     the user's Anthropic API key in Windows Credential Manager.
Write-Host "    installing yt-dlp==$YTDLP_VERSION + Pillow==$PILLOW_VERSION + mcp==$MCP_VERSION + keyring==$KEYRING_VERSION..."
& $embedPython -m pip install --no-warn-script-location --no-compile `
    "yt-dlp==$YTDLP_VERSION" "Pillow==$PILLOW_VERSION" "mcp==$MCP_VERSION" "keyring==$KEYRING_VERSION"
if ($LASTEXITCODE -ne 0) { throw 'pip install (yt-dlp + Pillow + MCP + keyring) failed' }

# 2e. Trim dev-only and build-time files we don't need at runtime.
# distutils-precedence.pth is dropped by setuptools and tries to import
# `_distutils_hack` at every Python startup. We strip setuptools above, so
# the .pth file would print a noisy ModuleNotFoundError warning on every
# server launch -- delete it too.
Write-Host '    trimming embeddable...'
$stripGlobs = @(
    "$StagingDir\python\Lib\site-packages\pip*",
    "$StagingDir\python\Lib\site-packages\setuptools*",
    "$StagingDir\python\Lib\site-packages\_distutils*",
    "$StagingDir\python\Lib\site-packages\distutils-precedence.pth",
    "$StagingDir\python\Lib\site-packages\wheel*",
    "$StagingDir\python\Lib\site-packages\__pycache__"
)
foreach ($g in $stripGlobs) {
    Get-Item -ErrorAction SilentlyContinue $g | ForEach-Object {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $_.FullName
    }
}
# Strip any stray .pyc caches generated by pip's bootstrap.
Get-ChildItem -Path "$StagingDir\python" -Filter '__pycache__' -Recurse -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $_.FullName }

# 2f. ffmpeg -- pull just ffmpeg.exe (and ffprobe.exe if present)
Write-Host '    extracting ffmpeg...'
$ffmpegTmp = Join-Path $BuildDir '_ffmpeg_tmp'
if (Test-Path $ffmpegTmp) { Remove-Item -Recurse -Force $ffmpegTmp }
Expand-Archive -Path $ffmpegZip -DestinationPath $ffmpegTmp -Force
$ffmpegExe = Get-ChildItem -Path $ffmpegTmp -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
if (-not $ffmpegExe) { throw 'ffmpeg.exe not found inside the gyan.dev archive' }
Copy-Item $ffmpegExe.FullName "$StagingDir\bin\ffmpeg.exe" -Force
$ffprobeExe = Get-ChildItem -Path $ffmpegTmp -Recurse -Filter 'ffprobe.exe' | Select-Object -First 1
if ($ffprobeExe) {
    Copy-Item $ffprobeExe.FullName "$StagingDir\bin\ffprobe.exe" -Force
}
Remove-Item -Recurse -Force $ffmpegTmp

# 2g. Server source + helpers + icon
Write-Host '    copying server source + templates...'
Copy-Item (Join-Path $RepoRoot 'server.py')      $StagingDir -Force
Copy-Item (Join-Path $RepoRoot 'yoink_mcp.py')   $StagingDir -Force
Copy-Item (Join-Path $RepoRoot 'yoink_mcp_tools.py') $StagingDir -Force
Copy-Item (Join-Path $RepoRoot 'requirements.txt') $StagingDir -Force
Copy-Item (Join-Path $RepoRoot 'yt_extract.py')  $StagingDir -Force
Copy-Item (Join-Path $RepoRoot 'topics.json')    $StagingDir -Force
Copy-Item (Join-Path $TemplatesDir 'stop-server.bat') $StagingDir -Force
Copy-Item (Join-Path $TemplatesDir 'stop-server.ps1') $StagingDir -Force
Copy-Item $IconSrc (Join-Path $StagingDir 'yoink.ico') -Force
Copy-Item (Join-Path $RepoRoot 'skills') (Join-Path $StagingDir 'skills') -Recurse -Force

# ---- 3. Compile installer ----------------------------------------------
Write-Step 'Compiling installer'
$iscc = Find-Iscc
Write-Host "    using $iscc"
& $iscc /Q (Join-Path $InstallerDir 'yoink.iss')
if ($LASTEXITCODE -ne 0) { throw 'ISCC compilation failed' }

$exe = Join-Path $BuildDir "Yoink-Setup-$VERSION.exe"
if (-not (Test-Path $exe)) { throw "ISCC reported success but $exe is missing" }

$sizeMb = (Get-Item $exe).Length / 1MB
Write-Host ''
Write-Host ("Built {0} ({1:N1} MB)" -f $exe, $sizeMb) -ForegroundColor Green
