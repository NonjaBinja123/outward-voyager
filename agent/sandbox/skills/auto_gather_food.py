def run_gather_food(state: dict) -> dict:
    """
    Gather food action for an autonomous game agent.
    
    Given that Food, Drink, and Sleep are all at 0% (critically dangerous)
    and the player has no seaweed or other food items, this function
    determines the best immediate action to search the environment
    around Cierzo for food and water.
    
    Args:
        state: dict containing the current game state, potentially including:
            - 'player_position': dict with 'x', 'y', 'z' coordinates
            - 'inventory': list of item dicts
            - 'nearby_items': list of items on the ground nearby
            - 'nearby_npcs': list of NPCs nearby
            - 'stats': dict with 'food', 'drink', 'sleep' percentages
            - 'location': current location name
            - 'nearby_interactables': list of interactable objects
    
    Returns:
        dict with 'action' (str) and 'params' (dict) describing what to do.
    """
    import random

    inventory = state.get('inventory', [])
    nearby_items = state.get('nearby_items', [])
    nearby_interactables = state.get('nearby_interactables', [])
    stats = state.get('stats', {})
    location = state.get('location', 'unknown')
    player_position = state.get('player_position', {'x': 0, 'y': 0, 'z': 0})

    food_keywords = ['food', 'berry', 'berries', 'meat', 'fish', 'bread', 'ration',
                     'jerky', 'gaberry', 'seaweed', 'egg', 'fruit', 'stew', 'potato',
                     'mushroom', 'cierzo ceviche', 'travel ration', 'luxury jerky']
    drink_keywords = ['water', 'waterskin', 'drink', 'potion', 'tea', 'juice', 'bottle']

    def is_food(item_name):
        name_lower = item_name.lower() if isinstance(item_name, str) else str(item_name).lower()
        return any(kw in name_lower for kw in food_keywords)

    def is_drink(item_name):
        name_lower = item_name.lower() if isinstance(item_name, str) else str(item_name).lower()
        return any(kw in name_lower for kw in drink_keywords)

    # Priority 1: Use any food/drink already in inventory
    for item in inventory:
        item_name = item.get('name', '') if isinstance(item, dict) else str(item)
        if is_food(item_name):
            return {
                'action': 'use_item',
                'params': {'item_name': item_name, 'reason': 'Consuming food from inventory to survive critical hunger'}
            }
        if is_drink(item_name):
            return {
                'action': 'use_item',
                'params': {'item_name': item_name, 'reason': 'Consuming drink from inventory to survive critical thirst'}
            }

    # Priority 2: Pick up any nearby food or drink items on the ground
    for item in nearby_items:
        item_name = item.get('name', '') if isinstance(item, dict) else str(item)
        item_pos = item.get('position', player_position) if isinstance(item, dict) else player_position
        if is_food(item_name) or is_drink(item_name):
            return {
                'action': 'pick_up_item',
                'params': {
                    'item_name': item_name,
                    'position': item_pos,
                    'reason': 'Picking up nearby food/drink to address critical survival needs'
                }
            }

    # Priority 3: Interact with water sources, fishing spots, or food containers
    for obj in nearby_interactables:
        obj_name = obj.get('name', '') if isinstance(obj, dict) else str(obj)
        obj