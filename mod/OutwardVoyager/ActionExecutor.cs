using System.Text.Json;
using System.Text.Json.Serialization;
using UnityEngine;
using UnityEngine.SceneManagement;

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
            case "navigate_to":
                NavigateTo(cmd.Params);
                break;
            case "navigate_cancel":
                Plugin.NavController?.Cancel();
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "navigate_cancel", success = true });
                break;
            case "scan_nearby":
                ScanNearby(cmd.Params);
                break;
            case "interact":
                Interact(cmd.Params);
                break;
            case "take_item":
                TakeItem(cmd.Params);
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

    private void NavigateTo(Dictionary<string, object?> p)
    {
        try
        {
            float x = Convert.ToSingle(p.GetValueOrDefault("x") ?? 0f);
            float y = Convert.ToSingle(p.GetValueOrDefault("y") ?? 0f);
            float z = Convert.ToSingle(p.GetValueOrDefault("z") ?? 0f);
            bool run = p.GetValueOrDefault("run") is bool b && b;

            Plugin.NavController!.SetTarget(new Vector3(x, y, z), run);
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "navigate_to", success = true });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Nav] navigate_to error: {ex.Message}");
            SendError($"navigate_to failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Walks the active scene hierarchy and reports every GameObject within radius.
    /// Uses SceneManager.GetRootGameObjects → recursive child walk, which is
    /// guaranteed to work in IL2CPP (no FindObjectsOfType needed).
    /// </summary>
    private void ScanNearby(Dictionary<string, object?> p)
    {
        float radius = Convert.ToSingle(p.GetValueOrDefault("radius") ?? 30f);
        try
        {
            var playerChar = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (playerChar == null) { SendError("no player character"); return; }

            var playerPos = playerChar.transform.position;
            var scene = SceneManager.GetActiveScene();
            var roots = scene.GetRootGameObjects();

            var results = new List<object>();
            var seen = new HashSet<int>();
            int playerRootId = playerChar.transform.root.gameObject.GetInstanceID();

            foreach (var root in roots)
                WalkChildren(root.transform, playerPos, radius, results, seen, playerRootId);

            Plugin.Log.LogInfo($"[Scan] Found {results.Count} objects within {radius}u.");
            _ = Plugin.WsServer!.SendAsync(new
            {
                type = "scan_result",
                count = results.Count,
                radius,
                objects = results,
            });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Scan] error: {ex.Message}");
            SendError($"scan failed: {ex.Message}");
        }
    }

    private static void WalkChildren(Transform t, Vector3 playerPos, float radius,
        List<object> results, HashSet<int> seen, int playerRootId)
    {
        var go = t.gameObject;
        int id = go.GetInstanceID();

        if (!seen.Add(id)) return;
        if (id == playerRootId) return;

        var pos = t.position;
        var flat = pos - playerPos;
        flat.y = 0f;
        float dist = flat.magnitude;

        if (dist <= radius)
        {
            // Collect useful info about this object
            bool hasCharacter = go.GetComponent<Character>() != null;
            bool isDead = hasCharacter && go.GetComponent<Character>().IsDead;
            bool hasCollider = go.GetComponent<Collider>() != null;

            results.Add(new
            {
                name = go.name,
                x = pos.x, y = pos.y, z = pos.z,
                distance = (float)Math.Round(dist, 1),
                tag = go.tag,
                has_character = hasCharacter,
                is_dead = isDead,
                has_collider = hasCollider,
                active = go.activeInHierarchy,
            });
        }

        // Recurse into children
        for (int i = 0; i < t.childCount; i++)
            WalkChildren(t.GetChild(i), playerPos, radius, results, seen, playerRootId);
    }

    /// <summary>
    /// Trigger the basic interaction on the nearest Item within radius.
    /// Uses IL2CPP reflection to call "Pickup" at runtime (no interop wrapper exists).
    /// params: { "radius": float }
    /// </summary>
    private void Interact(Dictionary<string, object?> p)
    {
        float radius = Convert.ToSingle(p.GetValueOrDefault("radius") ?? 3f);
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no player character"); return; }

            var playerPos = character.transform.position;
            var colliders = Physics.OverlapSphere(playerPos, radius);

            Item? nearestItem = null;
            float nearestDist = float.MaxValue;

            foreach (var col in colliders)
            {
                if (col == null) continue;
                var item = col.GetComponentInParent<Item>();
                if (item == null) continue;
                float dist = (col.transform.position - playerPos).magnitude;
                if (dist < nearestDist) { nearestDist = dist; nearestItem = item; }
            }

            if (nearestItem == null)
            {
                Plugin.Log.LogInfo($"[Interact] No item within {radius}u.");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "interact", success = false, reason = "nothing_nearby" });
                return;
            }

            bool ok = TryInvokeNoArgMethod(nearestItem, "Pickup");
            Plugin.Log.LogInfo($"[Interact] Pickup {nearestItem.name}: {(ok ? "ok" : "no method")}");
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "interact", success = ok, target = nearestItem.name });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Interact] error: {ex.Message}");
            SendError($"interact failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Take an item by name (as returned by scan_nearby).
    /// Uses IL2CPP reflection to call "Pickup" at runtime.
    /// params: { "name": string, "id": string }
    /// </summary>
    private void TakeItem(Dictionary<string, object?> p)
    {
        string targetName = p.GetValueOrDefault("name") as string ?? "";
        string targetId   = p.GetValueOrDefault("id")   as string ?? "";
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no player character"); return; }

            var playerPos = character.transform.position;
            var colliders = Physics.OverlapSphere(playerPos, 10f);

            Item? found = null;
            float nearestDist = float.MaxValue;

            foreach (var col in colliders)
            {
                if (col == null) continue;
                var item = col.GetComponentInParent<Item>();
                if (item == null) continue;

                bool idMatch   = !string.IsNullOrEmpty(targetId)   && col.transform.root.gameObject.GetInstanceID().ToString() == targetId;
                bool nameMatch = !string.IsNullOrEmpty(targetName) && item.name.Contains(targetName, StringComparison.OrdinalIgnoreCase);

                if (!idMatch && !nameMatch) continue;

                float dist = (col.transform.position - playerPos).magnitude;
                if (dist < nearestDist) { nearestDist = dist; found = item; }
            }

            if (found == null)
            {
                Plugin.Log.LogInfo($"[TakeItem] '{targetName}' not found within 10u.");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "take_item", success = false, reason = "item_not_found" });
                return;
            }

            bool ok = TryInvokeNoArgMethod(found, "Pickup");
            Plugin.Log.LogInfo($"[TakeItem] Pickup {found.name}: {(ok ? "ok" : "no method")}");
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "take_item", success = ok, item = found.name });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[TakeItem] error: {ex.Message}");
            SendError($"take_item failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Invoke a zero-argument instance method via IL2CPP reflection.
    /// Returns true if the method was found and invoked.
    /// </summary>
    private static bool TryInvokeNoArgMethod(Il2CppSystem.Object obj, string methodName)
    {
        try
        {
            var methods = obj.GetIl2CppType().GetMethods(
                Il2CppSystem.Reflection.BindingFlags.Public |
                Il2CppSystem.Reflection.BindingFlags.Instance);
            foreach (var m in methods)
            {
                if (m.Name != methodName) continue;
                if (m.GetParameters().Count != 0) continue;
                m.Invoke(obj, null);
                return true;
            }
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Il2CppInvoke] {methodName} failed: {ex.Message}");
        }
        return false;
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
