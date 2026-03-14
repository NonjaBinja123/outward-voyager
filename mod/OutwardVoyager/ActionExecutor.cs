using System.Text.Json;
using System.Text.Json.Serialization;

namespace OutwardVoyager;

/// <summary>
/// Receives commands from the Python agent and executes them in-game.
/// Commands arrive as JSON: { "action": "...", "params": { ... } }
/// </summary>
public class ActionExecutor
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public void HandleCommand(string json)
    {
        AgentCommand? cmd;
        try
        {
            cmd = JsonSerializer.Deserialize<AgentCommand>(json, JsonOpts);
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"Bad command JSON: {ex.Message}");
            return;
        }

        if (cmd is null) return;
        Plugin.Log.LogInfo($"Command received: {cmd.Action}");

        switch (cmd.Action)
        {
            case "get_state":
                SendState();
                break;
            case "say":
                SayInChat(cmd.Params.GetValueOrDefault("message") as string ?? "");
                break;
            case "move":
                MovePlayer(cmd.Params);
                break;
            default:
                Plugin.Log.LogWarning($"Unknown action: {cmd.Action}");
                SendError($"unknown action: {cmd.Action}");
                break;
        }
    }

    private void SendState()
    {
        var state = Plugin.StateReader!.ReadCurrentState();
        _ = Plugin.WsServer!.SendAsync(state);
    }

    private void SayInChat(string message)
    {
        ChatHook.SendMessage(message);
        _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "say", success = true });
    }

    private void MovePlayer(Dictionary<string, object?> p)
    {
        // TODO: Implement movement via game API post interop-gen
        Plugin.Log.LogInfo($"[MOVE] params={JsonSerializer.Serialize(p)}");
        _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "move", success = false, reason = "not_implemented_yet" });
    }

    private void SendError(string reason)
    {
        _ = Plugin.WsServer!.SendAsync(new { type = "error", reason });
    }
}

public class AgentCommand
{
    [JsonPropertyName("action")] public string Action { get; set; } = "";
    [JsonPropertyName("params")] public Dictionary<string, object?> Params { get; set; } = new();
}
