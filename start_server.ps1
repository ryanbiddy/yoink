# Launch server.py in the background with no console window.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSCommandPath

# Prefer the interpreter bundled with the installed product. The shipped
# Start Menu shortcut and HKCU\Run autostart key both call
# {app}\python\pythonw.exe directly; this manual-start script must match so
# it doesn't silently fail on a machine with no system Python on PATH.
$bundled = Join-Path $here "python\pythonw.exe"
if (Test-Path $bundled) {
    $py = $bundled
} else {
    Write-Warning "Bundled python\pythonw.exe not found; falling back to system 'pythonw' on PATH."
    $py = "pythonw"
}

Start-Process -FilePath $py -ArgumentList "`"$here\server.py`"" -WindowStyle Hidden
Write-Host "Yoink server launched. Logs: $here\server.log"
