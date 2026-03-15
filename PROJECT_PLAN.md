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

- [ ] Build observation dashboard (web UI)
- [ ] Implement reward trend visualization
- [ ] Implement memory graph visualization
- [ ] Implement preference evolution tracking
- [ ] Build manual override interface
- [ ] Begin aesthetic discovery experiments
- [ ] Long-term behavior logging and analysis tools
- [ ] Ongoing: observe, tune, experiment

---

## Confidence levels

| Phase | Confidence | Notes |
|---|---|---|
| Phase 1-2 | ~90% | Proven patterns; BepInEx reverse-engineering is the main unknown |
| Phase 3-4 | ~85% | Well-understood systems adapted to a new game |
| Phase 5 | ~80% | Reward systems are well-studied; tuning for Outward is the variable |
| Phase 6 | ~70% | Sandboxed self-modification is doable but needs careful iteration |
| Phase 7 | ~40-50% | Genuinely experimental; results unpredictable (and that's the point) |

---

## Tech stack summary

| Component | Technology |
|---|---|
| Game mod | BepInEx 5.x, C#, HarmonyX |
| Communication | WebSocket (C# ↔ Python) |
| Orchestrator | Python 3.11+, asyncio |
| LLM (local) | Ollama (Llama 3 / Mistral) |
| LLM (cloud) | Claude API, OpenAI API, Gemini API |
| Skill storage | SQLite |
| Memory | ChromaDB |
| Goal persistence | JSON files |
| Dashboard | Web UI (TBD — likely React or simple HTML) |
| Dev tool | Claude Code |

---

## Open questions (to revisit)

- **Goal selection system** — deferred from question 4. How exactly should the automatic curriculum work? LLM proposes vs scoring system vs hybrid?
- **Combat granularity** — how fast does the rule engine need to tick for real-time Outward combat?
- **Outward's chat system internals** — needs reverse-engineering to confirm hookability

## Side topics / future considerations

- **LLM upgrade path** — Llama 3.1:8b is the current local model. Quality is noticeably limited for complex reasoning (strategy loop produced identical outputs for 25+ minutes on llama3.2). Consider upgrading to Claude API (claude-haiku-4-5 for cost-efficiency, claude-sonnet-4-6 for quality). The config already has a `claude` provider slot — just needs `ANTHROPIC_API_KEY` env var and `enabled: true`. Cloud APIs cost money but may be necessary for Tier 2 (self-modification, emergent goals) to function well.

- **Autonomous movement / suggestion model** — Player movement commands should eventually be treated as suggestions, not orders. The agent weighs them against its current health, goals, danger level, and accumulated preferences — then decides whether to comply, refuse, or do something else. A YOLO personality might comply even at low health; a cautious one won't. Config toggle `agent.autonomous_movement` controls this:
  - `false` (default/dev): movement commands execute directly — full player control
  - `true` (autonomous): commands route through the agent's decision layer

  The decision layer (to be built in Tier 2) will use: current state + active goals + reward/preference history + LLM reasoning → action or refusal with explanation. The `navigate_to` primitive stays unchanged; only the layer deciding *whether* to call it changes.

- **In-game knowledge acquisition** — The agent should learn about the game from the game itself, not from pre-programmed knowledge. Sources to tap:
  - **Tutorial messages** — hook into the tutorial/hint system in Assembly-CSharp to capture text as it appears
  - **On-screen messages** — notifications, status popups, system messages (item acquired, quest updated, etc.)
  - **Loading/transition screen tips** — Outward shows gameplay tips between scenes; capture and store these
  - **Quest text** — quest descriptions, objectives, NPC dialogue — rich source of world/mechanic knowledge
  - **Experimentation** — agent tries an action, observes the outcome, logs what it learned (e.g. "sprinting drains stamina", "crouching reduces detection")
  - **Character states** — enumerate all states the character can be in (crouching, sprinting, climbing, swimming, encumbered, burning, etc.) and learn their triggers and effects by observation
  - **Controls / keybindings** — read the game's input map (likely in `InputManager` or `KeyboardInputManager` in Assembly-CSharp, or from the options/help menus) so the agent understands what actions are available and how they map to inputs

  Implementation approach: C# mod hooks capture text events and push them to the agent as `game_message` WebSocket events. Agent stores them in the adventure journal and extracts structured knowledge (mechanic facts, state definitions, control mappings) into a dedicated knowledge base. Over time the agent builds its own understanding of game mechanics purely from observation.
