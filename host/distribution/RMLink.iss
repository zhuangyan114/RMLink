; Build through scripts/Build-Installer.ps1, which validates and stages the payload.
#ifndef PayloadDirectory
  #error PayloadDirectory must be supplied by the installer builder
#endif
#ifndef AppVersion
  #error AppVersion must be supplied by the installer builder
#endif
#ifndef InstallerAppId
  #define InstallerAppId "{{E9B5FC27-7D26-4683-90D3-5387C315DFB0}"
#endif

[Setup]
AppId={#InstallerAppId}
AppName=RMLink
AppVersion={#AppVersion}
AppPublisher=RMLink contributors
AppPublisherURL=https://github.com/zhuangyan114/RMLink
AppSupportURL=https://github.com/zhuangyan114/RMLink/issues
DefaultDirName={%USERPROFILE}\.rmlink\app
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName=RMLink
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ChangesEnvironment=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\bin\rmlink.exe
UninstallDisplayName=RMLink {#AppVersion}
VersionInfoVersion={#AppVersion}.0
LicenseFile={#PayloadDirectory}\licenses\RMLink-LICENSE.txt
#ifdef PublisherSignCommand
SignTool=publisher
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[CustomMessages]
english.AddToPath=Add the rmlink command to my PATH
english.StartAtLogon=Start the background service when I sign in
english.DesktopIcon=Create a desktop shortcut
english.Launch=Open the RMLink console
english.Readme=RMLink instructions
english.PrepareError=Close the RMLink console and end active debug sessions, then try again.
english.ConfigureError=RMLink files were installed, but command or startup registration failed. Run Setup again to repair the installation.
chinesesimplified.AddToPath=将 rmlink 命令加入当前用户的 PATH
chinesesimplified.StartAtLogon=登录 Windows 时启动后台服务
chinesesimplified.DesktopIcon=创建桌面快捷方式
chinesesimplified.Launch=打开 RMLink 控制台
chinesesimplified.Readme=RMLink 使用说明
chinesesimplified.PrepareError=请先结束调试会话并关闭 RMLink 控制台，然后重试。
chinesesimplified.ConfigureError=程序文件已安装，但命令或登录自启设置失败。请重新运行安装器修复。

[Tasks]
Name: "addtopath"; Description: "{cm:AddToPath}"
Name: "startlogon"; Description: "{cm:StartAtLogon}"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[Files]
Source: "{#PayloadDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDirectory}\Manage-Installation.ps1"; Flags: dontcopy

[Icons]
Name: "{userprograms}\RMLink\RMLink"; Filename: "{app}\bin\rmlink.exe"; WorkingDir: "{app}"
Name: "{userprograms}\RMLink\{cm:Readme}"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\README.txt"""
Name: "{userdesktop}\RMLink"; Filename: "{app}\bin\rmlink.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\bin\rmlink.exe"; Description: "{cm:Launch}"; Flags: postinstall nowait skipifsilent

[Code]
function Lifecycle(const Action, Script, Extra: String): Boolean;
var
  ExitCode: Integer;
  ErrorPath, Parameters: String;
  ErrorText: AnsiString;
begin
  ErrorPath := ExpandConstant('{tmp}\rmlink-install-error.txt');
  DeleteFile(ErrorPath);
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + AddQuotes(Script) +
    ' -Action ' + Action + ' -InstallDirectory ' + AddQuotes(ExpandConstant('{app}')) +
    ' -ErrorFile ' + AddQuotes(ErrorPath) + Extra;
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Parameters, '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
  Result := Result and (ExitCode = 0);
  if not Result then begin
    if LoadStringFromFile(ErrorPath, ErrorText) then
      Log('RMLink installation helper: ' + UTF8Decode(ErrorText));
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ExtractTemporaryFile('Manage-Installation.ps1');
  if not Lifecycle('Prepare', ExpandConstant('{tmp}\Manage-Installation.ps1'), '') then
    Result := CustomMessage('PrepareError')
  else
    Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Extra: String;
begin
  if CurStep = ssPostInstall then begin
    Extra := ' -AddToPath off';
    if WizardIsTaskSelected('addtopath') then Extra := ' -AddToPath on';
    if WizardIsTaskSelected('startlogon') then Extra := Extra + ' -StartAtLogon on'
    else Extra := Extra + ' -StartAtLogon off';
    if not Lifecycle('Configure', ExpandConstant('{app}\Manage-Installation.ps1'), Extra) then
      RaiseException(CustomMessage('ConfigureError'));
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := Lifecycle('Prepare', ExpandConstant('{app}\Manage-Installation.ps1'), '');
  if not Result then
    SuppressibleMsgBox(CustomMessage('PrepareError'), mbError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    if not Lifecycle('Remove', ExpandConstant('{app}\Manage-Installation.ps1'), '') then
      RaiseException(CustomMessage('ConfigureError'));
  end;
end;
