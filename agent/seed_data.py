"""
Seed the agent's databases with starter skills and initial goals.

Run once: py seed_data.py
Safe to re-run — uses upsert for skills, skips existing goals.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from memory.goals import Goal, GoalSystem
from skills.database import SkillDatabase
from skills.schema import Skill


def load_config() -> dict:
    with open(Path(__file__).parent / "config.yaml") as f:
        return yaml.safe_load(f)


STARTER_SKILLS: list[Skill] = [
    # ── Exploration ────────────────────────────────────────────────────────
    Skill(
        id=None, name="scan_area", action_type="scan_nearby",
        parameters={"radius": 30.0},
        preconditions={},
        tags=["explore", "look_around", "scan"],
        description="Scan the surrounding area for objects and characters.",
    ),
    Skill(
        id=None, name="look_around_close", action_type="scan_nearby",
        parameters={"radius": 10.0},
        preconditions={},
        tags=["explore", "look_around"],
        description="Scan nearby area (10u) for items or threats.",
    ),

    # ── Navigation ─────────────────────────────────────────────────────────
    Skill(
        id=None, name="get_state", action_type="get_state",
        parameters={},
        preconditions={},
        tags=["status", "check", "state"],
        description="Request a full game state update from the mod.",
    ),

    # ── Item interaction ───────────────────────────────────────────────────
    Skill(
        id=None, name="pickup_nearby", action_type="interact",
        parameters={"radius": 3.0},
        preconditions={},
        tags=["loot", "gather", "item", "pickup"],
        description="Pick up the nearest item within 3 units.",
    ),
    Skill(
        id=None, name="pickup_reach", action_type="interact",
        parameters={"radius": 5.0},
        preconditions={},
        tags=["loot", "gather", "item", "pickup"],
        description="Pick up the nearest item within 5 units.",
    ),

    # ── Rest / wait ────────────────────────────────────────────────────────
    Skill(
        id=None, name="wait", action_type="wait",
        parameters={"seconds": 3},
        preconditions={},
        tags=["rest", "wait", "idle", "low_health"],
        description="Wait in place briefly (resting or observing).",
    ),

    # ── Chat ───────────────────────────────────────────────────────────────
    Skill(
        id=None, name="report_status", action_type="say",
        parameters={"message": "I'm exploring and learning my surroundings."},
        preconditions={},
        tags=["chat", "report", "status"],
        description="Say a status update in chat.",
    ),
]

STARTER_GOALS = [
    Goal(
        id="explore_starting_area",
        description="Explore and map out the starting area",
        priority=7,
        tags=["exploration", "early_game"],
    ),
    Goal(
        id="find_food_water",
        description="Find food and water to stay alive",
        priority=8,
        tags=["survival", "early_game"],
    ),
    Goal(
        id="learn_world",
        description="Observe and understand the game world through scanning and moving around",
        priority=5,
        tags=["exploration", "learning"],
    ),
    Goal(
        id="avoid_death",
        description="Stay alive — avoid combat until stronger, retreat if threatened",
        priority=10,
        tags=["survival", "combat"],
    ),
]


def main() -> None:
    config = load_config()

    # Seed skills
    db = SkillDatabase(config["skills"]["db_path"])
    for skill in STARTER_SKILLS:
        db.upsert(skill)
        print(f"  [skills] upserted '{skill.name}'")

    # Seed goals (only if no goals exist)
    goals = GoalSystem(
        config["goals"]["session_goals_path"],
        config["goals"]["long_term_goals_path"],
    )
    if not goals.session and not goals.long_term:
        for goal in STARTER_GOALS:
            goals.add_session_goal(goal)
            print(f"  [goals]  added '{goal.id}' (priority={goal.priority})")
    else:
        print(f"  [goals]  skipped — already have {len(goals.session)} session goals")

    print("\nSeed complete.")


if __name__ == "__main__":
    main()
