using UnityEngine;

namespace OutwardVoyager;

/// <summary>
/// Drives the player character toward a navigation target by:
///   1. Rotating the character to face the target each frame.
///   2. Injecting MoveVertical=1 via InputInjector so Outward's own pipeline
///      handles movement — animations, physics, and collision all work normally.
///
/// Stuck detection: if the character's position hasn't changed for 0.5s while
/// navigating, assumes a wall or obstacle and cancels.
/// </summary>
public class NavigationController : MonoBehaviour
{
    private Vector3? _target;
    private bool _run;

    private Vector3 _lastPos;
    private float _stuckTime;

    private const float ArrivalDistance = 2.5f;
    private const float NavUpdateInterval = 3.0f;
    private const float StuckTimeLimit = 0.5f;
    private const float StuckMoveThreshold = 0.1f; // units/sec below this = stuck

    private float _lastUpdateTime;

    public bool IsNavigating => _target.HasValue;

    public void SetTarget(Vector3 target, bool run)
    {
        _target = target;
        _run = run;
        _stuckTime = 0f;

        var character = CharacterManager.Instance?.GetFirstLocalCharacter();
        _lastPos = character?.transform.position ?? Vector3.zero;

        InputInjector.IsNavigating = true;
        Plugin.Log.LogInfo($"[Nav] Target set: ({target.x:F1},{target.y:F1},{target.z:F1}) run={run}");
    }

    public void Cancel()
    {
        if (!_target.HasValue) return;
        _target = null;
        _stuckTime = 0f;
        InputInjector.IsNavigating = false;
        Plugin.Log.LogInfo("[Nav] Navigation cancelled.");
    }

    private void Update()
    {
        if (!_target.HasValue) return;

        var character = CharacterManager.Instance?.GetFirstLocalCharacter();
        if (character == null) return;

        var pos = character.transform.position;
        var toTarget = _target.Value - pos;
        toTarget.y = 0f;
        float dist = toTarget.magnitude;

        // Arrived?
        if (dist <= ArrivalDistance)
        {
            Plugin.Log.LogInfo("[Nav] Arrived at target.");
            _target = null;
            _stuckTime = 0f;
            InputInjector.IsNavigating = false;
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_arrived" });
            return;
        }

        // Stuck detection — wall or impassable obstacle
        float moved = Vector3.Distance(pos, _lastPos) / Time.deltaTime;
        if (moved < StuckMoveThreshold)
        {
            _stuckTime += Time.deltaTime;
            if (_stuckTime >= StuckTimeLimit)
            {
                Plugin.Log.LogInfo("[Nav] Stuck — obstacle detected. Cancelling.");
                _target = null;
                _stuckTime = 0f;
                InputInjector.IsNavigating = false;
                _ = Plugin.WsServer!.SendAsync(new { type = "nav_failed", reason = "stuck" });
                return;
            }
        }
        else
        {
            _stuckTime = 0f;
        }
        _lastPos = pos;

        // Rotate to face target — InputInjector injects MoveVertical=1 (forward)
        // so Outward drives the character toward wherever it's facing
        var dir = toTarget.normalized;
        character.transform.rotation = Quaternion.RotateTowards(
            character.transform.rotation,
            Quaternion.LookRotation(dir),
            720f * Time.deltaTime  // fast rotation so character snaps to direction quickly
        );

        if (Time.time - _lastUpdateTime >= NavUpdateInterval)
        {
            _lastUpdateTime = Time.time;
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_update", distance = dist });
        }
    }
}
