using System.Net;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

namespace OutwardVoyager;

/// <summary>
/// Runs a simple WebSocket server on localhost so the Python agent can connect.
/// One client at a time; new connections replace the old one.
/// </summary>
public class WebSocketServer
{
    private readonly HttpListener _listener;
    private WebSocket? _client;
    private CancellationTokenSource _cts = new();
    private readonly SemaphoreSlim _sendLock = new(1, 1);

    public event Action<string>? OnMessageReceived;

    public WebSocketServer(int port)
    {
        _listener = new HttpListener();
        _listener.Prefixes.Add($"http://localhost:{port}/");
    }

    public async Task StartAsync()
    {
        _listener.Start();
        Plugin.Log.LogInfo("WebSocket server started.");

        while (!_cts.Token.IsCancellationRequested)
        {
            try
            {
                var ctx = await _listener.GetContextAsync().ConfigureAwait(false);
                if (ctx.Request.IsWebSocketRequest)
                {
                    // Drop previous client if any
                    _client?.Abort();
                    var wsCtx = await ctx.AcceptWebSocketAsync(null).ConfigureAwait(false);
                    _client = wsCtx.WebSocket;
                    Plugin.Log.LogInfo("Python agent connected.");
                    _ = ReceiveLoopAsync(_client, _cts.Token);
                }
                else
                {
                    ctx.Response.StatusCode = 400;
                    ctx.Response.Close();
                }
            }
            catch (Exception ex) when (!_cts.Token.IsCancellationRequested)
            {
                Plugin.Log.LogWarning($"WebSocket accept error: {ex.Message}");
            }
        }
    }

    private async Task ReceiveLoopAsync(WebSocket ws, CancellationToken ct)
    {
        var buf = new byte[64 * 1024];
        while (ws.State == WebSocketState.Open && !ct.IsCancellationRequested)
        {
            try
            {
                var result = await ws.ReceiveAsync(buf, ct).ConfigureAwait(false);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    Plugin.Log.LogInfo("Agent disconnected.");
                    await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", ct).ConfigureAwait(false);
                    break;
                }
                if (result.MessageType == WebSocketMessageType.Text)
                {
                    var msg = Encoding.UTF8.GetString(buf, 0, result.Count);
                    OnMessageReceived?.Invoke(msg);
                }
            }
            catch (Exception ex) when (!ct.IsCancellationRequested)
            {
                Plugin.Log.LogWarning($"Receive error: {ex.Message}");
                break;
            }
        }
    }

    /// <summary>Send a JSON-serializable object to the connected agent.</summary>
    public async Task SendAsync(object payload)
    {
        var ws = _client;
        if (ws is null || ws.State != WebSocketState.Open)
            return;
        var json = JsonSerializer.Serialize(payload);
        var bytes = Encoding.UTF8.GetBytes(json);
        await _sendLock.WaitAsync(_cts.Token).ConfigureAwait(false);
        try
        {
            if (ws.State == WebSocketState.Open)
                await ws.SendAsync(bytes, WebSocketMessageType.Text, true, _cts.Token).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"Send error: {ex.Message}");
            // Abort so Python detects the disconnect and reconnects cleanly.
            ws.Abort();
            _client = null;
        }
        finally
        {
            _sendLock.Release();
        }
    }

    public void Stop()
    {
        _cts.Cancel();
        _client?.Abort();
        _listener.Stop();
    }
}
