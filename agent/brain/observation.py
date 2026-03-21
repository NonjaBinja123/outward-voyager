"""
Observation — packages everything the Brain needs to build an LLM prompt.

Constructed by the Orchestrator before each think() call.
Responsible ONLY for: holding game state + serializing it to a clean text block.
"""
from typing import Any


class Observation:
    """
    Packages everything the Brain needs to build an LLM prompt.
    Constructed by the Orchestrator before each think() call.
    """

    def __init__(
        self,
        state: dict[str, Any],
        recent_journal: list[str] | None = None,
        active_goals: list[str] | None = None,
        pending_chat: list[str] | None = None,
        extra_context: str = "",
        scene_objects: list[dict] | None = None,
        blocked_nav_cells: set[tuple[int, int]] | None = None,
        stuck_uids: set[str] | None = None,
    ) -> None:
        self._state = state
        self.recent_journal = recent_journal or []
        self.active_goals = active_goals or []
        self.pending_chat = pending_chat or []
        self.extra_context = extra_context
        self.scene_objects = scene_objects or []
        # 5-unit grid cells that nav has failed for — suppress these from scene objects
        self._blocked_cells: set[tuple[int, int]] = blocked_nav_cells or set()
        # UIDs tried 3+ times with no effect — filter from INTERACT NOW entirely
        self._stuck_uids: set[str] = stuck_uids or set()

    # UIDs that are Unity scene hierarchy containers or placeholder objects.
    # The mod's scan picks these up — filter them out so the LLM never sees them.
    _GARBAGE_UIDS: frozenset[str] = frozenset({
        "Interiors", "Environment", "PlayerHouse", "_SNPC", "Exterior",
        "Dungeon", "Town", "Village", "City", "Interior",
        "Cube", "Sphere", "Cylinder", "Plane", "Quad",  # Unity default primitive names
    })

    @staticmethod
    def _fmt_stat(val: Any, max_val: Any) -> str:
        """Format a stat value, replacing astronomical garbage values with '?'."""
        def _clean(v: Any) -> str:
            try:
                f = float(v)
                if abs(f) > 1e10 or f != f:  # insane value or NaN
                    return "?"
                return f"{f:.0f}"
            except (TypeError, ValueError):
                return "?"
        return f"{_clean(val)}/{_clean(max_val)}"

    def state_summary(self) -> str:
        """Serialize game state to a compact, LLM-readable string."""
        s = self._state
        p = s.get("player", {})
        lines = [
            f"Scene: {s.get('scene', 'unknown')}",
        ]
        if all(k in p for k in ("pos_x", "pos_y", "pos_z")):
            rot = p.get("rotation_y", None)
            rot_str = f"  facing={rot:.0f}°" if rot is not None else ""
            lines.append(
                f"Position: ({p['pos_x']:.1f}, {p['pos_y']:.1f}, {p['pos_z']:.1f}){rot_str}"
            )
        lines += [
            f"Health: {self._fmt_stat(p.get('health'), p.get('max_health'))}",
            f"Stamina: {self._fmt_stat(p.get('stamina'), p.get('max_stamina'))}",
            f"Food: {self._fmt_stat(p.get('food'), p.get('max_food'))}",
            f"Drink: {self._fmt_stat(p.get('drink'), p.get('max_drink'))}",
            f"Sleep: {self._fmt_stat(p.get('sleep'), p.get('max_sleep'))}",
            f"In combat: {p.get('in_combat', False)}",
            f"Dead: {p.get('is_dead', False)}",
        ]
        status = p.get("status_effects", [])
        if status:
            lines.append(f"Status effects: {', '.join(status)}")

        # ── Nearby interactions (can trigger_interaction RIGHT NOW) ──────────
        raw = s.get("nearby_interactions", [])
        player_uid = next(
            (i.get("uid", "") for i in raw if i.get("distance", 999) == 0), ""
        )
        interactable = [
            i for i in raw
            if i.get("uid") != player_uid                # not self
            and i.get("uid") not in self._GARBAGE_UIDS  # not scene containers
            and float(i.get("distance", 999)) >= 2.0    # skip player-worn equipment (< 2m)
            and i.get("uid") not in self._stuck_uids    # skip already-tried UIDs
        ]
        lines.append("")
        lines.append("INTERACT NOW — use trigger_interaction with these UIDs only:")
        if interactable:
            for obj in interactable:
                uid = obj.get("uid", "?")
                name = obj.get("label") or obj.get("name") or uid
                dist = obj.get("distance", 0)
                x, z = obj.get("x", "?"), obj.get("z", "?")
                lines.append(f"  uid={uid!r}  name={name!r}  dist={dist:.1f}m  pos=({x}, {z})")
        else:
            lines.append("  (none — move closer to something before interacting)")

        # ── Scene objects (visible in area, navigate toward them) ────────────
        if self.scene_objects:
            # Only show objects worth navigating to (more than 8m away — already-nearby objects
            # are already in INTERACT NOW; showing them here causes pointless micro-navigation)
            far_enough = [o for o in self.scene_objects if float(o.get("distance", 999)) > 8]
            # Filter out objects in recently-failed nav cells (pathfinding blocked those areas)
            if self._blocked_cells:
                far_enough = [
                    o for o in far_enough
                    if (round(float(o.get("x", 0)) / 10) * 10,
                        round(float(o.get("z", 0)) / 10) * 10) not in self._blocked_cells
                ]
            characters = [o for o in far_enough if o.get("has_character") and not o.get("is_dead")]
            non_chars = [o for o in far_enough if not o.get("has_character")]

            if characters:
                lines.append("")
                lines.append("CHARACTERS NEARBY — navigate_to their pos then trigger_interaction:")
                for obj in characters[:10]:
                    name = obj.get("name", "?")
                    dist = obj.get("distance", "?")
                    x, y, z = obj.get("x", "?"), obj.get("y", "?"), obj.get("z", "?")
                    lines.append(f"  {name!r}  dist={dist}m  pos=({x}, {y}, {z})")

            if non_chars:
                lines.append("")
                lines.append("SCENE OBJECTS — navigate_to pos to approach:")
                for obj in non_chars[:15]:
                    name = obj.get("name", "?")
                    dist = obj.get("distance", "?")
                    x, y, z = obj.get("x", "?"), obj.get("y", "?"), obj.get("z", "?")
                    tag = obj.get("tag", "")
                    tag_str = f"  [{tag}]" if tag and tag not in ("Untagged", "") else ""
                    lines.append(f"  {name!r}  dist={dist}m  pos=({x}, {y}, {z}){tag_str}")

        # Screen message
        msg = s.get("screen_message", "")
        if msg:
            lines.append(f"Screen message: {msg!r}")

        # Inventory
        inv = s.get("inventory", {})
        pouch = inv.get("pouch", [])
        equipped = inv.get("equipped", {})
        if pouch:
            item_strs = []
            for i in pouch[:12]:
                qty = i.get("quantity", 1)
                name = i.get("name", "?")
                item_strs.append(f"{name}x{qty}" if qty > 1 else name)
            lines.append(f"Pouch ({len(pouch)} items): {', '.join(item_strs)}")
        if equipped:
            worn = [f"{slot}={name}" for slot, name in equipped.items() if name]
            if worn:
                lines.append(f"Equipped: {', '.join(worn)}")

        return "\n".join(l for l in lines if l)
