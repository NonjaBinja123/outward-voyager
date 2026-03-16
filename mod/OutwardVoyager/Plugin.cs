using System.Collections.Concurrent;
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
    internal static NavigationController? NavController;

    /// <summary>
    /// Actions queued from background threads to run on the Unity main thread.
    /// Drained every frame by StatePusher.Update().
    /// </summary>
    internal static readonly ConcurrentQueue<Action> MainThreadQueue = new();

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

        // NavigationController drives character movement toward a target each frame
        NavController = AddComponent<NavigationController>();

        // ChatHook patches ChatManager to relay player messages to agent
        ChatHook.Apply();

        // InputInjector patches ControlsInput so agent movement goes through
        // Outward's own pipeline (animations, physics, collision)
        try { InputInjector.Apply(); }
        catch (Exception ex) { Log.LogError($"InputInjector failed: {ex.Message}"); }

        Log.LogInfo($"{MyPluginInfo.PLUGIN_NAME} loaded. WebSocket listening on ws://localhost:9999/");
    }

    public override bool Unload()
    {
        WsServer?.Stop();
        return base.Unload();
    }
}
