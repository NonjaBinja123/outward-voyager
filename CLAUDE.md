# CLAUDE.md — Outward Voyager

## What is this project?

An autonomous AI agent that plays Outward Definitive Edition (Steam/PC). It develops emergent curiosity-driven behavior, learns from experience, writes its own code, and communicates with players through in-game chat.

This is a research/passion project exploring emergent AI behavior. The agent should feel like an independent player, not a bot.

## Architecture (two tiers)

**Tier 1 (foundation):** BepInEx C# mod → WebSocket → Python orchestrator → LLM router → skill database → ChromaDB memory → goal system

**Tier 2 (experimental):** Reward system → emergent preferences → combat learning → self-modifying code sandbox → aesthetic discovery → observation dashboard

## Key technical details

- **Game:** Outward Definitive Edition, Unity 2020.3.26, modded via BepInEx 5.x (Mono branch)
- **Game mod:** C# BepInEx plugin hooking into Assembly-CSharp.dll via HarmonyX
- **Agent:** Python 3.11+, asyncio, WebSocket connection to game mod
- **LLM:** Ollama (local, always-available fallback) + Claude/OpenAI/Gemini APIs (configurable priority, auto-rotate)
- **Memory:** ChromaDB vector database for adventure journal
- **Skills:** SQLite database with structured entries (action type, parameters, conditions, tags, success rate)
- **Goals:** JSON files persisting across sessions. Session goals feed long-term ambitions.

## Design principles (follow these strictly)

1. **Agent decides for itself** — it considers player chat input but is never obligated to obey
2. **Emergent over programmed** — preferences develop from experience, not pre-configuration
3. **LLM sets strategy, rules execute** — LLM is called periodically for high-level planning; a rule engine handles moment-to-moment decisions
4. **Cautious in combat, bold in exploration** — minimize deaths, but experiment freely when safe
5. **Quiet unless spoken to** — agent does not narrate in chat unless addressed
6. **Self-modification is sandboxed** — agent-written code must pass validation before integration
7. **Failed skills get pruned** — don't keep broken or outdated skills, forget and move on
8. **Retry 3x then ask Josh** — on failure, try 3 different approaches, then escalate via in-game chat
9. **Verify results** — always check game state after an action to confirm success

## Project structure

```
outward-voyager/
├── CLAUDE.md                 # This file
├── PROJECT_PLAN.md           # Full architecture and task list
├── mod/                      # C# BepInEx mod (Visual Studio / Rider project)
│   ├── OutwardVoyager/
│   │   ├── Plugin.cs         # BepInEx plugin entry point
│   │   ├── GameStateReader.cs
│   │   ├── ActionExecutor.cs
│   │   ├── ChatHook.cs
│   │   └── WebSocketServer.cs
│   └── OutwardVoyager.csproj
├── agent/                    # Python agent
│   ├── main.py               # Entry point
│   ├── orchestrator.py       # Strategy loop + rule engine
│   ├── llm_router.py         # Multi-provider LLM management
│   ├── skills/
│   │   ├── database.py       # SQLite skill storage
│   │   ├── schema.py         # Skill data structures
│   │   └── composer.py       # Skill composition system
│   ├── memory/
│   │   ├── journal.py        # ChromaDB adventure journal
│   │   ├── mental_map.py     # Place familiarity tracking
│   │   └── goals.py          # Session + long-term goal system
│   ├── reward/               # Tier 2
│   │   ├── engine.py         # Reward signal processing
│   │   ├── novelty.py        # Novelty decay algorithm
│   │   └── preferences.py    # Emergent preference tracking
│   ├── sandbox/              # Tier 2
│   │   ├── executor.py       # Sandboxed code execution
│   │   └── validator.py      # Code validation pipeline
│   └── config.yaml           # LLM provider config, priority order, settings
└── dashboard/                # Tier 2 — web observation dashboard
    └── (TBD)
```

## Conventions

- **Python:** Use type hints. Use dataclasses or Pydantic for data structures. Async where possible.
- **C#:** Follow standard Unity/BepInEx patterns. Use HarmonyX for patching.
- **Config:** YAML for agent configuration. JSON for persisted state (goals, mental map).
- **Logging:** Use Python's logging module. Log all LLM calls, actions, rewards, and self-modifications.
- **Git:** Commit frequently — treat every commit as a save state. Commit whenever a meaningful chunk of work is done, even if incomplete. This lets Josh roll back easily and lets Claude check previous working state. Use conventional commit prefixes (feat/fix/refactor/docs/chore). Keep mod/ and agent/ changes in separate commits when possible, but don't let that block committing.

## Current phase

Phase 1 — BepInEx Foundation. Setting up the game mod and establishing communication with the Python agent.

## Developer

Josh — intermediate developer, comfortable with C# and Python, new to BepInEx modding. Using Claude Code as primary development tool.
