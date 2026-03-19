def run_eat(state: dict) -> dict:
    inventory = state.get("inventory", [])
    
    food_item = None
    
    for item in inventory:
        if isinstance(item, dict):
            item_name = item.get("name", "").lower()
        elif isinstance(item, str):
            item_name = item.lower()
        else:
            continue
        
        if "seaweed" in item_name:
            food_item = item if isinstance(item, str) else item.get("name", "Seaweed")
            break
    
    if food_item is None:
        for item in inventory:
            if isinstance(item, dict):
                item_name = item.get("name", "").lower()
                item_type = item.get("type", "").lower()
            elif isinstance(item, str):
                item_name = item.lower()
                item_type = ""
            else:
                continue
            
            if "food" in item_type or any(keyword in item_name for keyword in [
                "berry", "fruit", "meat", "fish", "bread", "apple", "mushroom",
                "carrot", "potato", "seed", "kelp", "algae", "plant"
            ]):
                food_item = item if isinstance(item, str) else item.get("name", "")
                break
    
    if food_item is None:
        if len(inventory) > 0:
            first = inventory[0]
            food_item = first if isinstance(first, str) else first.get("name", "Seaweed")
        else:
            food_item = "Seaweed"
    
    return {
        "action": "eat",
        "params": {
            "item": food_item
        }
    }