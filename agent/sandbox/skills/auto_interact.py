def run_interact(state: dict) -> dict:
    """
    Interact with the nearest 'Interiors' interaction point to find food, water, or a bed.
    
    Given critically low Food, Drink, and Sleep (all at 0%), we need to urgently
    find a building interior in Cierzo that may contain resources to address these needs.
    The 'Interiors' interaction at ~23.8m is the best available lead.
    """
    target_name = "Interiors"
    target_distance = None
    target_id = None

    interactions = state.get("interactions", [])
    
    for interaction in interactions:
        name = interaction.get("name", "")
        if target_name.lower() in name.lower():
            dist = interaction.get("distance", float("inf"))
            if target_distance is None or dist < target_distance:
                target_distance = dist
                target_id = interaction.get("id", name)
                target_name = name

    if not interactions and target_id is None:
        nearby_objects = state.get("nearby_objects", [])
        for obj in nearby_objects:
            name = obj.get("name", "")
            if "interior" in name.lower() or "door" in name.lower() or "building" in name.lower():
                dist = obj.get("distance", float("inf"))
                if target_distance is None or dist < target_distance:
                    target_distance = dist
                    target_id = obj.get("id", name)
                    target_name = name

    if target_id is None:
        target_id = "Interiors"
        target_name = "Interiors"

    params = {
        "target": target_id,
        "target_name": target_name,
    }

    if target_distance is not None:
        params["distance"] = target_distance

    move_first = target_distance is not None and target_distance > 3.0

    if move_first:
        return {
            "action": "move_to_and_interact",
            "params": {
                "target": target_id,
                "target_name": target_name,
                "distance": target_distance,
                "reason": "Critically low Food/Drink/Sleep (all 0%). Moving to Interiors entrance to find supplies or a bed inside.",
            }
        }
    else:
        return {
            "action": "interact",
            "params": {
                "target": target_id,
                "target_name": target_name,
                "reason": "Critically low Food/Drink/Sleep (all 0%). Entering interior to search for food, water, or a bed.",
            }
        }