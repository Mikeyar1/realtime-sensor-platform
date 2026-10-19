; labreview_installer.iss
; Inno Setup script for LabReview
;
; Prerequisites:
;   1. Run PyInstaller first:
;        cd backend
;        uv run pyinstaller labreview.spec --clean --noconfirm
;   2. Install Inno Setup:
;        winget install JRSoftware.InnoSetup
;   3. Build this installer (from project root):
;        iscc labreview_installer.iss
;
; Output: dist\LabReview-Setup-<version>.exe

#define AppName      "LabReview"
#define AppVersion   "0.1.0"
#define AppPublisher "Your Lab"
#define AppExeName   "LabReview.exe"
#define SourceDir    "backend\dist\LabReview"

[Setup]
; Generate a fresh GUID for your app with: [guid]::NewGuid() in PowerShell
; Replace the placeholder below with your generated GUID (keep the braces)
AppId={{83eaa899-2565-4d93-91ea-c4837210de07}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppComments=Real-time human performance monitoring dashboard for VirTra simulation sessions.

; Where the app installs — user-space so no admin rights are ever needed
; Resolves to: C:\Users\<you>\AppData\Local\Programs\LabReview
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}

; Final installer .exe lands in dist\ at the project root
OutputDir=dist
OutputBaseFilename=LabReview-Setup-{#AppVersion}

; Compression
Compression=lzma2
SolidCompression=yes

; UI
WizardStyle=modern
SetupIconFile=

; Minimum Windows version: Windows 10
MinVersion=10.0

; Install without requiring admin rights — lets per-user paths (AppData, Documents) work correctly
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; --- PyInstaller output (the frozen app + all bundled files) ---
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; --- Editable config files ---
; Installed to %APPDATA%\LabReview so the user can edit them without admin rights.
; onlyifdoesntexist means updates won't clobber the operator's local edits.
Source: "backend\config.toml";           DestDir: "{userappdata}\{#AppName}";          Flags: onlyifdoesntexist
Source: "config\drill_registry.toml";    DestDir: "{userappdata}\{#AppName}\config";   Flags: onlyifdoesntexist
Source: "config\virtra_streams.toml";     DestDir: "{userappdata}\{#AppName}\config";   Flags: onlyifdoesntexist

[Dirs]
; Create the session data output folder so the app can write to it immediately
Name: "{userdocs}\{#AppName}\SessionData"

[Icons]
; Start Menu
Name: "{group}\{#AppName}";              Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}";   Filename: "{uninstallexe}"

; Desktop (opt-in task above)
Name: "{autodesktop}\{#AppName}";        Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch the app immediately after installation finishes
Filename: "{app}\{#AppExeName}"; \
    Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up any runtime-generated files inside the install folder on uninstall
Type: filesandordirs; Name: "{app}\__pycache__"
