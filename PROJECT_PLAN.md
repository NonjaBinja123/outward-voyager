# Outward Voyager — Emergent AI Research Platform

## Project overview

An autonomous AI agent that plays Outward Definitive Edition, develops emergent curiosity-driven behavior, and communicates with players through in-game chat. This is a research/passion project exploring the boundaries of what AI agents can become.

## Core principles

- **Autonomy over obedience** — the agent considers player input but decides for itself
- **Emergent over programmed** — preferences, curiosity, and appreciation develop through experience, not pre-configuration
- **Incremental toward ambitious** — Tier 1 delivers a working agent; Tier 2 pushes into experimental territory
- **Self-modification within safety** — the agent can write its own code, validated in a sandbox before integration

---

## Architecture

### Tier 1 — Solid foundation

#### Layer 1: Game interface (BepInEx C# mod)

| Component | Purpose |
|---|---|
| Game state reader | Extracts HP, stamina, position, inventory, nearby entities, environment data from Outward via Assembly-CSharp.dll hooks |
| Action executor | Sends commands into the game: move to point, attack, use item, interact with NPC, craft, dodge |
| Chat hook | Reads incoming in-game chat messages and sends messages as the agent character |
| WebSocket server | Exposes all game state and accepts action commands from the Python orchestrator |

**Tech:** C#, BepInEx 5.x, Unity 2020.3.26, HarmonyX patching, WebSocket (websocket-sharp or similar)

#### Layer 2: Python orchestrator

| Component | Purpose |
|---|---|
| Strategy loop | Periodically calls the LLM to set high-level strategy and goals; runs every N seconds (configurable) |
| Rule engine | Executes moment-to-moment decisions between LLM calls (movement, combat reactions, item use) based on current strategy |
| Chat handler | Processes incoming player messages, decides relevance, optionally passes to LLM for response |
| LLM router | Manages provider priority, auto-rotates on rate limit/error, manual override, Ollama as always-available fallback |

**Tech:** Python 3.11+, asyncio, websockets library

#### Layer 3: AI brain

| Component | Purpose |
|---|---|
| Skill database | Structured storage of learned action sequences with searchable tags (action type, context, success rate, parameters) |
| Retry engine | On action failure: retry up to 3 times with LLM-refined approach, then ask Josh via in-game chat |
| Self-verification | After executing an action, checks game state to confirm the intended outcome actually happened |
| Skill composition | Combines simple skills into complex routines; agent experiments with new combinations autonomously |

**Tech:** SQLite for skill storage, JSON schema for skill definitions

#### Layer 4: Memory and journal

| Component | Purpose |
|---|---|
| Adventure journal | Stores experiences with context, sentiment, and importance score in a vector database for semantic recall |
| Mental map | Loose tracking of places visited, familiarity scores, what was found there |
| Goal system | Session goals (short-term) that feed into long-term ambitions persisting across play sessions |

**Tech:** ChromaDB (vector database), JSON for goal persistence

#### LLM router

| Provider | Role |
|---|---|
| Ollama (local) | Always-available fallback; default for frequent/fast calls |
| Claude API | Cloud provider (configurable priority) |
| OpenAI API | Cloud provider (configurable priority) |
| Gemini API | Cloud provider (configurable priority) |

**Behavior:** Configurable priority order. Auto-rotates on rate limit, error, or token exhaustion. Manual override via config or chat command. Falls back to Ollama when all cloud providers are exhausted.

---

### Tier 2 — Experimental research layer

#### Reward system

- Every in-game event generates a reward signal (positive, negative, or neutral)
- Novelty bonus: new experiences reward more; familiar ones decay over time
- Strong experiences (very positive or very negative) persist longer in memory
- Reward history drives approach/avoid behavior for future decisions

#### Emergent preferences

- No pre-programmed likes/dislikes
- Mild preferences develop organically from accumulated reward history
- Agent may gravitate toward certain areas, enemy types, items, or activities
- Preferences influence goal selection but don't override survival instincts

#### Combat learning

- Track outcome of every combat tactic (dodge vs block, which attacks work on which enemies)
- Refine tactics over time based on success/failure rates
- Cautious in combat (minimize deaths), bold in exploration

#### Self-modifying code sandbox

- Agent can write new Python functions/sub-modules
- New code is tested in an isolated sandbox environment
- Validated code gets integrated into the agent's runtime
- Failed or outdated code gets pruned
- All self-modifications are logged for research observation

#### Aesthetic discovery (experimental)

- Reward signals from environmental variety, novel combinations, patterns
- If rewarding patterns emerge (sound combinations, visual environments), the agent may seek them out
- This is genuinely experimental — results are unpredictable and that's the point

#### Observation dashboard

- Web-based dashboard showing: reward trends over time, memory graph, preference evolution, skill library growth, self-modification history
- Manual override interface (only used when agent is stuck/broken)
- Long-term behavior logging for research analysis

---

## Phased build plan

### Phase 1 — BepInEx foundation

- [ ] Set up BepInEx dev environment for Outward Definitive Edition
- [ ] Decompile Assembly-CSharp.dll and map out key game classes
- [ ] Create base C# plugin that reads game state (HP, stamina, position, inventory)
- [ ] Hook into nearby entity detection (NPCs, enemies, items)
- [ ] Hook into in-game chat system (read incoming, send outgoing)
- [ ] Expose game state over local WebSocket
- [ ] Build Python WebSocket client and verify two-way communication
- [ ] Test: Python receives game state and sends a movement command that executes

### Phase 2 — Basic autonomy

- [ ] Implement action executor in C# (move to point, basic attack, use item, interact, dodge)
- [ ] Build Python rule engine for moment-to-moment execution
- [ ] Set up Ollama with a capable local model (Llama 3 / Mistral recommended)
- [ ] Build LLM router with provider config, auto-rotate, manual override
- [ ] Implement strategy loop (LLM sets plan → rules execute → repeat)
- [ ] Test: agent can walk around, pick up items, and respond to basic combat

### Phase 3 — Brain and skills

- [ ] Design skill schema (action type, parameters, conditions, tags, success rate)
- [ ] Set up SQLite skill database
- [ ] Create starter skill set (basic movement, looting, simple combat, resting)
- [ ] Implement iterative prompting (retry 3x with LLM feedback → ask Josh)
- [ ] Implement self-verification (check game state after actions)
- [ ] Build skill composition system (combine skills into routines)
- [ ] Test: agent can execute multi-step plans and recover from failures

### Phase 4 — Memory and goals

- [ ] Set up ChromaDB for adventure journal
- [ ] Implement experience storage (event + context + sentiment + importance)
- [ ] Build loose mental map (places visited, familiarity)
- [ ] Create goal system (session goals → long-term ambitions)
- [ ] Implement goal persistence across sessions
- [ ] Build chat personality (speaks when spoken to, independent decisions)
- [ ] Test: agent remembers past events and references them when relevant

Social memory (Stretch Goal 1 foundation):
- [ ] Design unified User identity model (in-game players + dashboard viewers share the same system)
- [ ] Create `social.db` SQLite with `users` and `relationship_traits` tables (see Data Schemas section)
- [ ] Create `social_interactions` ChromaDB collection (separate from `journal` — different retrieval patterns)
- [ ] Build `SocialMemoryManager` (`agent/social/memory.py`): write interaction records, retrieve relationship context for LLM prompt injection
- [ ] Build `RelationshipEngine` (`agent/social/relationships.py`): update disposition/trust scores after each interaction; infer traits via periodic LLM re-evaluation (not on every message — every N interactions)
- [ ] Extend `ChatHandler` to look up/create user record before processing any message
- [ ] Inject relationship context into LLM system prompt: "You've spoken with [name] 14 times. Disposition: cautious. Traits: helpful, persistent."
- [ ] Test: agent demonstrably responds with different tone to a high-trust vs low-trust user

### Phase 5 — Reward system (Tier 2 begins)

- [ ] Design reward signal schema (event type, valence, intensity, novelty score)
- [ ] Implement novelty decay algorithm
- [ ] Build experience-strength persistence (strong memories last longer)
- [ ] Implement approach/avoid behavior driven by reward history
- [ ] Build combat learning system (tactic outcome tracking + refinement)
- [ ] Wire reward signals into goal selection
- [ ] Test: agent demonstrably avoids negative experiences and seeks positive ones

### Phase 6 — Self-modification

- [ ] Build code sandbox (isolated Python environment for agent-written code)
- [ ] Implement validation pipeline (syntax check → unit test → integration test)
- [ ] Allow agent to propose new sub-routines from experience
- [ ] Implement code pruning (remove failed/outdated self-written code)
- [ ] Build emergent preference tracking (what does the agent gravitate toward?)
- [ ] Log all self-modifications for research
- [ ] Test: agent creates at least one novel routine that works

### Phase 7 — Emergent behavior and dashboard

- [ ] Begin aesthetic discovery experiments
- [ ] Long-term behavior logging and analysis tools
- [ ] Ongoing: observe, tune, experiment

Dashboard build (Stretch Goal 1):
- [ ] Build FastAPI backend (`dashboard/backend/app.py`):
    - WebSocket relay: pushes agent state to connected browser clients (1 Hz normal, 5 Hz during combat)
    - REST: `GET /state`, `POST /chat`, `GET /relationships` (admin view)
    - Token auth middleware: all routes require shared secret (generated on first run, stored in `config.yaml`)
    - Identity resolution: extracts `display_name` from request, creates/retrieves user record via `SocialMemoryManager`
- [ ] Build frontend (`dashboard/frontend/index.html` — vanilla HTML/JS, no build step, no npm):
    - Prompts for display name on first load; persists in localStorage
    - Live panels: agent status, current goal, reward trend sparkline, recent memories, active skills, chat feed
    - Chat input → `POST /chat` → agent's unified chat pipeline (advisory, not commanding)
- [ ] Build `IdentityManager` (`dashboard/backend/identity.py`):
    - Assigns stable UUID on first connection keyed to display_name + IP hash (hash not stored)
    - Returns identity token to client stored in localStorage; survives page refresh
- [ ] Build `CloudflareTunnelManager` (`dashboard/tunnel/manager.py`):
    - Optionally spawns `cloudflared tunnel --url http://localhost:<port>` as subprocess
    - Parses stdout to extract public `.trycloudflare.com` URL; prints it at startup
    - Entirely opt-in via `config.yaml`: `dashboard.enable_public_sharing: false`
    - Optional: named persistent tunnel if `cloudflare_tunnel_token` is set in config
- [ ] Wire `POST /chat` → `UnifiedChatHandler` in orchestrator (same pipeline as in-game chat — agent considers but decides for itself)
- [ ] Implement reward trend, memory graph, and preference evolution visualizations
- [ ] Build manual override interface (for use only when agent is stuck/broken)
- [ ] Test: remote user sends message through public tunnel URL; agent considers it; relationship record created

### Phase 8 — Standalone Windows .exe deployer

**Confidence: ~75%** — Individual components (Inno Setup, embedded Python, Ollama silent install) are all well-documented. Integration complexity, Steam path detection, and DPAPI key encryption require careful testing on clean VMs.

Ships the agent as a fully self-contained Windows installer. A friend with Outward runs one `.exe` and gets a working agent with no CLI, no PATH setup, and no technical knowledge required. All dependencies (Python runtime, Ollama, BepInEx mod, base LLM model) are bundled. Operates fully offline after install; internet is optional and only enhances the cloud LLM options.

**Build tools:**
- Installer: **Inno Setup 6.x** — free; single `.exe` output; readable Pascal scripting; native custom wizard pages; no runtime dependency; standard choice for indie Windows software
- System tray launcher: **C# WinForms** (`NotifyIcon`) — instant startup, .NET 4.8 present on all Win10/11, manages Python child process, no extra runtime
- API key encryption: **Windows DPAPI** via a small bundled C# helper (`KeyWriter.exe`), called from Inno Setup via named pipe (keys never appear in process list or env vars)

**What the installer bundles:**
- Python 3.11 embeddable ZIP (no system Python required)
- Pre-built Python dependency wheels (no internet pip install)
- BepInEx 5.x Mono ZIP
- `OutwardVoyager.dll` + `VoyagerBridge.dll`
- Ollama Windows installer (silent `/S` flag)
- Llama 3.2 3B GGUF (~2 GB) as the default offline base model
- Agent snapshot: ChromaDB data dir, `agent.db`, `social.db`, `goals/`, `preferences/`
- `VoyagerLauncher.exe` (system tray app, compiled separately)

**Installer wizard flow (no CLI, no PATH, GUI only):**
1. Detect Outward installation via Steam registry → `libraryfolders.vdf` scan → manual browse fallback
2. Extract BepInEx into game directory; copy `OutwardVoyager.dll` + `VoyagerBridge.dll` to `BepInEx/plugins/`
3. Extract Python 3.11 embeddable runtime to `%LOCALAPPDATA%\OutwardVoyager\python\`
4. Install Python dependencies using extracted Python against bundled wheels (fully offline)
5. Silent Ollama install; verify `ollama.exe` is present
6. GPU VRAM detection via WMI (`Win32_VideoController.AdapterRAM`); model recommendation by tier:
   - < 4 GB or CPU only → Llama 3.2 1B; 4–6 GB → Llama 3.2 3B; 6–10 GB → Llama 3.1 8B; 10 GB+ → Llama 3.1 8B Q8
   - Custom wizard page: shows model name, VRAM requirement, download size; "Download Now" or "Use CPU / Skip"
   - Checks `ollama list` first to skip download if a suitable model is already present
7. API key entry wizard page (Claude, OpenAI, Gemini — all labeled optional):
   - Keys encrypted via DPAPI to `%LOCALAPPDATA%\OutwardVoyager\keys.enc`; never written to env vars or PATH
8. Extract agent snapshot to `%LOCALAPPDATA%\OutwardVoyager\data\`
9. Create desktop shortcut → `VoyagerLauncher.exe`; complete

**`VoyagerLauncher.exe` (system tray app):**
- `NotifyIcon` context menu: Start Agent, Stop Agent, Open Dashboard, View Log, Export Snapshot, Exit
- Manages Python process lifecycle; captures stdout/stderr to scrollable log window
- "Open Dashboard" opens `http://localhost:<port>` in default browser
- "Export Snapshot" → invokes `agent/tools/export.py` → opens output folder in Explorer

**`voyager export` command (`agent/tools/export.py`):**
- Collects: ChromaDB data directory, `agent.db`, `social.db`, `goals/`, `preferences/`, `config.yaml` (API keys stripped, re-prompted on install)
- Produces: `voyager_snapshot_YYYYMMDD.zip` + `export_manifest.json`
- Developer feature: tray launcher can invoke `iscc.exe` (Inno Setup compiler) with the snapshot zip to produce a new redistributable installer

**Tasks:**
- [ ] Write `installer/setup.iss` (Inno Setup script: all embedded resources, wizard pages, extraction logic)
- [ ] Implement Steam path detection (Pascal script: registry → `libraryfolders.vdf` → browse dialog fallback)
- [ ] Implement BepInEx extraction and plugin DLL copy
- [ ] Implement Python runtime extraction and offline wheels install
- [ ] Implement Ollama silent install + post-install verification
- [ ] Implement GPU VRAM detection via WMI in Pascal script
- [ ] Build GPU model recommendation custom wizard page
- [ ] Implement model download progress page (`ollama pull` in Inno progress page; skip if already present)
- [ ] Build API key entry custom wizard page (input fields, show/hide toggle)
- [ ] Build `KeyWriter.exe` (C# DPAPI helper; receives keys via named pipe, not command line)
- [ ] Implement agent snapshot extraction step
- [ ] Build `VoyagerLauncher.exe` (C# WinForms: `NotifyIcon`, process manager, log window)
- [ ] Implement `agent/tools/export.py` (`voyager export` command)
- [ ] Test: clean Windows 10 VM; run installer end-to-end; launch game; agent connects and plays using snapshot state

---

### Phase 9 — Cross-game portability + experience transfer

**Confidence: ~65%** — Protocol design and migration tooling are well-understood engineering. Whether cross-game skill and personality transfer is *meaningfully useful* across different game mechanical vocabularies is an open research question — results are unpredictable, and that's part of the point.

**Architecture: VoyagerBridge + thin game adapters**

```
Agent (Python) ←→ Universal WebSocket protocol (PROTOCOL.md)
                           ↕
              VoyagerBridge.dll  (shared C# BepInEx DLL, versioned independently)
                           ↕
              [GameName]Adapter.dll  (thin, game-specific HarmonyX hooks)
                           ↕
              Game internals
```

The agent speaks the universal protocol always and never knows what specific game is on the other side. The `adapter_info` message (sent by every adapter on connect) declares what the game supports; the agent adapts its strategy prompts accordingly. For non-Unity games (future): a separate bridge binary implementing the same protocol spec. The spec is the boundary — not a shared binary.

**Universal WebSocket message envelope (all messages):**
```json
{
  "voyager_protocol": "1.0",
  "message_id": "<uuid>",
  "timestamp_utc": "<ISO8601>",
  "message_type": "<type>",
  "game_id": "outward_definitive",
  "payload": { ... }
}
```

**Message types:**

| Type | Direction | Description |
|---|---|---|
| `adapter_info` | adapter → agent | Sent on connect; declares game ID, supported actions, capabilities |
| `game_state` | adapter → agent | Periodic push of full game state |
| `action_command` | agent → adapter | Agent requests a game action |
| `action_result` | adapter → agent | Outcome of last action command |
| `chat_event` | adapter → agent | Incoming player chat message |
| `agent_chat_send` | agent → adapter | Agent sends a chat message |
| `world_event` | adapter → agent | Game event (death, item spawned, location entered, etc.) |
| `heartbeat` | bidirectional | Keep-alive |

**`adapter_info` payload example:**
```json
{
  "adapter_name": "OutwardAdapter",
  "adapter_version": "1.0.0",
  "game_id": "outward_definitive",
  "game_display_name": "Outward Definitive Edition",
  "supported_actions": ["move_to", "attack_target", "use_item", "equip_item", "interact_entity", "dodge", "rest", "craft_item", "send_chat", "loot_target"],
  "supported_world_events": ["entity_died", "item_spawned", "location_entered", "location_exited", "weather_changed", "death", "quest_updated"],
  "capabilities": { "has_mana": true, "has_stamina": true, "has_crafting": true, "has_quests": true }
}
```

**`game_state` core payload fields:** `agent` (health, stamina, mana, status_effects, position, is_in_combat), `environment` (location_id, location_display_name, weather, time_of_day, is_indoors), `inventory` (equipped, backpack, currency, carry_weight), `nearby_entities` (entity_id, display_name, entity_type, health_fraction, distance, is_hostile), `quest_state`

**Universal action types:** `move_to`, `attack_target`, `use_item`, `equip_item`, `unequip_item`, `interact_entity`, `dodge`, `block`, `jump`, `rest`, `craft_item`, `send_chat`, `loot_target`, `drop_item`. Adapters that cannot support a given action return `action_result.status: "unsupported"`.

**Universal world event subtypes:** `entity_died`, `item_spawned`, `item_despawned`, `location_entered`, `location_exited`, `weather_changed`, `time_changed`, `quest_updated`, `level_up`, `death`, `game_saved`, `game_loaded`

**Cross-game skill tagging (SQLite additions):**
```sql
ALTER TABLE skills ADD COLUMN game_scope TEXT NOT NULL DEFAULT 'game_specific';
  -- 'game_specific' | 'cross_game' | 'archived'
ALTER TABLE skills ADD COLUMN source_game_id TEXT;
  -- e.g. 'outward_definitive'; NULL for skills created before portability phase
```
Skill composer always loads `cross_game` skills; loads `game_specific` only if `source_game_id` matches current connected `game_id`; never loads `archived`. Initial cross-game skill tags: `navigation`, `social_interaction`, `resource_gathering`, `threat_assessment`, `resting`, `inventory_management`.

**ChromaDB provenance tagging:** All journal and social_interactions documents include `game_id` and `game_display_name` metadata at write time. LLM context injection references provenance: *"In Outward, you once encountered a bandit camp near a river and found it rewarding."*

**`voyage migrate` command (`agent/tools/migrate.py`):**

Export side collects:
- Personality profile (preferences + reward engine state)
- All ChromaDB documents from `journal` and `social_interactions` (provenance metadata preserved)
- Skills where `game_scope = 'cross_game'`; game-specific skills marked `archived` in source DB (not deleted)
- Relationship profiles (`users` + `relationship_traits` tables, full export)
- Long-term goals from `goals/` JSON
- NOT exported: mental map (resets for new game), session goals

Import side:
- Inserts ChromaDB docs into new instance (additive; prior provenance metadata intact)
- Inserts cross-game skills into new SQLite
- Merges relationship profiles (user_id continuity preserved across games)
- Sets `portability.has_prior_life: true`, `prior_game_ids: ["outward_definitive"]` in config
- LLM system prompt injection when `has_prior_life: true`: *"You have played other games before this one. You carry memories and relationships from your time in Outward Definitive Edition. You can reference those experiences when relevant."*

**Tasks:**
- [ ] Write `PROTOCOL.md` — full universal WebSocket spec (all message types, field definitions, adapter compliance checklist, versioning rules)
- [ ] Create `mod/VoyagerBridge/` C# project: WebSocket server, message envelope handling, `IGameAdapter` interface definition, heartbeat, reconnect logic
- [ ] Create `mod/OutwardAdapter/` C# project: implements `IGameAdapter`; moves all Outward-specific hooks out of current `Plugin.cs`/`GameStateReader.cs`/`ActionExecutor.cs`/`ChatHook.cs` into adapter
- [ ] Create `agent/protocol/` module: `adapter.py` (parse/validate universal messages), `registry.py` (track connected game from `adapter_info`), `dispatcher.py` (route messages to orchestrator)
- [ ] Add `game_scope` and `source_game_id` columns to skills SQLite schema and `agent/skills/schema.py` dataclass
- [ ] Update skill composer to filter by `game_scope` + `source_game_id`
- [ ] Add `game_id` + `game_display_name` metadata to all ChromaDB write operations in `journal.py` and `social/memory.py`
- [ ] Implement `agent/tools/migrate.py` (export side + import side)
- [ ] Inject prior-life context into LLM system prompt when `has_prior_life` is true
- [ ] Write adapter compliance test suite (Python mock adapter; validates all message types round-trip)
- [ ] Test: run `voyage migrate` from Outward session to a mock second game; verify agent references prior experiences in LLM response generation

---

## Data schemas

### `social.db` — `users` table

```sql
CREATE TABLE users (
  user_id            TEXT PRIMARY KEY,  -- UUID, stable across sessions
  display_name       TEXT NOT NULL,
  source             TEXT NOT NULL,     -- 'ingame' | 'dashboard'
  first_seen_at      TEXT NOT NULL,     -- ISO8601
  last_seen_at       TEXT NOT NULL,
  trust_level        REAL NOT NULL DEFAULT 0.5,   -- 0.0–1.0
  disposition        REAL NOT NULL DEFAULT 0.0,   -- -1.0 (dislike) to 1.0 (like)
  interaction_count  INTEGER NOT NULL DEFAULT 0,
  notes              TEXT               -- LLM-written free-form summary of this person
);
```

### `social.db` — `relationship_traits` table

```sql
CREATE TABLE relationship_traits (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id             TEXT NOT NULL REFERENCES users(user_id),
  trait               TEXT NOT NULL,       -- e.g. "swears a lot", "always helpful", "confrontational"
  confidence          REAL NOT NULL,       -- 0.0–1.0; decays if not reinforced over time
  first_inferred_at   TEXT NOT NULL,       -- ISO8601
  last_reinforced_at  TEXT NOT NULL        -- ISO8601
);
```

### ChromaDB — `social_interactions` collection

Kept separate from `journal` because social dialogue has a different retrieval pattern than game event narrative. Mixing them degrades query precision.

Metadata fields per document: `user_id`, `display_name`, `source` (`ingame`|`dashboard`), `game_id`, `sentiment` (`positive`|`neutral`|`negative`), `interaction_type`, `trust_delta`, `disposition_delta`, `session_id`

Document text: LLM-written natural language summary of the interaction written at storage time. Example: *"Josh suggested exploring the northern cave. I decided to follow the suggestion. The cave contained useful loot, which reinforced my trust in Josh's judgment."*

### `config.yaml` additions

```yaml
dashboard:
  enabled: true
  port: 8765
  secret_token: "<generated on first run>"
  enable_public_sharing: false
  cloudflare_tunnel_token: null     # optional: named persistent tunnel

social:
  disposition_decay_rate: 0.001     # per session, drift toward neutral
  trust_decay_rate: 0.0005
  trait_confidence_decay_rate: 0.01
  significant_interaction_threshold: 0.3  # sentiment magnitude to persist to ChromaDB

portability:
  current_game_id: "outward_definitive"
  has_prior_life: false
  prior_game_ids: []
```

---

## Confidence levels

| Phase | Confidence | Notes |
|---|---|---|
| Phase 1-2 | ~90% | Proven patterns; BepInEx reverse-engineering is the main unknown |
| Phase 3-4 | ~85% | Phase 4 social memory adds complexity but uses well-understood patterns |
| Phase 5 | ~80% | Reward systems are well-studied; tuning for Outward is the variable |
| Phase 6 | ~70% | Sandboxed self-modification is doable but needs careful iteration |
| Phase 7 | ~60% | Dashboard now concrete (FastAPI + vanilla JS + cloudflared); higher confidence than TBD |
| Phase 8 | ~75% | Inno Setup well-documented; GPU detection + DPAPI key storage are the trickiest pieces |
| Phase 9 | ~65% | Protocol design is solid; whether experience transfer is meaningfully useful is a research question |

---

## Tech stack summary

| Component | Technology |
|---|---|
| Game mod (Outward) | BepInEx 5.x, C#, HarmonyX — `OutwardAdapter.dll` |
| Universal mod bridge | `VoyagerBridge.dll` — shared C# BepInEx DLL, versioned independently |
| Communication | Universal WebSocket protocol (see `PROTOCOL.md`) |
| Orchestrator | Python 3.11+, asyncio |
| LLM (local) | Ollama (Llama 3 / Mistral) |
| LLM (cloud) | Claude API, OpenAI API, Gemini API |
| Skill storage | SQLite (`agent.db`) |
| Memory | ChromaDB |
| Social memory | SQLite (`social.db`) + ChromaDB `social_interactions` collection |
| Goal persistence | JSON files |
| Dashboard backend | FastAPI (Python) |
| Dashboard frontend | Vanilla HTML/JS (no build step) |
| Internet tunneling | cloudflared (opt-in) |
| Dashboard auth | Shared secret token in URL/header |
| Windows installer | Inno Setup 6.x |
| System tray launcher | C# WinForms (NotifyIcon) |
| API key encryption | Windows DPAPI via C# helper (`KeyWriter.exe`) |
| Cross-game migration | `voyage migrate` Python CLI |
| Dev tool | Claude Code |

---

## Open questions (to revisit)

- **Goal selection system** — deferred from question 4. How exactly should the automatic curriculum work? LLM proposes vs scoring system vs hybrid?
- **Combat granularity** — how fast does the rule engine need to tick for real-time Outward combat?
- **Outward's chat system internals** — needs reverse-engineering to confirm hookability
- **Disposition scoring algorithm** — delta rule per interaction (fast, low-latency) vs LLM-scored interaction quality (richer but adds latency to every message). Leaning toward: delta rule for real-time updates, LLM re-evaluates the full trait list every N interactions.
- **Social memory significance threshold** — not every chat message should be persisted to ChromaDB. Candidate triggers: sentiment magnitude exceeds `significant_interaction_threshold`, agent changes its current plan based on the user's input, or the user expresses strong positive/negative emotion. To be tuned in Phase 4.
- **VoyagerBridge versioning** — when the universal protocol evolves (new message types, field changes), how are older adapters handled? Proposal: `voyager_protocol` major version in envelope; bridge rejects connections from adapters with incompatible major version and logs a clear error.
- **Bundled Ollama model check** — installer should parse `ollama list` output before downloading to skip redundant downloads on machines where Ollama is already installed with a suitable model.
- **`voyage migrate` conflict resolution** — if the target game instance already has relationship profiles (partial install or prior migration), how are duplicate `user_id` records merged? Proposal: last-write-wins on structured scores; ChromaDB interaction history is additive (both sets of documents kept, provenance metadata distinguishes them).
