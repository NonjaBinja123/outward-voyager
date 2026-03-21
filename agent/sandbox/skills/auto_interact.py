def run_interact(state: dict) -> dict:
    """
    Interact with the closest actionable interaction point ('Interiors' at ~1.8m)
    to enter a building and search for food and water to address Hungry and Parched
    status effects.
    """
    interactions = state.get("interactions", [])
    
    target_interaction = None
    min_distance = float("inf")
    
    for interaction in interactions:
        distance = interaction.get("distance", float("inf"))
        name = interaction.get("name", "")
        
        if name.lower() == "interiors" and distance < min_distance:
            min_distance = distance
            target_interaction = interaction
    
    if target_interaction is None:
        for interaction in interactions:
            distance = interaction.get("distance", float("inf"))
            if distance < min_distance:
                min_distance = distance
                target_interaction = interaction
    
    if target_interaction is not None:
        interaction_id = target_interaction.get("id", target_interaction.get("name", "interiors"))
        return {
            "action": "interact",
            "params": {
                "target": interaction_id,
                "name": target_interaction.get("name", "Interiors"),
                "distance": target_interaction.get("distance", 1.8),
                "reason": "Entering building to find food and water for Hungry and Parched status effects"
            }
        }
    
    return {
        "action": "interact",
        "params": {
            "target": "interiors",
            "name": "Interiors",
            "distance": 1.8,
            "reason": "Entering building to find food and water for Hungry and Parched status effects"
        }
    }