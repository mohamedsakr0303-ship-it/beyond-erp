; Windows installers (Inno Setup 6), built by .github/workflows/deploy.yml:
;   iscc /DOnline /DAppVersion=1.2.0 packaging\installer.iss   -> BeyondERP-Setup.exe          (opens the website in its own window)
;   iscc /DAppVersion=1.2.0 packaging\installer.iss            -> BeyondERP-Offline-Setup.exe  (the full app on this computer)
; Both install for the current user only (no administrator rights) with Start-menu and desktop shortcuts.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifdef Online
  #define Name "BeyondERP"
  #define Id "{{6B7E8F52-2C1D-4A7B-9C55-0B3E0D0E0002}"
  #define Out "BeyondERP-Setup"
#else
  #define Name "BeyondERP Offline"
  #define Id "{{6B7E8F52-2C1D-4A7B-9C55-0B3E0D0E0001}"
  #define Out "BeyondERP-Offline-Setup"
#endif

[Setup]
AppId={#Id}
AppName={#Name}
AppVersion={#AppVersion}
AppPublisher=BEYOND DESIGN
DefaultDirName={localappdata}\Programs\{#Name}
DefaultGroupName={#Name}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename={#Out}
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
#ifdef Online
Source: "..\dist-online\BeyondERP.exe"; DestDir: "{app}"; Flags: ignoreversion
#else
Source: "..\dist\BeyondERP\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

[InstallDelete]
; shortcuts left by versions that were called "Beyond ERP"
#ifdef Online
Type: files; Name: "{group}\Beyond ERP.lnk"
Type: files; Name: "{group}\Uninstall Beyond ERP.lnk"
Type: files; Name: "{userdesktop}\Beyond ERP.lnk"
#else
Type: files; Name: "{group}\Beyond ERP Offline.lnk"
Type: files; Name: "{group}\Uninstall Beyond ERP Offline.lnk"
Type: files; Name: "{userdesktop}\Beyond ERP Offline.lnk"
#endif

[Icons]
Name: "{group}\{#Name}"; Filename: "{app}\BeyondERP.exe"
Name: "{group}\Uninstall {#Name}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#Name}"; Filename: "{app}\BeyondERP.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BeyondERP.exe"; Description: "{cm:LaunchProgram,{#Name}}"; Flags: nowait postinstall skipifsilent
; after an "Update now" from inside the app (a silent install): open the new version straight away
Filename: "{app}\BeyondERP.exe"; Flags: nowait runasoriginaluser; Check: WizardSilent
