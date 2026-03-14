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
    /// Tries multiple approaches since IL2CPP interop method availability varies.
    /// The log will show which approach worked so we can lock it in later.
    /// </summary>
    public static void SendMessage(string text)
    {
        var chatMgr = ChatManager.Instance;
        if (chatMgr == null)
        {
            Plugin.Log.LogWarning("[Chat] ChatManager.Instance is null.");
            return;
        }

        // Try each approach; the BepInEx log will show which one works.
        // ChatMessageReceived and SendChatMessage exist as native IL2CPP methods
        // but don't have C# interop wrappers, so we invoke via IL2CPP reflection.
        if (TryIl2CppInvoke(chatMgr, "ChatMessageReceived", text)) return;
        if (TryIl2CppInvoke(chatMgr, "SendChatMessage", text)) return;
        if (TryOnReceiveChatMessage(chatMgr, text)) return;

        Plugin.Log.LogWarning("[Chat] All chat display methods failed.");
    }

    private static bool TryIl2CppInvoke(ChatManager mgr, string methodName, string text)
    {
        try
        {
            // Use IL2CPP reflection to find and call methods the interop didn't wrap.
            var il2cppType = Il2CppInterop.Runtime.Il2CppType.From(typeof(ChatManager));
            // Search all methods on the IL2CPP type
            var methods = mgr.GetIl2CppType().GetMethods(
                Il2CppSystem.Reflection.BindingFlags.Public | Il2CppSystem.Reflection.BindingFlags.Instance);

            foreach (var m in methods)
            {
                if (m.Name != methodName) continue;
                var ps = m.GetParameters();
                if (ps.Count != 2) continue;

                m.Invoke(mgr, new Il2CppSystem.Object[]
                {
                    (Il2CppSystem.String)"Voyager",
                    (Il2CppSystem.String)text,
                });
                Plugin.Log.LogInfo($"[Chat] OK via IL2CPP invoke {methodName}: {text}");
                return true;
            }

            Plugin.Log.LogWarning($"[Chat] IL2CPP method '{methodName}' not found on ChatManager.");
            return false;
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Chat] IL2CPP invoke {methodName} failed: {ex.Message}");
            return false;
        }
    }

    private static bool TryOnReceiveChatMessage(ChatManager mgr, string text)
    {
        try
        {
            mgr.OnReceiveChatMessage("Voyager", text);
            Plugin.Log.LogInfo($"[Chat] Fallback via OnReceiveChatMessage: {text}");
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
