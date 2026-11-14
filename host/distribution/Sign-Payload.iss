; Use the same Inno SignTool substitution rules for payload EXEs and Setup.
[Setup]
AppName=RMLink signing
AppVersion=1
DefaultDirName={tmp}\RMLink-signing
OutputBaseFilename=signing-helper
Uninstallable=no
CreateAppDir=no
SignTool=publisher

[Files]
Source: "{#SignFile}"; DestDir: "{tmp}"; Flags: sign
