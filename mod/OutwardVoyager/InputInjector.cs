using HarmonyLib;

namespace OutwardVoyager;

/// <summary>
/// Injects movement input into Outward's ControlsInput pipeline so the agent
/// drives the character exactly as a player would — with animations, physics,
/// and collision all handled by the game's own systems.
///
/// When IsNavigating is true, MoveVertical returns 1 (walk forward) and
/// MoveHorizontal returns 0. The NavigationController rotates the character
/// to face the target, so "forward" always means "toward target".
/// </summary>
public static class InputInjector
{
    public static bool IsNavigating { get; set; } = false;
    public static int PlayerNum { get; set; } = 0;

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
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MoveVertical))]
    [HarmonyPostfix]
    private static void MoveVertical_Postfix(int _playerID, ref float __result)
    {
        if (IsNavigating && _playerID == PlayerNum)
            __result = 1.0f;
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MoveHorizontal))]
    [HarmonyPostfix]
    private static void MoveHorizontal_Postfix(int _playerID, ref float __result)
    {
        if (IsNavigating && _playerID == PlayerNum)
            __result = 0.0f;
    }
}
