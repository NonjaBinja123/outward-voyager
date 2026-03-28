using HarmonyLib;

namespace OutwardVoyager;

/// <summary>
/// Injects movement input into Outward's ControlsInput pipeline so the agent
/// drives the character exactly as a player would — with animations, physics,
/// and collision all handled by the game's own systems.
///
/// Movement is camera-relative (like real player input):
///   MoveVertical   = forward/backward relative to camera
///   MoveHorizontal = strafe left/right relative to camera
///
/// NavigationController computes these values each frame based on the angle
/// between the camera's forward direction and the direction to the target.
///
/// Camera control:
/// - Player-command mode (IsAutonomous=false): camera is fully user-controlled.
///   NavigationController zeroes CharacterCamera.m_cameraSmoothAutoInput each frame
///   so Outward's auto-follow doesn't steal the camera.
/// - Autonomous mode (IsAutonomous=true): agent can rotate camera via
///   InjectedCameraH / InjectedCameraV (added on top of any user input).
/// </summary>
public static class InputInjector
{
    public static bool IsConnected   { get; set; } = false;  // true only while agent is connected
    public static bool IsNavigating  { get; set; } = false;
    public static bool IsAutonomous  { get; set; } = false;
    public static int  PlayerNum     { get; set; } = 0;

    /// <summary>Camera-relative forward/backward movement input (-1 to 1).</summary>
    public static float InjectedVertical   { get; set; } = 0f;

    /// <summary>Camera-relative strafe left/right movement input (-1 to 1).</summary>
    public static float InjectedHorizontal { get; set; } = 0f;

    /// <summary>Camera horizontal rotation to add in autonomous mode (degrees/s equivalent).</summary>
    public static float InjectedCameraH    { get; set; } = 0f;

    /// <summary>Camera vertical rotation to add in autonomous mode (degrees/s equivalent).</summary>
    public static float InjectedCameraV    { get; set; } = 0f;

    private static Harmony? _harmony;

    public static void Apply()
    {
        _harmony = new Harmony(MyPluginInfo.PLUGIN_GUID + ".input");
        _harmony.PatchAll(typeof(InputInjector));
        Plugin.Log.LogInfo("InputInjector applied.");
    }

    public static void Remove()
    {
        _harmony?.UnpatchSelf();
        IsNavigating = false;
        InjectedVertical = InjectedHorizontal = InjectedCameraH = InjectedCameraV = 0f;
    }

    // ── Movement patches ─────────────────────────────────────────────────

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MoveVertical))]
    [HarmonyPostfix]
    private static void MoveVertical_Postfix(int _playerID, ref float __result)
    {
        if (IsConnected && IsNavigating && _playerID == PlayerNum)
            __result = InjectedVertical;
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MoveHorizontal))]
    [HarmonyPostfix]
    private static void MoveHorizontal_Postfix(int _playerID, ref float __result)
    {
        if (IsConnected && IsNavigating && _playerID == PlayerNum)
            __result = InjectedHorizontal;
    }

    // ── Camera patches (autonomous mode only) ────────────────────────────
    // These ADD to the existing player input rather than replacing it,
    // so manual camera control still works in autonomous mode if needed.

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.RotateCameraHorizontal))]
    [HarmonyPostfix]
    private static void RotateCameraH_Postfix(int _playerID, ref float __result)
    {
        if (IsConnected && IsAutonomous && _playerID == PlayerNum)
            __result += InjectedCameraH;
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.RotateCameraVertical))]
    [HarmonyPostfix]
    private static void RotateCameraV_Postfix(int _playerID, ref float __result)
    {
        if (IsConnected && IsAutonomous && _playerID == PlayerNum)
            __result += InjectedCameraV;
    }
}
