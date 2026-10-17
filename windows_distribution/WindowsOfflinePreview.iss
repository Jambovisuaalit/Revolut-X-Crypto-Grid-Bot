; Grid Console Windows OFFLINE preview — UNSIGNED DEVELOPMENT INSTALLER.
; Signing deliberately excluded. No live/account executables.
#ifndef BuildDist
  #define BuildDist "..\dist\GridConsoleOfflinePreview"
#endif
#ifndef ReleaseOutput
  #define ReleaseOutput "..\installer-dist"
#endif
#ifndef BuildVersion
  #define BuildVersion "0.1.0"
#endif
[Setup]
AppId={{A42117AC-54AE-477F-8EA8-31D03F3A7051}
AppName=Grid Console Offline Preview (Development Only)
AppVersion={#BuildVersion}
AppPublisher=Grid Console Developer Preview
AppPublisherURL=https://github.com/Jambovisuaalit/Revolut-X-Crypto-Grid-Bot
DefaultDirName={localappdata}\Programs\GridConsoleOfflinePreview
DefaultGroupName=Grid Console Offline Preview DEV
OutputDir={#ReleaseOutput}
OutputBaseFilename=GridConsoleOfflinePreview-UNSIGNED-DEV-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=no
UninstallDisplayIcon={app}\GridConsoleOfflinePreview.exe
SetupLogging=yes
LicenseFile=UNSIGNED_DEV_NOTICE.txt
[Files]
Source: "{#BuildDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "UNSIGNED_DEV_NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\Grid Console Offline Preview — DEV"; Filename: "{app}\GridConsoleOfflinePreview.exe"; Comment: "No account connection or trading"
[Run]
; No automatic launch. User must explicitly open the offline preview.
