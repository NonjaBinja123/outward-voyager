def run_investigate(state: dict) -> dict:
    """
    Investigate a nearby altar (mdl_env_propAltarBlueChamberHouseATable) to check
    for useful items or information.
    
    Args:
        state: dict containing the current game state, including nearby objects,
               player position, inventory, etc.
    
    Returns:
        dict with 'action' (str) and 'params' (dict) describing what the agent
        should do next.
    """
    import math

    target_object_id = "mdl_env_propAltarBlueChamberHouseATable"
    target_distance = 2.4
    interaction_range = 1.5

    nearby_objects = state.get("nearby_objects", [])
    player_position = state.get("player_position", {"x": 0.0, "y": 0.0, "z": 0.0})

    altar = None
    for obj in nearby_objects:
        obj_id = obj.get("id", "") or obj.get("name", "") or obj.get("model", "")
        if target_object_id in str(obj_id) or "altar" in str(obj_id).lower() or "Altar" in str(obj.get("name", "")):
            altar = obj
            break

    if altar is None:
        return {
            "action": "move_towards",
            "params": {
                "target_id": target_object_id,
                "description": "Moving towards the altar to investigate it for useful items.",
                "estimated_distance": target_distance,
                "search_radius": 5.0
            }
        }

    altar_position = altar.get("position", None)
    if altar_position is not None and player_position is not None:
        dx = altar_position.get("x", 0) - player_position.get("x", 0)
        dy = altar_position.get("y", 0) - player_position.get("y", 0)
        dz = altar_position.get("z", 0) - player_position.get("z", 0)
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    else:
        distance = target_distance

    if distance > interaction_range:
        return {
            "action": "move_towards",
            "params": {
                "target_id": altar.get("id", target_object_id),
                "target_position": altar.get("position", None),
                "description": f"Moving closer to the altar (distance: {distance:.1f}m, need: {interaction_range}m).",
                "current_distance": round(distance, 2)
            }
        }

    return {
        "action": "interact",
        "params": {
            "target_id": altar.get("id", target_object_id),
            "target_name": altar.get("name", "Altar"),
            "interaction_type": "investigate",
            "description": "Investigating the altar for useful items, loot, or information.",
            "expected_outcomes": [
                "find_items",
                "discover_information",
                "trigger_event"
            ],
            "follow_up": {
                "if_items_found": "loot_all",
                "if_event_triggered": "observe_and_respond",
                "if_nothing": "search_surroundings"
            }
        }
    }