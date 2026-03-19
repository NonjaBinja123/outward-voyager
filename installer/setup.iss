; Inno Setup 6 script — Outward Voyager installer
; Wizard pages: GPU detection → API keys → Outward dir (fallback) → install

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
  { Outward install path — auto-detected or user-supplied }
  OutwardDir: String;
  OutwardDirPage: TInputDirWizardPage;

  { Phase 8 additions }
  GpuDetected: Boolean;            { True if nvidia-smi returned exit code 0 }
  ApiKeysPage: TInputQueryWizardPage;  { 3 optional API key fields }

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

{ Detect NVIDIA GPU by running nvidia-smi.
  Returns True if nvidia-smi exits with code 0 (GPU present + driver installed). }
function DetectNvidiaGpu(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  { Try nvidia-smi from PATH first, then from the usual System32 driver location }
  if Exec('nvidia-smi', '', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := (ResultCode = 0)
  else if Exec(ExpandConstant('{sys}') + '\nvidia-smi.exe', '', '', SW_HIDE,
               ewWaitUntilTerminated, ResultCode) then
    Result := (ResultCode = 0);
end;

procedure InitializeWizard();
var
  GpuInfoPage: TOutputMsgWizardPage;
  GpuMsg: String;
begin
  { ── GPU detection ──────────────────────────────────────────────────────── }
  GpuDetected := DetectNvidiaGpu();

  if GpuDetected then
    GpuMsg :=
      'NVIDIA GPU detected — Voyager will use GPU-accelerated Ollama.' + #13#10 +
      #13#10 +
      'After installation, open a terminal and run:' + #13#10 +
      '  ollama pull llama3.1:8b' + #13#10 +
      #13#10 +
      'This downloads ~5 GB once and then runs fully offline.' + #13#10 +
      'GPU mode runs at 30-80 tokens/sec — fast enough for real-time decisions.'
  else
    GpuMsg :=
      'No NVIDIA GPU detected — Voyager will use CPU-only Ollama.' + #13#10 +
      #13#10 +
      'After installation, open a terminal and run:' + #13#10 +
      '  ollama pull llama3.1:8b' + #13#10 +
      #13#10 +
      'CPU mode runs at 3-8 tokens/sec, which is slower but fully functional.' + #13#10 +
      'Adding an API key on the next page will give you cloud-speed responses.';

  GpuInfoPage := CreateOutputMsgWizardPage(
    wpSelectDir,
    'GPU Detection',
    'Checking your system for AI acceleration...',
    GpuMsg);

  { ── API Keys page ──────────────────────────────────────────────────────── }
  ApiKeysPage := CreateInputQueryPage(
    GpuInfoPage.ID,
    'API Keys (Optional)',
    'Enter your LLM provider API keys.',
    'Keys are saved to agent\.env on your machine only — never transmitted ' +
    'anywhere by the installer. Leave all fields blank to use local Ollama only.');
  ApiKeysPage.Add('Anthropic API key (claude-sonnet-4-6):', False);
  ApiKeysPage.Add('OpenAI API key (gpt-4o-mini):', False);
  ApiKeysPage.Add('Google API key (gemini-2.5-flash):', False);

  { ── Outward location fallback ──────────────────────────────────────────── }
  OutwardDir := FindSteamOutwardPath();
  if OutwardDir = '' then
  begin
    OutwardDirPage := CreateInputDirPage(
      ApiKeysPage.ID,
      'Locate Outward Definitive Edition',
      'The installer could not find Outward automatically.',
      'Please select your Outward Definitive Edition install folder ' +
      '(the folder that contains Outward.exe):',
      False, '');
    OutwardDirPage.Add('');
    OutwardDirPage.Values[0] :=
      ExpandConstant('{pf}') + '\Steam\steamapps\common\Outward Definitive Edition';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath, AgentDir, PythonExe, EnvPath: String;
  ConfigLines: TArrayOfString;
  EnvLines: TArrayOfString;
  EnvCount: Integer;
  AnthropicKey, OpenAiKey, GoogleKey: String;
begin
  { After all files have been copied, write config + .env files. }
  if CurStep = ssPostInstall then
  begin
    { Pick up user-supplied Outward dir if the manual-selection page was shown }
    if (OutwardDir = '') and (OutwardDirPage <> nil) then
      OutwardDir := OutwardDirPage.Values[0];

    AgentDir   := ExpandConstant('{app}') + '\agent';
    PythonExe  := ExpandConstant('{app}') + '\python\python.exe';
    ConfigPath := ExpandConstant('{app}') + '\voyager_config.json';

    SetArrayLength(ConfigLines, 5);
    ConfigLines[0] := '{';
    ConfigLines[1] := '  "agent_dir": "' + StringChange(AgentDir, '\', '\\') + '",';
    ConfigLines[2] := '  "dashboard_port": 8080,';
    ConfigLines[3] := '  "python_exe": "' + StringChange(PythonExe, '\', '\\') + '"';
    ConfigLines[4] := '}';
    SaveStringsToFile(ConfigPath, ConfigLines, False);

    { Write agent\.env with any API keys the user entered. }
    EnvPath := AgentDir + '\.env';
    SetArrayLength(EnvLines, 4);
    EnvLines[0] := '# Outward Voyager API Keys — generated by installer';
    EnvCount := 1;

    AnthropicKey := Trim(ApiKeysPage.Values[0]);
    OpenAiKey    := Trim(ApiKeysPage.Values[1]);
    GoogleKey    := Trim(ApiKeysPage.Values[2]);

    if AnthropicKey <> '' then
    begin
      EnvLines[EnvCount] := 'ANTHROPIC_API_KEY=' + AnthropicKey;
      EnvCount := EnvCount + 1;
    end;
    if OpenAiKey <> '' then
    begin
      EnvLines[EnvCount] := 'OPENAI_API_KEY=' + OpenAiKey;
      EnvCount := EnvCount + 1;
    end;
    if GoogleKey <> '' then
    begin
      EnvLines[EnvCount] := 'GOOGLE_API_KEY=' + GoogleKey;
      EnvCount := EnvCount + 1;
    end;

    SetArrayLength(EnvLines, EnvCount);
    SaveStringsToFile(EnvPath, EnvLines, False);
  end;
end;
