#define MyAppName "GO2 Air Fleet Panel"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "GO2 Air Fleet Panel contributors"
#define MyAppURL "https://github.com/zalataraglados-prog/go2-air-fleet-panel"
#define MyAppExeName "GO2AirFleetPanel.exe"
#define ProjectRoot AddBackslash(SourcePath) + "..\.."
#define BundleRoot ProjectRoot + "\dist\GO2AirFleetPanel"

[Setup]
AppId={{44C27EB8-A227-4EBB-94BE-F9B949D82BA5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL + "/issues"}
AppUpdatesURL={#MyAppURL + "/releases"}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#ProjectRoot}\dist\installer
OutputBaseFilename=GO2-Air-Fleet-Panel-Setup-v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#BundleRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\.env.example"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "{#ProjectRoot}\.env.example"; DestDir: "{app}"; DestName: ".env"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
Source: "{#ProjectRoot}\config\config.example.yaml"; DestDir: "{app}\config"; DestName: "config.yaml"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
Source: "{#ProjectRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--open-browser"; WorkingDir: "{app}"
Name: "{group}\Open configuration folder"; Filename: "{sys}\explorer.exe"; Parameters: "{app}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--open-browser"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--open-browser"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
