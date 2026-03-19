using UnityEngine;

namespace OutwardVoyager;

/// <summary>
/// Drives the player character toward a navigation target using camera-relative
/// input injection. Each frame it computes the world-space direction to the target,
/// projects it into camera space, and sets InjectedVertical / InjectedHorizontal
/// on InputInjector. Outward's own input pipeline then moves the character with
/// full animations, physics, and collision — exactly as if the player were holding
/// the stick in that direction.
///
/// No manual character rotation — Outward handles facing from movement input.
/// </summary>
public class NavigationController : MonoBehaviour
{
    private Vector3? _target;
    private bool _run;

    private const float ArrivalDistance = 2.5f;
    private const float NavUpdateInterval = 3.0f;

    // Progress-based stuck detection: if distance to target hasn't decreased by
    // at least ProgressMinimum units over ProgressCheckInterval seconds → stuck.
    // This catches wall-sliding (character moves but isn't getting closer).
    private const float ProgressCheckInterval = 3.0f;
    private const float ProgressMinimum = 0.5f;
    private float _lastProgressCheckTime;
    private float _distAtLastProgressCheck;

    private float _lastUpdateTime;

    public bool IsNavigating => _target.HasValue;

    public void SetTarget(Vector3 target, bool run)
    {
        _target = target;
        _run = run;
        _lastProgressCheckTime = Time.time;

        var character = CharacterManager.Instance?.GetFirstLocalCharacter();
        var pos = character?.transform.position ?? Vector3.zero;
        _distAtLastProgressCheck = Vector3.Distance(pos, target);

        InputInjector.IsNavigating = true;
        Plugin.Log.LogInfo($"[Nav] Target set: ({target.x:F1},{target.y:F1},{target.z:F1}) run={run}");
    }

    public void Cancel()
    {
        if (!_target.HasValue) return;
        _target = null;
        _stuckTime = 0f;
        InputInjector.IsNavigating = false;
        InputInjector.InjectedVertical = 0f;
        InputInjector.InjectedHorizontal = 0f;
        Plugin.Log.LogInfo("[Nav] Navigation cancelled.");
    }

    private void Update()
    {
        if (!_target.HasValue) return;

        var character = CharacterManager.Instance?.GetFirstLocalCharacter();
        if (character == null) return;

        var cam = Camera.main;
        if (cam == null) return;

        var pos = character.transform.position;
        var toTarget = _target.Value - pos;
        toTarget.y = 0f;
        float dist = toTarget.magnitude;

        // Arrived?
        if (dist <= ArrivalDistance)
        {
            Plugin.Log.LogInfo("[Nav] Arrived at target.");
            StopNav();
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_arrived" });
            return;
        }

        // Progress-based stuck detection: check every ProgressCheckInterval seconds
        // whether the distance to target has actually decreased. This catches wall-sliding
        // where the character moves but isn't making progress toward the goal.
        if (Time.time - _lastProgressCheckTime >= ProgressCheckInterval)
        {
            float progress = _distAtLastProgressCheck - dist;
            if (progress < ProgressMinimum)
            {
                Plugin.Log.LogInfo($"[Nav] Stuck — no progress toward target (moved {progress:F2}u in {ProgressCheckInterval}s). Cancelling.");
                StopNav();
                _ = Plugin.WsServer!.SendAsync(new { type = "nav_failed", reason = "stuck" });
                return;
            }
            _lastProgressCheckTime = Time.time;
            _distAtLastProgressCheck = dist;
        }

        // Compute camera-relative input
        // Camera forward/right projected onto XZ plane
        var camFwd = cam.transform.forward;
        camFwd.y = 0f;
        camFwd.Normalize();

        var camRight = cam.transform.right;
        camRight.y = 0f;
        camRight.Normalize();

        var worldDir = toTarget.normalized;

        // Project world direction onto camera axes
        // Vertical = how much of worldDir aligns with camera forward (positive = forward)
        // Horizontal = how much of worldDir aligns with camera right (positive = right)
        float vertical = Vector3.Dot(worldDir, camFwd);
        float horizontal = Vector3.Dot(worldDir, camRight);

        InputInjector.InjectedVertical = vertical;
        InputInjector.InjectedHorizontal = horizontal;

        // In player-command mode, suppress Outward's camera auto-follow so the
        // user can freely rotate the camera while the character walks toward the target.
        // In autonomous mode, let auto-follow work (or agent drives camera explicitly).
        if (!InputInjector.IsAutonomous)
        {
            var charCams = CharacterCamera.CharCamList;
            if (charCams != null && charCams.Count > 0)
                charCams[0].m_cameraSmoothAutoInput = UnityEngine.Vector2.zero;
        }

        if (Time.time - _lastUpdateTime >= NavUpdateInterval)
        {
            _lastUpdateTime = Time.time;
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_update", distance = dist });
        }
    }

    private void StopNav()
    {
        _target = null;
        _stuckTime = 0f;
        InputInjector.IsNavigating = false;
        InputInjector.InjectedVertical = 0f;
        InputInjector.InjectedHorizontal = 0f;
    }
}
