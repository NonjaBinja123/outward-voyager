using System.Collections.Generic;
using UnityEngine;

namespace OutwardVoyager;

/// <summary>
/// Automatically navigates the game menus to load a save:
///   Main Menu → Continue → Select Character "AgentNeo" → Select Latest Save → Skip Load Prompt
///
/// Each step waits for the previous UI state to be ready before advancing.
/// Retries automatically if the game returns to the main menu after a failed load.
/// Logs every transition for debugging via BepInEx console.
/// </summary>
public class AutoLoader : MonoBehaviour
{
    public static string CharacterName { get; set; } = "AgentNeo";
    public static bool Enabled { get; set; } = true;

    private enum State
    {
        WaitForMenu,
        ClickContinue,
        WaitForCharPanel,
        SelectCharacter,
        WaitForSaveList,
        SelectSave,
        WaitForLoading,
        SkipLoadPrompt,
        Done
    }

    private State _state = State.WaitForMenu;
    private float _stateTimer;
    private bool _finished;

    private void Update()
    {
        if (!Enabled) return;

        // Auto-retry: if we previously finished but the game came back to the main menu
        // (e.g., load failed), reset and try again.
        if (_finished)
        {
            if (MenuManager.Instance != null && MenuManager.Instance.IsInMainMenuScene)
            {
                Plugin.Log.LogInfo("[AutoLoader] Back at main menu after finish — retrying.");
                _finished = false;
                _state = State.WaitForMenu;
                _stateTimer = 0f;
            }
            return;
        }

        _stateTimer += Time.deltaTime;

        try
        {
            switch (_state)
            {
                case State.WaitForMenu:      Step_WaitForMenu();      break;
                case State.ClickContinue:    Step_ClickContinue();    break;
                case State.WaitForCharPanel: Step_WaitForCharPanel(); break;
                case State.SelectCharacter:  Step_SelectCharacter();  break;
                case State.WaitForSaveList:  Step_WaitForSaveList();  break;
                case State.SelectSave:       Step_SelectSave();       break;
                case State.WaitForLoading:   Step_WaitForLoading();   break;
                case State.SkipLoadPrompt:   Step_SkipLoadPrompt();   break;
            }
        }
        catch (System.Exception ex)
        {
            Plugin.Log.LogError($"[AutoLoader] Error in {_state}: {ex.Message}");
        }
    }

    private void Go(State next)
    {
        Plugin.Log.LogInfo($"[AutoLoader] {_state} → {next}");
        _state = next;
        _stateTimer = 0f;
    }

    private void Finish(string msg)
    {
        Plugin.Log.LogInfo($"[AutoLoader] Finished: {msg}");
        _finished = true;
        _state = State.Done;
    }

    // ── 1. Wait for main menu to be fully initialized ───────────────────

    private void Step_WaitForMenu()
    {
        if (MenuManager.Instance == null || !MenuManager.Instance.IsInMainMenuScene) return;
        if (_stateTimer < 8f) return; // Wait 8s for splash/UI to fully settle
        Go(State.ClickContinue);
    }

    // ── 2. Press the Continue button ────────────────────────────────────

    private void Step_ClickContinue()
    {
        var ms = UnityEngine.Object.FindObjectOfType<MainScreen>();
        if (ms == null)
        {
            if (_stateTimer > 15f) Finish("MainScreen not found.");
            return;
        }

        Plugin.Log.LogInfo("[AutoLoader] Pressing Continue...");
        ms.OnContinueClicked();
        Go(State.WaitForCharPanel);
    }

    // ── 3. Wait for the character selection panel to populate ────────────
    // NOTE: After OnContinueClicked(), InLoading and IsLoadSequenceStarted become
    // true immediately (the game starts a load sequence even before showing the
    // character panel). We must NOT use those to short-circuit — only jump ahead
    // if IsGameplayLoading is true, which means the level is actually loading.

    private void Step_WaitForCharPanel()
    {
        if (_stateTimer > 30f) { Finish("Character panel never appeared."); return; }

        // IsGameplayLoading = true means the game actually started loading a level,
        // which only happens if character+save were auto-selected (single char/save).
        // Wait at least 2s before accepting this to avoid menu-transition false positives.
        if (_stateTimer > 2f)
        {
            var nll = NetworkLevelLoader.Instance;
            if (nll != null && nll.IsGameplayLoading)
            {
                Plugin.Log.LogInfo("[AutoLoader] Game auto-selected and started loading.");
                Go(State.WaitForLoading);
                return;
            }
        }

        // Wait for the character selection panel to appear and populate
        var ms = UnityEngine.Object.FindObjectOfType<MainScreen>();
        if (ms == null) return;
        if (!ms.IsCharacterSelectionMenuDisplayed) return;

        var panel = ms.m_characterSelection;
        if (panel == null) return;

        var slots = panel.m_saveSlot;
        if (slots == null || slots.Count == 0) return;

        Plugin.Log.LogInfo($"[AutoLoader] Character panel ready with {slots.Count} slot(s).");
        Go(State.SelectCharacter);
    }

    // ── 4. Find "AgentNeo" and click it ─────────────────────────────────

    private void Step_SelectCharacter()
    {
        var ms = UnityEngine.Object.FindObjectOfType<MainScreen>();
        if (ms == null) { Finish("MainScreen disappeared."); return; }

        var panel = ms.m_characterSelection;
        if (panel == null) { Finish("No CharacterSelectionPanel."); return; }

        var slots = panel.m_saveSlot;
        if (slots == null || slots.Count == 0) { Finish("No save slots."); return; }

        int targetIdx = -1;
        for (int i = 0; i < slots.Count; i++)
        {
            var slot = slots[i];
            if (slot == null) continue;
            string name = slot.CharacterName ?? "";
            Plugin.Log.LogInfo($"[AutoLoader]   Slot[{i}]: '{name}'");
            if (name.Equals(CharacterName, System.StringComparison.OrdinalIgnoreCase))
                targetIdx = i;
        }

        if (targetIdx < 0)
        {
            Plugin.Log.LogWarning($"[AutoLoader] '{CharacterName}' not found — using slot 0.");
            targetIdx = 0;
        }

        Plugin.Log.LogInfo($"[AutoLoader] Clicking character slot {targetIdx} ('{CharacterName}')...");
        panel.OnCharacterClicked(targetIdx);
        Go(State.WaitForSaveList);
    }

    // ── 5. Wait for save instance list to appear ────────────────────────

    private void Step_WaitForSaveList()
    {
        if (_stateTimer > 10f) { Finish("Save list never appeared."); return; }

        // If gameplay loading started (auto-selected), jump ahead
        var nll = NetworkLevelLoader.Instance;
        if (nll != null && nll.IsGameplayLoading)
        {
            Plugin.Log.LogInfo("[AutoLoader] Game started loading after character click.");
            Go(State.WaitForLoading);
            return;
        }

        // Give UI a moment to populate save history after character click
        if (_stateTimer < 1.5f) return;
        Go(State.SelectSave);
    }

    // ── 6. Select the topmost (latest) save ─────────────────────────────

    private void Step_SelectSave()
    {
        var ms = UnityEngine.Object.FindObjectOfType<MainScreen>();
        var panel = ms?.m_characterSelection;

        if (panel != null)
        {
            Plugin.Log.LogInfo("[AutoLoader] Clicking save instance 0 (latest)...");
            panel.OnSaveInstanceClicked(0);
        }
        else
        {
            Plugin.Log.LogWarning("[AutoLoader] Panel gone — hoping character click was enough.");
        }

        Go(State.WaitForLoading);
    }

    // ── 7. Wait for level loading to complete ───────────────────────────

    private void Step_WaitForLoading()
    {
        if (_stateTimer > 90f) { Finish("Loading timeout (90s)."); return; }

        var nll = NetworkLevelLoader.Instance;
        if (nll == null) return;

        // Scene fully loaded — done
        if (nll.IsOverallLoadingDone)
        {
            Finish("Level loaded successfully!");
            return;
        }

        // Loading done but waiting for "press any key to continue" prompt
        if (nll.AllPlayerDoneLoading && !nll.AllPlayerReadyToContinue)
        {
            Go(State.SkipLoadPrompt);
            return;
        }
    }

    // ── 8. Skip the "press any key to continue" screen ──────────────────

    private void Step_SkipLoadPrompt()
    {
        var nll = NetworkLevelLoader.Instance;
        if (nll == null) { Finish("NLL gone."); return; }

        if (nll.AllPlayerReadyToContinue || nll.IsOverallLoadingDone)
        {
            Finish("Load complete.");
            return;
        }

        Plugin.Log.LogInfo("[AutoLoader] Skipping load prompt...");

        try { nll.SetContinueAfterLoading(); }
        catch (System.Exception ex) { Plugin.Log.LogWarning($"[AutoLoader] SetContinueAfterLoading: {ex.Message}"); }

        try { nll.ForceAllPlayersReady(); }
        catch (System.Exception ex) { Plugin.Log.LogWarning($"[AutoLoader] ForceAllPlayersReady: {ex.Message}"); }

        // Keep retrying until the game advances
        if (_stateTimer > 5f)
        {
            Finish("Load prompt skip attempted — game should proceed.");
        }
    }
}
