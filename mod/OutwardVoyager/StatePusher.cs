using BepInEx.Unity.IL2CPP.UnityEngine;
using UnityEngine;

namespace OutwardVoyager;

/// <summary>
/// MonoBehaviour that pushes game state to the Python agent every 2 seconds.
/// Runs on BepInEx's hidden game object so it gets Unity Update() calls.
/// </summary>
public class StatePusher : MonoBehaviour
{
    private float _nextPush;
    private const float PushInterval = 2f;

    private void Update()
    {
        // Drain actions queued from background threads (e.g. WebSocket receive)
        while (Plugin.MainThreadQueue.TryDequeue(out var action))
        {
            try { action(); }
            catch (Exception ex) { Plugin.Log.LogWarning($"[MainThread] Action failed: {ex.Message}"); }
        }

        // Clear one-frame pulsed actions at end of each Update so they don't carry over
        InputInjector.ClearPulsed();

        if (Time.time < _nextPush) return;
        _nextPush = Time.time + PushInterval;

        if (Plugin.WsServer == null || Plugin.StateReader == null) return;
        var state = Plugin.StateReader.ReadCurrentState();
        _ = Plugin.WsServer.SendAsync(state);
    }
}
