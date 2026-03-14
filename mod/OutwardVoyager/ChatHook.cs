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
    /// Sends a message to in-game chat as the agent.
    /// TODO: confirm exact IL2CPP interop signature for ChatManager.SendChatMessage.
    /// For now uses OnReceiveChatMessage to inject a message directly into the chat UI.
    /// </summary>
    public static void SendMessage(string text)
    {
        try
        {
            var chatMgr = ChatManager.Instance;
            if (chatMgr == null) return;
            // Inject by calling receive handler directly — shows in local chat
            chatMgr.OnReceiveChatMessage("Voyager", text);
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"ChatHook.SendMessage error: {ex.Message}");
        }
    }

    [HarmonyPatch(typeof(ChatManager), nameof(ChatManager.OnReceiveChatMessage))]
    [HarmonyPostfix]
    private static void OnReceiveChatMessage_Postfix(string _charUID, string _message)
    {
        // Don't echo the agent's own messages back
        if (_charUID == "Voyager") return;

        Plugin.Log.LogInfo($"[Chat] {_charUID}: {_message}");

        _ = Plugin.WsServer?.SendAsync(new
        {
            type = "chat",
            player = _charUID,
            message = _message,
        });
    }
}
