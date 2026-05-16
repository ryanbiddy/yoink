; Yoink -- Inno Setup script.
;
; Built by ../build.ps1, which stages all files under installer\staging\
; before invoking ISCC against this script. ISCC writes the final
; Yoink-Setup-<version>.exe into ../build/.
;
; Layout of the installed product (under {app} = %LOCALAPPDATA%\Yoink):
;   python\         Python 3.11 embeddable + Lib\site-packages\yt_dlp
;   bin\            ffmpeg.exe + ffprobe.exe (PATH-prepended by server.py)
;   server.py       The local helper server. pythonw.exe runs it (no console).
;   yoink_mcp.py    MCP stdio entry point for agent clients.
;   yt_extract.py   Helper module imported by server.py.
;   topics.json     Topic-folder routing rules.
;   skills\yoink\   Yoink Operator Skill + copyable system prompt.
;   stop-server.bat Stops the server via the PID file written at startup.
;   yoink.ico       Used for shortcuts and the uninstaller.

#define AppName       "Yoink"
#define AppVersion    "2.0.0"
#define AppPublisher  "ReplayRyan"
#define AppURL        "https://ryanbiddy.com/yoink"

[Setup]
; Stable AppId -- keep this fixed across releases so upgrades replace cleanly.
AppId={{2A91D3F7-7E9B-4F3D-8EAD-1F2C9F2F8AAB}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#AppVersion}.0
DefaultDirName={localappdata}\Yoink
DefaultGroupName=Yoink
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\build
OutputBaseFilename=Yoink-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=yoink.ico
UninstallDisplayIcon={app}\yoink.ico
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
ChangesEnvironment=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Python embeddable distribution (already includes pythonw.exe + python.exe
; + the stdlib zip). After staging, Lib\site-packages contains yt_dlp.
Source: "staging\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion createallsubdirs

; Bundled binaries -- prepended to PATH at runtime by server.py.
Source: "staging\bin\*"; DestDir: "{app}\bin"; Flags: recursesubdirs ignoreversion

; Server source.
Source: "staging\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\yoink_mcp.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\yoink_mcp_tools.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\yt_extract.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\topics.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\skills\*"; DestDir: "{app}\skills"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "staging\stop-server.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\stop-server.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\yoink.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Yoink Server"; \
  Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\server.py"""; \
  WorkingDir: "{app}"; \
  IconFilename: "{app}\yoink.ico"; \
  Comment: "Start the Yoink helper server"

Name: "{group}\Stop Yoink Server"; \
  Filename: "{app}\stop-server.bat"; \
  WorkingDir: "{app}"; \
  IconFilename: "{app}\yoink.ico"; \
  Comment: "Stop the Yoink helper server"

Name: "{group}\Yoink folder"; \
  Filename: "{app}"; \
  IconFilename: "{app}\yoink.ico"; \
  Comment: "Open the Yoink install folder"

Name: "{group}\Uninstall Yoink"; \
  Filename: "{uninstallexe}"

[Registry]
; Auto-start the helper on every Windows login. uninsdeletevalue removes the
; entry on uninstall so we don't leave dead Run keys behind.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "Yoink"; \
  ValueData: """{app}\python\pythonw.exe"" ""{app}\server.py"""; \
  Flags: uninsdeletevalue

[Run]
; "Launch Yoink Server now" checkbox on the finish page (default checked).
Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\server.py"""; \
  WorkingDir: "{app}"; \
  Description: "Launch Yoink Server now"; \
  Flags: postinstall nowait skipifsilent

[UninstallRun]
; Stop a running server before file removal so unins doesn't fail on locked
; site-packages files. waituntilterminated gives the process time to exit.
Filename: "{app}\stop-server.bat"; \
  WorkingDir: "{app}"; \
  Flags: runhidden waituntilterminated; \
  RunOnceId: "StopYoink"

[UninstallDelete]
; Pip and the running Python create files we didn't ship (.pyc caches, the
; PID file, the live log). Sweep the whole install dir on uninstall.
Type: files; Name: "{app}\server.log"
Type: files; Name: "{app}\server.pid"
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"
Type: filesandordirs; Name: "{app}\python\Lib\site-packages"
Type: filesandordirs; Name: "{app}\python\__pycache__"
