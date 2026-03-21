def run_use_item(state: dict) -> dict:
    """
    Use the kitchen / cooking station to address Hungry and Parched status effects.
    Simulates pressing 'F' to interact with the kitchen and use it.
    """
    player = state.get("player", {})
    status_effects = player.get("status_effects", [])
    inventory = player.get("inventory", [])
    nearby_objects = state.get("nearby_objects", [])
    
    # Determine which items to use/consume based on status effects
    items_to_use = []
    
    has_hungry = "Hungry" in status_effects or "hungry" in status_effects
    has_parched = "Parched" in status_effects or "parched" in status_effects
    
    # Check if we're near a kitchen or cooking station
    kitchen_nearby = False
    kitchen_id = None
    for obj in nearby_objects:
        obj_name = ""
        obj_id = None
        if isinstance(obj, dict):
            obj_name = obj.get("name", "").lower()
            obj_id = obj.get("id", obj.get("name", "kitchen"))
        elif isinstance(obj, str):
            obj_name = obj.lower()
            obj_id = obj
        
        if "kitchen" in obj_name or "cooking" in obj_name or "stove" in obj_name or "campfire" in obj_name:
            kitchen_nearby = True
            kitchen_id = obj_id
            break
    
    # Build the target identifier
    target = kitchen_id if kitchen_id else "kitchen"
    
    # Determine what we want to cook/consume
    consume_goals = []
    if has_hungry:
        consume_goals.append("food")
    if has_parched:
        consume_goals.append("drink")
    if not consume_goals:
        # Even if no explicit status effects, use the kitchen as requested
        consume_goals.append("cook")
    
    # Look for relevant items in inventory to use at the kitchen
    food_items = []
    drink_items = []
    for item in inventory:
        item_name = ""
        if isinstance(item, dict):
            item_name = item.get("name", "").lower()
        elif isinstance(item, str):
            item_name = item.lower()
        
        food_keywords = ["meat", "fish", "bread", "fruit", "vegetable", "egg", "flour", "rice", "potato", "apple", "berry", "steak", "chicken", "raw", "ingredient", "food"]
        drink_keywords = ["water", "juice", "milk", "potion", "flask", "bottle", "drink", "tea", "soup", "broth"]
        
        for kw in food_keywords:
            if kw in item_name:
                food_items.append(item if isinstance(item, str) else item.get("id", item.get("name", "")))
                break
        
        for kw in drink_keywords:
            if kw in item_name:
                drink_items.append(item if isinstance(item, str) else item.get("id", item.get("name", "")))
                break
    
    params = {
        "key": "F",
        "target": target,
        "interaction_type": "use_kitchen",
        "goals": consume_goals,
    }
    
    if food_items:
        params["food_items"] = food_items
    if drink_items:
        params["drink_items"] = drink_items
    
    # Include status context for the agent to understand why we're doing this
    if status_effects:
        params["status_effects_to_address"] = [s for s in status_effects if s.lower() in ("hungry", "parched", "thirsty", "starving", "dehydrated")]
    
    return {
        "action": "use_item",
        "params": params,
    }