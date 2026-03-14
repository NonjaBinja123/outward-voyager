using BepInEx;
using BepInEx.Unity.IL2CPP;
using BepInEx.Unity.IL2CPP.UnityEngine;
using BepInEx.Logging;

namespace OutwardVoyager;

[BepInPlugin(MyPluginInfo.PLUGIN_GUID, MyPluginInfo.PLUGIN_NAME, MyPluginInfo.PLUGIN_VERSION)]
public class Plugin : BasePlugin
{
    internal static new ManualLogSource Log = null!;
    internal static WebSocketServer? WsServer;
    internal static GameStateReader? StateReader;
    internal static ActionExecutor? Executor;

    public override void Load()
    {
        Log = base.Log;
        Log.LogInfo($"{MyPluginInfo.PLUGIN_NAME} v{MyPluginInfo.PLUGIN_VERSION} loading...");

        StateReader = new GameStateReader();
        Executor = new ActionExecutor();

        WsServer = new WebSocketServer(9999);
        WsServer.OnMessageReceived += Executor.HandleCommand;
        _ = WsServer.StartAsync();

        // StatePusher pushes game state to agent every 2s
        AddComponent<StatePusher>();

        // ChatHook patches ChatManager to relay player messages to agent
        ChatHook.Apply();

        Log.LogInfo($"{MyPluginInfo.PLUGIN_NAME} loaded. WebSocket listening on ws://localhost:9999/");
    }

    public override bool Unload()
    {
        WsServer?.Stop();
        return base.Unload();
    }
}
