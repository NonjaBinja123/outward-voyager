using UnityEngine;

namespace OutwardVoyager;

/// <summary>
/// Drives the player character toward a navigation target each frame.
///
/// Current approach: CharacterController.SimpleMove via the CC cached on Character.
/// Components are resolved lazily on the first Update() tick after a target is set,
/// so the character is guaranteed to be loaded before we try to read its components.
/// </summary>
public class NavigationController : MonoBehaviour
{
    private Vector3? _target;
    private bool _run;
    private bool _componentsResolved;
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
        _componentsResolved = false; // force re-resolution on next Update
        Plugin.Log.LogInfo($"[Nav] Target set: ({target.x:F1},{target.y:F1},{target.z:F1}) run={run}");
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

        // Resolve components lazily — character is confirmed loaded at this point
        if (!_componentsResolved)
        {
            _lcc = character.GetComponent<LocalCharacterControl>();
            _cc  = character.GetComponent<CharacterController>();
            _componentsResolved = true;
            Plugin.Log.LogInfo($"[Nav] Components resolved — LCC:{_lcc != null} CC:{_cc != null}");

            if (_lcc != null)
            {
                _lcc.enabled = false;
                Plugin.Log.LogInfo("[Nav] LocalCharacterControl suspended.");
            }
            else
            {
                Plugin.Log.LogWarning("[Nav] LocalCharacterControl not found on character.");
            }
        }

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

        if (_cc != null)
        {
            _cc.SimpleMove(dir * speed);
        }
        else
        {
            // No known movement API available yet. Log once then stop navigating
            // to avoid spamming and causing physics issues.
            Plugin.Log.LogWarning("[Nav] No CharacterController found — movement not implemented for this character type. Cancelling.");
            _target = null;
            RestorePlayerInput();
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_failed", reason = "no_movement_api" });
        }

        if (Time.time - _lastUpdateTime >= NavUpdateInterval)
        {
            _lastUpdateTime = Time.time;
            _ = Plugin.WsServer!.SendAsync(new { type = "nav_update", distance = dist });
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
        _componentsResolved = false;
    }
}
