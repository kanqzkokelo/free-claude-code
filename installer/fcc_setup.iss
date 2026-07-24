; Free Claude Code - Windows Standalone Installer
; Inno Setup Script (requires Inno Setup 6)
; Build: iscc fcc_setup.iss

#define MyAppName "Free Claude Code"
#define MyAppShortName "FCC"
#define MyAppVersion "1.2.41"
#define MyAppPublisher "Free Claude Code"
#define MyAppURL "https://github.com/Alishahryar1/free-claude-code"
#define SourceDir ".."

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={commonpf}\{#MyAppShortName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=.\output
OutputBaseFilename=FreeClaudeCode-Setup-{#MyAppVersion}
SetupIconFile=.\assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
DisableWelcomePage=no
SetupLogging=yes
CloseApplications=no
RestartApplications=no
ShowLanguageDialog=no
UninstallDisplayIcon={app}\assets\icon.ico
UninstallDisplayName={#MyAppName}
UsedUserAreasWarning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nThe proxy will auto-start when you log in so you can use Claude Code with free models in any terminal.%n%nConfigure API keys and models at http://localhost:8082/admin after installation.

[Types]
Name: "full"; Description: "Full installation"

[Components]
Name: "core"; Description: "Free Claude Code Proxy"; Types: full; Flags: fixed
Name: "desktop_shortcut"; Description: "Desktop shortcut to Admin UI"; Types: full; Flags: disablenouninstallwarning

[Files]
; ---- Root-level files ----
Source: "{#SourceDir}\server.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "{#SourceDir}\pyproject.toml"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "{#SourceDir}\uv.lock"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "{#SourceDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\AGENTS.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\CLAUDE.md"; DestDir: "{app}"; Flags: ignoreversion

; ---- Python packages (recursive, exclude caches) ----
Source: "{#SourceDir}\api\*"; DestDir: "{app}\src\api"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__*,*.pyc,*.egg-info*,.venv*"
Source: "{#SourceDir}\cli\*"; DestDir: "{app}\src\cli"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__*,*.pyc,*.egg-info*,.venv*"
Source: "{#SourceDir}\config\*"; DestDir: "{app}\src\config"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__*,*.pyc,*.egg-info*,.venv*"
Source: "{#SourceDir}\core\*"; DestDir: "{app}\src\core"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__*,*.pyc,*.egg-info*,.venv*"
Source: "{#SourceDir}\messaging\*"; DestDir: "{app}\src\messaging"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__*,*.pyc,*.egg-info*,.venv*"
Source: "{#SourceDir}\providers\*"; DestDir: "{app}\src\providers"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__*,*.pyc,*.egg-info*,.venv*"

; ---- Assets ----
Source: ".\assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "{#SourceDir}\assets\*.png"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "{#SourceDir}\assets\*.mmd"; DestDir: "{app}\assets"; Flags: ignoreversion

; ---- Bundled uv ----
Source: ".\tools\uv.exe"; DestDir: "{app}\bin"; Flags: ignoreversion

; ---- Installer scripts and config ----
Source: ".\install_config.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: ".\uninstall_config.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: ".\start_fcc_admin.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: ".\preconfigured.env"; DestDir: "{app}"; DestName: ".env"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"

[Icons]
Name: "{group}\Free Claude Code Admin UI"; Filename: "{app}\start_fcc_admin.bat"; WorkingDir: "{app}"; Components: core
Name: "{group}\Free Claude Code (Logs Folder)"; Filename: "{app}\logs"; Components: core
Name: "{group}\Uninstall Free Claude Code"; Filename: "{uninstallexe}"
Name: "{commondesktop}\Free Claude Code Admin UI"; Filename: "{app}\start_fcc_admin.bat"; WorkingDir: "{app}"; Tasks: desktop_shortcut

[Tasks]
Name: "desktop_shortcut"; Description: "Create desktop shortcut to Admin UI"; Components: desktop_shortcut

[Run]
; Post-install script handles: Python install, venv, deps, Claude CLI, env vars, auto-start
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\install_config.ps1"" -InstallDir ""{app}"""; StatusMsg: "Configuring Free Claude Code (Python, deps, env vars, auto-start)..."; Flags: runhidden; Components: core

; Open admin UI after installation
Filename: "{app}\start_fcc_admin.bat"; Description: "Open Free Claude Code Admin UI"; Flags: postinstall nowait skipifsilent shellexec; Components: core

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\uninstall_config.ps1"" -InstallDir ""{app}"""; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\src\*.egg-info"

[Code]

var
  NodeJsInstalled: Boolean;

function InitializeSetup: Boolean;
var
  ResultCode: Integer;
begin
  NodeJsInstalled := False;
  Result := True;

  { Check if Node.js is installed }
  if ShellExec('open', 'cmd.exe', '/C "node --version > nul 2>&1"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    NodeJsInstalled := (ResultCode = 0);
  end;
end;
