def run_drink(state: dict) -> dict:
    inventory = state.get("inventory", [])
    
    seaweed_item = None
    for item in inventory:
        if isinstance(item, dict):
            item_name = item.get("name", "").lower()
            if "seaweed" in item_name:
                seaweed_item = item
                break
        elif isinstance(item, str):
            if "seaweed" in item.lower():
                seaweed_item = {"name": item}
                break
    
    if seaweed_item is not None:
        item_name = seaweed_item.get("name", "Seaweed")
        item_id = seaweed_item.get("id", seaweed_item.get("name", "Seaweed"))
        return {
            "action": "drink",
            "params": {
                "item": item_id,
                "item_name": item_name
            }
        }
    
    drinkable_keywords = ["water", "potion", "drink", "juice", "flask", "bottle", "tea", "brew", "elixir"]
    for item in inventory:
        if isinstance(item, dict):
            item_name = item.get("name", "").lower()
        elif isinstance(item, str):
            item_name = item.lower()
        else:
            continue
        
        for keyword in drinkable_keywords:
            if keyword in item_name:
                if isinstance(item, dict):
                    return {
                        "action": "drink",
                        "params": {
                            "item": item.get("id", item.get("name", item_name)),
                            "item_name": item.get("name", item_name)
                        }
                    }
                else:
                    return {
                        "action": "drink",
                        "params": {
                            "item": item,
                            "item_name": item
                        }
                    }
    
    if len(inventory) > 0:
        first_item = inventory[0]
        if isinstance(first_item, dict):
            return {
                "action": "use",
                "params": {
                    "item": first_item.get("id", first_item.get("name", "Seaweed")),
                    "item_name": first_item.get("name", "Seaweed"),
                    "purpose": "drink"
                }
            }
        else:
            return {
                "action": "use",
                "params": {
                    "item": first_item,
                    "item_name": str(first_item),
                    "purpose": "drink"
                }
            }
    
    return {
        "action": "drink",
        "params": {
            "item": "Seaweed",
            "item_name": "Seaweed"
        }
    }