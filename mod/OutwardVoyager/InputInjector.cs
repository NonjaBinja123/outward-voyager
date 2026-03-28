using HarmonyLib;
using System.Collections.Generic;

namespace OutwardVoyager;

/// <summary>
/// Injects inputs into Outward's ControlsInput pipeline via HarmonyX patches.
/// The real keyboard is NEVER touched — all inputs are asserted at the Unity layer.
///
/// Two input modes:
///   Pulse — true for exactly one Update frame, then auto-cleared by ClearPulsed().
///            Use for: attack, dodge, menu navigation, any single "press" action.
///   Hold  — true until explicitly released via ReleaseAction().
///            Use for: block, sprint (while the agent wants to keep holding).
///
/// Usage:
///   PulseAction("attack")   → attacks once next frame
///   HoldAction("block")     → blocks until ReleaseAction("block")
///   PulseAction("menu_down") → moves menu cursor down one step
///
/// StatePusher.Update() calls ClearPulsed() at end of every frame.
/// WebSocketServer calls ClearAll() on agent disconnect.
/// </summary>
public static class InputInjector
{
    // ── Connection / mode flags ───────────────────────────────────────────────
    public static bool IsConnected   { get; set; } = false;  // true only while agent connected
    public static bool IsNavigating  { get; set; } = false;
    public static bool IsAutonomous  { get; set; } = false;
    public static int  PlayerNum     { get; set; } = 0;

    // ── Analogue movement / camera (set each frame by NavigationController) ──
    public static float InjectedVertical   { get; set; } = 0f;
    public static float InjectedHorizontal { get; set; } = 0f;
    public static float InjectedCameraH    { get; set; } = 0f;
    public static float InjectedCameraV    { get; set; } = 0f;

    // ── Action queues ─────────────────────────────────────────────────────────
    private static readonly HashSet<string> _pulsed = new();   // one-frame actions
    private static readonly HashSet<string> _held   = new();   // held until released
    private static readonly object _lock = new();

    /// <summary>Assert an action for exactly one Update frame.</summary>
    public static void PulseAction(string action)
    {
        lock (_lock) _pulsed.Add(action);
    }

    /// <summary>Assert an action continuously until ReleaseAction() is called.</summary>
    public static void HoldAction(string action)
    {
        lock (_lock) _held.Add(action);
    }

    /// <summary>Stop holding an action.</summary>
    public static void ReleaseAction(string action)
    {
        lock (_lock) _held.Remove(action);
    }

    /// <summary>Clear all pulsed actions. Called by StatePusher.Update() each frame.</summary>
    public static void ClearPulsed()
    {
        lock (_lock) _pulsed.Clear();
    }

    /// <summary>Clear all held and pulsed actions. Called on agent disconnect.</summary>
    public static void ClearAll()
    {
        lock (_lock) { _pulsed.Clear(); _held.Clear(); }
    }

    private static bool IsActive(string action)
    {
        lock (_lock) return _held.Contains(action) || _pulsed.Contains(action);
    }

    // ── Harmony lifecycle ─────────────────────────────────────────────────────

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
        ClearAll();
    }

    // ── Movement patches ──────────────────────────────────────────────────────

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

    // ── Camera patches (autonomous mode only) ─────────────────────────────────

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

    // ── Combat patches ────────────────────────────────────────────────────────

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.Attack1Press))]
    [HarmonyPostfix]
    private static void Attack1Press_Postfix(int _playerID, ref bool __result)
    {
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("attack");
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.Attack1))]
    [HarmonyPostfix]
    private static void Attack1_Postfix(int _playerID, ref bool __result)
    {
        // Also fire Attack1 (held) so the game treats it as a proper button hold
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("attack") || IsActive("attack_hold");
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.Attack2))]
    [HarmonyPostfix]
    private static void Attack2_Postfix(int _playerID, ref bool __result)
    {
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("attack2");
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.Block))]
    [HarmonyPostfix]
    private static void Block_Postfix(int _playerID, ref bool __result)
    {
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("block");
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.DodgeButtonDown))]
    [HarmonyPostfix]
    private static void DodgeButtonDown_Postfix(int _playerID, ref bool __result)
    {
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("dodge");
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.Sprint))]
    [HarmonyPostfix]
    private static void Sprint_Postfix(int _playerID, ref bool __result)
    {
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("sprint");
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.Sheathe))]
    [HarmonyPostfix]
    private static void Sheathe_Postfix(int _playerID, ref bool __result)
    {
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("sheathe");
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.StealthButton))]
    [HarmonyPostfix]
    private static void StealthButton_Postfix(int _playerID, ref bool __result)
    {
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("stealth");
    }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.LockToggle))]
    [HarmonyPostfix]
    private static void LockToggle_Postfix(int _playerID, ref bool __result)
    {
        if (IsConnected && _playerID == PlayerNum) __result |= IsActive("lock_target");
    }

    // ── Quick slot patches ────────────────────────────────────────────────────

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.QuickSlotInstant1))]
    [HarmonyPostfix]
    private static void QS1_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("quickslot_1"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.QuickSlotInstant2))]
    [HarmonyPostfix]
    private static void QS2_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("quickslot_2"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.QuickSlotInstant3))]
    [HarmonyPostfix]
    private static void QS3_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("quickslot_3"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.QuickSlotInstant4))]
    [HarmonyPostfix]
    private static void QS4_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("quickslot_4"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.QuickSlotInstant5))]
    [HarmonyPostfix]
    private static void QS5_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("quickslot_5"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.QuickSlotInstant6))]
    [HarmonyPostfix]
    private static void QS6_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("quickslot_6"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.QuickSlotInstant7))]
    [HarmonyPostfix]
    private static void QS7_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("quickslot_7"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.QuickSlotInstant8))]
    [HarmonyPostfix]
    private static void QS8_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("quickslot_8"); }

    // ── Menu navigation patches ───────────────────────────────────────────────

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MenuUp))]
    [HarmonyPostfix]
    private static void MenuUp_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("menu_up"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MenuDown))]
    [HarmonyPostfix]
    private static void MenuDown_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("menu_down"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MenuLeft))]
    [HarmonyPostfix]
    private static void MenuLeft_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("menu_left"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MenuRight))]
    [HarmonyPostfix]
    private static void MenuRight_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("menu_right"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MenuQuickAction))]
    [HarmonyPostfix]
    private static void MenuConfirm_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("menu_confirm"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.MenuCancel))]
    [HarmonyPostfix]
    private static void MenuCancel_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("menu_cancel"); }

    // ── Menu toggle patches ───────────────────────────────────────────────────

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.ToggleInventory))]
    [HarmonyPostfix]
    private static void ToggleInventory_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("toggle_inventory"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.ToggleEquipment))]
    [HarmonyPostfix]
    private static void ToggleEquipment_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("toggle_equipment"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.ToggleMap))]
    [HarmonyPostfix]
    private static void ToggleMap_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("toggle_map"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.ToggleSkillMenu))]
    [HarmonyPostfix]
    private static void ToggleSkills_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("toggle_skills"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.ToggleCharacterStatusMenu))]
    [HarmonyPostfix]
    private static void ToggleStatus_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("toggle_status"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.ToggleCraftingMenu))]
    [HarmonyPostfix]
    private static void ToggleCrafting_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("toggle_crafting"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.GoToNextMenu))]
    [HarmonyPostfix]
    private static void GoNextMenu_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("go_next_menu"); }

    [HarmonyPatch(typeof(ControlsInput), nameof(ControlsInput.GoToPreviousMenu))]
    [HarmonyPostfix]
    private static void GoPrevMenu_Postfix(int _playerID, ref bool __result)
    { if (IsConnected && _playerID == PlayerNum) __result |= IsActive("go_prev_menu"); }
}
