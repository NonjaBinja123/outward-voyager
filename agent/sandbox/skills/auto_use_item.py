def run_use_item(state: dict) -> dict:
    inventory = state.get("inventory", [])
    equipment = state.get("equipment", {})
    
    target_item = None
    target_item_name = "Green Worker Attire"
    
    for item in inventory:
        if isinstance(item, dict):
            item_name = item.get("name", "")
            if item_name == target_item_name:
                target_item = item
                break
        elif isinstance(item, str):
            if item == target_item_name:
                target_item = {"name": target_item_name}
                break
    
    if target_item is None:
        for item in inventory:
            if isinstance(item, dict):
                item_name = item.get("name", "")
                if "worker attire" in item_name.lower() or "green" in item_name.lower():
                    target_item = item
                    target_item_name = item_name
                    break
            elif isinstance(item, str):
                if "worker attire" in item.lower() or "green" in item.lower():
                    target_item = {"name": item}
                    target_item_name = item
                    break
    
    if target_item is None:
        return {
            "action": "use_item",
            "params": {
                "item": "Green Worker Attire",
                "slot": "body"
            }
        }
    
    item_id = None
    if isinstance(target_item, dict):
        item_id = target_item.get("id", target_item.get("item_id", target_item.get("name", target_item_name)))
    else:
        item_id = target_item
    
    return {
        "action": "use_item",
        "params": {
            "item": item_id,
            "item_name": target_item_name,
            "slot": "body",
            "purpose": "equip"
        }
    }