using System.Text.Json;
using System.Text.Json.Serialization;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace OutwardVoyager;

/// <summary>
/// Receives commands from the Python agent and executes them in-game.
/// Commands arrive as JSON: { "action": "...", "params": { ... } }
/// </summary>
public class ActionExecutor
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public void HandleCommand(string json)
    {
        // Commands arrive on the WebSocket background thread.
        // All Unity API calls must run on the main thread — enqueue and return immediately.
        Plugin.MainThreadQueue.Enqueue(() => ExecuteCommand(json));
    }

    private void ExecuteCommand(string json)
    {
        AgentCommand? cmd;
        try
        {
            cmd = JsonSerializer.Deserialize<AgentCommand>(json, JsonOpts);
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"Bad command JSON: {ex.Message}");
            return;
        }

        if (cmd is null) return;
        Plugin.Log.LogInfo($"Command received: {cmd.Action}");

        switch (cmd.Action)
        {
            case "get_state":
                SendState();
                break;
            case "say":
                SayInChat(GetString(cmd.Params, "message"));
                break;
            case "display_player_chat":
                ChatHook.DisplayPlayerMessage(GetString(cmd.Params, "message"));
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "display_player_chat", success = true });
                break;
            case "move":
                MovePlayer(cmd.Params);
                break;
            case "navigate_to":
                NavigateTo(cmd.Params);
                break;
            case "navigate_cancel":
                Plugin.NavController?.Cancel();
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "navigate_cancel", success = true });
                break;
            case "set_autonomous":
                InputInjector.IsAutonomous = GetBool(cmd.Params, "enabled");
                Plugin.Log.LogInfo($"[Mode] Autonomous={InputInjector.IsAutonomous}");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "set_autonomous", success = true, enabled = InputInjector.IsAutonomous });
                break;

            // ── Menu navigation (driven from Python VisionAutoLoader) ──────────
            case "menu_query_state":
                MenuQueryState();
                break;
            case "menu_press_continue":
                MenuPressContinue();
                break;
            case "menu_select_character":
                MenuSelectCharacter(cmd.Params);
                break;
            case "menu_select_save":
                MenuSelectSave(cmd.Params);
                break;
            case "menu_press_space":
                MenuPressSpace();
                break;
            case "scan_nearby":
                ScanNearby(cmd.Params);
                break;
            case "interact":
                Interact(cmd.Params);
                break;
            case "take_item":
                TakeItem(cmd.Params);
                break;
            case "use_item":
                UseItem(cmd.Params);
                break;
            case "trigger_interaction":
                TriggerInteraction(cmd.Params);
                break;
            case "equip_item":
                EquipItem(cmd.Params);
                break;
            case "read_skills":
                ReadSkills();
                break;
            case "face_point":
                FacePoint(cmd.Params);
                break;
            case "open_menu":
                OpenMenu(cmd.Params);
                break;
            case "close_menu":
                CloseMenu();
                break;
            default:
                Plugin.Log.LogWarning($"Unknown action: {cmd.Action}");
                SendError($"unknown action: {cmd.Action}");
                break;
        }
    }

    private void SendState()
    {
        var state = Plugin.StateReader!.ReadCurrentState();
        _ = Plugin.WsServer!.SendAsync(state);
    }

    private void SayInChat(string message)
    {
        ChatHook.SendMessage(message);
        _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "say", success = true });
    }

    private void MovePlayer(Dictionary<string, object?> p)
    {
        // TODO: Implement movement via game API post interop-gen
        Plugin.Log.LogInfo($"[MOVE] params={JsonSerializer.Serialize(p)}");
        _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "move", success = false, reason = "not_implemented_yet" });
    }

    private void NavigateTo(Dictionary<string, object?> p)
    {
        try
        {
            float x = GetFloat(p, "x", 0f);
            float y = GetFloat(p, "y", 0f);
            float z = GetFloat(p, "z", 0f);
            bool run = GetBool(p, "run");

            Plugin.NavController!.SetTarget(new Vector3(x, y, z), run);
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "navigate_to", success = true });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Nav] navigate_to error: {ex.Message}");
            SendError($"navigate_to failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Walks the active scene hierarchy and reports every GameObject within radius.
    /// Uses SceneManager.GetRootGameObjects → recursive child walk, which is
    /// guaranteed to work in IL2CPP (no FindObjectsOfType needed).
    /// </summary>
    private void ScanNearby(Dictionary<string, object?> p)
    {
        float radius = GetFloat(p, "radius", 30f);
        try
        {
            var playerChar = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (playerChar == null) { SendError("no player character"); return; }

            var playerPos = playerChar.transform.position;
            var scene = SceneManager.GetActiveScene();
            var roots = scene.GetRootGameObjects();

            var results = new List<object>();
            var seen = new HashSet<int>();
            int playerRootId = playerChar.transform.root.gameObject.GetInstanceID();

            foreach (var root in roots)
                WalkChildren(root.transform, playerPos, radius, results, seen, playerRootId);

            Plugin.Log.LogInfo($"[Scan] Found {results.Count} objects within {radius}u.");
            _ = Plugin.WsServer!.SendAsync(new
            {
                type = "scan_result",
                count = results.Count,
                radius,
                objects = results,
            });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Scan] error: {ex.Message}");
            SendError($"scan failed: {ex.Message}");
        }
    }

    private static void WalkChildren(Transform t, Vector3 playerPos, float radius,
        List<object> results, HashSet<int> seen, int playerRootId)
    {
        var go = t.gameObject;
        int id = go.GetInstanceID();

        if (!seen.Add(id)) return;
        if (id == playerRootId) return;

        var pos = t.position;
        var flat = pos - playerPos;
        flat.y = 0f;
        float dist = flat.magnitude;

        if (dist <= radius)
        {
            // Collect useful info about this object
            bool hasCharacter = go.GetComponent<Character>() != null;
            bool isDead = hasCharacter && go.GetComponent<Character>().IsDead;
            bool hasCollider = go.GetComponent<Collider>() != null;

            results.Add(new
            {
                name = go.name,
                x = pos.x, y = pos.y, z = pos.z,
                distance = (float)Math.Round(dist, 1),
                tag = go.tag,
                has_character = hasCharacter,
                is_dead = isDead,
                has_collider = hasCollider,
                active = go.activeInHierarchy,
            });
        }

        // Recurse into children
        for (int i = 0; i < t.childCount; i++)
            WalkChildren(t.GetChild(i), playerPos, radius, results, seen, playerRootId);
    }

    /// <summary>
    /// Trigger the basic interaction on the nearest Item within radius.
    /// Uses IL2CPP reflection to call "Pickup" at runtime (no interop wrapper exists).
    /// params: { "radius": float }
    /// </summary>
    private void Interact(Dictionary<string, object?> p)
    {
        float radius = GetFloat(p, "radius", 3f);
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no player character"); return; }

            var playerPos = character.transform.position;
            var colliders = Physics.OverlapSphere(playerPos, radius);

            Item? nearestItem = null;
            float nearestDist = float.MaxValue;

            foreach (var col in colliders)
            {
                if (col == null) continue;
                var item = col.GetComponentInParent<Item>();
                if (item == null) continue;
                float dist = (col.transform.position - playerPos).magnitude;
                if (dist < nearestDist) { nearestDist = dist; nearestItem = item; }
            }

            if (nearestItem == null)
            {
                Plugin.Log.LogInfo($"[Interact] No item within {radius}u.");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "interact", success = false, reason = "nothing_nearby" });
                return;
            }

            bool ok = TryInvokeNoArgMethod(nearestItem, "Pickup");
            Plugin.Log.LogInfo($"[Interact] Pickup {nearestItem.name}: {(ok ? "ok" : "no method")}");
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "interact", success = ok, target = nearestItem.name });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Interact] error: {ex.Message}");
            SendError($"interact failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Take an item by name (as returned by scan_nearby).
    /// Uses IL2CPP reflection to call "Pickup" at runtime.
    /// params: { "name": string, "id": string }
    /// </summary>
    private void TakeItem(Dictionary<string, object?> p)
    {
        string targetName = GetString(p, "name");
        string targetId   = GetString(p, "id");
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no player character"); return; }

            var playerPos = character.transform.position;
            var colliders = Physics.OverlapSphere(playerPos, 10f);

            Item? found = null;
            float nearestDist = float.MaxValue;

            foreach (var col in colliders)
            {
                if (col == null) continue;
                var item = col.GetComponentInParent<Item>();
                if (item == null) continue;

                bool idMatch   = !string.IsNullOrEmpty(targetId)   && col.transform.root.gameObject.GetInstanceID().ToString() == targetId;
                bool nameMatch = !string.IsNullOrEmpty(targetName) && item.name.Contains(targetName, StringComparison.OrdinalIgnoreCase);

                if (!idMatch && !nameMatch) continue;

                float dist = (col.transform.position - playerPos).magnitude;
                if (dist < nearestDist) { nearestDist = dist; found = item; }
            }

            if (found == null)
            {
                Plugin.Log.LogInfo($"[TakeItem] '{targetName}' not found within 10u.");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "take_item", success = false, reason = "item_not_found" });
                return;
            }

            bool ok = TryInvokeNoArgMethod(found, "Pickup");
            Plugin.Log.LogInfo($"[TakeItem] Pickup {found.name}: {(ok ? "ok" : "no method")}");
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "take_item", success = ok, item = found.name });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[TakeItem] error: {ex.Message}");
            SendError($"take_item failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Use an item from the pouch by name (case-insensitive contains).
    /// params: { "name": string }
    /// </summary>
    private void UseItem(Dictionary<string, object?> p)
    {
        string targetName = GetString(p, "name");
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no player character"); return; }

            var pouch = character.Inventory?.Pouch;
            if (pouch == null) { SendError("pouch not accessible"); return; }

            var items = pouch.GetContainedItems();
            Item? found = null;
            foreach (var item in items)
            {
                if (item == null) continue;
                string displayName = item.DisplayName ?? item.name;
                if (displayName.Contains(targetName, StringComparison.OrdinalIgnoreCase) ||
                    item.name.Contains(targetName, StringComparison.OrdinalIgnoreCase))
                {
                    found = item;
                    break;
                }
            }

            if (found == null)
            {
                Plugin.Log.LogInfo($"[UseItem] '{targetName}' not found in pouch.");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "use_item", success = false, reason = "item_not_found" });
                return;
            }

            // Try item-level no-arg methods first
            bool ok = TryInvokeNoArgMethod(found, "Use");
            if (!ok) ok = TryInvokeNoArgMethod(found, "UseItem");
            if (!ok) ok = TryInvokeNoArgMethod(found, "OnUse");
            if (!ok) ok = TryInvokeNoArgMethod(found, "Perform");

            // Try character-level methods: character.UseItem(item) / character.Use(item)
            if (!ok)
            {
                ok = TryInvokeOneArgMethod(character, "UseItem", found);
                if (!ok) ok = TryInvokeOneArgMethod(character, "Use", found);
            }

            // Try Food component: food.OnEat(character) or similar
            if (!ok)
            {
                var foodComp = found.GetComponentInChildren<Food>();
                if (foodComp != null)
                {
                    ok = TryInvokeOneArgMethod(foodComp, "OnEat", character);
                    if (!ok) ok = TryInvokeOneArgMethod(foodComp, "Consume", character);
                    if (!ok) ok = TryInvokeNoArgMethod(foodComp, "Use");
                }
            }

            Plugin.Log.LogInfo($"[UseItem] Use {found.name}: {(ok ? "ok" : "no method found")}");
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "use_item", success = ok, item = found.name });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[UseItem] error: {ex.Message}");
            SendError($"use_item failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Trigger an interaction by UID, or the nearest InteractionActivator if UID is empty.
    /// params: { "uid": string }
    /// </summary>
    private void TriggerInteraction(Dictionary<string, object?> p)
    {
        string targetUid = GetString(p, "uid");
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no player character"); return; }

            var playerPos = character.transform.position;

            InteractionActivator? found = null;
            float nearestDist = float.MaxValue;

            var colliders = Physics.OverlapSphere(playerPos, 10f);
            foreach (var col in colliders)
            {
                if (col == null) continue;
                var root = col.transform.root.gameObject;
                var activator = root.GetComponentInChildren<InteractionActivator>();
                if (activator == null || !activator.gameObject.activeInHierarchy) continue;

                if (!string.IsNullOrEmpty(targetUid))
                {
                    if (!root.name.Equals(targetUid, StringComparison.OrdinalIgnoreCase)) continue;
                    found = activator;
                    break;
                }

                float dist = (root.transform.position - playerPos).magnitude;
                if (dist < nearestDist) { nearestDist = dist; found = activator; }
            }

            if (found == null)
            {
                Plugin.Log.LogInfo($"[TriggerInteraction] No matching activator found.");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "trigger_interaction", success = false, reason = "not_found" });
                return;
            }

            bool ok = TryInvokeNoArgMethod(found, "Interact");
            if (!ok) ok = TryInvokeNoArgMethod(found, "OnInteract");
            if (!ok) ok = TryInvokeNoArgMethod(found, "TriggerActivation");
            if (!ok) ok = TryInvokeNoArgMethod(found, "Activate");
            Plugin.Log.LogInfo($"[TriggerInteraction] Interact {found.name}: {(ok ? "ok" : "no method")}");
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "trigger_interaction", success = ok, target = found.name });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[TriggerInteraction] error: {ex.Message}");
            SendError($"trigger_interaction failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Equip an item from the pouch by name (case-insensitive contains).
    /// params: { "name": string }
    /// </summary>
    private void EquipItem(Dictionary<string, object?> p)
    {
        string targetName = GetString(p, "name");
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no player character"); return; }

            var pouch = character.Inventory?.Pouch;
            if (pouch == null) { SendError("pouch not accessible"); return; }

            var items = pouch.GetContainedItems();
            Item? found = null;
            foreach (var item in items)
            {
                if (item == null) continue;
                string displayName = item.DisplayName ?? item.name;
                if (displayName.Contains(targetName, StringComparison.OrdinalIgnoreCase) ||
                    item.name.Contains(targetName, StringComparison.OrdinalIgnoreCase))
                {
                    found = item;
                    break;
                }
            }

            if (found == null)
            {
                Plugin.Log.LogInfo($"[EquipItem] '{targetName}' not found in pouch.");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "equip_item", success = false, reason = "item_not_found" });
                return;
            }

            // Try Equip() on the item directly, then TryEquip, then EquipItem on character
            bool ok = TryInvokeNoArgMethod(found, "Equip");
            if (!ok) ok = TryInvokeNoArgMethod(found, "TryEquip");
            if (!ok)
            {
                // Try character.Inventory.Equipment.Equip(item) via reflection
                var equipComp = character.Inventory?.Equipment;
                if (equipComp != null)
                {
                    ok = TryInvokeOneArgMethod(equipComp, "Equip", found);
                    if (!ok) ok = TryInvokeOneArgMethod(equipComp, "EquipItem", found);
                }
            }
            Plugin.Log.LogInfo($"[EquipItem] Equip {found.name}: {(ok ? "ok" : "no method")}");
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "equip_item", success = ok, item = found.name });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[EquipItem] error: {ex.Message}");
            SendError($"equip_item failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Invoke a single-argument instance method via IL2CPP reflection.
    /// Returns true if the method was found and invoked.
    /// </summary>
    private static bool TryInvokeOneArgMethod(Il2CppSystem.Object obj, string methodName, Il2CppSystem.Object arg)
    {
        try
        {
            var methods = obj.GetIl2CppType().GetMethods(
                Il2CppSystem.Reflection.BindingFlags.Public |
                Il2CppSystem.Reflection.BindingFlags.Instance);
            foreach (var m in methods)
            {
                if (m.Name != methodName) continue;
                if (m.GetParameters().Count != 1) continue;
                m.Invoke(obj, new Il2CppSystem.Object[] { arg });
                return true;
            }
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Il2CppInvoke] {methodName} failed: {ex.Message}");
        }
        return false;
    }

    /// <summary>
    /// Invoke a zero-argument instance method via IL2CPP reflection.
    /// Returns true if the method was found and invoked.
    /// </summary>
    private static bool TryInvokeNoArgMethod(Il2CppSystem.Object obj, string methodName)
    {
        try
        {
            var methods = obj.GetIl2CppType().GetMethods(
                Il2CppSystem.Reflection.BindingFlags.Public |
                Il2CppSystem.Reflection.BindingFlags.Instance);
            foreach (var m in methods)
            {
                if (m.Name != methodName) continue;
                if (m.GetParameters().Count != 0) continue;
                m.Invoke(obj, null);
                return true;
            }
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[Il2CppInvoke] {methodName} failed: {ex.Message}");
        }
        return false;
    }

    // ── Menu navigation commands ─────────────────────────────────────────────

    private void MenuQueryState()
    {
        bool inMainMenu = MenuManager.Instance?.IsInMainMenuScene ?? false;

        var ms = UnityEngine.Object.FindObjectOfType<MainScreen>();
        bool inCharSelect = ms != null && ms.IsCharacterSelectionMenuDisplayed;

        var characters = new List<object>();
        if (inCharSelect && ms?.m_characterSelection != null)
        {
            var slots = ms.m_characterSelection.m_saveSlot;
            for (int i = 0; i < slots?.Count; i++)
            {
                var slot = slots[i];
                if (slot != null)
                    characters.Add(new { index = i, name = slot.CharacterName ?? "" });
            }
        }

        var nll = NetworkLevelLoader.Instance;
        bool isLoading   = nll?.IsGameplayLoading     ?? false;
        bool isLoadDone  = nll?.IsOverallLoadingDone  ?? false;
        bool allDone     = nll?.AllPlayerDoneLoading  ?? false;
        bool allReady    = nll?.AllPlayerReadyToContinue ?? false;

        string screen;
        if (isLoadDone || (!inMainMenu && !isLoading && !inCharSelect))
            screen = "in_game";
        else if (isLoading)
            screen = "loading";
        else if (inCharSelect)
            screen = "character_select";
        else if (inMainMenu)
            screen = "main_menu";
        else
            screen = "unknown";

        Plugin.Log.LogInfo($"[Menu] State query: screen={screen} chars={characters.Count} allDone={allDone} allReady={allReady}");
        _ = Plugin.WsServer!.SendAsync(new
        {
            type = "menu_state",
            screen,
            in_main_menu    = inMainMenu,
            in_char_select  = inCharSelect,
            is_loading      = isLoading,
            load_done       = isLoadDone,
            all_done_loading = allDone,
            all_ready        = allReady,
            characters,
        });
    }

    private void MenuPressContinue()
    {
        var ms = UnityEngine.Object.FindObjectOfType<MainScreen>();
        if (ms == null) { SendError("MainScreen not found"); return; }
        Plugin.Log.LogInfo("[Menu] Pressing Continue...");
        ms.OnContinueClicked();
        _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "menu_press_continue", success = true });
    }

    private void MenuSelectCharacter(Dictionary<string, object?> p)
    {
        string targetName = GetString(p, "name");
        var ms = UnityEngine.Object.FindObjectOfType<MainScreen>();
        var panel = ms?.m_characterSelection;
        if (panel == null) { SendError("No character panel visible"); return; }

        var slots = panel.m_saveSlot;
        int idx = 0; // default to first
        for (int i = 0; i < slots?.Count; i++)
        {
            if (slots[i]?.CharacterName?.Equals(targetName, System.StringComparison.OrdinalIgnoreCase) == true)
            {
                idx = i;
                break;
            }
        }
        Plugin.Log.LogInfo($"[Menu] Selecting character '{targetName}' at index {idx}");
        panel.OnCharacterClicked(idx);
        _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "menu_select_character", success = true, index = idx });
    }

    private void MenuSelectSave(Dictionary<string, object?> p)
    {
        int idx = (int)GetFloat(p, "index", 0f);
        var ms = UnityEngine.Object.FindObjectOfType<MainScreen>();
        var panel = ms?.m_characterSelection;
        if (panel == null) { SendError("No character panel for save select"); return; }
        Plugin.Log.LogInfo($"[Menu] Selecting save index {idx}");
        panel.OnSaveInstanceClicked(idx);
        _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "menu_select_save", success = true, index = idx });
    }

    private void MenuPressSpace()
    {
        var nll = NetworkLevelLoader.Instance;
        if (nll == null) { SendError("NetworkLevelLoader not found"); return; }
        Plugin.Log.LogInfo("[Menu] Skipping load prompt (ForceAllPlayersReady)...");
        try { nll.SetContinueAfterLoading(); } catch (Exception ex) { Plugin.Log.LogWarning($"SetContinueAfterLoading: {ex.Message}"); }
        try { nll.ForceAllPlayersReady(); }    catch (Exception ex) { Plugin.Log.LogWarning($"ForceAllPlayersReady: {ex.Message}"); }
        _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "menu_press_space", success = true });
    }

    /// <summary>
    /// Open a game menu panel via CharacterUI.
    /// params: { "menu": "inventory" | "skills" | "map" | "equipment" | "quest" }
    /// Falls back to trying common method names on CharacterUI via reflection.
    /// </summary>
    private void OpenMenu(Dictionary<string, object?> p)
    {
        string menu = GetString(p, "menu", "inventory").ToLowerInvariant();
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no character"); return; }

            // CharacterUI is the per-character HUD/menu controller
            var charUI = character.CharacterUI;
            if (charUI == null) { SendError("CharacterUI not found"); return; }

            bool ok = false;

            // Try menu-specific methods first, then generic show
            switch (menu)
            {
                case "inventory":
                case "bag":
                    ok = TryInvokeNoArgMethod(charUI, "ShowInventoryPanel");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "OpenInventoryPanel");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "ToggleInventory");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "ShowBag");
                    break;
                case "skills":
                case "abilities":
                    ok = TryInvokeNoArgMethod(charUI, "ShowSkillsPanel");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "OpenSkillPanel");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "ToggleSkillTree");
                    break;
                case "map":
                    ok = TryInvokeNoArgMethod(charUI, "ShowMap");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "OpenMap");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "ToggleMap");
                    break;
                case "equipment":
                case "gear":
                    ok = TryInvokeNoArgMethod(charUI, "ShowEquipmentPanel");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "OpenEquipmentPanel");
                    break;
                case "quest":
                case "journal":
                    ok = TryInvokeNoArgMethod(charUI, "ShowQuestPanel");
                    if (!ok) ok = TryInvokeNoArgMethod(charUI, "OpenQuestLog");
                    break;
            }

            // Log CharacterUI method names once for diagnostics
            LogCharacterUIMethods(charUI);

            Plugin.Log.LogInfo($"[OpenMenu] menu={menu}: {(ok ? "opened" : "no matching method")}");
            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "open_menu", success = ok, menu });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[OpenMenu] error: {ex.Message}");
            SendError($"open_menu failed: {ex.Message}");
        }
    }

    private static bool _charUIDumped = false;
    private static void LogCharacterUIMethods(Il2CppSystem.Object charUI)
    {
        if (_charUIDumped) return;
        _charUIDumped = true;
        try
        {
            var methods = charUI.GetIl2CppType().GetMethods(
                Il2CppSystem.Reflection.BindingFlags.Public |
                Il2CppSystem.Reflection.BindingFlags.Instance);
            Plugin.Log.LogInfo($"[CharacterUI] {methods.Count} public methods:");
            foreach (var m in methods)
                if (m.GetParameters().Count == 0)
                    Plugin.Log.LogInfo($"  {m.Name}()");
        }
        catch { }
    }

    private void CloseMenu()
    {
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no character"); return; }

            var charUI = character.CharacterUI;
            if (charUI == null) { SendError("CharacterUI not found"); return; }

            bool ok = TryInvokeNoArgMethod(charUI, "CloseAllMenus");
            if (!ok) ok = TryInvokeNoArgMethod(charUI, "HideAllPanels");
            if (!ok) ok = TryInvokeNoArgMethod(charUI, "CloseMenus");

            _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "close_menu", success = ok });
        }
        catch (Exception ex)
        {
            SendError($"close_menu failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Rotate the CharacterCamera so the player faces a world point.
    /// params: { "x": float, "y": float, "z": float }
    /// </summary>
    private void FacePoint(Dictionary<string, object?> p)
    {
        float tx = GetFloat(p, "x", 0f);
        float tz = GetFloat(p, "z", 0f);
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no character"); return; }

            var charPos = character.transform.position;
            float dx = tx - charPos.x;
            float dz = tz - charPos.z;
            if (Math.Abs(dx) < 0.01f && Math.Abs(dz) < 0.01f)
            {
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "face_point", success = false, reason = "too_close" });
                return;
            }

            // Compute world Y angle to face target (Unity: 0° = +Z, 90° = +X)
            float targetYaw = Mathf.Atan2(dx, dz) * Mathf.Rad2Deg;

            var charCams = CharacterCamera.CharCamList;
            if (charCams != null && charCams.Count > 0)
            {
                var cam = charCams[0];
                var euler = cam.transform.eulerAngles;
                cam.transform.eulerAngles = new Vector3(euler.x, targetYaw, euler.z);
                Plugin.Log.LogInfo($"[FacePoint] Rotated camera to yaw={targetYaw:F1}°");
                _ = Plugin.WsServer!.SendAsync(new { type = "ack", action = "face_point", success = true, yaw = targetYaw });
            }
            else
            {
                SendError("no camera found");
            }
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[FacePoint] error: {ex.Message}");
            SendError($"face_point failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Read the character's known skills from SkillKnowledge inventory.
    /// Returns a list of skill names and descriptions so Voyager can know what it's capable of.
    /// </summary>
    private void ReadSkills()
    {
        try
        {
            var character = CharacterManager.Instance?.GetFirstLocalCharacter();
            if (character == null) { SendError("no player character"); return; }

            var inv = character.Inventory;
            if (inv == null) { SendError("no inventory"); return; }

            var skillList = new List<object>();

            // SkillKnowledge holds learned skills — try casting to ItemContainer to iterate
            try
            {
                var sk = inv.SkillKnowledge;
                if (sk != null)
                {
                    // CharacterSkillKnowledge may extend BasicItemContainer — try the cast
                    var container = sk.TryCast<ItemContainer>();
                    Il2CppSystem.Collections.Generic.List<Item>? items = null;

                    if (container != null)
                    {
                        items = container.GetContainedItems();
                    }
                    else
                    {
                        // Fall back: iterate via reflection — find any IList<Item> field
                        var skType = (sk as Il2CppSystem.Object)!.GetIl2CppType();
                        foreach (var fname in new[] { "m_learnedItems", "m_skills", "m_learnedSkills", "LearnedSkills" })
                        {
                            var f = skType.GetField(fname,
                                Il2CppSystem.Reflection.BindingFlags.NonPublic |
                                Il2CppSystem.Reflection.BindingFlags.Public |
                                Il2CppSystem.Reflection.BindingFlags.Instance);
                            if (f == null) continue;
                            // Value is opaque — just log names; detailed parse skipped
                            Plugin.Log.LogInfo($"[ReadSkills] Found field {fname} — complex type, skipping");
                            break;
                        }
                    }

                    if (items != null)
                    {
                        foreach (var item in items)
                        {
                            if (item == null) continue;
                            string name = item.DisplayName ?? item.name ?? "";
                            string desc = "";
                            try
                            {
                                var type = item.GetIl2CppType();
                                foreach (var propName in new[] { "Description", "m_description", "GeneralDescription" })
                                {
                                    var prop = type.GetProperty(propName,
                                        Il2CppSystem.Reflection.BindingFlags.Public |
                                        Il2CppSystem.Reflection.BindingFlags.NonPublic |
                                        Il2CppSystem.Reflection.BindingFlags.Instance);
                                    if (prop != null)
                                    {
                                        var val = prop.GetValue(item, null);
                                        if (val != null) { desc = val.ToString() ?? ""; break; }
                                    }
                                }
                            }
                            catch { /* description read failed — skip */ }

                            if (!string.IsNullOrWhiteSpace(name))
                                skillList.Add(new { name, description = desc });
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Plugin.Log.LogWarning($"[ReadSkills] SkillKnowledge read failed: {ex.Message}");
            }

            Plugin.Log.LogInfo($"[ReadSkills] Found {skillList.Count} known skills.");
            _ = Plugin.WsServer!.SendAsync(new { type = "skills", skills = skillList });
        }
        catch (Exception ex)
        {
            Plugin.Log.LogWarning($"[ReadSkills] error: {ex.Message}");
            SendError($"read_skills failed: {ex.Message}");
        }
    }

    private void SendError(string reason)
    {
        _ = Plugin.WsServer!.SendAsync(new { type = "error", reason });
    }

    // ── Parameter helpers ────────────────────────────────────────────────────
    // JSON deserialization puts params into Dictionary<string, object?> where
    // values are JsonElement — Convert.ToSingle(JsonElement) throws.
    // These helpers unwrap JsonElement correctly for each type.

    private static float GetFloat(Dictionary<string, object?> p, string key, float def)
    {
        var val = p.GetValueOrDefault(key);
        if (val is JsonElement je) return je.TryGetSingle(out float f) ? f : def;
        if (val is null) return def;
        return Convert.ToSingle(val);
    }

    private static bool GetBool(Dictionary<string, object?> p, string key, bool def = false)
    {
        var val = p.GetValueOrDefault(key);
        if (val is JsonElement je && je.ValueKind == JsonValueKind.True)  return true;
        if (val is JsonElement je2 && je2.ValueKind == JsonValueKind.False) return false;
        if (val is bool b) return b;
        return def;
    }

    private static string GetString(Dictionary<string, object?> p, string key, string def = "")
    {
        var val = p.GetValueOrDefault(key);
        if (val is JsonElement je) return je.GetString() ?? def;
        return val as string ?? def;
    }
}

public class AgentCommand
{
    [JsonPropertyName("action")] public string Action { get; set; } = "";
    [JsonPropertyName("params")] public Dictionary<string, object?> Params { get; set; } = new();
}
