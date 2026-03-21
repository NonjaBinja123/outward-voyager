def run_eat(state: dict) -> dict:
    inventory = state.get("inventory", [])
    
    seaweed_item = None
    for item in inventory:
        if isinstance(item, dict):
            name = item.get("name", "").lower()
            if "seaweed" in name:
                seaweed_item = item
                break
        elif isinstance(item, str) and "seaweed" in item.lower():
            seaweed_item = {"name": item}
            break
    
    if seaweed_item is None:
        for item in inventory:
            if isinstance(item, dict):
                seaweed_item = item
                break
    
    params = {}
    
    if seaweed_item is not None:
        if isinstance(seaweed_item, dict):
            if "uid" in seaweed_item:
                params["item_uid"] = seaweed_item["uid"]
            elif "id" in seaweed_item:
                params["item_id"] = seaweed_item["id"]
            elif "item_uid" in seaweed_item:
                params["item_uid"] = seaweed_item["item_uid"]
            
            if "name" in seaweed_item:
                params["item"] = seaweed_item["name"]
            
            if not params:
                params["item"] = seaweed_item.get("name", "Seaweed")
        else:
            params["item"] = str(seaweed_item)
    else:
        params["item"] = "Seaweed"
    
    return {
        "action": "eat",
        "params": params
    }