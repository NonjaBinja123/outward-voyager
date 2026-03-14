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
        if (Time.time < _nextPush) return;
        _nextPush = Time.time + PushInterval;

        if (Plugin.WsServer == null || Plugin.StateReader == null) return;
        var state = Plugin.StateReader.ReadCurrentState();
        _ = Plugin.WsServer.SendAsync(state);
    }
}
