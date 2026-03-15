using HarmonyLib;

namespace OutwardVoyager;

/// <summary>
/// Patches ChatManager to intercept incoming player chat messages and relay
/// them to the Python agent via WebSocket.
///
/// Also provides SendMessage() so the agent can speak in chat.
/// </summary>
public static class ChatHook
{
    private static Harmony? _harmony;

    public static void Apply()
    {
        _harmony = new Harmony(MyPluginInfo.PLUGIN_GUID + ".chat");
        _harmony.PatchAll(typeof(ChatHook));
        Plugin.Log.LogInfo("ChatHook applied.");
    }

    public static void Remove() => _harmony?.UnpatchSelf();

    /// <summary>
    /// Sends a message to in-game chat as the agent via ChatManager.OnReceiveChatMessage.
    /// </summary>
    public static void SendMessage(string text)
    {
        // Must run on Unity main thread — queue it for StatePusher.Update()
        Plugin.MainThreadQueue.Enqueue(() =>
        {
            var chatMgr = ChatManager.Instance;
            if (chatMgr == null)
            {
                Plugin.Log.LogWarning("[Chat] ChatManager.Instance is null.");
                return;
            }
            TryOnReceiveChatMessage(chatMgr, text);
        });
    }

    private static bool TryOnReceiveChatMessage(ChatManager mgr, string text)
    {
        try
        {
            // Use the local player's UID so the game renders the message properly.
            // Prefix with "Voyager: " so it's visually distinct from player chat.
            var player = CharacterManager.Instance?.GetFirstLocalCharacter();
            string uid = player?.UID?.ToString() ?? "local";
            mgr.OnReceiveChatMessage(uid, "Voyager: " + text);
            Plugin.Log.LogInfo($"[Chat] Sent via OnReceiveChatMessage: {text}");
            return true;
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Chat] OnReceiveChatMessage failed: {ex.Message}");
            return false;
        }
    }

    [HarmonyPatch(typeof(ChatManager), nameof(ChatManager.OnReceiveChatMessage))]
    [HarmonyPostfix]
    private static void OnReceiveChatMessage_Postfix(string _charUID, string _message)
    {
        // Don't echo the agent's own messages back
        if (_message.StartsWith("Voyager: ")) return;

        Plugin.Log.LogInfo($"[Chat] {_charUID}: {_message}");

        _ = Plugin.WsServer?.SendAsync(new
        {
            type = "chat",
            player = _charUID,
            message = _message,
        });
    }
}
