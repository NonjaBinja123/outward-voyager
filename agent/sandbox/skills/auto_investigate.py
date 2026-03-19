def run_investigate(state: dict) -> dict:
    """
    Investigate a nearby LightHouseInteriorCierzo structure.
    
    Analyzes the current game state to determine the best approach
    for investigating the lighthouse interior, which may contain
    resources, shelter, or useful information about Cierzo.
    
    Args:
        state: dict containing the current game state, potentially including:
            - player_position: current player coordinates
            - nearby_structures: list of nearby structures/points of interest
            - inventory: current player inventory
            - health: player health status
            - discovered_locations: previously discovered locations
    
    Returns:
        dict with 'action' (str) and 'params' (dict) describing the
        investigation action to take.
    """
    
    player_position = state.get("player_position", {"x": 0, "y": 0, "z": 0})
    nearby_structures = state.get("nearby_structures", [])
    inventory = state.get("inventory", [])
    health = state.get("health", 100)
    discovered_locations = state.get("discovered_locations", [])
    
    target_structure = None
    target_id = None
    
    for structure in nearby_structures:
        struct_name = ""
        if isinstance(structure, dict):
            struct_name = structure.get("name", "")
            struct_id = structure.get("id", None)
        elif isinstance(structure, str):
            struct_name = structure
            struct_id = structure
        
        if "LightHouseInteriorCierzo" in str(struct_name) or "lighthouse" in str(struct_name).lower():
            target_structure = structure
            target_id = struct_id
            break
    
    if target_structure is None:
        target_id = "LightHouseInteriorCierzo"
        target_structure = {"name": "LightHouseInteriorCierzo", "id": target_id}
    
    is_previously_discovered = target_id in discovered_locations or "LightHouseInteriorCierzo" in discovered_locations
    
    has_light_source = any(
        (isinstance(item, str) and item.lower() in ("torch", "lantern", "flint", "light"))
        or (isinstance(item, dict) and item.get("type", "").lower() in ("torch", "lantern", "flint", "light"))
        for item in inventory
    )
    
    cautious = health < 50
    
    params = {
        "target": "LightHouseInteriorCierzo",
        "target_id": target_id,
        "structure_type": "lighthouse_interior",
        "location": "Cierzo",
        "approach": "cautious" if cautious else "normal",
        "search_thoroughly": not is_previously_discovered,
        "previously_discovered": is_previously_discovered,
        "has_light_source": has_light_source,
        "player_position": player_position,
        "objectives": [
            "search_for_resources",
            "check_for_shelter",
            "gather_information",
            "look_for_containers",
            "check_for_npcs",
            "examine_environment"
        ],
        "priority": "high",
    }
    
    if cautious:
        params["objectives"].insert(0, "check_for_dangers")
        params["notes"] = "Low health - proceed with caution, prioritize finding healing items or shelter."
    
    if not has_light_source:
        params["notes"] = params.get("notes", "") + " No light source detected - interior may be dark, search near entrance first."
    
    return {
        "action": "investigate",
        "params": params
    }