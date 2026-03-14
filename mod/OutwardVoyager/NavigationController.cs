using UnityEngine;

namespace OutwardVoyager;

/// <summary>
/// Drives the player character toward a navigation target each frame.
///
/// Strategy:
///   1. Disable LocalCharacterControl so player input doesn't fight our movement.
///   2. Drive movement via CharacterController.SimpleMove (Unity standard).
///   3. Re-enable LocalCharacterControl on arrival or cancel.
///
/// Fallback: if CharacterController isn't found, step via transform.Translate.
/// This is a prototype — sprint not yet implemented (same speed for walk/run).
/// </summary>
public class NavigationController : MonoBehaviour
{
    private Vector3? _target;
    private bool _run;
    private LocalCharacterControl? _lcc;
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
        Plugin.Log.LogInfo($"[Nav] Target set: ({target.x:F1},{target.y:F1},{target.z:F1}) run={run}");
        SuspendPlayerInput();
    }

    public void Cancel()
    {
        if (!_target.HasValue) return;
        _target = null;
        RestorePlayerInput();
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

        if (dist <= ArrivalDistance)
        {
            Plugin.Log.LogInfo("[Nav] Arrived at target.");
            _target = null;
            RestorePlayerInput();
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_arrived" });
            return;
        }

        var dir = toTarget.normalized;
        float speed = _run ? RunSpeed : WalkSpeed;
        MoveCharacter(character, dir, speed);

        if (Time.time - _lastUpdateTime >= NavUpdateInterval)
        {
            _lastUpdateTime = Time.time;
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_update", distance = dist });
        }
    }

    private void SuspendPlayerInput()
    {
        var character = CharacterManager.Instance?.GetFirstLocalCharacter();
        if (character == null) return;

        _lcc = character.GetComponent<LocalCharacterControl>();
        _cc  = character.GetComponent<CharacterController>();

        if (_lcc != null)
        {
            _lcc.enabled = false;
            Plugin.Log.LogInfo("[Nav] LocalCharacterControl suspended.");
        }
        else
        {
            Plugin.Log.LogWarning("[Nav] LocalCharacterControl not found — player may still have input.");
        }
    }

    private void RestorePlayerInput()
    {
        if (_lcc != null)
        {
            _lcc.enabled = true;
            Plugin.Log.LogInfo("[Nav] LocalCharacterControl restored.");
        }
        _lcc = null;
        _cc  = null;
    }

    private void MoveCharacter(Character character, Vector3 dir, float speed)
    {
        // CharacterController.SimpleMove handles gravity automatically.
        if (_cc != null)
        {
            _cc.SimpleMove(dir * speed);
            return;
        }

        // Fallback: direct transform step. Works but bypasses physics.
        character.transform.Translate(dir * speed * Time.deltaTime, Space.World);
    }
}
