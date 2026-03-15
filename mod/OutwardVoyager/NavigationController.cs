using UnityEngine;

namespace OutwardVoyager;

/// <summary>
/// Drives the player character toward a navigation target each frame via
/// CharacterController.SimpleMove. Player input is NOT disabled — LocalCharacterControl
/// remains active so chat and all other input continues to work.
///
/// Known limitation: walk animation does not play because we are moving through
/// CharacterController directly rather than through Outward's input pipeline.
/// Character is rotated to face the movement direction so it does not slide backwards.
/// </summary>
public class NavigationController : MonoBehaviour
{
    private Vector3? _target;
    private bool _run;
    private bool _componentsResolved;
    private CharacterController? _cc;

    private const float WalkSpeed = 3.0f;
    private const float RunSpeed  = 5.5f;
    private const float ArrivalDistance  = 2.5f;
    private const float NavUpdateInterval = 3.0f;
    private float _lastUpdateTime;

    public bool IsNavigating => _target.HasValue;

    public void SetTarget(Vector3 target, bool run)
    {
        _target = target;
        _run = run;
        _componentsResolved = false;
        Plugin.Log.LogInfo($"[Nav] Target set: ({target.x:F1},{target.y:F1},{target.z:F1}) run={run}");
    }

    public void Cancel()
    {
        if (!_target.HasValue) return;
        _target = null;
        _cc = null;
        _componentsResolved = false;
        Plugin.Log.LogInfo("[Nav] Navigation cancelled.");
    }

    private void Update()
    {
        if (!_target.HasValue) return;

        var character = CharacterManager.Instance?.GetFirstLocalCharacter();
        if (character == null) return;

        // Resolve CharacterController lazily — character guaranteed loaded here
        if (!_componentsResolved)
        {
            _cc = character.GetComponent<CharacterController>();
            _componentsResolved = true;
            Plugin.Log.LogInfo($"[Nav] CharacterController found: {_cc != null}");

            if (_cc == null)
            {
                Plugin.Log.LogWarning("[Nav] No CharacterController on player — cannot move. Cancelling.");
                _target = null;
                _ = Plugin.WsServer!.SendAsync(new { type = "nav_failed", reason = "no_movement_api" });
                return;
            }
        }

        if (_cc == null) return;

        var pos = character.transform.position;
        var toTarget = _target.Value - pos;
        toTarget.y = 0f;
        float dist = toTarget.magnitude;

        if (dist <= ArrivalDistance)
        {
            Plugin.Log.LogInfo("[Nav] Arrived at target.");
            _target = null;
            _cc = null;
            _componentsResolved = false;
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_arrived" });
            return;
        }

        var dir = toTarget.normalized;

        // Rotate character to face direction of movement so it doesn't slide backwards
        character.transform.rotation = Quaternion.RotateTowards(
            character.transform.rotation,
            Quaternion.LookRotation(dir),
            360f * Time.deltaTime
        );

        float speed = _run ? RunSpeed : WalkSpeed;
        _cc.SimpleMove(dir * speed);

        if (Time.time - _lastUpdateTime >= NavUpdateInterval)
        {
            _lastUpdateTime = Time.time;
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_update", distance = dist });
        }
    }
}
