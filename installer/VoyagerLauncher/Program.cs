using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace VoyagerLauncher;

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

record VoyagerConfig(
    [property: JsonPropertyName("agent_dir")]    string AgentDir,
    [property: JsonPropertyName("dashboard_port")] int DashboardPort,
    [property: JsonPropertyName("python_exe")]   string PythonExe
)
{
    public static VoyagerConfig Load()
    {
        string configPath = Path.Combine(AppContext.BaseDirectory, "voyager_config.json");
        if (!File.Exists(configPath))
            throw new FileNotFoundException($"Config not found: {configPath}");

        string json = File.ReadAllText(configPath);
        return JsonSerializer.Deserialize<VoyagerConfig>(json)
            ?? throw new InvalidDataException("voyager_config.json is empty or invalid.");
    }

    public string AgentLogPath => Path.Combine(AgentDir, "voyager.log");
    public string MainPy       => Path.Combine(AgentDir, "main.py");
    public string DashboardPy  => Path.Combine(AgentDir, "..", "dashboard", "backend", "app.py");
}

// ---------------------------------------------------------------------------
// ProcessManager
// ---------------------------------------------------------------------------

sealed class ProcessManager : IDisposable
{
    public event Action<string, bool>? ProcessExited; // (name, wasExpected)

    private readonly VoyagerConfig _config;
    private Process? _agent;
    private Process? _dashboard;
    private bool _stopping;

    public ProcessManager(VoyagerConfig config) => _config = config;

    public void Start()
    {
        _stopping = false;
        _agent    = Launch("agent",     _config.MainPy,      "");
        _dashboard = Launch("dashboard", _config.DashboardPy, $"--port {_config.DashboardPort}");
    }

    public void Stop()
    {
        _stopping = true;
        Kill(_agent,     "agent");
        Kill(_dashboard, "dashboard");
        _agent = _dashboard = null;
    }

    private Process? Launch(string name, string script, string args)
    {
        if (!File.Exists(script))
        {
            MessageBox.Show($"Script not found: {script}", "Outward Voyager",
                MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return null;
        }

        var psi = new ProcessStartInfo
        {
            FileName               = _config.PythonExe,
            Arguments              = $"\"{script}\" {args}".Trim(),
            WorkingDirectory       = Path.GetDirectoryName(script) ?? AppContext.BaseDirectory,
            UseShellExecute        = false,
            CreateNoWindow         = true,
            RedirectStandardError  = true,
            RedirectStandardOutput = true,
        };

        var proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
        proc.Exited += (_, _) =>
        {
            if (!_stopping)
            {
                ProcessExited?.Invoke(name, false);
                // Auto-restart after 5 s
                Task.Delay(5000).ContinueWith(_ =>
                {
                    if (!_stopping)
                        Launch(name, script, args);
                });
            }
            else
            {
                ProcessExited?.Invoke(name, true);
            }
        };

        proc.Start();
        // Drain output so the pipe buffer never fills and blocks the child
        proc.BeginErrorReadLine();
        proc.BeginOutputReadLine();
        return proc;
    }

    private static void Kill(Process? proc, string name)
    {
        try { proc?.Kill(entireProcessTree: true); }
        catch { /* already dead */ }
    }

    public void Dispose() => Stop();
}

// ---------------------------------------------------------------------------
// LogWindow
// ---------------------------------------------------------------------------

sealed class LogWindow : Form
{
    private readonly RichTextBox _box;
    private readonly System.Windows.Forms.Timer _timer;
    private readonly string _logPath;

    public LogWindow(string logPath)
    {
        _logPath = logPath;
        Text     = "Outward Voyager — Agent Log";
        Width    = 800;
        Height   = 500;
        MinimumSize = new Size(400, 300);

        _box = new RichTextBox
        {
            Dock      = DockStyle.Fill,
            ReadOnly  = true,
            BackColor = Color.Black,
            ForeColor = Color.LightGreen,
            Font      = new Font("Consolas", 9f),
            ScrollBars = RichTextBoxScrollBars.Vertical,
        };
        Controls.Add(_box);

        _timer = new System.Windows.Forms.Timer { Interval = 2000 };
        _timer.Tick += (_, _) => Refresh();
        _timer.Start();

        FormClosing += (_, e) => { e.Cancel = true; Hide(); };
        Refresh();
    }

    private new void Refresh()
    {
        if (!File.Exists(_logPath))
        {
            _box.Text = $"Log file not found:\n{_logPath}";
            return;
        }

        try
        {
            // Read last 100 lines without locking the file
            string[] lines = ReadTail(_logPath, 100);
            string content = string.Join(Environment.NewLine, lines);
            if (_box.Text != content)
            {
                _box.Text = content;
                _box.SelectionStart = _box.Text.Length;
                _box.ScrollToCaret();
            }
        }
        catch (IOException) { /* log may be locked briefly */ }
    }

    private static string[] ReadTail(string path, int count)
    {
        using var fs     = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        using var reader = new StreamReader(fs);
        var lines = new List<string>();
        string? line;
        while ((line = reader.ReadLine()) != null)
            lines.Add(line);
        return lines.Count <= count ? lines.ToArray() : lines.Skip(lines.Count - count).ToArray();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) { _timer.Stop(); _timer.Dispose(); }
        base.Dispose(disposing);
    }
}

// ---------------------------------------------------------------------------
// TrayApp
// ---------------------------------------------------------------------------

sealed class TrayApp : ApplicationContext
{
    private readonly VoyagerConfig   _config;
    private readonly ProcessManager  _pm;
    private readonly NotifyIcon      _tray;
    private readonly LogWindow       _logWindow;

    public TrayApp(VoyagerConfig config)
    {
        _config    = config;
        _pm        = new ProcessManager(config);
        _logWindow = new LogWindow(config.AgentLogPath);

        // Build context menu
        var menu = new ContextMenuStrip();
        menu.Items.Add("Open Dashboard", null, OpenDashboard);
        menu.Items.Add("Open Log",       null, (_, _) => { _logWindow.Show(); _logWindow.BringToFront(); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Exit", null, Exit);

        _tray = new NotifyIcon
        {
            Icon             = SystemIcons.Application,
            Text             = "Outward Voyager",
            ContextMenuStrip = menu,
            Visible          = true,
        };
        _tray.DoubleClick += (_, _) => { _logWindow.Show(); _logWindow.BringToFront(); };

        _pm.ProcessExited += OnProcessExited;
        _pm.Start();

        _tray.ShowBalloonTip(3000, "Outward Voyager", "Agent and dashboard are starting…", ToolTipIcon.Info);
    }

    private void OpenDashboard(object? sender, EventArgs e)
    {
        string url = $"http://localhost:{_config.DashboardPort}";
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch (Exception ex) { MessageBox.Show($"Could not open browser:\n{ex.Message}"); }
    }

    private void OnProcessExited(string name, bool wasExpected)
    {
        if (!wasExpected)
            _tray.ShowBalloonTip(4000, "Outward Voyager",
                $"The {name} process stopped unexpectedly. Restarting in 5 s…", ToolTipIcon.Warning);
    }

    private void Exit(object? sender, EventArgs e)
    {
        _pm.Stop();
        _tray.Visible = false;
        _tray.Dispose();
        _logWindow.Dispose();
        ExitThread();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) { _pm.Dispose(); _logWindow.Dispose(); }
        base.Dispose(disposing);
    }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

static class Program
{
    [STAThread]
    static void Main()
    {
        ApplicationConfiguration.Initialize();

        VoyagerConfig config;
        try
        {
            config = VoyagerConfig.Load();
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                $"Failed to load voyager_config.json:\n\n{ex.Message}\n\n" +
                "Make sure voyager_config.json is in the same directory as VoyagerLauncher.exe.",
                "Outward Voyager — Config Error",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        Application.Run(new TrayApp(config));
    }
}
