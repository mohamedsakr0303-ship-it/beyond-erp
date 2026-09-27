; Windows installer for the desktop app (Inno Setup 6). Built by .github/workflows/deploy.yml:
;   iscc /DAppVersion=1.2.0 packaging\installer.iss   (after pyinstaller packaging\beyond_erp.spec)
; Installs for the current user only (no administrator rights needed) with Start-menu and desktop shortcuts.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6B7E8F52-2C1D-4A7B-9C55-0B3E0D0E0001}
AppName=Beyond ERP
AppVersion={#AppVersion}
AppPublisher=BEYOND DESIGN
DefaultDirName={localappdata}\Programs\Beyond ERP
DefaultGroupName=Beyond ERP
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=BeyondERP-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\BeyondERP.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\BeyondERP\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Beyond ERP"; Filename: "{app}\BeyondERP.exe"
Name: "{group}\Uninstall Beyond ERP"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Beyond ERP"; Filename: "{app}\BeyondERP.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BeyondERP.exe"; Description: "{cm:LaunchProgram,Beyond ERP}"; Flags: nowait postinstall skipifsilent
