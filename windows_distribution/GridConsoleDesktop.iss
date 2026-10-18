; Grid Console Desktop v0.3 React — UNSIGNED DEVELOPMENT ONLY
; The installed app bundles a compiled React UI and read-only Python localhost controller.
; It does not run trading, authenticate accounts, or start data collection.
#ifndef BuildDist
  #define BuildDist "..\dist\GridConsoleDesktop"
#endif
#ifndef ReleaseOutput
  #define ReleaseOutput "..\installer-dist"
#endif
[Setup]
AppId={{774154E5-975B-4097-9C7C-F2820B8D82C9}
AppName=Grid Console Desktop (Offline Development Preview)
AppVersion=0.3.0
AppPublisher=Grid Console Developer Preview
AppPublisherURL=https://github.com/Jambovisuaalit/Revolut-X-Crypto-Grid-Bot
DefaultDirName={localappdata}\Programs\GridConsoleDesktopPreview
DefaultGroupName=Grid Console Desktop DEV
OutputDir={#ReleaseOutput}
OutputBaseFilename=GridConsoleDesktop-UNSIGNED-DEV-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=no
UninstallDisplayIcon={app}\GridConsoleDesktop.exe
SetupLogging=yes
LicenseFile=UNSIGNED_DEV_NOTICE.txt
[Files]
Source: "{#BuildDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "UNSIGNED_DEV_NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\Grid Console Desktop — DEV"; Filename: "{app}\GridConsoleDesktop.exe"; Comment: "Read-only local Grid Console"
[Run]
; Deliberately do not launch installer post-setup or autostart at Windows logon.
