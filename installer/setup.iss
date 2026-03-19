; Inno Setup 6 script — Outward Voyager installer
; TODO (future): GPU detection wizard page (CUDA vs CPU-only Ollama build)
; TODO (future): API key wizard page (Anthropic / OpenAI / Gemini key input + validation)

#define AppName    "Outward Voyager"
#define AppVersion "1.0.0"
#define AppPublisher "Josh / Outward Voyager Project"
#define AppExeName "VoyagerLauncher.exe"

[Setup]
AppId={{A7F3C2D1-84BE-4E10-9B6A-0F2E3D5C8A91}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\OutwardVoyager
DefaultGroupName={#AppName}
OutputBaseFilename=OutwardVoyager_Setup_{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0.17763
; Require at least Windows 10 1809 (for .NET 6 support)

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Launcher executable (built with dotnet publish --self-contained)
Source: "{src}\VoyagerLauncher\publish\VoyagerLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion

; Default config — written/overwritten by [Code] section after install
; (Included here so the file exists even if CurStepChanged fails)
Source: "{src}\VoyagerLauncher\voyager_config.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

; Bundled Python runtime (CPython 3.11 embeddable or full install)
Source: "{src}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs

; Agent source tree
Source: "{src}\agent\*"; DestDir: "{app}\agent"; Flags: ignoreversion recursesubdirs createallsubdirs

; Dashboard source tree
Source: "{src}\dashboard\*"; DestDir: "{app}\dashboard"; Flags: ignoreversion recursesubdirs createallsubdirs

; BepInEx framework
Source: "{src}\BepInEx\*"; DestDir: "{code:GetOutwardDir}\BepInEx"; Flags: ignoreversion recursesubdirs createallsubdirs

; Outward Voyager mod DLL + deps
Source: "{src}\mod\OutwardAdapter\bin\Release\netstandard2.0\OutwardAdapter.dll";  DestDir: "{code:GetOutwardDir}\BepInEx\plugins\OutwardVoyager"; Flags: ignoreversion
Source: "{src}\mod\OutwardAdapter\bin\Release\netstandard2.0\VoyagerBridge.dll";   DestDir: "{code:GetOutwardDir}\BepInEx\plugins\OutwardVoyager"; Flags: ignoreversion

[Icons]
; Desktop shortcut
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
; Start Menu entry
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
; Optionally launch the tray app after install completes
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

; -----------------------------------------------------------------------
; Pascal code section
; -----------------------------------------------------------------------
[Code]

var
  OutwardDir: String;
  OutwardDirPage: TInputDirWizardPage;

{ Return the discovered (or user-supplied) Outward install dir. }
{ Used by [Files] section via {code:GetOutwardDir}. }
function GetOutwardDir(Param: String): String;
begin
  Result := OutwardDir;
end;

{ Read a single value from a .vdf text file (Valve KeyValues format).
  Looks for:  "key"    "value"
  Returns '' if not found. }
function ReadVdfValue(const FilePath, Key: String): String;
var
  Lines: TArrayOfString;
  I: Integer;
  Line, TrimmedKey: String;
  P: Integer;
begin
  Result := '';
  TrimmedKey := LowerCase('"' + Key + '"');
  if not LoadStringsFromFile(FilePath, Lines) then Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Lowercase(Trim(Lines[I]));
    if Pos(TrimmedKey, Line) > 0 then
    begin
      P := Pos(TrimmedKey, Line) + Length(TrimmedKey);
      Line := Trim(Copy(Lines[I], P + (Pos(TrimmedKey, Line) - 1) + Length(TrimmedKey) + 1, MaxInt));
      { Strip surrounding quotes }
      if (Length(Line) >= 2) and (Line[1] = '"') then
        Line := Copy(Line, 2, Length(Line) - 2);
      Result := Line;
      Exit;
    end;
  end;
end;

{ Try to locate the Outward Definitive Edition install directory via Steam. }
{ Returns '' if not found. }
function FindSteamOutwardPath(): String;
var
  SteamInstall, LibFolders, LibPath, GamePath: String;
  I: Integer;
  Lines: TArrayOfString;
  Line: String;
const
  OutwardAppId = '794260';
begin
  Result := '';

  { 1. Find Steam install path from registry }
  if not RegQueryStringValue(HKLM, 'SOFTWARE\Valve\Steam', 'InstallPath', SteamInstall) then
    if not RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath', SteamInstall) then
      Exit;

  { 2. Check default library first }
  GamePath := SteamInstall + '\steamapps\common\Outward Definitive Edition';
  if DirExists(GamePath) then
  begin
    Result := GamePath;
    Exit;
  end;

  { 3. Parse libraryfolders.vdf for additional library locations }
  LibFolders := SteamInstall + '\steamapps\libraryfolders.vdf';
  if not FileExists(LibFolders) then Exit;
  if not LoadStringsFromFile(LibFolders, Lines) then Exit;

  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Trim(Lines[I]);
    { Lines containing "path" key point to library roots }
    if Pos('"path"', LowerCase(Line)) > 0 then
    begin
      LibPath := ReadVdfValue(LibFolders, 'path');
      if LibPath <> '' then
      begin
        GamePath := LibPath + '\steamapps\common\Outward Definitive Edition';
        if DirExists(GamePath) then
        begin
          Result := GamePath;
          Exit;
        end;
      end;
    end;
  end;
end;

procedure InitializeWizard();
begin
  OutwardDir := FindSteamOutwardPath();

  { If Steam lookup failed, show a directory picker page so the user can
    point us at the Outward install manually. }
  if OutwardDir = '' then
  begin
    OutwardDirPage := CreateInputDirPage(
      wpSelectDir,
      'Locate Outward Definitive Edition',
      'The installer could not find Outward automatically.',
      'Please select your Outward Definitive Edition install folder ' +
      '(the folder that contains Outward.exe):',
      False, '');
    OutwardDirPage.Add('');
    OutwardDirPage.Values[0] := ExpandConstant('{pf}\Steam\steamapps\common\Outward Definitive Edition');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath, AgentDir, PythonExe: String;
  Lines: TArrayOfString;
begin
  { After all files have been copied, write a real voyager_config.json. }
  if CurStep = ssPostInstall then
  begin
    { Pick up user-supplied Outward dir if the page was shown }
    if OutwardDir = '' then
      OutwardDir := OutwardDirPage.Values[0];

    AgentDir  := ExpandConstant('{app}') + '\agent';
    PythonExe := ExpandConstant('{app}') + '\python\python.exe';
    ConfigPath := ExpandConstant('{app}') + '\voyager_config.json';

    SetArrayLength(Lines, 5);
    Lines[0] := '{';
    Lines[1] := '  "agent_dir": "' + StringChange(AgentDir, '\', '\\') + '",';
    Lines[2] := '  "dashboard_port": 8080,';
    Lines[3] := '  "python_exe": "' + StringChange(PythonExe, '\', '\\') + '"';
    Lines[4] := '}';
    SaveStringsToFile(ConfigPath, Lines, False);
  end;
end;
